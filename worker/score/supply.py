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
    if prov == "footage":                       # real video, not licensed: only behind footage's switch (owner's decision)
        return "F", item.get("license"), item.get("page_url")
    if prov == "designed":
        return "A", "Designed by Valmera from the film's own words", None
    if prov == "pexels":
        return "A", "Pexels License", "https://www.pexels.com/license/"
    lic = str(item.get("license") or "")
    url = item.get("license_url")
    if not lic.startswith(("CC0", "Public domain", "CC BY")):   # Openverse writes "by-sa-4.0"
        import broll_source
        canon = broll_source.canon_licence(lic, url)
        if canon is None or canon[0].startswith(("Pexels", "Pixabay")):
            return None, lic, url
        lic, url = canon[0], canon[1] or url
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
    qs = [q for q in (wish.get("q") or []) if q][:1]     # the second search is the words' own thing (search_thing)
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


LOOK_SYSTEM = """You help a film researcher. You see numbered stock candidates and a list of wishes from a film. Each picture would take the whole frame in place of the person talking. Keep only pictures that show the very thing the wish names, so a viewer sees what is being said: never something loosely like it, a mood, a metaphor or a picture that is merely near the words. Of those, keep the ones an art director would be proud of: they plainly show what the wish wants, they feel like an advert or a film, not a stock catalogue: real light, a clear subject, restraint. Prefer dark, rich or graphic frames over flat catalogue shots. Drop anything off-subject, cheesy, watermarked, staged with grinning models, generic sale or discount signs, or showing a product the film doesn't sell, or a lookalike of what the customer sells. Drop any person who could be taken for the speaker, or who stands in for the speaker: this film already has its person. Objects, hands, textures and places travel; a stranger's face doesn't.
Answer with one JSON object and nothing else: {"keep":[{"c":number,"wish":number,"what":"six to ten plain words on what the picture shows"}]}. Up to 3 for each wish, the best of each first. A wish may keep none: an honest gap is better than a picture that is only near the words."""


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
    lines = [f"wish {i}: {w.get('want')}" + (f" | words said: \"{w['_said']}\"" if w.get("_said") else "")
             + (f" | the words name: {w['_thing']}" if w.get("_thing") else "") for i, w in enumerate(wishes)]
    lines.append("Candidates by number: " + ", ".join(f"{i} for wish {x['_wish']}" for i, x in enumerate(cands)))
    r = call.stream(LOOK_MODEL, LOOK_SYSTEM, [brief.image_block(sheet_path), {"type": "text", "text": "\n".join(lines)}], max_tokens=2500, effort="low",
                    purpose="director_look", deadline=deadline, thinking=False)
    return parse_keep(r.text), r.usd


CHECK_MODEL = os.environ.get("BIN_CHECK_MODEL", "claude-opus-5-5")   # the last look's eyes: what it removes never enters
CHECK_S = 18.0
CHECK_RESERVE_S = 8.0      # the end of the budget belongs to the look: a picture nobody checked never enters
ACQUIRE_S = 5.0            # and the seconds before it to the fetch: three named pictures were found and none arrived (p5591, 13 Nov), since
                           # the named check ran to 1.5 s before the look's reserve and every fetch then timed out


def wish_second(work, w):
    """The upload's second where a wish lands: its first word's start, or its `at`."""
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words = json.load(f).get("words") or []
    except (OSError, ValueError):
        words = []
    s = str(w or "").strip().strip("w")
    a = s.split("-")[0].strip() if "-" in s else s
    if a.isdigit() and words and not isinstance(w, (int, float)):
        try:
            return float(words[min(int(a), len(words) - 1)].get("t0") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(w)
    except (TypeError, ValueError):
        return 0.0


def pair_board(work, rows, path, cell_h=400):
    """rows: [(upload second, picture path)]. The upload above, the picture as the film would show it below
    (covering the frame), numbered from 0. Returns how many pairs have both frames."""
    src = os.path.join(work, "source.mp4")
    first = boards.grab(src, 0.5, height=cell_h)
    W, H = first.size if first is not None else (9, 16)
    cw = max(80, int(cell_h * W / max(1, H)))
    while len(rows) * (cw + 6) > 2400 and cell_h > 200:
        cell_h -= 40
        cw = max(80, int(cell_h * W / max(1, H)))
    gap, head = 6, 24
    im = Image.new("RGB", (max(1, len(rows) * (cw + gap) - gap), head + 2 * cell_h + gap), boards.BG)
    d = ImageDraw.Draw(im)
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        ups = list(pool.map(lambda r: boards.grab(src, r[0], height=cell_h * 2), rows))
    drawn = 0
    for i, ((t, pic), up) in enumerate(zip(rows, ups)):
        x = i * (cw + gap)
        if up is not None:
            im.paste(boards.fit(up, cw, cell_h), (x, head))
        try:
            p = Image.open(pic).convert("RGB")
        except OSError:
            continue
        k = max(cw / p.width, cell_h / p.height)
        p = p.resize((max(cw, int(p.width * k)), max(cell_h, int(p.height * k))))
        lx, ty = (p.width - cw) // 2, (p.height - cell_h) // 2
        im.paste(p.crop((lx, ty, lx + cw, ty + cell_h)), (x, head + cell_h + gap))
        d.text((x + 4, 3), f"pair {i + 1}  @{t:.1f}s  upload above, film below", fill=boards.FG, font=boards.font(13))
        drawn += 1
    boards.save(im, path, quality=86)
    return drawn


def clause_of(work, w):
    """The words said where a wish lands: its word range, read from the session's index."""
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words = json.load(f).get("words") or []
    except (OSError, ValueError):
        return ""
    lo = hi = None
    if isinstance(w, str) and "-" in w.strip("w"):
        a, _, b = w.strip("w").partition("-")
        if a.strip().isdigit() and b.strip().isdigit():
            lo, hi = int(a), int(b)
    elif isinstance(w, str) and w.strip("w").isdigit():
        lo = hi = int(w.strip("w"))
    if lo is not None:
        return " ".join(str(x.get("w", "")) for x in words[max(0, lo - 2):hi + 3])
    try:
        t = float(w)
    except (TypeError, ValueError):
        return ""
    return " ".join(str(x.get("w", "")) for x in words if t - 2.5 <= float(x.get("t0", -9)) <= t + 2.5)


def check_fit(work, items, wishes, deadline, asks=()):
    """Before a picture enters the Bin it faces the last look itself (taste.LOOK_SYSTEM, its model, its
    pairs): the upload at the picture's moment above, the picture full frame below, the words said there as
    the caption. It stays only when the look would leave it in the film (12 Nov: the last look removed a
    found picture on 5 of 7 films as "a library picture of something loosely like it, in place of the
    person talking"; each removal cost a second render). Returns (kept items, usd, report). No answer
    keeps only the named pictures, which a check that each shows its subject already passed."""
    from . import taste
    if not items:
        return items, 0.0, []
    tmp = tempfile.mkdtemp(prefix="check-", dir=work)
    try:
        rows, saids = [], []
        for it in items:
            w0 = wishes[it["wish"]] if 0 <= int(it.get("wish", -1)) < len(wishes) else {}
            ref = w0.get("w") or w0.get("at")
            rows.append((wish_second(work, ref), os.path.join(work, it["path"])))
            saids.append(clause_of(work, ref))
        verdict, usd, errors = {}, 0.0, []
        n_pairs = taste.LOOK_PAIRS

        def one(k):
            chunk = list(range(k, min(len(items), k + n_pairs)))
            board = os.path.join(tmp, f"pairs_{k}.jpg")
            pair_board(work, [rows[n] for n in chunk], board)
            ask = ["The pairs, with what the editor added at each moment:"]
            for j, n in enumerate(chunk, 1):
                shows = str(items[n].get("what") or "")[:90]      # the look can't tell Webber's car from any car unless told what it is
                ask.append(f"pair {j} @{rows[n][0]:.1f}s: b{n}.s1: " + ("a designed picture made of the words said" if items[n].get("provider") == "designed" else "a found picture")
                           + ", full frame for a second or two" + (f", showing {shows}" if shows else "")
                           + (f"; cap: the caption, reading {saids[n]}" if saids[n] else ""))
            if asks:        # what the customer asked for is theirs: the film's own look leaves an ask-bound picture in
                ask.append("The customer typed these asks: " + json.dumps([a.get("said") for a in asks if a.get("said")], ensure_ascii=False)[:900]
                           + ". A picture that shows what an ask asked for is the customer's own choice: post film for it unless it does not show that.")
            ask.append("Now the JSON.")
            r = call.stream(CHECK_MODEL, taste.LOOK_SYSTEM, [brief.image_block(board), {"type": "text", "text": "\n".join(ask)}],
                            max_tokens=1200, effort="low", purpose="director_bin_check",
                            deadline=min(deadline, time.monotonic() + CHECK_S), thinking=False)
            return chunk, r
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            for fut in [pool.submit(one, k) for k in range(0, len(items), n_pairs)]:
                try:
                    chunk, r = fut.result()
                except Exception as e:  # noqa: BLE001 that board has no verdict
                    errors.append(str(e)[:120])
                    continue
                usd += float(r.usd or 0.0)
                v = taste.read_look(r.text, len(chunk), {f"b{n}.s1" for n in chunk})
                pairs = []
                try:
                    pairs = json.loads(r.text[r.text.find("{"):r.text.rfind("}") + 1]).get("pairs") or []
                except (ValueError, AttributeError):
                    pass
                if v is None:
                    continue
                for j, n in enumerate(chunk):
                    p = pairs[j] if j < len(pairs) and isinstance(pairs[j], dict) else {}
                    post = str(p.get("post", "")).strip().lower()
                    verdict[n] = (f"b{n}.s1" not in v["remove"] and post in ("film", "same"), str(p.get("why") or "")[:100])
        kept, rep = [], []
        for n, it in enumerate(items):
            ok, why = verdict.get(n, (bool(it.get("named")) if errors else False, "no verdict"))
            rep.append(dict(id=it["id"], what=it.get("what"), designed=it.get("provider") == "designed", ok=ok, why=why))
            if ok:
                kept.append(it)
        if errors:
            rep.append(dict(check_error="; ".join(errors)[:200]))
        return kept, usd, rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def renumber(work, items):
    """Bin ids stay 0..n-1 after a check drops some: the Director reads ids off the board."""
    out = []
    for n, it in enumerate(items):
        if it["id"] != n:
            ext = os.path.splitext(it["path"])[1]
            dst = f"bin/{n}{ext}"
            os.replace(os.path.join(work, it["path"]), os.path.join(work, dst))
            it = dict(it, id=n, path=dst)
        out.append(it)
    return out


def bin_board(items, work, path, own=()):
    cols, rows = 6, 2
    gap = 4
    cw, ch = (BOARD_W - (cols - 1) * gap) // cols, (BOARD_H - (rows - 1) * gap) // rows
    im = Image.new("RGB", (BOARD_W, BOARD_H), boards.BG)
    f, fs = boards.font(19), boards.font(13)
    n = min(len(items), cols * rows)
    for j, (label, cell) in enumerate(own_cells(work, list(own)[:cols * rows - n], cw, ch)):  # the upload's own, after the found
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, d.textlength(label, font=fs) + 10, 21), fill=(0, 0, 0))
        d.text((5, 3), label, fill=(120, 230, 255), font=fs)
        im.paste(cell, (((n + j) % cols) * (cw + gap), ((n + j) // cols) * (ch + gap)))
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


# ------------------------------------------------------------------ named subjects

def name_wishes(work, wishes, about=""):
    """(subjects per wish, usd): what each moment's words and want name that a real picture could
    show (a driver, a city, a brand), by one model call. The customer's own (`mine`) are not asked:
    only the upload shows those. No answer names nothing, and the libraries answer as before."""
    import broll_source
    texts, idx = [], []
    for i, w in enumerate(wishes):
        if w.get("mine"):
            continue
        said = clause_of(work, w.get("w") or w.get("at"))
        # the words first: the Scout's want is often a mood ("a young driver's uncertain future") and the name is in
        # what was said ("où Oscar partira"). 13 Nov: a film that names four people got no named picture on one run in two
        texts.append((f'words said there: "{said}" | ' if said else "") + f"the editor wants: {w.get('want') or ''}")
        idx.append(i)
    out = [[] for _ in wishes]
    if not texts:
        return out, 0.0
    got, usd = broll_source.name_many(texts, context=about)        # what the film is about turns "Oscar" into Oscar Piastri
    for j, subs in zip(idx, got):
        out[j] = subs[:1]
    return out, usd


def named_for(work, wish, subject, hits, deadline):
    """Checked pictures of a named subject for one wish: Commons and Openverse by its name, the
    library's hits for the wish beside them, one check that each shows it. (kept, report)."""
    import broll_source
    kind = broll_source.KIND_VIDEO if wish.get("kind") == "video" else broll_source.KIND_PHOTO
    return broll_source.find_named(subject, kind, 3, also=hits, workdir=work, deadline=deadline)


# ------------------------------------------------------------------ designed pictures

MAX_DESIGNED = 4


def accent_of(per):
    """The film's own strongest colour for a designed picture's accent, when it reads on a dark page."""
    hot = plates_for(per or {}, 1, None)[0]["c0"]
    try:
        r, g, b = (int(hot[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return "#E8B23A"
    return hot if (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 >= 0.38 else "#E8B23A"


def reads_for(work, wishes, per):
    """(per wish {"thing", "design"} or None, usd): the photographable thing each moment's words name, for
    a library search by that very thing, and a designed picture where the words carry a number, two sides,
    steps, dates or a term, made only of what is said. One model call reads the words; nothing here does.
    Wordless moments and the customer's own get none."""
    import broll_source
    texts, idx = [], []
    for i, w in enumerate(wishes):
        if w.get("mine"):
            continue
        said = clause_of(work, w.get("w") or w.get("at"))
        if len(said.split()) < 3:
            continue
        texts.append(said)
        idx.append(i)
    out = [None] * len(wishes)
    if not texts:
        return out, 0.0
    got, usd = broll_source.read_moments(texts, accent=accent_of(per))
    for j, g in zip(idx, got):
        out[j] = g
    return out, usd


def search_thing(stock, wish, thing, budget_s):
    """Library hits for the very thing a moment's words name, in place of the Scout's second search (the
    same one library call per wish as before: the Pexels key's hourly allowance is shared by every film)."""
    kind = "photo" if wish.get("kind") == "photo" else "video"
    pool = cf.ThreadPoolExecutor(max_workers=1)
    futs = [pool.submit(stock._pexels_search, thing, kind, "portrait", 4)]
    out = []
    t0 = time.monotonic()
    for fut in futs:
        try:
            r = fut.result(timeout=max(0.05, budget_s - (time.monotonic() - t0)))
        except Exception:  # noqa: BLE001 late or down: the wish keeps what it has
            continue
        for it in (r[0] if isinstance(r, tuple) else r) or []:
            if licence_class(it)[0] and it.get("_thumb") and not (it.get("kind") == "video" and (it.get("duration_s") or 0) < 2):
                it["_q"] = thing
                out.append(it)
    pool.shutdown(wait=False, cancel_futures=True)
    return out


# ------------------------------------------------------------------ the upload's own moments

OWN_FRAMES = 16
OWN_ZOOM_MAX = 2.0
OWN_SYSTEM = """You help a film editor re-use the customer's own footage. You see numbered frames from the upload with their times, and moments of the film that want a picture, each with the second it lands and what is said there. For each moment, find a frame from ANOTHER time whose detail shows the very thing the words name, better than the frame at that moment does: the product, hands at work, a sign, a tool, a place, a gesture. Give the box around that detail as fractions of the frame, x0 y0 x1 y1 from the top left, tight on it but never cutting it, never cutting a head or printed words. Only a detail that is plainly there, sharp, and still reads when enlarged up to 2 times. Leave a moment out when no other frame shows its thing better than the moment itself, or when the words name nothing a frame could show: most moments are left out.
Answer with one JSON object and nothing else: {"own":[{"m":moment number,"f":frame number,"box":[x0,y0,x1,y1],"what":"at most eight plain words"}]}."""


def own_sheet(src, times, W, H):
    """The upload's frames, numbered with their times, on one sheet."""
    cols = 4 if H >= W else 3
    cw = (BOARD_W - (cols - 1) * 3) // cols
    ch = int(cw * H / max(1, W))
    rows = (len(times) + cols - 1) // cols
    im = Image.new("RGB", (BOARD_W, rows * (ch + 3)), boards.BG)
    f = boards.font(20)
    got = 0
    for i, t in enumerate(times):
        fr = boards.grab(src, t, width=cw)
        if fr is None:
            continue
        got += 1
        cell = boards.fit(fr, cw, ch)
        d = ImageDraw.Draw(cell)
        label = f"{i}  {t:.1f}s"
        d.rectangle((0, 0, d.textlength(label, font=f) + 10, 26), fill=(0, 0, 0))
        d.text((5, 2), label, fill=(255, 225, 60), font=f)
        im.paste(cell, ((i % cols) * (cw + 3), (i // cols) * (ch + 3)))
    return im if got else None


def reframe(box, W, H, out_ratio=9 / 16):
    """A detail box (source fractions) to a crop of the output's shape around it, no tighter than
    OWN_ZOOM_MAX of the full-height frame, inside the source. None for a box that can't be read."""
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return None
    bw, bh = (x1 - x0) * W * 1.3, (y1 - y0) * H * 1.3            # air round the detail
    h = max(bh, bw / out_ratio, H / OWN_ZOOM_MAX)
    w = h * out_ratio
    if w > W:
        w, h = W, W / out_ratio
    h = min(h, H)
    w = min(w, h * out_ratio)
    cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
    left = min(max(cx - w / 2, 0.0), W - w)
    top = min(max(cy - h / 2, 0.0), H - h)
    return [round(left / W, 3), round(top / H, 3), round((left + w) / W, 3), round((top + h) / H, 3)]


def own_moments(work, wishes, per, deadline):
    """(rows, usd): moments of the upload, re-framed, for the wishes the upload itself can show (the
    customer's own product, place or person, and what the Scout marked `own`). Each row names its
    source span and crop as the Score writes them: {"src": "main@12.0-14.0", "crop": [...]}."""
    want = [(i, w) for i, w in enumerate(wishes) if w.get("own") or w.get("mine")][:6]
    dur, W, H = float(per.get("dur") or 0), int(per.get("W") or 0), int(per.get("H") or 0)
    src = os.path.join(work, "source.mp4")
    if not want or dur < 2.0 or not W or not H or not os.path.exists(src):
        return [], 0.0
    n = OWN_FRAMES
    times = [round(dur * (k + 0.5) / n, 2) for k in range(n)]
    tmp = tempfile.mkdtemp(prefix="own-", dir=work)
    try:
        sheet = own_sheet(src, times, W, H)
        if sheet is None:
            return [], 0.0
        path = os.path.join(tmp, "own.jpg")
        boards.save(sheet, path, quality=82)
        lines = []
        for j, (_i, w) in enumerate(want):
            ref = w.get("w") or w.get("at")
            said = clause_of(work, ref)
            lines.append(f"moment {j} at {wish_second(work, ref):.1f}s: {w.get('want')}" + (f' | words said: "{said}"' if said else ""))
        r = call.stream(LOOK_MODEL, OWN_SYSTEM, [brief.image_block(path), {"type": "text", "text": "\n".join(lines)}],
                        max_tokens=900, effort="low", purpose="director_own", deadline=deadline, thinking=False)
        rows, seen = [], set()
        for o in parse_keep(r.text.replace('"m"', '"c"')):
            j, f = o.get("c"), o.get("f")
            if not (isinstance(j, int) and isinstance(f, int) and 0 <= j < len(want) and 0 <= f < n) or (j, f) in seen:
                continue
            crop = reframe(o.get("box"), W, H)
            if crop is None or crop[3] - crop[1] > 0.87:     # no closer than the take itself: nothing re-framed
                continue
            i, w = want[j]
            if abs(times[f] - wish_second(work, w.get("w") or w.get("at"))) < 1.5:
                continue                                # the same instant, enlarged: a zoom, not another moment
            seen.add((j, f))
            t0 = max(0.0, times[f] - 0.8)
            t1 = min(dur, t0 + 2.4)
            rows.append(dict(id=len(rows), src=f"main@{t0:.2f}-{t1:.2f}", crop=crop, at=times[f], wish=i,
                             what=str(o.get("what") or w.get("want") or "")[:80],
                             **{"for": f"w{w.get('w')}" if w.get("w") else str(w.get("at"))}))
        return rows[:6], float(r.usd or 0.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


HOOK_S = 3.0               # a talking film shows its first picture inside this
HOOK_CALL_S = 4.0
VIEW_CALL_S = 22.0         # sixteen frames read and up to six boxes written: 11 s measured
HOOK_TOKENS, VIEW_TOKENS = 1200, 3000     # room to answer: at 900 the views call spent every token before its first word and returned nothing (13 Nov, five uploads of five)
HOOK_SYSTEM = """You help a film editor open a short film of a person talking. You get what the film is about and its first words, each with its number. Name the one picture a viewer should see in the film's first three seconds, before or beside the speaker: the very thing the film is about, as its first words or its subject name it. A named person, team, company, place, product, animal, object or event that a photograph or a clip can show. Never a mood, a metaphor or a feeling, never the speaker, never a generic picture of the topic's field. When the thing is the speaker's own (their product, shop, vehicle, workplace), set "mine" true: only the upload can show it. When the first words and the subject name nothing a picture can show, answer {"hook":null}.
Answer with one JSON object and nothing else: {"hook":{"w":"first-last word number the picture lands on, inside the first three seconds","want":"what the picture shows, with the full name of who or what it is","q":["a short stock search","a second, broader one"],"kind":"photo or video","mine":false}}."""
VIEW_SYSTEM = """You help a film editor cut a short film from one upload that has no words to follow. You see numbered frames from the upload with their times. Find up to six details that deserve a shot of their own, the way an editor cuts in an insert: the thing the clip is about seen closer (a label, hands at work, a face reacting, the flame, the tool, the plate, the screen), or a second subject the wide frame holds. Give the box around each detail as fractions of the frame, x0 y0 x1 y1 from the top left, tight on it but never cutting it, never through a head or through printed words. Only a detail that is plainly there, sharp, and still reads when enlarged up to 2 times. Take details from different moments and different parts of the frame, and leave out a frame that only repeats another. When the frames are already close on one thing and hold nothing else to cut to, answer {"own":[]}.
Answer with one JSON object and nothing else: {"own":[{"c":frame number,"box":[x0,y0,x1,y1],"what":"at most eight plain words"}]}."""


def _first_words(work, until_s=7.0, most=24):
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words = json.load(f).get("words") or []
    except (OSError, ValueError):
        return []
    return [(i, w) for i, w in enumerate(words[:most]) if float(w.get("t0") or 0.0) < until_s]


def hook_wish(work, scout, wishes, per, deadline):
    """(wish or None, usd): the picture a talking film opens on, when no wish of the Scout's lands inside its first
    3 s. One model call reads what the film is about and its first words; nothing here reads them. The wish joins
    the others and takes the same route: the libraries, a named search, the check."""
    first = _first_words(work)
    dur = float((per or {}).get("dur") or 0.0)
    if len(first) < 4 or dur < 6.0:
        return None, 0.0
    for w in wishes:                         # the Scout already asked for a picture there
        if not w.get("mine") and wish_second(work, w.get("w") or w.get("at")) < HOOK_S and (str(w.get("w") or "").strip("w")[:1].isdigit() or w.get("at") is not None):
            return None, 0.0
    lines = ["The film is about: " + str((scout or {}).get("about") or "not known"),
             "First words: " + " ".join(f"{i}:{w.get('w')}" for i, w in first)]
    r = call.stream(LOOK_MODEL, HOOK_SYSTEM, [{"type": "text", "text": "\n".join(lines)}], max_tokens=HOOK_TOKENS, effort="low",
                    purpose="director_hook", deadline=deadline, thinking=False)
    txt = r.text or ""
    try:
        o = json.JSONDecoder().raw_decode(txt, max(txt.find("{"), 0))[0].get("hook")
    except (ValueError, AttributeError):
        return None, float(r.usd or 0.0)
    if not isinstance(o, dict) or not str(o.get("want") or "").strip():
        return None, float(r.usd or 0.0)
    ref = str(o.get("w") or "0").strip().strip("w")
    a = ref.split("-")[0].strip()
    if not a.isdigit() or int(a) > first[-1][0] or wish_second(work, ref) > HOOK_S + 1.0:
        ref = "0-1"
    q = [str(x)[:80] for x in (o.get("q") or []) if isinstance(x, str) and x.strip()][:2]
    return dict(w=ref if "-" in ref else f"{ref}-{ref}", at=None, want=str(o["want"])[:160], own=bool(o.get("mine")), mine=bool(o.get("mine")),
                q=q, kind="video" if o.get("kind") == "video" else "photo", hook=True), float(r.usd or 0.0)


def own_views(work, per, deadline, n_have=0):
    """(rows, usd): for footage with no words to follow, details of the upload itself worth a shot of their own,
    each another moment re-framed. One model call looks at sixteen frames; the boxes are checked here."""
    dur, W, H = float(per.get("dur") or 0), int(per.get("W") or 0), int(per.get("H") or 0)
    src = os.path.join(work, "source.mp4")
    if dur < 2.5 or not W or not H or not os.path.exists(src):
        return [], 0.0
    n = OWN_FRAMES
    times = [round(dur * (k + 0.5) / n, 2) for k in range(n)]
    tmp = tempfile.mkdtemp(prefix="view-", dir=work)
    try:
        sheet = own_sheet(src, times, W, H)
        if sheet is None:
            return [], 0.0
        path = os.path.join(tmp, "views.jpg")
        boards.save(sheet, path, quality=82)
        r = call.stream(LOOK_MODEL, VIEW_SYSTEM, [brief.image_block(path), {"type": "text", "text": f"The upload runs {dur:.1f} s. Frames 0 to {n - 1}."}],
                        max_tokens=VIEW_TOKENS, effort="low", purpose="director_views", deadline=deadline, thinking=False)
        rows, seen = [], []
        for o in parse_keep(r.text):
            f = o.get("c")
            if not (isinstance(f, int) and 0 <= f < n):
                continue
            crop = reframe(o.get("box"), W, H)
            if crop is None or crop[3] - crop[1] > 0.72:      # under 1.4 times closer is the take again, not a detail
                continue
            cx, cy = (crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2
            if any(abs(times[f] - t) < 1.0 and abs(cx - x) + abs(cy - y) < 0.2 for t, x, y in seen):
                continue                                # the same detail at the same moment
            seen.append((times[f], cx, cy))
            span = min(2.4, dur)
            t0 = min(max(0.0, times[f] - 0.8), max(0.0, dur - span))
            rows.append(dict(id=n_have + len(rows), src=f"main@{t0:.2f}-{t0 + span:.2f}", crop=crop, box=[round(float(v), 3) for v in o["box"]], at=times[f],
                             wish=None, what=str(o.get("what") or "")[:80], **{"for": f"@{times[f]:.1f}"}))
        return rows[:6], float(r.usd or 0.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def own_cells(work, rows, cw, ch):
    """Board cells for the upload's own moments: the crop as the film would show it."""
    src = os.path.join(work, "source.mp4")
    out = []
    for r in rows:
        fr = boards.grab(src, r["at"], width=720)
        if fr is None:
            continue
        x0, y0, x1, y1 = r["crop"]
        w, h = fr.size
        out.append((f"view:{r['id']} @{r['at']:.1f}s", boards.fit(fr.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))), cw, ch)))
    return out


def run(work, scout, per, acquire, deadline):
    """acquire(candidate) -> {"storage_key","w","h","dur","fps"} or None. Writes bin.json and the board.

    Three supplies at once: the libraries for each wish; for a wish whose words name a real person,
    place, brand or event, Commons and Openverse by that name with one check that each picture shows
    it (a library lookalike faces the same check and never stands in); and the upload's own moments,
    re-framed, for what only the upload can show."""
    t_all = time.monotonic()
    wishes = (scout or {}).get("wishes") or []
    import broll_source
    import stock
    report, cands = [], []
    usd = 0.0
    words_n = 0
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words_n = len(json.load(f).get("words") or [])
    except (OSError, ValueError):
        pass
    talk = words_n / max(float((per or {}).get("dur") or 0.0), 1.0) >= 1.0
    if talk:
        try:
            hook, hook_usd = hook_wish(work, scout, wishes, per or {}, min(deadline, time.monotonic() + HOOK_CALL_S))
            usd += hook_usd
            if hook:
                wishes = [hook] + list(wishes)
                if isinstance(scout, dict):
                    scout["wishes"] = wishes           # the Brief lists it with the Scout's own
        except Exception as e:  # noqa: BLE001 no hook wish: the Scout's wishes stand
            report.append(dict(hook_error=str(e)[:120] or "late"))
    side = cf.ThreadPoolExecutor(max_workers=6)
    view_f = None if talk else side.submit(broll_source.carried(own_views), work, per or {}, min(deadline, time.monotonic() + VIEW_CALL_S))
    name_f = side.submit(broll_source.carried(name_wishes), work, wishes, str((scout or {}).get("about") or ""))
    own_f = side.submit(broll_source.carried(own_moments), work, wishes, per or {}, min(deadline, time.monotonic() + 14.0))
    read_f = side.submit(broll_source.carried(reads_for), work, wishes, per or {})

    def things_found():
        """As soon as the words are read: each moment's very thing, searched by its name."""
        try:
            reads, read_usd = read_f.result(timeout=max(0.05, min(deadline - 14.0, time.monotonic() + 10.0) - time.monotonic()))
        except Exception as e:  # noqa: BLE001 unread: the Scout's own second search stands
            reads, read_usd = [None] * len(wishes), 0.0
            report.append(dict(read_error=str(e)[:120] or "late"))
        things = {i: r["thing"] for i, r in enumerate(reads) if r and r.get("thing") and not wishes[i].get("mine")}
        second = {i: things.get(i) or ((w.get("q") or [None, None]) + [None])[1] for i, w in enumerate(wishes) if not w.get("mine")}
        second = {i: q for i, q in second.items() if q}
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            more = dict(zip(second, pool.map(lambda i: search_thing(stock, wishes[i], second[i], 4.0), second)))
        return reads, read_usd, things, more
    thing_f = side.submit(broll_source.carried(things_found))
    t_search = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda w: search_wish(stock, broll_source, w) if not w.get("mine") else ([], [], 0.0), wishes))
    try:
        subjects, name_usd = name_f.result(timeout=max(0.05, min(deadline, t_search + 10.0) - time.monotonic()))
        usd += name_usd
    except Exception as e:  # noqa: BLE001 no naming: the libraries answer as before
        subjects = [[] for _ in wishes]
        report.append(dict(naming_error=str(e)[:120]))
    try:
        reads, read_usd, things, more = thing_f.result(timeout=max(0.05, min(deadline - 12.0, time.monotonic() + 5.0) - time.monotonic()))
        usd += read_usd
        for i, extra in more.items():
            hits, errs, secs = results[i]
            have = {h.get("id") for h in hits}
            results[i] = (hits + [h for h in extra if h.get("id") not in have][:4], errs, secs)
    except Exception as e:  # noqa: BLE001 no reading: the Scout's searches stand alone
        reads, things = [None] * len(wishes), {}
        report.append(dict(read_error=str(e)[:120] or "late"))
    named_f = {}
    for i, (wish, (hits, errs, secs)) in enumerate(zip(wishes, results)):
        sub = subjects[i][0] if i < len(subjects) and subjects[i] else None
        report.append(dict(wish=i, w=wish.get("w") or wish.get("at"), want=wish.get("want"), own=wish.get("own"), mine=bool(wish.get("mine")),
                           seconds=secs, hits=len(hits), errors=errs, named=sub["name"] if sub else None, thing=things.get(i)))
        if sub:     # its pictures come from the named route, checked; the library's join that check
            named_f[i] = side.submit(broll_source.carried(named_for), work, wish, sub, hits, min(deadline - CHECK_RESERVE_S - ACQUIRE_S, time.monotonic() + 16.0))
            continue
        for h in hits:
            h["_wish"] = i
            cands.append(h)
    t_search = round(time.monotonic() - t_search, 2)
    tmp = tempfile.mkdtemp(prefix="supply-", dir=work)
    items = []
    try:
        cands = fetch_stills(cands, tmp)
        keep = []
        if cands:
            sheet = contact_sheet(cands)
            sheet_path = os.path.join(tmp, "sheet.jpg")
            boards.save(sheet, sheet_path, quality=82)
            try:
                seen_as = [dict(w, _said=clause_of(work, w.get("w") or w.get("at")), _thing=things.get(i)) for i, w in enumerate(wishes)]
                keep, look_usd = look(sheet_path, cands, seen_as, min(deadline - CHECK_RESERVE_S - ACQUIRE_S, time.monotonic() + 12.0))
                usd += look_usd
            except Exception as e:  # noqa: BLE001 no Look came back: one picture per wish, library order
                report.append(dict(look_error=str(e)[:120]))
            if not keep:
                first = {}
                for i, c in enumerate(cands):
                    first.setdefault(c["_wish"], i)
                keep = [dict(c=i, wish=w) for w, i in first.items()]
        named = []
        for i, fut in named_f.items():
            try:
                kept, rep = fut.result(timeout=max(0.05, deadline - CHECK_RESERVE_S - ACQUIRE_S + 0.5 - time.monotonic()))
            except Exception as e:  # noqa: BLE001 unchecked is never shown: the wish falls back
                kept, rep = [], dict(say=f"the named search did not finish ({str(e)[:80] or 'late'})")
            usd += float(rep.get("usd") or 0.0)
            report[next(n for n, r in enumerate(report) if r.get("wish") == i)].update(
                named_found=len(kept), named_say=rep.get("say"), named_checked=len(rep.get("checked") or []))
            for c in kept:
                if licence_class(c)[0] is None:
                    continue
                c["_wish"] = i
                named.append(c)
        import footage.seam                      # real footage of the named subjects: the switch is off for customers
        named += footage.seam.bin_offers(work, wishes, subjects, deadline - CHECK_RESERVE_S - ACQUIRE_S, report,
                                         str((scout or {}).get("about") or ""))
        named = fetch_stills(named, tmp)
        first_named = len(cands)
        cands = cands + named
        keep = [dict(c=first_named + j, wish=c["_wish"], what=f"{c['named']}: {c.get('shows') or ''}".strip(": ")) for j, c in enumerate(named)] + keep
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
        specs = [r.get("design") if r else None for r in reads]     # designed pictures join the same look;
        designed = 0                                                  # one stays only where no real picture did
        for i, sp in enumerate(specs):
            if sp and designed < MAX_DESIGNED:
                c = dict(broll_source.designed_hit(sp), _wish=i)
                picks.append((dict(c=-1, wish=i, what=f"designed {sp['kind']}: {sp.get('what') or ''}".strip(": ")), c))
                designed += 1
        pool = cf.ThreadPoolExecutor(max_workers=4)
        jobs = [(k, c, pool.submit(acquire, c)) for k, c in picks]
        for k, c, fut in jobs:
            try:
                got = fut.result(timeout=max(0.05, deadline - CHECK_RESERVE_S - time.monotonic()))
            except Exception as e:  # noqa: BLE001 the Bin simply has one picture less
                wr = next((r for r in report if r.get("wish") == c["_wish"]), None)
                if wr is not None:
                    wr.setdefault("errors", []).append(f"acquire {c.get('id')}: {str(e)[:100]}")
                continue
            if not got:
                continue
            iid = len(items)
            final = f"bin/{iid}.jpg"
            if got.get("local"):                       # a still came back whole
                shutil.copyfile(got["local"], os.path.join(work, final))
                if os.path.dirname(os.path.abspath(got["local"])) == os.path.abspath(work):
                    os.remove(got["local"])            # the fetched copy goes: the Bin keeps its own
            elif c.get("design"):
                broll_source.draw_designed(c, os.path.join(work, final))
            else:
                c["_img"].convert("RGB").save(os.path.join(work, final), quality=90)
            cls, lic, lurl = licence_class(c)
            w0 = wishes[c["_wish"]]
            kind = "video" if c.get("kind") == "video" else "image"
            row = dict(id=iid, kind=kind, path=final, w=got.get("w") or c.get("width"), h=got.get("h") or c.get("height"), storage_key=got["storage_key"])
            if kind == "video":
                row.update(dur=round(float(got.get("dur") or c.get("duration_s") or 0), 2), fps=got.get("fps"))
            row["_looked"] = bool(k.get("what"))
            if w0.get("hook"):
                row["hook"] = True
            row.update({"what": k.get("what") or c.get("description") or c.get("title") or "", "for": f"w{w0.get('w')}" if w0.get("w") else str(w0.get("at")),
                        "wish": c["_wish"], "class": cls, "licence": lic, "licence_url": lurl, "provider": c.get("provider"), "author": c.get("credit"),
                        "page": c.get("page_url"), "query": c.get("_q")})
            if c.get("named"):
                row.update(named=c["named"], shows=c.get("shows"))
            if c.get("design"):
                row.update(design=c["design"])
            items.append(row)
        pool.shutdown(wait=False, cancel_futures=True)
        n_before = len(items)
        items, check_usd, check_rep = check_fit(work, items, wishes, deadline, (scout or {}).get("asks") or [])
        real = {it["wish"] for it in items if it.get("provider") != "designed"}
        for it in [x for x in items if x.get("provider") == "designed" and x["wish"] in real]:
            os.remove(os.path.join(work, it["path"]))
            items.remove(it)
        for it in items:
            it.pop("_looked", None)
        dropped = n_before - len(items)
        items = renumber(work, items)
        usd += check_usd
        report.append(dict(check=check_rep, dropped=dropped, usd=round(check_usd, 6)))
        read_pictures(work, items)
        try:
            own, own_usd = own_f.result(timeout=max(0.05, deadline - time.monotonic()))
            usd += own_usd
        except Exception as e:  # noqa: BLE001 the upload still stands as itself
            own = []
            report.append(dict(own_error=str(e)[:120]))
        if view_f is not None:
            try:
                views, view_usd = view_f.result(timeout=max(0.05, deadline - time.monotonic()))
                usd += view_usd
                for v in views:
                    v["id"] = len(own)
                    own.append(v)
            except Exception as e:  # noqa: BLE001 the upload still stands as itself
                report.append(dict(view_error=str(e)[:120] or "late"))
        plates = plates_for(per, 3, work)
        for r in report:
            if "wish" in r:
                r["bin"] = [it["id"] for it in items if it["wish"] == r["wish"]]
                r["own_moments"] = [o["src"] for o in own if o["wish"] == r["wish"]]
                r["fallback"] = "upload" if r.get("own") else "plate p0"
        credits = [f"{it['what']}: {it['author'] or it['provider']}, {it['licence']}, {it['page']}" for it in items if it["class"] in ("B", "F")]
        if items or own:
            bin_board(items, work, os.path.join(work, "bin_board.jpg"), own)
        said = [r["named_say"] for r in report if r.get("named_say") and not r.get("bin")]
        out = dict(own=own, items=items, plates=plates, wishes=report, credits=credits, said=said,
                   own_lines=[f"view:{o['id']} the upload at {o['at']:.1f}s, re-framed on: {o['what']}" + (f" (for {o['for']})" if o.get("wish") is not None else "") for o in own],
                   usage=dict(model=LOOK_MODEL, usd=round(usd, 6), candidates=len(cands), search_seconds=t_search, seconds=round(time.monotonic() - t_all, 1),
                              counts=dict(offered=n_before, kept=len(items), designed=sum(1 for it in items if it.get("provider") == "designed"),
                                          named=sum(1 for it in items if it.get("named")), own=len(own))))
        with open(os.path.join(work, "bin.json"), "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[supply] " + json.dumps(dict(
            wishes=len(wishes), items=len(items), checked=n_before, designed=out["usage"]["counts"]["designed"], named={r["named"]: r.get("named_found") for r in report if r.get("named")},
            own=len(own), said=len(said), seconds=out["usage"]["seconds"], usd=out["usage"]["usd"],
            errors=[{k: v for k, v in r.items() if k.endswith("error")} for r in report if any(k.endswith("error") for k in r)]),
            ensure_ascii=False)[:900], flush=True)
        return out
    finally:
        side.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(tmp, ignore_errors=True)
