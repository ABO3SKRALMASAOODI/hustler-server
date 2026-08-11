"""Deterministic spatial perception for edit decisions.

The filmstrip is excellent context for a model, but it is not a data structure
the compiler can enforce. This sidecar turns real source frames into a compact
time track of face boxes, text-like regions and dense-UI flags. It never
renders pixels and never asks an LLM to estimate coordinates.

The analysis is cached inside the index under its own version, so old indexes
gain it lazily without a global pipeline-version bump/re-index storm.
"""

from __future__ import annotations

from concurrent import futures
import os
import threading

import subject
import tiles as tilestrip


SPATIAL_VERSION = 1
# Reusing every one of the 144 filmstrip JPEGs added ~18s of OpenCV work on
# the indexing critical path. Sixty-four evenly retained coarse moments plus
# up to 32 real shot-boundary moments cover both long static takes and cuts
# while keeping the deterministic perception lane under a hard 96-frame cap.
MAX_SAMPLES = 96
FILMSTRIP_SAMPLE_BUDGET = 64
MIN_STEP_S = 1.0
# Require persistence across a clear majority of speaking samples. The
# audited clean close-up produced isolated MSER line-shapes on a jacket in
# 3/7 frames (0.429); actual burned captions persisted in 9/12 (0.75).
BURNED_CAPTION_BLOCK_SCORE = 0.60


_detector_local = threading.local()


class SpatialError(RuntimeError):
    pass


def plan_times(duration, shots=None, keep=None, max_samples=MAX_SAMPLES):
    """Bounded source moments covering runtime plus real shot changes."""
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return []
    spans = [(max(0.0, float(a)), min(duration, float(b)))
             for a, b in (keep or [[0.0, duration]]) if float(b) > float(a)]
    budget = max(8, int(max_samples))
    kept_dur = sum(b - a for a, b in spans) or duration
    # Reserve at most 24 slots for shot changes, but never let that reserve
    # consume a small caller's whole budget (max_samples=20 previously meant
    # one giant 12s step and a single frame).
    shot_reserve = min(24, max(1, budget // 3))
    step = max(MIN_STEP_S, kept_dur / max(1, budget - shot_reserve))
    times = []
    for a, b in spans:
        t = a + min(0.5, max(0.05, (b - a) / 2.0))
        while t < b - 0.02:
            times.append(t)
            t += step
    # Cuts are where composition most often changes. Add the first readable
    # frame and midpoint of each shot; a later even sampler preserves their
    # coverage without allowing a 2,000-cut screen recording to explode.
    for shot in shots or []:
        get = shot.get if isinstance(shot, dict) else \
            lambda k, _s=shot: getattr(_s, k)
        try:
            a, b = float(get("start")), float(get("end"))
        except (TypeError, ValueError):
            continue
        if not any(x < b and a < y for x, y in spans):
            continue
        times.extend((min(max(a + 0.12, 0.0), duration - 0.02),
                      min(max((a + b) / 2.0, 0.0), duration - 0.02)))
    ordered = []
    for t in sorted(times):
        t = round(min(max(t, 0.0), max(0.0, duration - 0.02)), 2)
        if not ordered or t - ordered[-1] >= 0.12:
            ordered.append(t)
    if len(ordered) <= budget:
        return ordered
    # Evenly retain the whole program, including both ends.
    idxs = {round(i * (len(ordered) - 1) / (budget - 1))
            for i in range(budget)}
    return [ordered[i] for i in sorted(idxs)]


def _cv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _face_detectors(cv2):
    """One cascade set per worker thread, not two XML loads per frame."""
    cascades = getattr(_detector_local, "cascades", None)
    if cascades is None:
        cascades = subject._cascades(cv2)
        _detector_local.cascades = cascades
    return cascades


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = max(1, (a[2] - a[0]) * (a[3] - a[1]) +
                (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union


def _text_regions(cv2, gray):
    """Broad text-line boxes from MSER character components.

    Recognition is unnecessary for collision avoidance. The detector is
    deliberately conservative: it requires several character-like components
    sharing a baseline, which rejects most natural edges while retaining
    subtitles, titles and UI labels in any language.
    """
    h, w = gray.shape[:2]
    try:
        mser = cv2.MSER_create()
        if hasattr(mser, "setMinArea"):
            mser.setMinArea(max(8, int(w * h * 0.00003)))
            mser.setMaxArea(max(40, int(w * h * 0.025)))
        regions, boxes = mser.detectRegions(gray)
    except Exception:
        return []
    chars = []
    for pts, raw in zip(regions, boxes):
        x, y, bw, bh = (int(v) for v in raw)
        if not (max(4, int(h * 0.012)) <= bh <= int(h * 0.11)):
            continue
        if not (2 <= bw <= int(w * 0.22)):
            continue
        ar = bw / max(1.0, bh)
        if not (0.08 <= ar <= 7.0):
            continue
        if len(pts) / max(1.0, bw * bh) < 0.12:
            continue
        box = (x, y, x + bw, y + bh)
        # MSER returns nested regions for the same glyph. Keep one.
        if any(_iou(box, old) > 0.72 for old in chars):
            continue
        chars.append(box)
    chars.sort(key=lambda b: ((b[1] + b[3]) / 2.0, b[0]))
    lines = []
    for box in chars:
        cy = (box[1] + box[3]) / 2.0
        bh = box[3] - box[1]
        best = None
        for line in lines:
            ly = line["cy"] / line["n"]
            lh = line["h"] / line["n"]
            gap = max(0, box[0] - line["x1"], line["x0"] - box[2])
            if abs(cy - ly) <= max(bh, lh) * 0.38 and gap <= w * 0.075:
                best = line
                break
        if best is None:
            lines.append({"x0": box[0], "y0": box[1], "x1": box[2],
                          "y1": box[3], "cy": cy, "h": bh, "n": 1})
        else:
            best["x0"] = min(best["x0"], box[0])
            best["y0"] = min(best["y0"], box[1])
            best["x1"] = max(best["x1"], box[2])
            best["y1"] = max(best["y1"], box[3])
            best["cy"] += cy
            best["h"] += bh
            best["n"] += 1
    out = []
    for line in lines:
        bw, bh = line["x1"] - line["x0"], line["y1"] - line["y0"]
        if line["n"] < 4 or bw < w * 0.075 or bh > h * 0.14:
            continue
        pad_x, pad_y = int(w * 0.008), int(h * 0.008)
        out.append([round(max(0, line["x0"] - pad_x) / w, 4),
                    round(max(0, line["y0"] - pad_y) / h, 4),
                    round(min(w, line["x1"] + pad_x) / w, 4),
                    round(min(h, line["y1"] + pad_y) / h, 4)])
    # Merge overlapping word groups into the broad line the caption compiler
    # must avoid.
    merged = []
    for box in sorted(out, key=lambda b: (b[1], b[0])):
        hit = next((m for m in merged if
                    min(m[3], box[3]) - max(m[1], box[1]) > 0 and
                    max(m[0], box[0]) - min(m[2], box[2]) < 0.04), None)
        if hit:
            hit[:] = [min(hit[0], box[0]), min(hit[1], box[1]),
                      max(hit[2], box[2]), max(hit[3], box[3])]
        else:
            merged.append(list(box))
    return merged[:24]


def analyze_frame(path):
    cv2 = _cv()
    if cv2 is None:
        return {"faces": [], "text": [], "dense_ui": False}
    img = cv2.imread(path)
    if img is None:
        return {"faces": [], "text": [], "dense_ui": False}
    h, w = img.shape[:2]
    # Geometry is normalized and both detectors are scale-invariant. Large
    # source stills/contact sheets otherwise turn a bounded sidecar into a
    # minute-long CPU pass for no additional placement accuracy.
    if w > 360:
        scale = 360.0 / w
        img = cv2.resize(img, (360, max(1, int(h * scale))))
        h, w = img.shape[:2]
    raw_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Histogram equalization helps Haar on uneven faces but amplifies JPEG
    # block noise into thousands of fake character components. Text detection
    # therefore reads the un-equalized pixels.
    gray = cv2.equalizeHist(raw_gray)
    cascades = _face_detectors(cv2)
    faces = []
    # Most talking-head frames are frontal. Avoid paying for the profile
    # cascade unless the frontal detector found nothing; profile speakers and
    # no-face B-roll still receive the fallback detector.
    face_hits = subject._faces_in(cv2, cascades[:1], gray)
    if not face_hits and len(cascades) > 1:
        face_hits = subject._faces_in(cv2, cascades[1:], gray)
    for x, y, bw, bh in face_hits[:2]:
        faces.append([round(x / w, 4), round(y / h, 4),
                      round((x + bw) / w, 4), round((y + bh) / h, 4)])
    text = _text_regions(cv2, raw_gray)
    edges = cv2.Canny(raw_gray, 80, 180)
    edge_density = float((edges > 0).sum()) / max(1, edges.size)
    dense_ui = bool(len(text) >= 5 and edge_density >= 0.07)
    return {"faces": faces, "text": text, "dense_ui": dense_ui,
            "edge_density": round(edge_density, 4),
            # Flat black/white decode gaps are objectively empty; luma and
            # spread let placement tools refuse those windows without asking
            # a model whether a blank frame contains something meaningful.
            "mean_luma": round(float(raw_gray.mean()), 2),
            "std_luma": round(float(raw_gray.std()), 2)}


def analyze_video(media_path, duration, workdir, shots=None, keep=None,
                  max_samples=MAX_SAMPLES):
    times = plan_times(duration, shots=shots, keep=keep,
                       max_samples=max_samples)
    if not times:
        raise SpatialError("video has no sampleable duration")
    frame_dir = os.path.join(workdir, "spatial_frames")
    frames = tilestrip.extract_frames(
        media_path, times, frame_dir,
        seek_ceiling=max(0.0, float(duration) - 0.05),
        parallelism=4, width=360)
    return analyze_frames(times, frames)


def analyze_frames(times, frames, max_samples=None):
    """Build a sidecar from an existing {time-index: jpeg} frame set."""
    work = [(i, t, frames[i]) for i, t in enumerate(times) if i in frames]
    if max_samples is not None and len(work) > int(max_samples):
        budget = max(1, int(max_samples))
        if budget == 1:
            work = [work[len(work) // 2]]
        else:
            picks = {round(i * (len(work) - 1) / (budget - 1))
                     for i in range(budget)}
            work = [work[i] for i in sorted(picks)]

    def _one(row):
        _i, t, path = row
        return {"t": t, **analyze_frame(path)}

    # OpenCV releases the GIL for its detectors. Four bounded workers reuse
    # their own cascade objects and keep result order stable, materially
    # reducing upload/index wait without competing with the earlier frame
    # extraction lane (which has already completed here).
    with futures.ThreadPoolExecutor(
            max_workers=max(1, min(4, len(work)))) as pool:
        samples = list(pool.map(_one, work))
    if not samples:
        raise SpatialError("could not decode any spatial sample frames")
    steps = [b["t"] - a["t"] for a, b in zip(samples, samples[1:])]
    return {"v": SPATIAL_VERSION, "samples": samples,
            "sample_step_s": round(sorted(steps)[len(steps) // 2], 2)
            if steps else None}


def augment_with_shot_frames(media_path, duration, workdir, sidecar, shots,
                             max_samples=MAX_SAMPLES):
    """Fill a coarse filmstrip sidecar with a bounded shot-change sample.

    The indexer already decoded up to 144 filmstrip moments. Reusing a bounded
    evenly-spaced subset is
    fast, but on a long recording they can be 13-52 seconds apart—the exact
    gap that let a later shot show nothing useful inside a chosen dimension.
    Keep those free samples and spend only the remaining budget (normally 32)
    on real shot starts/midpoints. This is not a second video scan.
    """
    current = list((sidecar or {}).get("samples") or [])
    budget = max(0, int(max_samples) - len(current))
    if budget <= 0 or not shots:
        return sidecar
    existing = [float(s.get("t", 0)) for s in current]
    duration = float(duration or 0.0)

    candidates = []
    for shot in shots:
        get = shot.get if isinstance(shot, dict) else \
            lambda key, _shot=shot: getattr(_shot, key)
        try:
            a, b = float(get("start")), float(get("end"))
        except (TypeError, ValueError):
            continue
        for t in (a + 0.12, (a + b) / 2.0):
            t = round(min(max(t, 0.0), max(0.0, duration - 0.02)), 2)
            if any(abs(t - old) <= 0.35 for old in existing):
                continue
            if not candidates or abs(t - candidates[-1]) > 0.12:
                candidates.append(t)
    candidates = sorted(set(candidates))
    if len(candidates) > budget:
        if budget == 1:
            candidates = [candidates[len(candidates) // 2]]
        else:
            picks = {round(i * (len(candidates) - 1) / (budget - 1))
                     for i in range(budget)}
            candidates = [candidates[i] for i in sorted(picks)]
    if not candidates:
        return sidecar
    frame_dir = os.path.join(workdir, "spatial_shot_frames")
    frames = tilestrip.extract_frames(
        media_path, candidates, frame_dir,
        seek_ceiling=max(0.0, duration - 0.05), parallelism=4, width=360)
    try:
        extra = analyze_frames(candidates, frames).get("samples") or []
    except SpatialError:
        return sidecar
    merged = {round(float(s.get("t", 0)), 2): s for s in current}
    for sample in extra:
        merged[round(float(sample.get("t", 0)), 2)] = sample
    samples = [merged[t] for t in sorted(merged)]
    steps = [b["t"] - a["t"] for a, b in zip(samples, samples[1:])]
    return {"v": SPATIAL_VERSION, "samples": samples,
            "sample_step_s": round(sorted(steps)[len(steps) // 2], 2)
            if steps else None}


def _during_speech(t, words):
    # A +/- 1s window accommodates a sample clock that does not land inside a
    # short word but clearly depicts the same speaking moment.
    return any(float(w.get("t0", 0)) - 1.0 <= t <=
               float(w.get("t1", 0)) + 1.0 for w in words)


def burned_caption_score(sidecar, words):
    """0..1 evidence that transcript-like text is already burned in."""
    candidates = [s for s in (sidecar or {}).get("samples") or []
                  if _during_speech(float(s.get("t", 0)), words or []) and
                  not s.get("dense_ui")]
    if not candidates:
        return 0.0
    hits = 0
    for sample in candidates:
        for x0, y0, x1, y1 in sample.get("text") or []:
            width, height = x1 - x0, y1 - y0
            central = x1 >= 0.18 and x0 <= 0.82
            subtitle_band = y1 >= 0.48 and y0 <= 0.93
            # Burned subtitles are line-like components. Portrait texture,
            # a microphone/collar and hands against a jacket can all merge
            # into broad MSER regions; the audited clean Elon close-up did
            # exactly that at y=.89-1.0. A real caption line is neither a
            # tall blob nor clipped by the frame edge, and is materially
            # wider than it is tall.
            line_like = (0.018 <= height <= 0.13 and
                         width / max(height, 1e-6) >= 2.0 and
                         y1 < 0.97)
            if central and subtitle_band and width >= 0.13 and line_like:
                hits += 1
                break
    return round(hits / len(candidates), 3)


def get_or_compute_for_index(worker_db, dbx, index_row, media_path, workdir,
                             keep=None):
    idx = index_row.get("json") or {}
    sidecar = idx.get("spatial")
    if isinstance(sidecar, dict) and sidecar.get("v") == SPATIAL_VERSION:
        return sidecar
    video = idx.get("video") or {}
    sidecar = analyze_video(
        media_path, video.get("duration"), workdir,
        shots=idx.get("shots") or [], keep=keep)
    try:
        worker_db.run(dbx.set_index_spatial, index_row["video_sha256"],
                      sidecar, index_row.get("pipeline_version"))
    except Exception as exc:
        print(f"[spatial] sidecar persist failed (non-fatal): {exc}",
              flush=True)
    return sidecar
