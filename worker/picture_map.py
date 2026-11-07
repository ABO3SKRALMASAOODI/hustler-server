"""What one uploaded file already holds: every change of picture baked into
it, and where each picture really sits in the frame.

Measured on pixels, one decode per source, on the executor (a ``frames`` job
with ``picture_map``), and kept on the asset row. An upload that is already a
montage or a slideshow reaches us as one file; the customer sees photos and
cuts, we see one kept span. p5520: 83 indexed shots against 90 real changes
of picture, so seven photos could never get the dissolve the customer asked
for, and index times rounded to the centisecond put a third of the indexed
cuts one frame late (the outgoing side's last frame was already the next
photo, which is what a dissolve then held).

The map is a measurement, never a judgement: frame differences for the cuts,
row and column profiles for the picture's rectangle. A cut is a frame unlike
its predecessor by much more than its neighbours are, and unlike the frames
two back too (a one-frame flash comes straight back). A rectangle is what is
left after the uniform bars on each side.

    {"v": 1, "fps": 30.0, "frames": 10430, "duration": 347.67,
     "width": 1920, "height": 1080,
     "cuts": [4.63, ...],            # keep-grid seconds of each change
     "shots": [{"start": 0.0, "end": 4.63, "still": true,
                "rect": [0.0, 0.0, 1.0, 1.0], "bars": false,
                "best": 2.0, "flat": false}, ...]}

``cuts`` and ``start``/``end`` sit on the EDL's centisecond keep grid, at the
last centisecond not after the new picture's first frame, so a kept span
split there starts exactly on that frame (renderer.first_frame_at) whichever
way the source's frame times round.
"""

import math
import subprocess

import numpy as np

VERSION = 1
# The analysis frame. Rectangles are read on it, so its width is what they
# are good to: 1/640 of the frame, under two pixels of a 1080p export.
ANALYSIS_W = 640
THREADS = 2
# Every Nth frame of a shot feeds its rectangle (bars don't move).
SAMPLE_EVERY = 15
# A cut: the frame's mean absolute difference from its predecessor, 0-255.
CUT_FLOOR = 2.0                 # on a still, anything real clears this
CUT_FLOOR_MOVING = 12.0         # moving footage changes this much by itself
MOVING_BASE = 0.3               # neighbours differing this much are moving
CUT_OVER_NEIGHBOURS = 4.0       # and on it a cut towers over them
NEIGHBOURS = 15                 # frames either side of the candidate
# The picture stays changed two frames on: a flash comes back.
CUT_STAYS = 0.5
# Changes of picture this close are one change (a blended frame on the join).
CUT_MERGE_FRAMES = 2
MIN_SHOT_FRAMES = 3
STILL_DIFF = 0.5                # within-shot difference of a held photo
FLAT_STD = 14.0                 # a sample this even holds no picture
STILL_FRAME = 0.05              # below this a frame repeats its predecessor
BAR_TOLERANCE = 14              # 0-255: how far a bar pixel strays
BAR_CONTENT_SHARE = 0.015       # of a row or column, to count as picture
BARS_MIN_SHARE = 0.02           # of the frame, to count as bars at all
SOURCE_MAX_S = 2700.0           # beyond this the index's shots stand


def cut_grid(frame, fps):
    """The keep-grid second that starts a span on ``frame``: the last
    centisecond at or before its time, so a trim from there takes this frame
    first (schemas round kept spans to centiseconds)."""
    t = frame / float(fps)
    grid = math.floor(t * 100.0 + 1e-6) / 100.0
    if frame > 0 and grid <= (frame - 1) / float(fps) + 1e-6:
        # Over 100 fps no centisecond falls between two frames.
        grid = round(t, 2)
    return round(grid, 2)


def find_cuts(diffs, away):
    """Frame indices that start a new picture.

    ``diffs[i]``: frame i against i-1 (``diffs[0]`` unused). ``away[i]``:
    frame i+2 against i-1 where known, else ``diffs[i]``."""
    d = np.asarray(diffs, dtype=np.float32)
    a = np.asarray(away, dtype=np.float32)
    n = len(d)
    if n < 3:
        return []
    hits = []
    for i in np.nonzero(d >= CUT_FLOOR)[0].tolist():
        if i == 0:
            continue
        lo, hi = max(1, i - NEIGHBOURS), min(n, i + NEIGHBOURS + 1)
        around = np.concatenate((d[lo:i], d[i + 1:hi]))
        # Repeated frames (a variable-rate source on a fixed clock) are no
        # measure of how much this footage moves.
        moved = around[around >= STILL_FRAME]
        base = float(np.median(moved)) if moved.size * 3 >= around.size \
            and moved.size else 0.0
        floor = CUT_FLOOR_MOVING if base >= MOVING_BASE else CUT_FLOOR
        if d[i] < max(floor, CUT_OVER_NEIGHBOURS * base):
            continue
        if d[i] < d[i - 1] or (i + 1 < n and d[i] < d[i + 1]):
            continue                        # the climb of a baked whip
        if a[i] < CUT_STAYS * d[i]:
            continue                        # a flash, gone again
        hits.append(i)
    cuts = []
    for i in hits:
        if cuts and i - cuts[-1] <= CUT_MERGE_FRAMES:
            cuts[-1] = i                    # the last one is the clean start
        elif i >= MIN_SHOT_FRAMES and n - i >= MIN_SHOT_FRAMES:
            if cuts and i - cuts[-1] < MIN_SHOT_FRAMES:
                continue
            cuts.append(i)
    return cuts


def _bar_value(edge_a, edge_b):
    """The shared value of two opposite frame edges when both are one flat
    colour, else None: a photo's own sky is flat at one end, not at both."""
    va, vb = float(np.median(edge_a)), float(np.median(edge_b))
    if abs(va - vb) > BAR_TOLERANCE:
        return None
    for edge, v in ((edge_a, va), (edge_b, vb)):
        if np.mean(np.abs(edge.astype(np.int16) - v) <= BAR_TOLERANCE) \
                < 1.0 - BAR_CONTENT_SHARE:
            return None
    return (va + vb) / 2.0


def _margin(lo, hi, bar):
    """Rows of uniform bar at the top and at the bottom of (lo, hi)."""
    flat = (np.abs(hi.astype(np.int16) - bar) <= BAR_TOLERANCE) & \
        ((hi.astype(np.int16) - lo.astype(np.int16)) <= BAR_TOLERANCE)
    share = 1.0 - flat.mean(axis=1)
    content = np.nonzero(share >= BAR_CONTENT_SHARE)[0]
    if content.size == 0:
        return 0
    # Baked into a frame, bars are centred: the narrower margin is the bar,
    # and a dark photo edge beside the wider one is the photo's.
    return min(int(content[0]), int(share.size - 1 - content[-1]))


def find_rect(lo, hi):
    """The picture's rectangle in a shot's lowest and highest samples, as
    fractions [x0, y0, x1, y1]; a frame with no bars comes back whole."""
    H, W = hi.shape
    whole = [0.0, 0.0, 1.0, 1.0]
    rows = _bar_value(hi[0], hi[-1])
    cols = _bar_value(hi[:, 0], hi[:, -1])
    my = 0 if rows is None else _margin(lo, hi, rows)
    mx = 0 if cols is None else _margin(lo.T, hi.T, cols)
    if 2 * (mx / W) + 2 * (my / H) < BARS_MIN_SHARE \
            or 2 * mx >= W - 2 or 2 * my >= H - 2:
        return whole
    return [round(mx / W, 5), round(my / H, 5),
            round(1.0 - mx / W, 5), round(1.0 - my / H, 5)]


def _shot_row(first, last, fps, duration, lo, hi, peak, best):
    rect = find_rect(lo, hi) if lo is not None else [0.0, 0.0, 1.0, 1.0]
    return {"start": cut_grid(first, fps) if first else 0.0,
            "end": (cut_grid(last, fps) if last is not None
                    else round(float(duration), 2)),
            "rect": rect, "bars": rect != [0.0, 0.0, 1.0, 1.0],
            "still": bool(peak < STILL_DIFF),
            # the sampled moment with the most picture in it, and whether
            # there is any: a montage's shards of black between its stills
            # have none
            "best": round(best[1] / fps, 2), "flat": bool(best[0] < FLAT_STD)}


def measure(src, *, ffmpeg=None, info=None):
    """The map of ``src`` (a local path or a signed URL). Raises only for a
    source that cannot be read at all; the caller keeps the index's shots."""
    import media
    info = info or media.probe(src)
    fps = float(info.get("fps") or 30.0)
    W, H = int(info["width"]), int(info["height"])
    aw = min(ANALYSIS_W, W) // 2 * 2
    ah = max(2, int(round(H * aw / W / 2.0)) * 2)
    size = aw * ah
    cmd = [ffmpeg or "ffmpeg", "-nostdin", "-v", "error",
           "-threads", str(THREADS), "-i", src, "-map", "0:v:0", "-vf",
           f"fps={fps:.6f},scale={aw}:{ah}:flags=bilinear,format=gray",
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    try:
        diffs, away, tiny = [], [], []
        samples = []                    # (frame index, full analysis frame)
        i = 0
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(ah, aw)
            small = frame[::4, ::4].astype(np.int16)
            diffs.append(float(np.abs(small - tiny[-1]).mean())
                         if tiny else 0.0)
            if len(tiny) >= 3:
                # frame i-3 against this one: what diffs[i-2] looks like
                # two frames after the change
                away.append(float(np.abs(small - tiny[-3]).mean()))
            tiny = (tiny + [small])[-3:]
            if i % SAMPLE_EVERY == 0:
                samples.append((i, frame.copy()))
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()
    if i == 0:
        raise ValueError("no picture could be decoded")
    # away[k] was measured at frame k+3 for the change at frame k+1
    aligned = list(diffs)
    for k, v in enumerate(away):
        if k + 1 < len(aligned):
            aligned[k + 1] = v
    cuts = find_cuts(diffs, aligned)
    duration = float(info.get("duration") or i / fps)
    edges = [0] + cuts + [i]
    d = np.asarray(diffs, dtype=np.float32)
    shots = []
    pos = 0
    for a, b in zip(edges, edges[1:]):
        lo = hi = None
        best = (-1.0, (a + b) // 2)
        # Frames a sample of this shot can take: clear of both joins.
        wanted = [f for f in samples[pos:] if a + 2 <= f[0] < b - 1]
        for f, fr in wanted:
            lo = fr if lo is None else np.minimum(lo, fr)
            hi = fr if hi is None else np.maximum(hi, fr)
            spread = float(fr.std())
            if spread > best[0]:
                best = (spread, f)
        if lo is None:
            near = min(samples, key=lambda s: abs(s[0] - (a + b) // 2))
            lo = hi = near[1]
            best = (float(near[1].std()), near[0])
        while pos < len(samples) and samples[pos][0] < b:
            pos += 1
        inner = d[a + 1:b]
        peak = float(inner.max()) if inner.size else 0.0
        shots.append(_shot_row(a, b if b < i else None, fps, duration,
                               lo, hi, peak, best))
    return {"v": VERSION, "fps": round(fps, 3), "frames": i,
            "duration": round(duration, 3), "width": W, "height": H,
            "cuts": [cut_grid(c, fps) for c in cuts], "shots": shots}


def run_on(local, info=None):
    """``measure`` for the frames job: never raises, and leaves a very long
    source to the index (one decode must fit the job's own timeout)."""
    import media
    try:
        info = info or media.probe(local)
        if float(info.get("duration") or 0.0) > SOURCE_MAX_S:
            return None
        return measure(local, info=info)
    except Exception as ex:  # noqa: BLE001 — a measurement never fails a job
        print(f"[picture_map] not measured: {type(ex).__name__}: {ex}",
              flush=True)
        return None


def expose(keep, cuts, min_span=0.05, last_frame_end=None):
    """``keep`` with its spans split at ``cuts``: the same frames in the same
    order, with every change of picture addressable. Returns (keep, added).

    ``last_frame_end``: the keep-grid second the source's picture ends on. A
    span whose end rounded up past it would be given one frame the source
    does not have (10,431 from 10,430); it ends on the grid instead, and the
    frames kept are the same."""
    points = sorted({round(float(c), 2) for c in cuts or []})
    out, added = [], 0
    if last_frame_end is not None and keep:
        last = keep[-1]
        over = float(last[1]) - float(last_frame_end)
        if 0 < over < 0.02 and float(last[0]) < float(last_frame_end):
            keep = list(keep[:-1]) + [[last[0], float(last_frame_end)]]
    for pair in keep or []:
        try:
            start, end = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            out.append(pair)
            continue
        inside = [c for c in points
                  if c - start >= min_span and end - c >= min_span]
        marks = [start, *inside, end]
        out.extend([round(marks[i], 3), round(marks[i + 1], 3)]
                   for i in range(len(marks) - 1))
        added += len(inside)
    return out, added


def shot_at(shots, src_t):
    """The map's row holding source second ``src_t``, or None."""
    for row in shots or []:
        if float(row["start"]) - 1e-6 <= src_t < float(row["end"]) - 1e-6:
            return row
    return (shots[-1] if shots and src_t >= float(shots[-1]["start"])
            else None)
