"""Crops and picture cards that FOLLOW the speaker's face inside a shot.

WHY (owner, Oct 10 2026)

"The card doesn't adjust with the face — occasionally part of the face
moves out." The Jobs showcase is one continuous shot of a man at a lectern
who leans and steps; its card showed one fixed region of the source, so his
face drifted to the card's edge and past it. A 9:16 crop of 16:9 footage
had the same failure: auto_reframe and focus_track aim ONE point per shot,
and a speaker who moves inside the shot walks out of it. A human editor
keyframes the crop to keep the face in frame — and does it with restraint:
the frame holds still while the speaker sways, then glides (never whips)
to where they settled.

THE DATA (schemas.FollowSpan)

``{"t0", "t1", "k": [[t, x, y], ...]}`` — SOURCE seconds; (x, y) the centre of
the crop window (Frame.follow) or of the card's source rect
(PictureCard.follow), as fractions of the source frame. One span per shot:
a span never crosses a cut (each shot has its own track; a cut may jump).
Between keys the path is a monotone cubic (PCHIP, zero slope at both ends,
so it eases out of a hold and settles into the next one); before the first
key and after the last it holds. Keys are written ONCE, at write time, from
a measured face track — so every render lane draws the same path, a preview
matches its final, and the python mirrors (keep-out, captions, look_at) see
exactly where the face is.

THE PLAN (plan)

The face track (3-5 fps, cleaned of detector blips) becomes a desired
window per sample: the face centred across, nudged toward where the speaker
looks (lead room, from the profile detector), the crown HEADROOM_TARGET of
the window below its top edge, all clamped to the source. The camera HOLDS
while the desired window stays inside a dead zone around the held aim (and
the head stays inside the window); when it leaves, the camera MOVES to the
next hold on a monotone ease whose duration obeys a velocity and an
acceleration limit (no whip), centred on the moment the speaker crossed —
the offline plan sees the future, so a move starts before the head reaches
the edge. Holds too short for their moves become pass-through points: a
speaker who keeps walking gets a steady pan. A shot that needs only one
hold gets NO follow span (restraint is the default: a still frame).

What the window must hold is keep_box: the whole head when it fits with
room to spare, else the face, else its centre line — a close-up whose head
is wider than a 9:16 window would otherwise turn every detector wobble into
a re-aim. A still shot whose aim cuts the face is nudged (nudge) only as far
as it must. Each SHOT is tracked on its own (speaker_track), a GROUP shot
(is_group) is never followed — that would decide who to frame — and a span
covers its whole shot, so a later cut that keeps more of it finds the path
(timeline.revealed_follow slows a re-aim hidden on a jump cut that a later
edit keeps again to a glide).

THE RENDER (window_chain)

A follow block crops the union of every window of the block out of the
source ONCE (static), scales it once to the output scale, runs the block's
CFR tail, then crops the output window per frame at an even pixel and
shifts the sub-pixel remainder with `perspective` (1/256 px, the camera's
own resampler) — only on frames where the window moves; holds are snapped
to even pixels and pass through untouched. No shimmer, no stair-steps.
"""
import bisect
import math
import os
import subprocess
import threading
import time
from collections import OrderedDict

import camera


# ── measurement ──────────────────────────────────────────────────────────
#
# WHY IT IS BUILT THIS WAY (production, Oct 10 2026)
#
# The first release measured the face track inside the tool call, on the
# lane the call runs on, with a 60 s wall-clock net, and swallowed every
# failure. On the Jobs showcase it followed locally and never in
# production: the MCP and agent lanes are Cloudflare standard-1 containers
# (0.5 vCPU), and the frontal + profile + mirrored-profile Haar pass over
# 43 s of kept 480p footage is ~14 CPU-s on a fast laptop core — over 60 s
# of wall clock there. Job 62747 (set_picture_card) ran 81 s on 36.6 CPU-s
# and job 62748 (auto_reframe) 74 s on 35.0 CPU-s: both spent the whole
# budget measuring, threw the half-built track away and wrote a still card
# and crop without a word. So now:
#
# * the track is cheaper where CPU is scarce: every second frame is a FULL
#   detection (the coarse 2 fps track, faces of every size in the frame —
#   what group shots are judged on), the frames between search only around
#   their neighbours' faces at their size (fine, ~1/5 of a full detection;
#   half the CPU overall). Coarse first across ALL windows, so a budget
#   that runs out mid-fine still leaves a complete 2 fps track
#   (MIN_MEASURE_FPS) to plan from;
# * where CPU is not scarce it runs there: agent_tools races this same
#   function on the batch media lane (run_faces_job, 4 vCPU, at most two
#   batch shards, the proxy range-read rather than staged) against the
#   local pass and takes whichever completes;
# * nothing measured is thrown away (FACE_STORE: per proxy, per process),
#   so a second tool call over the same footage reads it back, and a call
#   that ran out of time resumes where the last one stopped (the very same
#   call, too: the replay guard lets it through and the failure is not
#   remembered past its own call) — waiting on the media-lane job it
#   started if that is still running;
# * every kept window gets a sample, however short (_sample_plan): a
#   fragment without one would leave the track incomplete forever;
# * every outcome is reported (measure's ``report``): the tool result says
#   when the follow could not be measured and why, and a metric counts it.
SAMPLE_FPS = 4.0
# Detection width: a talking head's face stays >= ~45 px here (a 1080p
# interview ~48, a 480p archival talk ~60); Haar is scale-invariant above
# its 24 px floor, and the cost is the area (~10 ms a frame on one core).
DETECT_WIDTH = 448
# A write never decodes more than this many frames for one tool call.
MAX_MEASURE_FRAMES = 720
# ...nor plans from fewer than this many samples a second (below it the
# blip filter and the smoothing have no neighbours to compare against), so
# a call measures at most MAX_MEASURE_S of kept footage. Longer edits keep
# one aim per shot, exactly as before follow existed: decoding a long-form
# proxy end to end is minutes of tool time in a production lane.
MIN_MEASURE_FPS = 2.0
MAX_MEASURE_S = MAX_MEASURE_FRAMES / MIN_MEASURE_FPS
# Wall-clock bound of one measurement (a stalled decode, a lane with little
# CPU): past it the measurement stops and keeps what it measured — the plan
# runs only on a complete >= MIN_MEASURE_FPS track. A tool call spends at
# most this long on the face track, its own pass and the wait for the media
# lane together: the MCP backend answers synchronously for 110 s
# (MCP_SYNC_WAIT_S), and the rest of the call needs some of that.
MEASURE_BUDGET_S = 75.0
# The index's spatial samples are dense enough to plan from at this step.
DENSE_STEP_S = 0.5
# Windows closer than this decode in one ffmpeg pass (one seek's preroll —
# the proxy's GOP is ~8 s — and one process instead of one per fragment).
SPAN_GAP_S = 4.0
# The size band a fine sample searches (x its neighbours' face height):
# speaker_track drops anything outside 0.5-2x anyway. Only the band and the
# region are narrowed, never the frame shrunk: Haar's box grows ~8% on a
# frame shrunk toward its 24 px floor (Jobs: median 0.245 -> 0.265 of the
# height), and the plan's head geometry (HAIR_ABOVE_FACE ...) is fitted to
# the boxes of the full-resolution search.
SIZE_BAND = (0.45, 2.2)
# A fine frame searches its neighbours' faces padded by this much of the
# face's width and height (a speaker moves a fraction of a face in 0.25 s).
ROI_PAD = (0.9, 0.6)
# The measurement's identity: the FACE_STORE key and the remote handshake
# (an executor running another version measures differently).
FACES_VERSION = 2

# ── the plan (fractions of the WINDOW, per axis) ─────────────────────────
DEAD_ZONE = (0.14, 0.10)     # drift the camera ignores around a held aim
SAFE_MARGIN = 0.04           # the head box stays this far inside the window
LEAD = 0.05                  # look-direction lead room (window widths)
HEADROOM_TARGET = 0.10       # crown below the window's top edge
VMAX = 0.5                   # comfortable move speed (windows / s)
VMAX_HARD = 1.4              # the fastest a move is pushed to keep the head in
AMAX = 1.2                   # windows / s^2 (smoothstep peak 6D/T^2)
MOVE_MIN_S = 0.8             # a deliberate glide, never a snap
REACT = 0.15                 # a move centres this share of itself after the crossing
HOLD_MIN_S = 0.6             # a shorter hold between moves the same way is passed through
MIN_STEP = 0.08              # re-aims smaller than this are not worth a move
MAX_KEYS = 120               # per span (schemas.FOLLOW_MAX_KEYS)
# The detector box runs brow to chin; the head the window must keep is the
# hair above it (more than picture_cards' 0.40: a big-haired speaker looking
# down showed his hair at a held card's top edge), the ears either side and
# the chin and collar below.
HAIR_ABOVE_FACE = 0.50
HEAD_SIDE = 0.15
HEAD_BELOW = 0.35

# st()/ld() slots inside a follow expression (its own filter instances).
_R_X, _R_T, _R_U = 0, 9, 7


# ═════════════════════════════════════════════════════════════════════════
# The path: PCHIP over keys, one copy of the arithmetic for python and ffmpeg
# ═════════════════════════════════════════════════════════════════════════

def slopes(ts, vs):
    """Fritsch-Carlson monotone slopes, zero at both ends (the path holds
    outside its keys, so it leaves the first and enters the last at rest)."""
    n = len(ts)
    m = [0.0] * n
    if n < 3:
        return m
    h = [ts[i + 1] - ts[i] for i in range(n - 1)]
    d = [(vs[i + 1] - vs[i]) / h[i] if h[i] > 0 else 0.0 for i in range(n - 1)]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            continue
        w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
        m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    return m


def path(ts, vs, T, m=None):
    """The path at time T: a float for a float T (the python mirror), an
    ffmpeg expression (camera.X) for an expression T. A running sum of one
    cubic Hermite term per moving segment; each term is exactly its
    segment's delta once T is past it (h01(1) = 1, h10(1) = h11(1) = 0 in
    floating point too), so a hold on whole pixels stays on whole pixels."""
    n = len(ts)
    if n == 0:
        return 0.0
    if n == 1:
        return vs[0]
    m = slopes(ts, vs) if m is None else m
    terms = []
    for i in range(n - 1):
        h = ts[i + 1] - ts[i]
        dv = vs[i + 1] - vs[i]
        a, b = h * m[i], h * m[i + 1]
        if h <= 0 or (dv == 0 and a == 0 and b == 0):
            continue
        u = camera.clip((T - ts[i]) / h, 0.0, 1.0)

        def body(u, dv=dv, a=a, b=b):
            return (dv * (u * u * (3.0 - 2.0 * u)) + a * (u * (1.0 - u) * (1.0 - u))
                    + b * (u * u * (u - 1.0)))
        terms.append(camera.let(_R_U, u, body))
    if not terms:
        return vs[0]
    if not any(isinstance(t, camera.X) for t in terms):
        return vs[0] + sum(terms)
    return camera.X(camera._balanced_sum([camera.lit(vs[0])]
                                         + [camera.lit(t) for t in terms]), 1)


def keys_of(span):
    """(ts, xs, ys) of a FollowSpan dict ([] when malformed)."""
    ts, xs, ys = [], [], []
    for key in (span or {}).get("k") or []:
        try:
            t, x, y = float(key[0]), float(key[1]), float(key[2])
        except (TypeError, ValueError, IndexError):
            continue
        if ts and t <= ts[-1]:
            continue
        ts.append(t)
        xs.append(x)
        ys.append(y)
    return ts, xs, ys


def centre_at(span, t):
    """(x, y) the span's path gives at SOURCE second t."""
    ts, xs, ys = keys_of(span)
    if not ts:
        return None
    return path(ts, xs, float(t)), path(ts, ys, float(t))


def span_at(spans, t, slack=1e-6):
    """The follow span holding SOURCE second t, or None."""
    for span in spans or []:
        try:
            if float(span["t0"]) - slack <= float(t) <= float(span["t1"]) + slack:
                return span
        except (KeyError, TypeError, ValueError):
            continue
    return None


def moves(span):
    """True when the span's path is not one held position."""
    _ts, xs, ys = keys_of(span)
    return len(set(xs)) > 1 or len(set(ys)) > 1


def moving_windows(span):
    """[(t0, t1)] SOURCE seconds where the span's window moves."""
    ts, xs, ys = keys_of(span)
    out = []
    for i in range(len(ts) - 1):
        if xs[i] != xs[i + 1] or ys[i] != ys[i + 1]:
            if out and out[-1][1] >= ts[i] - 1e-9:
                out[-1] = (out[-1][0], ts[i + 1])
            else:
                out.append((ts[i], ts[i + 1]))
    return out


def frame_spans(edl):
    frame = (edl or {}).get("frame")
    if not isinstance(frame, dict):
        return []
    if (frame.get("ratio") or "source") == "source":
        return []
    return [sp for sp in frame.get("follow") or [] if isinstance(sp, dict)]


def frame_focus_at(edl, src_t):
    """The crop centre the frame's follow gives at SOURCE second src_t, or
    None where no follow span holds it (the static aim applies)."""
    span = span_at(frame_spans(edl), src_t)
    return centre_at(span, src_t) if span else None


def edges(edl):
    """SOURCE seconds where a follow span starts or ends (frame and source
    cards): the renderer splits its blocks there, so every block follows
    at most one span."""
    out = set()
    spans = list(frame_spans(edl))
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        if isinstance(card, dict) and card.get("source") and not card.get("panels"):
            spans += [sp for sp in card.get("follow") or [] if isinstance(sp, dict)]
    for sp in spans:
        for key in ("t0", "t1"):
            try:
                out.add(float(sp[key]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def moves_during(edl, src_windows):
    """True when a follow path (frame or source card) MOVES inside any of
    the SOURCE windows — a subject mask measured on one framing would drift
    off the picture there."""
    spans = list(frame_spans(edl))
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        if isinstance(card, dict):
            spans += [sp for sp in card.get("follow") or [] if isinstance(sp, dict)]
    for sp in spans:
        for a, b in moving_windows(sp):
            if any(min(b, float(e)) - max(a, float(s)) > 1e-3 for s, e in src_windows):
                return True
    return False


# ═════════════════════════════════════════════════════════════════════════
# Measurement: a cheap write-time face track
# ═════════════════════════════════════════════════════════════════════════

def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
             - inter)
    return inter / union if union > 0 else 0.0


def _hits(cv2, cascade, img, lo, hi):
    """[(x, y, w, h)] one cascade finds in ``img`` between lo and hi px tall
    (subject._faces_in's detector settings), biggest first."""
    try:
        found = cascade.detectMultiScale(img, scaleFactor=1.12, minNeighbors=6,
                                         minSize=(lo, lo), maxSize=(hi, hi))
    except Exception:
        return []
    out = [(int(x), int(y), int(w), int(h)) for x, y, w, h in found]
    out.sort(key=lambda b: -(b[2] * b[3]))
    return out


def detect(gray, cv2=None, cascades=None, face_px=None, roi=None):
    """[(box, look)] faces in one grayscale frame: box in fractions, look
    -1 (the face turns toward screen-left), +1 (screen-right) or 0 (frontal
    or unknown). Frontal Haar plus the profile cascade on the frame and on
    its mirror (the cascade finds faces turned screen-left; mirrored, it
    finds the ones turned right) — a three-quarter speaker the frontal
    cascade misses is still found, and the side he looks to is known.

    ``face_px`` (the height, in ``gray``'s pixels, of the face this shot
    shows): search only SIZE_BAND around it (the same boxes, fewer scales).
    ``roi`` ([x0, y0, x1, y1] fractions): search only there. Boxes are
    fractions of the WHOLE frame either way."""
    import subject
    cv2 = cv2 or subject._cv2()
    if cv2 is None:
        return []
    cascades = cascades if cascades is not None else subject._cascades(cv2)
    if not cascades:
        return []
    h, w = gray.shape[:2]
    x0 = y0 = 0
    img = gray
    if roi is not None:
        x0, y0 = max(0, int(math.floor(roi[0] * w))), max(0, int(math.floor(roi[1] * h)))
        x1, y1 = min(w, int(math.ceil(roi[2] * w))), min(h, int(math.ceil(roi[3] * h)))
        if x1 - x0 < 24 or y1 - y0 < 24:
            return []
        img = gray[y0:y1, x0:x1]
    if face_px:
        lo = max(24, int(SIZE_BAND[0] * face_px))
        hi = max(lo + 2, int(math.ceil(SIZE_BAND[1] * face_px)))
        if min(img.shape[:2]) < lo:
            return []

        def find(c, im):
            return _hits(cv2, c, im, lo, hi)
    else:
        def find(c, im):
            return subject._faces_in(cv2, [c], im)
    eq = cv2.equalizeHist(img)
    iw = eq.shape[1]

    def frac(x, y, bw, bh):
        return [(x + x0) / w, (y + y0) / h, (x + bw + x0) / w, (y + bh + y0) / h]
    found = []
    for x, y, bw, bh in find(cascades[0], eq):
        found.append((frac(x, y, bw, bh), 0, True))
    if len(cascades) > 1:
        left = find(cascades[1], eq)
        for x, y, bw, bh in left:
            found.append((frac(x, y, bw, bh), -1, False))
        if not left:                    # turned the other way, or frontal
            flip = cv2.flip(eq, 1)
            for x, y, bw, bh in find(cascades[1], flip):
                found.append((frac(iw - x - bw, y, bw, bh), 1, False))
    # One detection per face: overlapping hits merge (the frontal box when
    # there is one, the profile's look).
    groups = []
    for box, look, frontal in sorted(found, key=lambda f: (not f[2], -(f[0][2] - f[0][0]))):
        for g in groups:
            if _iou(g[0], box) > .25:
                g[1].add(look)
                break
        else:
            groups.append([box, {look}])
    out = []
    for box, looks in groups:
        looks.discard(0)
        look = looks.pop() if len(looks) == 1 else 0
        out.append(([round(v, 4) for v in box], look))
    return out


def too_long(windows):
    """True when ``windows`` hold more footage than one call measures."""
    return sum(max(0.0, float(b) - float(a)) for a, b in windows or []) \
        > MAX_MEASURE_S + 1e-6


def cpu_count():
    """The vCPUs this process may use: the Cloudflare lane's profile (the
    VM can show more cores than its share), else the cgroup quota, else the
    affinity mask. Never below 0.25."""
    if os.getenv("EXECUTOR_PROVIDER") == "cloudflare":
        try:
            import compute_cost
            cores = compute_cost.CLOUDFLARE_PROFILES.get(
                os.getenv("CLOUDFLARE_CONTAINER_PROFILE", ""))
            if cores:
                return max(.25, float(cores[0]))
        except Exception:
            pass
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max" and float(period) > 0:
            return max(.25, float(quota) / float(period))
    except (OSError, ValueError):
        pass
    try:
        return float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return float(os.cpu_count() or 1)


def decode_spans(windows, gap=SPAN_GAP_S, pad=0.0):
    """[(A, B, [windows])]: ``windows`` grouped into the stretches one
    ffmpeg pass decodes; each stretch reaches ``pad`` seconds past its
    windows' ends (never before 0), so a fragment shorter than a sample step
    still has a decoded frame beside it (_sample_plan)."""
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in windows):
        lo, hi = max(0.0, a - pad), b + pad
        if out and lo - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], hi)
            out[-1][2].append((a, b))
        else:
            out.append([lo, hi, [(a, b)]])
    return [(a, b, ws) for a, b, ws in out]


def _sample_plan(A, B, fps, k, windows, cuts):
    """What one decode span [A, B] (grid frame i at A + i/fps) samples for
    ``windows`` (inside it): (full, fine, empty).

    full {i: [t, ...]}: frame i gets a FULL detection, recorded at each t —
    every k-th grid frame inside a window (the coarse track); a fine frame
    with no full neighbour in its own shot within k frames (an isolated
    sample: no neighbour to search around, so a region search would miss
    it); and, for a window SHORTER than a sample step that no grid frame
    falls inside (a sliver beside a cut, a word kept between two removed
    ones), the nearest grid frame of its shot within one step, recorded at
    the window's edge — the old per-window decode sampled such a fragment
    at its start, and a fragment without a sample would leave the track
    incomplete forever. fine {i: t}: the frames between, searched around
    their full neighbours. empty [t]: windows no frame of their own shot
    reaches (a shot shorter than a step) — recorded as measured with no
    face, so they are covered without inventing a position."""
    n = int(math.floor((B - A) * fps + 1e-6)) + 1

    def at(i):
        return A + i / fps

    def shot(t):
        return bisect.bisect_right(cuts, t)

    inside = {}
    for a, b in windows:
        lo = max(0, int(math.ceil((a - A) * fps - 1e-6)))
        hi = min(n - 1, int(math.floor((b - A) * fps + 1e-6)))
        for i in range(lo, hi + 1):
            inside[i] = round(at(i), 3)
    full = {i: [t] for i, t in inside.items() if i % k == 0}
    fine = {}
    for i in sorted(inside):
        if i % k == 0:
            continue
        s = shot(at(i))
        if any(j in full and shot(at(j)) == s
               for j in range(i - k, i + k + 1) if j != i):
            fine[i] = inside[i]
        else:
            full[i] = [inside[i]]
    empty = []
    step = 1.0 / fps
    for a, b in windows:
        lo = max(0, int(math.ceil((a - A) * fps - 1e-6)))
        hi = min(n - 1, int(math.floor((b - A) * fps + 1e-6)))
        if lo <= hi:
            continue                         # it has grid frames of its own
        s = shot((a + b) / 2.0)
        best = None
        for i in range(max(0, int(math.floor((a - step - A) * fps)) - 1),
                       min(n - 1, int(math.ceil((b + step - A) * fps)) + 1) + 1):
            t = at(i)
            d = max(a - t, t - b, 0.0)
            if d <= step + 1e-6 and shot(t) == s and (best is None or d < best[0]):
                best = (d, i)
        if best is None:
            empty.append(round((a + b) / 2.0, 3))
            continue
        i = best[1]
        full.setdefault(i, []).append(round(min(max(at(i), a), b), 3))
        if i in fine:                        # one full detection serves both
            full[i].append(fine.pop(i))
    return full, fine, empty


def _stream_gray(path, a, b, fps, width, height, deadline, cancel=None,
                 threads=2, state=None):
    """Yield the grayscale frames of SOURCE [a, b] at ``fps`` as they decode;
    stops (and kills ffmpeg) at ``deadline`` (time.monotonic) or when
    ``cancel`` is set. ``state`` receives the frame count, the exit code and
    the tail of ffmpeg's stderr."""
    import tempfile
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", str(max(1, int(threads))),
           "-ss", f"{max(0.0, a):.3f}", "-i", path, "-t", f"{max(0.05, b - a):.3f}",
           "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"fps={fps:.4f},scale={width}:{height},format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    state = state if state is not None else {}
    state.update(frames=0, rc=None, stderr="", killed=False)
    err = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err,
                                stdin=subprocess.DEVNULL)
    except Exception:
        err.close()
        raise

    def stop():
        state["killed"] = True
        try:
            proc.kill()
        except Exception:
            pass
    # A decode that stalls (no output at all) must not outlive the budget:
    # the read below would block on it.
    timer = threading.Timer(max(0.0, deadline - time.monotonic()), stop)
    timer.daemon = True
    timer.start()
    size = width * height
    try:
        while True:
            if (cancel is not None and cancel.is_set()) or time.monotonic() > deadline:
                break
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            state["frames"] += 1
            yield np.frombuffer(buf, np.uint8).reshape(height, width)
    finally:
        timer.cancel()
        if proc.poll() is None:
            stop()
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        state["rc"] = proc.returncode
        try:
            err.seek(0)
            state["stderr"] = err.read()[-400:].decode("utf-8", "replace").strip()
        except Exception:
            pass
        err.close()


def _is_url(path):
    """A source ffmpeg reads over a protocol (a presigned object URL that it
    range-reads), not a local file."""
    return isinstance(path, str) and "://" in path[:16]


def _usage():
    try:
        import resource
        s = resource.getrusage(resource.RUSAGE_SELF)
        c = resource.getrusage(resource.RUSAGE_CHILDREN)
        return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime
    except Exception:
        return time.process_time()


def measure(path, windows, aspect, fps=SAMPLE_FPS, width=DETECT_WIDTH,
            timeout=90, budget_s=MEASURE_BUDGET_S, *, cuts=(), report=None,
            cancel=None, threads=None):
    """[(t, [(box, look), ...])] the faces found at ``fps`` over SOURCE
    ``windows`` [(a, b)] of the video at ``path`` (the proxy), boxes in
    fractions, sorted by t. ``aspect`` = height / width of the picture;
    ``cuts`` the shot boundaries (a fine sample looks only at neighbours in
    its own shot). ``threads``: the vCPUs to use (default cpu_count());
    ``timeout`` is unused (``budget_s`` bounds the whole measurement).

    Two passes, so the work a budget cuts short is the least useful: COARSE
    (every second sample, MIN_MEASURE_FPS) runs while the windows decode —
    a full detection, exactly what one sample always got (every face of
    every size: group shots are judged on these); FINE (the samples between)
    searches only around its coarse neighbours' faces, at their size (~1/5
    of the cost), and records a sample with no face either side as nobody
    there. Every window gets a sample, however short (_sample_plan). Bounded: past
    MAX_MEASURE_FRAMES the rate drops (never below MIN_MEASURE_FPS), more
    than MAX_MEASURE_S of footage is not measured, and past ``budget_s`` of
    wall clock (or once ``cancel`` is set) it stops and returns what it has.

    ``report`` (a dict) receives the outcome: status 'complete' (both
    passes), 'coarse' (a whole coarse track, fine cut short), 'partial' (the
    coarse track did not cover every window — never plan from it) or
    'failed'; why (None, 'budget', 'cancelled', 'empty', 'too_long',
    'no_proxy', 'no_opencv', 'no_cascades', 'decode'), detail, roi_times
    (the fine samples — not full detections, so group shots are judged
    without them), and timing/CPU figures."""
    started, cpu0 = time.monotonic(), _usage()
    rep = report if report is not None else {}
    rep.update(status="failed", why=None, detail="", frames=0, coarse=0,
               fine=0, fine_total=0, roi_times=[], footage_s=0.0,
               elapsed_s=0.0, cpu_s=0.0, fps=0.0, threads=0, decode_s=0.0)

    def done(status, why=None, detail="", frames=()):
        rep.update(status=status, why=why, detail=str(detail)[:300],
                   frames=len(frames), elapsed_s=round(time.monotonic() - started, 2),
                   cpu_s=round(_usage() - cpu0, 2))
        return sorted(frames, key=lambda fr: fr[0])

    windows = [(float(a), float(b)) for a, b in windows if float(b) - float(a) > .05]
    if not windows:
        return done("failed", "empty")
    total = sum(b - a for a, b in windows)
    rep["footage_s"] = round(total, 2)
    if too_long(windows):
        return done("failed", "too_long")
    if not path or not (_is_url(path) or os.path.exists(path)):
        return done("failed", "no_proxy", path or "no path")
    import subject
    cv2 = subject._cv2()
    if cv2 is None:
        return done("failed", "no_opencv")
    cascades = subject._cascades(cv2)
    if not cascades:
        return done("failed", "no_cascades")
    fps = min(float(fps), MAX_MEASURE_FRAMES / max(total, 1e-6))
    # coarse = every k-th sample: MIN_MEASURE_FPS when the rate allows two
    # passes, else every sample (a long measurement has no fine pass)
    k = max(1, int(math.floor(fps / MIN_MEASURE_FPS + 1e-6)))
    w = int(width) // 2 * 2
    h = max(2, int(round(w * float(aspect) / 2.0)) * 2)
    cpus = cpu_count() if threads is None else float(threads)
    n_threads = max(1, int(cpus))
    rep.update(fps=round(fps, 3), threads=n_threads)
    cuts = sorted(float(c) for c in cuts or ())
    deadline = started + float(budget_s)
    coarse, fine_todo, empty = {}, [], []
    decode_s, decode_err = 0.0, None
    try:
        prev_threads = cv2.getNumThreads()
        cv2.setNumThreads(n_threads)
    except Exception:
        prev_threads = None
    try:
        # Each stretch starts k samples before its first window, so that
        # window's first frame is a full detection, as it always was.
        for A, B, inside in decode_spans(windows, pad=k / fps):
            full, fine_at, nobody = _sample_plan(A, B, fps, k, inside, cuts)
            empty += nobody
            last = max(list(full) + list(fine_at), default=-1)
            st = {}
            t_dec = time.monotonic()
            gen = _stream_gray(path, A, B, fps, w, h, deadline, cancel,
                               threads=min(2, n_threads + 1), state=st)
            try:
                for i, gray in enumerate(gen):
                    if i > last:
                        break
                    if i in fine_at:
                        fine_todo.append((fine_at[i], gray))
                    elif i in full:
                        dets = detect(gray, cv2, cascades)
                        for t in full[i]:
                            coarse[t] = dets
            finally:
                gen.close()
            decode_s += time.monotonic() - t_dec
            if st.get("frames", 0) == 0 and not st.get("killed") \
                    and not (cancel is not None and cancel.is_set()):
                decode_err = (st.get("stderr") or
                              f"ffmpeg decoded no frame of {A:.2f}-{B:.2f}s "
                              f"(exit {st.get('rc')})")
            rep["coarse"] = len(coarse)
            if cancel is not None and cancel.is_set():
                return done("partial", "cancelled", frames=list(coarse.items()))
            if time.monotonic() > deadline:
                return done("partial", "budget",
                            f"the coarse track covered {len(coarse)} samples "
                            f"before {budget_s:g}s ran out",
                            frames=list(coarse.items()))
        if decode_err and not coarse:
            return done("failed", "decode", decode_err)
        rep.update(coarse=len(coarse), fine_total=len(fine_todo),
                   decode_s=round(decode_s, 2), decode_error=decode_err)
        # FINE: around the coarse neighbours' faces only
        times = sorted(coarse)
        fine, roi, cut_short = {}, [], None
        reach = k / fps + 1e-3

        def neighbours(t):
            shot = bisect.bisect_right(cuts, t)
            j = bisect.bisect_left(times, t)
            return [u for u in times[max(0, j - 1):j + 1]
                    if abs(u - t) <= reach and bisect.bisect_right(cuts, u) == shot]
        # A sample between two full detections that found nobody is measured
        # as nobody (no search needed) — recorded first, so a budget that
        # cuts the searches short still leaves every window covered. The
        # windows no frame of their own shot reaches are the same: nobody.
        searches = []
        for t, gray in fine_todo:
            near = neighbours(t)
            if near and not any(coarse[u] for u in near):
                fine[t] = []
                roi.append(t)
            else:
                searches.append((t, gray, near))
        for t in empty:
            if t not in coarse and t not in fine:
                fine[t] = []
                roi.append(t)
        fine_todo = None
        for t, gray, near in searches:
            if cancel is not None and cancel.is_set():
                cut_short = "cancelled"
                break
            if time.monotonic() > deadline:
                cut_short = "budget"
                break
            boxes = [d[0] for u in near for d in coarse[u]]
            if not boxes:
                # no full neighbour in its shot (_sample_plan makes such a
                # sample a full detection; kept as a guard): search it whole
                fine[t] = detect(gray, cv2, cascades)
                continue
            fw = max(b[2] - b[0] for b in boxes)
            fh = max(b[3] - b[1] for b in boxes)
            area = [min(b[0] for b in boxes) - ROI_PAD[0] * fw,
                    min(b[1] for b in boxes) - ROI_PAD[1] * fh,
                    max(b[2] for b in boxes) + ROI_PAD[0] * fw,
                    max(b[3] for b in boxes) + ROI_PAD[1] * fh]
            heights = sorted(b[3] - b[1] for b in boxes)
            fine[t] = detect(gray, cv2, cascades,
                             face_px=heights[len(heights) // 2] * h, roi=area)
            roi.append(t)
        searches = None
        frames = list(coarse.items()) + list(fine.items())
        rep.update(fine=len(fine), roi_times=roi)
        if cut_short:
            return done("coarse", cut_short, "the fine pass was cut short", frames)
        return done("complete", frames=frames)
    finally:
        if prev_threads is not None:
            try:
                cv2.setNumThreads(prev_threads)
            except Exception:
                pass


def measured_s(frames, windows, step=1.0 / MIN_MEASURE_FPS):
    """Seconds of ``windows`` the samples ``frames`` cover at >= the plan's
    minimum rate (what a partial measurement achieved)."""
    ts = sorted(float(t) for t, _d in frames or [])
    got = 0.0
    for a, b in windows:
        inside = [t for t in ts if a - step <= t <= b + step]
        for u, v in zip(inside, inside[1:]):
            if v - u <= 1.5 * step + 1e-6:
                got += max(0.0, min(v, b) - max(u, a))
    return got


# ═════════════════════════════════════════════════════════════════════════
# The store: what was measured, kept per proxy for the life of the process
# ═════════════════════════════════════════════════════════════════════════

class FaceStore:
    """Measured face samples per proxy ({t: (dets, full)}; full = a full
    detection, not a fine-pass region search), LRU over proxies. A sample is
    kept even when it holds no face — "measured: nobody there" is not
    "not measured". Tool contexts come and go between MCP calls; this does
    not, so a second call over the same footage (the card, then the crop)
    reads the track back, and a call that ran out of time resumes."""

    def __init__(self, max_videos=12):
        self._lock = threading.Lock()
        self._videos = OrderedDict()
        self.max_videos = int(max_videos)

    @staticmethod
    def key(identity, fps=SAMPLE_FPS, width=DETECT_WIDTH):
        return (str(identity), round(float(fps), 3), int(width), FACES_VERSION)

    def clear(self):
        with self._lock:
            self._videos.clear()

    def add(self, key, frames, roi_times=()):
        roi = {round(float(t), 3) for t in roi_times or ()}
        with self._lock:
            store = self._videos.setdefault(key, {})
            self._videos.move_to_end(key)
            for t, dets in frames or []:
                t = round(float(t), 3)
                full = t not in roi
                old = store.get(t)
                if old is None or full or not old[1]:
                    store[t] = (list(dets or []), full)
            while len(self._videos) > self.max_videos:
                self._videos.popitem(last=False)

    def samples(self, key, windows, slack=.05):
        """([(t, dets)], roi times) inside ``windows``."""
        with self._lock:
            store = dict(self._videos.get(key) or {})
        out, roi = [], set()
        for t in sorted(store):
            if any(float(a) - slack <= t <= float(b) + slack for a, b in windows):
                dets, full = store[t]
                out.append((t, list(dets)))
                if not full:
                    roi.add(t)
        return out, roi

    def uncovered(self, key, windows, step=1.0 / MIN_MEASURE_FPS):
        """The parts of ``windows`` without samples at >= MIN_MEASURE_FPS
        (a gap over 1.5 steps, or an edge more than a step from its first
        or last sample) — what a measurement still has to decode."""
        with self._lock:
            ts = sorted((self._videos.get(key) or {}).keys())
        out = []
        edge, gap = step + .1, 1.5 * step + 1e-6
        for a, b in windows:
            a, b = float(a), float(b)
            inside = [t for t in ts[bisect.bisect_left(ts, a - .05):
                                    bisect.bisect_right(ts, b + .05)]]
            if not inside:
                # a fragment shorter than a step is covered by a sample
                # within a step of it, as the plan's 2 fps would be
                if not (b - a <= step and ts[bisect.bisect_left(ts, a - step):
                                            bisect.bisect_right(ts, b + step)]):
                    out.append((a, b))
                continue
            if inside[0] - a > edge:
                out.append((a, inside[0] - .05))
            for u, v in zip(inside, inside[1:]):
                if v - u > gap:
                    out.append((u + .05, v - .05))
            if b - inside[-1] > edge:
                out.append((inside[-1] + .05, b))
        return [(round(a, 3), round(b, 3)) for a, b in out if b - a > .05]


FACE_STORE = FaceStore()


def pack_frames(frames):
    """measure's frames as compact JSON rows [t, [[x0, y0, x1, y1, look]]]."""
    return [[round(float(t), 3), [[*[round(float(v), 4) for v in box], int(look)]
                                  for box, look in dets or []]]
            for t, dets in frames or []]


def unpack_frames(rows):
    out = []
    for row in rows or []:
        try:
            t = float(row[0])
            dets = [([float(v) for v in d[:4]], int(d[4])) for d in row[1] or []
                    if len(d) >= 5]
        except (TypeError, ValueError, IndexError):
            continue
        out.append((t, dets))
    return out


def run_faces_job(worker_db, job):
    """Executor-side runner (matte-shaped: synchronous, no row): measure
    the face track over ``windows`` of the proxy at ``storage_key`` with
    this lane's CPU. ``worker_db`` is unused.

    payload: {storage_key, windows, aspect, cuts, fps, width, budget_s,
    faces_version} -> {ok, faces_version, frames (pack_frames), roi_times,
    report}. Repeats on this container read FACE_STORE."""
    import config
    import media_cache
    import shutil
    import storage
    import uuid
    payload = job.get("payload") or {}
    key = payload.get("storage_key")
    windows = [(float(a), float(b)) for a, b in payload.get("windows") or []]
    if not key or not windows:
        raise ValueError("faces job needs storage_key and windows")
    want = payload.get("faces_version")
    if want is not None and int(want) != FACES_VERSION:
        raise ValueError(f"executor measures faces v{FACES_VERSION} but the "
                         f"caller expects v{int(want)} — the lanes are mid-deploy")
    fps = float(payload.get("fps") or SAMPLE_FPS)
    width = int(payload.get("width") or DETECT_WIDTH)
    skey = FACE_STORE.key(key, fps, width)
    todo = FACE_STORE.uncovered(skey, windows)
    report = {"status": "complete", "why": None, "cached": not todo}
    if todo:
        workdir = os.path.join(config.TMP_DIR, f"faces_{uuid.uuid4().hex[:8]}")
        os.makedirs(workdir, exist_ok=True)
        try:
            # A container that holds the proxy reads it; any other range-
            # reads only the windows' bytes (the proxy is +faststart) instead
            # of staging all of it: production proxies run to ~400 MB, 25-60
            # s of download on a cold shard — most of the window this job
            # has to beat the caller's own pass.
            deadline = time.monotonic() + float(payload.get("budget_s") or 150.0)

            def staged():
                got = media_cache.lease(key, workdir, "proxy.mp4")
                if not got:
                    got = os.path.join(workdir, "proxy.mp4")
                    storage.download_to(key, got)
                return got

            def run(source):
                return measure(source, todo, float(payload.get("aspect") or .5625),
                               fps=fps, width=width,
                               budget_s=max(1.0, deadline - time.monotonic()),
                               cuts=payload.get("cuts") or (), report=report)
            local = media_cache.resident(key, workdir, "proxy.mp4")
            url = None
            if not local:
                try:
                    url = storage.presign_get(key, expires=3600)
                except Exception:
                    url = None
            frames = run(local or url) if (local or url) else []
            report["source"] = "cached" if local else "stream" if url else None
            if not local and (not url or report.get("status") == "failed"):
                # no URL, or a stream that read nothing: stage the proxy
                report["stream_error"] = report.get("detail") if url else None
                frames = run(staged())
                report["source"] = "staged"
            FACE_STORE.add(skey, frames, report.get("roi_times"))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    frames, roi = FACE_STORE.samples(skey, windows)
    left = FACE_STORE.uncovered(skey, windows)
    report = {k: v for k, v in report.items() if k != "roi_times"}
    return {"ok": not left, "faces_version": FACES_VERSION,
            "frames": pack_frames(frames), "roi_times": sorted(roi),
            "uncovered": left, "report": report}


def speaker_track(frames, cuts=()):
    """[(t, box, look)] of ONE person through ``frames`` (measure's
    output). Each SHOT (between ``cuts``, source seconds) is tracked on its
    own — a camera change never carries the last shot's position into the
    next, where it could pick a bystander standing where the previous
    speaker sat: detections far off the shot's typical face size are
    dropped, the track keeps the face nearest the running position (seeded
    at the median of the shot's largest faces, so one false positive cannot
    capture it), and isolated blips — a sample far from both its
    neighbours' positions in the same shot — are removed."""
    import bisect
    cuts = sorted(float(c) for c in cuts or ())
    shots = {}
    for t, dets in frames or []:
        shots.setdefault(bisect.bisect_right(cuts, float(t)), []).append((t, dets))
    out = []
    for k in sorted(shots):
        out += drop_blips(_track_shot(shots[k]))
    return out


def _track_shot(frames):
    biggest = []
    for _t, dets in frames:
        if dets:
            biggest.append(max(dets, key=lambda d: d[0][3] - d[0][1])[0])
    if not biggest:
        return []
    hs = sorted(b[3] - b[1] for b in biggest)
    S = hs[len(hs) // 2]
    cxs = sorted((b[0] + b[2]) / 2 for b in biggest)
    cys = sorted((b[1] + b[3]) / 2 for b in biggest)
    prev = (cxs[len(cxs) // 2], cys[len(cys) // 2])
    out = []
    for t, dets in frames:
        dets = [d for d in dets if .5 * S <= d[0][3] - d[0][1] <= 2.0 * S]
        if not dets:
            continue
        box, look = min(dets, key=lambda d: math.hypot(
            (d[0][0] + d[0][2]) / 2 - prev[0], (d[0][1] + d[0][3]) / 2 - prev[1]))
        out.append((float(t), list(box), int(look)))
        prev = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return out


GROUP_FACE_RATIO = .6     # a second face at least this tall as the first
GROUP_SHARE = .35         # of a shot's frames with a face: a group shot


def face_counts(frames):
    """[(t, n)] for every frame of ``frames`` (measure's output) with a face:
    n = faces at least GROUP_FACE_RATIO the height of its largest — two
    people in the shot, not a poster or a face in the crowd."""
    out = []
    for t, dets in frames or []:
        hs = [d[0][3] - d[0][1] for d in dets or []]
        if hs:
            top = max(hs)
            out.append((float(t), sum(1 for h in hs if h >= GROUP_FACE_RATIO * top)))
    return out


def is_group(counts, t0, t1):
    """True when [t0, t1] is a GROUP shot: two or more comparable faces in
    at least GROUP_SHARE of its frames with a face (and in 3 or more). The
    follow tracks ONE face; in a group shot that would decide who to frame
    — the static aim keeps that decision where it was made."""
    inside = [n for t, n in counts or [] if t0 - .05 <= t <= t1 + .05]
    many = sum(1 for n in inside if n >= 2)
    return bool(inside) and many >= 3 and many >= GROUP_SHARE * len(inside)


def drop_blips(samples, reach_s=.8):
    """Samples whose centre sits more than ~a face width from the median of
    their neighbours (within ``reach_s``) on either side are detector
    blips, not the speaker moving."""
    if len(samples) < 3:
        return list(samples)
    keep = []
    for i, (t, box, look) in enumerate(samples):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        fw = box[2] - box[0]
        near = [s for j, s in enumerate(samples)
                if j != i and abs(s[0] - t) <= reach_s]
        if len(near) < 2:
            keep.append((t, box, look))
            continue
        nx = sorted((s[1][0] + s[1][2]) / 2 for s in near)
        ny = sorted((s[1][1] + s[1][3]) / 2 for s in near)
        mx, my = nx[len(nx) // 2], ny[len(ny) // 2]
        if abs(cx - mx) > .8 * fw or abs(cy - my) > .8 * (box[3] - box[1]):
            continue
        keep.append((t, box, look))
    return keep


def index_samples(index, windows, frames_out=None):
    """[(t, box, 0)] from the index's spatial samples inside ``windows``
    (the largest face of each), and whether they are dense enough to plan
    a follow from (median step <= DENSE_STEP_S). ``frames_out`` (a list)
    receives every sample's faces in measure's shape (for face_counts)."""
    samples = (((index or {}).get("spatial") or {}).get("samples")) or []
    out = []
    for s in samples:
        try:
            t = float(s.get("t"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not any(a - .05 <= t <= b + .05 for a, b in windows):
            continue
        boxes = [f for f in s.get("faces") or [] if f and len(f) == 4
                 and f[2] > f[0] and f[3] > f[1]]
        if boxes:
            out.append((t, list(max(boxes, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))), 0))
            if frames_out is not None:
                frames_out.append((t, [(list(f), 0) for f in boxes]))
    out.sort(key=lambda s: s[0])
    steps = sorted(b[0] - a[0] for a, b in zip(out, out[1:]))
    dense = bool(steps) and steps[len(steps) // 2] <= DENSE_STEP_S + 1e-6
    return out, dense


# ═════════════════════════════════════════════════════════════════════════
# The plan: hold, then glide
# ═════════════════════════════════════════════════════════════════════════

def head_box(face):
    w, h = face[2] - face[0], face[3] - face[1]
    return [face[0] - HEAD_SIDE * w, face[1] - HAIR_ABOVE_FACE * h,
            face[2] + HEAD_SIDE * w, face[3] + HEAD_BELOW * h]


def keep_box(face, ww, wh):
    """What the window must hold of the speaker, per axis: the whole head
    (head_box) when it fits with SAFE_MARGIN either side AND leaves the
    window a dead zone's worth of play, else the face box on the same
    terms, else only the face's centre line. A tight close-up whose head is
    wider than a 9:16 window can never be held whole: demanding it turned
    every detector wobble into a re-aim (a hunting camera, 16 holds in 9 s
    of Elon), so the window then just keeps the face — or its centre — and
    the dead zone keeps it still."""
    head = head_box(face)

    def axis(h0, h1, f0, f1, size, dz):
        room = (1.0 - dz) * size - 2.0 * SAFE_MARGIN * size
        if h1 - h0 <= room:
            return h0, h1
        if f1 - f0 <= room:
            return f0, f1
        c = (f0 + f1) / 2.0
        return c, c
    x0, x1 = axis(head[0], head[2], face[0], face[2], ww, DEAD_ZONE[0])
    y0, y1 = axis(head[1], head[3], face[1], face[3], wh, DEAD_ZONE[1])
    return [x0, y0, x1, y1]


def _isect(a, b):
    return (max(a[0], b[0]), min(a[1], b[1]))


def _empty(iv):
    return iv[0] > iv[1] + 1e-12


def _clip(v, iv):
    return min(max(v, iv[0]), iv[1])


def _bounds(size):
    """Centres that keep a window of ``size`` (fraction) inside 0..1."""
    if size >= 1.0 - 1e-9:
        return (0.5, 0.5)
    return (size / 2.0, 1.0 - size / 2.0)


def _median(vals):
    s = sorted(vals)
    return s[len(s) // 2] if s else None


def _smooth_looks(samples, reach_s=1.0):
    out = []
    for t, _box, _look in samples:
        near = [lk for tt, _b, lk in samples if abs(tt - t) <= reach_s]
        out.append(sum(near) / len(near) if near else 0.0)
    return out


def smooth(samples, median_s=.6, mean_s=.3):
    """The face track without detector jitter: each box coordinate the
    median of its samples within ``median_s``, then the mean within
    ``mean_s`` (a Haar box wobbles a few % frame to frame; the speaker does
    not)."""
    ts = [s[0] for s in samples]
    med = []
    for t, _b, look in samples:
        near = [s[1] for s in samples if abs(s[0] - t) <= median_s]
        med.append((t, [_median([b[i] for b in near]) for i in range(4)], look))
    out = []
    for t, _b, look in med:
        near = [m[1] for m in med if abs(m[0] - t) <= mean_s]
        out.append((t, [sum(b[i] for b in near) / len(near) for i in range(4)], look))
    return out if ts else []


def _desired(samples, ww, wh, headroom, lead):
    """Per sample: (t, dx, dy, hard x band, hard y band) — the window
    centre the composition wants, and the centres that keep the head
    inside the window."""
    bx, by = _bounds(ww), _bounds(wh)
    looks = _smooth_looks(samples)
    mx, my = SAFE_MARGIN * ww, SAFE_MARGIN * wh
    rows = []
    for (t, f, _look), lk in zip(samples, looks):
        head = head_box(f)
        dx = (f[0] + f[2]) / 2.0 + lead * ww * max(-1.0, min(1.0, lk))
        dy = head[1] - headroom * wh + wh / 2.0
        kb = keep_box(f, ww, wh)
        hx = (kb[2] + mx - ww / 2.0, kb[0] - mx + ww / 2.0)
        hy = (kb[3] + my - wh / 2.0, kb[1] - my + wh / 2.0)
        if _empty(hx):                       # a head wider than the window
            hx = ((f[0] + f[2]) / 2.0,) * 2
        if _empty(hy):
            hy = (dy, dy)
        hx, hy = _isect(hx, bx), _isect(hy, by)
        if _empty(hx):
            hx = (_clip(dx, bx),) * 2
        if _empty(hy):
            hy = (_clip(dy, by),) * 2
        rows.append((t, _clip(dx, bx), _clip(dy, by), hx, hy))
    return rows


def nudge(aim, samples, ww, wh, kept=None):
    """The aim nearest ``aim`` (a window centre, source fractions) that
    keeps every sample's keep_box inside the window — the least correction
    of a still shot whose aim cuts the speaker (restraint: the shot keeps
    the composition it was given, moved only as far as it must). None when
    no single aim holds them all. ``kept``: only samples on kept footage."""
    bx, by = _bounds(ww), _bounds(wh)
    mx, my = SAFE_MARGIN * ww, SAFE_MARGIN * wh
    ix, iy = bx, by
    for t, f, _look in samples or []:
        if kept and not any(float(a) - .05 <= t <= float(b) + .05 for a, b in kept):
            continue
        kb = keep_box(f, ww, wh)
        for axis, raw, bounds in ((0, (kb[2] + mx - ww / 2.0, kb[0] - mx + ww / 2.0), bx),
                                  (1, (kb[3] + my - wh / 2.0, kb[1] - my + wh / 2.0), by)):
            band = _isect(raw, bounds)
            if _empty(band):
                # past the source's edge (or a window as tall as the
                # source): as near as the source allows, like _desired
                band = (_clip((raw[0] + raw[1]) / 2.0, bounds),) * 2
            if axis == 0:
                ix = _isect(ix, band)
            else:
                iy = _isect(iy, band)
    if _empty(ix) or _empty(iy):
        return None
    return _clip(float(aim[0]), ix), _clip(float(aim[1]), iy)


def _holds(rows, ww, wh, dz):
    """Maximal runs of samples whose dead-zone bands share an aim; each
    hold aims at its samples' median, clipped into the shared band."""
    zx, zy = dz[0] * ww, dz[1] * wh
    bands = []
    for t, dx, dy, hx, hy in rows:
        cx = _isect((dx - zx, dx + zx), hx)
        cy = _isect((dy - zy, dy + zy), hy)
        bands.append((hx if _empty(cx) else cx, hy if _empty(cy) else cy))
    holds, i = [], 0
    while i < len(rows):
        ix, iy = bands[i]
        j = i
        while j + 1 < len(rows):
            nx, ny = _isect(ix, bands[j + 1][0]), _isect(iy, bands[j + 1][1])
            if _empty(nx) or _empty(ny):
                break
            ix, iy, j = nx, ny, j + 1
        ax = _clip(_median([r[1] for r in rows[i:j + 1]]), ix)
        ay = _clip(_median([r[2] for r in rows[i:j + 1]]), iy)
        holds.append([i, j, ax, ay, ix, iy])
        i = j + 1
    # A re-aim smaller than MIN_STEP is a nudge, and nudges read as a
    # hunting camera: the earlier aim holds on while it still keeps every
    # head of the next hold inside the window (the hard band).
    merged = []
    for h in holds:
        if merged:
            p = merged[-1]
            small = (abs(h[2] - p[2]) <= MIN_STEP * ww
                     and abs(h[3] - p[3]) <= MIN_STEP * wh)
            fits = all(r[3][0] - 1e-9 <= p[2] <= r[3][1] + 1e-9
                       and r[4][0] - 1e-9 <= p[3] <= r[4][1] + 1e-9
                       for r in rows[h[0]:h[1] + 1])
            if small and fits:
                p[1] = h[1]
                continue
        merged.append(h)
    return merged


def _gaps(kept, lo, hi):
    """Removed stretches between kept fragments, clipped to [lo, hi]."""
    frags = sorted((float(a), float(b)) for a, b in kept or [])
    out = []
    for (_a0, b0), (a1, _b1) in zip(frags, frags[1:]):
        g0, g1 = max(b0, lo), min(a1, hi)
        if g1 - g0 >= .08:
            out.append((g0, g1))
    return out


def _move_s(dx, dy, ww, wh, vmax=VMAX):
    d = max(abs(dx) / max(ww, 1e-9), abs(dy) / max(wh, 1e-9))
    return max(MOVE_MIN_S, 1.5 * d / vmax, math.sqrt(6.0 * d / AMAX))


def _keys(holds, rows, moves_, t0, t1):
    """Keys from holds and their moves [(c, T)]."""
    keys = []
    n = len(holds)
    for k, (_i, _j, ax, ay, *_bands) in enumerate(holds):
        hs = t0 if k == 0 else moves_[k - 1][0] + moves_[k - 1][1] / 2.0
        he = t1 if k == n - 1 else moves_[k][0] - moves_[k][1] / 2.0
        # A short hold between two moves the same way is a waypoint, not a
        # stop: one key there keeps the pan going (no stop-and-go walk).
        through = 0 < k < n - 1 and he - hs < HOLD_MIN_S and all(
            (holds[k][i] - holds[k - 1][i]) * (holds[k + 1][i] - holds[k][i]) >= 0
            for i in (2, 3))
        if k == 0:
            keys.append((max(t0, min(he, t1)), ax, ay))
        elif k == n - 1:
            keys.append((min(t1, max(hs, t0)), ax, ay))
        elif he - hs >= .06 and not through:
            keys.append((hs, ax, ay))
            keys.append((he, ax, ay))
        else:
            keys.append(((hs + he) / 2.0, ax, ay))
    keys.sort(key=lambda k: k[0])
    out = []
    for t, x, y in keys:
        if out and t - out[-1][0] < .04:
            # two keys closer than a frame: one point, at their mean
            pt, px, py = out.pop()
            t, x, y = (pt + t) / 2.0, (px + x) / 2.0, (py + y) / 2.0
        out.append((t, x, y))
    return out


def _path_np(ts, vs, m, T):
    """path() over a numpy array of times (the planner's checks)."""
    import numpy as np
    out = np.full(T.shape, float(vs[0]))
    for i in range(len(ts) - 1):
        h = ts[i + 1] - ts[i]
        dv = vs[i + 1] - vs[i]
        a, b = h * m[i], h * m[i + 1]
        if h <= 0 or (dv == 0 and a == 0 and b == 0):
            continue
        u = np.clip((T - ts[i]) / h, 0.0, 1.0)
        out += dv * (u * u * (3.0 - 2.0 * u)) + a * (u * (1.0 - u) * (1.0 - u)) \
            + b * (u * u * (u - 1.0))
    return out


def _violation(keys, samples, ww, wh, lo=None, hi=None, step=1 / 15.0,
               kept=None):
    """(worst overhang of the head past the window edge, as a share of the
    window, and its time) along the planned path between lo and hi — on
    the KEPT moments only (``kept`` fragments; a move inside removed
    footage is never seen) — the face interpolated between samples."""
    import numpy as np
    if not samples or not keys:
        return 0.0, None
    st = np.array([s[0] for s in samples], float)
    heads = np.array([keep_box(s[1], ww, wh) for s in samples], float)
    a = st[0] if lo is None else max(st[0], lo)
    b = st[-1] if hi is None else min(st[-1], hi)
    if b < a:
        return 0.0, None
    T = np.arange(a, b + 1e-9, step)
    if kept:
        mask = np.zeros(T.shape, bool)
        for k0, k1 in kept:
            mask |= (T >= float(k0)) & (T <= float(k1))
        T = T[mask]
        if not len(T):
            return 0.0, None
    hd = [np.interp(T, st, heads[:, i]) for i in range(4)]
    ts = [k[0] for k in keys]
    xs = [k[1] for k in keys]
    ys = [k[2] for k in keys]
    cx = _path_np(ts, xs, slopes(ts, xs), T)
    cy = _path_np(ts, ys, slopes(ts, ys), T)
    over = np.maximum.reduce([
        np.zeros_like(T),
        ((cx - ww / 2.0) - np.maximum(hd[0], 0.0)) / ww,
        (np.minimum(hd[2], 1.0) - (cx + ww / 2.0)) / ww,
        ((cy - wh / 2.0) - np.maximum(hd[1], 0.0)) / wh,
        (np.minimum(hd[3], 1.0) - (cy + wh / 2.0)) / wh])
    i = int(np.argmax(over))
    if over[i] <= 1e-9:
        return 0.0, None
    return float(over[i]), float(T[i])


def plan(samples, t0, t1, ww, wh, *, kept=None, dead_zone=DEAD_ZONE,
         headroom=HEADROOM_TARGET, lead=LEAD):
    """Plan one shot's follow path.

    samples: [(t, face box, look)] SOURCE seconds and fractions (one person,
    speaker_track's output); [t0, t1] the shot's span; kept: the kept
    fragments inside it (default all of it); ww, wh the window (crop or
    card rect) as fractions of the source. Returns (keys, info): keys
    [[t, x, y], ...] of the window CENTRE, or None when one held position
    frames the whole shot (restraint: no follow needed). A move that can
    happen inside removed footage (a jump cut) does — the framing changes
    on the cut instead of gliding on screen. info: {"holds", "moves",
    "hidden" (moves made on a cut), "overhang" (worst head overhang past
    the window's edge, share of the window, along the plan), "static"}."""
    t0, t1 = float(t0), float(t1)
    kept = [(max(t0, float(a)), min(t1, float(b))) for a, b in (kept or [(t0, t1)])
            if min(t1, float(b)) - max(t0, float(a)) > 1e-3]
    raw = sorted([s for s in samples or []
                  if t0 - .3 <= float(s[0]) <= t1 + .3], key=lambda s: s[0])
    info = {"holds": 0, "moves": 0, "hidden": 0, "overhang": 0.0, "static": None}
    if len(raw) < 2 or t1 - t0 < .3 or not kept:
        return None, info
    sm = smooth(raw)
    dz = tuple(dead_zone)
    for _attempt in range(4):
        rows = _desired(sm, ww, wh, headroom, lead)
        holds = _holds(rows, ww, wh, dz)
        if len(holds) <= MAX_KEYS // 2:
            break
        dz = (dz[0] * 1.5, dz[1] * 1.5)      # a very busy shot: calmer
    info["holds"] = len(holds)
    if len(holds) == 1:
        info["static"] = (holds[0][2], holds[0][3])
        return None, info

    def check(moves_, lo=None, hi=None):
        return _violation(_keys(holds, rows, moves_, t0, t1), raw, ww, wh,
                          lo, hi, kept=kept)

    moves_, hidden = [], []
    for k in range(len(holds) - 1):
        te, ts_ = rows[holds[k][1]][0], rows[holds[k + 1][0]][0]
        T = _move_s(holds[k + 1][2] - holds[k][2], holds[k + 1][3] - holds[k][3], ww, wh)
        T = min(T, t1 - t0)
        # an operator reacts to the move, a beat after it starts (the head
        # check below pulls it earlier where the head would leave)
        c = min(max((te + ts_) / 2.0 + REACT * T, t0 + T / 2.0), t1 - T / 2.0)
        moves_.append([c, T])
        hidden.append(False)
    # A move inside removed footage near its moment is a reframe on the
    # jump cut: unseen, and the cut reads as the edit it is.
    for k in range(len(moves_)):
        te, ts_ = rows[holds[k][1]][0], rows[holds[k + 1][0]][0]
        lo_ = (moves_[k - 1][0] + moves_[k - 1][1] / 2.0) if k else t0
        hi_ = (moves_[k + 1][0] - moves_[k + 1][1] / 2.0) if k + 1 < len(moves_) else t1
        cands = [g for g in _gaps(kept, max(lo_, te - 1.0), min(hi_, ts_ + 1.0))]
        if not cands:
            continue
        span = (min(te, ts_) - 1.5, max(te, ts_) + 1.5)
        visible = check(moves_, *span)[0]
        best = None
        for g0, g1 in cands:
            trial = [list(m) for m in moves_]
            trial[k] = [(g0 + g1) / 2.0, max(.02, (g1 - g0) - .02)]
            w, _at = check(trial, *span)
            dist = min(abs((g0 + g1) / 2.0 - (te + ts_) / 2.0), 9.0)
            if w <= visible + 1e-4 and (best is None or (w, dist) < best[:2]):
                best = (w, dist, trial[k])
        if best:
            moves_[k] = best[2]
            hidden[k] = True
    worst, at = check(moves_)
    # The head must not leave the window: a visible move that lets it is
    # made faster (down to VMAX_HARD) or started earlier/later, nearest
    # first.
    for _round in range(24):
        if worst <= 1e-4 or at is None:
            break
        free = [i for i in range(len(moves_)) if not hidden[i]]
        if not free:
            break
        k = min(free, key=lambda i: abs(moves_[i][0] - at))
        c, T = moves_[k]
        floor_T = _move_s(holds[k + 1][2] - holds[k][2], holds[k + 1][3] - holds[k][3],
                          ww, wh, VMAX_HARD)
        lo_, hi_ = c - T / 2.0 - 1.5, c + T / 2.0 + 1.5
        here, _at = check(moves_, lo_, hi_)
        best = (here, c, T)
        for cand_T in sorted({T, max(floor_T, T * .8), max(floor_T, T * .6), floor_T}):
            for shift in (0.0, -.15, .15, -.3, .3, -.5, -.75):
                cand_c = min(max(c + shift, t0 + cand_T / 2.0), t1 - cand_T / 2.0)
                moves_[k] = [cand_c, cand_T]
                w2, _a2 = check(moves_, lo_, hi_)
                if w2 < best[0] - 1e-6:
                    best = (w2, cand_c, cand_T)
        moves_[k] = [best[1], best[2]]
        if best[0] >= here - 1e-6:
            break
        worst, at = check(moves_)
    keys = _keys(holds, rows, moves_, t0, t1)
    info.update(moves=len(moves_), hidden=sum(hidden), overhang=round(worst, 4))
    out = [[round(t, 3), round(x, 4), round(y, 4)] for t, x, y in keys]
    if len({(x, y) for _t, x, y in out}) < 2:
        info["static"] = (out[0][1], out[0][2])
        return None, info
    return out, info


# ═════════════════════════════════════════════════════════════════════════
# The render: one static union crop, a per-frame even crop, sub-pixel rest
# ═════════════════════════════════════════════════════════════════════════

def _floor_even(v):
    return int(math.floor(v / 2.0)) * 2


def _ceil_even(v):
    return int(math.ceil(v / 2.0)) * 2


def _round_even(v):
    return int(round(v / 2.0)) * 2


class Axis:
    """One axis of a follow block: window origins along the block, mapped
    onto the scaled (and padded) union frame the per-frame crop reads."""

    def __init__(self, src, out, k, ts, origins, lo, hi):
        self.src, self.out, self.k = float(src), int(out), float(k)
        size = out / k                       # the window, source px
        # PCHIP never overshoots its keys: the extremes over [lo, hi] are
        # keys inside it or the path at its ends.
        m = slopes(ts, origins)
        vals = [path(ts, origins, t, m) for t in (lo, hi)]
        vals += [v for t, v in zip(ts, origins) if lo < t < hi]
        mn, mx = min(vals), max(vals)
        margin = 4.0 / k + 2.0
        smax = _floor_even(src)
        self.s0 = max(0, _floor_even(mn - margin))
        self.s1 = min(smax, _ceil_even(mx + size + margin))
        if self.s1 - self.s0 < 2:
            self.s0, self.s1 = 0, smax
        span = self.s1 - self.s0
        self.scaled = max(2, _round_even(span * k))
        kx = self.scaled / float(span)
        raw = [(o - self.s0) * kx for o in origins]
        lo_x = min([(v - self.s0) * kx for v in vals] + [0.0])
        hi_x = max((v - self.s0) * kx for v in vals)
        self.pad0 = _ceil_even(max(0.0, -lo_x))
        # Every key on an even pixel: a hold is then an exact crop, and the
        # sub-pixel stage only ever runs while the window moves (it reads
        # up to 4 px past the window, so a moving axis keeps that margin).
        snapped = [_round_even(self.pad0 + v) for v in raw]
        # Keys outside the block keep their values (a block that opens in
        # the middle of a move must start where the move is); only the
        # path inside [lo, hi] has to lie on the union frame, and does.
        self.static = not any(
            snapped[i] != snapped[i + 1] and ts[i + 1] > lo and ts[i] < hi
            for i in range(len(ts) - 1))
        inside = [path(ts, [float(v) for v in snapped], t) for t in (lo, hi)]
        inside += [v for t, v in zip(ts, snapped) if lo < t < hi]
        reach = out + (0 if self.static else 4)
        self.pad1 = _ceil_even(max(0.0, max(hi_x + self.pad0, max(inside))
                                   + reach - self.pad0 - self.scaled))
        self.full = self.pad0 + self.scaled + self.pad1
        self.keys = snapped
        if self.static:
            v = int(round(inside[0]))
            self.keys = [min(max(v, 0), max(0, self.full - reach))] * len(ts)


def _between(windows, fps):
    half = .5 / float(fps)
    return camera._balanced_sum([f"between(t,{a - half:.4f},{b + half:.4f})"
                                 for a, b in windows])


def window_chain(parts, in_label, out_label, uid, *, src_size, out_size, k,
                 ts, ox, oy, length, fps, tail, grade=None, interpolation=None,
                 sharpen=None, flags="", time_map=None, key_span=None):
    """Append a follow block: [in_label] (the block's SOURCE frames, PTS 0 at
    its first) -> [out_label], exactly out_size, CFR via ``tail``.

    ts: key times on the BLOCK clock (seconds from its first frame); ox, oy:
    the window's top-left in SOURCE pixels at each key (it may reach past
    the source: black there). k: output px per source px. length: the
    block's program seconds. time_map(text) -> text maps the block clock
    to the keys' clock (a sped segment), None = the same clock; key_span
    is then the (start, end) the block covers on the keys' clock."""
    sw, sh = src_size
    OW, OH = out_size
    lo, hi = (0.0, float(length)) if key_span is None else \
        (float(key_span[0]), float(key_span[1]))
    ax = Axis(sw, OW, k, ts, ox, lo, hi)
    ay = Axis(sh, OH, k, ts, oy, lo, hi)
    head = f"[{in_label}]crop={ax.s1 - ax.s0}:{ay.s1 - ay.s0}:{ax.s0}:{ay.s0}"
    if grade:
        head += f",format=yuv420p,{grade}"
    head += f"{sharpen or ''},scale={ax.scaled}:{ay.scaled}{flags},setsar=1"
    if ax.pad0 or ax.pad1 or ay.pad0 or ay.pad1:
        head += (f",pad={ax.full}:{ay.full}:{ax.pad0}:{ay.pad0}:color=black")
    head += f",{tail}"
    fpsr = round(float(fps), 3)

    def value(axis, tvar):
        tv = camera.X(time_map(tvar.s) if time_map else tvar.s)
        if axis.static:
            return float(axis.keys[0])
        return camera.let(_R_T, tv, lambda T: path(ts, [float(v) for v in axis.keys], T))

    def whole(axis, tvar):
        """The even crop origin, and the sub-pixel rest, at time tvar."""
        if axis.static:
            return float(axis.keys[0]), 0.0
        limit = float(max(0, axis.full - axis.out - 4))
        ival = camera.let(_R_X, value(axis, tvar), lambda v: camera.clip(
            2.0 * camera.floor((v + 1e-6) / 2.0), 0.0, limit))
        frac = camera.let(_R_X, value(axis, tvar), lambda v: v - camera.clip(
            2.0 * camera.floor((v + 1e-6) / 2.0), 0.0, limit))
        return ival, frac

    if ax.static and ay.static:
        parts.append(f"{head},crop={OW}:{OH}:{ax.keys[0]}:{ay.keys[0]}[{out_label}]")
        return
    tn = camera.X(f"n/{fpsr:.3f}")
    ix, _fx = whole(ax, tn)
    iy, _fy = whole(ay, tn)
    cw = OW + (0 if ax.static else 4)
    ch = OH + (0 if ay.static else 4)
    crop = f"crop={cw}:{ch}:x='{camera.lit(ix)}':y='{camera.lit(iy)}'"
    to = camera.X(f"(on-1)/{fpsr:.3f}")
    _ix, fx = whole(ax, to)
    _iy, fy = whole(ay, to)
    sx, sy = camera.lit(fx), camera.lit(fy)
    c = {"x0": sx, "y0": sy,
         "x1": f"{cw}+{sx}" if sx != "0" else str(cw), "y1": sy,
         "x2": sx, "y2": f"{ch}+{sy}" if sy != "0" else str(ch),
         "x3": f"{cw}+{sx}" if sx != "0" else str(cw),
         "y3": f"{ch}+{sy}" if sy != "0" else str(ch)}
    opts = ":".join(f"{n}='{v}'" for n, v in c.items())
    # Only where a key segment moves (on the block clock); a sped block
    # maps its clock, so it keeps the stage on throughout.
    win = []
    if time_map is None:
        for i in range(len(ts) - 1):
            if (ax.keys[i] != ax.keys[i + 1] or ay.keys[i] != ay.keys[i + 1]) \
                    and ts[i + 1] > lo and ts[i] < hi:
                a, b = max(lo, ts[i]), min(hi, ts[i + 1])
                if win and win[-1][1] >= a - 1e-9:
                    win[-1] = (win[-1][0], b)
                else:
                    win.append((a, b))
    enable = f":enable='{_between(win, fps)}'" if win else ""
    if time_map is None and not win:
        # nothing moves inside this block: the even crop alone is exact
        parts.append(f"{head},{crop},crop={OW}:{OH}:0:0[{out_label}]")
        return
    persp = (f"perspective={opts}:sense=source:eval=frame"
             f":interpolation={interpolation or camera.INTERPOLATION}{enable}")
    parts.append(f"{head},{crop},{persp},crop={OW}:{OH}:0:0[{out_label}]")


def block_time_map(pieces, s):
    """time_map for window_chain on a sped segment: block seconds -> the
    keys' clock (source seconds minus the block's source start ``s``).
    pieces: [(src start, src end, factor)] (renderer.speed_pieces)."""
    if not pieces or all(abs(f - 1.0) < 1e-9 for _a, _b, f in pieces):
        return None
    rows, P = [], 0.0
    for a, b, f in pieces:
        rows.append((P, a - s, f))
        P += (b - a) / f

    def tmap(tau):
        out = f"{rows[-1][1]:.6f}+({tau}-{rows[-1][0]:.6f})*{rows[-1][2]:.6f}"
        for P0, a0, f in reversed(rows[:-1]):
            nxt = rows[rows.index((P0, a0, f)) + 1][0]
            out = (f"if(lt({tau},{nxt:.6f}),{a0:.6f}+({tau}-{P0:.6f})*{f:.6f},"
                   f"{out})")
        return out
    return tmap
