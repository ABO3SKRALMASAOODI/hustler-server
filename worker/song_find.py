"""Find NAMED things on the open web: a song, or real b-roll footage.

search_music covers vibes and genres from the CC catalogs; this covers the
thing the user (or the edit) asked for BY NAME — "Blinding Lights", or
"SpaceX Starship launch" when the podcast mentions Elon Musk and the cut
needs the rocket. It runs yt-dlp in search mode (`ytsearchN:`) — the same
tool, hardening idiom and cookies the fetch path already trusts — and
returns candidate LINKS. It never downloads: the chosen URL goes through
fetch_url, so every byte still moves under url_media's extractor bounds
and cleanup. One module, zero new network paths.

RANKING IS A HINT, NOT A VERDICT. YouTube search for a song name is mostly
right and reliably polluted — lyric videos, sped-up edits, hour loops,
covers. A small score sorts the junk down (and an auto-generated "- Topic"
channel up, since that is the label's own audio), but the MODEL makes the
pick from the listed facts, and the words in the user's own request beat
every penalty: someone who asked for the slowed version wants the slowed
version.

Gating: FIND_SONG_ENABLED is the deployment switch; FIND_SONG_USER_IDS
narrows it to specific accounts ("" = everyone) so the capability can run
admin-only without a redeploy. Both are checked in the tool, and the tool
is hidden entirely when the extractor path itself is off.
"""

import json
import os
import re
import subprocess
import sys

import config

SEARCH_COUNT = 6
SEARCH_TIMEOUT_S = 60

# Title words that usually mean "not the song the user named". Each is
# forgiven when the user's own query contains it — asking for "lofi remix"
# makes "remix" the point, not pollution.
_JUNK = ("lyric", "sped up", "slowed", "reverb", "8d", "nightcore",
         "bass boosted", "1 hour", "one hour", "10 hour", "loop",
         "live", "cover", "remix", "karaoke", "instrumental", "reaction",
         "tutorial")


class SongFindError(Exception):
    pass


def available():
    """Hidden entirely unless the whole chain can run: the deployment
    switch, the fetch path the pick is handed to, and the extractor."""
    if not (config.FIND_SONG_ENABLED and config.URL_FETCH_ENABLED):
        return False
    import url_media
    return url_media._ytdlp_available()


def footage_available():
    """find_footage's own switch, on the same extractor chain."""
    if not (config.FIND_FOOTAGE_ENABLED and config.URL_FETCH_ENABLED):
        return False
    import url_media
    return url_media._ytdlp_available()


def _in_allowlist(ids, user_id):
    if not ids:
        return True
    allowed = {x.strip() for x in ids.split(",") if x.strip()}
    return str(user_id) in allowed


def allowed_for(user_id):
    """Per-account gate. Empty allowlist = every user; a CSV of ids
    restricts the tool to those accounts (the planned admin-only mode)."""
    return _in_allowlist(config.FIND_SONG_USER_IDS, user_id)


def footage_allowed_for(user_id):
    return _in_allowlist(config.FIND_FOOTAGE_USER_IDS, user_id)


def _score(cand, query):
    q = (query or "").lower()
    title = (cand.get("title") or "").lower()
    uploader = (cand.get("uploader") or "").lower()
    s = 0.0
    if "official audio" in title:
        s += 3
    elif "official video" in title or "official music video" in title:
        s += 1
    elif re.search(r"\baudio\b", title):
        s += 1
    # Auto-generated label channels ("Artist - Topic") are the platform's
    # own full-quality audio of exactly the named track.
    if uploader.endswith("- topic"):
        s += 3
    for w in _JUNK:
        if w in title:
            # The user's own words beat every penalty: a marker they asked
            # for is the point of the search, not pollution.
            s += 2 if w in q else -2
    d = cand.get("duration_s") or 0
    if d and (d < 45 or d > 720):
        s -= 3          # a 30s short or an hour loop is not "the song"
    return s


def rank(candidates, query):
    return sorted(candidates, key=lambda c: _score(c, query), reverse=True)


# Footage titles that usually mean "about the thing", not "footage OF the
# thing" — forgiven when the query itself asks for one.
_FOOTAGE_JUNK = ("reaction", "compilation", "full interview", "full podcast",
                 "full episode", "explained", "review", "breakdown",
                 "live stream", "livestream")


def _score_footage(cand, query):
    q = (query or "").lower()
    title = (cand.get("title") or "").lower()
    s = 0.0
    d = cand.get("duration_s") or 0
    # fetch_url's byte cap is the real wall: a 30-minute upload will be
    # refused for size after a long download. Short real clips win.
    if d:
        if d <= 360:
            s += 2
        elif d <= 720:
            s += 0
        elif d <= 1800:
            s -= 3
        else:
            s -= 6
    if "4k" in title or re.search(r"\bhd\b", title):
        s += 1
    for w in _FOOTAGE_JUNK:
        if w in title:
            s += 2 if w in q else -2
    return s


def rank_footage(candidates, query):
    return sorted(candidates, key=lambda c: _score_footage(c, query),
                  reverse=True)


def search_footage(query, count=SEARCH_COUNT):
    """Candidate links for real b-roll of a named topic, best guess first."""
    return rank_footage(_yt_candidates(query, count,
                                       what="a topic to find footage of"),
                        query)


def search(query, count=SEARCH_COUNT):
    """Candidate links for a song name, best guess first."""
    return rank(_yt_candidates(query, count, what="a song name"), query)


def _yt_candidates(query, count, what="a search query"):
    query = (query or "").strip()
    if not query:
        raise SongFindError(f"{what} is required")
    cmd = [sys.executable, "-m", "yt_dlp",
           # Same no-on-disk-config rule as the extractor: nothing outside
           # this argv may inject flags into a process holding our env.
           "--ignore-config",
           "--no-warnings", "--quiet",
           "--socket-timeout", "20",
           "--flat-playlist", "--dump-json",
           f"ytsearch{int(count)}:{query}"]
    cookies = config.YTDLP_COOKIES_FILE
    if cookies and os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    if config.YTDLP_PROXY:
        cmd += ["--proxy", config.YTDLP_PROXY]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=SEARCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SongFindError("the web search timed out — try again, or ask "
                            "the user for a link")
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        vid = d.get("id")
        if not vid:
            continue
        dur = None
        try:
            dur = round(float(d.get("duration")), 1) if d.get("duration") \
                else None
        except (TypeError, ValueError):
            dur = None
        out.append({
            "title": (d.get("title") or "").strip() or "untitled",
            "uploader": (d.get("uploader") or d.get("channel") or
                         "").strip() or None,
            "duration_s": dur,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "_thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    if not out and proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise SongFindError((detail[-1] if detail else "search failed")[:160])
    return out


def describe(c):
    bits = []
    if c.get("uploader"):
        bits.append(c["uploader"])
    if c.get("duration_s"):
        bits.append(f"{c['duration_s']:.0f}s")
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"{c['url']} — \"{c['title']}\"{tail}"
