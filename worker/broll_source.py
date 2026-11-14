"""Track 4C: where cinematic B-roll comes from (ROADMAP 4C "Owns").

Six libraries, searched in parallel, every hit carrying its licence:

  pexels, pixabay   keyed stock (PEXELS_API_KEY / PIXABAY_API_KEY), video
                    and photo (stock.py's provider lanes)
  openverse         keyless CC photos (stock.py's lane)
  wikimedia         Wikimedia Commons: video (transcoded renditions) and
                    photos, licence from the file's own metadata; a query
                    that is the name of one thing is searched by its
                    Wikidata id first (``depicts``, P180), then as text
  nasa              NASA Image and Video Library: video and photos, public
                    domain under NASA's media guidelines
  archive           Internet Archive moving images under public-domain / CC0
                    / CC BY(-SA) only, from ARCHIVE_COLLECTIONS alone: an
                    uploader's licence field proves nothing anywhere else
                    (the archival lane)

Licence policy (``canon_licence``): public domain, CC0, CC BY, CC BY-SA and
the Pexels / Pixabay licences pass; NC, ND, "all rights reserved" and
unknown licences never reach the editor. Every candidate keeps its
provider, title, author, licence, licence URL and page — the ledger line the
``broll`` fx item carries. Commons also hosts pictures made by image
generators beside real photographs: ``drop_ai_made`` has a model read their
metadata before anything is fetched.

Acquisition reuses the stock lane: ``stock.download`` (and on the redesign
policy the executor's ``stock_acquire`` job) fetches a hit by its
``_url`` (photo) or ``_files`` renditions (video); ``verify_media`` refuses
a "photo" that is really a web page (JRE defect 29) and a video nothing can
decode. Nothing here publishes, uploads to a provider or keeps reference
media.
"""

import json
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
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_TIMEOUT_S = 3.0
NASA_API = "https://images-api.nasa.gov/search"
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/"
# The curated collections whose items are what their licence field says.
# Everywhere else on the Archive that field is the uploader's word: its search
# returns Digimon and Blue's Clues as CC0 (SOURCING section 8).
ARCHIVE_COLLECTIONS = ("prelinger", "FedFlix", "nasa", "universal_newsreels")
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


_WIKIDATA_IDS = {}          # query, folded -> (id, label) or None


def _wikidata_id(query):
    """(id, label) when the whole query is the name of one Wikidata item,
    else None. Wikidata's own label and alias match decides, so a sentence
    about a shot stays a text search. Best effort: None when it does not
    answer."""
    name = " ".join(str(query or "").split()).casefold()
    if not name:
        return None
    if name in _WIKIDATA_IDS:
        return _WIKIDATA_IDS[name]
    try:
        data = net_fetch.get_json(WIKIDATA_API, params={
            "action": "wbsearchentities", "format": "json", "type": "item",
            "search": query, "language": "en", "uselang": "en", "limit": 1},
            timeout_s=WIKIDATA_TIMEOUT_S,
            allowed_hosts=["www.wikidata.org"])
    except Exception as e:  # noqa: BLE001 — the text search still answers
        print(f"[broll] wikidata: {str(e)[:120]}", flush=True)
        return None
    hit = next(iter(data.get("search") or []), None) or {}
    match = hit.get("match") or {}
    found = None
    if hit.get("id") and match.get("type") in ("label", "alias") and \
            str(match.get("text") or "").casefold() == name:
        found = (hit["id"], hit.get("label") or str(query))
    if len(_WIKIDATA_IDS) >= 4096:
        _WIKIDATA_IDS.clear()
    _WIKIDATA_IDS[name] = found
    return found


def _commons_pages(search, kind, count):
    """One Commons search: the result pages, best first."""
    filetype = "video" if kind == KIND_VIDEO else "bitmap"
    prop = "videoinfo" if kind == KIND_VIDEO else "imageinfo"
    pre = "vi" if kind == KIND_VIDEO else "ii"
    params = {"action": "query", "format": "json", "generator": "search",
              "gsrnamespace": 6, "gsrsearch": f"{search} filetype:{filetype}",
              "gsrlimit": max(6, count * 3), "prop": prop,
              f"{pre}prop": "url|size|mime|extmetadata" + (
                  "|derivatives" if kind == KIND_VIDEO else ""),
              f"{pre}urlwidth": 640,
              f"{pre}extmetadatafilter": "LicenseShortName|LicenseUrl|"
              "Artist|ObjectName|ImageDescription|AttributionRequired|"
              "Categories|Credit"}
    data = net_fetch.get_json(COMMONS_API, params=params,
                              timeout_s=API_TIMEOUT_S,
                              allowed_hosts=["commons.wikimedia.org"])
    return sorted(((data.get("query") or {}).get("pages") or {}).values(),
                  key=lambda p: p.get("index", 0))


def _depicted_pages(query, kind, count):
    """(pages stating they depict what the query names, its label)."""
    named = _wikidata_id(query)
    if not named:
        return [], None
    try:
        return _commons_pages(f"haswbstatement:P180={named[0]}", kind,
                              count), named[1]
    except Exception as e:  # noqa: BLE001 — the text search still answers
        print(f"[broll] commons depicts: {str(e)[:120]}", flush=True)
        return [], None


def _wikimedia(query, kind, orientation, count):
    prop = "videoinfo" if kind == KIND_VIDEO else "imageinfo"
    with ThreadPoolExecutor(max_workers=1) as pool:
        depicted_f = pool.submit(_depicted_pages, query, kind, count)
        pages = _commons_pages(query, kind, count)
        depicted, label = depicted_f.result()
    named = {p.get("pageid") for p in depicted}
    pages = depicted + [p for p in pages if p.get("pageid") not in named]
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
                "_file": p.get("title"),
                "_categories": _clip(mv("Categories"), 300),
                "_source_note": _clip(mv("Credit"), 200),
                "width": info.get("width"), "height": info.get("height")}
        if p.get("pageid") in named:
            # its own words may not name it: the statement does
            base["keywords"] = [label]
        if kind == KIND_VIDEO:
            files = []
            dur = float(info.get("duration") or 0.0)
            for d in info.get("derivatives") or []:
                ftype = str(d.get("type") or "")
                if not (d.get("src") and d.get("width")):
                    continue
                if not ftype.startswith(("video/webm", "video/mp4",
                                         "video/ogg")):
                    continue
                # the original is a derivative without a transcode key and
                # can be gigabytes: only when its own size fits the cap
                if not d.get("transcodekey") and int(
                        info.get("size") or 0) > MAX_VIDEO_BYTES:
                    continue
                bw = d.get("bandwidth")
                if bw and dur and float(bw) / 8.0 * dur > MAX_VIDEO_BYTES:
                    continue
                if int(d["width"]) > 1920:
                    continue
                files.append({"link": d["src"], "width": int(d["width"]),
                              "height": int(d.get("height") or 0),
                              "file_type": ftype.split(";")[0],
                              "bandwidth": bw})
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
                    # NASA's renditions run to gigabytes: ask each one's
                    # size first and keep what the byte cap can take
                    try:
                        _ct, size = net_fetch.head_kind(
                            h, timeout_s=API_TIMEOUT_S)
                    except Exception:  # noqa: BLE001
                        size = None
                    if size and size > MAX_VIDEO_BYTES:
                        continue
                    files.append({"link": h, "width": w, "height": hh,
                                  "file_type": "video/mp4", "nominal": True,
                                  "size": size})
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
         f"collection:({' OR '.join(ARCHIVE_COLLECTIONS)}) AND "
         "licenseurl:(*publicdomain* OR *creativecommons*)")
    data = net_fetch.get_json(IA_SEARCH, params={
        "q": q, "fl[]": ["identifier", "title", "licenseurl", "creator",
                         "year", "subject", "description", "collection"],
        "rows": max(20, count * 6),
        "output": "json"}, timeout_s=API_TIMEOUT_S + 10,
        allowed_hosts=["archive.org"])
    docs = ((data.get("response") or {}).get("docs") or [])
    out = []
    for d in docs:
        lic = canon_licence(d.get("licenseurl"), d.get("licenseurl"))
        if not lic or not d.get("identifier"):
            continue
        held_in = d.get("collection")
        held_in = held_in if isinstance(held_in, list) else [held_in]
        if not set(held_in) & set(ARCHIVE_COLLECTIONS):
            continue
        ident = str(d["identifier"])
        out.append({
            "provider": "archive", "kind": KIND_VIDEO,
            "id": f"archive:video:{ident}", "title": _clip(d.get("title"),
                                                             200),
            # a film's title rarely names its pictures: its subjects and
            # description do (relevance reads them)
            "description": _clip(" ".join(x for x in (
                d.get("description") if isinstance(d.get("description"),
                                                   str) else "",
            ) if x), 300) or _clip(d.get("title"), 300),
            "keywords": (d.get("subject") if isinstance(d.get("subject"),
                                                        list)
                         else [d.get("subject")] if d.get("subject")
                         else []),
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


# ── pictures an image generator made ─────────────────────────────────────

AI_CHECK_MAX = 40          # one call reads this many items
AI_CHECK_TIMEOUT_S = 30.0
_AI_MADE = {}              # candidate id -> True: an image generator's work
_AI_SYSTEM = """You check picture metadata for a video editor. Wikimedia \
Commons hosts images made by AI image generators beside real photographs, and \
the editor must never use one as a real picture.

For each numbered item, read its file name, title, description, categories \
and source line, in any language. An item is AI-made when they say or plainly \
imply that a generative model made the picture or invented what it shows: \
text-to-image output, an AI portrait of a real person, an AI "reconstruction" \
of a scene. A real photograph, scan or drawing about the subject of AI is not \
AI-made, and neither is one retouched by ordinary software.

Answer with only a JSON array of the numbers of the AI-made items, [] if none."""


def from_commons(cand):
    """Commons' own pictures, and the same files reached through Openverse."""
    return cand.get("provider") == "wikimedia" or \
        cand.get("_origin") == "wikimedia"


def _ai_row(n, cand):
    return {"n": n, "file": cand.get("_file"), "title": cand.get("title"),
            "description": cand.get("description"),
            "categories": cand.get("_categories"),
            "source": cand.get("_source_note") or cand.get("credit")}


def _read_ai_made(cands):
    """{candidate id: True when a model reads its metadata as AI-made}, or
    None when no model answered."""
    import llm
    lane = None
    if llm.claude_trial_lane() is None and \
            not llm.paid_editor_plan(llm.turn_plan()):
        # Library searches run on pool threads, which hold no turn.
        import claude_lane
        if claude_lane.available():
            lane = (claude_lane.ClaudeLaneClient(timeout=AI_CHECK_TIMEOUT_S),
                    config.CLAUDE_TRIAL_MODEL)
    rows = [_ai_row(n, c) for n, c in enumerate(cands, 1)]
    got = llm.ask_text(_AI_SYSTEM, json.dumps(rows, ensure_ascii=False),
                       max_tokens=1000, temperature=0.0,
                       purpose="commons_ai_items", reasoning_effort="low",
                       retry_empty=True, lane=lane)
    picked = llm.extract_json_array((got or {}).get("text"))
    if picked is None or not all(
            isinstance(n, int) and 1 <= n <= len(cands) for n in picked):
        return None
    return {c["id"]: n in picked for n, c in enumerate(cands, 1)}


def drop_ai_made(cands):
    """(candidates without Commons' AI items, a note or None). Nothing from
    Commons passes unread: with no model to read it those items are left out
    and the note says so, and the other libraries carry the search."""
    todo = {c["id"]: c for c in cands
            if from_commons(c) and c["id"] not in _AI_MADE}
    note = None
    if todo:
        batch = list(todo.values())[:AI_CHECK_MAX]
        read = _read_ai_made(batch)
        if read is None:
            note = ("Wikimedia Commons pictures were left out: their AI check "
                    "did not answer")
        else:
            if len(_AI_MADE) >= 8192:
                _AI_MADE.clear()
            _AI_MADE.update(read)
    return [c for c in cands if not from_commons(c)
            or _AI_MADE.get(c["id"]) is False], note


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


# ── named subjects: real people, places, brands and events ───────────────
#
# A stock library has no Oscar Piastri: p5591 asked for him at McLaren and got
# six Pexels lookalikes, rightly refused. Commons and Openverse do hold him,
# with a licence and a credit on every file. A model reads the words, in any
# language and spelling, and names what a picture must show (never a word
# list); the libraries are searched by those names; a vision check looks at
# every candidate beside its own caption and keeps only the ones that plainly
# show it. A picture the check did not pass is never offered, and when none
# passes the caller gets the honest sentence to say.

NAMED_MODEL = os.environ.get("NAMED_PICTURE_MODEL", "claude-haiku-5-5")
# The picture check (check_named) is a vision call of its own: gpt-6-sol at low effort through
# score.call's OpenAI rung (lane footage, 13 Nov 2026). Lane Q's skeptic judged every disputed
# picture against the frames and found gpt-6-sol right where Opus let library lookalikes in
# (week-1112/lanes/Q/skeptic.json, picture_check: SAFE). It is paid from the OpenAI credit until
# 30 Nov 2026 and in OpenAI money after that. When the OpenAI rung cannot answer (no key, an
# error) the check falls back to NAMED_MODEL inside the same deadline.
NAMED_CHECK_MODEL = os.environ.get("NAMED_CHECK_MODEL", "gpt-6-sol")
NAMED_CHECK_MAX = 14       # pictures one check call looks at
NAMED_NAME_S = 12.0        # the naming call's own deadline
NAMED_CHECK_S = 25.0
NAMED_SEARCH_S = 6.0       # every library for one subject, together
COMMONS_DOWNLOAD_W = 1920  # a Commons rendition step, never an upscale
_NAMED = {}                # text, folded -> subjects (list), bounded

NAME_SYSTEM = """You help a film editor find real pictures. You get words from a customer's request or from a film's narration, in any language, perhaps misspelled or run together.

List the specific real-world subjects they name that a public photo archive (Wikimedia Commons) could hold a genuine photo of: a named person, a named place or building, a brand, company or product by name, a named event, team, vehicle, artwork or organisation. Fix the spelling to the subject's usual English name ("oscar piastrin" is Oscar Piastri). Only what the words name or plainly point to; a generic thing, an idea, a feeling, a kind of place or an unnamed person is not a subject. When the words relate two named things ("X in his McLaren"), give one subject for the person with the context in its searches and in must_show.

For each subject: "name" (the usual English name), "is" (person, place, brand, event or thing), "searches" (one to three Commons searches, most specific first, the bare name last), "must_show" (one plain sentence: what a picture must visibly show to count, the way a picture editor would brief it, naming every form the subject can take on camera: a racing driver "himself, or his own car on track"; a company "its logo on its building or product"; a city "a recognisable view of it").

Answer with one JSON object and nothing else: {"subjects":[...]}; {"subjects":[]} when nothing is named."""

CHECK_NAMED_SYSTEM = """You check pictures for a film editor before he may use them. Each numbered picture was found for a named real subject; you get what the picture must show, and the picture's own caption from its archive.

Keep a picture when a viewer would accept at once that it shows that subject: the caption names the subject, nothing in the frame contradicts it, and the frame holds one of the forms the brief allows (the person, his own car or stage in the act the caption names, the building, the event) large enough to read on a phone. A driver is shown by his car on track: the face inside a helmet need not be readable. You do not recognise faces: you judge the caption against what the frame plainly shows (a livery, a number, a sign, the setting, a person where a person should be).

Drop a picture when the frame shows something else than the brief (a crowd, a logo alone, a document, a map, a different car), when the subject is a speck, when it is a drawing, caricature, collage or screenshot, when its caption or look says an image generator made it, when it is a lookalike with no caption naming the subject, or when it is too dark, blurred or watermarked to use.

Answer with one JSON object and nothing else: {"fit":[{"n":number,"ok":true or false,"shows":"at most eight plain words","grade":1 to 5}]}, one entry per picture, grade 5 for a picture you would lead a film with."""


def _json_obj(text):
    """The first JSON object in a model's answer, or None."""
    s = str(text or "")
    i = s.find("{")
    while i >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return None


def carried(fn):
    """fn for a pool thread, carrying the caller's llm recorder: the turn's
    ledger, its trial scope and llm_calls travel with it, so a call made on
    a pool thread is metered like one made on the turn's own thread."""
    import llm
    rec = llm.get_recorder()

    def run(*a, **k):
        prev = llm.get_recorder()
        llm.set_recorder(rec)
        try:
            return fn(*a, **k)
        finally:
            llm.set_recorder(prev)
    return run


def _objects(text):
    """Every flat JSON object in an answer, in order, so a line cut off by
    the token cap costs that line only."""
    dec, s, out, i = json.JSONDecoder(), str(text or ""), [], 0
    s = s[s.find("[") + 1:] if "[" in s else s
    while True:
        j = s.find("{", i)
        if j < 0:
            return out
        try:
            obj, i = dec.raw_decode(s, j)
            if isinstance(obj, dict):
                out.append(obj)
        except ValueError:
            i = j + 1


def _ask(system, content, purpose, budget_s, max_tokens, model=None):
    """One model call on the product's lane. (text, usd). Raises when no
    lane or no answer: every caller keeps its honest fallback. A model
    other than NAMED_MODEL that fails before the deadline (no OpenAI key,
    a provider error) hands the call to NAMED_MODEL with the time left."""
    import time
    from score import call
    model = model or NAMED_MODEL
    deadline = time.monotonic() + budget_s
    try:
        r = call.stream(model, system, content, max_tokens=max_tokens,
                        effort="low", purpose=purpose, thinking=False,
                        deadline=deadline)
    except call.Late:
        raise
    except Exception:  # noqa: BLE001 the Claude lane answers instead
        if model == NAMED_MODEL or deadline - time.monotonic() < 3.0:
            raise
        r = call.stream(NAMED_MODEL, system, content, max_tokens=max_tokens,
                        effort="low", purpose=purpose, thinking=False,
                        deadline=deadline)
    return r.text, float(r.usd or 0.0)


def _subjects_of(obj):
    out = []
    for s in (obj or {}).get("subjects") or []:
        if not isinstance(s, dict):
            continue
        name = " ".join(str(s.get("name") or "").split())[:80]
        if not name:
            continue
        qs = [" ".join(str(q).split())[:100] for q in s.get("searches") or []
              if str(q).strip()][:3] or [name]
        out.append({"name": name, "is": str(s.get("is") or "thing")[:20],
                    "searches": qs,
                    "must_show": " ".join(str(s.get("must_show") or
                                              name).split())[:240]})
    return out[:3]


def name_subjects(text, context=""):
    """(subjects, usd) for one text: what it names that a real picture could
    show. [] when it names nothing. Raises when the model did not answer."""
    key = " ".join(f"{text} | {context}".split()).casefold()[:600]
    if key in _NAMED:
        return _NAMED[key], 0.0
    user = f"Words: {str(text)[:800]}"
    if context:
        user += f"\nWhat the film is about: {str(context)[:600]}"
    txt, usd = _ask(NAME_SYSTEM, [{"type": "text", "text": user}],
                    "named_subjects", NAMED_NAME_S, 600)
    obj = _json_obj(txt)
    if obj is None:
        raise BrollError("the naming call answered without JSON")
    subs = _subjects_of(obj)
    if len(_NAMED) >= 2048:
        _NAMED.clear()
    _NAMED[key] = subs
    return subs, usd


def name_many(texts, context="", system=None):
    """(list of subject lists, one per text, usd): one call for a film's
    moments. Raises when the model did not answer. `system`: another naming
    rule with the same answer (worker/footage names what video exists of)."""
    if not texts:
        return [], 0.0
    lines = [f"moment {i}: {str(t)[:300]}" for i, t in enumerate(texts)]
    user = ("Each moment separately. Answer {\"moments\":[{\"m\":number,"
            "\"subjects\":[...]}]}, one entry per moment, subjects [] where "
            "nothing is named.\n" + "\n".join(lines))
    if context:
        user += f"\nWhat the film is about: {str(context)[:600]}"
    txt, usd = _ask(system or NAME_SYSTEM, [{"type": "text", "text": user}],
                    "named_subjects", NAMED_NAME_S, 1800)
    obj = _json_obj(txt)
    if obj is None:
        raise BrollError("the naming call answered without JSON")
    out = [[] for _ in texts]
    for m in obj.get("moments") or []:
        if isinstance(m, dict) and isinstance(m.get("m"), int) and \
                0 <= m["m"] < len(texts):
            out[m["m"]] = _subjects_of(m)
    return out, usd


def _sized_url(c):
    """A Commons original can be 8000 px and 40 MB: take the 1920 px step of
    the same file when the original is wider, never an upscale."""
    url = c.get("_url")
    thumb = str(c.get("_thumb") or "")
    w = int(c.get("width") or 0)
    if c.get("provider") == "wikimedia" and w > COMMONS_DOWNLOAD_W and \
            "/thumb/" in thumb and "px-" in thumb:
        head, _, tail = thumb.rpartition("/")
        step = tail.split("px-", 1)
        if len(step) == 2 and step[0].isdigit():
            return f"{head}/{COMMONS_DOWNLOAD_W}px-{step[1]}"
    return url


def search_named(subject, kind=KIND_PHOTO, count=6, budget_s=None):
    """(candidates, errors): Commons (by Wikidata 'depicts' first, then text)
    and Openverse for one subject, every search at once. Photos always, since
    a named person is rarely on free video; Commons video too when asked."""
    import stock
    import time
    jobs = []
    pool = ThreadPoolExecutor(max_workers=6)
    for q in subject["searches"][:3]:
        jobs.append((f"commons {q}", pool.submit(_wikimedia, q, KIND_PHOTO,
                                                 None, count)))
    if kind == KIND_VIDEO:
        jobs.append(("commons video", pool.submit(
            _wikimedia, subject["searches"][0], KIND_VIDEO, None, 3)))
    jobs.append(("openverse", pool.submit(
        stock._openverse_search, subject["searches"][0], KIND_PHOTO, None,
        count)))
    t0 = time.monotonic()
    out, seen, errors = [], set(), []
    for name, fut in jobs:
        try:
            got = fut.result(timeout=max(0.05, (budget_s or NAMED_SEARCH_S) -
                                         (time.monotonic() - t0)))
        except Exception as e:  # noqa: BLE001 — one library late or down
            errors.append(f"{name}: {str(e)[:100] or 'over its time'}")
            continue
        for c in got or []:
            if c.get("id") in seen or not c.get("_thumb"):
                continue
            seen.add(c["id"])
            c = dict(c)
            if c.get("kind") == KIND_PHOTO:
                c["_url"] = _sized_url(c)
            out.append(c)
    pool.shutdown(wait=False, cancel_futures=True)
    return out, errors


def _caption(c):
    bits = [c.get("title"), c.get("description")]
    if c.get("keywords"):
        bits.append("depicts: " + ", ".join(map(str, c["keywords"])))
    if c.get("_categories"):
        bits.append("categories: " + str(c["_categories"]))
    text = " | ".join(str(b) for b in bits if b)
    return _clip(text, 360) or "(no caption)"


def check_named(subject, cands, workdir=None, budget_s=None):
    """(kept, report, usd): the candidates a vision model passes as showing
    the subject, best first, each with what it shows. No answer passes
    nothing: an unchecked picture of a real person is never offered."""
    import base64
    import shutil
    import tempfile
    from PIL import Image
    cands = cands[:NAMED_CHECK_MAX]
    if not cands:
        return [], [], 0.0
    tmp = tempfile.mkdtemp(prefix="named-", dir=workdir)
    try:
        def thumb(i):
            p = os.path.join(tmp, f"{i}.jpg")
            try:
                net_fetch.download(cands[i]["_thumb"], p,
                                   max_bytes=4 * 1024 * 1024, timeout_s=10)
                with Image.open(p) as im:
                    im = im.convert("RGB")
                    im.thumbnail((448, 448))
                    im.save(p, quality=85)
                return i, p
            except Exception:  # noqa: BLE001 — that candidate is not seen
                return i, None
        with ThreadPoolExecutor(max_workers=6) as pool:
            seen = dict(pool.map(thumb, range(len(cands))))
        content, rows = [], []
        for i, c in enumerate(cands):
            p = seen.get(i)
            if not p:
                continue
            with open(p, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode()
            content += [{"type": "text", "text": f"picture {i}: caption: "
                         f"{_caption(c)}"},
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg",
                            "data": data}}]
            rows.append(i)
        if not rows:
            return [], [dict(error="no thumbnail could be read")], 0.0
        content.append({"type": "text", "text":
                        f"Subject: {subject['name']}. Each picture must "
                        f"show: {subject['must_show']}"})
        txt, usd = _ask(CHECK_NAMED_SYSTEM, content, "named_check",
                        budget_s or NAMED_CHECK_S, 2400,
                        model=NAMED_CHECK_MODEL)
        verdict = {}
        for o in _objects(txt):          # a cut-off answer keeps its lines
            if isinstance(o.get("n"), int):
                verdict[o["n"]] = o
        kept, report = [], []
        for i in rows:
            v = verdict.get(i) or {}
            ok = v.get("ok") is True
            grade = v.get("grade") if isinstance(v.get("grade"), int) else 0
            report.append(dict(id=cands[i]["id"], ok=ok, grade=grade,
                               shows=_clip(v.get("shows"), 120)))
            if ok:
                kept.append(dict(cands[i], named=subject["name"],
                                 shows=_clip(v.get("shows"), 120) or
                                 subject["name"], _grade=grade))
        kept.sort(key=lambda c: -c["_grade"])
        return kept, report, usd
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def license_note(c):
    """The credit line an agent reads and a film's credits carry."""
    who = c.get("credit") or ("Wikimedia Commons contributor"
                              if c.get("provider") == "wikimedia" else None)
    src = {"wikimedia": "Wikimedia Commons", "openverse": "Openverse"}.get(
        c.get("provider"), c.get("provider"))
    lic = c.get("license") or ""
    if who:
        return f"{lic}, credit: {who} via {src}"[:240]
    return f"{lic} via {src}"[:240]


def find_named(subject, kind=KIND_PHOTO, count=6, also=(), workdir=None,
               deadline=None):
    """Checked pictures of one named subject. ``also`` are other libraries'
    hits for the same words (stock lookalikes): they face the same check.
    Returns (kept, report) with report["say"] the honest sentence when
    nothing passed and report["usd"] the model spend."""
    import time
    left = (lambda cap: cap if deadline is None else
            max(1.0, min(cap, deadline - time.monotonic())))
    cands, errors = search_named(subject, kind, count,
                                 left(NAMED_SEARCH_S) * 0.6
                                 if deadline is not None else None)
    lic_ok = []
    # the archives' own files first; a library's lookalikes fill what's left
    others = [dict(x) for x in (also or [])]
    n_other = min(len(others), 4)
    for c in cands[:NAMED_CHECK_MAX - n_other] + others[:n_other]:
        if c.get("provider") in ("wikimedia", "openverse") and \
                c.get("license") and not c.get("license_note"):
            c["license_note"] = license_note(c)
        lic_ok.append(c)
    report = dict(subject=subject["name"], must_show=subject["must_show"],
                  searched=subject["searches"], candidates=len(lic_ok),
                  errors=errors, usd=0.0)
    if not lic_ok:
        report["say"] = (f"No free-licence picture of {subject['name']} was "
                         "found in Wikimedia Commons, Openverse or the stock "
                         "libraries" + (f" ({'; '.join(errors)[:160]})"
                                        if errors else "") + ".")
        return [], report
    try:
        kept, checked, usd = check_named(subject, lic_ok, workdir,
                                         left(NAMED_CHECK_S))
    except Exception as e:  # noqa: BLE001 — unchecked is never shown
        report["say"] = (f"{len(lic_ok)} pictures were found for "
                         f"{subject['name']}, but the check that each one "
                         "really shows him, her or it did not answer, so "
                         "none is offered.")
        report["check_error"] = str(e)[:160]
        return [], report
    report.update(checked=checked, usd=round(usd, 6), kept=len(kept))
    if not kept:
        report["say"] = (f"None of the {len(checked)} free-licence pictures "
                         f"found for {subject['name']} passed the check that "
                         "it really shows that, so none is offered.")
    for c in kept:
        c["description"] = f"SHOWS {c['named']}: {c['shows']}"
        c.pop("_grade", None)
    return kept[:max(1, count)], report


# ── designed pictures ────────────────────────────────────────────────────
# Where no real picture shows what is said, a picture made of what is said:
# the number, the two sides, the steps, the dates (score/boards.designed).
# A model reads the words and decides; nothing here reads them by rule.

DESIGN_MODEL = os.environ.get("DESIGN_PICTURE_MODEL", NAMED_MODEL)
DESIGN_S = 12.0
DESIGNED = "designed"
DESIGN_LICENCE = "Designed by Valmera from the film's own words"

DESIGN_SYSTEM = """You read moments of a short film for its picture editor. You get the words said at each moment, in any language. For each moment answer two things.

"thing": the one concrete, photographable thing the words name or plainly point to, in two to five plain English words a stock library would know ("airline cockpit", "fishing nets on a boat deck", "turmeric root"), or "" when the words name nothing a camera could film: an idea, a feeling, an opinion, a greeting. Never a mood or a metaphor for the words.

"design": a picture made of the words themselves, only when they carry something a designed picture shows better than the person saying it: a figure ("number"), two named sides with their values ("versus"), steps or items in a row ("list"), amounts to compare ("bars", each part with its number in "n"), dates in order ("timeline"), or a technical term with its plain meaning ("term"). Otherwise null: a sentence, a claim, a feeling or anything a caption already says is never a design. Every word and number on it comes from the words given, never a fact, figure, date or name they do not say. Write it in the language of the words. Short: a value at most twelve characters, a label at most five words, a title at most four words or empty.

Answer with one JSON object and nothing else: {"moments":[{"m":0,"thing":"fishing nets on a boat deck","design":null},{"m":1,"thing":"","design":{"kind":"number","title":"","parts":[{"value":"150 years","label":"before she can breed"}],"what":"at most eight plain words on what it shows"}}]}, one entry per moment."""

_GLYPHS = {}


def _covers(text):
    """True when the designed picture's faces hold a glyph for every
    character: a word the font cannot draw would come out as boxes."""
    if "cmap" not in _GLYPHS:
        try:
            from fontTools.ttLib import TTFont
            from score import boards
            _GLYPHS["cmap"] = set(TTFont(os.path.join(
                os.path.dirname(boards.FONT), "InterDisplay-Black.ttf"))
                .getBestCmap())
        except Exception:  # noqa: BLE001 — no table read: Latin only
            _GLYPHS["cmap"] = set(range(0x20, 0x250))
    cmap = _GLYPHS["cmap"]
    return all(ord(ch) in cmap or ch.isspace() for ch in str(text))


def read_moments(texts, accent=None, budget_s=None):
    """(list of {"thing", "design"} per text, usd) by one model call: the
    photographable thing each moment's words name, and the designed picture
    its words carry (a spec for score/boards.designed, or None). Raises when
    the model did not answer; every caller keeps the moment without them."""
    import time
    from score import boards, call
    if not texts:
        return [], 0.0
    lines = [f"moment {i}: {str(t)[:360]}" for i, t in enumerate(texts)]
    r = call.stream(DESIGN_MODEL, DESIGN_SYSTEM,
                    [{"type": "text", "text": "\n".join(lines)}],
                    max_tokens=2400, effort="low", purpose="designed_picture",
                    thinking=False,
                    deadline=time.monotonic() + (budget_s or DESIGN_S))
    out = [{"thing": "", "design": None} for _ in texts]
    for o in _objects(r.text):
        m = o.get("m")
        if not isinstance(m, int) or not 0 <= m < len(texts):
            continue
        if "thing" in o:
            out[m]["thing"] = _clip(o.get("thing") or "", 60)
        d = o.get("design") if isinstance(o.get("design"), dict) else \
            (o if o.get("kind") else None)
        spec = boards.design_spec(dict(d, accent=accent or "#E8B23A")) \
            if d else None
        if spec is None:
            continue
        words = [spec["title"]] + [p["label"] + " " + p["value"]
                                   for p in spec["parts"]]
        if not _covers(" ".join(words)):
            continue
        spec["what"] = _clip(d.get("what") or spec["kind"], 80)
        out[m]["design"] = spec
    return out, float(r.usd or 0.0)


def design_specs(texts, accent=None, budget_s=None):
    """(spec or None per text, usd): the designed pictures alone."""
    got, usd = read_moments(texts, accent, budget_s)
    return [g["design"] for g in got], usd


def designed_hit(spec, orientation="portrait"):
    """A designed picture as a library hit: stock.download draws it from
    the spec on whichever worker acquires it, so it travels like any hit."""
    import hashlib
    W, H = (1920, 1080) if orientation == "landscape" else \
        (1080, 1080) if orientation == "square" else (1080, 1920)
    key = json.dumps({k: spec.get(k) for k in ("kind", "title", "parts",
                                                "accent", "page")},
                     sort_keys=True, ensure_ascii=False)
    hid = "designed:" + hashlib.sha1(
        f"{key}|{W}x{H}".encode()).hexdigest()[:14]
    what = spec.get("what") or spec["kind"]
    return {"id": hid, "provider": DESIGNED, "kind": KIND_PHOTO,
            "width": W, "height": H, "license": DESIGN_LICENCE,
            "license_url": None, "credit": "Valmera", "title": what,
            "description": f"DESIGNED {spec['kind']}: {what}",
            "design": {k: spec.get(k) for k in ("kind", "title", "parts",
                                                "accent", "page")}}


def draw_designed(item, out_path, want_w=None, want_h=None):
    """The designed hit's picture, drawn at its own size, as a JPEG."""
    from score import boards
    W = int(item.get("width") or want_w or 1080)
    H = int(item.get("height") or want_h or 1920)
    im = boards.designed(boards.design_spec(item.get("design")), W, H)
    im.save(out_path, "JPEG", quality=92)
    return out_path


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
        if img is not None and width and img.shape[1] > width:
            import cv2
            k = width / float(img.shape[1])
            img = cv2.resize(img, (width, max(2, int(img.shape[0] * k))),
                             interpolation=cv2.INTER_AREA)
        return [np.asarray(img)] if img is not None else []
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
    return analyse_local(fetch_local(cand, workdir, want_w, want_h))


def fetch_local(cand, workdir, want_w, want_h):
    """The network half (safe in parallel threads): download and verify;
    {path, kind, bytes, width, height[, duration_s, fps]}."""
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
    return {"path": path, "kind": kind, "bytes": os.path.getsize(path),
            **info}


def analyse_local(got):
    """The decoding half (run in ONE thread: numpy's BLAS is not safe to
    drive from several Python threads at once — measured as minutes of
    spinning): review frames and the motion profile."""
    path, kind = got["path"], got["kind"]
    info = got
    # capped at 1280 px: a 6000-px archive scan is judged as well at a
    # fifth of its size; blockiness is read only at the file's own size
    got["frames"] = frames_of(path, kind, n=4, width=1280)
    got["native"] = int(info.get("width") or 0) <= 1280
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
