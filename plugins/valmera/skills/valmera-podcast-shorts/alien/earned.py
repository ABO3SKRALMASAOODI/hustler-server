"""Earned pictures, the hook picture, motion and sound of a podcast short. On the shell, for every film.

    find(ctx, script, llm, worker)      supply stage: the story's real pictures, checked    -> supply/earned/earned.json
    apply(l1, script, earned, idx, ..)  render stage: a plan pass on the Director's baked plan (L1), before the alien passes

What the rater found missing on 13 Nov (lanes/RATINGS/2026-11-13.json) is made here, not by hand per film:

  pictures  Every person, company, place, product and document the script stage names goes down the named route
            (worker/broll_source: Wikimedia Commons by Wikidata 'depicts' first, then Openverse; one vision check
            keeps only pictures that show the subject). A model then says what must stay in frame, and the crop is
            solved from that box and the faces. Nothing found means nothing shown: no lookalike from a stock library.
            A number, a comparison, a quote or a paper he describes becomes a designed panel, drawn in the plan so
            its bars and lines move.
  hook      Frame 0 is the story's subject, full frame, with the hook line set large and sharp on that frame.
  own       His own footage, re-framed closer, cut in on the payoff's first word.
  motion    Pictures settle and drift on one curve; panels land, bars grow and lines type on eased curves; every
            cut to a picture lands on the onset of its word.
  sound     Approved library sounds only, on picture events only, thinned to the film's class (TASTE 4.2), set a
            fixed distance under the measured voice.

Everything reads numbers: word times, the spine, face boxes, loudness. The words on a designed panel come from the
script stage (a model reading his words). Nothing here reads a meaning by rule.
"""
import json
import math
import os
import subprocess

import l1pass as L
import sidecars as S

V = "/Users/masaoodi/Documents/Valmera/.valmera"
PHOTO_MIN = 520                    # a found picture's short side, pixels
CHECK_S, SEARCH_S = 45.0, 14.0
HOOK_S = (1.25, 2.7)               # the hook picture holds until a stressed onset in this window
HOLD_F = (36, 80)                  # a picture's life, frames
TAKE_MIN_F = 20                    # a stretch of the take between two pictures is at least this long, or it is closed
TAIL_F = 42                        # the last frames stay on live footage
PUNCH = 1.13                       # the payoff's closer framing (TASTE 4.2: a step on a take is at most 15%)
FACE_H = 0.205                     # his face's height in the frame after the re-frame (the references sit at 0.2-0.3)
EYES_Y = 0.30                      # and where his eyes ride
UPSCALE = 3.0                      # TASTE 4.2: the frame never asks for more than 3x of the source's pixels
MARK = (0.5594, 0.115, 0.9146, 0.1923)      # the watermark keep-out: no held card enters it
CAP_TOP = 0.585                    # above the caption seat
FACE_FOOT = 0.575                  # a face in a full-frame picture ends above the caption seat
PAPER, INK, GLASS = ("#F4F1EA", "#E7E1D4"), "#15171A", ("#191C22", "#0C0D10")
# sample per role and how far under the voice its hit sits, by supply class (TASTE 4.2: T -9, M -6, P -4.5 dB).
# The owner's rule for a talking short (SKILL.md, Oct 2026): one or two sounds, each on a structural moment with a
# visual partner; no opening whoosh; no bright sound (click, pop, shutter) on a word's onset. So: soft air into a
# landing, one low hit on the payoff's cut, nothing on frame 0.
SOUNDS = dict(hook=None, picture="whoosh_soft_1", design="whoosh_soft_2", payoff="impact_1")
OWNER_MAX = 2
UNDER_DB = dict(T=9.0, M=6.0, P=4.5)
RATE_10S = dict(T=0.6, M=1.5)


# ----------------------------------------------------------------- curves (baked: the plan carries numbers)

def expo(n, a, b):
    n = int(n)
    if n <= 1:
        return [float(b)] * max(n, 0)
    return [a + (b - a) * (1 - 2 ** (-10 * i / (n - 1))) / (1 - 2 ** -10) for i in range(n)]


def ease(n, a, b):
    n = int(n)
    if n <= 1:
        return [float(b)] * max(n, 0)
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(a + (b - a) * t * t * t * (t * (t * 6 - 15) + 10))
    return out


def settle_zoom(n, land=0.085, drift=0.035, tau=6.9):
    """A landed picture's zoom, frame by frame: it arrives a little close and settles on the atlas's curve
    (ATLAS 8.5, tau 6.9 frames), then drifts in slowly for as long as it holds. Never below 1."""
    return [1.0 + land * math.exp(-i / tau) + drift * (i / max(n - 1, 1)) for i in range(int(n))]


def r4(v):
    return [round(float(x), 5) for x in v]


# ----------------------------------------------------------------- supply: the named route

def _worker(worker):
    import sys
    if worker not in sys.path:
        sys.path.insert(0, worker)
    import broll_source as B
    import net_fetch
    return B, net_fetch


def _ask_with(llm, spend, what):
    """The named route's model call on the shell's credited key: (text, usd). Every call is written to the spend."""
    def ask(system, content, purpose, budget_s, max_tokens, model=None, **_):
        # the route may name another model for a call (worker/broll_source._ask); the shell's key is the credited
        # Claude key, so a model the shell has no price for is answered by the route's own Claude model
        import anthropic
        import broll_source as B
        use = model if model in llm.PRICES else B.NAMED_MODEL
        c = anthropic.Anthropic(timeout=float(budget_s) + 20.0, max_retries=1)
        r = c.messages.create(model=use, max_tokens=int(max_tokens), system=system,
                              messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        usd = round(llm.cost(use, r.usage), 6)
        spend.append(dict(stage="supply", provider="anthropic", model=use, what=f"{what}: {purpose}", usd=usd))
        return text, usd
    return ask


def _subject(row, story=""):
    """The subject as the named route reads it. The check also gets what the film is about, so a picture that plainly
    belongs to another country, era or trade than the story (a formulary of another country, a namesake) is dropped
    by the model that looks at it, not by a rule."""
    name = " ".join(str(row.get("name") or row.get("shows") or "").split())[:80]
    qs = [" ".join(str(q).split())[:100] for q in row.get("searches") or [] if str(q).strip()][:3] or [name]
    must = " ".join(str(row.get("must_show") or row.get("shows") or name).split())[:240]
    if story:
        must += (f" It stands in a film about: {story[:260]} Drop a picture that plainly belongs to another country, era or "
                 "trade than that story, unless the subject itself is from there.")
    return dict(name=name, searches=qs, must_show=must)


def _faces_of(img):
    """YuNet on a still: [x, y, w, h] of each face in image fractions, largest first."""
    import cv2
    h, w = img.shape[:2]
    k = 960.0 / max(w, h)
    small = cv2.resize(img, (max(int(w * k), 2), max(int(h * k), 2)), interpolation=cv2.INTER_AREA)
    det = cv2.FaceDetectorYN.create(f"{S.MODELS}/face_detection_yunet_2023mar.onnx", "", (small.shape[1], small.shape[0]), 0.7, 0.3, 20)
    _, fs = det.detect(small)
    rows = [[float(f[0]) / small.shape[1], float(f[1]) / small.shape[0], float(f[2]) / small.shape[1], float(f[3]) / small.shape[0]]
            for f in (fs if fs is not None else [])]
    return sorted(rows, key=lambda r: -r[2] * r[3])


FRAME_SYSTEM = """You frame found pictures for a vertical film. Each numbered picture comes with what it must show. For each, answer two things.

"box": the smallest box that must stay in frame for a viewer to still see that: the person's head and shoulders, the whole object, the sign with its building; for a sheet of print, the printed area without its blank margin. Fractions of the picture's width and height, origin top left.

"print": true when the picture is itself a sheet of print or drawing (a document, a patent, a letter, a page, a poster, an advertisement, a chart), whose words a crop would cut; false for a photograph of people, places or things, even when a sign or a label is somewhere in it.

Answer with one JSON object and nothing else: {"boxes":[{"n":0,"box":[x0,y0,x1,y1],"print":false}]}, one entry per picture."""


def _keep_boxes(B, ask, rows, tmp):
    """{row index: {"box": [x0, y0, x1, y1], "print": bool}} by one vision call: what must stay in frame, and whether
    the picture is a sheet of print. A picture with no answer keeps its centre."""
    import base64
    from PIL import Image
    content, order = [], []
    for r in rows:
        p = os.path.join(tmp, f"box_{r['i']}.jpg")
        with Image.open(r["abs"]) as im:
            im = im.convert("RGB")
            im.thumbnail((512, 512))
            im.save(p, quality=85)
        with open(p, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        content += [{"type": "text", "text": f"picture {len(order)} must show: {r['must_show']}"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}]
        order.append(r["i"])
    if not order:
        return {}
    try:
        text, _ = ask(FRAME_SYSTEM, content, "frame_boxes", 40.0, 1200)
    except Exception:  # noqa: BLE001 no answer: every picture keeps its centre
        return {}
    out = {}
    for o in B._objects(text):
        n, box = o.get("n"), o.get("box")
        if isinstance(n, int) and 0 <= n < len(order) and isinstance(box, list) and len(box) == 4:
            try:
                x0, y0, x1, y1 = [min(max(float(v), 0.0), 1.0) for v in box]
            except (TypeError, ValueError):
                continue
            if x1 - x0 > 0.03 and y1 - y0 > 0.03:
                out[order[n]] = dict(box=[x0, y0, x1, y1], print=o.get("print") is True)
    return out


def solve_frame(w, h, faces, box, frame=(1080, 1920), paper=False):
    """How a found picture sits in the vertical frame: ("full", crop) when what must stay fits the 9:16 window of
    the picture at a usable size, else ("card", crop) with the picture whole on its own blurred self. An object may
    lose a sliver of its edge; a paper (print, a patent, an advertisement) never loses a word.
    crop is [x0, y0, x1, y1] in picture fractions, at the frame's aspect for "full"."""
    W, H = frame
    ar = W / H
    cw, ch = min(1.0, h * ar / w), min(1.0, w / ar / h)
    face = faces[0] if faces else None
    keep = list(box) if box else ([face[0] - 0.6 * face[2], face[1] - 0.6 * face[3], face[0] + 1.6 * face[2], face[1] + 2.2 * face[3]]
                                  if face else [0.5 - cw / 2, 0.0, 0.5 + cw / 2, 1.0])
    keep = [min(max(v, 0.0), 1.0) for v in keep]
    kw = keep[2] - keep[0]
    fits = kw * 0.92 <= cw if paper else (kw * 0.86 <= cw or cw >= 0.70)    # an object may lose a sliver; a paper only its margin
    if fits and cw * w >= 460:                                   # what matters fits the window: full frame
        cx = (keep[0] + keep[2]) / 2
        x0 = min(max(cx - cw / 2, 0.0), 1.0 - cw)
        if face:                                                # the eyes ride high, clear of the hook line and the captions
            cy = face[1] + face[3] * 0.45
            y0 = min(max(cy - 0.30 * ch, 0.0), 1.0 - ch)
            if (face[1] + 1.06 * face[3] - y0) / ch > FACE_FOOT:  # a face that reaches the caption seat cannot fill the frame:
                return "card", [0.0, 0.0, 1.0, 1.0]              # no caption is ever written on it (TASTE P3)
        else:
            y0 = min(max((keep[1] + keep[3]) / 2 - 0.42 * ch, 0.0), 1.0 - ch)
        return "full", [round(x0, 5), round(y0, 5), round(x0 + cw, 5), round(y0 + ch, 5)]
    return "card", [0.0, 0.0, 1.0, 1.0]


def face_frame(w, h, face, frame=(1080, 1920)):
    """The 9:16 window of a picture about its largest face, or None when the face would not fit it or the window is
    too small to fill a phone. For the hook: a wide picture with a face in it (a banknote, a group) opens on the face."""
    W, H = frame
    cw, ch = min(1.0, h * (W / H) / w), min(1.0, w / (W / H) / h)
    if not face or face[2] > 0.8 * cw or W / (cw * w) > 2.6:     # the window may be enlarged 2.6 times, inside the 3x of TASTE 4.2
        return None
    x0 = min(max(face[0] + face[2] / 2 - cw / 2, 0.0), 1.0 - cw)
    y0 = min(max(face[1] + face[3] * 0.45 - 0.30 * ch, 0.0), 1.0 - ch)
    if (face[1] + 1.06 * face[3] - y0) / ch > FACE_FOOT:          # the face would reach the caption seat
        return None
    return [round(x0, 5), round(y0, 5), round(x0 + cw, 5), round(y0 + ch, 5)]


def find(ctx, script, llm, worker):
    """The story's pictures, found and checked. Rows the script stage planned keep their order and index."""
    out_dir = os.path.join(ctx.supply, "earned")
    out_path = os.path.join(out_dir, "earned.json")
    if os.path.exists(out_path):
        return json.load(open(out_path))
    os.makedirs(out_dir, exist_ok=True)
    import cv2
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    B, net_fetch = _worker(worker)
    spend = []
    ask = _ask_with(llm, spend, "named picture")
    B._ask = ask                                                 # the route's own check, paid from the shell's key
    plan = [dict(r, i=i) for i, r in enumerate(script.get("pictures") or []) if isinstance(r, dict)]
    wanted = [r for r in plan if r.get("kind") in ("named", "thing")]

    idea = script.get("idea") or {}
    story = " ".join(f"{script.get('title') or ''}. {idea.get('why') or ''}".split())

    def one(r):
        sub = _subject(r, story)
        try:
            cands, errors = B.search_named(sub, B.KIND_PHOTO, 8, SEARCH_S)
            cands = [c for c in cands if min(int(c.get("width") or 0), int(c.get("height") or 0)) >= PHOTO_MIN]
            kept, checked = [], 0
            for k in range(0, min(len(cands), 2 * B.NAMED_CHECK_MAX), B.NAMED_CHECK_MAX):      # the next sheet only when the first kept none
                got_, report, _ = B.check_named(sub, cands[k:k + B.NAMED_CHECK_MAX], workdir=ctx.tmp, budget_s=CHECK_S)
                kept, checked = kept + got_, checked + len(report)
                if kept:
                    break
        except Exception as e:  # noqa: BLE001 a subject that cannot be searched or checked is not shown
            return r["i"], sub, [], dict(error=str(e)[:200])
        return r["i"], sub, kept, dict(candidates=len(cands), checked=checked, kept=len(kept), errors=errors)

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = list(pool.map(one, wanted))
    rows, used, got = [], set(), {}
    by_i = {r["i"]: r for r in plan}
    for i, sub, kept, rep in found:
        rec = dict(i=i, name=sub["name"], must_show=by_i.get(i, {}).get("must_show") or sub["name"], report=rep, ok=False)
        kept = sorted(kept, key=lambda c: (-int(c.get("_grade") or 0), int(c.get("width") or 0) > int(c.get("height") or 0),
                                           -min(int(c.get("width") or 0), int(c.get("height") or 0))))     # among equals, the upright one fills a phone
        for c in kept:
            if c["id"] in used:
                continue
            path = os.path.join(out_dir, f"{i}.jpg")
            try:
                net_fetch.download(c["_url"], path + ".part", max_bytes=40 * 1024 * 1024, timeout_s=30)
                with Image.open(path + ".part") as im:
                    im = im.convert("RGB")
                    im.thumbnail((2400, 2400))
                    im.save(path, "JPEG", quality=93)
                    w, h = im.size
                os.remove(path + ".part")
            except Exception as e:  # noqa: BLE001 that file did not arrive: the next kept one
                rec["report"]["fetch"] = str(e)[:160]
                continue
            used.add(c["id"])
            rec.update(ok=True, path=f"earned/{i}.jpg", abs=path, w=w, h=h, id=c["id"], shows=c.get("shows"),
                       title=c.get("title"), credit=c.get("credit"), license=c.get("license"), license_url=c.get("license_url"),
                       page_url=c.get("page_url"), license_note=c.get("license_note") or B.license_note(c))
            break
        if not rec["ok"]:
            rec["said"] = (f"No free-licence picture found for {sub['name']} passed the check that it really shows that, "
                           "so none is shown.")
        rows.append(rec)
        got[i] = rec
    boxes = _keep_boxes(B, ask, [r for r in rows if r["ok"]], ctx.tmp)
    for r in rows:
        if not r["ok"]:
            continue
        img = cv2.imread(r["abs"])
        faces = _faces_of(img)
        r["faces"] = [[round(v, 4) for v in f] for f in faces[:3]]
        r["box"] = (boxes.get(r["i"]) or {}).get("box")
        r["paper"] = bool((boxes.get(r["i"]) or {}).get("print")) or str(by_i.get(r["i"], {}).get("is") or "").lower() == "document"
        r["layout"], r["crop"] = solve_frame(r["w"], r["h"], faces, r["box"], paper=r["paper"])
        r.pop("abs", None)
    out = dict(rows=rows, plan=len(plan), wanted=len(wanted), found=sum(1 for r in rows if r["ok"]),
               credits=[dict(i=r["i"], name=r["name"], credit=r.get("credit"), license=r.get("license"), license_url=r.get("license_url"),
                             page_url=r.get("page_url")) for r in rows if r["ok"]],
               said=[r["said"] for r in rows if r.get("said")], spend=spend)
    json.dump(out, open(out_path + ".part", "w"), ensure_ascii=False, indent=1)
    os.replace(out_path + ".part", out_path)
    return out


# ----------------------------------------------------------------- the plan pass

def frame_of(l1, t):
    """A second of the clip as a programme frame, through the spine. None when that second was cut."""
    for e in l1.get("spine") or []:
        if e["src"][0] - 1e-3 <= t <= e["src"][1] + 1e-3:
            return int(round(e["f"][0] + (t - e["src"][0]) * l1.get("fps", 30)))
    return None


def _norm(w):
    return "".join(ch for ch in str(w).lower() if ch.isalnum())


def onset(words, quote, at):
    """Clip second at which the quoted words start: the heard word that opens them, nearest the second the script
    gave. Two transcripts of one voice are being aligned; when the quote is not heard, the nearest word start."""
    toks = [t for t in (_norm(x) for x in str(quote or "").split()) if t]
    best = None
    for i, w in enumerate(words):
        if toks and _norm(w["w"]) == toks[0] and abs(w["t0"] - at) <= 3.0:
            m = sum(1 for k, t in enumerate(toks[:5]) if i + k < len(words) and _norm(words[i + k]["w"]) == t)
            key = (m, -abs(w["t0"] - at))
            if best is None or key > best[0]:
                best = (key, w["t0"])
    if best:
        return best[1]
    return min(words, key=lambda w: abs(w["t0"] - at))["t0"] if words else at


def cut_shot(it, a, b):
    """The part of a shot on frames [a, b): its source seconds and every per-frame list go with it."""
    f0, f1 = it["f"]
    out = dict(it, f=[a, b], id=it["id"] if (a, b) == (f0, f1) else f"{it['id']}~{a}")
    if isinstance(it.get("src"), list):
        t0, t1 = it["src"]
        k = (t1 - t0) / max(f1 - f0, 1)
        out["src"] = [round(t0 + k * (a - f0), 4), round(t0 + k * (b - f0), 4)]
    for key, v in it.items():
        if key in ("f", "src", "id") or not isinstance(v, list):
            continue
        if key == "crop" and v and not isinstance(v[0], list):
            continue
        if len(v) == f1 - f0:
            out[key] = v[a - f0:b - f0]
    return out


def paint(l1, a, b, make):
    """Replace the shots on frames [a, b) with make(a, b) -> shot or list of shots. One shot stays on every frame."""
    keep = []
    for it in l1["items"]:
        if it.get("k") != "shot" or it["f"][1] <= a or it["f"][0] >= b:
            keep.append(it)
            continue
        if it["f"][0] < a:
            keep.append(cut_shot(it, it["f"][0], a))
        if it["f"][1] > b:
            keep.append(cut_shot(it, b, it["f"][1]))
    new = make(a, b)
    l1["items"] = sorted(keep + (new if isinstance(new, list) else [new]), key=lambda it: it["f"][0])


def take_crop(l1):
    """The take's framing: the crop most of the take's frames use."""
    votes = {}
    for it in l1["items"]:
        if it.get("k") == "shot" and it.get("pic") == "main" and isinstance(it.get("crop"), list):
            c = it["crop"][0] if isinstance(it["crop"][0], list) else it["crop"]
            key = tuple(round(float(v), 4) for v in c)
            votes[key] = votes.get(key, 0) + it["f"][1] - it["f"][0]
    return list(max(votes, key=votes.get)) if votes else None


def strip_director(l1):
    """The Director's film is the take, its captions and the name chip. Anything else it placed (library pictures,
    cards, display lines, flashes, sounds) leaves: the engine brings the story's own. Found-picture shots on the
    voice go back to the take, in sync."""
    fps = l1.get("fps", 30)
    units = L.units(l1)
    chips = {key for key, items in units.items() if any(it.get("k") == "card" for it in items) and any(it.get("k") == "word" for it in items)}
    chip_ids = {id(it) for key in chips for it in units[key]}
    gone = dict(cards=0, display=0, flashes=0, sounds=len(l1.get("sound") or []), pictures=0)
    keep = []
    for it in l1["items"]:
        k = it.get("k")
        if k == "card" and id(it) not in chip_ids:
            gone["cards"] += 1
        elif k == "word" and it.get("role") != "cap" and id(it) not in chip_ids:
            gone["display"] += 1
        elif k == "flash" and it.get("mode") != "pool":
            gone["flashes"] += 1
        else:
            keep.append(it)
    l1["items"] = keep
    l1["sound"] = []
    crop = take_crop(l1)
    if crop:
        for it in [it for it in l1["items"] if it.get("k") == "shot" and it.get("pic") != "main"]:
            a, b = it["f"]
            parts = []
            for e in l1.get("spine") or []:
                lo, hi = max(a, e["f"][0]), min(b, e["f"][1])
                if hi > lo:
                    parts.append(dict(id=f"{it['id']}~t{lo}", k="shot", f=[lo, hi], pic="main", crop=list(crop),
                                      src=[round(e["src"][0] + (lo - e["f"][0]) / fps, 4), round(e["src"][0] + (hi - e["f"][0]) / fps, 4)]))
            if parts and sum(p["f"][1] - p["f"][0] for p in parts) == b - a:      # only where his voice runs under it
                l1["items"] = [x for x in l1["items"] if x is not it] + parts
                gone["pictures"] += 1
        l1["items"].sort(key=lambda it: it["f"][0])
    return gone, chips


def stressed_onset(words, lev, lo_s, hi_s, fps=30):
    """The most stressed word start in a window of the clip: the loudest onset, from the sound's own level."""
    cand = [w for w in words if lo_s <= w["t0"] <= hi_s]
    if not cand:
        return None
    def loud(w):
        i = int(w["t0"] * fps)
        seg = lev[i:i + 5]
        return float(seg.mean()) if len(seg) else 0.0
    return max(cand, key=loud)["t0"]


def schedule(rows, n, hook_end, last_ok):
    """Where each picture lives: it lands two frames ahead of its word, holds its time, never leaves a sliver of
    the take between itself and the next, and never runs into the payoff or the tail. rows: [(i, on_f, hold_f)]."""
    out = []
    for i, on, hold in sorted(rows, key=lambda r: r[1]):
        a = max(on - 2, 0)
        if a < hook_end + TAKE_MIN_F:
            continue
        b = min(a + min(max(hold, HOLD_F[0]), HOLD_F[1]), last_ok, n)
        if out:
            pa, pb = out[-1][1], out[-1][2]
            if a < pb:
                if a - pa >= HOLD_F[0] - 6:
                    out[-1][2] = a                              # the earlier picture gives way on the new word
                else:
                    continue
            elif a - pb < TAKE_MIN_F:
                out[-1][2] = a                                  # no sliver of the take between two pictures
        if b - a < 30:
            continue
        out.append([i, a, b])
    return [tuple(r) for r in out]


def _zoomed(crop, z, cx=None, cy=None):
    """A crop zoomed by z about a point of the picture (its own centre when none), kept inside the picture."""
    x0, y0, x1, y1 = crop
    w, h = (x1 - x0) / z, (y1 - y0) / z
    cx = (x0 + x1) / 2 if cx is None else cx
    cy = (y0 + y1) / 2 if cy is None else cy
    rx, ry = (cx - x0) / max(x1 - x0, 1e-6), (cy - y0) / max(y1 - y0, 1e-6)
    nx0 = min(max(cx - rx * w, 0.0), 1.0 - w)
    ny0 = min(max(cy - ry * h, 0.0), 1.0 - h)
    return [round(nx0, 5), round(ny0, 5), round(nx0 + w, 5), round(ny0 + h, 5)]


def bright_under_mark(path, crop):
    """Relative luminance of a picture where the watermark will stand, through its crop: 0..1, or None unread."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            x0, y0, x1, y1 = crop
            box = (int((x0 + MARK[0] * (x1 - x0)) * w), int((y0 + MARK[1] * (y1 - y0)) * h),
                   int((x0 + MARK[2] * (x1 - x0)) * w), int((y0 + MARK[3] * (y1 - y0)) * h))
            px = list(im.crop(box).resize((24, 8)).getdata())
        return (sum(px) / len(px) / 255.0) ** 2.2
    except Exception:  # noqa: BLE001 unread: no shade
        return None


def picture_items(pid, row, a, b, l1, top=0.36, room=None, work=None):
    """A found picture on frames [a, b). Full frame when its subject fits the window: it settles and drifts on one
    curve; where it is bright under the watermark, the top edge falls off so the mark is read. Otherwise whole, as
    a card that lands over the live take. Returns (shot or None, other items)."""
    W, H = l1["frame"]
    n = b - a
    key = f"earned:{row['i']}"
    l1["assets"][key] = dict(kind="image", path=row["path"], w=row["w"], h=row["h"])
    face = row["faces"][0] if row.get("faces") else None
    if row.get("layout") == "full":
        c = row["crop"]
        fx = (face[0] + face[2] / 2) if face else None
        fy = (face[1] + face[3] / 2) if face else None
        crops = [_zoomed(c, z, fx, fy) for z in settle_zoom(n)]
        extra = []
        lum = bright_under_mark(os.path.join(work, row["path"]), c) if work else None
        if lum is not None and lum > 0.35:                         # the whole top edge falls off, as a lens would: no smudge behind the mark
            extra.append(dict(id=f"{pid}.shade", k="flash", f=[a, b], mode="pool", a=round(min(0.15 + 0.25 * lum, 0.38), 3), z=0,
                              rect=[-0.35, -0.2, 1.35, round(MARK[3] + 0.16, 4)]))
        return dict(id=f"{pid}.s", k="shot", f=[a, b], pic=key, crop=crops), extra
    ar = row["w"] / row["h"]                                     # whole, as a card over the live take, under his chin: he stays,
    room = room or (0.725 - top)                                 # the room pass makes it a lamp on him, and the captions sit at its foot
    w = 0.88
    h = w * (W / H) / ar
    crop = [0.0, 0.0, 1.0, 1.0]
    if h > room and ar < 1.0:                                    # an upright picture or paper: as large as its place holds, never trimmed
        w, h = round(room * ar * H / W, 5), room
    elif h > room:                                               # a wide picture taller than its place: trimmed top and bottom, never squeezed
        k = room / h
        crop, h = [0.0, round(0.5 - k / 2, 5), 1.0, round(0.5 + k / 2, 5)], room
    land = min(n, 12)
    y = top + h / 2
    card = dict(id=f"{pid}.card", k="card", f=[a, b], z=2, pic=key, crop=crop, x=0.5, w=round(w, 5), h=round(h, 5),
                y=r4(expo(land, y + 0.02, y) + [y] * (n - land)), s=r4(expo(land, 0.93, 1.0) + [1.0] * (n - land)), rot=0, op=_fade(n, 3, 4),
                radius=0.012, border=dict(w=0.0035, c="#FFFFFF"), shadow=dict(dx=0.0, dy=0.014, blur=0.045, a=0.7))
    return None, [card]


def _wrap(tokens, voices, W, x0, x1, gap):
    """Tokens [(text, voice)] into rows that fit x0..x1: [[(text, voice, x)]]."""
    rows, cur, x = [], [], x0
    for t, v in tokens:
        tw = L.text_w(voices[v], t, W)
        if cur and x + tw > x1:
            rows.append(cur)
            cur, x = [], x0
        cur.append((t, v, x))
        x += tw + gap
    if cur:
        rows.append(cur)
    return rows


def ground_under(path, crop, box):
    """(dark, light): the 15th and 85th percentile of a picture's relative luminance under a box of the frame."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            x0, y0, x1, y1 = crop
            px = sorted(im.crop((int((x0 + box[0] * (x1 - x0)) * w), int((y0 + box[1] * (y1 - y0)) * h),
                                 int((x0 + box[2] * (x1 - x0)) * w), int((y0 + box[3] * (y1 - y0)) * h))).resize((48, 16)).getdata())
        return (px[int(len(px) * 0.15)] / 255.0) ** 2.2, (px[int(len(px) * 0.85)] / 255.0) ** 2.2
    except Exception:  # noqa: BLE001 unread: the accent stays
        return None


def _contrast(ink_lum, grounds):
    return min((max(ink_lum, g) + 0.05) / (min(ink_lum, g) + 0.05) for g in grounds)


def _hits_face(box, faces):
    """TASTE P3 on the plan: a box of type stands on a face when they share more than 15% of either."""
    for f in faces or []:
        ix, iy = min(box[2], f[2]) - max(box[0], f[0]), min(box[3], f[3]) - max(box[1], f[1])
        if ix > 0 and iy > 0 and ix * iy > 0.15 * min((box[2] - box[0]) * (box[3] - box[1]), (f[2] - f[0]) * (f[3] - f[1])):
            return True
    return False


def hook_type(l1, line, accent_words, accent, a, b, bottom=0.556, ground=None, faces=None):
    """The hook line, large, left, two rows at most, standing sharp on frame 0. The words that carry it take the
    serif voice in the film's accent and breathe once; a hairline draws under them. ``ground(box)`` gives the
    picture's dark and light under the line: where the accent would not read there (WCAG 3.0 for display type, after
    the shade), those words are white and only the hairline keeps the colour; where white would not read, the shade
    deepens. ``faces`` are the picture's faces in the frame: the line stands above the caption seat when that is
    clear of them, else under the chin (smaller if it must be, the captions at its foot), else it is not set: a face
    is never written on. Returns (items, the line's foot when the captions must sit under it, else None)."""
    W, H = l1["frame"]
    acc = {_norm(x) for x in str(accent_words or "").split()}
    words = [w for w in str(line or "").split() if w]
    if not words:
        return [], None

    def lay(px):
        l1["voices"]["hk"] = dict(font="InterDisplay-Black.ttf", px=px, track=-2.5, shadow=dict(a=0.55, blur=0.014))
        l1["voices"]["hka"] = dict(font="InstrumentSerif-Italic.ttf", px=int(px * 1.16), track=-1, shadow=dict(a=0.55, blur=0.014))
        toks = [(w, "hka" if _norm(w) in acc else "hk") for w in words]
        return _wrap(toks, l1["voices"], W, 0.07, 0.93, 0.26 * px / W)

    def box_at(rows, px, foot):
        lead = 1.06 * px / H
        right = max(x + L.text_w(l1["voices"][v], t, W) for row in rows for t, v, x in row)
        return [0.07, foot - (len(rows) - 1) * lead - 0.78 * 1.16 * px / H, right, foot + 0.24 * 1.16 * px / H]

    px, rows = 112, None
    for _ in range(6):
        rows = lay(px)
        if len(rows) <= 2:
            break
        px = int(px * 0.9)
    rows = rows[:2]
    floor = None
    if _hits_face(box_at(rows, px, bottom), faces):              # the seat above the captions is on a face: under the chin
        chin = max(f[3] for f in faces)
        placed = False
        for p2 in (px, int(px * 0.88), int(px * 0.78), 84):
            r2 = lay(p2)[:2]
            foot = chin + 0.022 + 0.78 * 1.16 * p2 / H + (len(r2) - 1) * 1.06 * p2 / H
            bx = box_at(r2, p2, foot)
            if bx[3] <= 0.735 and not _hits_face(bx, faces):
                px, rows, bottom, floor, placed = p2, r2, foot, bx[3] + 0.016, True
                break
        if not placed:
            return [], None
    else:
        lay(px)
    lead = 1.06 * px / H
    n = b - a
    out = []
    pool, acc_ink = 0.58, accent
    g = ground([0.07, bottom - len(rows) * lead, 0.93, bottom + 0.02]) if ground else None
    if g:
        shaded = [v * (1.0 - 0.8 * pool) for v in g]
        if _contrast(L._lum(accent), shaded) < 3.0:
            acc_ink = "#FFFFFF"
        if _contrast(1.0, shaded) < 4.5:
            pool = 0.74
    for r, row in enumerate(rows):
        y = bottom - (len(rows) - 1 - r) * lead
        for k, (t, v, x) in enumerate(row):
            it = dict(id=f"hook.{r}.{k}", k="word", f=[a, b], t=t, v=v, x=round(x, 5), y=round(y, 5), ink=acc_ink if v == "hka" else "#FFFFFF",
                      role="big", on=a, z=6, grounded=True)
            if v == "hka" and n > 16:                             # sharp and at rest on frame 0, then one breath
                it["s"] = r4([1.0] * 3 + ease(6, 1.0, 1.07) + ease(7, 1.07, 1.0) + [1.0] * (n - 16))
            out.append(it)
        marks = [(x, L.text_w(l1["voices"][v], t, W)) for t, v, x in row if v == "hka"]
        if marks and n > 16:
            x0, x1 = marks[0][0], marks[-1][0] + marks[-1][1]
            y0 = y + 0.2 * px / H
            grow = expo(12, 0.0, 1.0)
            rect = [[round(x0, 5), round(y0, 5), round(x0 + max((x1 - x0) * g_, 0.002), 5), round(y0 + 0.0042, 5)] for g_ in [0.0] * 2 + grow + [1.0] * (n - 14)]
            out.append(dict(id=f"hook.{r}.rule", k="flash", f=[a, b], mode="fill", c=accent, a=1, rect=rect, z=3))
    out.append(dict(id="hook.pool", k="flash", f=[a, b], mode="pool", a=pool, z=0,           # a wide, soft ground: a shade, not a smudge
                    rect=[0.0, round(bottom - len(rows) * lead - 0.11, 4), 1.0, round(bottom + 0.12, 4)]))
    return out, floor


def _fade(n, inn=5, out=5):
    return r4([min(1.0, (i + 1) / inn, (n - i) / out) for i in range(n)])


def _num(part):
    try:
        return float(part.get("n"))
    except (TypeError, ValueError):
        digits = "".join(ch for ch in str(part.get("value") or "") if ch.isdigit() or ch == ".")
        try:
            return float(digits)
        except ValueError:
            return None


def panel_items(pid, design, a, b, l1, accent, top, height):
    """A designed panel beside the head, drawn in the plan so it moves: the panel lands and settles, then its
    contents arrive on eased curves (bars grow, values rise, a paper's lines type on, the marked line takes a
    highlighter). Every word on it is the script stage's, from his words. The panel is as tall as what it holds
    and stands on the foot of its place. Returns items, or [] when it cannot fit."""
    items, used = _panel(pid, design, a, b, l1, accent, top, height)
    spare = top + height - 0.024 - used
    if items and spare > 0.012:
        items, _ = _panel(pid, design, a, b, l1, accent, top + spare, height - spare)
    return items


def _panel(pid, design, a, b, l1, accent, top, height):
    """(items, the lowest point its contents reach)."""
    W, H = l1["frame"]
    n = b - a
    if n < 30:
        return [], top
    kind = str(design.get("kind") or "")
    paper = kind == "document"
    low = [top]
    x0, x1 = 0.07, 0.93
    y0, y1 = top, top + height
    pad = 0.045
    V = l1["voices"]
    V["dv"] = dict(font="InterDisplay-Black.ttf", px=92, track=-2.5)
    V["dl"] = dict(font="InterDisplay-Bold.ttf", px=31, track=5)
    V["dd"] = dict(font="InstrumentSerif-Regular.ttf", px=58, track=0)
    ink, dim = (INK, "#6B6F76") if paper else ("#FFFFFF", "#9AA1AC")
    land = min(n, 12)
    card = dict(id=f"{pid}.panel", k="card", f=[a, b], z=2, pic="plate",
                plate=dict(c0=(PAPER if paper else GLASS)[0], c1=(PAPER if paper else GLASS)[1], grain=0.03),
                x=0.5, y=r4(expo(land, (y0 + y1) / 2 + 0.018, (y0 + y1) / 2) + [(y0 + y1) / 2] * (n - land)), w=round(x1 - x0, 5), h=round(height, 5),
                s=r4(expo(land, 0.94, 1.0) + [1.0] * (n - land)), rot=0, op=_fade(n, 4, 5), radius=0.02 if not paper else 0.008,
                border=dict(w=0.0018, c="#2B3038" if not paper else "#FFFFFF"), shadow=dict(dx=0.0, dy=0.012, blur=0.04, a=0.6))
    items = [card]
    seq = [0]

    def word(t, v, x, y, col, at):
        """One piece of type that rises 0.014 h into place out of a short blur, from frame a+at; it leaves with the panel."""
        at = min(at, n - 12)
        life = n - at
        k = min(life, 9)
        op = [min(1.0, (i + 1) / 3) for i in range(life)]
        for j in range(min(5, life)):
            op[life - 1 - j] = min(op[life - 1 - j], (j + 1) / 5)
        seq[0] += 1
        low[0] = max(low[0], y + 0.24 * V[v]["px"] / H)
        items.append(dict(id=f"{pid}.{seq[0]}", k="word", f=[a + at, b], t=t, v=v, x=round(x, 5), ink=col, role="big", on=a + at, z=5,
                          y=r4(expo(k, y + 0.014, y) + [y] * (life - k)), blur=r4(expo(k, 7.0, 0.0) + [0.0] * (life - k)), op=r4(op)))

    def row_of(text, v, x, y, col, at, step=2, x_max=None):
        """Words of one line, left to right, each a little after the last. Returns the x after the last word."""
        gap = L.text_w(V[v], "n n", W) - 2 * L.text_w(V[v], "n", W)
        for i, t in enumerate(str(text).split()):
            tw = L.text_w(V[v], t, W)
            if x_max is not None and x + tw > x_max:
                break
            word(t, v, x, y, col, at + i * step)
            x += tw + gap
        return x

    def fits(text, v, width):
        return L.text_w(V[v], str(text), W) <= width

    title = " ".join(str(design.get("title") or "").split())
    parts = [p for p in design.get("parts") or [] if isinstance(p, dict) and str(p.get("value") or "").strip()][:4]
    inner_x0, inner_x1 = x0 + pad, x1 - pad
    y = y0 + 0.012
    if title:
        y += 0.027
        row_of(title.upper(), "dl", inner_x0, y, accent if not paper else "#8A5A00", 5, 1, inner_x1)
    if kind in ("number", "versus", "bars") and parts:
        if not all(any(ch.isdigit() for ch in str(p["value"])) for p in parts):
            return [], top                                       # a chart is made of figures he said: words alone are the captions' work
        vals = [_num(p) for p in parts]
        top_v = max([v for v in vals if v] or [1.0])
        cols = len(parts) if kind == "versus" or len(parts) <= 2 else 1
        if cols > 1:                                             # sides stand next to each other, each bar under its value
            cw = (inner_x1 - inner_x0 - 0.04 * (cols - 1)) / cols
            while V["dv"]["px"] > 54 and not all(fits(p["value"], "dv", cw) for p in parts):
                V["dv"]["px"] = int(V["dv"]["px"] * 0.92)
            vy = y + 0.018 + 0.78 * V["dv"]["px"] / H
            for i, p in enumerate(parts):
                cx = inner_x0 + i * (cw + 0.04)
                at = 7 + i * 7
                big = max(vals, key=lambda v: v or 0) if any(vals) else None
                word(str(p["value"]), "dv", cx, vy, accent if (vals[i] is not None and vals[i] == big and len(parts) > 1) else ink, at)
                by = vy + 0.022
                frac = (vals[i] / top_v) if vals[i] else 1.0
                life = n - at
                g = expo(min(life, 16), 0.0, 1.0) + [1.0] * max(life - 16, 0)
                rect = [[round(cx, 5), round(by, 5), round(cx + max(cw * frac * k, 0.003), 5), round(by + 0.013, 5)] for k in g]
                items.append(dict(id=f"{pid}.bar{i}", k="flash", f=[a + at, b], mode="fill", z=3, rect=rect, a=_fade(life, 2, 5),
                                  c=accent if (vals[i] is not None and vals[i] == big and len(parts) > 1) else "#5C6370"))
                row_of(str(p.get("label") or "").upper(), "dl", cx, by + 0.013 + 0.03, dim, at + 4, 1, cx + cw)
        else:                                                    # amounts stacked: value, label, bar to its share
            rows_h = (y1 - y - 0.02) / len(parts)
            V["dv"]["px"] = int(min(V["dv"]["px"], rows_h * H * 0.52))
            for i, p in enumerate(parts):
                at = 7 + i * 6
                vy = y + i * rows_h + 0.012 + 0.78 * V["dv"]["px"] / H
                xe = L.text_w(V["dv"], str(p["value"]), W) + inner_x0
                word(str(p["value"]), "dv", inner_x0, vy, accent if i == 0 and len(parts) == 1 else ink, at)
                row_of(str(p.get("label") or "").upper(), "dl", xe + 0.03, vy - 0.006, dim, at + 4, 1, inner_x1)
                if len(parts) > 1 and vals[i]:
                    life = n - at
                    g = expo(min(life, 16), 0.0, 1.0) + [1.0] * max(life - 16, 0)
                    by = vy + 0.016
                    rect = [[round(inner_x0, 5), round(by, 5), round(inner_x0 + max((inner_x1 - inner_x0) * vals[i] / top_v * k, 0.003), 5), round(by + 0.008, 5)] for k in g]
                    items.append(dict(id=f"{pid}.bar{i}", k="flash", f=[a + at, b], mode="fill", z=3, rect=rect, a=_fade(life, 2, 5),
                                      c=accent if vals[i] == top_v else "#5C6370"))
    elif kind == "document":                                     # a paper he describes; never his sentence set again as type
        lines = [" ".join(str(x).split()) for x in (design.get("lines") or []) if str(x).strip()][:5]
        v = "dd"
        mark = _norm(design.get("mark"))
        while True:                                              # the lines as large as the paper holds
            gap = L.text_w(V[v], "n n", W) - 2 * L.text_w(V[v], "n", W)
            laid = []                                            # (text, x, row, marked)
            row, x = 0, inner_x0
            for ln in lines:
                marked = bool(mark) and (mark in _norm(ln) or _norm(ln) in mark)
                for t in ln.split():
                    tw = L.text_w(V[v], t, W)
                    if x + tw > inner_x1 and x > inner_x0:
                        row, x = row + 1, inner_x0
                    laid.append((t, x, row, marked))
                    x += tw + gap
                row, x = row + 1, inner_x0
            lead = 1.22 * V[v]["px"] / H
            room = int((y1 - y - 0.03) / lead)
            if row <= room or V[v]["px"] <= 40:
                break
            V[v]["px"] -= 4
        if room < 1:
            return [], top
        laid = [w for w in laid if w[2] < room]
        base = y + 0.012 + 0.82 * V[v]["px"] / H
        for i, (t, x, r, marked) in enumerate(laid):
            word(t, v, x, base + r * lead, ink, 6 + i)
        for r in sorted({w[2] for w in laid if w[3]}):           # the highlighter crosses the marked line after it is typed
            ws = [w for w in laid if w[2] == r and w[3]]
            xa = ws[0][1] - 0.006
            xb = ws[-1][1] + L.text_w(V[v], ws[-1][0], W) + 0.006
            at = min(6 + max(i for i, w in enumerate(laid) if w[2] == r and w[3]) + 3, n - 14)
            life = n - at
            g = expo(min(life, 12), 0.0, 1.0) + [1.0] * max(life - 12, 0)
            yb = base + r * lead
            rect = [[round(xa, 5), round(yb - 0.74 * V[v]["px"] / H, 5), round(xa + max((xb - xa) * k, 0.003), 5), round(yb + 0.2 * V[v]["px"] / H, 5)] for k in g]
            items.append(dict(id=f"{pid}.mark{r}", k="flash", f=[a + at, b], mode="fill", z=3, rect=rect, c=accent,
                              a=r4([0.42 * v_ for v_ in _fade(life, 2, 5)])))
    else:
        return [], top
    for it in items:
        if it.get("k") == "flash" and isinstance(it.get("rect"), list) and it["rect"] and isinstance(it["rect"][0], list):
            low[0] = max(low[0], it["rect"][-1][3])
    return (items, low[0]) if len(items) > 1 else ([], top)


def ride_captions(l1, card, moved):
    """Captions that would stand on a panel take the panel's top edge as their seat while it is there (TASTE P1)."""
    cb = L.card_box(card)
    for key, ws in L.units(l1).items():
        if any(it.get("k") != "word" or it.get("role") != "cap" for it in ws):
            continue
        a, b = min(w["f"][0] for w in ws), max(w["f"][1] for w in ws)
        both = min(b, card["f"][1]) - max(a, card["f"][0])
        box = L.unit_box(ws, l1)
        if both * 2 < b - a or box[3] <= cb[1] - 0.004 or box[1] >= cb[3]:
            continue
        dy = cb[1] - L.EDGE - box[3]
        for w in ws:
            w["y"] = L._shift(w["y"], dy)
        moved.append(key)


def ride_under(l1, card, moved):
    """Captions that would stand on a found picture's card take its foot as their seat while it is there: no type on
    its print, and his mouth above it stays clear (TASTE P1, P6)."""
    cb = L.card_box(card)
    for key, ws in L.units(l1).items():
        if any(it.get("k") != "word" or it.get("role") != "cap" for it in ws):
            continue
        a, b = min(w["f"][0] for w in ws), max(w["f"][1] for w in ws)
        both = min(b, card["f"][1]) - max(a, card["f"][0])
        box = L.unit_box(ws, l1)
        if both * 2 < b - a or box[3] <= cb[1] - 0.004 or box[1] >= cb[3] + 0.004:      # it lives with the card, not past its edge
            continue
        dy = cb[3] + 0.016 - box[1]
        for w in ws:
            w["y"] = L._shift(w["y"], dy)
        moved.append(key)


def voice_lufs(media):
    """Integrated loudness of the clip's own sound (its voice), LUFS."""
    r = subprocess.run([S.FF, "-nostdin", "-v", "info", "-i", media, "-vn", "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    val = None
    for ln in r.stderr.splitlines():
        ln = ln.strip()
        if ln.startswith("I:") and "LUFS" in ln:
            try:
                val = float(ln.split()[1])
            except (IndexError, ValueError):
                pass
    return val if val is not None else -20.0


def place_sounds(l1, events, manifest, voice, np10, table=None):
    """Sounds on picture events only, thinned to the class's allowance and never more than the owner's two: the
    payoff's cut first, then a designed landing, then the first picture; one sample never inside 3 s of itself;
    each hit a fixed distance under the measured voice. events: [(frame, role)]."""
    fps = l1.get("fps", 30)
    n = int(l1["dur_f"])
    table = dict(SOUNDS, **(table or {}))
    lib = {s["id"]: s for s in manifest}
    cls = "T" if np10 < 1 else "M" if np10 < 5 else "P"
    rate = RATE_10S.get(cls) or min(0.9 * np10, 6.0)
    allow = min(OWNER_MAX, max(1, int(rate * n / fps / 10.0)))
    events = [e for e in events if table.get(e[1])]
    first = [e for e in events if e[1] == "payoff"][:allow]
    rest = sorted((e for e in events if e[1] != "payoff"), key=lambda e: (e[1] != "design", e[0]))   # a designed landing first, then the first picture
    rest = rest[:max(allow - len(first), 0)]
    chosen, last, count = [], {}, {}
    for f, role in sorted(first + rest):
        s = lib.get(table.get(role) or "")
        if s is None:
            continue
        if s["id"] in last and (f - last[s["id"]] < 3 * fps or count.get(s["id"], 0) >= max(2, int(2 * n / fps / 30))):
            continue
        last[s["id"]], count[s["id"]] = f, count.get(s["id"], 0) + 1
        db = max(min((voice - UNDER_DB[cls]) - float(s.get("hit_lufs") or -16.0), 0.0), -30.0)
        start = f / fps - float(s.get("peak_s") or 0.0)
        chosen.append(dict(id=f"earned.snd{len(chosen)}", s=s["id"], file=s["file"], event_f=int(f), start_s=round(max(start, 0.0), 4),
                           trim_s=round(max(-start, 0.0), 4), db=round(db, 1), peak_s=s.get("peak_s"), role=role))
    l1["sound"] = chosen
    return dict(cls=cls, np10=round(np10, 2), allowed=allow, placed=len(chosen), voice_lufs=round(voice, 1), under_db=UNDER_DB[cls],
                events=[dict(f=s["event_f"], s=s["s"], role=s["role"], db=s["db"]) for s in chosen])


def take_face(source, t, crop):
    """The speaker's face at a second of the clip: its centre in source fractions (x, y of the eyes), or None."""
    import cv2
    import numpy as np
    p = S.probe(source)
    w = 960
    h = int(round(w * p["h"] / p["w"] / 2) * 2)
    r = subprocess.run([S.FF, "-nostdin", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", source, "-frames:v", "1", "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
    if len(r.stdout) < w * h * 3:
        return None
    img = np.frombuffer(r.stdout[:w * h * 3], np.uint8).reshape(h, w, 3)
    x0, y0, x1, y1 = crop
    inside = [f for f in _faces_of(img) if x0 <= f[0] + f[2] / 2 <= x1 and y0 <= f[1] + f[3] / 2 <= y1]
    if not inside:
        return None
    f = inside[0]
    return f[0] + f[2] / 2, f[1] + f[3] * 0.42, f


def reframe_take(l1, source, crop, step=PUNCH):
    """His own footage, re-framed: every stretch of the take in the Director's framing moves closer, so his face is
    about a fifth of the frame's height with the eyes at 0.30, and whatever sat under him leaves the frame. Faces
    are sampled across the take; the zoom stops where he would lean out of the frame, or where the frame (with the
    payoff's step still to come) would need more than 3x of the source's pixels. Returns (crop, record)."""
    import statistics
    W = l1["frame"][0]
    fps = l1.get("fps", 30)
    key = tuple(round(float(v), 4) for v in crop)
    shots = [it for it in l1["items"] if it.get("k") == "shot" and it.get("pic") == "main" and isinstance(it.get("crop"), list)
             and not isinstance(it["crop"][0], list) and tuple(round(float(v), 4) for v in it["crop"]) == key and isinstance(it.get("src"), list)]
    if not shots:
        return crop, dict(zoom=1.0, why="no stretch of the take in one framing")
    total = sum(it["f"][1] - it["f"][0] for it in shots)
    times, acc = [], 0
    for it in shots:                                             # eight moments, spread over the take by its length
        n = it["f"][1] - it["f"][0]
        for k in range(8):
            f = (k + 0.5) * total / 8 - acc
            if 0 <= f < n:
                times.append(it["src"][0] + (it["src"][1] - it["src"][0]) * f / n)
        acc += n
    faces = [f for f in (take_face(source, t, crop) for t in times) if f]
    if len(faces) < 3:
        return crop, dict(zoom=1.0, why="his face was not found often enough to move closer", samples=len(faces))
    fx, ey = statistics.median(f[0] for f in faces), statistics.median(f[1] for f in faces)
    fw, fh = statistics.median(f[2][2] for f in faces), statistics.median(f[2][3] for f in faces)
    spread = max(abs(f[0] - fx) for f in faces)
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    src_w = (l1["assets"].get("main") or {}).get("w") or 1920
    z = min(FACE_H / max(fh / ch, 1e-4), UPSCALE / step * (cw * src_w) / W, cw / (2 * (spread + 1.1 * fw)), 1.7)
    if z < 1.06:
        return crop, dict(zoom=1.0, why="already close", face_h=round(fh / ch, 3))
    w, h = cw / z, ch / z
    x0 = min(max(fx - w / 2, 0.0), 1.0 - w)
    y0 = min(max(ey - EYES_Y * h, 0.0), 1.0 - h)
    new = [round(x0, 5), round(y0, 5), round(x0 + w, 5), round(y0 + h, 5)]
    for it in shots:
        it["crop"] = list(new)
    return new, dict(zoom=round(z, 3), face_h=[round(fh / ch, 3), round(fh / h, 3)], eyes_y=round((ey - y0) / h, 3), samples=len(faces),
                     upscale=round(W / (w * src_w), 2), crop=new)


def apply(l1, script, earned, idx, source, sounds_manifest, own=True, panel_low=False, work=None):
    """The plan pass. Returns what it did, in numbers, for film.json."""
    import numpy as np
    fps = l1.get("fps", 30)
    n = int(l1["dur_f"])
    W, H = l1["frame"]
    words = idx.get("words") or []
    win0 = float(script["window"][0])
    accent = script.get("accent") or "#FFC857"
    gone, chips = strip_director(l1)
    crop = take_crop(l1)
    framed = None
    if crop:
        crop, framed = reframe_take(l1, source, crop, PUNCH if own else 1.0)
    lev = S.level(source, int(idx["video"]["duration"] * fps) + 1)
    got = {r["i"]: dict(r) for r in earned.get("rows") or [] if r.get("ok")}
    for r in got.values():                                       # the frame is solved here, from what supply measured
        r["layout"], r["crop"] = solve_frame(r["w"], r["h"], r.get("faces") or [], r.get("box"), tuple(l1["frame"]), bool(r.get("paper")))
    plan = [dict(r, i=i) for i, r in enumerate(script.get("pictures") or []) if isinstance(r, dict)]

    def land(row):
        try:
            at = float(row.get("at")) - win0
        except (TypeError, ValueError):
            at = 0.0
        return frame_of(l1, onset(words, row.get("on"), at))

    # the hook: frame 0 to a stressed word
    hook = script.get("hook") or {}
    hi = hook.get("picture") if isinstance(hook.get("picture"), int) else None
    if hi in got and got[hi]["layout"] != "full":                # the script's hook is a wide picture: it opens on its face, when it has one
        c = face_frame(got[hi]["w"], got[hi]["h"], (got[hi].get("faces") or [None])[0], tuple(l1["frame"]))
        if c:
            got[hi] = dict(got[hi], layout="full", crop=c)
    full = [got[i] for i in sorted(got) if got[i].get("layout") == "full"]      # frame 0 is a picture that fills the phone
    hrow = got[hi] if hi in got and got[hi]["layout"] == "full" else (full[0] if full else None)
    spine0 = (l1.get("spine") or [dict(f=[0, n], src=[0.0, n / fps])])[0]
    t_end = stressed_onset(words, lev, spine0["src"][0] + HOOK_S[0], spine0["src"][0] + HOOK_S[1], fps)
    hook_end = frame_of(l1, t_end) if t_end is not None else None
    hook_end = int(min(max(hook_end if hook_end is not None else 54, 36), min(84, n // 3)))
    rec = dict(stripped=gone, framed=framed, hook=None, pictures=[], designs=[], own=None, skipped=[])
    items, hook_moved = [], []
    if hrow:
        shot, cards = picture_items("hook", hrow, 0, hook_end, l1, work=work)
        paint(l1, 0, hook_end, lambda a, b: shot)
        items += cards
        rec["hook"] = dict(picture=hrow["i"], name=hrow["name"], layout=hrow["layout"], frames=[0, hook_end], line=hook.get("line"))
    ground = None
    if hrow and hrow.get("layout") == "full" and work:
        ground = lambda box: ground_under(os.path.join(work, hrow["path"]), hrow["crop"], box)      # noqa: E731
    hfaces = None
    if hrow and hrow.get("layout") == "full":                     # the picture's faces in the frame, a little larger: it lands close and settles
        x0, y0, x1, y1 = hrow["crop"]
        hfaces = [[(f[0] - 0.09 * f[2] - x0) / (x1 - x0), (f[1] - 0.09 * f[3] - y0) / (y1 - y0),
                   (f[0] + 1.09 * f[2] - x0) / (x1 - x0), (f[1] + 1.09 * f[3] - y0) / (y1 - y0)] for f in hrow.get("faces") or []]
    htype, floor = hook_type(l1, hook.get("line") or script.get("hook_line"), hook.get("accent"), accent, 0, hook_end, ground=ground, faces=hfaces)
    items += htype
    rec_hook_line = bool(htype)
    if floor is not None:                                        # the line stands under the chin: the hook's captions sit at its foot
        L.split_captions_at(l1, [hook_end])
        ride_under(l1, dict(f=[0, hook_end], x=0.5, y=floor / 2 - 0.008, w=1.0, h=floor - 0.016, s=1), hook_moved)
    # the payoff: no picture after it, and his own footage closer
    pay = script.get("payoff") or {}
    pay_f = None
    if pay.get("on"):
        try:
            pay_f = frame_of(l1, onset(words, pay.get("on"), float(pay.get("at")) - win0))
        except (TypeError, ValueError):
            pay_f = None
    if pay_f is not None and not (hook_end + 60 <= pay_f <= n - 24):
        pay_f = None
    last_ok = min(n - TAIL_F, (pay_f - 6) if pay_f is not None else n)
    rows = []
    for r in plan:
        if hrow and r["i"] == hrow["i"]:
            continue
        if r.get("kind") == "design" and not panel_items("probe", r.get("design") or {}, 0, 90, dict(l1, voices=dict(l1["voices"])), accent, 0.55, 0.235):
            rec["skipped"].append(dict(i=r["i"], why="not a design of figures or a paper"))
            continue
        if r.get("kind") == "design" or r["i"] in got:
            f = land(r)
            if f is None:
                rec["skipped"].append(dict(i=r["i"], why="its words were cut"))
                continue
            try:
                hold = int(round(float(r.get("hold") or 1.8) * fps))
            except (TypeError, ValueError):
                hold = 54
            rows.append((r["i"], f, hold + (14 if r.get("kind") == "design" else 0)))
        else:
            rec["skipped"].append(dict(i=r["i"], why="no picture passed the check"))
    slots = schedule(rows, n, hook_end, last_ok)
    placed = {i for i, _, _ in slots}
    rec["skipped"] += [dict(i=i, why="no room on the timeline") for i, _, _ in rows if i not in placed]
    face = take_face(source, (spine0["src"][0] + spine0["src"][1]) / 2, crop) if crop else None
    chin = None
    if face and crop:
        f = face[2]
        chin = (f[1] + f[3] - crop[1]) / (crop[3] - crop[1])
    card_top = min(max(0.30, (chin or 0.0) + 0.035), 0.46)
    events = [(3, "hook")] if hrow else []
    by_i = {r["i"]: r for r in plan}
    moved, n_design = [], 0
    for i, a, b in slots:
        r = by_i[i]
        if r.get("kind") == "design":
            if n_design >= 2:
                rec["skipped"].append(dict(i=i, why="two designs a film"))
                continue
            top = max(0.30, (chin or 0.0) + 0.035)
            height = min(0.235, CAP_TOP - 0.012 - top)
            if panel_low or height < 0.17:
                top, height = 0.552, 0.235
            its = panel_items(f"e{i}", r.get("design") or {}, a, b, l1, accent, top, height)
            if not its:
                rec["skipped"].append(dict(i=i, why="the design did not fit its panel"))
                continue
            items += its
            n_design += 1
            if top > 0.5:
                L.split_captions_at(l1, [a, b])
                ride_captions(l1, its[0], moved)
            events.append((a + 2, "design"))
            rec["designs"].append(dict(i=i, kind=(r.get("design") or {}).get("kind"), frames=[a, b], on=r.get("on")))
        else:
            row = got[i]
            shot, cards = picture_items(f"e{i}", row, a, b, l1, top=card_top, work=work)
            if shot is not None:
                paint(l1, a, b, lambda a_, b_, shot=shot: shot)
            for c in cards:
                if c.get("k") == "card":                           # only the captions that live with the card move, and only while it stands
                    L.split_captions_at(l1, [c["f"][0], c["f"][1]])
                    ride_under(l1, c, moved)
            items += cards
            events.append((a, "picture"))
            rec["pictures"].append(dict(i=i, name=row["name"], shows=row.get("shows"), layout=row["layout"], frames=[a, b], on=r.get("on"),
                                        licence=row.get("license"), credit=row.get("credit")))
    # his own footage, re-framed on the payoff
    if own and pay_f is not None and crop:
        t_pay = None
        for e in l1.get("spine") or []:
            if e["f"][0] <= pay_f < e["f"][1]:
                t_pay = e["src"][0] + (pay_f - e["f"][0]) / fps
        pf = take_face(source, t_pay, crop) if t_pay is not None else None
        close = _zoomed(crop, PUNCH, pf[0] if pf else None, pf[1] if pf else crop[1] + 0.3 * (crop[3] - crop[1]))
        a0 = max(pay_f - 1, 0)
        for it in [it for it in l1["items"] if it.get("k") == "shot" and it.get("pic") == "main" and it["f"][1] > a0]:
            lo = max(it["f"][0], a0)
            part = cut_shot(it, lo, it["f"][1])
            part["crop"] = list(close)
            part["id"] = f"{it['id']}~own{lo}"
            paint(l1, lo, it["f"][1], lambda a_, b_, part=part: part)
        events.append((a0, "payoff"))
        rec["own"] = dict(frame=a0, zoom=PUNCH, on=pay.get("on"), face=bool(pf))
    # a chip never names a found picture: it waits for the take
    busy = np.zeros(n + 1, bool)
    for a, b in L.picture_shots(l1):
        busy[a:b] = True
    for it in items:
        if it.get("k") == "card":
            busy[it["f"][0]:it["f"][1]] = True
    for key in chips:
        its = L.units(l1).get(key) or []
        if not its:
            continue
        a, b = min(it["f"][0] for it in its), max(it["f"][1] for it in its)
        if not busy[a:b].any():
            continue
        t = next((f for f in range(a, n - (b - a)) if not busy[f:f + (b - a)].any()), None)
        if t is None:
            gone_ids = {id(it) for it in its}
            l1["items"] = [it for it in l1["items"] if id(it) not in gone_ids]
            continue
        for it in its:
            d = t - a
            it["f"] = [it["f"][0] + d, it["f"][1] + d]
            if "on" in it:
                it["on"] += d
    l1["items"] = sorted(l1["items"] + items, key=lambda it: it["f"][0])
    pics = len(L.picture_shots(l1)) + len([c for c in L.cards(l1)])
    np10 = pics / max(n / fps / 10.0, 1e-6)
    rec["sound"] = place_sounds(l1, events, sounds_manifest, voice_lufs(source), np10, script.get("sounds"))
    if rec["hook"]:
        rec["hook"]["line_set"] = rec_hook_line
    rec.update(new_pictures=pics, np10=round(np10, 2), hook_frames=hook_end, captions_moved=moved + hook_moved,
               found=len(got), planned=len(plan), credits=earned.get("credits"), said=earned.get("said"))
    rec["ok"] = bool(rec["hook"] and len(rec["pictures"]) + len(rec["designs"]) >= 3)
    return rec
