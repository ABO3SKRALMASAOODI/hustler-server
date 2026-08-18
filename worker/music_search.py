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

KNOWN LICENSING IS INFORMATION, NOT A HIDDEN WALL. Every offered hit carries
the provider's raw license metadata and a separate normalized obligation —
public domain, credit-required, or NON-COMMERCIAL-ONLY — so the choice is
made with visible evidence. Unrecognized/absent terms are excluded rather
than silently rewritten as CC BY. No-derivatives (ND) is also excluded:
syncing music in timed relation with picture is itself an adaptation, so an
ND track cannot be used in ANY edit, monetized or not. For a SPECIFIC
copyrighted song the
path is the user's own file or link: fetch_url ingests anything a URL can
reach, and the trending-sound flow (cut to the grid, platform adds the
licensed audio in-app) stays the honest route for sounds the platforms
license only inside their own apps.

Downloads go through net_fetch (SSRF policy, byte cap, wall clock) and land
as ordinary project assets (kind 'music'), so add_music, swap_music,
set_music_fit and get_audio_analysis all work on them unchanged.
"""

import datetime as dt
import json
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


# License handling: provider claims and our interpretation stay separate.
# Unknown provider text must never fall through to a permissive CC-BY label.
# The one categorical exclusion is the no-derivatives family — syncing music
# under picture is itself an adaptation, so an ND track is unusable in any
# edit. Search also excludes unrecognized terms: without a known grant there
# is no rights basis for the editor to use the track.
_CC_LICENSE = re.compile(r"(cc0|pdm|publicdomain|zero|creativecommons|"
                         r"^by|/by)", re.I)

_CC_FAMILY_CODE = r"by-nc-nd|by-nc-sa|by-nc|by-nd|by-sa|by"


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _license_family(license_id):
    """Return a narrow normalized family only for recognized CC/PD terms.

    This is an interpretation used to derive simple capability signals. The
    raw provider value remains separately preserved and is always the value a
    caller should inspect when it needs provenance.
    """
    lid = str(license_id or "").strip().lower().replace("_", "-")
    if not lid:
        return None
    # For URLs, accept only Creative Commons' own host/path vocabulary. A
    # random URL with ``?by=...`` or prose containing the word "by" is not a
    # license and must remain unknown.
    if "://" in lid:
        if not re.match(
                r"^https?://(?:www\.)?creativecommons\.org/", lid):
            return None
        if ("/publicdomain/zero/" in lid or
                "/publicdomain/mark/" in lid):
            return "public-domain"
        match = re.search(
            rf"/licenses/({_CC_FAMILY_CODE})(?:/|$)", lid)
        return match.group(1) if match else None

    compact = re.sub(r"[\s]+", "-", lid).strip("-/")
    if re.fullmatch(r"(?:cc-?)?0(?:[- ]?1(?:\.0)?)?|cc0(?:-1\.0)?|"
                    r"pdm(?:-1\.0)?|public-domain-mark(?:-1\.0)?",
                    compact):
        return "public-domain"
    match = re.fullmatch(
        rf"(?:cc-)?({_CC_FAMILY_CODE})(?:-v?\d+(?:\.\d+)*)?",
        compact)
    return match.group(1) if match else None


def _license_capabilities(license_id):
    """Mechanical rights signals for a recognized provider license value.

    ``None`` means unknown, not false and never a permissive default.
    """
    family = _license_family(license_id)
    if not family:
        return {
            "normalized_license_family": None,
            "commercial_use_allowed": None,
            "attribution_required": None,
            "derivatives_allowed": None,
        }
    public_domain = family == "public-domain"
    return {
        "normalized_license_family": family,
        "commercial_use_allowed": (True if public_domain else
                                   "nc" not in family),
        "attribution_required": False if public_domain else True,
        "derivatives_allowed": (True if public_domain else
                                "nd" not in family),
    }


def provenance(item):
    """Stable, JSON-safe provider provenance with explicit unknowns.

    New catalog hits carry each raw field directly. Compatibility fallbacks
    read older cached hits/assets without manufacturing a label or canonical
    landing page that was never captured.
    """
    item = item or {}
    provider = item.get("provider") or item.get("source") or None
    result_id = item.get("id") or item.get("result_id") or None
    candidate_id = item.get("provider_candidate_id")
    if (candidate_id is None and provider and result_id
            and str(result_id).startswith(f"{provider}:")):
        candidate_id = str(result_id).split(":", 1)[1] or None
    raw_id = item.get("provider_reported_license_id")
    if raw_id is None:
        # ``license`` is the compatibility field historically persisted from
        # the catalog response. It is raw/composed provider text, not our note.
        raw_id = item.get("license") or None
    raw_label = item.get("provider_reported_license_label") or None
    raw_version = item.get("provider_reported_license_version") or None
    raw_url = item.get("provider_reported_license_url") or None
    raw_license_basis = raw_id or raw_label or raw_url
    interpreted = _license_capabilities(raw_license_basis)
    provider_metadata_exposed = bool(
        provider and raw_license_basis and
        interpreted["normalized_license_family"])
    if not provider_metadata_exposed:
        interpreted = {
            "normalized_license_family": None,
            "commercial_use_allowed": None,
            "attribution_required": None,
            "derivatives_allowed": None,
        }
    canonical = (item.get("canonical_source_url") or
                 item.get("page_url") or None)
    return {
        "provider": provider,
        "provider_candidate_id": candidate_id,
        "provider_reported_license_id": raw_id,
        "provider_reported_license_label": raw_label,
        "provider_reported_license_version": raw_version,
        "provider_reported_license_url": raw_url,
        "license_verification_status": (
            "provider_metadata_exposed"
            if provider_metadata_exposed else "unknown"),
        **interpreted,
        "canonical_source_url": canonical,
        # A legacy source URL may be a landing page or a media endpoint. Keep
        # it visible, but do not relabel it canonical without direct evidence.
        "source_url": item.get("source_url") or canonical,
        "creator": (item.get("creator") or item.get("artist") or
                    item.get("author") or None),
        "provider_retrieved_at": item.get("provider_retrieved_at") or None,
        "downloaded_sha256": item.get("downloaded_sha256") or None,
    }


def usable(item, commercial_only=False):
    """Whether a catalog item has a known grant usable for this edit."""
    rights = provenance(item)
    if rights["license_verification_status"] != "provider_metadata_exposed":
        return False
    if rights["derivatives_allowed"] is not True:
        return False
    if commercial_only and rights["commercial_use_allowed"] is not True:
        return False
    return True


def _license_note(license_id, author):
    family = _license_family(license_id)
    if family == "public-domain":
        return "public domain — no obligations"
    if not family:
        return ("license UNKNOWN — no use permission established; do not use "
                "until the rights are verified")
    who = author or "the artist"
    if "nd" in family:
        return (f"CC {family.upper()} — NO DERIVATIVES: not usable for "
                "syncing to video")
    if "nc" in family:
        # Checked before by-sa: "by-nc-sa" is NC first — the commercial
        # restriction is the fact that changes what the user may do.
        return (f"CC {family.upper()} — "
                "NON-COMMERCIAL USE ONLY: fine for a personal video, NOT "
                f"for monetized/business content; credit {who} in the "
                "caption/description")
    if family == "by-sa":
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
    retrieved_at = _utc_now()
    for t in (data.get("results") or []):
        lic = t.get("license_ccurl") or ""
        url = t.get("audiodownload") or t.get("audio")
        candidate_id = t.get("id")
        if not url or candidate_id is None:
            continue
        item = {
            "provider": "jamendo", "id": f"jamendo:{candidate_id}",
            "provider_candidate_id": str(candidate_id),
            "title": (t.get("name") or "").strip() or "untitled",
            "artist": (t.get("artist_name") or "").strip() or None,
            "duration_s": float(t.get("duration") or 0) or None,
            "license": lic,
            "provider_reported_license_id": lic or None,
            "provider_reported_license_label": None,
            "provider_reported_license_version": None,
            "provider_reported_license_url": lic or None,
            "page_url": t.get("shareurl"),
            "canonical_source_url": t.get("shareurl"),
            "source_url": t.get("shareurl"),
            "provider_retrieved_at": retrieved_at,
            "_url": url,
        }
        if usable(item, commercial_only=commercial_only):
            out.append(item)
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
    retrieved_at = _utc_now()
    for t in (data.get("results") or []):
        lic = "-".join(x for x in (t.get("license"),
                                   t.get("license_version")) if x)
        url = t.get("url")
        candidate_id = t.get("id")
        if not url or candidate_id is None:
            continue
        dur = None
        try:
            dur = round(float(t.get("duration") or 0) / 1000.0, 1) or None
        except (TypeError, ValueError):
            dur = None
        if dur and ((min_s and dur < min_s) or (max_s and dur > max_s)):
            continue
        item = {
            "provider": "openverse", "id": f"openverse:{candidate_id}",
            "provider_candidate_id": str(candidate_id),
            "title": (t.get("title") or "").strip() or "untitled",
            "artist": (t.get("creator") or "").strip() or None,
            "duration_s": dur,
            "license": lic,
            "provider_reported_license_id": t.get("license") or None,
            "provider_reported_license_label": None,
            "provider_reported_license_version": (
                t.get("license_version") or None),
            "provider_reported_license_url": t.get("license_url") or None,
            "page_url": t.get("foreign_landing_url"),
            "canonical_source_url": t.get("foreign_landing_url"),
            "source_url": t.get("foreign_landing_url"),
            "provider_retrieved_at": retrieved_at,
            "_url": url,
        }
        if usable(item, commercial_only=commercial_only):
            out.append(item)
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
    if provenance(item)["commercial_use_allowed"] is False:
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
        try:
            dur = round(float(data.get("duration") or 0) / 1000.0, 1) or None
        except (TypeError, ValueError):
            dur = None
        url = data.get("url")
        if not url:
            raise MusicSearchError("that result no longer has downloadable audio")
        item = {
            "provider": "openverse", "id": raw,
            "provider_candidate_id": ident,
            "title": (data.get("title") or "").strip() or "untitled",
            "artist": (data.get("creator") or "").strip() or None,
            "duration_s": dur, "license": lic,
            "provider_reported_license_id": data.get("license") or None,
            "provider_reported_license_label": None,
            "provider_reported_license_version": (
                data.get("license_version") or None),
            "provider_reported_license_url": data.get("license_url") or None,
            "page_url": data.get("foreign_landing_url"),
            "canonical_source_url": data.get("foreign_landing_url"),
            "source_url": data.get("foreign_landing_url"),
            "provider_retrieved_at": _utc_now(),
            "_url": url,
        }
        if not usable(item):
            raise MusicSearchError(
                "that result has no recognized usable remix license")
        return item
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
        if not url:
            raise MusicSearchError("that result is no longer downloadable")
        item = {
            "provider": "jamendo", "id": raw,
            "provider_candidate_id": ident,
            "title": (t.get("name") or "").strip() or "untitled",
            "artist": (t.get("artist_name") or "").strip() or None,
            "duration_s": float(t.get("duration") or 0) or None,
            "license": lic,
            "provider_reported_license_id": lic or None,
            "provider_reported_license_label": None,
            "provider_reported_license_version": None,
            "provider_reported_license_url": lic or None,
            "page_url": t.get("shareurl"),
            "canonical_source_url": t.get("shareurl"),
            "source_url": t.get("shareurl"),
            "provider_retrieved_at": _utc_now(),
            "_url": url,
        }
        if not usable(item):
            raise MusicSearchError(
                "that result has no recognized usable remix license")
        return item
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


def probe_audio_stream(path):
    """Strict factual first-audio-stream probe for a fetched candidate.

    Unlike the renderer's permissive playback fallback, a failed probe is
    explicit ``unavailable`` and never becomes ``has_audio_stream=true``.
    """
    fact = {
        "source_audio_stream_status": "unavailable",
        "source_has_audio_stream": None,
        "source_audio_stream_codec": None,
        "source_audio_stream_channels": None,
        "source_audio_stream_probe_tool": "ffprobe",
        "source_audio_stream_probed_at": _utc_now(),
    }
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index,codec_name,channels",
             "-of", "json", path], capture_output=True, timeout=30)
        if result.returncode != 0:
            return fact
        payload = json.loads((result.stdout or b"").decode() or "{}")
        streams = payload.get("streams") or []
        if not isinstance(streams, list):
            return fact
        fact["source_audio_stream_status"] = "complete"
        fact["source_has_audio_stream"] = bool(streams)
        if streams:
            stream = streams[0] if isinstance(streams[0], dict) else {}
            fact["source_audio_stream_codec"] = (
                str(stream.get("codec_name") or "").strip() or None)
            try:
                channels = int(stream.get("channels"))
                fact["source_audio_stream_channels"] = (
                    channels if channels > 0 else None)
            except (TypeError, ValueError):
                pass
        return fact
    except Exception:
        return fact


def describe(item):
    """One line per hit, license obligation included — what the agent reads
    and what it must pass on."""
    bits = []
    if item.get("artist"):
        bits.append(f"by {item['artist']}")
    if item.get("duration_s"):
        bits.append(f"{item['duration_s']:.0f}s")
    rights = provenance(item)
    bits.append(
        "tool_normalized_license_note=" +
        _license_note(rights["provider_reported_license_id"] or
                      rights["provider_reported_license_label"] or
                      rights["provider_reported_license_url"],
                      rights["creator"]))
    truth = lambda value: ("true" if value is True else
                           "false" if value is False else "unknown")
    raw = lambda value: str(value) if value not in (None, "") else "unknown"
    bits.extend([
        f"provider={raw(rights['provider'])}",
        f"provider_candidate_id={raw(rights['provider_candidate_id'])}",
        ("provider_reported_license_id="
         f"{raw(rights['provider_reported_license_id'])}"),
        ("provider_reported_license_label="
         f"{raw(rights['provider_reported_license_label'])}"),
        ("provider_reported_license_version="
         f"{raw(rights['provider_reported_license_version'])}"),
        ("provider_reported_license_url="
         f"{raw(rights['provider_reported_license_url'])}"),
        ("license_verification_status="
         f"{rights['license_verification_status']}"),
        ("normalized_license_family="
         f"{raw(rights['normalized_license_family'])}"),
        f"commercial_use_allowed={truth(rights['commercial_use_allowed'])}",
        f"attribution_required={truth(rights['attribution_required'])}",
        f"derivatives_allowed={truth(rights['derivatives_allowed'])}",
        f"canonical_source_url={raw(rights['canonical_source_url'])}",
        f"source_url={raw(rights['source_url'])}",
        f"creator={raw(rights['creator'])}",
        f"provider_retrieved_at={raw(rights['provider_retrieved_at'])}",
        f"downloaded_sha256={raw(rights['downloaded_sha256'])}",
    ])
    return f"{item['id']} — \"{item['title']}\" ({'; '.join(bits)})"


def license_note(item):
    rights = provenance(item)
    return _license_note(rights["provider_reported_license_id"] or
                         rights["provider_reported_license_label"] or
                         rights["provider_reported_license_url"],
                         rights["creator"])
