"""Live sound-effect search: real editors' sounds found online, on demand.

The bundled pack is deleted (2026-08-08: its 18 sounds live in R2 under
legacy-sfx/ and every EDL reference was rewritten to those plain keys) and
AI sound GENERATION is gone with it — generated one-shots read as uncanny
under real footage, and the owner pulled them. What replaced both is the
thing editors actually do: fetch the real sound. Openverse (anonymous or
authenticated, the
same aggregator music_search already trusts) fronts Freesound's half a
million recorded effects — the exact library a working editor's whoosh,
camera shutter and UI click already come from — served as direct CDN mp3s.

The music_search rules apply verbatim: licenses are LABELS, not walls
(public domain / credit / NON-COMMERCIAL-ONLY stated per hit, only
no-derivatives excluded, since layering a sound under picture is an
adaptation), and downloads ride net_fetch's SSRF policy into ordinary
project assets. One sfx-specific twist: results are duration-capped —
a "click" that runs four minutes is a field recording, not an accent.
"""

import math
import re

import config
import net_fetch
import music_search
from music_search import _license_note, _license_ok

OPENVERSE_API = "https://api.openverse.org/v1/audio/"

MAX_RESULTS = 12
API_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 120
# An accent is seconds long. The cap keeps ambience beds and full field
# recordings out of a picker meant for one-shots; a deliberate ambience
# search can raise it per call up to a minute.
DEFAULT_MAX_S = 15.0
HARD_MAX_S = 60.0

# A tiny outage catalog of real, verified CC0 Freesound recordings. These are
# Openverse result records, not generated/bundled audio: the CDN bytes and
# creator landing pages remain the source of truth. It exists so a transient
# Openverse search 401 cannot turn an otherwise-finished edit into "no SFX".
# Normal healthy searches still win and return a broader ranked slate.
_OUTAGE_CATALOG = (
    {"id": "f1dc6045-7965-41de-87f8-4731d2583fd2",
     "title": "Swosh swoosh whoosh air sound, free, high quality",
     "author": "qubodup", "duration_s": .49,
     "url": "https://cdn.freesound.org/previews/60/60026_71257-hq.mp3",
     "page": "https://freesound.org/people/qubodup/sounds/60026",
     "terms": ("whoosh", "swoosh", "swish", "transition", "sweep")},
    {"id": "b7ae7822-8ec5-4a82-b6f8-d539b51caa61",
     "title": "Swipe Whoosh", "author": "qubodup", "duration_s": .447,
     "url": "https://cdn.freesound.org/previews/60/60007_71257-hq.mp3",
     "page": "https://freesound.org/people/qubodup/sounds/60007",
     "terms": ("swipe", "whoosh", "swoosh", "transition")},
    {"id": "3849018d-0ac8-4f6b-b44d-5ec26a4b32af",
     "title": "Mouse click sounds", "author": "Masgame",
     "duration_s": 2.31,
     "url": "https://cdn.freesound.org/previews/347/347544_5662401-hq.mp3",
     "page": "https://freesound.org/people/Masgame/sounds/347544",
     "terms": ("click", "mouse", "ui", "button", "tap")},
    {"id": "3290f0e3-a217-48c9-bafb-06401b961c21",
     "title": "Camera Shutter", "author": "roachpowder",
     "duration_s": .295,
     "url": "https://cdn.freesound.org/previews/170/170229_3133582-hq.mp3",
     "page": "https://freesound.org/people/roachpowder/sounds/170229",
     "terms": ("camera", "shutter", "photo", "photograph", "snapshot")},
    {"id": "2ca2ac61-9ab4-4189-92d9-a369776ccfd9",
     "title": "Pop (made by DuffyBro)", "author": "DuffyBro",
     "duration_s": 2.191,
     "url": "https://cdn.freesound.org/previews/319/319107_5422458-hq.mp3",
     "page": "https://freesound.org/people/DuffyBro/sounds/319107",
     "terms": ("pop", "bubble", "caption", "reveal")},
    {"id": "210a2965-2408-40d4-a1fc-97caed05f251",
     "title": "Impact", "author": "chriskalos", "duration_s": 2.623,
     "url": "https://cdn.freesound.org/previews/172/172779_2430808-hq.mp3",
     "page": "https://freesound.org/people/chriskalos/sounds/172779",
     "terms": ("impact", "hit", "slam", "thump", "punch")},
    {"id": "96a9062b-be8d-42ae-aacc-f64d3a34294a",
     "title": "Riser", "author": "Rizzard", "duration_s": 2.0,
     "url": "https://cdn.freesound.org/previews/561/561207_10825267-hq.mp3",
     "page": "https://freesound.org/people/Rizzard/sounds/561207",
     "terms": ("riser", "rise", "build", "swell", "tension")},
    {"id": "ff071be3-b0d0-4f5c-96a4-fe8a5adb66ec",
     "title": "Outtake Beep-1k.wav", "author": "slappy13",
     "duration_s": .12,
     "url": "https://cdn.freesound.org/previews/151/151779_2704059-hq.mp3",
     "page": "https://freesound.org/people/slappy13/sounds/151779",
     "terms": ("beep", "bleep", "tone", "censor")},
    {"id": "e567288e-f223-4dc3-a586-6a86cad6626b",
     "title": "BOOM - 1.wav", "author": "zgump", "duration_s": .416,
     "url": "https://cdn.freesound.org/previews/86/86330_377011-hq.mp3",
     "page": "https://freesound.org/people/zgump/sounds/86330",
     "terms": ("boom", "explosion", "drop", "bass")},
)


class SfxSearchError(Exception):
    pass


def _outage_hits(query, cap, count):
    words = set(re.findall(r"[a-z0-9]+", str(query or "").casefold()))
    rows = []
    for raw in _OUTAGE_CATALOG:
        if raw["duration_s"] > cap or not words.intersection(raw["terms"]):
            continue
        item = {
            "provider": "openverse",
            "id": f"openverse:{raw['id']}",
            "title": raw["title"], "author": raw["author"],
            "duration_s": raw["duration_s"], "license": "cc0-1.0",
            "page_url": raw["page"], "_url": raw["url"],
        }
        rows.append((_rank_score(item, query, cap), item))
    rows.sort(key=lambda row: (-row[0], row[1]["id"]))
    return [item for _score, item in rows[:count]]


def available():
    return bool(config.SFX_SEARCH_ENABLED)


_NOISY_WORDS = {
    "ambience", "ambient", "atmosphere", "background", "compilation",
    "extended", "field recording", "full track", "loop", "music", "pack",
    "song", "soundscape", "ten minutes", "theme",
}
_ONE_SHOT_WORDS = {
    "clean", "dry", "foley", "hit", "impact", "one shot", "oneshot",
    "single", "sting", "transition",
}


def _rank_score(item, query, cap):
    """Editorial relevance score for a one-shot picker.

    Openverse's text rank is broad-audio rank: a search for "camera shutter"
    can put a four-minute ambience loop above a clean shutter transient. This
    second pass rewards literal physical matches and useful one-shot duration,
    while keeping provider order as a small tiebreaker rather than trusting it
    as the entire edit decision.
    """
    q = " ".join(re.findall(r"[\w]+", str(query or "").lower()))
    title = str(item.get("title") or "").lower()
    hay = " ".join(str(x or "") for x in (
        title, item.get("description"), item.get("tags"))).lower()
    qwords = [w for w in q.split() if len(w) > 1]
    score = 0.0
    if q and q in title:
        score += 12.0
    score += 3.0 * sum(1 for w in qwords if w in title)
    score += 1.0 * sum(1 for w in qwords if w in hay and w not in title)
    score += 2.5 * sum(1 for w in _ONE_SHOT_WORDS if w in hay)
    score -= 7.0 * sum(1 for w in _NOISY_WORDS if w in hay)

    dur = item.get("duration_s")
    if dur:
        # Physical transients should be tight; risers/whooshes need room.
        target = (3.0 if any(w in q for w in ("riser", "build", "swell"))
                  else 1.6 if any(w in q for w in ("whoosh", "swoosh"))
                  else 0.7 if any(w in q for w in
                                  ("click", "tap", "snap", "beep", "shutter"))
                  else 1.1)
        score += max(-5.0, 4.0 - abs(math.log(max(dur, 0.05) / target)) * 2.2)
        if dur > min(cap, 8.0):
            score -= 3.0
    return score


def search(query, max_s=None, count=MAX_RESULTS):
    query = (query or "").strip()
    if not query:
        raise SfxSearchError("a search query is required")
    count = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))
    cap = min(float(max_s or DEFAULT_MAX_S), HARD_MAX_S)
    # modification only: ND may not be layered into an edit at all. The
    # category field is unpopulated on Freesound entries, so the one-shot
    # shape is enforced by the duration cap, not a category filter.
    params = {"q": query, "license_type": "modification",
              "page_size": max(count * 2, 10)}
    try:
        data = music_search._openverse_get_json(OPENVERSE_API, params=params)
    except Exception as exc:
        fallback = _outage_hits(query, cap, count)
        if fallback:
            return fallback
        raise SfxSearchError(str(exc))
    raw_results = data.get("results") or []
    ranked = []
    for provider_i, t in enumerate(raw_results):
        if not _license_ok(t.get("license") or ""):
            continue
        url = t.get("url")
        if not url:
            continue
        dur = None
        try:
            dur = round(float(t.get("duration") or 0) / 1000.0, 2) or None
        except (TypeError, ValueError):
            dur = None
        if dur and dur > cap:
            continue
        lic = "-".join(x for x in (t.get("license"),
                                   t.get("license_version")) if x)
        item = {
            "provider": "openverse", "id": f"openverse:{t.get('id')}",
            "title": (t.get("title") or "").strip() or "untitled",
            "author": (t.get("creator") or "").strip() or None,
            "duration_s": dur, "license": lic,
            "page_url": t.get("foreign_landing_url"),
            "_url": url,
        }
        # Search-only evidence is retained for ranking and intentionally not
        # exposed to the EDL or storage metadata.
        rank_item = dict(item, description=t.get("description"),
                         tags=t.get("tags"))
        ranked.append((_rank_score(rank_item, query, cap), -provider_i, item))
    ranked.sort(key=lambda row: (-row[0], -row[1],
                                 (row[2].get("title") or "").lower(),
                                 row[2].get("id") or ""))
    hits = [row[2] for row in ranked[:count]]
    # An empty upstream page gets the verified physical fallback. A non-empty
    # page whose every result was rejected (for example all ND licenses) must
    # stay empty; the safety filter is not a provider outage.
    return hits or (_outage_hits(query, cap, count) if not raw_results else [])


def resolve(result_id):
    """Recover a stable Openverse hit after the search turn has ended."""
    if not str(result_id or "").startswith("openverse:"):
        raise SfxSearchError("sound result id must be an Openverse id")
    try:
        hit = music_search.resolve(result_id)
    except music_search.MusicSearchError as exc:
        raise SfxSearchError(str(exc))
    return {"provider": hit["provider"], "id": hit["id"],
            "title": hit["title"], "author": hit.get("artist"),
            "duration_s": hit.get("duration_s"), "license": hit.get("license"),
            "page_url": hit.get("page_url"), "_url": hit.get("_url")}


def download(item, out_path):
    """Fetch a hit's audio file through the net_fetch policy."""
    url = item.get("_url")
    if not url:
        raise SfxSearchError("that result has no downloadable audio")
    net_fetch.download(url, out_path,
                       max_bytes=config.SFX_FETCH_MAX_MB * 1024 * 1024,
                       timeout_s=DOWNLOAD_TIMEOUT_S)
    return item


def describe(item):
    bits = []
    if item.get("author"):
        bits.append(f"by {item['author']}")
    if item.get("duration_s"):
        bits.append(f"{item['duration_s']:g}s")
    bits.append(_license_note(item.get("license"), item.get("author")))
    return f"{item['id']} — \"{item['title']}\" ({', '.join(bits)})"


def license_note(item):
    return _license_note(item.get("license"), item.get("author"))
