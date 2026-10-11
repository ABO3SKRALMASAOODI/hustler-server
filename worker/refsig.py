"""Reference signatures: what a finished edit is made of, measured per frame.

ROADMAP Wave 0 track 0D. One decode of a reel at thumbnail size feeds every
detector, so a 20 s reference is profiled in a couple of seconds on CPU:

    cuts                hard cuts, with punctuation bursts (flash, negative,
                        threshold frames) bridged so they are not cuts
    negatives           tonally inverted frames (edges kept, intensity flipped)
    white_flashes       full-picture white frames, 1-3 frames long
    colour_flashes      full-picture single-colour frames, 1-3 frames long
    bw                  B&W frames and sections (runs >= 0.2 s)
    layout              full / letterbox / pillarbox / card / stack, with the
                        picture rect(s)
    text_boxes          OCR boxes (RapidOCR PP-OCRv4; optional, dev lanes)
    occluded_text       type partly hidden by a person (OCR + person matte)
    type_on_black       frames of type on a black plate, and their density
    strobe_sections     windows where cuts + punctuation run >= 6 per second
    flash_rate          Harding-style flashes per second (qa_metrics)

Shared with the product's "make it like this" (Wave 2, 2F): ``analyze``
returns a JSON-safe dict, every event in frames AND seconds.

Honest-off: OCR and the person matte are optional. Without RapidOCR or the
RVM model, ``text_boxes`` / ``occluded_text`` come back as
``{"available": False, "why": ...}``, never as an empty "nothing found".
Production agent lanes have neither, and nothing else here needs them.

The OCR models are pinned by sha256 (``OCR_MODELS``); a package whose model
files do not match is refused, so a silently upgraded wheel cannot change
the numbers a benchmark reports.
"""

import json
import math
import os
import subprocess

import numpy as np

import qa_metrics

# 2: take-level cut checks; 3: stroke-edge ink in occluded_text;
# 4: flash rate from qa_metrics.flash_risk over every 1 s window
SIG_VERSION = 4

# RapidOCR (github.com/RapidAI/RapidOCR, Apache-2.0) rapidocr-onnxruntime
# 1.4.4: the PaddleOCR PP-OCRv4 detector/recogniser (Apache-2.0) it bundles.
OCR_PACKAGE = ("rapidocr-onnxruntime", "1.4.4")
OCR_MODELS = {
    "models/ch_PP-OCRv4_det_infer.onnx":
        "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "models/ch_PP-OCRv4_rec_infer.onnx":
        "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
    "models/ch_ppocr_mobile_v2.0_cls_infer.onnx":
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}
# The person matte for occluded_text: the RVM file the worker already bakes
# (config.MATTE_RVM_MODEL; GPL-3.0 weights, server-side use only).
RVM_SHA256 = "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"

THUMB_W = 64            # correlation thumbnails
FEAT_W = 192            # feature frames


# ── decode + per-frame features ───────────────────────────────────────────

def _even(v):
    return max(2, int(round(v / 2.0)) * 2)


class Feats:
    """Per-frame features of one reel (or one rect of it)."""

    def __init__(self, path, rect=None, feat_w=FEAT_W):
        info = qa_metrics.probe(path)
        self.path = path
        self.fps = float(info["fps"] or 30.0)
        self.src_w, self.src_h = info["width"], info["height"]
        self.rect = list(rect) if rect else [0, 0, self.src_w, self.src_h]
        rw, rh = self.rect[2], self.rect[3]
        self.w = feat_w
        self.h = _even(rh * feat_w / float(rw))
        _, frames = qa_metrics.read_frames(path, rect=rect,
                                           size=(self.w, self.h))
        self.n = len(frames)
        self.frames = frames
        tw = THUMB_W
        th = max(4, int(round(rh * tw / float(rw))))
        self.thumb = np.stack([_resize_box(f, tw, th) for f in frames]) \
            if frames else np.zeros((0, th, tw, 3))
        rgb = self.thumb.astype(np.float32)
        self.gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + \
            0.114 * rgb[..., 2]
        self.grad = np.stack([_grad(g) for g in self.gray]) if self.n else \
            self.gray
        self.hist = np.stack([_hist(f) for f in frames]) if frames else \
            np.zeros((0, 64))
        # composition: 8x6 cell means of Y, Cb, Cr (blur- and
        # small-motion-tolerant; what a take keeps and a cut replaces)
        self.comp = np.stack([_ycc_stack(_resize_box(t, 8, 6))
                              for t in self.thumb]) if frames else \
            np.zeros((0, 6, 8, 3))
        stats = [_frame_stats(f) for f in frames]
        self.luma_mean = np.array([s[0] for s in stats])
        self.white_frac = np.array([s[1] for s in stats])
        self.black_frac = np.array([s[2] for s in stats])
        self.chroma_p75 = np.array([s[3] for s in stats])
        self.midtone_frac = np.array([s[4] for s in stats])
        self.texture = np.array([s[5] for s in stats])
        self.dom_hue = np.array([s[6] for s in stats])
        self.dom_share = np.array([s[7] for s in stats])
        self.chroma_p98 = np.array([s[8] for s in stats])

    def t(self, f):
        return round(f / self.fps, 3)


def _resize_box(img, w, h):
    """Area resize with numpy (integer box when it divides, else sampling of
    box means on a grid)."""
    a = np.asarray(img, np.float32)
    H, W = a.shape[:2]
    ys = np.linspace(0, H, h + 1).astype(int)
    xs = np.linspace(0, W, w + 1).astype(int)
    cs = np.cumsum(np.cumsum(np.pad(a.astype(np.float64),
                                    ((1, 0), (1, 0), (0, 0))), 0), 1)
    y0, y1 = ys[:-1], ys[1:]
    x0, x1 = xs[:-1], xs[1:]
    s = (cs[y1][:, x1] - cs[y0][:, x1] - cs[y1][:, x0] + cs[y0][:, x0])
    area = ((y1 - y0)[:, None] * (x1 - x0)[None, :]).astype(np.float32)
    return s / np.maximum(area, 1)[..., None]


def _grad(g):
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def _hist(f):
    """Joint RGB histogram, 4 levels per channel (64 bins). (A finer 48-bin
    per-channel histogram was tried and lost precision on the held-out
    reel; see benchmarks/refbench/README.md, AT-0D-2.)"""
    q = (np.asarray(f) // 64).astype(np.int32)
    idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    h = np.bincount(idx.ravel(), minlength=64).astype(np.float32)
    return h / max(1.0, h.sum())


def _ycc_stack(f):
    y, cb, cr = _ycc(f)
    return np.stack([y, cb, cr], axis=-1)


def _ycc(f):
    a = np.asarray(f, np.float32)
    y = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    cb = -0.168736 * a[..., 0] - 0.331264 * a[..., 1] + 0.5 * a[..., 2]
    cr = 0.5 * a[..., 0] - 0.418688 * a[..., 1] - 0.081312 * a[..., 2]
    return y, cb, cr


def _frame_stats(f):
    y, cb, cr = _ycc(f)
    chroma = np.sqrt(cb * cb + cr * cr)
    white = float(((y >= 200) & (chroma <= 24)).mean())
    black = float((y <= 16).mean())
    mid = (y >= 24) & (y <= 232)
    midf = float(mid.mean())
    if mid.sum() >= 20:
        p75, p98 = (float(v) for v in np.percentile(chroma[mid], (75, 98)))
    else:
        p75 = p98 = float("nan")
    gy = np.abs(np.diff(y, axis=0)).mean() + np.abs(np.diff(y, axis=1)).mean()
    # dominant saturated hue (12 bins) and its share of the frame
    hue = (np.degrees(np.arctan2(cr, cb)) + 360.0) % 360.0
    sat = chroma >= 40
    if sat.any():
        hb = (hue[sat] // 30).astype(int)
        cnt = np.bincount(hb, minlength=12)
        dom = int(cnt.argmax())
        share = float(cnt[dom]) / y.size
    else:
        dom, share = -1, 0.0
    return (float(y.mean()), white, black, p75, midf, float(gy), dom, share,
            p98)


def _corr(a, b):
    a = a.ravel() - a.mean()
    b = b.ravel() - b.mean()
    d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum()) / d if d > 1e-6 else 0.0


# ── detectors ─────────────────────────────────────────────────────────────

def negatives(F, *, reach=6, corr_i=-0.3, corr_g=0.3, min_std=6.0,
              passes=2):
    """Frames whose picture is the tonal inverse of its own take: intensity
    anti-correlated with the take, edge structure kept (gradient magnitude
    is unchanged by inversion; an unrelated shot has neither).

    The pairwise relation is symmetric (a normal frame is just as
    anti-correlated with the negative beside it), so each frame is judged
    against the per-pixel MEDIAN of its same-take neighbours within
    ``reach`` (gradient correlation >= ``corr_g``). In a dense strobe the
    neighbours can be mostly negatives, so a second pass re-inverts the
    neighbours the first pass flagged before taking the median.
    """
    flagged = set()
    for _ in range(max(1, passes)):
        out = []
        for t in range(F.n):
            if F.gray[t].std() < min_std:
                continue
            nbs = []
            for k in range(-reach, reach + 1):
                n = t + k
                if k == 0 or n < 0 or n >= F.n or F.gray[n].std() < min_std:
                    continue
                if _corr(F.grad[t], F.grad[n]) >= corr_g:
                    nbs.append(255.0 - F.gray[n] if n in flagged
                               else F.gray[n])
            if not nbs:
                continue
            ref = np.median(np.stack(nbs), axis=0)
            if ref.std() >= min_std and _corr(F.gray[t], ref) <= corr_i:
                out.append(t)
        flagged = set(out)
    return sorted(flagged)


def _runs(frames):
    runs = []
    for f in sorted(frames):
        if runs and f == runs[-1][1] + 1:
            runs[-1][1] = f
        else:
            runs.append([f, f])
    return runs


def white_flashes(F, *, white=0.80, max_len=3, texture_max=4.0, neg=None):
    """Full-picture white frames (type over them allowed), 1-``max_len``
    frames, with a non-white frame on at least one side. A bright NEGATIVE
    is a negative, not a white flash."""
    neg = set(neg if neg is not None else negatives(F))
    cand = [t for t in range(F.n)
            if F.white_frac[t] >= white and F.texture[t] <= texture_max
            and t not in neg]
    out = []
    for a, b in _runs(cand):
        if b - a + 1 > max_len:
            continue
        before = F.white_frac[a - 1] if a > 0 else 1.0
        after = F.white_frac[b + 1] if b + 1 < F.n else 1.0
        if min(before, after) < 0.5:
            out.extend(range(a, b + 1))
    return out


def colour_flashes(F, *, share=0.70, max_len=3, texture_max=6.0, neg=None):
    """Full-picture single-hue frames (1-``max_len`` frames) that differ from
    both neighbours' dominant hue or share."""
    neg = set(neg if neg is not None else negatives(F))
    cand = [t for t in range(F.n)
            if F.dom_share[t] >= share and F.texture[t] <= texture_max
            and t not in neg]
    out = []
    for a, b in _runs(cand):
        if b - a + 1 > max_len:
            continue
        nb = [x for x in (a - 1, b + 1) if 0 <= x < F.n]
        if any(F.dom_share[x] < share * 0.6 or F.dom_hue[x] != F.dom_hue[a]
               for x in nb):
            out.extend(range(a, b + 1))
    return out


def _dist(F, a, b):
    """0..1 frame distance: half colour (histogram L1), half STRUCTURE
    (1 - gradient-magnitude correlation). Structure is what a cut breaks and
    what a flash, a negative or a grade change keeps."""
    h = float(np.abs(F.hist[a] - F.hist[b]).sum()) / 2.0
    g = _corr(F.grad[a], F.grad[b])
    return 0.5 * min(1.0, 1.5 * h) + 0.5 * (1.0 - max(0.0, g))


def _dc(F, a, b):
    """Composition distance: mean |Δ| of the 8x6 YCbCr cell means (0..255)."""
    return float(np.abs(F.comp[a] - F.comp[b]).mean())


def _extent(F, a, b, gx=8, gy=6, mean_tol=8.0, corr_min=0.5):
    """Share of a gx x gy grid whose cells change between frames a and b
    (mean shift > mean_tol, or correlation < corr_min)."""
    A, B = F.gray[a], F.gray[b]
    H, W = A.shape
    ys = np.linspace(0, H, gy + 1).astype(int)
    xs = np.linspace(0, W, gx + 1).astype(int)
    ch = 0
    for i in range(gy):
        for j in range(gx):
            ca = A[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            cb = B[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            if abs(ca.mean() - cb.mean()) > mean_tol or \
                    _corr(ca, cb) < corr_min:
                ch += 1
    return ch / float(gx * gy)


def take_step(F, t, punct=(), span=3):
    """(S, coh): how far the take before ``t`` is from EVERY one of the next
    ``span`` + 1 frames (S, the step that persists), and how much the take
    from ``t`` moves over them (coh). Punctuation frames are skipped. A cut
    is a big S with a small coh; a move inside a take has coh ~ S."""
    punct = set(punct or ())
    pre = t - 1
    while pre in punct and pre > 0:
        pre -= 1
    post = [k for k in range(t, min(F.n, t + 12)) if k not in punct]
    post = post[:span + 1]
    if len(post) < 2 or pre < 0:
        return 0.0, float("inf")
    S = min(_dc(F, pre, k) for k in post)
    coh = max(_dc(F, post[0], k) for k in post[1:])
    return S, coh


def cuts(F, *, thr=0.45, rel=2.5, cap=0.85, quiet=0.10, sharp=4.0,
         bridge=3, same=0.30, punct=None, merge=2, soft=True, local_pct=50,
         takes=True, local_extent=0.3, local_r=3.0, run_k=0.6,
         step_d=0.25, step_s=3.5, step_coh=1.5, step_bef=1.5):
    """Hard cuts: frame t starts a new take.

    A change at t is a candidate when it is big (``_dist`` >= ``thr`` and
    >= ``rel`` x the local median, capped at ``cap`` so a busy reel cannot
    hide a total change) or when it is SHARP: >= ``quiet`` and >= ``sharp``
    x both neighbouring changes (a reframe of the same subject between two
    still takes). A candidate is a cut unless the take resumes within
    ``bridge`` frames -- frame t-1 (skipping known punctuation) matches one
    of t+1..t+bridge, or t matches a frame before the burst -- which is a
    flash, a negative, a threshold frame or an inset blink. "Matches" is
    ``_dist`` < min(``same``, half the change), so small cuts are judged on
    their own scale. Changes within ``merge`` frames of a reported cut
    collapse into it. ``soft`` adds dissolves: 2-4 consecutive moderate
    changes between two still stretches whose ends differ like a cut.

    ``takes`` then judges every candidate as a change of TAKE on the
    composition grid (``take_step``), away from punctuation:
      - when the old take's composition comes back within ``span`` frames
        (S < min(``step_s``, half the change)), it was a blink (an inset,
        a flash of type), not a cut;
      - a LOCAL change (< ``local_extent`` of the grid changes, and the step
        is not clean: S / (coh + 0.5) < ``local_r``) is an effect or an
        overlay inside the take, not a cut;
      - one step of a continuous move (the composition steps by >=
        ``run_k`` x as much on both neighbouring frames, and the change is
        not total: d < ``cap``) is a pan, whip or page flip, not a cut;
      - a clean step into a still take is a cut even when the busy reel
        around it hides it from the ratio test: d >= ``step_d``, the step
        persists (S >= ``step_s``), the new take holds still (coh <=
        ``step_coh``) and the step is ``step_bef`` x larger than the old
        take's own movement.
    Returns sorted frame indices.
    """
    if F.n < 2:
        return []
    d = np.array([0.0] + [_dist(F, t - 1, t) for t in range(1, F.n)])
    punct = set(punct or [])
    out = []
    for t in range(1, F.n):
        lo, hi = max(1, t - 15), min(F.n, t + 16)
        local = float(np.percentile(d[lo:hi], local_pct))
        nb = max(d[t - 1] if t > 1 else 0.0,
                 d[t + 1] if t + 1 < F.n else 0.0, 0.01)
        big = d[t] >= max(thr, min(cap, rel * local))
        sharp_ok = d[t] >= quiet and d[t] >= sharp * nb
        if not (big or sharp_ok):
            continue
        same_t = min(same, 0.5 * d[t])
        prev = t - 1
        while prev in punct and prev > 0:
            prev -= 1
        if t in punct and _corr(F.grad[prev], F.grad[t]) >= 0.45:
            continue          # an effect frame of the take that was playing
        if any(t + k < F.n and _dist(F, prev, t + k) < same_t
               for k in range(1, bridge + 1)):
            continue          # the take resumes after a short burst
        if any(t - k >= 0 and _dist(F, t - k, t) < same_t
               for k in range(2, bridge + 2)):
            continue          # this frame IS the take resuming
        if out and t - out[-1] <= merge:
            continue
        out.append(t)
    if soft:
        for t in range(2, F.n - 4):
            if any(abs(t - c) <= 4 for c in out):
                continue
            for span in (2, 3, 4):
                e = t + span            # first frame after the dissolve
                if e + 1 >= F.n:
                    break
                run = d[t:e]
                if run.min() < 0.12 or d[t - 1] > 0.08 or d[e] > 0.08:
                    continue
                if _dist(F, t - 1, e) >= 0.45:
                    out.append(t)
                    break
        out.sort()
    if takes and F.n >= 8:
        out = _take_check(F, out, d, punct, merge=merge, cap=cap,
                          local_extent=local_extent, local_r=local_r,
                          run_k=run_k, step_d=step_d, step_s=step_s,
                          step_coh=step_coh, step_bef=step_bef)
    return out


def _take_check(F, cand, d, punct, *, merge, cap, local_extent, local_r,
                run_k, step_d, step_s, step_coh, step_bef):
    def near_punct(t, a, b):
        return any(k in punct for k in range(t + a, t + b))
    out = []
    for t in cand:
        if not near_punct(t, -2, 3):
            S, coh = take_step(F, t, punct)
            if S < min(step_s, 0.5 * _dc(F, t - 1, t)):
                continue                       # the take resumes: a blink
            if _extent(F, t - 1, t) < local_extent and \
                    S / (coh + 0.5) < local_r:
                continue                       # an effect inside the take
            if 2 <= t < F.n - 1:
                s0 = _dc(F, t - 1, t)
                if _dc(F, t - 2, t - 1) >= run_k * s0 and \
                        _dc(F, t, t + 1) >= run_k * s0 and d[t] < cap:
                    continue                   # a step of a continuous move
        out.append(t)
    for t in range(3, F.n - 3):
        if d[t] < step_d or any(abs(t - c) <= merge for c in out) or \
                near_punct(t, -2, 4):
            continue
        S, coh = take_step(F, t, punct)
        bef = max(_dc(F, t - 2, t - 1), _dc(F, t - 3, t - 1))
        if S >= step_s and coh <= step_coh and S >= step_bef * bef:
            out.append(t)
    return sorted(out)


def bw(F, *, chroma_max=8.0, min_mid=0.05, min_section_s=0.2):
    """B&W frames (75th-percentile chroma of mid-tones <= ``chroma_max``)
    and sections (runs >= ``min_section_s``). Frames with too few mid-tones
    (near black or white) are undecidable and neither start nor break a
    section."""
    frames, undecided = [], []
    for t in range(F.n):
        if F.midtone_frac[t] < min_mid or not np.isfinite(F.chroma_p75[t]):
            undecided.append(t)
        elif F.chroma_p75[t] <= chroma_max:
            frames.append(t)
    und = set(undecided)
    sections, cur = [], None
    for t in range(F.n):
        if t in und:
            continue
        if t in frames:
            if cur is None:
                cur = [t, t]
            else:
                cur[1] = t
        else:
            if cur is not None:
                sections.append(cur)
            cur = None
    if cur is not None:
        sections.append(cur)
    min_len = max(2, int(round(min_section_s * F.fps)))
    sections = [s for s in sections if s[1] - s[0] + 1 >= min_len]
    return frames, undecided, sections


def layout(path, *, samples=48, still=3.0):
    """Where the picture lives on the canvas.

    Temporal luma std over ``samples`` frames: pixels that never change are
    canvas (bars, plate, card surround). Returns {"layout", "rects",
    "canvas": [w, h]} with rects in source pixels; "stack" when two or more
    separate moving bands sit one above the other."""
    info = qa_metrics.probe(path)
    W, H = info["width"], info["height"]
    n = info["frames"] or 1
    step = max(1, n // samples)
    w = 160
    h = _even(H * w / float(W))
    idx, frames = qa_metrics.read_frames(path, gray=True, size=(w, h),
                                         frames=set(range(0, n, step)))
    if len(frames) < 3:
        return {"layout": "unknown", "rects": [], "canvas": [W, H]}
    st = np.stack(frames).astype(np.float32)
    dyn = st.std(axis=0) > still
    rows = dyn.mean(axis=1) > 0.05
    cols = dyn.mean(axis=0) > 0.05
    bands = _runs([i for i in range(h) if rows[i]])
    bands = [b for b in bands if b[1] - b[0] + 1 >= max(3, h // 20)]
    kx = W / float(w)
    ky = H / float(h)
    rects = []
    for a, b in bands:
        sub = dyn[a:b + 1]
        c = np.nonzero(sub.mean(axis=0) > 0.05)[0]
        if not len(c):
            continue
        rects.append([int(c[0] * kx), int(a * ky),
                      int((c[-1] - c[0] + 1) * kx),
                      int((b - a + 1) * ky)])
    if not rects:
        return {"layout": "still", "rects": [], "canvas": [W, H]}
    if len(rects) >= 2:
        kind = "stack"
    else:
        x, y, rw, rh = rects[0]
        full_w = rw >= 0.94 * W
        full_h = rh >= 0.94 * H
        if full_w and full_h:
            kind = "full"
        elif full_w:
            kind = "letterbox"
        elif full_h:
            kind = "pillarbox"
        else:
            kind = "card"
        if kind == "card":
            # a rounded card leaves its corners static
            r0, c0 = int(y / ky), int(x / kx)
            r1 = min(h - 1, int((y + rh) / ky) - 1)
            c1 = min(w - 1, int((x + rw) / kx) - 1)
            corners = [dyn[r0, c0], dyn[r0, c1], dyn[r1, c0], dyn[r1, c1]]
            if not any(corners):
                kind = "card_rounded"
    return {"layout": kind, "rects": rects, "canvas": [W, H],
            "cols_dynamic": float(cols.mean())}


def changes(F, *, thr=0.45):
    """Every frame whose picture changes like a cut from the frame before
    (``_dist`` >= thr), before any bridging: in a strobe of alternating
    images each switch is one, though ``cuts`` reads the alternation as one
    take with inserts."""
    return [t for t in range(1, F.n) if _dist(F, t - 1, t) >= thr]


def punctuation_events(F, cut, punct):
    """One event per cut, per punctuation RUN (a 2-frame negative is one
    hit, not two changes), and per raw change that is neither (the
    switches of a strobe)."""
    ev = set(cut)
    for a, _b in _runs(punct):
        ev.add(a)
    for t in changes(F):
        if t in punct or (t - 1) in punct or any(abs(t - c) <= 1
                                                  for c in cut):
            continue
        ev.add(t)
    return sorted(ev)


def strobe_sections(F, events, *, rate=6.0, min_s=0.5):
    """Windows where punctuation + cuts run at >= ``rate`` per second for at
    least ``min_s``. ``events``: frame indices of every cut / flash."""
    ev = np.zeros(F.n, np.int32)
    for e in events:
        if 0 <= e < F.n:
            ev[e] = 1
    win = max(1, int(round(F.fps)))
    cs = np.concatenate([[0], np.cumsum(ev)])
    hot = np.zeros(F.n, bool)
    for t in range(F.n):
        a, b = t, min(F.n, t + win)
        if cs[b] - cs[a] >= rate * (b - a) / win and b - a >= win // 2:
            hot[a:b] = True
    secs = [s for s in _runs(np.nonzero(hot)[0].tolist())
            if (s[1] - s[0] + 1) / F.fps >= min_s]
    return [{"f": s, "t": [F.t(s[0]), F.t(s[1] + 1)],
             "events": int(cs[s[1] + 1] - cs[s[0]])} for s in secs]


def type_on_black(F, *, black=0.75, min_ink=0.003, max_ink=0.25,
                  texture_min=0.6, ocr_frames=None):
    """Frames of type on a black plate: >= ``black`` of the picture near
    black, a little bright ink with edge texture (or OCR text when
    ``ocr_frames`` is given). Returns (frames, density) where density is the
    share of the reel's frames."""
    out = []
    for t in range(F.n):
        if F.black_frac[t] < black:
            continue
        ink = 1.0 - F.black_frac[t]
        if not (min_ink <= ink <= max_ink):
            continue
        if ocr_frames is not None:
            if t in ocr_frames:
                out.append(t)
        elif F.texture[t] >= texture_min:
            out.append(t)
    return out, (len(out) / float(F.n) if F.n else 0.0)


# ── OCR (RapidOCR PP-OCRv4; dev lanes) ────────────────────────────────────

_ocr_engine = None
_ocr_dead = None


def ocr_status():
    """(available, why)."""
    try:
        import rapidocr_onnxruntime as r                       # noqa: F401
    except Exception as e:                                     # pragma: no cover
        return False, f"rapidocr-onnxruntime not installed ({e.__class__.__name__})"
    import hashlib
    base = os.path.dirname(r.__file__)
    for rel, sha in OCR_MODELS.items():
        p = os.path.join(base, rel)
        if not os.path.exists(p):
            return False, f"missing OCR model {rel}"
        with open(p, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != sha:
                return False, f"OCR model {rel} does not match its pinned sha256"
    return True, "ok"


def _ocr():
    global _ocr_engine, _ocr_dead
    if _ocr_engine is not None:
        return _ocr_engine
    if _ocr_dead:
        raise RuntimeError(_ocr_dead)
    ok, why = ocr_status()
    if not ok:
        _ocr_dead = why
        raise RuntimeError(why)
    from rapidocr_onnxruntime import RapidOCR
    _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_frames(path, frames, *, rect=None, width=None, min_conf=0.5):
    """{frame: [{"box": [x0, y0, x1, y1], "text", "conf"}]} in source px."""
    eng = _ocr()
    info = qa_metrics.probe(path)
    rw = rect[2] if rect else info["width"]
    rh = rect[3] if rect else info["height"]
    size = None
    k = 1.0
    if width and width < rw:
        size = (int(width), _even(rh * width / float(rw)))
        k = rw / float(width)
    idx, imgs = qa_metrics.read_frames(path, rect=rect, size=size,
                                       frames=set(frames))
    ox, oy = (rect[0], rect[1]) if rect else (0, 0)
    out = {}
    for f, im in zip(idx, imgs):
        res, _ = eng(np.ascontiguousarray(im[..., ::-1]))
        boxes = []
        for quad, text, conf in (res or []):
            if float(conf) < min_conf:
                continue
            q = np.asarray(quad, np.float64) * k
            boxes.append({"box": [int(q[:, 0].min() + ox),
                                  int(q[:, 1].min() + oy),
                                  int(q[:, 0].max() + ox),
                                  int(q[:, 1].max() + oy)],
                          "text": str(text), "conf": round(float(conf), 3)})
        out[f] = boxes
    return out


# ── person matte (RVM; dev and batch lanes) ───────────────────────────────

def rvm_model_path():
    p = os.environ.get("VALMERA_RVM_MODEL") or os.environ.get(
        "MATTE_RVM_MODEL")
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "models", "rvm_mobilenetv3_fp32.onnx"),
                 os.path.expanduser(
                     "~/Documents/Valmera/.valmera/models/"
                     "rvm_mobilenetv3_fp32.onnx")):
        if os.path.exists(cand):
            return cand
    return None


_RVM_CHECKED = {}


def rvm_status(path=None):
    """(ok, why): the RVM file exists and matches ``RVM_SHA256``, so a
    swapped or truncated model cannot silently change the masks (and every
    occluded-text number) a benchmark reports. Checked once per file."""
    mp = path or rvm_model_path()
    if not mp or not os.path.exists(mp):
        return False, "no RVM model (set VALMERA_RVM_MODEL)"
    key = (mp, os.path.getsize(mp), os.path.getmtime(mp))
    if key not in _RVM_CHECKED:
        import hashlib
        h = hashlib.sha256()
        with open(mp, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        _RVM_CHECKED[key] = h.hexdigest() == RVM_SHA256
    if not _RVM_CHECKED[key]:
        return False, f"RVM model {mp} does not match its pinned sha256"
    return True, "ok"


def person_masks(path, frames, *, width=512, rect=None):
    """{frame: soft mask (h, w) float32} for the requested frames, measured
    with RVM over every frame from min(frames) to max(frames) in order (the
    recurrent state needs the sequence). Raises when no model is present or
    the model is not the pinned file."""
    ok, why = rvm_status()
    if not ok:
        raise RuntimeError(why)
    mp = rvm_model_path()
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(mp, so, providers=["CPUExecutionProvider"])
    info = qa_metrics.probe(path)
    rw = rect[2] if rect else info["width"]
    rh = rect[3] if rect else info["height"]
    w = min(width, rw)
    h = _even(rh * w / float(rw))
    f0, f1 = min(frames), max(frames)
    idx, imgs = qa_metrics.read_frames(path, rect=rect, size=(w, h),
                                       start_frame=f0, count=f1 - f0 + 1,
                                       seek=True)
    want = set(frames)
    rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4
    ds = np.array([min(1.0, 512.0 / float(max(w, h)))], np.float32)
    out = {}
    for f, im in zip(idx, imgs):
        src = np.ascontiguousarray(
            (im.astype(np.float32) / 255.0).transpose(2, 0, 1))[None]
        _fgr, pha, r1, r2, r3, r4 = sess.run(None, {
            "src": src, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2],
            "r4i": rec[3], "downsample_ratio": ds})
        rec = [r1, r2, r3, r4]
        if f in want:
            out[f] = np.clip(pha[0, 0], 0, 1).astype(np.float32)
    return out


def _ink_mask(img, box):
    """Pixels of the type's ink inside ``box``: the colour cluster of the
    box that is NOT the box border's colour (two-means in RGB)."""
    x0, y0, x1, y1 = box
    sub = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    if len(sub) < 16:
        return None, None
    border = np.concatenate([img[y0:y1, x0].reshape(-1, 3),
                             img[y0:y1, x1 - 1].reshape(-1, 3),
                             img[y0, x0:x1].reshape(-1, 3),
                             img[y1 - 1, x0:x1].reshape(-1, 3)]).astype(
        np.float32)
    bg = np.median(border, axis=0)
    dist = np.sqrt(((sub - bg) ** 2).sum(axis=1))
    far = sub[dist >= np.percentile(dist, 70)]
    ink = np.median(far, axis=0) if len(far) else bg
    return ink, bg


def _near(b, r):
    """Binary dilation by a (2r+1) square (numpy only)."""
    out = b.copy()
    for axis in (0, 1):
        acc = out.copy()
        for k in range(1, r + 1):
            acc |= np.roll(out, k, axis=axis)
            acc |= np.roll(out, -k, axis=axis)
        out = acc
    return out


def occluded_text(path, frames, *, ocr=None, masks=None, rect=None,
                  mask_width=512, min_cover=0.04, ink_ratio=0.35,
                  person_thr=0.5, edge_min=24.0):
    """Frames where type is partly hidden by a person standing in front of it.

    For each OCR line the band at the line's height is extended sideways by
    half a line height; the person matte inside that band must cover >=
    ``min_cover`` of it, the type's ink must be present outside the person
    and nearly absent (<= ``ink_ratio`` of the outside density) where the
    person is: type painted OVER a person keeps its ink there, type behind
    a person loses it. Returns {"frames": [...], "lines": {frame: [...]}}.
    """
    info = qa_metrics.probe(path)
    W = rect[2] if rect else info["width"]
    H = rect[3] if rect else info["height"]
    ocr = ocr if ocr is not None else ocr_frames(path, frames, rect=rect)
    with_text = [f for f in frames if ocr.get(f)]
    if not with_text:
        return {"frames": [], "lines": {}}
    masks = masks if masks is not None else person_masks(
        path, with_text, width=mask_width, rect=rect)
    idx, imgs = qa_metrics.read_frames(path, rect=rect,
                                       frames=set(with_text))
    pics = dict(zip(idx, imgs))
    hits, lines = [], {}
    ox, oy = (rect[0], rect[1]) if rect else (0, 0)
    for f in with_text:
        m = masks.get(f)
        img = pics.get(f)
        if m is None or img is None:
            continue
        mh, mw = m.shape
        found = []
        for b in ocr[f]:
            x0, y0, x1, y1 = b["box"]
            x0, x1 = x0 - ox, x1 - ox
            y0, y1 = y0 - oy, y1 - oy
            lh = max(4, y1 - y0)
            ex = int(0.5 * lh)
            bx0, bx1 = max(0, x0 - ex), min(W, x1 + ex)
            by0, by1 = max(0, y0), min(H, y1)
            if bx1 - bx0 < 8 or by1 - by0 < 4:
                continue
            ink, bg = _ink_mask(img, (max(0, x0), by0, min(W, x1), by1))
            if ink is None or np.sqrt(((ink - bg) ** 2).sum()) < 40:
                continue
            band = img[by0:by1, bx0:bx1].astype(np.float32)
            d_ink = np.sqrt(((band - ink) ** 2).sum(axis=2))
            d_bg = np.sqrt(((band - bg) ** 2).sum(axis=2))
            # ink is ink-coloured AND on a stroke edge: a bright plain
            # wall beside the speaker is ink-coloured too, but flat (v5
            # captions over a dark shirt against a pale projection wall
            # read as "type behind a person" without this)
            g = band.mean(axis=2)
            gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
            gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
            edge = _near(np.maximum(gx, gy) >= edge_min, max(1, lh // 12))
            inky = (d_ink < np.minimum(d_bg, 90.0)) & edge
            # the person matte sampled at the band's pixels
            ys = np.clip((np.arange(by0, by1) * mh / float(H)).astype(int),
                         0, mh - 1)
            xs = np.clip((np.arange(bx0, bx1) * mw / float(W)).astype(int),
                         0, mw - 1)
            person = m[np.ix_(ys, xs)] >= person_thr
            cover = float(person.mean())
            if cover < min_cover or person.all():
                continue
            d_out = float(inky[~person].mean())
            d_in = float(inky[person].mean())
            ok = d_out >= 0.08 and d_in <= ink_ratio * d_out
            found.append({"box": b["box"], "text": b["text"],
                          "person_cover": round(cover, 3),
                          "ink_out": round(d_out, 3),
                          "ink_in": round(d_in, 3), "occluded": bool(ok)})
            if ok:
                hits.append(f)
        lines[f] = found
    return {"frames": sorted(set(hits)), "lines": lines}


# ── the whole signature ───────────────────────────────────────────────────

def analyze(path, *, rect=None, ocr=False, ocr_every_s=0.5, person=False,
            frames_range=None):
    """The reel's signature (JSON-safe). ``rect`` limits picture detectors
    to a sub-rectangle (a stack's picture band). ``ocr`` adds text boxes
    every ``ocr_every_s``; ``person`` adds occluded_text on those frames
    (needs OCR). ``frames_range`` (f0, f1) limits OCR/person work."""
    F = Feats(path, rect=rect)
    neg = negatives(F)
    wf = white_flashes(F, neg=neg)
    cf = colour_flashes(F, neg=neg)
    cut = cuts(F, punct=set(neg) | set(wf) | set(cf))
    bw_frames, bw_und, bw_secs = bw(F)
    events = punctuation_events(F, cut, set(neg) | set(wf) | set(cf))
    sig = {
        "version": SIG_VERSION, "path": os.path.basename(path),
        "fps": F.fps, "frames": F.n, "duration_s": round(F.n / F.fps, 3),
        "size": [F.src_w, F.src_h], "rect": F.rect,
        "layout": layout(path),
        "cuts": {"f": cut, "t": [F.t(f) for f in cut]},
        "shots": len(cut) + 1,
        "asl_s": round(F.n / F.fps / (len(cut) + 1), 3),
        "negatives": {"f": neg, "t": [F.t(f) for f in neg],
                      "runs": _runs(neg)},
        "white_flashes": {"f": wf, "t": [F.t(f) for f in wf]},
        "colour_flashes": {"f": cf, "t": [F.t(f) for f in cf]},
        "bw": {"f": bw_frames, "undecided": bw_und, "sections": bw_secs,
               "sections_t": [[F.t(a), F.t(b + 1)] for a, b in bw_secs],
               "selective_colour": [bool(np.nanmedian(
                   F.chroma_p98[a:b + 1]) >= 40.0) for a, b in bw_secs]},
        "strobe_sections": strobe_sections(F, events),
    }
    tob, dens = type_on_black(F)
    sig["type_on_black"] = {"f": tob, "density": round(dens, 4)}
    try:
        # the SAME counter as the flash_risk metric (every 1 s window, not
        # a sample of them): a strobing reference must read as strobing,
        # since "make it like this" treats that as the flash opt-in
        fr = qa_metrics.flash_risk(frames=F.frames, fps=F.fps, opted_in=True)
        sig["flash_rate_max_per_s"] = int(fr["value"] or 0)
        sig["contains_flashing"] = bool(fr["detail"]["contains_flashing"])
    except Exception as e:                                     # pragma: no cover
        sig["flash_rate_max_per_s"] = None
        sig["flash_rate_error"] = str(e)[:120]
    if ocr:
        ok, why = ocr_status()
        if not ok:
            sig["text_boxes"] = {"available": False, "why": why}
        else:
            a, b = frames_range or (0, F.n - 1)
            step = max(1, int(round(F.fps * ocr_every_s)))
            fr = list(range(a, b + 1, step))
            boxes = ocr_frames(path, fr, rect=rect)
            sig["text_boxes"] = {"available": True,
                                 "frames": {str(k): v
                                            for k, v in boxes.items()}}
            tob2, dens2 = type_on_black(
                F, ocr_frames={k for k, v in boxes.items() if v})
            sig["type_on_black_ocr"] = {"f": tob2, "sampled": len(fr)}
            if person:
                try:
                    occ = occluded_text(path, fr, ocr=boxes, rect=rect)
                    sig["occluded_text"] = {
                        "available": True, "f": occ["frames"],
                        "t": [F.t(f) for f in occ["frames"]]}
                except RuntimeError as e:
                    sig["occluded_text"] = {"available": False,
                                            "why": str(e)}
    return sig


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Reference signature of a reel")
    ap.add_argument("video")
    ap.add_argument("--rect", default=None, help="x,y,w,h picture rect")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--person", action="store_true")
    a = ap.parse_args(argv)
    rect = [int(v) for v in a.rect.split(",")] if a.rect else None
    print(json.dumps(analyze(a.video, rect=rect, ocr=a.ocr,
                             person=a.person), indent=1))


if __name__ == "__main__":
    main()
