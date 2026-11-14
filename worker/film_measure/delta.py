#!/usr/bin/env python3
"""edit_delta: what an edit actually changed, measured, with no model and no
keyword rule anywhere. Signal processing only (ffmpeg, numpy, OpenCV).

    edit_delta.py --delivered final.mp4 --source upload.mp4 [--source b.mp4] [--out delta.json]
    edit_delta.py --delivered reel.mp4                      # standalone: no source

Inputs may be paths or presigned URLs. With a source it answers: how much of
the delivered picture IS the unchanged upload; which delivered seconds come
from where; shots added; cut rate; added graphic coverage; type events; camera
moves; audio events; duration change; colour shift. Without a source it gives
the same counts for the delivered video alone (references, finished shorts).

`class` is the provable part:
  same_upload  the delivered video is essentially the upload        ceiling 2
  trim_only    the upload with parts removed, nothing added         ceiling 3
  light_touch  one thin layer over the upload (captions or a grade,
               no new pictures, almost no cuts)                     ceiling 4
  edited       a real change of the picture                         no ceiling
RUBRIC_V3.md ties the ceilings to the score; the CLI refuses a score above it.

How the match works (no assumption that the delivered frame is the whole
source frame): each delivered sample is compared by normalised correlation
with every indexed source frame under a grid of crops (a 9:16 crop slid over a
16:9 source, a punch-in, the source as a band inside the frame), the best
candidates are then located exactly with template matching, the matched source
region is warped onto the delivered frame, a gain/offset grade is fitted, and
what remains different block by block is what the edit added. Audio is aligned
separately with a band-difference fingerprint, which pins the source time even
on a static talking head, and shows jump cuts, reorders and added sound.
"""
import json
import math
import os
import subprocess
import sys

import cv2
import numpy as np
from . import numeric as ndimage
from . import numeric as sfft

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
LONG_EDGE = 256                    # analysis size of every frame
CUT_FPS, CUT_MAX_FRAMES = 15.0, 5400
SAMPLE_FPS, SAMPLE_MAX = 3.0, 420  # delivered frames matched against the source
SRC_MAX_FRAMES = 1400              # indexed source frames, all sources together
COARSE = 24                        # side of the normalised coarse vector
BLOCK = 8                          # residual block, in analysis pixels
# Block is "changed" when the mean graded L difference exceeds this (0-100 L*).
CHANGED_L = 9.0
AUDIO_RATE, AUDIO_HOP = 8000, 160  # 20 ms frames
AUDIO_BANDS = 24
END_CARD_RANGE = (2.5, 7.0)


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, **kw)


def probe(path):
    r = sh([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
            "-show_streams", path], timeout=120)
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed: " + r.stderr.decode("utf8", "replace")[:200])
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("no video stream")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int((v.get("tags") or {}).get("rotate", rot) or rot)
    if abs(rot) % 180 == 90:
        w, h = h, w
    sar = v.get("sample_aspect_ratio") or "1:1"
    try:
        n, dn = [int(x) for x in sar.split(":")]
        if n and dn and n != dn:
            w = int(round(w * n / dn))
    except ValueError:
        pass
    dur = float((d.get("format") or {}).get("duration") or v.get("duration") or 0)
    vdur = float(v.get("duration") or 0)
    fr = v.get("avg_frame_rate") or "0/1"
    try:
        n, dn = [float(x) for x in fr.split("/")]
        fps = n / dn if dn else 0.0
    except ValueError:
        fps = 0.0
    return {"duration": dur, "video_duration": vdur or dur, "width": w, "height": h,
            "fps": round(fps, 3), "has_audio": a is not None,
            "bytes": int((d.get("format") or {}).get("size") or 0)}


def size_for(w, h, long_edge=LONG_EDGE):
    scale = long_edge / float(max(w, h))
    return max(16, int(round(w * scale / 2)) * 2), max(16, int(round(h * scale / 2)) * 2)


def frames(path, w, h, fps=None, keyframes=False, limit=None):
    """Decoded BGR frames (uint8, h×w×3), low resolution, as a list."""
    cmd = [FFMPEG, "-nostdin", "-v", "error"]
    if keyframes:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-i", path, "-map", "0:v:0"]
    vf = []
    if fps and not keyframes:
        vf.append(f"fps={fps:.4f}")
    vf.append(f"scale={w}:{h}:flags=area")
    cmd += ["-vf", ",".join(vf)]
    if keyframes:
        cmd += ["-vsync", "vfr"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n, out = w * h * 3, []
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            out.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
            if limit and len(out) >= limit:
                break
    finally:
        p.stdout.close()
        p.terminate()
        p.wait()
    return out


# ── Cuts, flashes, shots ─────────────────────────────────────────────────

def cut_analysis(path, info):
    dur = info["video_duration"] or info["duration"]
    fps = min(CUT_FPS, CUT_MAX_FRAMES / max(dur, 1.0))
    w, h = size_for(info["width"], info["height"], 96)
    fr = frames(path, w, h, fps=fps)
    n = len(fr)
    if n < 3:
        return {"fps": fps, "cuts": [], "flashes": [], "frames": n, "duration": dur}
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]).astype(np.float32)
    hists = []
    for f in fr:
        hs = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        hh = cv2.calcHist([hs], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
        hists.append((hh / (hh.sum() + 1e-6)).ravel())
    hists = np.stack(hists)
    dpx = np.abs(gray[1:] - gray[:-1]).mean(axis=(1, 2)) / 64.0
    dh = np.abs(hists[1:] - hists[:-1]).sum(axis=1) / 2.0
    score = 0.5 * np.clip(dpx, 0, 1.5) + 0.5 * dh
    # Local baseline, so a fast pan is not a thousand cuts.
    base = ndimage.median_filter(score, size=int(max(9, 1.2 * fps)), mode="nearest")
    hits = np.where((score > 0.22) & (score > 3.0 * base + 0.06))[0]
    cuts, flashes = [], []
    i = 0
    while i < len(hits):
        k = hits[i]
        j = i
        while j + 1 < len(hits) and hits[j + 1] - hits[j] <= 2:
            j += 1
        group = hits[i:j + 1]
        last = group[-1]
        # A flash returns to the picture before it within two frames.
        if len(group) >= 2 and k >= 1 and last + 2 < n:
            back = np.abs(gray[k] - gray[min(last + 1, n - 1)]).mean() / 64.0
            if back < 0.12:
                flashes.append(round((k + 1) / fps, 3))
                i = j + 1
                continue
        cuts.append(round((last + 1) / fps, 3))
        i = j + 1
    return {"fps": round(fps, 3), "cuts": cuts, "flashes": flashes, "frames": n,
            "duration": dur, "gray": gray, "score": score}


def shots_from(cuts, dur):
    edges = [0.0] + list(cuts) + [dur]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)
            if edges[i + 1] - edges[i] > 0.02]


def camera_moves(gray, fps, cuts, min_s=0.3):
    """Coherent global moves inside shots (LK + a robust similarity): a zoom
    (scale changes over 3%) or a pan or tilt (over 6% of the frame). Handheld
    shake nets out and does not count."""
    n = len(gray)
    if n < 4:
        return {"moves": 0, "zooms": 0, "pans": 0, "moving_pct": 0.0}
    step = max(1, int(round(fps / 6.0)))
    h, w = gray.shape[1:]
    edges = sorted(int(round(c * fps)) for c in cuts)
    moves = zooms = pans = 0
    moving = 0.0
    bounds = [0] + edges + [n]
    for a, b in zip(bounds[:-1], bounds[1:]):
        if (b - a) / fps < min_s:
            continue
        ls, tx, ty = 0.0, 0.0, 0.0
        peak_s, peak_t = 0.0, 0.0
        for i in range(a + 1, b - step, step):
            p = gray[i].astype(np.uint8)
            q = gray[min(i + step, b - 1)].astype(np.uint8)
            pts = cv2.goodFeaturesToTrack(p, 60, 0.01, 4)
            if pts is None or len(pts) < 8:
                continue
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(p, q, pts, None, winSize=(11, 11), maxLevel=2)
            good = st.ravel() == 1
            if good.sum() < 8:
                continue
            m, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC,
                                                 ransacReprojThreshold=1.5)
            if m is None or inl is None or inl.sum() < 8:
                continue
            s = math.hypot(m[0, 0], m[1, 0])
            ls += math.log(max(s, 1e-3))
            tx += m[0, 2]
            ty += m[1, 2]
            peak_s = max(peak_s, abs(ls))
            peak_t = max(peak_t, math.hypot(tx / w, ty / h))
        z = peak_s >= 0.03
        t = peak_t >= 0.06
        if z or t:
            moves += 1
            zooms += int(z)
            pans += int(t and not z)
            moving += (b - a) / fps
    dur = n / fps
    return {"moves": moves, "zooms": zooms, "pans": pans,
            "moving_pct": round(100.0 * moving / max(dur, 0.1), 1)}


# ── Type ─────────────────────────────────────────────────────────────────

def text_mask(bgr):
    """Blocks that look like set type: thin high-contrast strokes in a row.
    Morphology only."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(grad.mean()) < 3:
        bw[:] = 0
    h, w = g.shape
    join = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((2, max(5, w // 28)), np.uint8))
    cnt, _ = cv2.findContours(join, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros((h, w), np.uint8)
    for c in cnt:
        x, y, bw_, bh = cv2.boundingRect(c)
        if not (0.018 * h <= bh <= 0.2 * h) or bw_ < 2.0 * bh or bw_ > 0.98 * w:
            continue
        roi = bw[y:y + bh, x:x + bw_]
        fill = roi.mean() / 255.0
        trans = np.abs(np.diff(roi.astype(np.int16), axis=1)).sum() / 255.0 / (bw_ * bh)
        # Letters: many on/off transitions along the row, half-filled box.
        if 0.25 <= fill <= 0.85 and trans >= 0.12:
            mask[y:y + bh, x:x + bw_] = 1
    return mask


def type_events(masks, times):
    """A type event: the set type changes (a new caption, a word swap, a
    title in or out)."""
    ev, area, prev = [], [], None
    for m, t in zip(masks, times):
        a = float(m.mean())
        area.append(a)
        if prev is None:
            prev = m
            if a >= 0.002:
                ev.append(t)
            continue
        inter = float((m & prev).sum())
        union = float((m | prev).sum())
        if union > 0 and inter / union < 0.45 and max(a, float(prev.mean())) >= 0.002:
            ev.append(t)
        prev = m
    return ev, float(np.mean(area)) if area else 0.0


# ── Source index and matching ────────────────────────────────────────────

def norm_vec(gray_small):
    v = gray_small.astype(np.float32).ravel()
    v -= v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-3 else v


def crop_grid(sw, sh_, dw, dh):
    """(x, y, w, h) boxes of a dw:dh window over a sw×sh_ source frame."""
    rd, rs = dw / float(dh), sw / float(sh_)
    boxes = []
    if abs(math.log(rd / rs)) < 0.04:
        ch, cw = sh_, sw
    elif rd < rs:
        ch, cw = sh_, int(round(sh_ * rd))
    else:
        cw, ch = sw, int(round(sw / rd))
    nx = 1 if cw >= sw - 1 else 7
    ny = 1 if ch >= sh_ - 1 else 5
    for z, xn, yn in ((1.0, nx, ny), (1.35, max(nx // 2, 3), max(ny // 2, 3))):
        zw, zh = int(round(cw / z)), int(round(ch / z))
        for x in np.linspace(0, sw - zw, xn):
            for y in np.linspace(0, sh_ - zh, yn):
                boxes.append((int(x), int(y), zw, zh))
    return boxes


class SourceIndex:
    def __init__(self, sources, dw, dh):
        self.items = []
        total = sum(max(s["info"]["video_duration"], 0.1) for s in sources)
        offset = 0.0
        self.frames = []                 # BGR
        self.times = []
        self.src_of = []
        self.grids = {}
        for si, s in enumerate(sources):
            inf = s["info"]
            d = max(inf["video_duration"], 0.1)
            quota = max(8, int(SRC_MAX_FRAMES * d / total))
            fps = min(15.0, quota / d)
            w, h = size_for(inf["width"], inf["height"])
            long_src = d > 1500 or fps < 0.6
            fr = frames(s["path"], w, h, fps=None if long_src else fps,
                        keyframes=long_src, limit=quota if not long_src else None)
            if long_src and len(fr) > quota:
                keep = np.linspace(0, len(fr) - 1, quota).astype(int)
                fr = [fr[i] for i in keep]
            if not fr:
                offset += d
                continue
            dt = d / len(fr) if long_src else 1.0 / fps
            for i, f in enumerate(fr):
                self.frames.append(f)
                self.times.append(offset + (i + 0.5) * dt)
                self.src_of.append(si)
            offset += d
        self.total = offset
        self.gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in self.frames]
        self.build(dw, dh)

    def build(self, dw, dh):
        vecs, refs = [], []
        full_vecs, full_refs = [], []
        for i, g in enumerate(self.gray):
            h, w = g.shape
            key = (w, h)
            if key not in self.grids:
                self.grids[key] = crop_grid(w, h, dw, dh)
            for b, (x, y, cw, ch) in enumerate(self.grids[key]):
                small = cv2.resize(g[y:y + ch, x:x + cw], (COARSE, COARSE),
                                   interpolation=cv2.INTER_AREA)
                vecs.append(norm_vec(small))
                refs.append((i, b))
            full_vecs.append(norm_vec(cv2.resize(g, (COARSE, COARSE),
                                                 interpolation=cv2.INTER_AREA)))
            full_refs.append(i)
        self.vecs = np.stack(vecs) if vecs else np.zeros((0, COARSE * COARSE), np.float32)
        self.refs = refs
        self.full_vecs = np.stack(full_vecs) if full_vecs else np.zeros((0, COARSE * COARSE),
                                                                        np.float32)
        self.full_refs = full_refs


ZOOMS = (1.0, 1.06, 1.12, 1.2, 1.3, 1.45, 1.65, 1.9, 2.3)


def locate(S, T_gray, base_wh, zooms):
    """Best (score, x, y, w, h) of the delivered frame inside source frame S."""
    best = (-1.0, 0, 0, 0, 0)
    sh_, sw = S.shape
    bw, bh = base_wh
    for z in zooms:
        tw, th = int(round(bw / z)), int(round(bh / z))
        if tw < 12 or th < 12 or tw > sw or th > sh_:
            continue
        t = cv2.resize(T_gray, (tw, th), interpolation=cv2.INTER_AREA)
        if float(t.std()) < 2.0:
            continue
        res = cv2.matchTemplate(S, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best[0]:
            best = (float(mx), loc[0], loc[1], tw, th)
    return best


def base_box(sw, sh_, dw, dh):
    rd, rs = dw / float(dh), sw / float(sh_)
    if rd < rs:
        return int(round(sh_ * rd)), sh_
    return sw, int(round(sw / rd))


def residual(d_bgr, s_bgr_warped):
    """Fit a grade, return (changed block mask, colour shift ΔE, gain info)."""
    a = cv2.cvtColor(d_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(s_bgr_warped, cv2.COLOR_BGR2LAB).astype(np.float32)
    a[..., 0] *= 100.0 / 255.0
    b[..., 0] *= 100.0 / 255.0
    a[..., 1:] -= 128.0
    b[..., 1:] -= 128.0
    # Tolerate small misregistration: compare blurred pictures.
    a_ = cv2.GaussianBlur(a, (5, 5), 0)
    b_ = cv2.GaussianBlur(b, (5, 5), 0)
    La, Lb = a_[..., 0].ravel(), b_[..., 0].ravel()
    keep = np.ones_like(La, bool)
    gain, off = 1.0, 0.0
    for _ in range(3):
        if keep.sum() < 50:
            break
        A = np.stack([Lb[keep], np.ones(int(keep.sum()), np.float32)], axis=1)
        sol, *_ = np.linalg.lstsq(A, La[keep], rcond=None)
        gain, off = float(sol[0]), float(sol[1])
        keep = np.abs(La - (gain * Lb + off)) < 12.0
    fitted = gain * b_[..., 0] + off
    chroma_a = a_[..., 1:] - a_[..., 1:].reshape(-1, 2)[keep].mean(axis=0) if keep.any() else a_[..., 1:]
    chroma_b = b_[..., 1:] - b_[..., 1:].reshape(-1, 2)[keep].mean(axis=0) if keep.any() else b_[..., 1:]
    dl = np.abs(a_[..., 0] - fitted)
    dc = np.linalg.norm(chroma_a - chroma_b, axis=2)
    diff = dl + 0.35 * dc
    h, w = diff.shape
    bh, bw = h // BLOCK, w // BLOCK
    block = cv2.resize(diff, (bw, bh), interpolation=cv2.INTER_AREA)
    changed = block > CHANGED_L
    if keep.any():
        rawa, rawb = a_.reshape(-1, 3)[keep], b_.reshape(-1, 3)[keep]
        de = float(np.linalg.norm(rawa - rawb, axis=1).mean())
        sa, sb = float(np.hypot(rawa[:, 1], rawa[:, 2]).mean()), float(np.hypot(rawb[:, 1], rawb[:, 2]).mean())
    else:
        de, sa, sb = 99.0, 0.0, 0.0
    return changed, de, gain, off, (sa + 0.5) / (sb + 0.5)


def fixed_mark(samples):
    """Pixels that stay put while the picture changes (a watermark): they must
    not steer the match, or a dark frame matches only the mark."""
    if len(samples) < 6:
        return None
    pick = samples[:: max(1, len(samples) // 24)]
    st = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in pick]).astype(np.float32)
    sd = st.std(axis=0)
    if float(np.percentile(sd, 90)) < 12.0:
        return None                 # a static scene: nothing tells a mark apart
    mean = st.mean(axis=0)
    # Still, and standing out from what surrounds it: a dark area of the
    # scene is still too, and is no mark.
    around = cv2.blur(mean, (41, 41))
    mask = (sd < 6.0) & (np.abs(mean - around) > 22.0)
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    lab, n = ndimage.label(mask)
    keep = np.zeros_like(mask)
    for k in range(1, n + 1):
        part = lab == k
        if part.sum() <= 0.06 * mask.size:
            keep |= part
    return keep if keep.any() else None


def match_samples(samples, times, index, dw, dh):
    """One row per delivered sample: where it came from and what changed."""
    if not samples or not index.frames:
        return []
    mark = fixed_mark(samples)
    bw_blocks, bh_blocks = dw // BLOCK, dh // BLOCK
    dvec, ivec = [], []
    inset = None
    S0 = index.gray[0]
    sh0, sw0 = S0.shape
    r_s = sw0 / float(sh0)
    # The source as a band inside the frame (a 16:9 clip inside a 9:16 canvas).
    if abs(math.log((dw / float(dh)) / r_s)) > 0.15:
        if dw / float(dh) < r_s:
            ih = int(round(dw / r_s))
            inset = (0, (dh - ih) // 2, dw, ih)
        else:
            iw = int(round(dh * r_s))
            inset = ((dw - iw) // 2, 0, iw, dh)
    for f in samples:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        if mark is not None:
            g = g.copy()
            g[mark] = int(np.median(g))
        dvec.append(norm_vec(cv2.resize(g, (COARSE, COARSE), interpolation=cv2.INTER_AREA)))
        if inset:
            x, y, w, h = inset
            ivec.append(norm_vec(cv2.resize(g[y:y + h, x:x + w], (COARSE, COARSE),
                                            interpolation=cv2.INTER_AREA)))
    dvec = np.stack(dvec)
    sims = dvec @ index.vecs.T                      # samples × (frames·crops)
    rows = []
    prev_zoom, prev_src_i = None, None
    prev_t, prev_t_src = None, None
    isims = np.stack(ivec) @ index.full_vecs.T if inset and len(index.full_vecs) else None
    for si, (f, t) in enumerate(zip(samples, times)):
        s = sims[si]
        top = np.argpartition(-s, min(24, len(s) - 1))[:24]
        cands, seen = [], set()
        for k in top[np.argsort(-s[top])]:
            fi = index.refs[k][0]
            if fi not in seen:
                seen.add(fi)
                cands.append((float(s[k]), fi))
            if len(cands) >= 3:
                break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        blank = float(g.std()) < 4.0
        if mark is not None:
            g = g.copy()
            g[mark] = int(np.median(g))
        best = None
        # Time continuity: the frame the last match predicts, and the same
        # second of the source, are always tried: a dark or busy frame can
        # lose the coarse vote and still be exactly that frame.
        tt = np.asarray(index.times)
        for guess in ((prev_t_src + (t - prev_t)) if prev_t is not None else None, t):
            if guess is not None and len(tt):
                fi = int(np.argmin(np.abs(tt - guess)))
                if fi not in seen:
                    seen.add(fi)
                    cands.append((1.0, fi))
        for c, fi in cands:
            if c < 0.38:
                continue
            S = index.gray[fi]
            base = base_box(S.shape[1], S.shape[0], dw, dh)
            zooms = ZOOMS
            if prev_zoom is not None:
                near = [z for z in ZOOMS if abs(math.log(z / prev_zoom)) < 0.2]
                zooms = near or ZOOMS
            r = locate(S, g, base, zooms)
            if r[0] < 0.75 and prev_zoom is not None:
                r2 = locate(S, g, base, ZOOMS)
                r = r2 if r2[0] > r[0] else r
            if best is None or r[0] > best[0][0]:
                best = (r, fi, base)
        row = {"t": round(t, 3), "score": 0.0, "matched": False, "src_t": None,
               "src_i": None, "zoom": None, "cx": None, "cy": None,
               "changed": None, "de": None, "area": 0.0, "inset": False,
               "blank": blank}
        use_inset = False
        if isims is not None:
            fi = int(np.argmax(isims[si]))
            ic = float(isims[si][fi])
            if ic >= 0.62 and (best is None or best[0][0] < ic):
                use_inset = True
                x, y, w, h = inset
                S = index.frames[fi]
                warp = cv2.resize(S, (w, h), interpolation=cv2.INTER_AREA)
                ch, de, gain, off, sat = residual(f[y:y + h, x:x + w], warp)
                frac = (h * w) / float(dh * dw)
                mask = np.ones((bh_blocks, bw_blocks), bool)
                yb, xb = y // BLOCK, x // BLOCK
                hh = min(ch.shape[0], bh_blocks - yb)
                ww = min(ch.shape[1], bw_blocks - xb)
                mask[yb:yb + hh, xb:xb + ww] = ch[:hh, :ww]
                row.update(score=ic, matched=True, src_t=index.times[fi], src_i=fi,
                           zoom=1.0, cx=0.5, cy=0.5, changed=mask, de=de, gain=gain,
                           off=off, sat=sat, area=frac, inset=True)
                prev_zoom, prev_src_i = 1.0, fi
                prev_t, prev_t_src = t, index.times[fi]
        if not use_inset and best is not None and best[0][0] >= 0.62:
            (score, x, y, w, h), fi, base = best
            S = index.frames[fi]
            warp = cv2.resize(S[y:y + h, x:x + w], (dw, dh), interpolation=cv2.INTER_AREA)
            ch, de, gain, off, sat = residual(f, warp)
            row.update(score=score, matched=True, src_t=index.times[fi], src_i=fi,
                       zoom=base[0] / float(w), cx=(x + w / 2.0) / S.shape[1],
                       cy=(y + h / 2.0) / S.shape[0], changed=ch, de=de, gain=gain,
                       off=off, sat=sat, area=1.0)
            prev_zoom, prev_src_i = row["zoom"], fi
            prev_t, prev_t_src = t, index.times[fi]
        rows.append(row)
    return rows


# ── Audio ────────────────────────────────────────────────────────────────

def audio_bands(path):
    r = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0",
                        "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"],
                       capture_output=True)
    pcm = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    if len(pcm) < AUDIO_RATE // 2:
        return None
    win = np.hanning(512).astype(np.float32)
    n = 1 + (len(pcm) - 512) // AUDIO_HOP
    if n < 4:
        return None
    edges = np.geomspace(4, 256, AUDIO_BANDS + 1).astype(int)
    out = np.zeros((n, AUDIO_BANDS), np.float32)
    for i0 in range(0, n, 4096):
        idx = np.arange(i0, min(i0 + 4096, n))
        seg = np.stack([pcm[i * AUDIO_HOP:i * AUDIO_HOP + 512] for i in idx]) * win
        spec = np.abs(np.fft.rfft(seg, axis=1)) ** 2
        for b in range(AUDIO_BANDS):
            out[idx, b] = spec[:, edges[b]:max(edges[b + 1], edges[b] + 1)].mean(axis=1)
    rms = 20 * math.log10(float(np.sqrt((pcm ** 2).mean())) + 1e-6)
    return {"E": 10 * np.log10(out + 1e-9), "rms_db": round(rms, 1)}


def fingerprint(E):
    """Haitsma-Kalker style bits: sign of the band difference's change."""
    d = E[:, :-1] - E[:, 1:]
    return np.where(d[1:] - d[:-1] > 0, 1.0, -1.0).astype(np.float32)


def audio_align(del_a, src_as):
    """[(t_delivered, t_source, confidence)], source time on the concatenated
    timeline of the sources."""
    F_d = fingerprint(del_a["E"])
    srcE = [a["E"] for a in src_as]
    F_s = fingerprint(np.concatenate(srcE, axis=0))
    n = F_s.shape[0]
    L = 150                                 # 3 s chunks
    dur = F_d.shape[0] * AUDIO_HOP / AUDIO_RATE
    hop_s = max(1.0, dur / 300.0)
    hop = int(hop_s * AUDIO_RATE / AUDIO_HOP)
    N = sfft.next_fast_len(n + L, real=True)
    FS = sfft.rfft(F_s, N, axis=0)
    out = []
    for start in range(0, max(F_d.shape[0] - L, 1), hop):
        c = F_d[start:start + L]
        if c.shape[0] < L // 2:
            break
        FC = sfft.rfft(c, N, axis=0)
        cc = sfft.irfft((FS * np.conj(FC)).sum(axis=1), N)[:max(n - c.shape[0], 1)]
        k = int(np.argmax(cc))
        conf = float(cc[k]) / (c.shape[0] * c.shape[1])
        out.append((start * AUDIO_HOP / AUDIO_RATE, k * AUDIO_HOP / AUDIO_RATE, conf))
    return out


def audio_spans(align, thr=0.18):
    """Runs of constant offset: each run is one continuous stretch of source
    audio. Returns (spans, matched fraction)."""
    spans, cur = [], None
    good = 0
    for t, s, c in align:
        if c < thr:
            cur = None
            continue
        good += 1
        off = s - t
        if cur is not None and abs(off - cur["off"]) < 0.25:
            cur["end"] = t
            cur["n"] += 1
        else:
            cur = {"off": off, "start": t, "end": t, "n": 1}
            spans.append(cur)
    return [x for x in spans if x["n"] >= 2 or len(align) < 6], good / max(len(align), 1)


# The placed-sound counter (12 Nov, judge lane). The first counter compared
# the delivery with the source at one offset per 3 s run and one gain per run,
# so the social master's loudness over trimmed speech, and every syllable a few
# frames off its source, read as placed sounds: a film with 0 sounds in its EDL
# (p5591 floor) measured 18. Now every 20 ms frame finds its own source frame
# (the run's offset +-0.3 s, chosen by the local spectrum), the source is
# widened +-40 ms so an onset a frame early explains itself, and a placed
# sound counts only where the source at that aligned instant is not
# speech-shaped: a gap, where what the delivery holds over the source is
# sound the edit laid in. Hits within a second are one gesture (a whoosh
# into its impact, a hit and its tail).
_EDGES = np.geomspace(4, 256, AUDIO_BANDS + 1).astype(int)
_BAND_HZ = (_EDGES[:-1] + _EDGES[1:]) / 2.0 * AUDIO_RATE / 512.0
SPEECH_BAND = (_BAND_HZ >= 300) & (_BAND_HZ <= 3400)
HOP_S = AUDIO_HOP / AUDIO_RATE
FINE_SEARCH = 15       # hops: +-0.3 s around a run's offset
FINE_WIN = 12          # hops: +-0.24 s of spectrum picks a frame's source frame
ONSET_DIL = 2          # hops: +-40 ms of the source explains an onset
SPEECH_REL = -25.0     # dB: source speech band within 25 dB of its loud frames
SPEECH_SHARE = 0.5     # and half the frame's energy in 300-3400 Hz
SPEECH_GUARD = 5       # hops: +-0.1 s round a speech frame is still speech
ADDED_DB = 15.0        # dB the delivery holds over what the aligned source explains
AUDIBLE_REL = -40.0    # dB under the delivery's loud frames: quieter is not heard
OVER_VOICE_DB = 18.0   # dB over speech: reported (h6), never counted
MERGE_S = 1.0          # s: hits closer than this are one gesture
BED_SHARE = 0.5        # sound added in half the gaps or more is a bed
BED_OVER = 8.0         # dB: over a bed, a placed sound stands this far above it


def _level(E, mask=None):
    P = 10.0 ** (E / 10.0)
    if mask is not None:
        P = P[:, mask]
    return 10.0 * np.log10(P.sum(axis=1) + 1e-12)


def fine_map(E_d, E_s, spans):
    """Per delivered 20 ms frame: the source frame it plays (-1 where no run
    covers it), and how well it fits."""
    n, ns = E_d.shape[0], E_s.shape[0]
    cost = np.full(n, np.inf, np.float32)
    idx = np.full(n, -1, np.int64)
    for sp in spans:
        a = max(0, int((sp["start"] - 3.0) / HOP_S))
        b = min(n, int((sp["end"] + 6.0) / HOP_S))
        if b <= a:
            continue
        o = int(round(sp["off"] / HOP_S))
        seg = E_d[a:b]
        for sh in range(-FINE_SEARCH, FINE_SEARCH + 1):
            j = np.arange(a, b) + o + sh
            ok = (j >= 0) & (j < ns)
            if not ok.any():
                continue
            d = seg - E_s[np.clip(j, 0, ns - 1)]
            g = np.median(d[ok])
            c = np.where(ok, np.abs(d - g).mean(axis=1), 1e3)
            c = ndimage.uniform_filter1d(c, 2 * FINE_WIN + 1, mode="nearest") + 0.02 * abs(sh)
            m = c < cost[a:b]
            cost[a:b][m] = c[m]
            idx[a:b][m] = j[m]
    return idx, cost


def fine_joins(idx, min_jump=0.06, hold=0.3):
    """Delivered times where the source jumps (a keep join), from the fine map:
    the offset changes by min_jump s and holds for `hold` s on both sides."""
    have = idx >= 0
    if have.sum() < 10:
        return []
    off = np.where(have, idx - np.arange(len(idx)), np.nan).astype(np.float64)
    k = int(hold / HOP_S)
    good = np.where(have)[0]
    o = np.interp(np.arange(len(idx)), good, off[good])
    med = ndimage.median_filter(o, size=2 * k + 1, mode="nearest")
    out, i = [], k
    step = int(round(min_jump / HOP_S))
    while i < len(idx) - k:
        if abs(med[min(i + k, len(med) - 1)] - med[max(i - k, 0)]) >= step:
            # the frame where the offset turns
            seg = np.abs(np.diff(o[max(i - k, 0):i + k + 1]))
            j = max(i - k, 0) + int(np.argmax(seg)) + 1
            if have[j - 1] and have[min(j, len(idx) - 1)]:
                out.append(round(j * HOP_S, 2))
            i = j + k
        else:
            i += 1
    return out


def audio_added(del_a, src_as, spans):
    """Sound the edit laid over the source, and the placed sounds in it.
    Returns added_pct (of the measured programme), bed, and the times of the
    placed sounds; `_map` (private) is the per-frame source frame."""
    empty = {"added_pct": None, "events": 0, "times": [], "bed": False, "over_voice": []}
    if not spans:
        return empty
    E_d = del_a["E"]
    E_s = np.concatenate([a["E"] for a in src_as], axis=0)
    n, ns = E_d.shape[0], E_s.shape[0]
    idx, cost = fine_map(E_d, E_s, spans)
    have = idx >= 0
    if have.sum() < 10:
        return empty
    jj = np.clip(idx, 0, ns - 1)
    S = E_s[jj].copy()
    for k in range(1, ONSET_DIL + 1):
        S = np.maximum(S, E_s[np.clip(jj - k, 0, ns - 1)])
        S = np.maximum(S, E_s[np.clip(jj + k, 0, ns - 1)])
    Ls_sp, Ls_all = _level(E_s, SPEECH_BAND), _level(E_s)
    ref_s = np.percentile(Ls_sp, 95)
    sp_src = (Ls_sp > ref_s + SPEECH_REL) & (Ls_sp - Ls_all > 10 * np.log10(SPEECH_SHARE))
    sp_src = ndimage.maximum_filter1d(sp_src.astype(np.uint8), 2 * SPEECH_GUARD + 1).astype(bool)
    speech = have & sp_src[jj]
    # one gain per band (the master's EQ and loudness), set where the source speaks
    base = speech if speech.sum() > 50 else have
    eq = np.median(E_d[base] - S[base], axis=0)
    Ld = _level(E_d)
    X = ndimage.uniform_filter1d(Ld - _level(S + eq), 3, mode="nearest")
    ref_d = np.percentile(Ld[have], 95)
    audible = Ld > ref_d + AUDIBLE_REL
    gap = have & ~speech
    added = gap & (X > ADDED_DB) & audible
    bed = bool(gap.sum() > 50 and added[gap].mean() >= BED_SHARE)
    hit = added.copy()
    if bed:
        w = int(3.0 / HOP_S)
        lg = np.where(gap, Ld, np.nan)
        for i in np.where(hit)[0]:
            hit[i] = Ld[i] > np.nanmedian(lg[max(0, i - w):i + w + 1]) + BED_OVER

    def events(mask):
        out, last_hit, last_on = [], -1e9, -1e9
        for i in np.where(mask)[0]:
            t = i * HOP_S
            if t - last_hit >= 0.5 and t - last_on >= MERGE_S:
                out.append(round(t, 2))
                last_on = t
            last_hit = t
        return out
    times = events(hit)
    over = [t for t in events(speech & (X > OVER_VOICE_DB) & audible)
            if all(abs(t - u) >= MERGE_S for u in times)]
    return {"added_pct": round(100.0 * float(added[have].mean()), 1),
            "added_gap_pct": round(100.0 * float(added[gap].mean()), 1) if gap.any() else 0.0,
            "speech_pct": round(100.0 * float(speech[have].mean()), 1),
            "bed": bed, "events": len(times), "times": times, "over_voice": over,
            "_map": idx}


def audio_onsets(del_a):
    E = del_a["E"]
    flux = np.clip(E[1:] - E[:-1], 0, None).mean(axis=1)
    base = ndimage.median_filter(flux, size=101, mode="nearest")
    hits = np.where(flux > base + 5.0)[0]
    n, last = 0, -100
    for i in hits:
        if i - last > 12:
            n += 1
            last = i
    silent = float((E.max(axis=1) < E.max() - 45).mean())
    return {"onsets": n, "silence_pct": round(100 * silent, 1)}


def _window(path, t, tol):
    r = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{max(t - tol, 0):.3f}", "-i", path,
                        "-t", f"{2 * tol:.3f}", "-an", "-vf", "scale=64:36,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.uint8)
    if x.size < 64 * 36:
        return None
    return x[:x.size // (64 * 36) * 64 * 36].reshape(-1, 36, 64).astype(np.float32)


def _flat_means(x):
    return x.mean(axis=(1, 2))[x.std(axis=(1, 2)) < 4.0] if x is not None else np.zeros(0)


def _flat_or_flash(path, t, tol, flat_mean=None):
    """The upload around `t` s (native frame rate) holds a flat frame of the
    same kind as the delivered flash (black for black, white for white), or
    a frame that leaves and returns within two frames."""
    x = _window(path, t, tol)
    if x is None:
        return False
    fm = _flat_means(x)
    if len(fm) and (flat_mean is None or np.abs(fm - flat_mean).min() <= 40):
        return True
    for k in range(1, len(x) - 1):
        for back in (k + 1, k + 2):
            if back < len(x) and np.abs(x[k] - x[k - 1]).mean() > 25 and \
                    np.abs(x[back] - x[k - 1]).mean() < 0.35 * np.abs(x[k] - x[k - 1]).mean():
                return True
    return False


def upload_flashes(flashes, srcs, a_map, rows, tol=0.6, delivered=None):
    """The delivered flashes that are the upload's own (a black gap in a
    newscast): the source second the delivery plays there, from the fine
    audio map (the nearest mapped frame within 3 s, as a black gap is often
    silent) or else the matched picture, holds a flat frame or a flash."""
    if not flashes or not srcs:
        return []
    bounds, off = [], 0.0
    for s in srcs:
        d = max(s["info"]["video_duration"], 0.1)
        bounds.append((off, off + d, s["path"]))
        off += d
    matched = [r for r in rows or [] if r.get("matched")]
    own = []
    for f in flashes:
        st = None
        if a_map is not None and len(a_map):
            i = int(f / HOP_S)
            lo, hi = max(0, i - 150), min(len(a_map), i + 151)
            js = np.where(a_map[lo:hi] >= 0)[0]
            if len(js):
                j = lo + int(js[np.argmin(np.abs(lo + js - i))])
                st = (a_map[j] + (i - j)) * HOP_S
        if st is None and matched:
            r = min(matched, key=lambda r: abs(r["t"] - f))
            if abs(r["t"] - f) <= 3.0:
                st = r["src_t"] + (f - r["t"])
        if st is None:
            continue
        fm = _flat_means(_window(delivered, f, 0.15)) if delivered else np.zeros(0)
        for a, b, path in bounds:
            if a <= st < b:
                if _flat_or_flash(path, st - a, tol, float(np.median(fm)) if len(fm) else None):
                    own.append(f)
                break
    return own


# ── The whole delta ──────────────────────────────────────────────────────

def measure(delivered, sources=(), end_card="auto"):
    d_info = probe(delivered)
    dw, dh = size_for(d_info["width"], d_info["height"])
    dur = d_info["video_duration"] or d_info["duration"]
    cuts = cut_analysis(delivered, d_info)
    from . import picture_events as TC
    kinds = TC.cut_kinds(cuts.get("gray"), cuts["fps"], cuts["cuts"])
    cam = camera_moves(cuts.pop("gray", np.zeros((0, 1, 1))), cuts["fps"], cuts["cuts"])
    cuts.pop("score", None)
    sfps = min(SAMPLE_FPS, SAMPLE_MAX / max(dur, 1.0))
    samples = frames(delivered, dw, dh, fps=sfps)
    times = [(i + 0.5) / sfps for i in range(len(samples))]
    masks = [text_mask(f) for f in samples]
    ev, text_area = type_events(masks, times)
    del_audio = audio_bands(delivered) if d_info["has_audio"] else None
    out = {"version": 1, "mode": "paired" if sources else "standalone",
           "delivered": {"duration_s": round(dur, 3), "width": d_info["width"],
                         "height": d_info["height"], "has_audio": d_info["has_audio"]},
           "shots": len(shots_from(cuts["cuts"], dur)),
           "cuts": len(cuts["cuts"]),
           "cuts_per_min": round(60.0 * len(cuts["cuts"]) / max(dur, 1.0), 1),
           "flashes": len(cuts["flashes"]),
           "median_shot_s": round(float(np.median([b - a for a, b in shots_from(cuts["cuts"], dur)])), 2),
           "camera_moves": cam["moves"], "camera_zooms": cam["zooms"],
           "camera_moving_pct": cam["moving_pct"],
           "type_events": len(ev),
           "type_events_per_min": round(60.0 * len(ev) / max(dur, 1.0), 1),
           "text_area_pct": round(100 * text_area, 2)}
    if del_audio:
        out["audio"] = dict(audio_onsets(del_audio), rms_db=del_audio["rms_db"])
        out["audio_events"] = out["audio"]["onsets"]
        out["audio_events_per_min"] = round(60.0 * out["audio"]["onsets"] / max(dur, 1.0), 1)
    if not sources:
        out["class"] = "standalone"
        out["ceiling"] = None
        out["proof"] = (f"{out['cuts']} cuts ({out['cuts_per_min']}/min), {out['camera_moves']} camera moves, "
                        f"{out['type_events']} type events in {round(dur, 1)} s; no source to compare.")
        out["taste"] = TC.summarise(dur, kinds, cuts["flashes"])
        return out
    srcs = []
    for s in sources:
        try:
            srcs.append({"path": s, "info": probe(s)})
        except Exception as e:  # noqa: BLE001
            print("source skipped:", str(e)[:120], file=sys.stderr)
    if not srcs:
        out["class"] = "unknown"
        out["ceiling"] = None
        out["proof"] = "The source could not be read."
        return out
    src_total = sum(s["info"]["video_duration"] for s in srcs)
    index = SourceIndex(srcs, dw, dh)
    rows = match_samples(samples, times, index, dw, dh)
    # ── end card: a static unmatched tail of a few seconds ──
    end_s = 0.0
    if end_card == "auto" and rows:
        i = len(rows) - 1
        while i >= 0 and not rows[i]["matched"]:
            i -= 1
        tail = dur - (rows[i]["t"] if i >= 0 else 0.0) if i < len(rows) - 1 else 0.0
        if END_CARD_RANGE[0] <= tail <= END_CARD_RANGE[1]:
            end_s = tail
    elif isinstance(end_card, (int, float)):
        end_s = float(end_card)
    prog = max(dur - end_s, 0.1)
    # A black or flat frame says nothing about where it came from.
    live = [r for r in rows if r["t"] <= prog and not r["blank"]]
    matched = [r for r in live if r["matched"]]
    blocks = (dh // BLOCK) * (dw // BLOCK)
    # ── a small fixed mark (the watermark) is not an edit ──
    persistent = None
    if matched:
        stack = np.stack([r["changed"] for r in matched]).astype(np.float32)
        rate = stack.mean(axis=0)
        cand = rate >= 0.7
        if cand.any():
            sm = np.stack([cv2.resize(cv2.cvtColor(samples[int(round(r["t"] * sfps - 0.5))],
                                                   cv2.COLOR_BGR2GRAY),
                                      (dw // BLOCK, dh // BLOCK), interpolation=cv2.INTER_AREA)
                           for r in matched[:: max(1, len(matched) // 40)]])
            still = sm.std(axis=0) < 10.0
            persistent = cand & still
            lab, num = ndimage.label(persistent)
            keep = np.zeros_like(persistent)
            for k in range(1, num + 1):
                part = lab == k
                if part.sum() <= 0.03 * blocks:
                    keep |= part
            persistent = keep if keep.any() else None
    def changed_frac(r):
        m = r["changed"]
        if persistent is not None:
            return float((m & ~persistent).sum()) / max(blocks - int(persistent.sum()), 1)
        return float(m.mean())
    # Inside a matched sample the blocks that are not the source are what the
    # edit put on top. Median over a short window, so a moving subject sampled
    # a beat away from its source frame does not read as a graphic.
    ch = np.array([changed_frac(r) if r["matched"] else 1.0 for r in live]) if live else np.zeros(0)
    if len(ch) >= 5:
        ch_sm = ndimage.median_filter(ch, size=5, mode="nearest")
    else:
        ch_sm = ch
    matched_flags = np.array([r["matched"] for r in live]) if live else np.zeros(0, bool)
    if len(matched_flags) >= 3:
        # one stray sample does not make a shot of its own
        matched_flags = ndimage.median_filter(matched_flags.astype(np.uint8), size=3, mode="nearest").astype(bool)
    unmatched_pct = 100.0 * (1.0 - float(matched_flags.mean())) if len(matched_flags) else 100.0
    overlay_pct = 100.0 * float(ch_sm[matched_flags].mean()) if matched_flags.any() else 0.0
    unchanged_pct = 100.0 * float(((1.0 - ch_sm) * matched_flags).mean()) if len(ch_sm) else 0.0
    # ── framing moves the edit added ──
    zs = [(r["t"], r["zoom"], r["cx"], r["cy"]) for r in matched if not r["inset"]]
    added_moves = 0
    punch = 0
    if len(zs) >= 3:
        z = np.array([x[1] for x in zs], np.float32)
        cx = np.array([x[2] for x in zs], np.float32)
        z_s = ndimage.median_filter(z, size=3, mode="nearest")
        steps = np.abs(np.diff(np.log(z_s)))
        punch = int((steps > 0.06).sum())
        # a reframe that drifts: the crop centre travels within a shot
        cx_s = ndimage.median_filter(cx, size=5, mode="nearest")
        added_moves = punch + int((np.abs(np.diff(cx_s)) > 0.04).sum() // 3)
    zoom_median = float(np.median([x[1] for x in zs])) if zs else None
    # ── audio ──
    src_a = []
    a_spans, a_frac, a_add = [], 0.0, {"added_pct": None, "events": 0}
    if del_audio:
        for s in srcs:
            if s["info"]["has_audio"]:
                try:
                    a = audio_bands(s["path"])
                except Exception:  # noqa: BLE001
                    a = None
                if a:
                    src_a.append(a)
    if del_audio and src_a:
        align = audio_align(del_audio, src_a)
        a_spans, a_frac = audio_spans(align)
        a_add = audio_added(del_audio, src_a, a_spans)
    # ── order: how the delivered seconds walk through the source ──
    jumps, back = 0, 0
    if len(a_spans) >= 1:
        ord_spans = sorted(a_spans, key=lambda x: x["start"])
        for p, q in zip(ord_spans[:-1], ord_spans[1:]):
            jumps += 1
            if q["start"] + q["off"] < p["end"] + p["off"] - 1.0:
                back += 1
        used_s = sum(sp["end"] - sp["start"] + 3.0 for sp in a_spans)
    else:
        seq = [r["src_t"] for r in matched]
        for p, q in zip(seq[:-1], seq[1:]):
            if q < p - 2.5:
                back += 1
            if abs(q - p) > 2.5:
                jumps += 1
        used_s = len(set(int(x) for x in seq)) * 1.0
    used_pct = 100.0 * min(used_s, src_total) / max(src_total, 0.1)
    # ── shots that are not the source ──
    shots = shots_from([c for c in cuts["cuts"] if c < prog], prog)
    shots_added = 0
    for a, b in shots:
        rr = [i for i, r in enumerate(live) if a <= r["t"] < b]
        if rr and float(matched_flags[rr].mean()) < 0.34:
            shots_added += 1
    # A delivered cut is the edit's when the source does not continue through
    # it. Where the source's sound runs on through the cut, the sound decides:
    # it pins the source time far better than a look-alike frame in a fast
    # montage. Without usable sound the picture decides.
    audio_rules = a_frac >= 0.5
    if audio_rules:
        # Each inserted shot opens and closes one cut; a jump in the source's
        # sound is one cut.
        cuts_added = min(jumps + 2 * shots_added, jumps + len(cuts["cuts"]))
    else:
        cuts_added = jumps
        for c in cuts["cuts"]:
            if c >= prog:
                continue
            rr = [r for r in live if abs(r["t"] - c) < 0.7]
            before = [r for r in rr if r["t"] < c]
            after = [r for r in rr if r["t"] >= c]
            if before and after:
                b, a = before[-1], after[0]
                if not (b["matched"] and a["matched"]) or \
                        abs((a["src_t"] - b["src_t"]) - (a["t"] - b["t"])) > 1.0:
                    cuts_added += 1
    # ── type the edit added: type where the picture is not the source. A
    # flicker of one sample is a moving subject, not a caption. ──
    added_ev = 0
    if matched:
        rmasks = []
        by_t = {r["t"]: m for r, m in zip(rows, masks)}
        for r in live:
            m = by_t[r["t"]]
            if r["matched"]:
                bm = cv2.resize(m.astype(np.float32), (dw // BLOCK, dh // BLOCK),
                                interpolation=cv2.INTER_AREA) > 0.2
                hit = bm & r["changed"]
                rmasks.append(hit.astype(np.uint8) if hit.mean() >= 0.004 else
                              np.zeros_like(hit, np.uint8))
            else:
                rmasks.append(np.zeros((dh // BLOCK, dw // BLOCK), np.uint8))
        held = []
        for i, m in enumerate(rmasks):
            nb = [rmasks[j] for j in (i - 1, i + 1) if 0 <= j < len(rmasks)]
            held.append(m if any((m & x).sum() >= 0.5 * max(m.sum(), 1) for x in nb) else np.zeros_like(m))
        e2, _ = type_events(held, [r["t"] for r in live])
        added_ev = len(e2)
    # ── colour ──
    des = [r["de"] for r in matched if r.get("de") is not None]
    de = float(np.median(des)) if des else None
    sats = [r.get("sat") for r in matched if r.get("sat")]
    gains = [r.get("gain") for r in matched if r.get("gain")]
    offs = [r.get("off") for r in matched if r.get("off") is not None]
    # ── the numbers ──
    out.update({
        "source": {"count": len(srcs), "duration_s": round(src_total, 3),
                   "width": srcs[0]["info"]["width"], "height": srcs[0]["info"]["height"],
                   "frames_indexed": len(index.frames)},
        "end_card_s": round(end_s, 2),
        "program_s": round(prog, 3),
        "duration_change_s": round(prog - src_total, 2),
        "duration_ratio": round(prog / max(src_total, 0.1), 3),
        "unchanged_pct": round(unchanged_pct, 1),
        "unmatched_pct": round(unmatched_pct, 1),
        "overlay_pct": round(overlay_pct, 1),
        "persistent_mark_pct": round(100.0 * float(persistent.sum()) / blocks, 2) if persistent is not None else 0.0,
        "reframe_zoom": round(zoom_median, 3) if zoom_median else None,
        "punch_ins": punch,
        "camera_moves_added": added_moves,
        "shots_added": shots_added,
        "cuts_added": cuts_added,
        "reorders": back,
        "source_used_pct": round(used_pct, 1),
        "type_events_added": added_ev,
        "colour_shift_de": round(de, 2) if de is not None else None,
        "luma_gain": round(float(np.median(gains)), 3) if gains else None,
        "luma_offset": round(float(np.median(offs)), 2) if offs else None,
        "saturation_ratio": round(float(np.median(sats)), 3) if sats else None,
        "audio_source_pct": round(100 * a_frac, 1) if del_audio and src_a else None,
        "audio_breaks": jumps,
        "audio_added_pct": a_add["added_pct"],
        "audio_events_added": a_add["events"],
        "audio_over_voice": a_add.get("over_voice") or [],
    })
    # TASTE (week-1112) ceilings: joins in the source's sound are speech edits.
    # A 3 s run knows a join only to its chunk; the fine map pins it to 20 ms.
    ords = sorted(a_spans, key=lambda x: x["start"])
    coarse = [(p["end"], q["start"] + 3.0) for p, q in zip(ords[:-1], ords[1:])]
    fj = fine_joins(a_add["_map"]) if a_add.get("_map") is not None else []
    joins = [(round(t - 0.2, 2), round(t + 0.2, 2)) for t in fj] + \
        [c for c in coarse if not any(c[0] - 0.35 <= t <= c[1] + 0.35 for t in fj)]
    # A flash the upload already has (a black gap in a newscast) is not the edit's.
    own = upload_flashes(cuts["flashes"], srcs, a_add.get("_map"), rows, delivered=delivered)
    out["flashes_upload_own"] = len(own)
    edit_flashes = [f for f in cuts["flashes"] if f not in own]
    out["taste"] = TC.summarise(prog, kinds, edit_flashes, joins, a_add.get("times"),
                                a_add["added_pct"], paired=bool(del_audio and src_a),
                                bed=a_add.get("bed"), over_voice=a_add.get("over_voice"))
    out.update(classify(out))
    return out


def classify(m):
    """The provable part. Numbers in, one class out."""
    un, nomatch, ov = m["unchanged_pct"], m["unmatched_pct"], m["overlay_pct"]
    de = m["colour_shift_de"] if m["colour_shift_de"] is not None else 0.0
    added_audio = m["audio_added_pct"] or 0.0
    cuts_rate = 60.0 * m["cuts_added"] / max(m["program_s"], 1.0)
    moves = m["camera_moves_added"]
    # The source's sound running unbroken under the picture proves the order:
    # a few look-alike frames that lost their match mean nothing then.
    sound_whole = (m["audio_source_pct"] or 0) >= 90 and m["cuts_added"] == 0
    picture_kept = un >= 85 and nomatch <= (8 if sound_whole else 4) and m["shots_added"] == 0
    thin_type = ov < 2.0 or (m["type_events_added"] <= 2 and ov < 3.0)
    # The placed-sound counter reads 0 on a film with no sound in its EDL (12 Nov),
    # so one placed sound, or one standing over the voice, is something added.
    no_sound = added_audio < 15 and m["audio_events_added"] == 0 and not m.get("audio_over_voice")
    cls, ceil = "edited", None
    whole = m["source_used_pct"] >= 80 and m["reorders"] == 0 and m["cuts_added"] <= 1
    if picture_kept and thin_type and de < 5.0 and moves <= 1 and no_sound:
        if whole:
            cls, ceil = "same_upload", 2
        elif m["reorders"] <= 1 and cuts_rate <= 12:
            cls, ceil = "trim_only", 3
    if cls == "edited":
        # One thin layer: no new pictures, hardly any cuts, under an eighth of
        # the frame touched, whatever the layer is (captions, a grade, a bed).
        if nomatch <= 8 and m["shots_added"] <= 1 and ov < 12.0 and cuts_rate <= 8 and moves <= 3:
            cls, ceil = "light_touch", 4
        # A look over the same take: no new picture, no cut, no type, no move.
        # A strong grade or vignette can touch a quarter of the frame and it
        # is still one layer (p5275: 15% touched, nothing else; rated 6 before).
        elif nomatch <= 8 and m["shots_added"] == 0 and m["cuts_added"] == 0 \
                and m["type_events_added"] == 0 and moves <= 1 and ov < 25.0:
            cls, ceil = "light_touch", 4
    # Nothing matched although the source's sound runs under the picture, or
    # no source frame could be read: that is a failed comparison, not proof of
    # a new picture. No class is claimed and no ceiling fires.
    indexed = (m.get("source") or {}).get("frames_indexed", 1)
    if indexed == 0 or (nomatch >= 90 and (m["audio_source_pct"] or 0) >= 50):
        why = "no frame of the upload could be read" if indexed == 0 else "no frame matched"
        return {"class": "unknown", "ceiling": None,
                "proof": (f"The picture could not be compared with the upload: {why}, while "
                          f"{m['audio_source_pct'] or 0:.0f}% of the sound is the upload's. Judged from the frames.")}
    who = {"same_upload": "The delivered video is essentially the upload",
           "trim_only": "The upload with parts removed and nothing added",
           "light_touch": "One thin layer over the upload",
           "edited": "A real change of the picture"}[cls]
    proof = (f"{who}: {un:.0f}% of the picture is the unchanged source, "
             f"{nomatch:.0f}% is not from it; {m['shots_added']} shots added, "
             f"{m['cuts_added']} cuts added, {ov:.1f}% overlay, "
             f"{m['type_events_added']} type events added, {moves} camera moves added, "
             f"colour shift ΔE {de:.1f}, {m['program_s']:.0f} s from {m['source']['duration_s']:.0f} s.")
    return {"class": cls, "ceiling": ceil, "proof": proof}

