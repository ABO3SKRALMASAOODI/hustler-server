"""Burned-in text, measured on the cut itself (WF1-LOOP round 7, 16 Nov 2026).

Why. The owner's podcast shorts of 16 Nov carried other channels' subtitles ("...setting a new standard in
computing infrastructure", "99.99% high availability") and burned-in promo labels ("VOLUME FACTORY") under
Valmera's own captions. The vision look sees one frame per moment and a subtitle is up for part of a clip only,
so a clean scout frame admitted a cut that is dirty a second later.

What is measured, never read. Text lines are found in several frames of the cut by their stroke structure
(top-hat and black-hat response, glyphs closed into horizontal lines, the same geometry ``worker/inpaint``
uses to erase burned captions). Text that belongs to the picture (a sign, a livery, a printed page) moves with
the scene. Text an editor laid over the picture does not: it stays fixed in the frame while the scene changes
around it, or its words change while it stays in the same place. Those two signals decide; no word list, no OCR,
no model here: the measurement is the evidence a model's look is checked against (see the repair below).

  subtitles  a line seated at the same place in the frame whose glyphs change between frames
  label      a line seated at the same place whose glyphs hold while the picture around it changes

A text that holds still over a still picture (a sign in a locked-off shot) cannot be told from a burned label by
signal alone; it is reported as ``still_text`` and not rejected here (the vision checks judge it).
"""
import cv2
import numpy as np

VERSION = 1
SAMPLES = 8                 # frames read across a 1-3 s cut
WIDTH = 640                 # analysis width; text-line geometry is in frame fractions
MIN_LINE_H, MAX_LINE_H = 0.018, 0.16
MIN_LINE_W, MIN_ASPECT = 0.06, 2.0
MAX_INK_FRAC = 0.22
SEATED_IOU = 0.5            # the same line box in another frame
SEATED_SHARE = 0.5          # ... in at least this share of the frames read
GLYPHS_HOLD = 0.55          # ink IoU inside a seated box: the same glyphs
SCENE_MOVED = 9.0           # mean abs grey change (0-255) round a seated box: the picture changed
SUBS_CHANGE = 0.35          # ink IoU at or under this between frames where the scene round it held: new words
MIN_AREA = 0.004            # a seated line under this share of the frame is a corner bug or a channel mark


def _odd(n, lo=3):
    n = max(lo, int(round(n)))
    return n if n % 2 else n + 1


def frames(path, n=SAMPLES, width=WIDTH):
    """n frames spread across the cut (never on its first or last frame), resized to ``width``."""
    cap = cv2.VideoCapture(path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        out = []
        if total <= 0:
            return out
        idx = sorted({min(total - 1, int(total * (0.06 + 0.88 * k / max(n - 1, 1)))) for k in range(n)})
        for i in idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, f = cap.read()
            if not ok or f is None:
                continue
            h, w = f.shape[:2]
            if w != width:
                f = cv2.resize(f, (width, max(2, int(round(h * width / w)))), interpolation=cv2.INTER_AREA)
            out.append(f)
        return out
    finally:
        cap.release()


def _ink(gray):
    h = gray.shape[0]
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (_odd(h * 0.018), _odd(h * 0.018)))
    resp = cv2.max(cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k), cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k))
    resp = cv2.GaussianBlur(resp, (3, 3), 0)
    if float(resp.max()) < 30:
        return np.zeros_like(gray)
    _, b = cv2.threshold(resp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b[resp < 24] = 0
    if b.mean() > MAX_INK_FRAC * 255:
        return np.zeros_like(gray)
    return b


def _strokes(patch):
    """Ink runs across a line's middle third (the median over its rows): about one or two a letter in text."""
    h = patch.shape[0]
    rows = patch[h // 3:max(h // 3 + 1, 2 * h // 3)] > 0
    if not rows.size:
        return 0.0
    runs = np.count_nonzero(rows[:, 1:] & ~rows[:, :-1], axis=1) + rows[:, 0]
    return float(np.median(runs))


def lines(bgr):
    """(ink mask, [text-line boxes in frame fractions]) of one frame."""
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    b = _ink(gray)
    if not b.any():
        return b, []
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (_odd(W * 0.035, 9), _odd(H * 0.006, 3)))
    closed = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    boxes = []
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (MIN_LINE_H * H <= h <= MAX_LINE_H * H) or w < MIN_LINE_W * W or w > 0.99 * W or w / float(h) < MIN_ASPECT:
            continue
        dens = float(b[y:y + h, x:x + w].mean()) / 255.0
        if not 0.10 <= dens <= 0.9:
            continue
        # glyphs: a line of text is many strokes across, not one long edge (a roofline, a table edge). Counted as ink
        # runs along the line's middle rows, so heavy type whose letters touch still counts its strokes.
        if _strokes(b[y:y + h, x:x + w]) < max(3.0, 0.3 * w / max(h, 1)):
            continue
        boxes.append((x / W, y / H, (x + w) / W, (y + h) / H))
    return b, boxes


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def _px(box, W, H, pad=0.0):
    x0, y0, x1, y1 = box
    return (max(0, int((x0 - pad) * W)), max(0, int((y0 - pad) * H)), min(W, int(np.ceil((x1 + pad) * W))), min(H, int(np.ceil((y1 + pad) * H))))


def _mask_iou(a, b):
    a, b = a > 0, b > 0
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / u if u else 1.0


def measure_frames(fs, view=None):
    """Seated text in a list of frames. view: [x0, x1] of the frame that will be seen (a 9:16 slice); None, all.
    Returns {"version", "frames", "seated": [{kind, box, share, glyphs_hold, scene_change}], "overlay": bool}."""
    rec = dict(version=VERSION, frames=len(fs), seated=[], overlay=False)
    if len(fs) < 3:
        return rec
    H, W = fs[0].shape[:2]
    per = [lines(f) for f in fs]
    greys = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.int16) for f in fs]
    clusters = []
    for k, (_ink_k, bx) in enumerate(per):
        for b in bx:
            home = next((c for c in clusters if _iou(c["box"], b) >= SEATED_IOU), None)
            if home is None:
                clusters.append(dict(box=b, at={k}))
            else:
                home["at"].add(k)
                home["box"] = (min(home["box"][0], b[0]), min(home["box"][1], b[1]), max(home["box"][2], b[2]), max(home["box"][3], b[3]))
    need = max(2, int(np.ceil(SEATED_SHARE * len(fs))))
    for c in clusters:
        box = c["box"]
        area = (box[2] - box[0]) * (box[3] - box[1])
        if len(c["at"]) < need or area < MIN_AREA:
            continue
        if view is not None and (min(box[2], view[1]) - max(box[0], view[0])) < 0.25 * (box[2] - box[0]):
            continue
        x0, y0, x1, y1 = _px(box, W, H)
        X0, Y0, X1, Y1 = _px(box, W, H, pad=0.06)
        at = sorted(c["at"])
        holds, scene, quiet_changes = [], [], []
        for a, b in zip(at, at[1:]):
            g = _mask_iou(per[a][0][y0:y1, x0:x1], per[b][0][y0:y1, x0:x1])
            ring = np.abs(greys[a][Y0:Y1, X0:X1] - greys[b][Y0:Y1, X0:X1]).astype(np.float32)
            ring[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = np.nan
            ch = float(np.nanmean(ring)) if np.isfinite(ring).any() else 0.0
            holds.append(g)
            scene.append(ch)
            if ch < SCENE_MOVED:
                quiet_changes.append(g)
        if not holds:
            continue
        g_med, s_med = float(np.median(holds)), float(np.median(scene))
        if quiet_changes and min(quiet_changes) <= SUBS_CHANGE:
            kind = "subtitles"            # new words in the same seat over a picture that held
        elif g_med >= GLYPHS_HOLD and s_med >= SCENE_MOVED:
            kind = "label"                # the same words in the same seat while the picture moved under them
        elif g_med < GLYPHS_HOLD and s_med >= SCENE_MOVED and len(at) >= len(fs) - 1:
            kind = "subtitles"            # a seat held through every frame while both its words and the scene changed
        else:
            kind = "still_text"
        rec["seated"].append(dict(kind=kind, box=[round(v, 4) for v in box], share=round(len(at) / len(fs), 3),
                                  glyphs_hold=round(g_med, 3), scene_change=round(s_med, 2)))
    rec["overlay"] = any(s["kind"] in ("subtitles", "label") for s in rec["seated"])
    return rec


def view_of(x, w, h, aspect=9 / 16):
    """[x0, x1] of a w x h picture that a cover crop of ``aspect`` centred across on x shows."""
    if not w or not h:
        return None
    vw = min(1.0, aspect * float(h) / float(w))
    if vw >= 0.999:
        return None
    cx = min(max(float(x if x is not None else 0.5), vw / 2), 1 - vw / 2)
    return [cx - vw / 2, cx + vw / 2]


def measure(path, x=None, aspect=None, n=SAMPLES):
    """Burned-in text of one cut file (see the module doc). x and aspect: the slice the film will show."""
    fs = frames(path, n=n)
    view = None
    if fs and aspect:
        h, w = fs[0].shape[:2]
        view = view_of(x, w, h, aspect)
    return measure_frames(fs, view)


# ---------------------------------------------------------------- the repair: keep the picture, lose the other editor's text
#
# A model that looked at the cut (the podcast shorts' placement check) says where another editor's text sits. The text
# lines measured above that touch its box give the exact edges. The clip is then cut down to the largest band of the
# picture clear of that text that can still fill the phone within the upscale limit, nearest the subject. The clip
# is kept and re-checked, never thrown away for a subtitle it can lose.

PAD = 0.015


def _touch(a, b, pad=0.02):
    return a[0] < b[2] + pad and a[2] > b[0] - pad and a[1] < b[3] + pad and a[3] > b[1] - pad


def text_boxes(model_box, seated, pad=0.02):
    """The other editor's text in frame fractions: the model's box grown to every measured text line it touches."""
    if not model_box:
        return []
    box = [float(v) for v in model_box]
    grown = True
    while grown:
        grown = False
        for s in seated or []:
            b = s["box"] if isinstance(s, dict) else s
            if _touch(box, b, pad) and not (b[0] >= box[0] and b[1] >= box[1] and b[2] <= box[2] and b[3] <= box[3]):
                box = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
                grown = True
    return [[round(min(max(v, 0.0), 1.0), 4) for v in box]]


def _free(spans):
    """Intervals of [0, 1] not covered by spans."""
    out, at = [], 0.0
    for a, b in sorted(spans):
        if a > at:
            out.append((at, a))
        at = max(at, b)
    if at < 1.0:
        out.append((at, 1.0))
    return out


def clear_rect(w, h, boxes, x=0.5, aspect=9 / 16, out_w=1080, out_h=1920, max_up=3.0):
    """[x0, y0, x1, y1] (fractions) of the largest full-width or full-height band of a w x h picture clear of boxes
    (frame fractions) that can still fill an out_w x out_h frame of this aspect within max_up, the band holding the
    subject (x) first. None when no band can."""
    if not boxes or not w or not h:
        return None
    rows = _free([(b[1] - PAD, b[3] + PAD) for b in boxes])
    cols = _free([(b[0] - PAD, b[2] + PAD) for b in boxes])
    cands = [(0.0, a, 1.0, b) for a, b in rows] + [(a, 0.0, b, 1.0) for a, b in cols]
    best, key = None, None
    for r in cands:
        rw, rh = (r[2] - r[0]) * w, (r[3] - r[1]) * h
        if rw <= 0 or rh <= 0 or rw / rh < aspect * 0.999:
            continue
        if max(out_w / rw, out_h / rh) > max_up:
            continue
        k = (r[0] <= x <= r[2], rw * rh)
        if key is None or k > key:
            best, key = r, k
    return [round(v, 4) for v in best] if best else None


def crop_size(w, h, rect):
    """(w, h) in pixels of what crop_clip writes for rect, the same arithmetic as its filter."""
    import math
    return (int(math.trunc(w * float(f"{rect[2] - rect[0]:.4f}") / 2) * 2), int(math.trunc(h * float(f"{rect[3] - rect[1]:.4f}") / 2) * 2))


def crop_clip(src, dst, rect, ffmpeg="ffmpeg"):
    """The clip cut down to rect (fractions), even-sized, every other property kept."""
    import subprocess
    x0, y0, x1, y1 = rect
    vf = (f"crop=trunc(iw*{x1 - x0:.4f}/2)*2:trunc(ih*{y1 - y0:.4f}/2)*2:trunc(iw*{x0:.4f}):trunc(ih*{y0:.4f}),setsar=1")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", src, "-vf", vf, "-an", "-c:v", "libx264", "-crf", "17",
                        "-preset", "medium", "-movflags", "+faststart", "-y", dst], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("crop failed: " + (r.stderr or "")[-300:])
    return dst


def remap(rect, value):
    """A point, a box or a face record of the whole picture, in the fractions of rect (None when it falls outside)."""
    x0, y0, x1, y1 = rect
    fx = lambda v: (v - x0) / (x1 - x0)   # noqa: E731
    fy = lambda v: (v - y0) / (y1 - y0)   # noqa: E731
    if isinstance(value, dict):
        b = value.get("box")
        if not b:
            return None
        nb = [fx(b[0]), fy(b[1]), fx(b[2]), fy(b[3])]
        cw = min(nb[2], 1) - max(nb[0], 0)
        ch = min(nb[3], 1) - max(nb[1], 0)
        if cw <= 0.5 * (nb[2] - nb[0]) or ch <= 0.5 * (nb[3] - nb[1]):
            return None
        out = dict(value, box=[round(v, 4) for v in nb])
        if value.get("eyes"):
            out["eyes"] = [[round(fx(e[0]), 4), round(fy(e[1]), 4)] for e in value["eyes"]]
        return out
    return round(min(max(fx(float(value)), 0.0), 1.0), 4)
