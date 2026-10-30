"""A short silent clip gets a camera that finds its subject (QB, 30 Oct 2026).

The First cut kept a short silent clip whole and added a poster title: nothing
else. Both real clips of that kind scored 5.75 ("raw footage with a title"):
a 17 s tripod shot of a man walking toward the camera from a speck in the
middle of the frame, and a 15 s mirror clip of a physique in a wide bathroom.
Every judgement asked the same three things: crop in on the subject, give the
camera a move that follows the action so something changes every few seconds,
and end on a moving close frame rather than a wide one.

This is that camera, with the editor's own shipped tool (add_zoom_path, eased,
the travelling camera screen_follow uses). The trial model looks at six frames
across the clip and boxes the subject on each (one call, about $0.0006; a
frame difference found the swaying trees and the arm holding the phone
instead). Between those looks the box is interpolated; its height sets how
far the camera has pushed in (a speck gets 1.9x, a subject that fills the
frame gets none), the window follows its centre, and the last keyframe pushes
a little deeper so the clip ends live.

WHO GETS IT: the First cut's engine accounts (trial and new subscribers), for
a silent vertical clip of 3-20 s kept whole. No lane, no camera.
"""
import base64
import json
import re
import subprocess

Z_MAX = 1.9                 # the deepest push (a source rendered at 720 wide)
FILL = 0.5                  # the subject's height as a share of the window
MIN_H = 0.05                # a smaller subject is a speck: the deepest push
START_S = 0.9               # the camera starts easing in here (under the title)
STEP_S = 2.4                # one keyframe about this often
MAX_DZ = 0.45               # the push changes by at most this per keyframe
END_PUSH = 0.1              # the last keyframe pushes a little further: live
LOOKS = 6                   # frames the model boxes

SYSTEM = (
    "You direct the camera on a short silent clip. Each numbered image is one "
    "frame of it. For each frame give the box (pixel coordinates of THAT "
    "image: x0 y0 x1 y1) around the clip's main subject: the person, animal "
    "or object the shot is about, whole, with a little room. If the subject "
    "is small in the frame, the box is still tight on it. Answer with JSON "
    "only: {\"boxes\": [{\"n\": <frame number>, \"box\": [x0, y0, x1, y1]}]}.")


def parse_boxes(text, sizes):
    """{n: (x0, y0, x1, y1)} fractions of frame n from the model's pixel
    boxes; boxes that are not a box, outside the frame or the whole frame
    are dropped."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        pick = json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}
    out = {}
    for item in pick.get("boxes") or []:
        try:
            n = int(item.get("n"))
            x0, y0, x1, y1 = [float(v) for v in item.get("box")]
        except (TypeError, ValueError, AttributeError):
            continue
        if n not in sizes:
            continue
        w, h = sizes[n]
        if max(x0, y0, x1, y1) <= 1.5:
            # WAVE_A review (31 Oct): a model that answers in fractions
            # ([0.42, 0.3, 0.6, 0.9]) is not boxing a 1-pixel speck in the
            # corner (read as pixels it sent the camera 1.9x into the top left)
            box = [x0, y0, x1, y1]
        else:
            box = [x0 / w, y0 / h, x1 / w, y1 / h]
        if not (-0.02 <= box[0] < box[2] <= 1.02 and -0.02 <= box[1] < box[3] <= 1.02):
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.8:
            continue                      # "the whole frame" points at nothing
        if box[2] - box[0] < 0.02 or box[3] - box[1] < 0.02:
            continue                      # a degenerate box points at nothing either
        out[n] = tuple(min(max(v, 0.0), 1.0) for v in box)
    return out


def look(ctx, start, end, spend, n=LOOKS):
    """[(source second, box)] from one look of the trial model at ``n``
    frames across [start, end] and one at the very end (a subject walking out
    of the frame: without it the camera held the last box and sliced him at
    the edge, judge 2 of 2); [] without the lane or a usable answer."""
    import claude_lane
    import first_cut_visual as fcv
    import llm
    model = getattr(ctx, "first_cut_model", None)
    if not model or not claude_lane.available():
        return []
    try:
        path = ctx.proxy_path()
    except Exception:
        return []
    parts, sizes, times = [], {}, {}
    marks = [start + (end - start) * (k + 0.5) / n for k in range(n)]
    marks.append(max(start, end - 0.3))
    for k, t in enumerate(marks):
        try:
            jpg = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i",
                 path, "-frames:v", "1", "-vf", "scale=512:-2", "-f", "image2",
                 "-c:v", "mjpeg", "-q:v", "5", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            jpg = b""
        size = fcv._jpeg_size(jpg) if jpg else None
        if not size:
            continue
        sizes[k + 1], times[k + 1] = size, t
        parts.append({"type": "text", "text":
                      f"Frame {k + 1} ({size[0]}x{size[1]} px) at {t:.1f} s"})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if len(sizes) < 3:
        return []
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": parts}],
            max_tokens=2000, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("first_cut_subject_scout", {"model": model, "frames": len(sizes)},
                   {"content": text}, resp.usage)
        print(f"[first_cut] subject scout: {text.strip()[:240]}", flush=True)
    except Exception as exc:  # noqa: BLE001 - no camera is the fallback
        print(f"[first_cut] subject scout failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return []
    boxes = parse_boxes(text, sizes)
    return [(times[n], b) for n, b in sorted(boxes.items())]


TITLE_ABOVE_Y = 0.26        # clear of the brand mark's band (0.115-0.192)
TITLE_BELOW_Y = 0.80
TITLE_ROOM = 0.14           # the band a title needs clear of the subject


def title_y(samples, until=4.0):
    """Where the poster title sits so it never crosses the subject: in the
    band above it (the references' title sits above the head) or below it,
    whichever has the room, else None (the title's own default). The camera
    keeps the subject mid-frame, so the title stays toward an edge."""
    near = [b for t, b in samples if t <= until] or [b for _t, b in samples[:2]]
    if not near:
        return None
    top = min(b[1] for b in near)
    bottom = max(b[3] for b in near)
    above = top - 0.2
    below = 0.95 - bottom
    if above >= TITLE_ROOM and above >= below - 0.1:
        return TITLE_ABOVE_Y
    if below >= TITLE_ROOM:
        return TITLE_BELOW_Y
    return TITLE_ABOVE_Y if above >= TITLE_ROOM else None


def _at(samples, t):
    """The box at second ``t``, interpolated between the looks (held before
    the first and after the last)."""
    if t <= samples[0][0]:
        return samples[0][1]
    for (t0, b0), (t1, b1) in zip(samples, samples[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / max(1e-6, t1 - t0)
            return tuple(a + (b - a) * f for a, b in zip(b0, b1))
    return samples[-1][1]


def _window(cx, cy, z):
    half = 0.5 / z
    x0 = min(max(cx - half, 0.0), 1.0 - 2 * half)
    y0 = min(max(cy - half, 0.0), 1.0 - 2 * half)
    return [round(x0, 4), round(y0, 4), round(x0 + 2 * half, 4),
            round(y0 + 2 * half, 4)]


def keyframes(samples, prog, start_s=START_S):
    """add_zoom_path keyframes [{t, rect, strength}] from the looks
    [(second, box)], or [] when they hold no subject worth a camera: the
    untouched frame until the camera starts, then an eased push whose depth
    follows the subject's height and whose window follows its centre, a
    keyframe every STEP_S, the last one a little deeper (live to the end)."""
    if len(samples) < 3 or prog < 3.0:
        return []
    samples = sorted(samples)
    times = [start_s + STEP_S]
    while times[-1] + STEP_S < prog - 0.8:
        times.append(times[-1] + STEP_S)
    times.append(prog)
    pts, z_prev = [], 1.0
    for t in times:
        b = _at(samples, min(t, prog))
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        bh, bw = max(MIN_H, b[3] - b[1]), max(0.05, b[2] - b[0])
        z = max(1.0, min(Z_MAX, FILL / bh, 0.9 / bw))
        z = min(max(z, z_prev - MAX_DZ), z_prev + MAX_DZ)
        pts.append((t, cx, cy, z))
        z_prev = z
    _t1, cx1, cy1, z1 = pts[0]
    first = _window(cx1, cy1, max(z1, 1.15))
    keys = [{"t": 0.0, "rect": first, "strength": 0.0},
            {"t": round(start_s, 2), "rect": first, "strength": 0.0}]
    for t, cx, cy, z in pts:
        keys.append({"t": round(min(t, prog), 2), "rect": _window(cx, cy, z),
                     "strength": round(z - 1.0, 3)})
    if prog - keys[-1]["t"] > 0.2:
        keys.append(dict(keys[-1], t=round(prog, 2)))
    # the ending keeps moving: a little deeper than the last framing
    end = keys[-1]
    z_end = min(Z_MAX + 0.15, 1.0 + end["strength"] + END_PUSH)
    keys[-1] = dict(end, strength=round(z_end - 1.0, 3))
    return keys if any(k["strength"] > 0.12 for k in keys) else []


def whole_looks(ctx, spend):
    """The looks over the whole untouched clip, program time = source time
    (a short silent clip is kept whole); [] when the program was cut."""
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        prog = float(schemas.program_duration(edl))
    except Exception:
        return []
    keep = [list(map(float, k)) for k in edl.get("keep") or []]
    if len(keep) > 1:
        return []
    a = keep[0][0] if keep else 0.0
    b = keep[0][1] if keep else prog
    return [(t - a, box) for t, box in look(ctx, a, b, spend)]


def camera(ctx, steps, started, run, spend, samples=None):
    """Write the subject camera from the looks (taken here when not given).
    Returns the add_zoom_path result or None."""
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        prog = float(schemas.program_duration(edl))
    except Exception:
        return None
    if samples is None:
        samples = whole_looks(ctx, spend)
    keys = keyframes(samples, prog)
    if not keys:
        return None
    return run(ctx, steps, "add_zoom_path", {
        "keyframes": keys, "ease": "cubic_in_out",
        "purpose": "a camera that finds the subject of a short silent clip: "
                   "pushes in on it, follows it and keeps moving to the end"},
               started)
