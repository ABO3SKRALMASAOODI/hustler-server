"""Track 4C: where cinematic B-roll comes from (ROADMAP 4C "Owns").

Six libraries, searched in parallel, every hit carrying its licence:

  pexels, pixabay   keyed stock (PEXELS_API_KEY / PIXABAY_API_KEY), video
                    and photo (stock.py's provider lanes)
  openverse         keyless CC photos (stock.py's lane)
  wikimedia         Wikimedia Commons: video (transcoded renditions) and
                    photos, licence from the file's own metadata
  nasa              NASA Image and Video Library: video and photos, public
                    domain under NASA's media guidelines
  archive           Internet Archive moving images under public-domain / CC0
                    / CC BY(-SA) only (the archival lane)

Licence policy (``canon_licence``): public domain, CC0, CC BY, CC BY-SA and
the Pexels / Pixabay licences pass; NC, ND, "all rights reserved" and
unknown licences never reach the editor. Every candidate keeps its
provider, title, author, licence, licence URL and page — the ledger line the
``broll`` fx item carries.

Acquisition reuses the stock lane: ``stock.download`` (and on the redesign
policy the executor's ``stock_acquire`` job) fetches a hit by its
``_url`` (photo) or ``_files`` renditions (video); ``verify_media`` refuses
a "photo" that is really a web page (JRE defect 29) and a video nothing can
decode. Nothing here publishes, uploads to a provider or keeps reference
media.
"""

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import net_fetch

KIND_VIDEO = "video"
KIND_PHOTO = "photo"
PROVIDERS = ("pexels", "pixabay", "openverse", "wikimedia", "nasa",
             "archive")
KEYLESS = ("openverse", "wikimedia", "nasa", "archive")
API_TIMEOUT_S = 15.0
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
NASA_API = "https://images-api.nasa.gov/search"
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/"
MAX_VIDEO_BYTES = 90 * 1024 * 1024
NASA_LICENCE = ("Public domain (NASA media guidelines)",
                "https://www.nasa.gov/nasa-brand-center/images-and-media/")
_STOP = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to",
         "for", "with", "by", "from", "is", "it", "its", "as", "into"}


class BrollError(Exception):
    pass


# ── licences ─────────────────────────────────────────────────────────────

def canon_licence(raw, url=None):
    """(name, url, attribution_required) for a licence a cutaway may carry,
    else None (NC / ND / reserved / unknown are refused)."""
    t = f" {str(raw or '').strip().lower()} {str(url or '').lower()} "
    if not t.strip():
        return None
    if re.search(r"(\bnc\b|-nc|non-?commercial|\bnd\b|-nd|no-?deriv|"
                 r"all rights reserved|fair use)", t):
        return None
    ver = re.search(r"(\d\.\d)", t)
    v = f" {ver.group(1)}" if ver else ""
    if "publicdomain/zero" in t or re.search(r"\bcc0\b|cc-zero", t):
        return ("CC0 1.0", url or
                "https://creativecommons.org/publicdomain/zero/1.0/", False)
    if "publicdomain" in t or "public domain" in t or re.search(
            r"\bpdm\b|\bpd-|pd-usgov|pd-old", t):
        return ("Public domain", url, False)
    if re.search(r"licenses/by-sa|cc[ -]by-sa|\bby-sa\b", t):
        return (f"CC BY-SA{v}", url, True)
    if re.search(r"licenses/by/|cc[ -]by\b|\bby-\d|\bcc by\b|^ ?by ", t):
        return (f"CC BY{v}", url, True)
    if "pexels" in t:
        return ("Pexels License", "https://www.pexels.com/license/", False)
    if "pixabay" in t:
        return ("Pixabay Content License",
                "https://pixabay.com/service/license-summary/", False)
    if "nasa" in t:
        return (NASA_LICENCE[0], NASA_LICENCE[1], False)
    return None


def credit(cand):
    """The BrollCredit dict (the ledger line) for a candidate."""
    import broll_fx
    c = {"provider": str(cand.get("provider") or "unknown")[:40],
         "license": str(cand.get("license") or "")[:80],
         "title": _clip(cand.get("title") or cand.get("description"), 200),
         "author": _clip(cand.get("credit"), 200),
         "license_url": _clip(cand.get("license_url"), 400),
         "page_url": _clip(cand.get("page_url"), 600),
         "source_url": _clip(cand.get("source_url"), 800),
         "attribution_required": bool(cand.get("attribution_required"))}
    if c["attribution_required"] and not c["author"]:
        c["author"] = {"wikimedia": "Wikimedia Commons contributor",
                       "archive": "Internet Archive uploader"}.get(
            c["provider"], c["provider"])
    c["line"] = broll_fx.credit_line(c)[:400]
    return {k: v for k, v in c.items() if v not in (None, "")}


def _clip(v, n):
    if v is None:
        return None
    s = re.sub(r"<[^>]+>", "", str(v))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] or None


# ── providers ────────────────────────────────────────────────────────────

def status():
    """{provider: "on" | "off: why"} for this deployment."""
    import stock
    out = {}
    if not getattr(config, "STOCK_SEARCH_ENABLED", True):
        return {p: "off: stock search is disabled on this deployment"
                for p in PROVIDERS}
    allowed = _allowed()
    for p in PROVIDERS:
        if p not in allowed:
            out[p] = "off: not enabled (BROLL_PROVIDERS)"
        elif p == "pexels" and not stock.PEXELS_KEY:
            out[p] = "off: no PEXELS_API_KEY"
        elif p == "pixabay" and not stock.PIXABAY_KEY:
            out[p] = "off: no PIXABAY_API_KEY"
        else:
            out[p] = "on"
    return out


def _allowed():
    raw = str(getattr(config, "BROLL_PROVIDERS", "") or
              os.environ.get("BROLL_PROVIDERS", "") or "").strip()
    if not raw:
        return set(PROVIDERS)
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _wikimedia(query, kind, orientation, count):
    filetype = "video" if kind == KIND_VIDEO else "bitmap"
    prop = "videoinfo" if kind == KIND_VIDEO else "imageinfo"
    pre = "vi" if kind == KIND_VIDEO else "ii"
    params = {"action": "query", "format": "json", "generator": "search",
              "gsrnamespace": 6, "gsrsearch": f"{query} filetype:{filetype}",
              "gsrlimit": max(6, count * 3), "prop": prop,
              f"{pre}prop": "url|size|mime|extmetadata" + (
                  "|derivatives" if kind == KIND_VIDEO else ""),
              f"{pre}urlwidth": 640,
              f"{pre}extmetadatafilter": "LicenseShortName|LicenseUrl|"
              "Artist|ObjectName|ImageDescription|AttributionRequired"}
    data = net_fetch.get_json(COMMONS_API, params=params,
                              timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["commons.wikimedia.org"])
    pages = sorted(((data.get("query") or {}).get("pages") or {}).values(),
                   key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        info = (p.get(prop) or [{}])[0]
        meta = info.get("extmetadata") or {}

        def mv(k):
            return (meta.get(k) or {}).get("value")
        lic = canon_licence(mv("LicenseShortName"), mv("LicenseUrl"))
        if not lic:
            continue
        title = _clip(mv("ObjectName") or str(p.get("title") or "")
                      .replace("File:", ""), 200)
        base = {"provider": "wikimedia", "title": title,
                "description": _clip(mv("ImageDescription"), 300) or title,
                "credit": _clip(mv("Artist"), 200), "license": lic[0],
                "license_url": lic[1], "attribution_required": lic[2],
                "page_url": info.get("descriptionurl"),
                "_thumb": info.get("thumburl"),
                "width": info.get("width"), "height": info.get("height")}
        if kind == KIND_VIDEO:
            files = []
            for d in info.get("derivatives") or []:
                ftype = str(d.get("type") or "")
                if not (d.get("src") and d.get("width")):
                    continue
                if not ftype.startswith(("video/webm", "video/mp4",
                                         "video/ogg")):
                    continue
                files.append({"link": d["src"], "width": int(d["width"]),
                              "height": int(d.get("height") or 0),
                              "file_type": ftype.split(";")[0],
                              "bandwidth": d.get("bandwidth")})
            if not files:
                continue
            out.append(dict(base, kind=KIND_VIDEO,
                            id=f"wikimedia:video:{p.get('pageid')}",
                            duration_s=info.get("duration"), _files=files))
        else:
            mime = str(info.get("mime") or "")
            if not mime.startswith("image/") or mime == "image/svg+xml":
                continue
            out.append(dict(base, kind=KIND_PHOTO,
                            id=f"wikimedia:photo:{p.get('pageid')}",
                            duration_s=None, _url=info.get("url")))
        if len(out) >= count * 2:
            break
    return out


def _nasa(query, kind, orientation, count):
    media_type = "video" if kind == KIND_VIDEO else "image"
    data = net_fetch.get_json(NASA_API, params={
        "q": query, "media_type": media_type,
        "page_size": max(6, count * 2)}, timeout_s=API_TIMEOUT_S,
        allowed_hosts=["images-api.nasa.gov"])
    items = ((data.get("collection") or {}).get("items") or [])
    out = []
    for it in items[: max(6, count * 2)]:
        d = (it.get("data") or [{}])[0]
        nid = d.get("nasa_id")
        if not nid:
            continue
        thumb = next((ln.get("href") for ln in it.get("links") or []
                      if ln.get("render") == "image"), None)
        author = d.get("photographer") or d.get("secondary_creator") or \
            (f"NASA/{d.get('center')}" if d.get("center") else "NASA")
        out.append({
            "provider": "nasa",
            "kind": KIND_VIDEO if kind == KIND_VIDEO else KIND_PHOTO,
            "id": f"nasa:{media_type}:{nid}", "title": _clip(d.get("title"),
                                                               200),
            "description": _clip(d.get("description"), 300),
            "credit": _clip(author, 200), "license": NASA_LICENCE[0],
            "license_url": NASA_LICENCE[1], "attribution_required": False,
            "page_url": f"https://images.nasa.gov/details/{nid}",
            "keywords": d.get("keywords") or [],
            "_thumb": _https(thumb), "_collection": _https(it.get("href")),
            "width": None, "height": None, "duration_s": None})
    return out


def _https(u):
    if isinstance(u, str) and u.startswith("http://"):
        return "https://" + u[len("http://"):]
    return u


def nasa_renditions(cand):
    """Fill a NASA candidate's ``_files`` / ``_url`` from its asset
    manifest (one more request; done only for shortlisted hits)."""
    if cand.get("provider") != "nasa" or not cand.get("_collection"):
        return cand
    hrefs = net_fetch.get_json(cand["_collection"], timeout_s=API_TIMEOUT_S,
                               allowed_hosts=["images-assets.nasa.gov"])
    hrefs = [_https(h) for h in (hrefs or []) if isinstance(h, str)]
    if cand["kind"] == KIND_VIDEO:
        # ~medium/~mobile are 720p-ish; ~orig can be gigabytes
        order = {"~large.mp4": (1920, 1080), "~medium.mp4": (1280, 720),
                 "~mobile.mp4": (854, 480), "~small.mp4": (640, 360)}
        files = []
        for h in hrefs:
            for suf, (w, hh) in order.items():
                if h.endswith(suf):
                    files.append({"link": h, "width": w, "height": hh,
                                  "file_type": "video/mp4", "nominal": True})
        cand["_files"] = files
    else:
        for suf in ("~large.jpg", "~orig.jpg", "~medium.jpg"):
            hit = next((h for h in hrefs if h.endswith(suf)), None)
            if hit:
                cand["_url"] = hit
                break
    return cand


def _archive(query, kind, orientation, count):
    if kind != KIND_VIDEO:
        return []
    q = (f"({query}) AND mediatype:movies AND "
         "licenseurl:(*publicdomain* OR *creativecommons*)")
    data = net_fetch.get_json(IA_SEARCH, params={
        "q": q, "fl[]": ["identifier", "title", "licenseurl", "creator",
                         "year"], "rows": max(20, count * 6),
        "output": "json"}, timeout_s=API_TIMEOUT_S + 10,
        allowed_hosts=["archive.org"])
    docs = ((data.get("response") or {}).get("docs") or [])
    out = []
    for d in docs:
        lic = canon_licence(d.get("licenseurl"), d.get("licenseurl"))
        if not lic or not d.get("identifier"):
            continue
        ident = str(d["identifier"])
        out.append({
            "provider": "archive", "kind": KIND_VIDEO,
            "id": f"archive:video:{ident}", "title": _clip(d.get("title"),
                                                             200),
            "description": _clip(d.get("title"), 300),
            "credit": _clip(d.get("creator") if not isinstance(
                d.get("creator"), list) else ", ".join(d["creator"]), 200),
            "license": lic[0], "license_url": lic[1],
            "attribution_required": lic[2], "archival": True,
            "year": d.get("year"),
            "page_url": f"https://archive.org/details/{ident}",
            "_ident": ident, "width": None, "height": None,
            "duration_s": None,
            "_thumb": f"https://archive.org/services/img/{ident}"})
        if len(out) >= count * 2:
            break
    return out


def archive_renditions(cand):
    """Fill an Internet Archive hit's renditions from its metadata: the
    h.264 / MPEG4 derivatives under the byte cap, with their size, and
    the thumbnails' times (where in a long film the useful picture is)."""
    if cand.get("provider") != "archive" or not cand.get("_ident"):
        return cand
    ident = cand["_ident"]
    m = net_fetch.get_json(IA_META + ident, timeout_s=API_TIMEOUT_S + 10,
                           allowed_hosts=["archive.org"])
    files, thumbs = [], []
    for f in m.get("files") or []:
        name, fmt = str(f.get("name") or ""), str(f.get("format") or "")
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if fmt == "Thumbnail":
            mm = re.search(r"_(\d{6})\.jpg$", name)
            if mm:
                thumbs.append((int(mm.group(1)), _ia_url(ident, name)))
            continue
        if not (name.lower().endswith(".mp4") and (
                "h.264" in fmt.lower() or "mpeg4" in fmt.lower())):
            continue
        if not size or size > MAX_VIDEO_BYTES:
            continue
        try:
            w, h = int(f.get("width") or 0), int(f.get("height") or 0)
            length = float(f.get("length") or 0)
        except (TypeError, ValueError):
            w, h, length = 0, 0, 0.0
        files.append({"link": _ia_url(ident, name), "width": w or 640,
                      "height": h or 480, "file_type": "video/mp4",
                      "size": size, "length": length})
    cand["_files"] = files
    if files:
        best = max(files, key=lambda f: f["width"])
        cand["width"], cand["height"] = best["width"], best["height"]
        cand["duration_s"] = best.get("length") or None
    cand["_thumbs"] = sorted(thumbs)[:24]
    return cand


def _ia_url(ident, name):
    from urllib.parse import quote
    return f"https://archive.org/download/{ident}/{quote(name)}"


def _keyed(name):
    import stock

    def run(query, kind, orientation, count):
        fn = {"pexels": stock._pexels_search,
              "pixabay": stock._pixabay_search,
              "openverse": stock._openverse_search}[name]
        hits = fn(query, kind, orientation, count)
        out = []
        for h in hits:
            raw = h.get("license") or name
            lic = canon_licence(raw)
            if not lic:
                continue
            h = dict(h, license=lic[0], license_url=lic[1],
                     attribution_required=lic[2],
                     title=h.get("description"))
            out.append(h)
        return out
    return run


LANES = {"pexels": _keyed("pexels"), "pixabay": _keyed("pixabay"),
         "openverse": _keyed("openverse"), "wikimedia": _wikimedia,
         "nasa": _nasa, "archive": _archive}


def search(query, kind=KIND_VIDEO, orientation="portrait", count=4,
           providers=None, archival=False):
    """(candidates, errors, searched): every live provider in parallel for
    ``kind``; ``archival`` adds the Internet Archive lane (and is the only
    way it is searched). Candidates carry canonical licences only."""
    st = status()
    want = [p for p in (providers or PROVIDERS) if p in LANES]
    if not archival:
        want = [p for p in want if p != "archive"]
    lanes = [p for p in want if st.get(p) == "on"]
    if kind == KIND_PHOTO:
        lanes = [p for p in lanes if p != "archive"]
    else:
        lanes = [p for p in lanes if p != "openverse"]
    got, errors = {}, []
    if not lanes:
        return [], [], []
    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        futs = {pool.submit(LANES[p], query, kind, orientation, count): p
                for p in lanes}
        for f in as_completed(futs):
            p = futs[f]
            try:
                got[p] = f.result() or []
            except Exception as e:  # noqa: BLE001 — one library down
                errors.append(f"{p}: {str(e)[:120]}")
    out = []
    for p in lanes:
        out.extend(got.get(p) or [])
    return out, errors, lanes


# ── ranking before anything is downloaded ────────────────────────────────

def _tokens(text):
    toks = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return [t[:-1] if len(t) > 4 and t.endswith("s") else t
            for t in toks if t not in _STOP]


def relevance(cand, queries):
    """0..1: how much of the moment's query the hit's own words say."""
    hay = set(_tokens(" ".join(str(x) for x in (
        cand.get("title"), cand.get("description"),
        " ".join(cand.get("keywords") or [])) if x)))
    best = 0.0
    for q in queries:
        qt = [t for t in _tokens(q)]
        if not qt:
            continue
        best = max(best, sum(t in hay for t in qt) / float(len(qt)))
    return round(best, 3)


def prerank(cands, queries, W, H, need_s=2.0, archival=False):
    """Candidates sorted by metadata (relevance first), with the ones that
    cannot work dropped: too small (when the size is known), too short."""
    import broll_score
    keep = []
    for c in cands:
        rel = relevance(c, queries)
        w, h = c.get("width") or 0, c.get("height") or 0
        fit = "frame" if (archival or c.get("archival")) else "cover"
        re_ = broll_score.res_eff(w, h, W, H, fit) if w and h else None
        floor = broll_score.REJECT_RES_ARCHIVAL if fit == "frame" \
            else broll_score.REJECT_RES
        if re_ is not None and re_ < floor:
            continue
        dur = c.get("duration_s")
        if c.get("kind") == KIND_VIDEO and dur and float(dur) < need_s:
            continue
        prior = {"pexels": 0.9, "pixabay": 0.75, "wikimedia": 0.7,
                 "nasa": 0.8, "archive": 0.65, "openverse": 0.6}.get(
            c.get("provider"), 0.5)
        meta = 0.62 * rel + 0.2 * (re_ if re_ is not None else 0.5) + \
            0.18 * prior
        keep.append(dict(c, _rel=rel, _meta=round(meta, 4),
                         _fit=fit))
    keep.sort(key=lambda c: -c["_meta"])
    return keep


# ── fetching and checking ────────────────────────────────────────────────

_MAGIC = ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"),
          (b"GIF87a", "gif"), (b"GIF89a", "gif"), (b"RIFF", "webp"),
          (b"BM", "bmp"))


def image_kind(path):
    """'jpeg' / 'png' / 'gif' / 'webp' / 'bmp' by the file's own bytes,
    or None (an HTML page saved as .jpg is None)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for magic, name in _MAGIC:
        if head.startswith(magic):
            if name == "webp" and head[8:12] != b"WEBP":
                return None
            return name
    return None


def verify_media(path, kind):
    """Raise BrollError unless ``path`` really is a ``kind`` (photo: image
    bytes that decode; video: a decodable picture stream). Returns
    {width, height[, duration_s, fps]}."""
    if kind == KIND_PHOTO:
        if image_kind(path) is None:
            with open(path, "rb") as f:
                head = f.read(64).lower()
            what = "a web page" if b"<html" in head or b"<!doctype" in \
                head else "not an image"
            raise BrollError(f"the provider served {what}, not a photo")
        from PIL import Image
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                return {"width": im.width, "height": im.height}
        except Exception as e:  # noqa: BLE001
            raise BrollError(f"the photo does not decode ({str(e)[:80]})")
    import media
    try:
        info = media.probe(path)
    except Exception as e:  # noqa: BLE001
        raise BrollError(f"the clip does not decode ({str(e)[:80]})")
    if not info.get("width"):
        raise BrollError("the clip has no picture")
    return {"width": info["width"], "height": info["height"],
            "duration_s": info.get("duration"), "fps": info.get("fps")}


def frames_of(path, kind, n=4, width=None, at=None):
    """RGB frames across a clip (``at``: explicit seconds) or the still."""
    import numpy as np
    import depth_fx
    if kind == KIND_PHOTO:
        img = depth_fx._decode_frame(path, 0.0)
        return [img] if img is not None else []
    import media
    info = media.probe(path)
    dur = float(info.get("duration") or 0.0)
    times = at or [max(0.0, dur * f) for f in
                   ((0.1, 0.37, 0.63, 0.9) if n >= 4 else (0.5,))]
    out = []
    for t in times:
        img = depth_fx._decode_frame(path, min(t, max(0.0, dur - 0.1)))
        if img is None:
            continue
        if width and img.shape[1] > width:
            import cv2
            k = width / float(img.shape[1])
            img = cv2.resize(img, (width, int(img.shape[0] * k)),
                             interpolation=cv2.INTER_AREA)
        out.append(np.asarray(img))
    return out


def acquire_local(cand, workdir, want_w, want_h):
    """Download (dev / single-box lane) and verify one candidate; returns
    {path, kind, bytes, width, height, duration_s, fps, frames,
    motion_profile, native}. Raises BrollError."""
    import stock
    os.makedirs(workdir, exist_ok=True)
    kind = cand.get("kind")
    local = cand.get("_local_path") if os.environ.get(
        "VALMERA_BROLL_LOCAL") == "1" else None
    if local:
        path = local
    else:
        ext = ".jpg" if kind == KIND_PHOTO else _video_ext(cand, want_w,
                                                           want_h)
        path = os.path.join(workdir, re.sub(r"[^A-Za-z0-9_.-]", "_",
                                            cand["id"])[:80] + ext)
        try:
            stock.download(cand, path, want_w, want_h)
        except Exception as e:  # noqa: BLE001
            raise BrollError(f"download failed ({str(e)[:120]})")
    info = verify_media(path, kind)
    got = {"path": path, "kind": kind, "bytes": os.path.getsize(path),
           **info}
    got["frames"] = frames_of(path, kind, n=4)
    got["native"] = True
    got["motion_profile"] = None
    if kind == KIND_VIDEO:
        try:
            import motion_judge
            got["motion_profile"] = motion_judge.analyze_video(
                path, duration_s=info.get("duration_s"))
        except Exception:  # noqa: BLE001
            pass
    return got


def _video_ext(cand, want_w, want_h):
    import stock
    f = stock._pick_video_file(cand.get("_files") or [], want_w, want_h)
    t = str((f or {}).get("file_type") or "video/mp4")
    return {"video/webm": ".webm", "video/ogg": ".ogv"}.get(t, ".mp4")


def shoot_list(moments, orientation="portrait"):
    """What to film when no library has the shot: one line per moment."""
    frame = {"portrait": "vertical 9:16", "landscape": "horizontal 16:9",
             "square": "square 1:1"}.get(orientation, "vertical 9:16")
    lines = []
    for m in moments:
        q = str(m.get("query") or "").strip()
        why = str(m.get("purpose") or "").strip()
        dur = float(m.get("duration_s") or 2.4)
        lines.append(
            f"{m.get('id') or '?'}: film {q or 'the subject'} — {frame}, "
            f"{max(3.0, dur + 1.5):.0f} s of steady footage, slow push or "
            f"a locked-off frame with movement inside it, no logos or "
            f"faces into the lens" + (f" (it proves: {why})" if why else ""))
    return lines
