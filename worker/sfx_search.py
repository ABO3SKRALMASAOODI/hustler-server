"""Live sound-effect search: real editors' sounds found online, on demand.

The bundled pack is deleted (2026-08-08: its 18 sounds live in R2 under
legacy-sfx/ and every EDL reference was rewritten to those plain keys) and
AI sound GENERATION is gone with it — generated one-shots read as uncanny
under real footage, and the owner pulled them. What replaced both is the
thing editors actually do: fetch the real sound. Openverse (keyless, the
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

import config
import net_fetch
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
    data = net_fetch.get_json(OPENVERSE_API, params=params,
                              timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["api.openverse.org"])
    out = []
    for t in (data.get("results") or []):
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
        out.append({
            "provider": "openverse", "id": f"openverse:{t.get('id')}",
            "title": (t.get("title") or "").strip() or "untitled",
            "author": (t.get("creator") or "").strip() or None,
            "duration_s": dur, "license": lic,
            "page_url": t.get("foreign_landing_url"),
            "_url": url,
        })
        if len(out) >= count:
            break
    return out


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
