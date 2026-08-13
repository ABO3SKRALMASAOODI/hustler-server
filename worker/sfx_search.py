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


class SfxSearchError(Exception):
    pass


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
    data = music_search._openverse_get_json(OPENVERSE_API, params=params)
    ranked = []
    for provider_i, t in enumerate(data.get("results") or []):
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
    return [row[2] for row in ranked[:count]]


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
