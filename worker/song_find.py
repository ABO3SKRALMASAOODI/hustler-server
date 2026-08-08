"""Find a NAMED song on the open web — the link-finding half of "add
Blinding Lights".

search_music covers vibes and genres from the CC catalogs; this covers the
song the user asked for BY NAME, which those catalogs do not carry. It runs
yt-dlp in search mode (`ytsearchN:`) — the same tool, hardening idiom and
cookies the fetch path already trusts — and returns candidate LINKS. It
never downloads: the chosen URL goes through fetch_url, so every byte still
moves under url_media's extractor bounds and cleanup. One new module, zero
new network paths.

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


def allowed_for(user_id):
    """Per-account gate. Empty allowlist = every user; a CSV of ids
    restricts the tool to those accounts (the planned admin-only mode)."""
    ids = config.FIND_SONG_USER_IDS
    if not ids:
        return True
    allowed = {x.strip() for x in ids.split(",") if x.strip()}
    return str(user_id) in allowed


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


def search(query, count=SEARCH_COUNT):
    """Candidate links for a song name, best guess first."""
    query = (query or "").strip()
    if not query:
        raise SongFindError("a song name is required")
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
        })
    if not out and proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise SongFindError((detail[-1] if detail else "search failed")[:160])
    return rank(out, query)


def describe(c):
    bits = []
    if c.get("uploader"):
        bits.append(c["uploader"])
    if c.get("duration_s"):
        bits.append(f"{c['duration_s']:.0f}s")
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"{c['url']} — \"{c['title']}\"{tail}"
