"""Supply: pictures a Director can choose from instead of wishing for.

Ladder: the upload first (own wishes stay with the upload, as crops and plates), then the stock
libraries. Each wish gets a budget. One Look on one contact sheet keeps at most 8 candidates: it
sees what a library's metadata doesn't say. Everything ends on one board with ids.

Out: <work>/bin/{N}.jpg, bin_board.jpg, bin.json (items with licence and class, plates, credits,
per-wish results). Every wish ends with at least a plate. Production orchestration holds no media
bytes: a clip is acquired by the egress lane and published there, and this side keeps its
thumbnail for the compiler's reading of light and for the board. Stills are small and come back.
"""
import concurrent.futures as cf
import json
import os
import shutil
import tempfile
import time

from PIL import Image, ImageDraw

from . import boards, brief, call

BOARD_W, BOARD_H = 1568, 880
MAX_ITEMS = 8
WISH_BUDGET_S = 6.0
LOOK_MODEL = "claude-haiku-5-5"


def licence_class(item):
    """SOURCING 6: A free to use, B with credit. Anything else never reaches the Bin."""
    prov = item.get("provider")
    if prov == "pexels":
        return "A", "Pexels License", "https://www.pexels.com/license/"
    lic = str(item.get("license") or "")
    url = item.get("license_url")
    if lic.startswith(("CC0", "Public domain")):
        return "A", lic, url
    if lic.startswith("CC BY"):
        return "B", lic, url
    return None, lic, url


def search_wish(stock, broll, wish):
    """Library hits for one wish, inside its budget. A slow library loses its turn, not the wish:
    Pexels and Commons run as separate lanes, so neither waits on the other."""
    t0 = time.time()
    kind = "photo" if wish.get("kind") == "photo" else "video"
    qs = [q for q in (wish.get("q") or []) if q][:2]
    pool = cf.ThreadPoolExecutor(max_workers=3)
    jobs = [(q, pool.submit(stock._pexels_search, q, kind, "portrait", 4)) for q in qs]
    if qs:
        jobs.append((qs[0] + " (commons)", pool.submit(broll.search, qs[0], "photo", "portrait", 3, ["wikimedia"])))
    hits, errs = [], []
    for q, fut in jobs:
        left = max(0.05, WISH_BUDGET_S - (time.time() - t0))
        try:
            r = fut.result(timeout=left)
        except cf.TimeoutError:
            errs.append(f"{q}: over budget")
            continue
        except Exception as e:  # noqa: BLE001 one library down must not stop a film
            errs.append(f"{q}: {str(e)[:100]}")
            continue
        if isinstance(r, tuple):
            r = r[0]
        for it in r or []:
            it["_q"] = q.replace(" (commons)", "")
            hits.append(it)
    pool.shutdown(wait=False, cancel_futures=True)
    keep = []
    seen = set()
    for h in hits:
        c, _, _ = licence_class(h)
        if c is None or h.get("id") in seen or not h.get("_thumb"):
            continue
        if h.get("kind") == "video" and (h.get("duration_s") or 0) < 2:
            continue
        seen.add(h["id"])
        keep.append(h)
    return keep[:8], errs, round(time.time() - t0, 2)


def fetch_thumb(net_fetch, item, path):
    net_fetch.download(item["_thumb"], path, max_bytes=4 * 1024 * 1024, timeout_s=10)
    im = Image.open(path).convert("RGB")
    return im


def contact_sheet(cands):
    """Every candidate with its number, for the Look. Cells as large as the count allows."""
    n = max(1, len(cands))
    best = None
    for ch in range(420, 60, -4):
        cw = int(ch * 0.62)
        cols, rows = (BOARD_W + 3) // (cw + 3), (BOARD_H + 3) // (ch + 3)
        if cols * rows >= n:
            best = (cols, rows, cw, ch)
            break
    cols, rows, cw, ch = best or (16, 8, 94, 106)
    used = (n + cols - 1) // cols
    im = Image.new("RGB", (BOARD_W, used * (ch + 3)), boards.BG)
    f = boards.font(15)
    for i, c in enumerate(cands):
        t = c.get("_img")
        if t is None:
            continue
        cell = boards.fit(t, cw, ch)
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, 30, 18), fill=(0, 0, 0))
        d.text((3, 1), str(i), fill=(255, 225, 60), font=f)
        im.paste(cell, ((i % cols) * (cw + 3), (i // cols) * (ch + 3)))
    return im


LOOK_SYSTEM = """You help a film researcher. You see numbered stock candidates and a list of wishes from a film. Keep the pictures an art director would be proud of: they plainly show what the wish wants, they feel like an advert or a film, not a stock catalogue: real light, a clear subject, restraint. Prefer dark, rich or graphic frames over flat catalogue shots. Drop anything off-subject, cheesy, watermarked, staged with grinning models, generic sale or discount signs, or showing a product the film doesn't sell, or a lookalike of what the customer sells. Drop any person who could be taken for the speaker, or who stands in for the speaker: this film already has its person. Objects, hands, textures and places travel; a stranger's face doesn't.
Answer with one JSON object and nothing else: {"keep":[{"c":number,"wish":number,"what":"six to ten plain words on what the picture shows"}]}. Up to 3 for each wish, the best of each first. A wish may keep none when nothing fits: an honest gap is better than a wrong picture."""


def parse_keep(txt):
    """Object by object: one malformed line from a cheap model costs that line, not the Bin."""
    dec = json.JSONDecoder()
    out, i = [], max(txt.find("["), 0)
    while True:
        j = txt.find("{", i)
        if j < 0:
            return out
        try:
            obj, i = dec.raw_decode(txt, j)
            if isinstance(obj, dict) and "c" in obj:
                out.append(obj)
        except ValueError:
            i = j + 1


def look(sheet_path, cands, wishes, deadline):
    """One vision pick. Returns (keep list, usd)."""
    lines = [f"wish {i}: {w.get('want')}" for i, w in enumerate(wishes)]
    lines.append("Candidates by number: " + ", ".join(f"{i} for wish {x['_wish']}" for i, x in enumerate(cands)))
    r = call.stream(LOOK_MODEL, LOOK_SYSTEM, [brief.image_block(sheet_path), {"type": "text", "text": "\n".join(lines)}], max_tokens=2500, effort="low",
                    purpose="director_look", deadline=deadline, thinking=False)
    return parse_keep(r.text), r.usd


def bin_board(items, work, path):
    cols, rows = 6, 2
    gap = 4
    cw, ch = (BOARD_W - (cols - 1) * gap) // cols, (BOARD_H - (rows - 1) * gap) // rows
    im = Image.new("RGB", (BOARD_W, BOARD_H), boards.BG)
    f, fs = boards.font(19), boards.font(13)
    for i, it in enumerate(items[:cols * rows]):
        p = os.path.join(work, it["path"])
        fr = Image.open(p).convert("RGB")
        fr.thumbnail((900, 900))
        cell = boards.fit(fr, cw, ch) if fr is not None else Image.new("RGB", (cw, ch), boards.BG)
        d = ImageDraw.Draw(cell)
        label = it.get("ref") or f"bin:{it['id']}"
        d.rectangle((0, 0, d.textlength(label, font=f) + 10, 25), fill=(0, 0, 0))
        d.text((5, 2), label, fill=(255, 225, 60), font=f)
        tail = (f"{it['dur']:.1f}s" if it["kind"] == "video" else "photo") + f" {it['class']}"
        d.rectangle((0, ch - 19, d.textlength(tail, font=fs) + 8, ch), fill=(0, 0, 0))
        d.text((4, ch - 18), tail, fill=boards.FG, font=fs)
        im.paste(cell, ((i % cols) * (cw + gap), (i // cols) * (ch + gap)))
    boards.save(im, path, quality=85)


def plates_for(per, n, work):
    """Designed pages from the film's real colours. They exist before any library answers."""
    accs = [a["hex"] for a in per.get("accents", [])]
    tx = [t.get("colour") for t in per.get("text", []) if t.get("colour")]
    hot = next((c for c in tx if c), None) or (accs[-1] if accs else "#C2080D")
    deep = shade(hot, 0.72)
    out = [dict(id="p0", c0=hot, c1=deep, why="the saturated colour already in the footage, at page strength"),
           dict(id="p1", c0="#111113", c1="#050506", why="near black, for a page or a pause"),
           dict(id="p2", c0="#F2EFE9", c1="#DAD5CC", why="paper, for a dry fact")]
    return out[:max(1, n)]


def shade(hx, k):
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % (int(r * k), int(g * k), int(b * k))


def read_pictures(work, rows):
    """What each Bin picture already holds: printed text and faces, with boxes, by signal. The compiler
    keeps type off them and the Brief says so, so a title never lands on a title printed in a photo."""
    import spatial
    for r in rows:
        p = os.path.join(work, r["path"])
        try:
            d = spatial.analyze_frame(p)
        except Exception:  # noqa: BLE001 a picture unread is a picture without boxes
            continue
        text = [dict(s="", b=[round(float(v), 3) for v in b]) for b in d.get("text") or []]
        r["seen"] = [dict(t=0.0, text=text, faces=[[round(float(v), 3) for v in q[:4]] for q in d.get("faces") or []])]


def fetch_stills(cands, tmp):
    import net_fetch

    def one(i):
        c = cands[i]
        return i, fetch_thumb(net_fetch, c, os.path.join(tmp, f"{i}.jpg"))
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(one, i) for i in range(len(cands))]
        for f in cf.as_completed(futs):
            try:
                i, im = f.result()
                cands[i]["_img"] = im
            except Exception:  # noqa: BLE001 a missing thumb only loses that candidate
                pass
    return [c for c in cands if c.get("_img") is not None]


def run(work, scout, per, acquire, deadline):
    """acquire(candidate) -> {"storage_key","w","h","dur","fps"} or None. Writes bin.json and the board."""
    t_all = time.monotonic()
    wishes = (scout or {}).get("wishes") or []
    import broll_source
    import stock
    report, cands = [], []
    t_search = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda w: search_wish(stock, broll_source, w) if not w.get("mine") else ([], [], 0.0), wishes))
    for i, (wish, (hits, errs, secs)) in enumerate(zip(wishes, results)):
        report.append(dict(wish=i, w=wish.get("w") or wish.get("at"), want=wish.get("want"), own=wish.get("own"), mine=bool(wish.get("mine")), seconds=secs, hits=len(hits), errors=errs))
        for h in hits:
            h["_wish"] = i
            cands.append(h)
    t_search = round(time.monotonic() - t_search, 2)
    tmp = tempfile.mkdtemp(prefix="supply-", dir=work)
    usd = 0.0
    items = []
    try:
        cands = fetch_stills(cands, tmp)
        keep = []
        if cands:
            sheet = contact_sheet(cands)
            sheet_path = os.path.join(tmp, "sheet.jpg")
            boards.save(sheet, sheet_path, quality=82)
            try:
                keep, usd = look(sheet_path, cands, wishes, min(deadline, time.monotonic() + 12.0))
            except Exception as e:  # noqa: BLE001 no Look came back: one picture per wish, library order
                report.append(dict(look_error=str(e)[:120]))
            if not keep:
                first = {}
                for i, c in enumerate(cands):
                    first.setdefault(c["_wish"], i)
                keep = [dict(c=i, wish=w) for w, i in first.items()]
        binroot = os.path.join(work, "bin")
        shutil.rmtree(binroot, ignore_errors=True)
        os.makedirs(binroot, exist_ok=True)
        by_wish, seen, same = {}, set(), set()   # two wishes can land on one file: it is one picture in the Bin
        for k in keep:
            n = k.get("c")
            if isinstance(n, int) and 0 <= n < len(cands) and n not in seen and cands[n].get("id") not in same:
                seen.add(n)
                same.add(cands[n].get("id"))
                by_wish.setdefault(cands[n]["_wish"], []).append((k, cands[n]))
        picks = []
        while len(picks) < MAX_ITEMS and any(by_wish.values()):       # round the wishes, so no moment goes without
            for w in sorted(by_wish):
                if by_wish[w] and len(picks) < MAX_ITEMS:
                    picks.append(by_wish[w].pop(0))
            pool = cf.ThreadPoolExecutor(max_workers=4)
        jobs = [(k, c, pool.submit(acquire, c)) for k, c in picks]
        for k, c, fut in jobs:
            try:
                got = fut.result(timeout=max(0.05, deadline - time.monotonic()))
            except Exception as e:  # noqa: BLE001 the Bin simply has one picture less
                report[c["_wish"]].setdefault("errors", []).append(f"acquire {c.get('id')}: {str(e)[:100]}")
                continue
            if not got:
                continue
            iid = len(items)
            final = f"bin/{iid}.jpg"
            if got.get("local"):                       # a still came back whole
                shutil.copyfile(got["local"], os.path.join(work, final))
            else:
                c["_img"].convert("RGB").save(os.path.join(work, final), quality=90)
            cls, lic, lurl = licence_class(c)
            w0 = wishes[c["_wish"]]
            kind = "video" if c.get("kind") == "video" else "image"
            row = dict(id=iid, kind=kind, path=final, w=got.get("w") or c.get("width"), h=got.get("h") or c.get("height"), storage_key=got["storage_key"])
            if kind == "video":
                row.update(dur=round(float(got.get("dur") or c.get("duration_s") or 0), 2), fps=got.get("fps"))
            row.update({"what": k.get("what") or c.get("description") or c.get("title") or "", "for": f"w{w0.get('w')}" if w0.get("w") else str(w0.get("at")),
                        "wish": c["_wish"], "class": cls, "licence": lic, "licence_url": lurl, "provider": c.get("provider"), "author": c.get("credit"),
                        "page": c.get("page_url"), "query": c.get("_q")})
            items.append(row)
        pool.shutdown(wait=False, cancel_futures=True)
        read_pictures(work, items)
        plates = plates_for(per, 3, work)
        for r in report:
            if "wish" in r:
                r["bin"] = [it["id"] for it in items if it["wish"] == r["wish"]]
                r["fallback"] = "upload" if r.get("own") else "plate p0"
        credits = [f"{it['what']}: {it['author'] or it['provider']}, {it['licence']}, {it['page']}" for it in items if it["class"] == "B"]
        if items:
            bin_board(items, work, os.path.join(work, "bin_board.jpg"))
        out = dict(own=[], items=items, plates=plates, wishes=report, credits=credits,
                   usage=dict(model=LOOK_MODEL, usd=round(usd, 6), candidates=len(cands), search_seconds=t_search, seconds=round(time.monotonic() - t_all, 1)))
        with open(os.path.join(work, "bin.json"), "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
