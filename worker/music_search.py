"""Live music search: tracks found on the open web, on demand.

Round 98 replaced the bundled 24-track pack with this; the pack itself is
now fully deleted (its files were copied to R2 under legacy-music/ and
every EDL in the database rewritten to those plain storage keys,
2026-08-08). The agent finds music at request time. Two providers, the
stock.py idiom exactly:

  Jamendo   (JAMENDO_CLIENT_ID) — a real music catalog with search and
            per-track Creative Commons licensing; ordered by THIS MONTH'S
            popularity so results skew current.
  Openverse (anonymous/auth)    — the CC audio aggregator (Jamendo, FMA,
            Wikimedia...) and always available. Production can authenticate
            for a larger, steadier quota.

Both configured catalogs are searched concurrently. Results are quality
ranked inside each source and interleaved, so one provider's latency, outage,
or house style cannot silently decide the soundtrack.

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
set_music_fit and get_audio_analysis all work on them unchanged.
"""

import re
import subprocess
import threading
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import net_fetch

JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
OPENVERSE_API = "https://api.openverse.org/v1/audio/"
OPENVERSE_TOKEN_API = "https://api.openverse.org/v1/auth_tokens/token/"

MAX_RESULTS = 12
API_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 180


class MusicSearchError(Exception):
    pass


_openverse_token = None
_openverse_token_expires_at = 0.0
_openverse_token_retry_at = 0.0
_openverse_token_lock = threading.Lock()


def _openverse_auth_headers(force_refresh=False):
    """Return a bearer header when credentials are configured.

    Openverse client-credential tokens currently last ten hours.  Refreshing
    five minutes early keeps an edit from hitting the expiry boundary.  Token
    acquisition failure intentionally degrades to the anonymous API: search
    must not go down merely because an optional credential is stale.
    """
    static = config.OPENVERSE_API_TOKEN
    if static:
        return {"Authorization": f"Bearer {static}"}
    if not (config.OPENVERSE_CLIENT_ID and config.OPENVERSE_CLIENT_SECRET):
        return {}
    global _openverse_token, _openverse_token_expires_at
    global _openverse_token_retry_at
    now = time.monotonic()
    if (not force_refresh and _openverse_token
            and now < _openverse_token_expires_at):
        return {"Authorization": f"Bearer {_openverse_token}"}
    if not force_refresh and now < _openverse_token_retry_at:
        return {}
    with _openverse_token_lock:
        now = time.monotonic()
        if (not force_refresh and _openverse_token
                and now < _openverse_token_expires_at):
            return {"Authorization": f"Bearer {_openverse_token}"}
        try:
            data = net_fetch.post_form_json(
                OPENVERSE_TOKEN_API,
                data={"grant_type": "client_credentials",
                      "client_id": config.OPENVERSE_CLIENT_ID,
                      "client_secret": config.OPENVERSE_CLIENT_SECRET},
                timeout_s=API_TIMEOUT_S,
                allowed_hosts=["api.openverse.org"])
            token = str(data.get("access_token") or "").strip()
            if not token:
                raise MusicSearchError("Openverse returned no access token")
            ttl = max(60, int(data.get("expires_in") or 36000))
            _openverse_token = token
            _openverse_token_expires_at = time.monotonic() + max(30, ttl - 300)
            _openverse_token_retry_at = 0.0
            return {"Authorization": f"Bearer {token}"}
        except Exception:
            _openverse_token = None
            _openverse_token_expires_at = 0.0
            # A bad secret or provider outage must not add a 20-second OAuth
            # failure to every search in the same edit. Anonymous calls remain
            # usable while one worker-local retry is deferred.
            _openverse_token_retry_at = time.monotonic() + 300.0
            return {}


def _openverse_get_json(url, *, params=None):
    """GET Openverse with auth when available and never fail on bad auth.

    A 401 with a cached client token gets one refresh.  A pre-issued token or
    broken credentials then get one anonymous attempt, which is important:
    optional auth may raise limits, but can never become a catalog kill switch.
    """
    headers = _openverse_auth_headers()
    try:
        return net_fetch.get_json(
            url, params=params, headers=headers, timeout_s=API_TIMEOUT_S,
            allowed_hosts=["api.openverse.org"])
    except net_fetch.FetchError as exc:
        if "HTTP 401" not in str(exc) or not headers:
            raise
    if (not config.OPENVERSE_API_TOKEN
            and config.OPENVERSE_CLIENT_ID and config.OPENVERSE_CLIENT_SECRET):
        refreshed = _openverse_auth_headers(force_refresh=True)
        if refreshed:
            try:
                return net_fetch.get_json(
                    url, params=params, headers=refreshed,
                    timeout_s=API_TIMEOUT_S,
                    allowed_hosts=["api.openverse.org"])
            except net_fetch.FetchError as exc:
                if "HTTP 401" not in str(exc):
                    raise
    return net_fetch.get_json(
        url, params=params, timeout_s=API_TIMEOUT_S,
        allowed_hosts=["api.openverse.org"])


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


def _jamendo_search(query, min_s, max_s, count, commercial_only=False):
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
        if commercial_only and "nc" in lic.lower():
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


def _openverse_search(query, min_s, max_s, count, commercial_only=False):
    # modification only: syncing music under a video is a derivative work,
    # so ND licenses must never even be requested. NC results remain welcome
    # for personal use and are filtered locally for commercial briefs.
    params = {"q": query, "license_type": "modification",
              "category": "music",
              # Pull enough candidates that filtering NC locally does not
              # turn the first page into a false "no music" result.
              "page_size": min(20, count * (3 if commercial_only else 1))}
    data = _openverse_get_json(OPENVERSE_API, params=params)
    out = []
    for t in (data.get("results") or []):
        lic = "-".join(x for x in (t.get("license"),
                                   t.get("license_version")) if x)
        if not _license_ok(t.get("license") or ""):
            continue
        if commercial_only and "nc" in lic.lower():
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


def _quality_score(item, query, min_s=None, max_s=None):
    """Provider-neutral editorial relevance for a music results grid."""
    title = str(item.get("title") or "").casefold()
    query = " ".join(re.findall(r"[\w]+", str(query or "").casefold()))
    words = [word for word in query.split() if len(word) > 2]
    score = 0.0
    if query and query in title:
        score += 10.0
    score += 2.5 * sum(1 for word in words if word in title)
    if "nc" in str(item.get("license") or "").casefold():
        score -= 8.0
    try:
        duration = float(item.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration:
        if min_s is not None and duration >= float(min_s):
            score += 1.5
        if max_s is not None and duration <= float(max_s):
            score += 1.5
        if min_s is None and max_s is None and 45 <= duration <= 360:
            score += 1.0
    if any(term in title for term in ("test tone", "sound check",
                                      "full album", "compilation")):
        score -= 10.0
    return score


def _diverse_rank(provider_hits, query, min_s, max_s, count):
    """Quality-sort within catalogs, then interleave their house styles."""
    buckets = {}
    for provider, hits in provider_hits:
        ranked = sorted(enumerate(hits), key=lambda row: (
            -_quality_score(row[1], query, min_s, max_s), row[0],
            str(row[1].get("id") or "")))
        if ranked:
            buckets[provider] = [item for _idx, item in ranked]
    out = []
    while buckets and len(out) < count:
        order = sorted(buckets, key=lambda name: (
            -_quality_score(buckets[name][0], query, min_s, max_s), name))
        for name in order:
            if len(out) >= count:
                break
            out.append(buckets[name].pop(0))
            if not buckets[name]:
                del buckets[name]
    return out


def search(query, min_s=None, max_s=None, count=MAX_RESULTS,
           commercial_only=False):
    """Search every provider in parallel and return a diverse ranked page."""
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
    got = {}
    with ThreadPoolExecutor(max_workers=max(1, len(lanes))) as pool:
        futures = {
            pool.submit(fn, query, min_s, max_s, count, commercial_only): name
            for name, fn in lanes}
        for future in as_completed(futures):
            name = futures[future]
            try:
                got[name] = future.result()
            except Exception as e:
                errors.append(f"{name}: {str(e)[:120]}")
    provider_hits = [(name, got.get(name) or []) for name, _fn in lanes]
    if any(hits for _name, hits in provider_hits):
        return _diverse_rank(provider_hits, query, min_s, max_s, count)
    if errors and len(errors) == len(lanes):
        raise MusicSearchError("; ".join(errors))
    return []


def resolve(result_id):
    """Recover a provider result by its stable id on a later agent turn."""
    raw = str(result_id or "").strip()
    provider, sep, ident = raw.partition(":")
    if not sep or not ident:
        raise MusicSearchError("result id must include its provider prefix")
    if provider == "openverse":
        try:
            uuidlib.UUID(ident)
        except (ValueError, AttributeError):
            raise MusicSearchError("that Openverse result id is not valid")
        data = _openverse_get_json(f"{OPENVERSE_API}{ident}/")
        lic = "-".join(x for x in (data.get("license"),
                                    data.get("license_version")) if x)
        if not _license_ok(data.get("license") or ""):
            raise MusicSearchError("that result has no usable remix license")
        try:
            dur = round(float(data.get("duration") or 0) / 1000.0, 1) or None
        except (TypeError, ValueError):
            dur = None
        url = data.get("url")
        if not url:
            raise MusicSearchError("that result no longer has downloadable audio")
        return {"provider": "openverse", "id": raw,
                "title": (data.get("title") or "").strip() or "untitled",
                "artist": (data.get("creator") or "").strip() or None,
                "duration_s": dur, "license": lic,
                "page_url": data.get("foreign_landing_url"), "_url": url}
    if provider == "jamendo":
        if not config.JAMENDO_CLIENT_ID or not ident.isdigit():
            raise MusicSearchError("that Jamendo result cannot be resolved")
        data = net_fetch.get_json(
            JAMENDO_API,
            params={"client_id": config.JAMENDO_CLIENT_ID, "format": "json",
                    "id": ident, "include": "licenses"},
            timeout_s=API_TIMEOUT_S, allowed_hosts=["api.jamendo.com"])
        rows = data.get("results") or []
        if not rows:
            raise MusicSearchError("that Jamendo result no longer exists")
        t = rows[0]
        lic = t.get("license_ccurl") or ""
        url = t.get("audiodownload") or t.get("audio")
        if not (_CC_LICENSE.search(lic) and _license_ok(lic) and url):
            raise MusicSearchError("that result is no longer downloadable")
        return {"provider": "jamendo", "id": raw,
                "title": (t.get("name") or "").strip() or "untitled",
                "artist": (t.get("artist_name") or "").strip() or None,
                "duration_s": float(t.get("duration") or 0) or None,
                "license": lic, "page_url": t.get("shareurl"), "_url": url}
    raise MusicSearchError(f"unsupported result provider '{provider}'")


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
