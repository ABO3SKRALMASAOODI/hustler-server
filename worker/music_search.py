"""Live music search: tracks found on the open web, on demand.

Round 98 replaced the bundled 24-track pack with this; the pack itself is
now fully deleted (its files were copied to R2 under legacy-music/ and
every EDL in the database rewritten to those plain storage keys,
2026-08-08). The agent finds music at request time. Two providers, the
stock.py idiom exactly:

  Jamendo   (JAMENDO_CLIENT_ID) — a real music catalog with search and
            per-track Creative Commons licensing; ordered by THIS MONTH'S
            popularity so results skew current. Tried first when
            configured.
  Openverse (keyless)           — the CC audio aggregator (Jamendo, FMA,
            Wikimedia...) as the always-available fallback.

LICENSING IS INFORMATION, NOT A WALL. Every hit carries its license and
author, and the line the agent reads states the obligation outright —
public domain, credit-required, or NON-COMMERCIAL-ONLY — so the choice is
made in the conversation, by the person whose video it is, instead of by a
filter they cannot see. The one family still excluded is no-derivatives
(ND): syncing music in timed relation with picture is itself an adaptation,
so an ND track cannot be used in ANY edit, monetized or not — offering one
would only manufacture a violation. For a SPECIFIC copyrighted song the
path is the user's own file or link: fetch_url ingests anything a URL can
reach, and the trending-sound flow (cut to the grid, platform adds the
licensed audio in-app) stays the honest route for sounds the platforms
license only inside their own apps.

Downloads go through net_fetch (SSRF policy, byte cap, wall clock) and land
as ordinary project assets (kind 'music'), so add_music, swap_music,
set_music_fit, get_audio_analysis and listen_to all work on them unchanged.
"""

import re
import subprocess

import config
import net_fetch

JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
OPENVERSE_API = "https://api.openverse.org/v1/audio/"

MAX_RESULTS = 12
API_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 180


class MusicSearchError(Exception):
    pass


def available():
    return bool(config.MUSIC_SEARCH_ENABLED)


def providers():
    out = []
    if config.JAMENDO_CLIENT_ID:
        out.append("jamendo")
    out.append("openverse")
    return out


# License handling: the terms are STATED per hit, never silently filtered.
# The one exclusion is the no-derivatives family — syncing music under
# picture is itself an adaptation, so an ND track is unusable in any edit.
_CC_LICENSE = re.compile(r"(cc0|pdm|publicdomain|zero|creativecommons|"
                         r"^by|/by)", re.I)


def _license_note(license_id, author):
    lid = (license_id or "").lower()
    # "cc0", never a bare "0": version suffixes ("by-4.0", ".../by/3.0/")
    # contain zeros too, and matching them stamped "public domain — no
    # obligations" onto BY tracks, suppressing the very credit line this
    # function exists to deliver.
    if any(x in lid for x in ("cc0", "pdm", "publicdomain", "zero")):
        return "public domain — no obligations"
    who = author or "the artist"
    if "nc" in lid:
        # Checked before by-sa: "by-nc-sa" is NC first — the commercial
        # restriction is the fact that changes what the user may do.
        return (f"CC {lid.upper() if len(lid) <= 12 else 'BY-NC'} — "
                "NON-COMMERCIAL USE ONLY: fine for a personal video, NOT "
                f"for monetized/business content; credit {who} in the "
                "caption/description")
    if "by-sa" in lid:
        return (f"CC BY-SA — free for commercial use; credit {who} in the "
                "caption/description")
    return (f"CC BY — free for commercial use; credit {who} in the "
            "caption/description")


def _license_ok(text):
    """False only for the no-derivatives family — an ND track cannot be
    synced under a video AT ALL, so offering one would just manufacture a
    license violation. Everything else is allowed and labeled."""
    t = (text or "").lower()
    if not t:
        return False
    return not any(x in t for x in ("-nd", "/nd/", "nd-"))


def _jamendo_search(query, min_s, max_s, count):
    params = {
        "client_id": config.JAMENDO_CLIENT_ID, "format": "json",
        "limit": count, "search": query, "audiodownload_allowed": "true",
        "include": "licenses",
        # This month's most-listened matches first — "current" by
        # measurement, not by adjective.
        "order": "popularity_month",
    }
    if min_s or max_s:
        params["durationbetween"] = f"{int(min_s or 0)}_{int(max_s or 3600)}"
    data = net_fetch.get_json(JAMENDO_API, params=params,
                              timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["api.jamendo.com"])
    out = []
    for t in (data.get("results") or []):
        lic = t.get("license_ccurl") or ""
        if not (_CC_LICENSE.search(lic) and _license_ok(lic)):
            continue
        url = t.get("audiodownload") or t.get("audio")
        if not url:
            continue
        out.append({
            "provider": "jamendo", "id": f"jamendo:{t.get('id')}",
            "title": (t.get("name") or "").strip() or "untitled",
            "artist": (t.get("artist_name") or "").strip() or None,
            "duration_s": float(t.get("duration") or 0) or None,
            "license": lic, "page_url": t.get("shareurl"),
            "_url": url,
        })
    return out


def _openverse_search(query, min_s, max_s, count):
    # modification only: syncing music under a video is a derivative work,
    # so ND licenses must never even be requested. NC results are welcome —
    # their restriction is stated per hit, not filtered.
    params = {"q": query, "license_type": "modification",
              "category": "music", "page_size": count}
    data = net_fetch.get_json(OPENVERSE_API, params=params,
                              timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["api.openverse.org"])
    out = []
    for t in (data.get("results") or []):
        lic = "-".join(x for x in (t.get("license"),
                                   t.get("license_version")) if x)
        if not _license_ok(t.get("license") or ""):
            continue
        url = t.get("url")
        if not url:
            continue
        dur = None
        try:
            dur = round(float(t.get("duration") or 0) / 1000.0, 1) or None
        except (TypeError, ValueError):
            dur = None
        if dur and ((min_s and dur < min_s) or (max_s and dur > max_s)):
            continue
        out.append({
            "provider": "openverse", "id": f"openverse:{t.get('id')}",
            "title": (t.get("title") or "").strip() or "untitled",
            "artist": (t.get("creator") or "").strip() or None,
            "duration_s": dur, "license": lic,
            "page_url": t.get("foreign_landing_url"),
            "_url": url,
        })
    return out


def search(query, min_s=None, max_s=None, count=MAX_RESULTS):
    """Search the configured providers, first-with-results wins (the
    stock.py rule: merged catalogs read as noise)."""
    if not available():
        raise MusicSearchError("music search is disabled on this deployment")
    query = (query or "").strip()
    if not query:
        raise MusicSearchError("a search query is required")
    count = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))
    errors = []
    lanes = []
    if config.JAMENDO_CLIENT_ID:
        lanes.append(("jamendo", _jamendo_search))
    lanes.append(("openverse", _openverse_search))
    for name, fn in lanes:
        try:
            hits = fn(query, min_s, max_s, count)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:120]}")
            continue
        if hits:
            return hits[:count]
    if errors and len(errors) == len(lanes):
        raise MusicSearchError("; ".join(errors))
    return []


def download(item, out_path):
    """Fetch a hit's audio file through the net_fetch policy."""
    url = item.get("_url")
    if not url:
        raise MusicSearchError("that result has no downloadable audio")
    net_fetch.download(url, out_path,
                       max_bytes=config.MUSIC_FETCH_MAX_MB * 1024 * 1024,
                       timeout_s=DOWNLOAD_TIMEOUT_S)
    return item


def probe_duration_s(path):
    """Container duration of a downloaded audio file, 0.0 when unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, timeout=30)
        return round(float((out.stdout or b"").decode().strip() or 0), 2)
    except Exception:
        return 0.0


def describe(item):
    """One line per hit, license obligation included — what the agent reads
    and what it must pass on."""
    bits = []
    if item.get("artist"):
        bits.append(f"by {item['artist']}")
    if item.get("duration_s"):
        bits.append(f"{item['duration_s']:.0f}s")
    bits.append(_license_note(item.get("license"), item.get("artist")))
    return f"{item['id']} — \"{item['title']}\" ({', '.join(bits)})"


def license_note(item):
    return _license_note(item.get("license"), item.get("artist"))
