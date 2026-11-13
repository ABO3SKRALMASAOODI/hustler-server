"""The film's look, solved on its own footage (week-1112, lane K round four).

A look here is a decision anyone can see beside the upload, and it is numbers end to end. The Director names
one (`dna.look`), which fixes the design: the hue that owns the shadows, how much colour stays, how hard the
tone is. The style card it named gives the tone the reference films measure (CIELAB percentiles and chroma,
`assets/colour.json`, read off the films themselves). This module reads the upload's own frames and solves the
strengths: exposure a third of the way to the card's median as far as skin allows, contrast half way to its
spread, saturation a quarter of the way to its chroma, then the shadow hue as far as it takes for the film to stand a measured distance
off its upload (CIE delta E 2000, mean over what is neither skin nor black), and never past what skin can take.

    look = grade.solve(grade.frames(work), "cream_teal", "S9")

It lowers to what today's renderer draws on every source alike, the take, a moment of it cut in and a found
picture: one grade window of exposure, contrast and saturation (worker/gradewin.py, one table a plane), and the
shadow hue as `effects.grade_custom` temperature and tint, which ffmpeg's colorbalance lays on the shadows and
the low mids only (nothing above a code value of 0.4 moves, measured), so lit skin keeps the colour it came
with and the highlights hold. A keyed tone window was tried first and dropped: the renderer grades a cutaway
with plain windows only, so a film that cuts its own take in came out in two colours. `apply` is the numpy twin
of the chain, table for table, so what the meter prints is what the render makes. No model is asked and no
meaning is read: frames in, numbers out, the same frames give the same look.
"""
import json
import os
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
VERSION = 5                # moves when a design or the solver changes: a kept look is solved again
N_FRAMES, FRAME_W = 12, 96
SOLVE_PX = 6000            # pixels the solver reads: percentiles and means settle long before this

DE_MIN, DE_MAX = 3.0, 20.0
SKIN_DE_MAX = 6.0          # skin moves with the exposure and the contrast only, and never past this
CLIP_MAX = 0.03            # share of the picture a grade may push onto pure black or white beyond what the upload holds
PULL = 0.5                 # how far the spread goes toward the card's numbers
PULL_C = 0.25              # and the chroma: the cards are drained films, and half way took the gold out of a firework (p5571)
PULL_L = 0.35              # and the median: a card's is its pages and plates too, so the take goes a third of the way
EV_MAX = 0.25              # stops of exposure, at most
SKIN_DE_EV = 4.5           # what exposure alone may do to skin
SAT = (0.7, 1.15)          # the chroma a look may leave or add: skin loses what the frame loses, so never under 0.7

# The designs. `sh` is the hue the shadows take, `sat` a bias on the chroma the card asks for, `ev` and `con`
# biases on its tone, `de` how far off the upload the film stands. The solver doses the hue between AMOUNT's ends.
# `hi` and `temp` are the keyed tone: the hue the highlights hold and the white balance of everything that is not
# skin. It is one more grade window with the renderer's skin key, drawn on the take only, so the lowering leaves
# it out of a film that cuts its own take in as a picture (score.lower): that film is one colour or it is two.
LOOKS = {
    "cream_teal": {"sh": "#0A4656", "hi": "#F4E4C4", "temp": 0.1, "sat": 0.94, "ev": 0.0, "con": 1.08, "de": 9.0,
                   "is": "film print: teal owns the shadows, skin and highlights stay warm. People, streets, interiors, a story"},
    "warm_low": {"sh": "#5A2E08", "hi": "#FCDDB0", "temp": 0.2, "sat": 0.96, "ev": -0.10, "con": 1.08, "de": 8.5,
                 "is": "warm low key: amber in the shadows, a little darker, a lamp in the room. Talk at a desk, evening, craft, food"},
    "cold_doc": {"sh": "#0E2C56", "hi": "#E4EEF6", "temp": -0.15, "sat": 0.86, "ev": 0.0, "con": 1.10, "de": 8.5,
                 "is": "cold documentary: steel blue shadows, colour held back. News, tech, a claim, a city"},
    "bleach": {"sh": "#1C3038", "hi": "#F2EEE4", "temp": 0.0, "sat": 0.70, "ev": 0.0, "con": 1.22, "de": 9.0,
               "is": "bleach bypass: hard contrast, colour drained to two thirds. Sport, grit, work, an argument"},
    "teal_bleach": {"sh": "#064450", "hi": "#ECEEE4", "temp": -0.05, "sat": 0.78, "ev": 0.0, "con": 1.16, "de": 9.5,
                    "is": "teal bleach: drained and contrasty, teal in everything dark. A podcast, a confession, a hard story"},
    "night": {"sh": "#0A1C5C", "hi": "#CFE4F6", "temp": -0.12, "sat": 0.90, "ev": -0.15, "con": 1.14, "de": 9.0,
              "is": "night: darker, blue in the blacks. Footage shot after dark, fire, screens, a city at night"},
    "print": {"sh": "#3C3410", "hi": "#F6E9CE", "temp": 0.1, "sat": 0.90, "ev": 0.03, "con": 1.05, "de": 7.5, "most": 0.75,
              "is": "paper print: soft, olive in the shadows, colour a touch faded. Archive, memory, a quiet routine"},
    "golden": {"sh": "#4A3204", "hi": "#FFD488", "temp": 0.15, "sat": 1.10, "ev": 0.05, "con": 1.08, "de": 8.5,
               "is": "golden: richer colour, gold-brown shadows. Outdoors, travel, lifestyle, a product in the sun"},
    "electric": {"sh": "#28106A", "hi": "#D6F2FF", "temp": -0.06, "sat": 1.14, "ev": 0.0, "con": 1.12, "de": 9.0,
                 "is": "electric: indigo blacks, saturated colour. A game, neon, fireworks, a party"},
    "clean": {"sh": "#16283A", "sat": 1.04, "ev": 0.03, "con": 1.10, "de": 6.0, "most": 0.4,
              "is": "clean commercial: deeper blacks a touch cool, colours true. A screen, a slide, a product whose colours are the brand"},
}
DEFAULT = "cream_teal"
AMOUNT = (0.3, 1.0)        # the hue's dose, least and most: at 1 temperature or tint is at the end of its range


def _cards():
    try:
        with open(os.path.join(HERE, "assets", "colour.json"), encoding="utf-8") as f:
            return json.load(f).get("cards") or {}
    except (OSError, ValueError):
        return {}


CARDS = _cards()


# ------------------------------------------------------------------ frames

def keep(work, frames_bgr):
    """The upload's colour sample, kept from the frames perception already decoded: a dozen, small, RGB."""
    try:
        import cv2
        pick = frames_bgr[:: max(1, len(frames_bgr) // N_FRAMES)][:N_FRAMES]
        h = max(2, int(round(pick[0].shape[0] * FRAME_W / pick[0].shape[1])))
        arr = np.stack([cv2.cvtColor(cv2.resize(f, (FRAME_W, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB) for f in pick])
        np.save(os.path.join(work, "colour.npy"), arr)
        return arr
    except Exception:  # noqa: BLE001 - the look falls back to reading the file itself
        return None


def frames(work, src=None):
    """(n, h, w, 3) uint8 RGB of the upload, or None when there is nothing to read (a compile with no media)."""
    if not work:
        return None
    p = os.path.join(work, "colour.npy")
    src = src or os.path.join(work, "source.mp4")
    try:
        if os.path.exists(p) and (not os.path.exists(src) or os.path.getmtime(p) >= os.path.getmtime(src)):
            return np.load(p)
    except (OSError, ValueError):
        pass
    if not os.path.exists(src):
        return None
    try:
        idx = json.load(open(os.path.join(work, "index.json"))).get("video") or {}
        dur, W, H = float(idx.get("duration") or 0), int(idx.get("width") or 0), int(idx.get("height") or 0)
        if dur <= 0 or W <= 0 or H <= 0:
            return None
        h = max(2, int(round(H * FRAME_W / W)) // 2 * 2)
        r = subprocess.run([FF, "-nostdin", "-v", "error", "-threads", "1", "-i", src, "-vf", f"fps={N_FRAMES / dur:.5f},scale={FRAME_W}:{h}:flags=area",
                            "-frames:v", str(N_FRAMES), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120)
        n = len(r.stdout) // (FRAME_W * h * 3)
        if not n:
            return None
        arr = np.frombuffer(r.stdout[: n * FRAME_W * h * 3], np.uint8).reshape(n, h, FRAME_W, 3)
        np.save(p, arr)
        return arr
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def pixels(arr):
    """Frames to (n, 3) floats 0..1, the bars a letterboxed upload carries left out."""
    g = arr.astype(np.float32).mean(axis=3)
    rows = (g.mean(axis=(0, 2)) > 10) | (g.std(axis=(0, 2)) > 4)
    cols = (g.mean(axis=(0, 1)) > 10) | (g.std(axis=(0, 1)) > 4)
    if rows.sum() < 4 or cols.sum() < 4:
        rows[:], cols[:] = True, True
    return arr[:, rows][:, :, cols].reshape(-1, 3).astype(np.float64) / 255.0


# ------------------------------------------------------------------ the renderer's grade, in numpy

def balance_rgb(t, m):
    """What renderer.grade_custom_chain hands colorbalance for a temperature and a tint: (rs, gs, bs), mids at 0.6."""
    return 0.10 * t + 0.03 * m, -0.08 * m, -0.12 * t + 0.05 * m


def _sm(lo, hi, v):
    t = np.clip((v - lo) / max(1e-6, hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def apply(px, look, keyed=True):
    """`px` (n, 3) sRGB 0..1 through the chain as the render draws it: the shadow hue (colorbalance on shadows and
    low mids, ffmpeg's own weights on max + min), the window's tables, then the keyed tone when the film carries
    one and `keyed` says this source takes it (the take does, a cutaway does not). Returns (graded, skin weight)."""
    import gradewin
    import lutlib
    base, bal = look.get("base") or {}, look.get("balance") or {}
    out = np.asarray(px, np.float64)
    if bal:
        l = out.max(axis=1) + out.min(axis=1)
        ws = np.clip((0.333 - l) * 4 + 0.5, 0, 1) * 0.7
        wm = np.clip((l - 0.333) * 4 + 0.5, 0, 1) * np.clip((1 - l - 0.333) * 4 + 0.5, 0, 1) * 0.7
        off = np.array(balance_rgb(float(bal.get("temperature") or 0.0), float(bal.get("tint") or 0.0)))
        out = np.clip(out + (ws + 0.6 * wm)[:, None] * off[None, :], 0.0, 1.0)
    ycc = lutlib.rgb_to_ycc8(out)
    y = np.clip(np.rint(ycc[:, 0]), 0, 255).astype(np.int64)
    yt = np.asarray(gradewin.y_table(base.get("ev"), base.get("contrast")), np.float64)[y]
    s = 1.0 if base.get("saturation") is None else float(base["saturation"])
    cb = np.clip(128 + (ycc[:, 1] - 128) * s, 16, 240)
    cr = np.clip(128 + (ycc[:, 2] - 128) * s, 16, 240)
    key = look.get("key") if keyed else None
    if key:            # gradewin.grade_graph: offsets by the graded luma, skin keyed on what the window was handed
        a_u, a_v, s_u, s_v, h_u, h_v, cross = gradewin._tone_offsets(key)
        lum = np.clip((yt - 16) / 219.0, 0.0, 1.0)
        sh = (1 - _sm(cross - 0.3, cross + 0.05, lum)) * _sm(0.0, 0.12, lum)
        hi = _sm(cross - 0.05, cross + 0.3, lum)
        w = lutlib.skin_weight(cb, cr, yt)
        cb2 = np.clip(cb + a_u * lum ** 0.6 + s_u * sh + h_u * hi, 16, 240)
        cr2 = np.clip(cr + a_v * lum ** 0.6 + s_v * sh + h_v * hi, 16, 240)
        cb, cr = cb2 * (1 - w) + cb * w, cr2 * (1 - w) + cr * w
    src = lutlib.rgb_to_ycc8(np.asarray(px, np.float64))
    return lutlib.ycc8_to_rgb(np.stack([yt, cb, cr], axis=-1)), lutlib.skin_weight(src[:, 1], src[:, 2], src[:, 0])


def numbers(px, lab=None):
    """What a film's colour measures: L* at the 5th, 50th and 95th percentile, mean chroma, and where its darks lean."""
    import lutlib
    lab = lutlib.rgb_to_lab(px) if lab is None else lab
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    dark = L < 30
    return {"L5": round(float(np.percentile(L, 5)), 1), "L50": round(float(np.percentile(L, 50)), 1), "L95": round(float(np.percentile(L, 95)), 1),
            "C": round(float(np.hypot(a, b).mean()), 1),
            "sh": [round(float(a[dark].mean()), 1), round(float(b[dark].mean()), 1)] if dark.sum() > 50 else None}


def _mix(hx, k):
    """A tone colour at strength k: its chroma scaled about its own grey, so the hue is the design and k is the dose."""
    import lutlib
    r, g, b = (v / 255.0 for v in lutlib.hex_rgb(hx))
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#%02x%02x%02x" % tuple(int(round(255 * min(max(y + (c - y) * k, 0.0), 1.0))) for c in (r, g, b))


def _hue(hx):
    """A shadow colour as the temperature and tint that lean the shadows its way, at full dose: the pair whose
    colorbalance offsets lie nearest the colour's own lean off grey, scaled to the end of the range."""
    import lutlib
    r, g, b = (v / 255.0 for v in lutlib.hex_rgb(hx))
    y = (r + g + b) / 3
    d = np.array([r - y, g - y, b - y])
    A = np.array([[0.10, 0.03], [0.0, -0.08], [-0.12, 0.05]])
    tm, *_ = np.linalg.lstsq(A, d, rcond=None)
    k = float(np.abs(tm).max())
    return (float(tm[0] / k), float(tm[1] / k)) if k > 1e-9 else (0.0, 0.0)


def _balance(rec, k):
    t, m = _hue(rec["sh"])
    return {"temperature": round(t * k, 3), "tint": round(m * k, 3)}


def recipe(raw):
    """(name, design) for what the Score wrote: a name, an object with a name and its own hues, or the older seven
    numbers, read by the way they lean. Never fails: an unknown name is the default design, and the caller says so."""
    if isinstance(raw, str) and raw in LOOKS:
        return raw, dict(LOOKS[raw])
    if isinstance(raw, dict):
        name = raw.get("name") if raw.get("name") in LOOKS else None
        if name is None:
            t = raw.get("temp")
            t = float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0.0
            s = raw.get("sat")
            s = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else 1.0
            name = "bleach" if s < 0.72 else "warm_low" if t > 0.012 else "print" if t > 0.004 else "cold_doc" if t < -0.012 else DEFAULT
        rec = dict(LOOKS[name])
        v = raw.get("shadows")
        if isinstance(v, str) and len(v) == 7 and v.startswith("#"):
            try:
                int(v[1:], 16)
                rec["sh"] = v
            except ValueError:
                pass
        k = raw.get("amount")
        if isinstance(k, (int, float)) and not isinstance(k, bool):
            rec["de"] = float(np.clip(rec["de"] * float(k), DE_MIN, DE_MAX))
        return name, rec
    return DEFAULT, dict(LOOKS[DEFAULT])


def solve(arr, raw, card=None):
    """The look for these frames, or None when there are none. `raw` is what the Score wrote, `card` the style
    card the film named. The answer holds the two windows' fields and what was measured on the way."""
    if arr is None or not len(arr):
        return None
    import lutlib
    name, rec = recipe(raw)
    px = pixels(arr)
    px = px[:: max(1, len(px) // SOLVE_PX)]          # a fixed stride: the same frames give the same sample
    lab0 = lutlib.rgb_to_lab(px)
    was = numbers(px, lab0)
    want = CARDS.get(card) if isinstance(card, str) else None

    def clipped(p):
        y = 0.2126 * p[:, 0] + 0.7152 * p[:, 1] + 0.0722 * p[:, 2]
        return float(((y < 0.012) | (y > 0.988)).mean())
    clip0 = clipped(px)

    carry = (lab0[:, 0] > 8) & (lab0[:, 0] < 97)          # what can take colour: not the bars, the blacks or blown white

    def measure(base, bal=None):
        out, skin = apply(px, {"base": base, "balance": bal})
        lab = lutlib.rgb_to_lab(out)
        de = lutlib.delta_e2000(lab0, lab)
        w = (1.0 - skin) * carry
        return out, lab, float((de * w).sum() / max(w.sum(), 1e-6)), float((de * skin).sum() / max(skin.sum(), 1e-6)) if skin.sum() > 20 else 0.0

    # tone first, each a search on the renderer's own tables: the median a third of the way to the card's, as far
    # as skin allows; then the spread; then the chroma
    l50 = was["L50"] + PULL_L * (want["L50"] - was["L50"]) if want else was["L50"]
    l50 += 12.0 * rec["ev"]
    evs = []
    for e in (round(v, 2) for v in np.arange(-EV_MAX, EV_MAX + 1e-6, 0.05)):
        out, lab, _, sde = measure({"ev": e})
        if sde <= SKIN_DE_EV or abs(e) < 1e-9:
            evs.append((abs(numbers(out, lab)["L50"] - l50), abs(e), e))
    ev = min(evs)[2]
    spread0 = was["L95"] - was["L5"]
    spread = (spread0 + PULL * ((want["L95"] - want["L5"]) - spread0) if want else spread0) * rec["con"]
    best = None
    for c in (round(v, 2) for v in np.arange(1.0, 1.3401, 0.02)):
        out, lab, _, sde = measure({"ev": ev, "contrast": c})
        n = numbers(out, lab)
        if c > 1.0 and (clipped(out) - clip0 > CLIP_MAX or sde > SKIN_DE_MAX):
            break
        d = abs((n["L95"] - n["L5"]) - spread)
        if best is None or d < best[0] - 1e-9:
            best = (d, c)
    con = best[1] if best else 1.0
    chroma = (was["C"] + PULL_C * (want["C"] - was["C"]) if want else was["C"]) * rec["sat"]
    sat = min((round(v, 3) for v in np.arange(SAT[0], SAT[1] + 1e-6, 0.025)),
              key=lambda v: (abs(numbers(*measure({"ev": ev, "contrast": con, "saturation": v})[:2])["C"] - chroma), abs(v - 1)))
    base = {"ev": ev, "contrast": con, "saturation": sat}
    # the shadow hue: the dose it takes to stand `de` off the upload, never more than skin can take
    lo, hi = AMOUNT[0], min(AMOUNT[1], float(rec.get("most") or AMOUNT[1]))
    if measure(base, _balance(rec, hi))[2] < rec["de"] and measure(base, _balance(rec, hi))[3] <= SKIN_DE_MAX:
        lo = hi            # the whole dose still stands short of `de`: the design gets all it carries
    for _ in range(10):
        mid = (lo + hi) / 2
        _, _, de, sde = measure(base, _balance(rec, mid))
        if de < rec["de"] and sde <= SKIN_DE_MAX:
            lo = mid
        else:
            hi = mid
    k = round(lo, 2)
    bal = _balance(rec, k)
    out, lab, de, sde = measure(base, bal)
    look = {"name": name, "card": card if want else None, "amount": k, "base": {key: v for key, v in base.items() if abs(v - (0.0 if key == "ev" else 1.0)) > 1e-6},
            "balance": bal, "de": round(de, 1), "skin_de": round(sde, 1), "skin": round(float(apply(px, {})[1].mean()), 3),
            "was": was, "now": numbers(out, lab), "aim": {k_: want[k_] for k_ in ("L5", "L50", "L95", "C")} if want else None}
    if rec.get("hi") and de < rec["de"]:
        # the keyed tone, for what the shadow hue alone leaves short: the highlights' hue and the white balance
        # of everything that is not skin, dosed the same way
        def keyed(q):
            return {"temperature": round(float(np.clip(rec.get("temp", 0.0) * q * 2, -1, 1)), 3),
                    "split_tone": {"shadows": "#808080", "highlights": _mix(rec["hi"], q), "balance": 0.0}, "protect": ["skin"]}

        def measure_key(q):
            o, skin = apply(px, {"base": base, "balance": bal, "key": keyed(q)})
            lb = lutlib.rgb_to_lab(o)
            d = lutlib.delta_e2000(lab0, lb)
            w = (1.0 - skin) * carry
            return o, lb, float((d * w).sum() / max(w.sum(), 1e-6)), float((d * skin).sum() / max(skin.sum(), 1e-6)) if skin.sum() > 20 else 0.0
        lo, hi = 0.2, 1.4
        for _ in range(10):
            mid = (lo + hi) / 2
            _, _, d, sd = measure_key(mid)
            if d < rec["de"] and sd <= SKIN_DE_MAX:
                lo = mid
            else:
                hi = mid
        o, lb, d, sd = measure_key(lo)
        look.update(key=keyed(round(lo, 2)), key_de=round(d, 1), key_skin_de=round(sd, 1), key_now=numbers(o, lb))
    return look


def windows(look, start, end, keyed=True):
    """The look's tone as today's fx items over [start, end): the plain one, which a found picture takes too, and
    the keyed tone when the film carries one and its take is never cut in as a picture (`keyed`)."""
    out = []
    if not look or end - start < 0.2:
        return out
    if look.get("base"):
        out.append({"id": "look.base", "type": "grade_window", "stage": "screen", "start": start, "end": end, **look["base"]})
    if keyed and look.get("key"):
        out.append({"id": "look.key", "type": "grade_window", "stage": "screen", "start": start, "end": end, **look["key"]})
    return out


def custom(look):
    """The look's shadow hue as `effects.grade_custom`: the renderer lays it on footage and cutaways alike."""
    bal = (look or {}).get("balance") or {}
    return {k: v for k, v in bal.items() if abs(float(v)) > 1e-3}


def menu():
    """The looks by name, for the prompt and the Brief."""
    return [f"{k}: {v['is']}" for k, v in LOOKS.items()]


def line(look):
    """One meter row: what the look is and what it measures."""
    if not look:
        return None
    w, n, a = look["was"], look["now"], look.get("aim")
    row = (f"LOOK {look['name']}: stands {look['de']} delta E off the upload where it is neither skin nor black, skin moves {look['skin_de']} "
           f"({int(look['skin'] * 100)}% of the frame). L* 5/50/95 {w['L5']:.0f}/{w['L50']:.0f}/{w['L95']:.0f} to {n['L5']:.0f}/{n['L50']:.0f}/{n['L95']:.0f}, "
           f"chroma {w['C']:.0f} to {n['C']:.0f}")
    if look.get("key"):
        row += f". With the keyed tone on a take that is never cut in as a picture: {look['key_de']}, skin {look['key_skin_de']}"
    if a:
        row += f"; card {look['card']} measures {a['L5']:.0f}/{a['L50']:.0f}/{a['L95']:.0f}, chroma {a['C']:.0f}"
    return row
