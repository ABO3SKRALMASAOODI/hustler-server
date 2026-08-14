"""Stock b-roll: search a library, pull the right file, hand back an asset.

The agent can already cut, caption and grade what the user uploaded. What it
could not do is ADD footage the user does not have — "show a shot of a busy
city" needed a clip from somewhere. This module is that somewhere.

Three providers, searched together:

  Pexels    (PEXELS_API_KEY)  — video + photo, the better-curated library
  Pixabay   (PIXABAY_API_KEY) — video + photo, the fallback library
  Openverse (anonymous/auth)  — PHOTO ONLY, and a different kind of photo:
            Wikimedia Commons / Flickr / museum collections, which is where
            pictures of REAL subjects live — a named person, a company, a
            rocket on its pad. Stock libraries answer "a busy city";
            Openverse answers "Elon Musk". Licenses are labels, not walls
            (the music_search rules): public domain / credit /
            NON-COMMERCIAL-ONLY stated per hit, only no-derivatives
            excluded. Real topical VIDEO is find_footage's job (the web's
            video search), not a stock library's.

Design notes that matter for quality, because "a stock clip appeared" and "the
RIGHT stock clip appeared, at the right size" are very different products:

* ORIENTATION IS DERIVED, NOT GUESSED. The project's own output aspect picks
  portrait/landscape/square, so a 9:16 edit never gets a letterboxed 16:9
  b-roll dropped into it.
* THE FILE VARIANT IS CHOSEN, NOT DEFAULTED. Providers return every rendition
  from 360p to 4K under one result. We take the SMALLEST rendition that still
  covers the output frame. Taking the largest would burn the worker's disk and
  minutes of download for pixels the render throws away; taking the default
  would upscale.
* FAILURE IS HONEST. No key -> the capability reports itself off rather than
  returning nothing and letting the agent invent a clip. No results -> says so.
  Both are shapes the agent is otherwise tempted to paper over.

Downloads go through net_fetch, so the SSRF policy, the byte cap and the
wall-clock deadline documented there apply unchanged — a provider CDN is not
more trusted than any other host.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import net_fetch

PEXELS_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"
PIXABAY_API = "https://pixabay.com/api/"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"
OPENVERSE_IMAGE_API = "https://api.openverse.org/v1/images/"

API_TIMEOUT_S = float(os.getenv("STOCK_API_TIMEOUT_S", "12"))
DOWNLOAD_TIMEOUT_S = float(os.getenv("STOCK_DOWNLOAD_TIMEOUT_S", "90"))
MAX_VIDEO_BYTES = int(os.getenv("STOCK_MAX_VIDEO_BYTES", str(90 * 1024 * 1024)))
MAX_PHOTO_BYTES = int(os.getenv("STOCK_MAX_PHOTO_BYTES", str(15 * 1024 * 1024)))

# A search result set the model can actually reason about. More than this and
# the tool result becomes a wall of near-identical clips that costs tokens and
# buys no better a choice.
MAX_RESULTS = 8

KIND_VIDEO = "video"
KIND_PHOTO = "photo"


class StockError(Exception):
    """Anything the user should be told about in a plain sentence."""


def _openverse_search(query, kind, orientation, count):
    """The keyless topical-photo lane. Videos are not Openverse's medium —
    a video query returns [] here and the honest no-video-provider error
    happens in search()."""
    if kind != KIND_PHOTO:
        return []
    from music_search import (_license_note, _license_ok,
                              _openverse_get_json)
    # Ask Openverse for production-sized originals up front. Relevance-only
    # search can otherwise lead with a perfectly topical 333px Flickr image;
    # it passes validation but visibly falls apart on a 1080x1920 canvas.
    # Pull a wider slate because licensing and dead-link checks may remove
    # several candidates before the editor sees the contact sheet.
    params = {"q": query, "license_type": "modification",
              "size": "large", "mature": "false", "filter_dead": "true",
              "unstable__authority": "true",
              "page_size": max(12, count * 3)}
    aspect = {"portrait": "tall", "landscape": "wide",
              "square": "square"}.get(orientation)
    if aspect:
        params["aspect_ratio"] = aspect
    data = _openverse_get_json(OPENVERSE_IMAGE_API, params=params)
    out = []
    for p in (data.get("results") or []):
        if not _license_ok(p.get("license") or ""):
            continue
        url = p.get("url")
        if not url:
            continue
        lic = "-".join(x for x in (p.get("license"),
                                   p.get("license_version")) if x)
        creator = (p.get("creator") or "").strip() or None
        src = (p.get("source") or p.get("provider") or "").strip()
        title = (p.get("title") or "").strip() or None
        out.append({
            "provider": "openverse", "kind": KIND_PHOTO,
            "id": f"openverse:photo:{p.get('id')}",
            "width": p.get("width"), "height": p.get("height"),
            "duration_s": None,
            "description": (f"{title} [{src}]" if title and src
                            else title or src or None),
            "credit": creator,
            "license": lic,
            "license_note": _license_note(lic, creator),
            "page_url": p.get("foreign_landing_url"),
            "_url": url,
            "_thumb": p.get("thumbnail") or url,
        })
    return out


def available():
    """Photos are always available (Openverse is keyless); VIDEO stock still
    needs a keyed library — search() says so honestly per call."""
    return bool(config.STOCK_SEARCH_ENABLED)


def video_available():
    return available() and bool(PEXELS_KEY or PIXABAY_KEY)


def _quality_score(item, query, orientation):
    """Provider-neutral score for professional, usable B-roll candidates."""
    qwords = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower())
              if len(w) > 2}
    desc = (item.get("description") or "").lower()
    score = 2.5 * sum(1 for w in qwords if w in desc)
    try:
        w, h = float(item.get("width") or 0), float(item.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if w and h:
        score += min(5.0, (w * h) / (1920 * 1080) * 2.0)
        if orientation == orientation_for(w, h):
            score += 3.0
    dur = item.get("duration_s")
    try:
        dur = float(dur or 0)
    except (TypeError, ValueError):
        dur = 0
    if item.get("kind") == KIND_VIDEO and dur:
        score += 3.0 if 4 <= dur <= 25 else 1.0 if dur <= 45 else -2.0
    # These titles frequently signal wallpaper/SEO filler rather than a shot
    # with a clear subject and editorial purpose.
    score -= 2.5 * sum(1 for term in
                       ("background", "wallpaper", "loop", "abstract",
                        "compilation", "template") if term in desc)
    return score


def _diverse_rank(provider_hits, query, orientation, count):
    """Quality-sort within each library, then interleave the libraries.

    Returning the first non-empty provider made every result grid Pexels when
    that key existed — exactly the homogeneous stock look users recognize.
    Interleaving preserves quality while guaranteeing the sighted agent sees
    genuinely different libraries before choosing by thumbnail.
    """
    buckets = {}
    for name, hits in provider_hits:
        ranked = sorted(hits, key=lambda h: (
            -_quality_score(h, query, orientation),
            str(h.get("id") or "")))
        if ranked:
            buckets[name] = ranked
    out = []
    while buckets and len(out) < count:
        # The provider whose current best is strongest leads this round; each
        # other live provider still contributes one before anyone gets two.
        order = sorted(buckets, key=lambda name: (
            -_quality_score(buckets[name][0], query, orientation), name))
        for name in order:
            if len(out) >= count:
                break
            out.append(buckets[name].pop(0))
            if not buckets[name]:
                del buckets[name]
    return out


def providers():
    out = [n for n, k in (("pexels", PEXELS_KEY),
                          ("pixabay", PIXABAY_KEY)) if k]
    out.append("openverse")
    return out


def orientation_for(width, height):
    """The project's output frame -> the orientation to search for."""
    try:
        w, h = float(width or 0), float(height or 0)
    except (TypeError, ValueError):
        return "landscape"
    if w <= 0 or h <= 0:
        return "landscape"
    r = w / h
    if r < 0.95:
        return "portrait"
    if r > 1.05:
        return "landscape"
    return "square"


def _pick_video_file(files, want_w, want_h):
    """Smallest rendition that still covers the output frame.

    Providers hand back everything from 360p to 4K. Covering the frame is what
    stops an upscale; picking the SMALLEST that covers is what stops a 4K
    download for a 1080p timeline. If nothing covers (rare, tiny sources), the
    largest available is the closest we can get and is used instead.
    """
    usable = [f for f in files
              if f.get("link") and (f.get("width") or 0) > 0
              and str(f.get("file_type", "video/mp4")).startswith("video")]
    if not usable:
        return None
    usable.sort(key=lambda f: (f.get("width", 0), f.get("height", 0)))
    for f in usable:
        if f.get("width", 0) >= want_w and f.get("height", 0) >= want_h:
            return f
    return usable[-1]


# ── Pexels ───────────────────────────────────────────────────────────────

def _pexels_search(query, kind, orientation, count):
    url = PEXELS_VIDEO_API if kind == KIND_VIDEO else PEXELS_PHOTO_API
    params = {"query": query, "per_page": count}
    if orientation in ("landscape", "portrait", "square"):
        params["orientation"] = orientation
    data = net_fetch.get_json(
        url, params=params, timeout_s=API_TIMEOUT_S,
        allowed_hosts=["api.pexels.com"],
        headers={"Authorization": PEXELS_KEY})

    out = []
    if kind == KIND_VIDEO:
        for v in (data.get("videos") or []):
            out.append({
                "provider": "pexels", "kind": KIND_VIDEO,
                "id": f"pexels:video:{v.get('id')}",
                "width": v.get("width"), "height": v.get("height"),
                "duration_s": v.get("duration"),
                "description": (v.get("alt") or "").strip() or None,
                "credit": ((v.get("user") or {}).get("name") or "").strip() or None,
                "page_url": v.get("url"),
                "_files": v.get("video_files") or [],
                "_thumb": v.get("image"),
            })
    else:
        for p in (data.get("photos") or []):
            src = p.get("src") or {}
            out.append({
                "provider": "pexels", "kind": KIND_PHOTO,
                "id": f"pexels:photo:{p.get('id')}",
                "width": p.get("width"), "height": p.get("height"),
                "duration_s": None,
                "description": (p.get("alt") or "").strip() or None,
                "credit": (p.get("photographer") or "").strip() or None,
                "page_url": p.get("url"),
                "_url": src.get("large2x") or src.get("original") or src.get("large"),
                "_thumb": src.get("medium") or src.get("small"),
            })
    return out


# ── Pixabay ──────────────────────────────────────────────────────────────

def _pixabay_search(query, kind, orientation, count):
    url = PIXABAY_VIDEO_API if kind == KIND_VIDEO else PIXABAY_API
    params = {"key": PIXABAY_KEY, "q": query, "per_page": max(3, count),
              "safesearch": "true"}
    if kind == KIND_PHOTO:
        params["image_type"] = "photo"
        if orientation == "portrait":
            params["orientation"] = "vertical"
        elif orientation == "landscape":
            params["orientation"] = "horizontal"
    data = net_fetch.get_json(url, params=params, timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["pixabay.com"])

    out = []
    for h in (data.get("hits") or []):
        tags = (h.get("tags") or "").strip() or None
        credit = (h.get("user") or "").strip() or None
        if kind == KIND_VIDEO:
            vids = h.get("videos") or {}
            # Normalise Pixabay's named sizes into the same shape Pexels uses,
            # so _pick_video_file is the ONE renditions rule for both.
            files = [{"link": v.get("url"), "width": v.get("width"),
                      "height": v.get("height"), "file_type": "video/mp4"}
                     for v in vids.values() if isinstance(v, dict) and v.get("url")]
            if not files:
                continue
            biggest = max(files, key=lambda f: f.get("width") or 0)
            out.append({
                "provider": "pixabay", "kind": KIND_VIDEO,
                "id": f"pixabay:video:{h.get('id')}",
                "width": biggest.get("width"), "height": biggest.get("height"),
                "duration_s": h.get("duration"),
                "description": tags, "credit": credit,
                "page_url": h.get("pageURL"), "_files": files,
            })
        else:
            link = h.get("largeImageURL") or h.get("webformatURL")
            if not link:
                continue
            out.append({
                "provider": "pixabay", "kind": KIND_PHOTO,
                "id": f"pixabay:photo:{h.get('id')}",
                "width": h.get("imageWidth"), "height": h.get("imageHeight"),
                "duration_s": None, "description": tags, "credit": credit,
                "page_url": h.get("pageURL"), "_url": link,
                "_thumb": h.get("webformatURL") or h.get("previewURL"),
            })
    return out


# ── public API ───────────────────────────────────────────────────────────

def search(query, kind=KIND_VIDEO, orientation=None, count=MAX_RESULTS):
    """Search the configured providers. Returns [] when nothing matched.

    Every configured provider is searched in parallel. Results are quality-
    scored within each library and interleaved across libraries so the agent's
    visual contact sheet is not a wall of one provider's house style.
    """
    if not available():
        raise StockError("stock search is disabled on this deployment")
    query = (query or "").strip()
    if not query:
        raise StockError("a search query is required")
    count = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))
    if kind not in (KIND_VIDEO, KIND_PHOTO):
        raise StockError(f"unknown stock kind '{kind}'")
    if kind == KIND_VIDEO and not video_available():
        raise StockError(
            "no VIDEO stock library is configured — photos work "
            "(kind='photo'), and for real topical footage find_footage "
            "searches the web's video")

    lanes = [(n, f) for n, f, key in
             (("pexels", _pexels_search, PEXELS_KEY),
              ("pixabay", _pixabay_search, PIXABAY_KEY)) if key]
    if kind == KIND_PHOTO:
        lanes.append(("openverse", _openverse_search))
    errors, got = [], {}
    with ThreadPoolExecutor(max_workers=max(1, len(lanes))) as pool:
        futures = {pool.submit(fn, query, kind, orientation, count): name
                   for name, fn in lanes}
        for future in as_completed(futures):
            name = futures[future]
            try:
                got[name] = future.result()
            except Exception as e:
                # One provider being down must not take the capability with it.
                errors.append(f"{name}: {str(e)[:120]}")
    provider_hits = [(name, got.get(name) or []) for name, _fn in lanes]
    if any(hits for _name, hits in provider_hits):
        return _diverse_rank(provider_hits, query, orientation, count)
    if errors and len(errors) == len(lanes):
        raise StockError("; ".join(errors))
    return []


def resolve(item, want_w, want_h):
    """(download_url, max_bytes) for the rendition that fits this frame."""
    if item.get("kind") == KIND_PHOTO:
        if not item.get("_url"):
            raise StockError("that result has no downloadable image")
        return item["_url"], MAX_PHOTO_BYTES
    f = _pick_video_file(item.get("_files") or [], want_w, want_h)
    if not f:
        raise StockError("that result has no downloadable video file")
    item["picked_width"] = f.get("width")
    item["picked_height"] = f.get("height")
    return f["link"], MAX_VIDEO_BYTES


def download(item, out_path, want_w, want_h):
    """Fetch the chosen rendition to out_path. Returns the item, annotated."""
    url, cap = resolve(item, want_w, want_h)
    net_fetch.download(url, out_path, max_bytes=cap,
                       timeout_s=DOWNLOAD_TIMEOUT_S)
    item["source_url"] = url
    return item


def summarize(items):
    """One compact line per hit for the agent to choose from."""
    lines = []
    for i in items:
        bits = []
        if i.get("width") and i.get("height"):
            bits.append(f"{i['width']}x{i['height']}")
        if i.get("duration_s"):
            bits.append(f"{int(i['duration_s'])}s")
        if i.get("credit"):
            bits.append(f"by {i['credit']}")
        # The Openverse lane carries a real license obligation per hit —
        # the line the agent reads must say it (the music_search contract).
        if i.get("license_note"):
            bits.append(i["license_note"])
        desc = i.get("description") or "(no description)"
        lines.append(f"  {i['id']} — {desc[:90]} ({', '.join(bits)})")
    return "\n".join(lines)
