"""What the type, behind and HUD devices share: where a word is said, how big a line stands, which ink reads on the
ground under it, where a block can sit off the faces, the captions and the watermark, and how a word item is cut.

Signal only. A word is found on the film's own word clock (its caption words carry the frame each word is said), a
ground by the pixels the base shot shows there, a face by YuNet on those pixels. Which words, which figures and which
device come from the caller (a model that read the script); nothing here reads a meaning.
"""
import math
import os
import random

import numpy as np

from ..alien import glyphs, sense as asense
from ..plan import A

MARK = (0.5594, 0.115, 0.9146, 0.1923)   # the watermark's keep-out (renderer.watermark_geometry at 1080x1920): no type, ever
READ = (0.05, 0.10, 0.95, 0.80)          # where reading type stands (TASTE P2); a graphic may pass it, never the mark
RAIL_X, RAIL_Y = 0.86, 0.55              # right of x 0.86 under y 0.55: the platform's buttons
FLOOR_PX = 38                            # the reading floor at 1080 wide
DARK, LIGHT = "#0B0A09", "#F6F1E7"
MONO = {"font": "JetBrainsMono-Variable.ttf", "var": {"wght": 560}, "track": 2}
SANS = "InterDisplay-Black.ttf"
SCALE = 1.0                              # output pixels per pixel at 1080 wide: every px constant here is written at 1080


def scaled(l1):
    """Set the frame's scale for the px constants (a device calls it first); returns W / 1080."""
    global SCALE
    SCALE = float((l1.get("frame") or [1080])[0]) / 1080.0
    return SCALE


def px(v):
    return round(float(v) * SCALE, 2)


def mono(n):
    return dict(MONO, px=px(n))


def opts(ask):
    """An ask's fields, wherever the caller put them: at the top or under ``args``. An ask placed by its programme frames
    ("f": a frame or [a, z], as the Director's asks and palette.apply's log carry them) and no "at" is at those frames:
    every device here reads its place from "at" (20 Nov rating: type devices asked at 4-24 s read no "at", found their text
    nowhere and landed at frame 0, so counters, names and ladders piled into the first second)."""
    out = dict(ask.get("args") or {})
    out.update({k: v for k, v in ask.items() if k != "args"})
    if out.get("at") is None and isinstance(out.get("f"), (int, float, list, tuple)) and not isinstance(out.get("f"), bool):
        f = out["f"]
        if isinstance(f, (list, tuple)):
            if len(f) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in f[:2]):
                out["at"] = [int(f[0]), int(f[1])]
        else:
            out["at"] = int(f)
    return out


def rng_of(l1, ask, salt=""):
    """The film's seed and the ask's id: the same ask on the same film breaks its ties the same way."""
    return random.Random(f"{l1.get('seed', 0)}|{ask.get('id')}|{ask.get('dev')}|{salt}")


def size(l1):
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    return W, H, float(l1.get("fps") or 30), int(l1["dur_f"])


# ----------------------------------------------------------------- the word clock

def norm(w):
    return "".join(ch for ch in str(w).lower() if ch.isalnum())


def said(l1, seg=None):
    """The film's words in the order they are said: [(text, frame said, frame it ends, sentence)], one row a word.
    The scene's own word clock (``said``: [word, frame, end frame, sentence]) when the workflow wrote it; else the
    captions, which ink each word on the frame it is spoken (``on``; a word cut into pieces for its ink is one word).
    ``seg``: only that sentence's words."""
    rows = l1.get("said")
    if rows:
        out = []
        for r in rows:
            try:
                out.append((str(r[0]), int(r[1]), int(r[2]) if len(r) > 2 else int(r[1]) + 6, int(r[3]) if len(r) > 3 and r[3] is not None else None))
            except (TypeError, ValueError, IndexError):
                continue
        out.sort(key=lambda r: r[1])
        return [r for r in out if seg is None or r[3] == int(seg)] if seg is not None and any(r[3] is not None for r in out) else out
    seen, out = set(), []
    for it in l1.get("items") or []:
        if it.get("k") != "word" or it.get("role") != "cap":
            continue
        on = int(it.get("on", it["f"][0]))
        key = (norm(it.get("t")), on)
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append((str(it.get("t")), on, int(it["f"][1]), None))
    out.sort(key=lambda r: r[1])
    return out


def find(l1, text, near=None, words=None):
    """[(word, frame said)] where ``text`` is said: the run of the film's words that spells it, nearest ``near``.
    A phrase spoken only in part is found by its longest said run; None when no word of it is said."""
    words = said(l1) if words is None else words
    want = [norm(w) for w in str(text or "").split() if norm(w)]
    if not want or not words:
        return None
    keys = [norm(w[0]) for w in words]
    best = None
    for n in range(len(want), 0, -1):
        for s in range(len(want) - n + 1):
            part = want[s:s + n]
            for i in range(len(keys) - n + 1):
                if keys[i:i + n] == part:
                    d = abs(words[i][1] - near) if near is not None else i
                    if best is None or d < best[0]:
                        best = (d, [(words[j][0], words[j][1]) for j in range(i, i + n)])
        if best:
            return best[1]
    return None


def frame_of(l1, at, near=None, words=None):
    """A programme frame from what an ask gives: a frame, {"f"}, or {"word", "near"|"f"} found on the word clock."""
    if at is None:
        return None
    if isinstance(at, bool):
        return None
    if isinstance(at, (int, float)):
        return int(at)
    if isinstance(at, (list, tuple)) and at and isinstance(at[0], (int, float)):
        return int(at[0])
    if isinstance(at, dict):
        if at.get("f") is not None and not at.get("word"):
            return int(at["f"])
        if at.get("seg") is not None:
            ws = said(l1, seg=at["seg"])
            if ws and not at.get("word"):
                return ws[0][1]
            words = ws or words
        hit = find(l1, at.get("word") or at.get("t"), at.get("near", at.get("f", near)), words)
        if hit:
            return hit[0][1]
        return int(at["f"]) if at.get("f") is not None else None
    if isinstance(at, str):
        hit = find(l1, at, near, words)
        return hit[0][1] if hit else None
    return None


def span(l1, ask, need=24, text=None):
    """(a, z, said) for an ask: its first and last programme frame and the words of ``text`` as they are said in it.
    ``at`` names the start ([a, z], a frame, or a word), ``until`` the end; with no end the device holds ``need``
    frames past the last word of its text. Clamped inside the film."""
    W, H, fps, dur = size(l1)
    o = opts(ask)
    at = o.get("at")
    words = said(l1)
    a = frame_of(l1, at, words=words)
    z = int(at[1]) if isinstance(at, (list, tuple)) and len(at) > 1 and isinstance(at[1], (int, float)) else None
    hit = None
    if text:
        hit = find(l1, text, near=a, words=words)
        if a is None and hit:
            a = hit[0][1]
    if a is None:
        a = 0
    if isinstance(o.get("until"), (int, float)) and not isinstance(o.get("until"), bool):
        z = int(o["until"])
    elif o.get("until") is not None:
        u = frame_of(l1, o["until"], near=a, words=words)
        if u is not None:
            nxt = [w[1] for w in words if w[1] > u]
            z = max(z or 0, (nxt[0] if nxt else u + need))
    if hit and at is not None:
        # the device has its place: its text said a second or more away from it (a title's "the" said in the hook) is
        # another moment's, so the device stands on its own place (a name asked at 4 s landed on frame 22's "the")
        slack = int(round(fps))
        if not a - slack <= hit[0][1] <= (z if z is not None else a) + slack:
            hit = None
    if z is None:
        last = max([h[1] for h in hit] if hit else [a])
        z = last + need
    a, z = max(0, min(a, dur - 2)), max(0, min(z, dur))
    if z - a < 2:
        z = min(dur, a + max(2, need))
        a = min(a, z - 2)
    z = bounded(o, z, a + 2)
    return a, z, hit or []


def bounded(o, z, floor=None):
    """``z`` no later than the ask's ``bound`` (the frame the caller's window ends: the next subject, the next display),
    never before ``floor`` (a figure still lands on the frame it is said, and holds a beat)."""
    b = o.get("bound")
    if b is None or isinstance(b, bool):
        return z
    try:
        b = int(b)
    except (TypeError, ValueError):
        return z
    out = min(int(z), b)
    return max(out, int(floor)) if floor is not None else out


def times(l1, text, a, z, hit):
    """Frame each word of ``text`` lands on: said words on their own frame, the rest spread between them."""
    ws = str(text or "").split()
    if not ws:
        return []
    got = {}
    j = 0
    for i, w in enumerate(ws):
        while j < len(hit) and norm(hit[j][0]) != norm(w):
            j += 1
        if j < len(hit):
            got[i] = hit[j][1]
            j += 1
    out, last = [], a
    for i in range(len(ws)):
        if i in got:
            last = max(got[i], last)
        else:
            nxt = next((got[k] for k in range(i + 1, len(ws)) if k in got), None)
            step = 3 if nxt is None else max((nxt - last) // (sum(1 for k in range(i, len(ws)) if k not in got and (nxt is None or k < len(ws))) + 1), 1)
            last = last + (0 if i == 0 and last == a else step)
        out.append(min(max(last, a), z - 1))
    return out


# ----------------------------------------------------------------- lines

def voice(font=SANS, px=120.0, track=0, shadow=0.55, var=None, glow=None):
    v = {"font": font, "px": round(float(px), 2), "track": track}
    if var:
        v["var"] = dict(var)
    if shadow:
        v["shadow"] = {"a": float(shadow), "blur": 0.012}
    if glow:
        v["glow"] = {"a": float(glow), "blur": 0.03}
    return v


_MET = {}


def metrics(v, text):
    """How the renderer draws a line, in pixels from the item's pen (x, y), y down: {"adv", "ink" (as drawn: shaped with
    the voice's tracking and set in the middle of the compiler's slot), "c" (the ink centre a scale grows from),
    "glyphs" ([x0, y0, x1, y1] of each glyph's ink, in reading order)}. The engine's own shaper and slot
    (scene.type.compiler_advance, scene.type_atlas), so a seat measured here is the seat drawn."""
    key = (v.get("font"), v.get("family"), str(v.get("var")), round(float(v.get("px", 58)), 3), float(v.get("track", 0) or 0),
           bool(v.get("shaped")), str(text))
    got = _MET.get(key)
    if got is not None:
        return got
    px, tr = float(v.get("px", 58)), float(v.get("track", 0) or 0)
    try:
        from .. import type as ST
        face = glyphs.face(v)
        lines, adv = face.outline(text, px, tr)
        quads = [list(q[1:]) for q in face.quads(text, px, tr)]
        try:
            dx = (float(ST.compiler_advance(text, v)) - adv) / 2.0
        except Exception:      # noqa: BLE001 - a face Pillow cannot open: the line sits at its own pen
            dx = 0.0
    except Exception:      # noqa: BLE001 - no shaper here: the plain measure
        adv, b = glyphs.box(v, text, px)
        lines, quads, dx = None, [list(b)], 0.0
    if lines is not None and len(lines):
        raw = [float(lines[:, [0, 2]].min()), float(lines[:, [1, 3]].min()), float(lines[:, [0, 2]].max()), float(lines[:, [1, 3]].max())]
    elif lines is not None:
        raw = [0.0, -px, float(adv), 0.0]
    else:
        raw = list(quads[0])
    got = {"adv": float(adv), "ink": [raw[0] + dx, raw[1], raw[2] + dx, raw[3]], "c": ((raw[0] + raw[2]) / 2 + dx, (raw[1] + raw[3]) / 2),
           "glyphs": [[q[0] + dx, q[1], q[2] + dx, q[3]] for q in quads], "dx": dx}
    if len(_MET) > 20000:
        _MET.clear()
    _MET[key] = got
    return got


def ink_box(v, text):
    """(advance, [x0, y0, x1, y1]) of a line's ink as the renderer draws it, pixels from its pen (baseline left), y down."""
    m = metrics(v, text)
    return m["adv"], list(m["ink"])


def drawn(v, text, x, y, W, H, s=1.0, part=None):
    """[x0, y0, x1, y1] (frame fractions) of a line's ink as drawn with its pen at (x, y) and scale ``s`` (the renderer
    scales a word about its ink centre). ``part``: (first, last) glyph of the line to measure instead of the whole."""
    m = metrics(v, text)
    b = m["ink"]
    if part is not None and m["glyphs"]:
        gs = m["glyphs"][part[0]:part[1]] or m["glyphs"]
        b = [min(g[0] for g in gs), min(g[1] for g in gs), max(g[2] for g in gs), max(g[3] for g in gs)]
    X, Y = float(x) * W, float(y) * H
    cx, cy = X + m["c"][0], Y + m["c"][1]
    s = float(s)
    return [(cx + (X + b[0] - cx) * s) / W, (cy + (Y + b[1] - cy) * s) / H, (cx + (X + b[2] - cx) * s) / W, (cy + (Y + b[3] - cy) * s) / H]


def item_box(it, voices, W, H, f=None):
    """Where a word item's ink stands on programme frame f (its last frame when None), as drawn."""
    v = (voices or {}).get(it.get("v"))
    if not v:
        return None
    lo = it["f"][0]
    f = it["f"][1] - 1 if f is None else f
    return drawn(v, str(it.get("t") or ""), A(it.get("x", 0), f, lo), A(it.get("y", 0), f, lo), W, H, s=A(it.get("s", 1), f, lo))


def fit(v, text, width_px, cap_px=None, floor_px=None):
    """The voice at the size where ``text`` is ``width_px`` wide (never past ``cap_px``, never under the floor)."""
    got = glyphs.fit_px(v, text, width_px, cap_px=cap_px)
    return dict(v, px=round(max(float(got), float(floor_px if floor_px is not None else FLOOR_PX * SCALE)), 2))


def place(v, text, cx, base_y, W, H, align="c"):
    """Pen (x, y) as frame fractions for a line whose ink is centred on (or starts at, or ends at) cx, on base_y."""
    _, b = ink_box(v, text)
    if align == "l":
        x = cx - b[0] / W
    elif align == "r":
        x = cx - b[2] / W
    else:
        x = cx - (b[0] + b[2]) / 2 / W
    return round(x, 6), round(base_y, 6)


def block_of(v, text, x, y, W, H, s=1.0):
    """[x0, y0, x1, y1] (frame fractions) a line's ink takes with its pen at (x, y)."""
    return drawn(v, text, x, y, W, H, s=s)


def wrap(v, text, width_px):
    """Lines of ``text`` no wider than ``width_px`` at the voice's size; a word wider than that stands alone."""
    out, cur = [], ""
    for w in str(text).split():
        t = (cur + " " + w).strip()
        if cur and ink_box(v, t)[1][2] > width_px:
            out.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        out.append(cur)
    return out


# ----------------------------------------------------------------- what the frame holds there

def caption_top(l1, a=None, z=None):
    """The highest a caption's ink reaches on the lower caption seat (frame fraction), over [a, z) or the whole film;
    0.80 with none. A caption the film lifts to the top of the frame (over a busy lower picture) is not a floor: it is
    in the way where it stands (``caption_boxes``)."""
    tops = []
    for it in l1.get("items") or []:
        if it.get("k") != "word" or it.get("role") != "cap":
            continue
        if a is not None and (it["f"][1] <= a or it["f"][0] >= z):
            continue
        y = it["y"][-1] if isinstance(it.get("y"), list) else it.get("y", 0.8)
        if float(y) < 0.45:
            continue
        px = float(((l1.get("voices") or {}).get(it.get("v")) or {}).get("px", 58))
        tops.append(float(y) - 0.8 * px / l1["frame"][1])
    return min(tops) if tops else 0.80


def caption_boxes(l1, a, z, pad=0.012):
    """[(box, f0, f1)] of the captions standing between a and z, as drawn, padded: type laid off the caption seat
    must not land on one."""
    W, H = l1["frame"]
    out = []
    for it in l1.get("items") or []:
        if it.get("k") != "word" or it.get("role") != "cap" or it["f"][1] <= a or it["f"][0] >= z:
            continue
        try:
            b = item_box(it, l1.get("voices"), W, H)
        except Exception:      # noqa: BLE001
            b = None
        if b:
            out.append(([b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad], int(it["f"][0]), int(it["f"][1])))
    return out


def others_t(l1, a, z, skip=()):
    """[(box, f0, f1)]: the type already standing between a and z that is not a caption, each with the frames it
    stands. Its box is where it rests (its last frame), as drawn."""
    W, H = l1["frame"]
    out = []
    for it in l1.get("items") or []:
        if it.get("k") != "word" or it.get("role") == "cap" or it["id"] in skip or it.get("behind"):
            continue
        if it["f"][1] <= a or it["f"][0] >= z:
            continue
        try:
            b = item_box(it, l1.get("voices"), W, H)
        except Exception:      # noqa: BLE001 - a word that cannot be measured is not in the way
            b = None
        if b:
            out.append((b, int(it["f"][0]), int(it["f"][1])))
    return out


def others(l1, a, z, skip=()):
    """Boxes of the type already standing between a and z that is not a caption: a block must not land on it."""
    return [b for b, _, _ in others_t(l1, a, z, skip)]


def clear_of_mark(items, voices, W, H, own):
    """A device's own type (items whose id starts with ``own``) moved down, as one block, until no word of it meets
    the watermark's keep-out on any frame: a repair, never a refusal. Returns how far it moved (frame fraction)."""
    mine = [it for it in items if str(it.get("id", "")).startswith(own) and it.get("k") in ("word", "flash")]
    need = 0.0
    for it in mine:
        if it.get("k") != "word" or it.get("role") == "cap":
            continue
        lo, hi = it["f"]
        for f in sorted({lo, (lo + hi) // 2, hi - 1} | set(range(lo, hi, 4))):
            try:
                b = item_box(it, voices, W, H, f)
            except Exception:      # noqa: BLE001
                b = None
            if b and overlap(b, MARK) > 1e-6:
                need = max(need, MARK[3] + 0.006 - b[1])
    if need <= 0:
        return 0.0
    for it in mine:
        if it.get("k") == "word":
            y = it.get("y", 0)
            it["y"] = [round(float(q) + need, 6) for q in y] if isinstance(y, list) else round(float(y) + need, 6)
        elif it.get("mode") in ("fill", "neg") and it.get("rect") is not None:
            r = it["rect"]
            sh = (lambda q: [q[0], round(q[1] + need, 5), q[2], round(q[3] + need, 5)])
            it["rect"] = [sh(q) for q in r] if isinstance(r[0], (list, tuple)) else sh(r)
    return round(need, 4)


def overlap(a, b):
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h


class Sense:
    """What the devices measure on the base picture, read once a film: small grey frames, faces, a ground's tone.
    A palette ``sense`` handed in by the caller is used first for anything it has (the take's matte, faces)."""

    def __init__(self, l1, work, outer=None):
        self.l1, self.work, self.outer = l1, work, outer
        self._fr, self._faces = {}, {}

    def frames(self, a, z, n=3, size=(108, 192)):
        fs = sorted({a, (a + z) // 2, max(a, z - 1)} if n <= 3 else set(np.linspace(a, max(a, z - 1), n).astype(int).tolist()))
        want = [f for f in fs if (f, size) not in self._fr]
        for f in want:
            got = asense.shot_frames(self.l1, self.work, f, f + 1, size=size, gray=False)
            self._fr[(f, size)] = got.get(f)
        return {f: self._fr[(f, size)] for f in fs if self._fr.get((f, size)) is not None}

    def ground(self, box, a, z):
        """Mean tone (0 black .. 1 white) and texture under a box over [a, z); 0.25 where there is no picture."""
        tones, tex = [], []
        for fr in self.frames(a, z).values():
            g = (fr.astype(np.float32) @ asense.LUMA) / 255.0
            h, w = g.shape
            x0, y0 = int(max(box[0], 0) * w), int(max(box[1], 0) * h)
            x1, y1 = int(min(box[2], 1) * w), int(min(box[3], 1) * h)
            if x1 <= x0 or y1 <= y0:
                continue
            p = g[y0:y1, x0:x1]
            tones.append(float(p.mean()))
            tex.append(float(p.std()))
        return (float(np.mean(tones)) if tones else 0.25), (float(np.mean(tex)) if tex else 0.0)

    def faces(self, f):
        """[[x0, y0, x1, y1]] frame fractions of the faces on programme frame f (YuNet), largest first."""
        if f in self._faces:
            return self._faces[f]
        out = []
        if self.outer is not None and hasattr(self.outer, "faces"):
            try:
                out = [list(map(float, b[:4])) for b in (self.outer.faces(f) or [])]
            except Exception:      # noqa: BLE001
                out = []
        if not out:
            fr = self.frames(f, f + 1, size=(270, 480)).get(f)
            out = detect_faces(fr) if fr is not None else []
        self._faces[f] = out
        return out

    def faces_over(self, a, z, n=3):
        fs = sorted({a, (a + z) // 2, max(a, z - 1)}) if n <= 3 else sorted(set(np.linspace(a, max(a, z - 1), n).astype(int).tolist()))
        return [b for f in fs for b in self.faces(f)]


def model_path(name, env=None):
    """A signal model's file: its environment variable, the worker's models folder, or the project's model store."""
    if env and os.environ.get(env) and os.path.exists(os.environ[env]):
        return os.environ[env]
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands = [os.path.join(here, "models", name)]
    if os.environ.get("VALMERA_MODELS"):
        cands.insert(0, os.path.join(os.environ["VALMERA_MODELS"], name))
    d = here
    for _ in range(5):
        d = os.path.dirname(d)
        cands.append(os.path.join(d, ".valmera", "models", name))
    return next((c for c in cands if os.path.exists(c)), None)


_YN = {}


def detect_faces(rgb):
    """Faces on an RGB frame (YuNet), [[x0, y0, x1, y1]] fractions, the box carried down to the chin; [] without
    the model."""
    try:
        import cv2
    except Exception:      # noqa: BLE001
        return []
    p = model_path("face_detection_yunet_2023mar.onnx", "YUNET_MODEL")
    if not p or not hasattr(cv2, "FaceDetectorYN"):
        return []
    h, w = rgb.shape[:2]
    key = (p, w, h)
    if key not in _YN:
        try:
            _YN[key] = cv2.FaceDetectorYN.create(p, "", (w, h), 0.6, 0.3, 20)
        except Exception:      # noqa: BLE001
            _YN[key] = None
    det = _YN[key]
    if det is None:
        return []
    try:
        _, fs = det.detect(np.ascontiguousarray(rgb[..., ::-1]))
    except Exception:      # noqa: BLE001
        return []
    out = []
    for r in fs if fs is not None else []:
        x, y, bw, bh = (float(v) for v in r[:4])
        eye = (float(r[5]) + float(r[7])) / 2
        mouth = (float(r[11]) + float(r[13])) / 2
        y1 = max(y + bh, mouth + 0.9 * max(mouth - eye, 0))
        out.append([max(x / w, 0.0), max(y / h, 0.0), min((x + bw) / w, 1.0), min(y1 / h, 1.0)])
    out.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return out


def seat(l1, S, a, z, bw, bh, prefer=0.36, align="c", x=None, avoid=(), lo=0.13, graphic=False):
    """The baseline (and left edge) of a block (bw, bh frame fractions) over [a, z): off the faces, the captions, the
    type already standing, the platform's rail and the watermark; on the calmer ground; near ``prefer``.
    Returns (x0, baseline y)."""
    cap = caption_top(l1, a, z) - 0.03
    faces = S.faces_over(a, z)
    stand = list(others(l1, a, z)) + [list(b) for b in avoid] + [b for b, _, _ in caption_boxes(l1, a, z)]
    hi = max(cap, lo + bh + 0.02)
    xs = [x] if x is not None else ([0.5 - bw / 2] if align == "c" else [0.07, 0.5 - bw / 2])
    best, at = None, (xs[0], min(max(prefer + bh, lo + bh), hi))
    for x0 in xs:
        # under the watermark's keep-out where the block's columns meet it, with room for a word's landing punch
        top = max(lo, MARK[3] + 0.012 + 0.12 * bh) if (x0 < MARK[2] and x0 + bw > MARK[0]) else lo
        for y1 in np.arange(top + bh, max(hi, top + bh) + 1e-6, 0.012):
            box = [x0, y1 - bh, x0 + bw, y1]
            s = -abs((y1 - bh / 2) - prefer) * 1.2
            for fb in faces:
                fz = [fb[0] - 0.02, fb[1] - 0.03, fb[2] + 0.02, fb[3] + 0.02]
                s -= 9.0 * overlap(box, fz) / max(bw * bh, 1e-6)
            for ob in stand:
                s -= 6.0 * overlap(box, ob) / max(bw * bh, 1e-6)
            s -= 40.0 * overlap(box, MARK) / max(bw * bh, 1e-6)
            if not graphic and y1 > RAIL_Y and box[2] > RAIL_X:
                s -= 2.0
            if best is None or s > best:
                best, at = s, (float(x0), float(y1))
    return at


def ink_on(tone, ink=LIGHT, dark=DARK):
    """The ink that reads on a ground of this tone: the light ink on most pictures, the dark one on a bright ground."""
    return dark if tone > 0.62 else ink


def palette_of(l1, o=None):
    """The film's own ink, accent and display face, from what it already sets large (or the ask's own)."""
    o = o or {}
    ink, accent, font = LIGHT, None, None
    counts = {}
    for it in l1.get("items") or []:
        if it.get("k") == "word" and it.get("role") == "big" and it.get("ink"):
            counts[it["ink"].upper()] = counts.get(it["ink"].upper(), 0) + 1
            v = (l1.get("voices") or {}).get(it.get("v")) or {}
            font = font or v.get("font")
    for c in sorted(counts, key=lambda c: -counts[c]):
        if c not in (DARK.upper(), "#000000") and lum(c) < 0.85 and accent is None and sat(c) > 0.18:
            accent = c
    dsp = (l1.get("voices") or {}).get("dsp") or {}
    return {"ink": o.get("ink") or LIGHT, "accent": o.get("accent") or accent or "#F2C14E",
            "font": font_file(o.get("font")) or dsp.get("font") or font or SANS}


def font_file(name):
    """The font file a device's ``font`` word names, or None for the film's own display face. The cards offer roles
    ('mono', 'display'); the director may also name a family or a file that is not on this machine. 20 Nov
    (Alexandr Wang m03): a typewriter asked for font 'display', the word reached the renderer as a file name and the
    strict plan stopped the short; a role or a missing file now draws in the film's own face."""
    if not name or not isinstance(name, str):
        return None
    if name.strip().lower() == "mono":
        return MONO["font"]
    from ..type import find_font
    try:
        find_font(name)
    except FileNotFoundError:
        return None
    return name


def rgb(hexc):
    c = str(hexc or "#FFFFFF").lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def lum(hexc):
    r, g, b = rgb(hexc)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def sat(hexc):
    r, g, b = rgb(hexc)
    return (max(r, g, b) - min(r, g, b)) / max(max(r, g, b), 1e-6)


# ----------------------------------------------------------------- items

def word(id_, f, t, v, x, y, ink, z=8, role="graphic", on=None, **kw):
    it = {"id": id_, "k": "word", "f": [int(f[0]), int(f[1])], "t": t, "v": v, "x": x, "y": y, "ink": ink, "role": role,
          "on": int(on if on is not None else f[0]), "z": z}
    it.update({k: v_ for k, v_ in kw.items() if v_ is not None})
    return it


def rect(id_, f, r, c, a=1.0, z=1, mode="fill"):
    """A flat rectangle (a flash in fill mode): drawn after the picture and the film layer, under every word."""
    return {"id": id_, "k": "flash", "f": [int(f[0]), int(f[1])], "mode": mode, "rect": r, "c": c, "a": a, "z": z}


def cut(it, a, b, new_id=None):
    """The part of an item on programme frames [a, b): its per-frame lists sliced, its id kept or renamed."""
    lo, hi = it["f"]
    a, b = max(int(a), lo), min(int(b), hi)
    out = dict(it, f=[a, b], id=new_id or it["id"])
    for k, v in it.items():
        if isinstance(v, list) and len(v) == hi - lo and k not in ("f", "src"):
            out[k] = v[a - lo:b - lo]
    if it.get("src"):
        s0, s1 = float(it["src"][0]), float(it["src"][1])
        r = (s1 - s0) / max(hi - lo, 1)
        out["src"] = [round(s0 + r * (a - lo), 5), round(s0 + r * (b - lo), 5)]
    return out


def picture_spans(l1, pic, a, b):
    """Intervals where the identified asset is the visible picture after device cuts."""
    layers = [v for v in l1["items"] if v["k"] in ("shot", "card") and v.get("pic")
              and v["f"][0] < b and v["f"][1] > a]
    edges = sorted({a, b, *(f for v in layers for f in v["f"] if a < f < b)})
    spans = []
    for start, end in zip(edges, edges[1:]):
        live = [v for v in layers if v["f"][0] <= start < v["f"][1]]
        # A source document can be a card over the interview. A montage with
        # more than one foreground subject cannot identify a single portrait.
        subjects = [v for v in live if v["pic"] not in ("palette_gate", "palette_crt")]
        front = [v for v in subjects if v["k"] == "card"] or subjects
        if front and all(v["pic"] == pic for v in front):
            if spans and spans[-1][1] == start:
                spans[-1][1] = end
            else:
                spans.append([start, end])
    return spans


def clear_captions(l1, a, z):
    """(items, drop): the caption words and their shade pools cut out of [a, z), for a page of type that takes the
    whole frame and says the words itself. A word or pool reaching past the page keeps its parts outside it."""
    items, drop = [], []
    for it in l1.get("items") or []:
        cap = it.get("k") == "word" and it.get("role") == "cap"
        pool = it.get("k") == "flash" and it.get("mode") == "pool"
        if not (cap or pool) or it["f"][1] <= a or it["f"][0] >= z:
            continue
        drop.append(it["id"])
        if it["f"][0] < a:
            items.append(cut(it, it["f"][0], a, new_id=f"{it['id']}~a"))
        if it["f"][1] > z:
            items.append(cut(it, z, it["f"][1], new_id=f"{it['id']}~z"))
    return items, drop


def hard_steps(n, k, last=None):
    """``k`` change frames over ``n``: fast first, slowing hard into the end (a counter that lands)."""
    if k <= 1 or n <= 1:
        return [0]
    w = [1.0 + 2.6 * (i / (k - 1)) ** 2.2 for i in range(k)]
    tot = sum(w)
    out, acc = [], 0.0
    for i in range(k):
        out.append(int(round(acc / tot * n)))
        acc += w[i]
    out = sorted(set(min(max(v, 0), n - 1) for v in out))
    return out


def punch(n, size=0.12, tau=2.4):
    """A scale that lands large and settles to 1 (the word hits)."""
    return [round(1.0 + size * math.exp(-i / tau), 5) for i in range(n)]


def ease_out(n, a, b, frames=None):
    m = max(int(frames or n), 1)
    return [round(a + (b - a) * (1 - (1 - min(i / m, 1.0)) ** 3), 6) for i in range(n)]
