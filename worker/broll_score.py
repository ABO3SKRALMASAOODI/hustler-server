"""Track 4C: the cinematic scorer (ROADMAP 4C "Cinematic scorer").

Judges one B-roll candidate from a few of its frames (the executor's review
frames, or frames decoded locally) and its probe facts, against the hero
frame of the short. Pure numpy/OpenCV: it runs on every lane (agent, MCP,
Shorts — no models, no browser). Every measure is reported; a hard reject
says why in words.

Measures (0..1 unless noted):

  res_eff         output pixels the picture really has after the fit: the
                  cover crop's short side over the frame's (1.0 = native);
                  under REJECT_RES it would be a soft upscale
  crop_survival   share of the picture's saliency (spectral residual, plus
                  faces) inside the best output-aspect window — a wide shot
                  whose subject sits at the edge does not survive 9:16;
                  ``focus_x``/``focus_y`` aim that window
  motion          the clip's motion profile (motion_judge) read as a
                  cinematic score: gentle, continuous camera or subject
                  movement wins; frozen, shaky or strobing loses
  dof             depth-of-field estimate: sharpness of the salient region
                  over the rest (a shallow-focus shot reads cinematic)
  dyn_range       luma spread P2..P98 with a clipping penalty (flat or
                  burnt pictures lose)
  de_hero         mean Lab dE2000 to the hero frame before matching, and
                  ``de_matched`` after the matched LUT (what the de-stocking
                  grade will land on)
  watermark       a logo or handle burned into the picture: text lines or
                  hard edges that stay put in every frame while the picture
                  changes, or text lines in a corner of a still
                  (inpaint._line_mask); any hit rejects
  bpp             bits per pixel per frame (bytes x 8 / duration / w / h /
                  fps): a starved encode rejects under REJECT_BPP
  blockiness      8x8 block-edge energy over in-block energy, measured only
                  on native-resolution frames
  eyes_to_camera  a frontal face looking into the lens, large in frame: the
                  posed-stock tell ("stock cheese")
  siglip          the SigLIP stock-cheese term: None here (the vision lane
                  is dark; ROADMAP §10) — it scores 0, never guessed
"""

import math

import numpy as np

# Hard floors.
REJECT_RES = 0.36          # a 720p landscape clip cropped to 9:16 is 0.375
REJECT_RES_ARCHIVAL = 0.22
REJECT_BPP = 0.018         # bits / pixel / frame when motion is unmeasured
REJECT_JPEG_BPP = 0.55     # bits / pixel for a still (heavy JPEG)
REJECT_BLOCKY = 1.6
REJECT_SURVIVAL = 0.30
# Weights of the soft score (they sum to 1).
WEIGHTS = {"res_eff": 0.16, "crop_survival": 0.16, "motion": 0.14,
           "dof": 0.10, "dyn_range": 0.12, "palette": 0.14, "clean": 0.10,
           "cheese": 0.08}
ANALYSIS_W = 480


def _gray(rgb):
    a = np.asarray(rgb, np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _small(rgb, width=ANALYSIS_W):
    import cv2
    h, w = rgb.shape[:2]
    if w <= width:
        return np.asarray(rgb)
    k = width / float(w)
    return cv2.resize(np.asarray(rgb), (width, max(2, int(round(h * k)))),
                      interpolation=cv2.INTER_AREA)


# ── saliency and the output-aspect window ────────────────────────────────

def saliency(gray):
    """Spectral-residual saliency (Hou & Zhang 2007) on a 64-px-high map,
    resized back and normalised to sum 1."""
    import cv2
    h, w = gray.shape
    sh = 64
    sw = max(8, int(round(w * sh / float(h))))
    g = cv2.resize(gray.astype(np.float32), (sw, sh),
                   interpolation=cv2.INTER_AREA)
    f = np.fft.fft2(g)
    amp = np.log(np.abs(f) + 1e-6)
    ph = np.angle(f)
    resid = amp - cv2.blur(amp, (3, 3))
    s = np.abs(np.fft.ifft2(np.exp(resid + 1j * ph))) ** 2
    s = cv2.GaussianBlur(s.astype(np.float32), (0, 0), 2.5)
    # a centre prior: photographers frame the subject inward
    yy, xx = np.mgrid[0:sh, 0:sw]
    prior = np.exp(-(((xx - sw / 2.0) / (0.6 * sw)) ** 2
                     + ((yy - sh / 2.0) / (0.6 * sh)) ** 2))
    s = s * (0.5 + 0.5 * prior)
    s = cv2.resize(s, (w, h), interpolation=cv2.INTER_LINEAR)
    tot = float(s.sum())
    return s / tot if tot > 0 else np.full_like(s, 1.0 / s.size)


def best_window(sal, aspect, faces=()):
    """(share, fx, fy): the output-aspect window (largest of that aspect)
    holding the most saliency, every face box inside counted double."""
    h, w = sal.shape
    m = sal.copy()
    for (x, y, fw, fh) in faces or ():
        x0, y0 = max(0, int(x)), max(0, int(y))
        m[y0:y0 + int(fh), x0:x0 + int(fw)] *= 3.0
    m = m / max(1e-12, float(m.sum()))
    if w / float(h) > aspect:                         # slide horizontally
        cw = max(2, int(round(h * aspect)))
        col = m.sum(axis=0)
        cs = np.concatenate([[0.0], np.cumsum(col)])
        sums = cs[cw:] - cs[:-cw]
        x = int(np.argmax(sums))
        share = float(sums[x])
        fx = (x + cw / 2.0) / w
        return share, fx, 0.5
    ch = max(2, int(round(w / aspect)))
    row = m.sum(axis=1)
    cs = np.concatenate([[0.0], np.cumsum(row)])
    sums = cs[ch:] - cs[:-ch]
    y = int(np.argmax(sums))
    return float(sums[y]), 0.5, (y + ch / 2.0) / h


def res_eff(w, h, W, H, fit="cover"):
    """Short side of the picture actually shown, over the frame's need."""
    if not (w and h and W and H):
        return 0.0
    if fit == "cover":
        k = max(W / float(w), H / float(h))
    else:
        k = min(W / float(w), H / float(h))
        if fit == "frame":
            k *= 0.94
    return float(min(1.0, 1.0 / k)) if k > 0 else 0.0


# ── faces ────────────────────────────────────────────────────────────────

def faces(rgb):
    """[(x, y, w, h, frontal_score)] in rgb pixels: YuNet when the model is
    configured (landmark symmetry = looking into the lens), else OpenCV's
    frontal Haar cascade (frontal by construction, score 0.8)."""
    import cv2
    import config
    bgr = np.ascontiguousarray(np.asarray(rgb)[..., ::-1])
    h, w = bgr.shape[:2]
    path = getattr(config, "YUNET_MODEL", None)
    out = []
    if path:
        try:
            import os
            if os.path.exists(path):
                det = cv2.FaceDetectorYN.create(path, "", (w, h), 0.7)
                _n, got = det.detect(bgr)
                for f in (got if got is not None else []):
                    x, y, fw, fh = [float(v) for v in f[:4]]
                    le, re_, nose = f[4:6], f[6:8], f[8:10]
                    mid = (le[0] + re_[0]) / 2.0
                    eye = max(1.0, abs(re_[0] - le[0]))
                    off = abs(nose[0] - mid) / eye     # 0 = straight on
                    out.append((x, y, fw, fh,
                                float(max(0.0, 1.0 - off * 2.5))))
                return out
        except Exception:  # noqa: BLE001 — fall back to Haar
            out = []
    try:
        casc = cv2.CascadeClassifier(cv2.data.haarcascades
                                     + "haarcascade_frontalface_default.xml")
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in casc.detectMultiScale(
                g, 1.15, 6, minSize=(max(16, w // 24), max(16, w // 24))):
            out.append((float(x), float(y), float(fw), float(fh), 0.8))
    except Exception:  # noqa: BLE001
        pass
    return out


# ── watermarks, text and compression ─────────────────────────────────────

def _line_mask(rgb):
    import cv2
    import inpaint
    bgr = cv2.cvtColor(np.asarray(rgb, np.uint8), cv2.COLOR_RGB2BGR)
    return inpaint._line_mask(bgr) > 0


def _edges(gray):
    import cv2
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy)


def _type_like(gray, box):
    """Is a line box TYPE (0..1)? Type is ink of one flat tone (white or
    black, the way marks are set) standing off its surround, broken by
    letter gaps into a comb of strokes; a cloud rim, a horizon or a
    texture has neither the flat ink nor the gaps."""
    import cv2
    x, y, w, h = box
    g = np.asarray(gray, np.float32)
    crop = g[y:y + h, x:x + w]
    if crop.size == 0 or h < 5 or w < 10:
        return 0.0
    c8 = np.clip(crop, 0, 255).astype(np.uint8)
    _t, b = cv2.threshold(c8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = b > 0
    best = 0.0
    for ink in (bright, ~bright):
        frac = float(ink.mean())
        if not 0.08 <= frac <= 0.55:
            continue
        iv, bv = crop[ink], crop[~ink]
        if iv.size < 8 or bv.size < 8:
            continue
        flat = float(iv.std())
        contrast = abs(float(iv.mean()) - float(bv.mean()))
        if flat > 28.0 or contrast < 55.0:
            continue
        # letter gaps: columns of the middle band with no ink, in runs
        midband = ink[h // 5: max(h // 5 + 1, 4 * h // 5)]
        colink = midband.mean(axis=0) > 0.08
        gaps = int(np.count_nonzero(~colink[1:] & colink[:-1]))
        per = gaps / max(1.0, w / float(h))
        if per < 0.6:
            continue
        best = max(best, min(1.0, 0.35 + 0.25 * per + contrast / 400.0
                             - flat / 200.0))
    return best


def _text_boxes(rgb):
    """[(x, y, w, h, type_like)] of text-line components of one frame."""
    import cv2
    m = _line_mask(rgb).astype(np.uint8)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    g = _gray(rgb)
    out = []
    for i in range(1, n):
        x, y, w, h, _a = stats[i]
        out.append((int(x), int(y), int(w), int(h),
                    _type_like(g, (x, y, w, h))))
    return out


# A still's corner band (fractions) where credit lines, handles and stock
# logos sit, and what a line there must look like to count as type.
CORNER_Y, CORNER_X = 0.16, 0.40
TYPE_LIKE = 0.5


def _still_mark(rgb):
    """(score, box) of a credit line / handle / logo type in a corner band
    of a still: a text line clear of the frame edge, set bright with real
    contrast, with dense strokes."""
    h, w = rgb.shape[:2]
    m = max(2, int(0.004 * max(w, h)))
    g = _gray(rgb)
    ed = _edges(g)
    best, box = 0.0, None
    for x, y, bw, bh, _tl in _text_boxes(rgb):
        cy = (y + bh / 2.0) / h
        cx = (x + bw / 2.0) / w
        corner = (cy < CORNER_Y or cy > 1 - CORNER_Y) and (
            cx < CORNER_X or cx > 1 - CORNER_X)
        if not corner:
            continue
        if x <= m or y <= m or x + bw >= w - m or y + bh >= h - m:
            continue                        # a frame edge, a scan border
        if not (0.012 * h <= bh <= 0.07 * h) or bw > 0.42 * w:
            continue
        if bw / float(bh) < 2.2:
            continue
        lo, hi = np.percentile(g[y:y + bh, x:x + bw], [10, 90])
        if hi < 150.0 or hi - lo < 80.0:
            continue
        if float((ed[y:y + bh, x:x + bw] > 60.0).mean()) < 0.18:
            continue
        sc = min(1.0, 0.5 + (hi - lo) / 400.0)
        if sc > best:
            best, box = sc, [round(x / w, 4), round(y / h, 4),
                             round(bw / w, 4), round(bh / h, 4)]
    return best, box


WM_W = 768                 # the executor's review-frame width
MOVING_TSTD = 8.0          # a pixel that changes across the samples
LOCKED_MOVING = 0.35       # under this share of moving pixels: locked off


def _in_corner(x, y, w, h, W, H):
    cx, cy = (x + w / 2.0) / W, (y + h / 2.0) / H
    return (cy < 0.18 or cy > 0.82) and (cx < 0.30 or cx > 0.70)


def _pinned(g, x, y, bw, bh, search=12, edges=None):
    """(pinned, surround_moves) for a box across the samples: ``pinned``
    when its patch matches itself best at zero offset between every pair
    of samples (an overlay is welded to the frame; scene content drifts
    with the slowest pan); ``surround_moves`` when the ring around it
    matches best elsewhere or not at all in some pair."""
    import cv2
    H, W = g.shape[1:]
    p = 3
    x0, y0 = max(0, x - p), max(0, y - p)
    x1, y1 = min(W, x + bw + p), min(H, y + bh + p)
    big = max(bw, bh)
    rx0, ry0 = max(0, x - big), max(0, y - big)
    rx1, ry1 = min(W, x + bw + big), min(H, y + bh + big)
    pinned, moves = True, False
    em = edges if edges is not None else g
    for k, (a, b) in enumerate(zip(g, g[1:])):
        # the patch is matched on its EDGES: a semi-transparent mark's
        # pixels change with the picture behind it, its outline does not
        ea, eb = em[k].astype(np.float32), em[k + 1].astype(np.float32)
        tpl = ea[y0:y1, x0:x1]
        sx0, sy0 = max(0, x0 - search), max(0, y0 - search)
        sx1, sy1 = min(W, x1 + search), min(H, y1 + search)
        reg = eb[sy0:sy1, sx0:sx1]
        if tpl.std() < 2.0 or reg.shape[0] < tpl.shape[0] or \
                reg.shape[1] < tpl.shape[1]:
            return False, False
        r = cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _ml, loc = cv2.minMaxLoc(r)
        dx, dy = loc[0] + sx0 - x0, loc[1] + sy0 - y0
        # welded: the best match sits at zero offset and stands out of the
        # surface (when the picture behind changed completely, only the
        # mark's own outline correlates, weakly but only there)
        if abs(dx) > 1 or abs(dy) > 1 or mx < 0.25 or \
                mx - float(np.median(r)) < 0.15:
            pinned = False
            break
        # the surround: the ring box without the patch, as its own template
        rt = a[ry0:ry1, rx0:rx1].copy()
        rt[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = rt.mean()
        qx0, qy0 = max(0, rx0 - search), max(0, ry0 - search)
        qx1, qy1 = min(W, rx1 + search), min(H, ry1 + search)
        rr = b[qy0:qy1, qx0:qx1].copy()
        ox, oy = x0 - qx0, y0 - qy0
        rr[oy:oy + (y1 - y0), ox:ox + (x1 - x0)] = rt.mean()
        if rt.std() < 2.0:
            continue
        r2 = cv2.matchTemplate(rr, rt, cv2.TM_CCOEFF_NORMED)
        _mn, m2, _ml, l2 = cv2.minMaxLoc(r2)
        ddx, ddy = l2[0] + qx0 - rx0, l2[1] + qy0 - ry0
        if abs(ddx) > 1 or abs(ddy) > 1 or m2 < 0.85:
            moves = True
    return pinned, moves


def watermark(frames, still=False):
    """(score 0..1, box fractions or None, why).

    Clip (3+ samples across it):
      * persistent type — a line of text present in every sample at the
        same place whose ink does not change (a handle, a URL, a channel
        name, a stock brand, a burned-in show logo); in a locked-off shot
        only inside the corner bands (where scene text rarely sits and
        bugs always do);
      * a static island — hard edges that do not change while the picture
        around them moves (a shape logo, a bug), only in a moving shot,
        and only when its surround really moves (a locked-off background
        stays put too).
    Still: a line of flat-toned type in a corner band (conservative: most
    libraries here never watermark, and a false hit throws away a good
    photo)."""
    import cv2
    fr = [_small(f, WM_W) for f in frames if f is not None]
    if not fr:
        return 0.0, None, "no frames"
    h, w = fr[0].shape[:2]
    fr = [f if f.shape[:2] == (h, w) else
          cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA) for f in fr]
    if still or len(fr) < 3:
        sc, box = _still_mark(_small(fr[0]))
        if sc >= 0.35:
            return sc, box, "a line of type in a corner of the picture"
        return 0.0, None, "clean"
    g = np.stack([_gray(f) for f in fr]).astype(np.float32)
    tstd = g.std(axis=0)
    moving = float((tstd > MOVING_TSTD).mean())
    locked = moving < LOCKED_MOVING
    ed = np.stack([_edges(x) for x in g])
    emin = np.min(ed, axis=0)
    calm = max(6.0, 0.5 * float(np.percentile(tstd, 75)))
    cands = []
    # persistent type: a text line in at least half the samples
    votes = np.sum([_line_mask(f) for f in fr], axis=0)
    pers = votes >= max(2, int(math.ceil(len(fr) / 2.0)))
    n, _lab, st, _c = cv2.connectedComponentsWithStats(
        pers.astype(np.uint8), 8)
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if area < 0.00025 * w * h or bw > 0.45 * w or bh > 0.14 * h:
            continue
        ink = emin[y:y + bh, x:x + bw] > 30.0
        if ink.sum() < 12 or ink.mean() < 0.08:
            continue
        # marks are set to read: a bright face with real contrast (white or
        # grey type, often with a dark outline); dark scene detail is not
        lo, hi = np.percentile(g[:, y:y + bh, x:x + bw].mean(axis=0),
                               [10, 90])
        if hi < 130.0 or hi - lo < 70.0:
            continue
        cands.append(("type", int(x), int(y), int(bw), int(bh), int(area)))
    # pinned cells: a grid over the persistent edges, each cell asked
    # whether it is welded to the frame while its surround moves (a shape
    # logo, a bug, a semi-transparent mark: its outline stays put even
    # when the picture shows through). Cells, not components: on a dolly
    # past server racks the scene's persistent edges merge into one blob
    # that would swallow the mark.
    if not locked:
        cw, ch = max(16, w // 12), max(10, h // 18)
        hit = np.zeros((h, w), np.uint8)
        for y in range(0, h - ch + 1, ch // 2):
            for x in range(0, w - cw + 1, cw // 2):
                if (emin[y:y + ch, x:x + cw] > 40.0).mean() < 0.06:
                    continue
                pinned, around = _pinned(g, x, y, cw, ch, edges=ed)
                if pinned and around:
                    hit[y:y + ch, x:x + cw] = 1
        n, _lab, st, _c = cv2.connectedComponentsWithStats(hit, 8)
        hsv = [cv2.cvtColor(f, cv2.COLOR_RGB2HSV) for f in fr]
        for i in range(1, n):
            x, y, bw, bh, area = st[i]
            # a mark covers a few cells together; one or two pinned cells
            # are a vanishing point or a planet's limb
            if area > 0.15 * w * h or area < 3 * cw * ch * 0.95:
                continue
            ink = emin[y:y + bh, x:x + bw] > 40.0
            dens = float(ink.mean())
            val = float(np.mean([hh[y:y + bh, x:x + bw, 2][ink].mean()
                                 for hh in hsv])) / 255.0 if ink.any() \
                else 0.0
            # type or a logo has dense strokes; a flat shape logo is bright
            if dens < 0.10 and val < 0.85:
                continue
            cands.append(("cell", int(x), int(y), int(bw), int(bh),
                          int(ink.sum())))
    cands.sort(key=lambda c: -c[5])
    best, box, why = 0.0, None, "clean"
    for kind, x, y, bw, bh, area in cands[:20]:
        if kind == "cell":
            pinned, around = True, True    # established cell by cell
        else:
            pinned, around = _pinned(g, x, y, bw, bh, edges=ed)
        if not pinned:
            continue                       # it moves with the picture
        if not around:
            # the picture around it does not move either (a locked-off
            # shot or a static part of the scene): only type in a corner
            # band counts — where bugs sit and scene text rarely does
            if kind != "type" or not _in_corner(x, y, bw, bh, w, h):
                continue
        sc = min(1.0, (0.55 if kind == "type" else 0.45)
                 + area / (0.004 * w * h))
        if kind == "cell":
            sc = min(1.0, 0.45 + area / (0.0015 * w * h))
        if sc > best:
            best, box = sc, (x / w, y / h, bw / w, bh / h)
            why = ("type that stays put on every sample" if kind == "type"
                   else "a mark that stays put while the picture moves")
    if best >= 0.35:
        return best, [round(v, 4) for v in box], why
    return 0.0, None, "clean"


def _box(xs, ys, w, h):
    return [round(xs.min() / w, 4), round(ys.min() / h, 4),
            round((xs.max() - xs.min() + 1) / w, 4),
            round((ys.max() - ys.min() + 1) / h, 4)]


def bpp(meta):
    """Bits per pixel (per frame for a clip), or None."""
    try:
        b = float(meta.get("bytes") or 0)
        w, h = float(meta.get("width") or 0), float(meta.get("height") or 0)
        if not (b and w and h):
            return None
        if meta.get("kind") == "still":
            return b * 8.0 / (w * h)
        d = float(meta.get("duration_s") or 0)
        fps = float(meta.get("fps") or 0)
        if not (d and fps):
            return None
        return b * 8.0 / (d * fps * w * h)
    except (TypeError, ValueError):
        return None


def bpp_floor(profile):
    """The bits a clip needs per pixel per frame before it falls apart:
    a still picture compresses to almost nothing honestly, movement
    needs bits (0.010 for a locked shot .. 0.034 for a busy one)."""
    try:
        m = float((profile or {}).get("mean_frame_change") or 0.0)
    except (TypeError, ValueError, AttributeError):
        m = 0.0
    if not profile:
        return REJECT_BPP
    return 0.010 + 0.004 * max(0.0, min(6.0, m))


def blockiness(gray):
    """8x8 block-boundary gradient energy over the in-block energy (1.0 =
    no grid; heavy MPEG blocking > 1.6). Native-resolution frames only."""
    g = np.asarray(gray, np.float32)
    dx = np.abs(np.diff(g, axis=1))
    cols = np.arange(dx.shape[1])
    on = dx[:, (cols % 8) == 7].mean()
    off = dx[:, (cols % 8) != 7].mean()
    dy = np.abs(np.diff(g, axis=0))
    rows = np.arange(dy.shape[0])
    on_y = dy[(rows % 8) == 7, :].mean()
    off_y = dy[(rows % 8) != 7, :].mean()
    return float(0.5 * (on / max(1e-3, off) + on_y / max(1e-3, off_y)))


# ── picture qualities ────────────────────────────────────────────────────

def dyn_range(gray):
    p2, p98 = np.percentile(gray, [2, 98])
    spread = (p98 - p2) / 255.0
    clip = float(((gray >= 252) | (gray <= 3)).mean())
    return float(max(0.0, min(1.0, (spread - 0.25) / 0.5))
                 * (1.0 - min(1.0, clip * 4.0))), round(float(spread), 3), \
        round(clip, 4)


def dof(gray, sal):
    """Sharpness of the salient region over the rest: 0 (deep, flat) ..
    1 (a subject crisp against a soft background)."""
    import cv2
    lap = np.abs(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F))
    lap = cv2.blur(lap, (9, 9))
    thr = np.quantile(sal, 0.8)
    a = float(lap[sal >= thr].mean()) if (sal >= thr).any() else 0.0
    b = float(lap[sal < np.quantile(sal, 0.5)].mean()) \
        if (sal < np.quantile(sal, 0.5)).any() else a
    ratio = a / max(1e-3, b)
    return float(max(0.0, min(1.0, (ratio - 1.2) / 2.8))), round(ratio, 2)


def motion_score(profile, still=False):
    """The motion profile as a cinematic score (stills score as moved: the
    cutaway gives them a 2.5D move)."""
    if still:
        return 0.6, "still (moved by the cutaway)"
    if not isinstance(profile, dict):
        return 0.5, "not measured"
    m = float(profile.get("mean_frame_change") or 0.0)
    freeze = float(profile.get("freeze_share") or 0.0)
    abrupt = float(profile.get("abrupt_changes_per_10s") or 0.0)
    blank = float(profile.get("blank_share") or 0.0)
    if blank > 0.3:
        return 0.0, "mostly blank"
    # gentle continuous motion (1.5-9 mean change) is the cinematic band
    if m < 0.6 or freeze > 0.6:
        s = 0.25
    elif m < 1.5:
        s = 0.55
    elif m <= 9.0:
        s = 1.0
    elif m <= 16.0:
        s = 0.6
    else:
        s = 0.3
    s *= max(0.2, 1.0 - 0.12 * max(0.0, abrupt - 1.0))
    return float(s), f"mean change {m:.1f}, {abrupt:.1f} cuts/10 s"


def palette_gap(frames, hero):
    """(dE before, dE after a matched LUT, lut) between the candidate's
    pixels and the hero's: lutlib's MKL transfer, limited like a shot
    match."""
    import lutlib
    if hero is None or not len(hero):
        return None, None, None
    # uint8 in: lutlib reads 0..255 from uint8 and 0..1 from floats
    src = np.concatenate([_small(f, 160).reshape(-1, 3) for f in frames]
                         ).astype(np.uint8)
    ref = np.concatenate([_small(f, 160).reshape(-1, 3) for f in hero]
                         ).astype(np.uint8)
    la = lutlib.rgb_to_lab(src.astype(float) / 255.0)
    lb = lutlib.rgb_to_lab(ref.astype(float) / 255.0)
    before = float(lutlib.delta_e2000(la.mean(axis=0), lb.mean(axis=0)))
    try:
        # the whole distribution, not the shared colours: a cutaway shares
        # almost no content with the camera, and the point is the WORLD it
        # sits in (tonality, white balance, saturation), not one wall
        lut = lutlib.match_lut(src, ref, method="mkl",
                               limit=MATCH_LIMIT_LAB, shared=False)
        after_px = lutlib.apply(lut, src / 255.0)
        lc = lutlib.rgb_to_lab(np.clip(after_px, 0, 1))
        after = float(lutlib.delta_e2000(lc.mean(axis=0), lb.mean(axis=0)))
    except Exception:  # noqa: BLE001
        lut, after = None, before
    return before, after, lut


# How far a match may push colour (Lab units). Wider than a shot match
# (finish_tools 40): a cutaway is pulled into the camera's world.
MATCH_LIMIT_LAB = 60.0


def noise_sigma(gray):
    """Fine-grain noise estimate (Immerkaer 1996) in 8-bit levels."""
    g = np.asarray(gray, np.float32)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    k = (g[:-2, :-2] - 2 * g[:-2, 1:-1] + g[:-2, 2:]
         - 2 * g[1:-1, :-2] + 4 * g[1:-1, 1:-1] - 2 * g[1:-1, 2:]
         + g[2:, :-2] - 2 * g[2:, 1:-1] + g[2:, 2:])
    return float(math.sqrt(math.pi / 2.0) * np.abs(k).mean() / 6.0)


def sharpness(gray):
    import cv2
    return float(cv2.Laplacian(np.asarray(gray, np.float32),
                               cv2.CV_32F).var())


# ── the score ────────────────────────────────────────────────────────────

def score(frames, meta, hero=None, W=1080, H=1920, fit="cover",
          archival=False, native=False):
    """Judge one candidate. ``frames``: 1-6 RGB arrays across the clip (a
    still: one); ``meta``: {kind video|still, width, height, fps,
    duration_s, bytes, motion_profile}; ``hero``: RGB frames of the short
    (None: no palette term); ``native``: frames are at the file's own
    resolution (blockiness is measured only then).

    Returns {score 0..1, reject [reasons], measures {...}, focus_x,
    focus_y, lut (matched, or None), grain, soften}."""
    frames = [np.asarray(f) for f in frames if f is not None]
    still = meta.get("kind") == "still"
    out = {"score": 0.0, "reject": [], "measures": {}, "focus_x": 0.5,
           "focus_y": 0.5, "lut": None, "grain": None, "soften": None}
    if not frames:
        out["reject"].append("no frames could be read")
        return out
    ms = out["measures"]
    w = int(meta.get("width") or frames[0].shape[1])
    h = int(meta.get("height") or frames[0].shape[0])
    aspect = W / float(H)
    small = [_small(f) for f in frames]
    mid = small[len(small) // 2]
    gmid = _gray(mid)

    # resolution and crop survival
    re_ = res_eff(w, h, W, H, fit)
    ms["res_eff"] = round(re_, 3)
    floor = REJECT_RES_ARCHIVAL if archival else REJECT_RES
    if re_ < floor:
        out["reject"].append(f"too small for the frame ({w}x{h} would be "
                             f"upscaled {1 / max(re_, 1e-3):.1f}x)")
    fc = faces(mid)
    sal = saliency(gmid)
    if fit == "cover":
        share, fx, fy = best_window(sal, aspect, [f[:4] for f in fc])
        if fc:
            # every face must survive the crop (never cut a head in half)
            sh, sw = gmid.shape
            cw = min(sw, sh * aspect)
            x0 = fx * sw - cw / 2.0
            inside = [x0 - 2 <= f[0] and f[0] + f[2] <= x0 + cw + 2
                      for f in fc]
            if not all(inside):
                share *= 0.6
    else:
        share, fx, fy = 1.0, 0.5, 0.5
    out["focus_x"], out["focus_y"] = round(fx, 4), round(fy, 4)
    ms["crop_survival"] = round(share, 3)
    if fit == "cover" and share < REJECT_SURVIVAL:
        out["reject"].append("the subject does not survive the vertical "
                             "crop")

    # motion, depth of field, range
    mot, mwhy = motion_score(meta.get("motion_profile"), still)
    ms["motion"] = round(mot, 3)
    ms["motion_note"] = mwhy
    d, ratio = dof(gmid, sal)
    ms["dof"], ms["dof_ratio"] = round(d, 3), ratio
    dr, spread, clip = dyn_range(gmid)
    ms["dyn_range"], ms["luma_spread"], ms["clipped"] = round(dr, 3), \
        spread, clip

    # palette vs the hero, and the matched LUT
    # the colour the viewer gets is the part the cover crop shows: the
    # match is learnt on that window (a sunset's sky outside a 9:16 crop
    # must not pull the grade)
    shown = small
    if fit == "cover":
        import broll_fx
        shown = [broll_fx.crop_aspect(f, aspect, (fx, fy))[0]
                 for f in small]
    before, after, lut = palette_gap(shown, hero) if hero is not None \
        else (None, None, None)
    ms["de_hero"] = None if before is None else round(before, 2)
    ms["de_matched"] = None if after is None else round(after, 2)
    out["lut"] = lut
    pal = 0.6 if before is None else float(
        max(0.0, min(1.0, 1.0 - (min(before, after or before) - 2.0)
                     / 18.0)))

    # watermark, compression
    # the mark is judged at review-frame size (768 px), not the analysis
    # size: a handle in a corner is a few pixels tall at 480
    wm, box, why = watermark([_small(f, WM_W) for f in frames], still=still)
    ms["watermark"], ms["watermark_box"] = round(wm, 3), box
    if wm >= 0.35:
        out["reject"].append(f"watermark or logo ({why})")
    b = bpp(dict(meta, kind="still" if still else "video"))
    ms["bpp"] = None if b is None else round(b, 4)
    if b is not None and not archival:
        if still and b < REJECT_JPEG_BPP:
            out["reject"].append(f"over-compressed still ({b:.2f} bits/px)")
        if not still and b < bpp_floor(meta.get("motion_profile")):
            out["reject"].append(f"low-bitrate encode ({b:.3f} bits/px "
                                 "per frame)")
    if native and not still:
        bl = float(np.median([blockiness(_gray(f)) for f in frames]))
        ms["blockiness"] = round(bl, 3)
        if bl > REJECT_BLOCKY and not archival:
            out["reject"].append(f"visible compression blocks ({bl:.2f})")
    else:
        ms["blockiness"] = None
    clean = 1.0 - wm

    # stock cheese: a big frontal face looking into the lens
    cheese = 0.0
    fa = float(gmid.size)
    for (x, y, fw, fh, frontal) in fc:
        big = min(1.0, (fw * fh) / fa / 0.04)
        cheese = max(cheese, frontal * big)
    ms["eyes_to_camera"] = round(cheese, 3)
    ms["siglip"] = None

    parts = {"res_eff": min(1.0, re_ / 0.9), "crop_survival": share,
             "motion": mot, "dof": d, "dyn_range": dr, "palette": pal,
             "clean": clean, "cheese": 1.0 - cheese}
    out["score"] = round(float(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)),
                         4)
    ms["parts"] = {k: round(float(v), 3) for k, v in parts.items()}

    # the de-stocking finish: grain up to the hero's, soften over-sharp
    if hero is not None and len(hero):
        hg = _gray(_small(np.asarray(hero[0])))
        hn, cn = noise_sigma(hg), noise_sigma(gmid)
        ms["noise_hero"], ms["noise_clip"] = round(hn, 2), round(cn, 2)
        out["grain"] = round(float(max(0.0, min(0.6, (hn - cn) / 6.0))), 3) \
            or None
        hs, cs = sharpness(hg), sharpness(gmid)
        ms["sharp_ratio"] = round(cs / max(1e-3, hs), 2)
        if cs > 2.2 * hs:
            out["soften"] = round(float(min(0.6, (cs / hs - 2.2) / 6.0
                                            + 0.15)), 3)
    return out
