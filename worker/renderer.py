"""Frame-accurate EDL renderer.

Always re-encodes (never stream-copies): trim+concat of keep segments with
inserts spliced at their boundaries, captions burned from a generated .ass,
music mixed with gain + speech ducking, voiceover mixed with program-audio
ducking, volume automation via enable='between(t,a,b)'.

Every source — the main video, inserted clips, inserted images — is
normalized to the project's output frame (EDL.frame), fps and audio format
before concat, so mixed-resolution material can never distort.

previews read the 720p PROXY and encode fast at 480p with dense keyframes
(Safari scrubbing accuracy); finals read the ORIGINAL at source resolution.
Every render also emits a 3x3 contact sheet for the agent's self-check.
"""

import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid

import audio_qc
import audit
import captions as caplib
import config
import db as dbx
import gradelut
import graphics
import media
import screenframe
import sheets
import stitch
import storage
import timeline as timeline_mod
import travel
from schemas import (clean_fingerprint, patch_fingerprint, EDLValidationError,
                     is_canvas_program, keep_boundaries, quad_bbox,
                     speed_pieces, validate_edl)
from timeline import Timeline, merge_spans, transition_junctions

DUCK_DB = -12.0            # music under speech AND program audio under voiceover
MAX_ENABLE_SPANS = 80
AUDIO_NORM = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"

# Color-grade presets (EDL.effects.grade). Applied to all footage after
# concat, BEFORE captions burn — text never gets graded.
GRADE_FILTERS = {
    "vibrant": "eq=saturation=1.35:contrast=1.08",
    "warm": "colorbalance=rs=.08:gs=.02:bs=-.08,eq=saturation=1.12",
    "cool": "colorbalance=rs=-.05:bs=.08,eq=saturation=1.05",
    "bw": "hue=s=0,eq=contrast=1.1",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "cinematic": ("colorbalance=bs=.05:rs=-.03,"
                  "eq=contrast=1.12:saturation=1.12:brightness=-0.02"),
}


def grade_custom_chain(gc):
    """The grade_custom filter chain, as build_filtergraph emits it — shared
    with the agent's grade contact strip (round 91), which must show EXACTLY
    the chain the render will run. Returns "" when nothing is set.

    Continuous color controls, applied AFTER the preset so "cinematic but
    warmer" composes. exposure maps to eq brightness (+-0.35 full scale);
    temperature/tint to colorbalance shadows+midtones — the portable
    approximation (colortemperature exists but its neutral point drifts
    across ffmpeg majors; colorbalance does not).
    """
    eq_bits = []
    if gc.get("exposure") is not None:
        eq_bits.append(f"brightness={0.35 * float(gc['exposure']):.3f}")
    if gc.get("contrast") is not None:
        eq_bits.append(f"contrast={float(gc['contrast']):.3f}")
    if gc.get("saturation") is not None:
        eq_bits.append(f"saturation={float(gc['saturation']):.3f}")
    cb_bits = []
    temp = float(gc.get("temperature") or 0.0)
    tint = float(gc.get("tint") or 0.0)
    rs = 0.10 * temp + 0.03 * tint
    bs = -0.12 * temp + 0.05 * tint
    gs = -0.08 * tint
    if abs(rs) > 1e-4:
        cb_bits.append(f"rs={rs:.3f}:rm={rs * 0.6:.3f}")
    if abs(gs) > 1e-4:
        cb_bits.append(f"gs={gs:.3f}:gm={gs * 0.6:.3f}")
    if abs(bs) > 1e-4:
        cb_bits.append(f"bs={bs:.3f}:bm={bs * 0.6:.3f}")
    chain = []
    if eq_bits:
        chain.append("eq=" + ":".join(eq_bits))
    if cb_bits:
        chain.append("colorbalance=" + ":".join(cb_bits))
    # shadows/highlights are TONAL-REGION controls, so they are a master
    # curve, not eq: a fixed midpoint (0.5) and a moved quarter-point is
    # what makes "lift the shadows" brighten the dark corners without
    # washing the whole image the way eq brightness does. The clamps keep
    # the five points strictly monotone for any in-range value, which is
    # all ffmpeg's curves requires.
    sh = float(gc.get("shadows") or 0.0)
    hl = float(gc.get("highlights") or 0.0)
    if abs(sh) > 1e-4 or abs(hl) > 1e-4:
        y0 = min(max(0.12 * sh, 0.0), 0.35)
        y1 = min(max(0.25 + 0.14 * sh, 0.02), 0.48)
        y2 = min(max(0.75 + 0.14 * hl, 0.52), 0.98)
        y3 = min(1.0, max(1.0 + 0.10 * hl, y2 + 0.01))
        chain.append(f"curves=master='0/{y0:.3f} 0.25/{y1:.3f} 0.5/0.5 "
                     f"0.75/{y2:.3f} 1/{y3:.3f}'")
    return ",".join(chain)


def _enable_expr(spans):
    return "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)


# ── The screen takeover (round 55) ──────────────────────────────────────────
# Transparent border, in pixels, padded around the asset before it is warped.
# `perspective` samples outside the source by CLAMPING to the edge pixel, so
# without this the pixels outside the destination quad are the asset's own
# opaque border smeared across the whole frame — the base video disappears.
# Two is enough to give the interpolator something transparent to reach for,
# and small enough that the compensation below is sub-pixel honest.
SCREEN_PAD_PX = 2
# What makes the takeover read as ONE camera move instead of an effect
# followed by a cut (round 62b, from a real user's "it should be so smooth
# you can't even tell where it switches"):
#   * the content FADES onto the glass over the window's first beat — its
#     first frame is never pixel-identical to what the filmed screen really
#     displays, and snapping it on read as a pop exactly where the move
#     begins;
#   * the momentum CARRIES THROUGH the cut — the push used to stop dead on
#     the arrival frame, and a dead stop is where the eye finds the seam. A
#     short landing keeps moving: the picture punches past full frame and
#     settles, the classic through-the-glass overshoot. It starts at exactly
#     zero extra zoom on the arrival frame itself, so the frame-identical
#     handoff (the round-55 invariant) is untouched.
SCREEN_FADE_IN_S = 0.35
SCREEN_LAND_ZOOM = 0.08
SCREEN_LAND_S = 0.55
# Round 64, from the user watching the round-63b render: "the next scene is
# appearing before even the laptop screen becomes full frame". He is right
# again, and the cause was WHEN the content faded on, not how: the fade ran
# from the WINDOW'S START (st=0), so with an accelerating 1.5s push the
# recording was fully opaque on the glass while the camera had covered ~10% of
# its travel — the viewer sat in a wide shot of the room watching the laptop
# play the next scene for a full second. The scene switch must happen where
# the eye cannot compare the two pictures: LATE in the dive, when the glass
# already dominates the frame. So the content's appearance is now a function
# of the PUSH'S PROGRESS, not of wall clock — alpha ramps from
# SCREEN_APPEAR_E0 to SCREEN_APPEAR_E1 of the eased zoom travel (the same e
# that drives z), which on an accelerating ease lands the dissolve in the
# fastest, most magnified part of the move. Until then the glass shows what
# the camera actually filmed — the professional grammar: push into the REAL
# screen, and the content materializes as it swells to meet the frame.
SCREEN_APPEAR_E0 = 0.45
SCREEN_APPEAR_E1 = 0.85
# A dissolve shorter than this reads as the pop the round-62b fade was added
# to kill, so the start slides earlier to protect it.
SCREEN_APPEAR_MIN_S = 0.12
# Round 63b, from the user watching the round-63 render: "it doesn't make
# sense to switch scenes unless the first one is zoomed close to full frame".
# He is right, and the geometry was hiding it: the push reached full frame
# EXACTLY at the cut, so with an accelerating ease the room was still visible
# around the glass until the final ~0.2s and the entire scene switch lived in
# a blink at the end of the dive. The arrival now happens EARLY: the push
# completes SCREEN_HOLD_S before the handoff, the content rides full-frame
# for that beat, and the landing punch starts AT THE ARRIVAL and carries
# THROUGH the cut with one continuous sin profile — the overlay grows past
# identity pre-cut and the program zoom continues the same curve post-cut, so
# the cut sits strictly inside a moving, already-full-frame picture. Both
# sides of the cut show the same content at the same magnification.
SCREEN_HOLD_S = 0.30
# Where along the push the shape/skew correction is allowed to begin, as a
# fraction of the eased travel (see the g weight in screen_lock_corner_paths).
SCREEN_CORR_E0 = 0.6
# Extra seconds of content decoded past the window end so the pinned branch
# can never run out of frames before the cut (round 66 — see the trim in the
# takeover chain). Display is still gated by the overlay's enable window.
SCREEN_SUPPLY_PAD_S = 0.35


def screen_lock_hold(dur):
    """How long the content rides full frame before the handoff. ONE
    function: the overlay's corner paths, the camera push and the landing
    term all read it, so the three cannot disagree about when the push
    actually lands."""
    return min(SCREEN_HOLD_S, max(0.0, float(dur)) * 0.25)


def _ease_expr(kind, p):
    """The move's shape as an ffmpeg expression over p (already clipped 0-1).

    'smooth' is smoothstep — zero velocity at both ends, which is what stops a
    push from starting with a visible lurch and stopping with a visible thud.
    """
    if kind == "linear":
        return p
    if kind == "accelerate":
        return f"({p})*({p})"
    return f"({p})*({p})*(3-2*({p}))"


def _ease_inv(kind, e):
    """The time fraction p at which _ease_expr reaches progress e — the ease
    functions are monotonic on [0,1], so each has a closed-form inverse.
    Used to place the content's appearance at a chosen point of the ZOOM
    TRAVEL (round 64): 'appear at 45% of the push' is a statement about e,
    and this converts it into the branch-local second `fade` needs."""
    e = min(max(float(e), 0.0), 1.0)
    if kind == "linear":
        return e
    if kind == "accelerate":
        return math.sqrt(e)
    # smoothstep: e = p*p*(3-2p). The real root on [0,1], via the
    # trigonometric solution of the depressed cubic.
    return 0.5 - math.sin(math.asin(1.0 - 2.0 * e) / 3.0)


def _screen_lock_ease(lock, tvar, t0, dur, fps):
    """The eased 0-1 progress of a takeover, as an expression over `tvar`.

    The denominator is the window MINUS one frame, not the window: the last
    frame a filter emits inside [t0, t0+dur] sits at t0+dur-1/fps, so dividing
    by the full window means the move stops just short of arriving and the pin
    hands off a pixel or two out of register. Both the camera push and the
    corner pin call this — the same string, so they cannot drift apart even by
    a rounding.
    """
    span = max(dur - 1.0 / max(fps, 1e-6), dur * 0.5, 1e-3)
    p = f"clip(({tvar}-{t0:.3f})/{span:.5f},0,1)" if t0 else \
        f"clip(({tvar})/{span:.5f},0,1)"
    return _ease_expr(lock.get("ease") or "smooth", p)


def screen_appear_window(lock, dur, fps):
    """(fade_start, fade_end) of the content's dissolve, in branch-local
    seconds. ONE function — the overlay chain emits it and the tests assert
    against it, so 'when does the content appear' has a single answer.

    The window is placed on the push's PROGRESS (e in [SCREEN_APPEAR_E0,
    SCREEN_APPEAR_E1]), converted to seconds through the ease's inverse: on
    an accelerating dive that lands the dissolve late and fast, which is the
    point — the room must already be gone from view when the scene changes.

    EXCEPT when the corners are MATCHED (round 65): then the quad came from
    finding the content's own pixels on the filmed glass, which means the
    screen is already displaying this very content — so the pin can live on
    the glass from the window's start (a short fade only smooths the residual
    exposure difference between the filmed emitter and the clean clip), and
    delaying it would HIDE the continuity the match just proved.
    """
    if lock.get("corners_source") == "matched":
        return 0.0, min(SCREEN_FADE_IN_S, dur * 0.5)
    hold = screen_lock_hold(dur)
    dur_push = dur - hold
    span = max(dur_push - 1.0 / max(fps, 1e-6), dur_push * 0.5, 1e-3)
    kind = lock.get("ease") or "smooth"
    f0 = span * _ease_inv(kind, SCREEN_APPEAR_E0)
    f1 = span * _ease_inv(kind, SCREEN_APPEAR_E1)
    if f1 - f0 < SCREEN_APPEAR_MIN_S:
        f0 = max(0.0, f1 - SCREEN_APPEAR_MIN_S)
    if f1 <= SCREEN_APPEAR_MIN_S:
        # A window too short to stage the late appearance at all: the old
        # start-of-window fade is the honest fallback.
        return 0.0, min(SCREEN_FADE_IN_S, dur * 0.5)
    return f0, f1


def screen_lock_geometry(lock):
    """The two numbers a screen takeover is defined by, from its quad alone.

    Returns (cx, cy, zoom_end): where the camera aims, and how far in it goes
    for the quad to exactly fill the frame. ONE function, called by the zoom
    stage AND by the corner pin, so the camera and the content cannot be
    computed from different arithmetic — that is the whole reason the takeover
    is a single EDL item and not a zoom sitting next to an overlay.
    """
    corners = [float(v) for v in lock["corners"]]
    x, y, w, h = quad_bbox(corners)
    cx, cy = x + w / 2.0, y + h / 2.0
    # max, not min: the quad must COVER the frame at the end, so the push is
    # driven by whichever side of it is furthest from filling.
    span = max(w, h, 1e-4)
    z_full = 1.0 / span
    push = min(max(float(lock.get("push", 1.0)), 0.0), 1.0)
    return cx, cy, 1.0 + push * (z_full - 1.0)


def _screen_lock_terms(lock, t, a, b, fps):
    """(strength_term, cx_term, cy_term) for the shared zoompan.

    The takeover's camera push is emitted as one more term of the SAME zoompan
    every other zoom rides, so a takeover and an unrelated zoom compose instead
    of fighting over the geometry filter — and so there is exactly one place
    the frame's crop is decided.
    """
    cx, cy, z_end = screen_lock_geometry(lock)
    # The ease spans the window MINUS the hold (round 63b): the camera lands
    # early and z rides at z_end through the hold — the clip() inside the
    # ease pins it at 1 for the remainder of the window.
    e = _screen_lock_ease(lock, t, a,
                          (b - a) - screen_lock_hold(b - a), fps)
    win = f"between({t},{a:.3f},{b:.3f})"
    strength = f"{z_end - 1.0:.5f}*({e})*{win}"
    cxt = (f"{cx - 0.5:.5f}*{win}" if abs(cx - 0.5) > 1e-6 else None)
    cyt = (f"{cy - 0.5:.5f}*{win}" if abs(cy - 0.5) > 1e-6 else None)
    return strength, cxt, cyt


def screen_lock_corner_paths(lock, W, H, fps, dur):
    """The eight per-frame `perspective` expressions that pin the content.

    Geometry, once, so the next reader does not have to re-derive it:

    The base is zoomed about (cx, cy) by z(t). The renderer's targeted zoom
    crops at offset (1-1/z)*cx and shows a window 1/z wide, so a point at frame
    fraction u lands on screen at (u - (1-1/z)*cx)*z. Note what that gives for
    u = cx: the quad's CENTRE is pinned at cx for every z — the screen does not
    drift across the frame as the camera pushes, it only grows. That is the
    property that makes the move read as a dolly rather than a pan.

    At z_end the quad exactly fills the frame in its longer dimension. Whatever
    is left over — the short dimension of a screen that is not the output's
    aspect, and the SKEW of a screen shot at an angle — is closed by adding the
    quad's own end-state error, weighted by g. Writing the correction as
    (frame_corner - lock_corner_at_end) and not as a blend toward the frame is
    what keeps a frontal, output-aspect screen glued to the glass for the whole
    push: its error is zero, so the correction term is identically zero and the
    content never detaches. An angled screen's error is its skew, and g rides
    it out over the tail of the move — the glass flattening into the frame,
    which IS the transition people mean when they say "it opens up".

    g is the ease SQUARED on purpose: the un-skew has to happen late, while the
    screen is already large and the correction is least visible.

    Expressions are in `on/fps`, not `t`: vf_perspective exposes W, H, `in` and
    `on` — there is no `t` variable. The chain forces CFR at the render rate
    first, so on/fps is exactly the local second.
    """
    cx, cy, z_end = screen_lock_geometry(lock)
    corners = [float(v) for v in lock["corners"]]
    tvar = f"on/{fps:.3f}"
    hold = screen_lock_hold(dur)
    dur_push = dur - hold
    e = _screen_lock_ease(lock, tvar, 0.0, dur_push, fps)
    z = f"(1+{z_end - 1.0:.5f}*({e}))"
    # The correction weight (round 65 reshape). g closes the quad's SKEW and
    # its SHAPE GAP: a 16:10 laptop filmed at an angle is ~1.5:1 on screen
    # while the output is 16:9, so the content must stretch ~15% of the frame
    # to become frame-shaped, and WHEN that happens is visible. e*e ran it
    # through the whole second half of the push — at 85% travel the content
    # already overhung the glass by a sixth of the frame width with the room
    # still visible around it, which a real user read (correctly) as a flat
    # rectangle floating over the room, "not tuned to the rotation of the
    # laptop". The correction now starts at SCREEN_CORR_E0 of the travel and
    # runs quadratically to the arrival: the content stays GLUED to the true
    # quad until the glass dominates the frame, and the un-shape happens
    # inside the fastest, most magnified stretch of the dive, where the room
    # is already at the edges. Still exactly 1 at the arrival, so the
    # frame-identical handoff is untouched.
    gl = f"clip(({e}-{SCREEN_CORR_E0})/{1.0 - SCREEN_CORR_E0:.3f},0,1)"
    g = f"(({gl})*({gl}))"
    # The through-cut punch (round 63b): from the moment the push lands
    # (dur_push), the content keeps moving — it grows past identity on the
    # same sin curve the program-side landing continues after the handoff,
    # so the cut sits inside one uninterrupted zoom instead of at the exact
    # first full-frame instant. Zero during the push itself.
    l_tot = max(hold + SCREEN_LAND_S, 1e-3)
    # land=False (round 71g): a dead-flat landing — no overshoot on either
    # side of the cut. Must gate BOTH this overlay-side grow and the
    # program-side post-cut term below in build_filtergraph, or the two
    # halves of the cut disagree by the settle amount on the handoff frame.
    grow = (f"(1+{SCREEN_LAND_ZOOM:.3f}"
            f"*sin(PI*clip(({tvar}-{dur_push:.3f})/{l_tot:.4f},0,1))"
            f"*gt({tvar},{dur_push:.3f}))") \
        if hold > 0.02 and lock.get("land") is not False else "1"
    # The destination is grown by the transparent border's share so the CONTENT
    # (which sits inset by SCREEN_PAD_PX) lands where the quad says. Without
    # this the takeover hands off two pixels small and the cut shows.
    kx = W / max(1.0, W - 2.0 * SCREEN_PAD_PX)
    ky = H / max(1.0, H - 2.0 * SCREEN_PAD_PX)
    # Round 63: a TRACKED screen. corner_path carries the quad's measured
    # motion through the window (the shot is handheld — the glass wobbles),
    # so each corner coordinate becomes a piecewise-linear function of time
    # instead of a constant. `corners` stays the ARRIVAL quad: it is what the
    # camera geometry above was computed from, and the end-state correction
    # below must aim at where the screen IS when the move lands, or the
    # un-skew would chase a moving target and overshoot.
    path = lock.get("corner_path")
    out = []
    # Storage order is the filter's order (TL, TR, BL, BR); the frame corner
    # each one has to arrive at follows the same order.
    frame_corners = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    for i, (fx, fy) in enumerate(frame_corners):
        if path:
            # The tracked coordinate is blended toward the ARRIVAL corner by
            # g — the same weight that closes the skew. Two reasons, one
            # mechanism: the ease reaches 1 on the LAST EMITTED frame
            # (dur - 1/fps, the round-55 minus-one-frame rule) while the
            # path's final knot sits at dur, so an unblended lerp lands the
            # handoff a few pixels out of identity; and near the landing the
            # screen fills most of the frame, where chasing hand wobble
            # would shake the whole picture — the blend retires the wobble
            # exactly as fast as the un-skew takes over.
            px = _piecewise_linear_expr(tvar,
                                        [(p[0], p[1 + 2 * i]) for p in path])
            py = _piecewise_linear_expr(tvar,
                                        [(p[0], p[2 + 2 * i]) for p in path])
            ax, ay = corners[2 * i], corners[2 * i + 1]
            qx = f"({px}*(1-{g})+{ax:.5f}*{g})"
            qy = f"({py}*(1-{g})+{ay:.5f}*{g})"
            lock_x = f"(({qx}-(1-1/{z})*{cx:.5f})*{z})"
            lock_y = f"(({qy}-(1-1/{z})*{cy:.5f})*{z})"
        else:
            qx, qy = corners[2 * i], corners[2 * i + 1]
            lock_x = f"(({qx:.5f}-(1-1/{z})*{cx:.5f})*{z})"
            lock_y = f"(({qy:.5f}-(1-1/{z})*{cy:.5f})*{z})"
        # the end state comes from the ARRIVAL quad in both branches
        eqx, eqy = corners[2 * i], corners[2 * i + 1]
        end_x = (eqx - (1.0 - 1.0 / z_end) * cx) * z_end
        end_y = (eqy - (1.0 - 1.0 / z_end) * cy) * z_end
        ex = f"({lock_x}+{g}*{fx - end_x:.5f})"
        ey = f"({lock_y}+{g}*{fy - end_y:.5f})"
        # fractions -> pixels, expanded about the frame centre by the border
        # share and by the through-cut punch (identity during the push).
        out.append(f"{W}*(0.5+({ex}-0.5)*{kx:.6f}*{grow})")
        out.append(f"{H}*(0.5+({ey}-0.5)*{ky:.6f}*{grow})")
    return out


def _piecewise_linear_expr(tvar, kfs):
    """Piecewise-linear value over `tvar` from [(t, v), ...] (ascending t).
    Held flat before the first knot and after the last. Segments are
    half-open [t_i, t_{i+1}) so shared knots are counted exactly once."""
    if len(kfs) == 1:
        return f"{kfs[0][1]:.5f}"
    terms = [f"{kfs[0][1]:.5f}*lt({tvar},{kfs[0][0]:.3f})"]
    for (t0, v0), (t1, v1) in zip(kfs, kfs[1:]):
        span = max(t1 - t0, 1e-4)
        seg = f"({v0:.5f}+{v1 - v0:.5f}*({tvar}-{t0:.3f})/{span:.4f})"
        terms.append(f"{seg}*gte({tvar},{t0:.3f})*lt({tvar},{t1:.3f})")
    terms.append(f"{kfs[-1][1]:.5f}*gte({tvar},{kfs[-1][0]:.3f})")
    return "(" + "+".join(terms) + ")"


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def _anim_expr(v, tvar):
    """Compile an AnimFloat (a constant or a keyframe list — see schemas)
    to an ffmpeg expression over `tvar` (an expression yielding the
    ELEMENT-LOCAL time in seconds). Easing curves are the same closed forms
    anim_value evaluates python-side, so tools, tests and renders agree.
    Nested if() rather than summed between(): keyframe segments share
    endpoints, and summed windows double-count exactly at the shared
    instant."""
    if not isinstance(v, list):
        return f"{float(v):.4f}"
    kfs = [(k["t"], k["v"], k.get("ease")) if isinstance(k, dict)
           else (k.t, k.v, k.ease) for k in v]
    expr = f"{kfs[-1][1]:.4f}"          # after the last keyframe: hold
    for i in range(len(kfs) - 1, 0, -1):
        t0, v0, _ = kfs[i - 1]
        t1, v1, ease = kfs[i]
        if t1 - t0 <= 1e-9 or ease == "hold":
            seg = f"{v0:.4f}"
        else:
            p = f"(({tvar}-{t0:.3f})/{t1 - t0:.3f})"
            if ease == "in":
                p = f"pow({p},2)"
            elif ease == "out":
                p = f"({p}*(2-{p}))"
            elif ease == "in_out":
                p = f"({p}*{p}*(3-2*{p}))"
            seg = f"({v0:.4f}+{v1 - v0:.4f}*{p})"
        expr = f"if(lt({tvar},{t1:.3f}),{seg},{expr})"
    return f"if(lt({tvar},{kfs[0][0]:.3f}),{kfs[0][1]:.4f},{expr})"


def _atempo_chain(factor):
    """atempo accepts 0.5-2.0 per instance; chain instances for the rest.
    Returns e.g. 'atempo=2.0,atempo=1.5' for 3.0x."""
    steps = []
    f = float(factor)
    while f > 2.0 + 1e-9:
        steps.append(2.0)
        f /= 2.0
    while f < 0.5 - 1e-9:
        steps.append(0.5)
        f /= 0.5
    steps.append(round(f, 4))
    return ",".join(f"atempo={s:g}" for s in steps)


def frame_dims(src_w, src_h, ratio):
    """Output dims for a target aspect ratio, never exceeding the source's
    pixel budget: the output's short side is the source's short side, the
    long side derived from the ratio and capped at the source's long side
    (re-deriving the short side when capped). 1920x1080 at 9:16 -> 1080x1920;
    at 1:1 -> 1080x1080; at 4:5 -> 1080x1350."""
    if not ratio or ratio == "source":
        return _even(src_w), _even(src_h)
    rw, rh = (int(x) for x in ratio.split(":"))
    short_src, long_src = min(src_w, src_h), max(src_w, src_h)
    r_long, r_short = max(rw, rh), min(rw, rh)
    long_out = short_src * r_long / r_short
    short_out = short_src
    if long_out > long_src:
        long_out = long_src
        short_out = long_out * r_short / r_long
    if rh >= rw:                       # portrait or square target
        return _even(short_out), _even(long_out)
    return _even(long_out), _even(short_out)


def _needs_preview_downscale(H):
    """Is a trailing preview downscale still worth emitting?

    False whenever the graph is already at (or under) the preview height,
    which since this round is every graph built through preview_geometry. A
    scale to the size you already are is not free — it is one more full-frame
    pass through swscale for every frame — so it is skipped rather than
    emitted and relied upon to be a no-op.

    TRUE when H is unknown. A caller that builds a graph without dimensions
    has not been through preview_geometry, so nothing has capped it and the
    height could be anything; the two outcomes are not symmetric. Emitting a
    scale that turns out to be redundant costs one pass, while skipping one
    that was needed ships a full-resolution file as the "preview" — slower to
    encode and larger than the final it stands in for.
    """
    cap = config.PREVIEW_MAX_HEIGHT
    if not cap:
        return False
    return H is None or H > cap


def preview_geometry(W, H, fps):
    """Shrink a PREVIEW's frame and rate to the proof-of-the-edit budget.

    Returns (W, H, fps) unchanged for a final export, or scaled down for a
    preview — see config.PREVIEW_MAX_LONG_EDGE for why this is the largest
    single speed lever in the product.

    The aspect ratio is preserved exactly (the long edge is capped, the short
    edge derived and made even), so nothing in the graph has to know: text,
    captions, watermark and zoom viewports are all expressed against W/H and
    scale with them. NEVER up-scales — a 540p source stays 540p.

    TWO CAPS USED TO DISAGREE, AND THE GRAPH PAID FOR IT. The long-edge cap
    below decided the size every filter ran at, and then a SECOND, unrelated
    `scale=-2:min(480,...)` at the very end of the graph decided the size the
    file was actually written at. For every real project the second one won:
    a 960x540 proxy sailed under the 1280 long-edge cap untouched, ran the
    whole stack at 540p, and was then thrown away down to 854x480. So the
    grade, the custom grade, the vignette, the unsharp, the zoom, the burned
    captions and the watermark each processed 1.26x the pixels that survived
    — and on the project this was measured against that stack is SIX
    full-frame passes at 6-9s apiece.

    Measured on project 368's real graph (55s, 960x540 proxy): the whole
    chain takes 43.2s with the downscale last, and 32.2s with it first — 25%
    of the render, paid for pixels nobody ever sees. Capping here instead
    means the trailing scale has nothing left to do and every filter runs at
    output size, which is also where sharpening and text SHOULD happen: text
    burned at 480 is text rendered at 480, not text rendered at 540 and then
    resampled.
    """
    cap = config.PREVIEW_MAX_LONG_EDGE
    if cap and max(W, H) > cap:
        k = cap / float(max(W, H))
        W, H = _even(W * k), _even(H * k)
    hcap = config.PREVIEW_MAX_HEIGHT
    if hcap and H > hcap:
        k = hcap / float(H)
        W, H = _even(W * k), _even(H * k)
    if config.PREVIEW_MAX_FPS:
        fps = min(fps, config.PREVIEW_MAX_FPS)
    return W, H, fps


def _normalize_video(parts, in_label, out_label, W, H, fps, mode, uid,
                     focus=None, seg_dur=None):
    """Append graph parts that bring in_label to exactly WxH @ fps, sar 1.
    mode: crop (center-crop), pad (black bars), pad_blur (blurred backdrop).

    focus (round 36): (fx, fy) fractions of the source frame the crop window
    centers on, or None for the legacy center crop. Only the crop mode uses
    it — pad modes never discard picture. None/center emits the EXACT legacy
    filter string, so every stored EDL (all focus-less) renders
    byte-identically.

    seg_dur (round 56): this block's own PROGRAM length, when the block is
    about to become a `concat` segment. `fps` is what makes that concat legal
    — kept spans, a looped PNG title card and a clip from another file all
    arrive at different rates and concat needs one — but on **ffmpeg 5.1.9**
    (Debian bookworm, which is what this image installs) `fps` after a
    `setpts=PTS-STARTPTS` emits frames PAST the segment's real content, and
    concat then honours that longer duration when placing the next segment.

    Three things kept it hidden for a day of forensics:
      * ffmpeg 8.1.2 renders the IDENTICAL graph to the correct length, so it
        never reproduced on a dev machine.
      * Video alone is correct even on 5.1 — the stretch appears only when
        audio shares the concat, because that is when the padded video is what
        the segment's length is taken from.
      * A preview renders from our own proxy and a final from the customer's
        original, which made it look like a property of their file.

    Measured on the export that exposed it (project 226, 14 kept spans + a 1.5s
    title card): 158.58s for a 150.48s edit on 5.1.9, +0.6s per segment
    compounding, and 150.53s from the same graph on 8.1.2. It failed
    verification three times and the user was told only "the render is the
    wrong length".

    So the block is bounded to the length the EDL says it is. On 5.1 that
    discards the padding; on 8.1 there is nothing to discard and the trim is a
    no-op — which is what makes it safe to apply unconditionally instead of
    sniffing an ffmpeg version into a filter chain. Verified on both builds
    against the real EDL: 150.60s and 150.53s for 150.48s expected.
    """
    bound = ("" if seg_dur is None
             else f"trim=end={float(seg_dur):.3f},setpts=PTS-STARTPTS,")
    tail = f"fps={fps:.3f},{bound}setsar=1,format=yuv420p"
    if mode == "pad":
        parts.append(
            f"[{in_label}]{frame_fit_filter(mode, W, H)},{tail}[{out_label}]")
    elif mode == "pad_blur":
        parts.append(f"[{in_label}]split[pbA{uid}][pbB{uid}]")
        parts.append(f"[pbA{uid}]scale={W}:{H}:"
                     f"force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"boxblur=20[pbBG{uid}]")
        parts.append(f"[pbB{uid}]scale={W}:{H}:"
                     f"force_original_aspect_ratio=decrease[pbFG{uid}]")
        parts.append(f"[pbBG{uid}][pbFG{uid}]overlay=(W-w)/2:(H-h)/2,"
                     f"{tail}[{out_label}]")
    else:                              # crop
        parts.append(f"[{in_label}]{frame_fit_filter(mode, W, H, focus)},"
                     f"{tail}[{out_label}]")


def frame_fit_filter(mode, W, H, focus=None, pad_color="black"):
    """The scale (+crop or +pad) that maps a SOURCE frame onto the output frame.

    Extracted from _normalize_video so that anything which has to land in
    EXACTLY the same geometry as the picture can ask for it instead of
    re-deriving it — worker/matte.py measures a subject mask that is composited
    over the render, and a mask cropped differently from the frame is a subject
    cut out of the wrong part of the picture.

    Byte-identical to what _normalize_video emitted before the extraction
    (several tests compare whole filtergraphs against stored legacy strings).
    'pad_blur' shares pad's geometry: the picture content lands in the same
    fitted rectangle, and the blurred backdrop behind it is the base picture's
    business, not a mask's.
    """
    if mode in ("pad", "pad_blur"):
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad_color}")
    fx = focus[0] if focus else None
    fy = focus[1] if focus else None
    if (fx is not None and abs(float(fx) - 0.5) > 1e-6) or \
            (fy is not None and abs(float(fy) - 0.5) > 1e-6):
        # Fractions survive the uniform scale, so the focus point maps
        # straight onto the SCALED frame; clip() keeps the window inside
        # the picture when the subject sits near an edge.
        xe = (f"x='clip(iw*{float(fx if fx is not None else 0.5):.4f}"
              f"-ow/2,0,iw-ow)'")
        ye = (f"y='clip(ih*{float(fy if fy is not None else 0.5):.4f}"
              f"-oh/2,0,ih-oh)'")
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:{xe}:{ye}")
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}")


def fit_fractions(src_w, src_h, W, H, mode=None, focus=None):
    """Python mirror of frame_fit_filter's geometry, in FRACTIONS.

    Round 72: look_at(output_times) shows the assembled program, and 'what
    the viewer sees' includes how a frame lands on the canvas — a 16:10
    screen recording cover-cropped into a 16:9 program does not show its top
    and bottom edges, and every aimed coordinate is a fraction of the CANVAS,
    not of the raw frame. This returns what the two ffmpeg chains above
    produce, without running them:

      ('crop', x0, y0, x1, y1)  — the rect OF THE SOURCE frame the output
          shows (cover fit; focus mirrors the clip() expressions above)
      ('pad',  x0, y0, x1, y1)  — the rect OF THE OUTPUT frame the whole
          source lands in (contain fit; the rest is bars)

    Tests assert this against the emitted filter strings' arithmetic — the
    two must never drift."""
    src_ar = float(src_w) / float(src_h)
    out_ar = float(W) / float(H)
    if (mode or "crop") in ("pad", "pad_blur"):
        if src_ar >= out_ar:            # bars top/bottom
            fh = out_ar / src_ar
            return ("pad", 0.0, (1.0 - fh) / 2.0, 1.0, (1.0 + fh) / 2.0)
        fw = src_ar / out_ar            # bars left/right
        return ("pad", (1.0 - fw) / 2.0, 0.0, (1.0 + fw) / 2.0, 1.0)
    # cover fit: the shown rect keeps the output aspect and fills one axis
    if src_ar >= out_ar:                # sides cropped
        fw = out_ar / src_ar
        fx = 0.5 if not focus or focus[0] is None else float(focus[0])
        x0 = min(max(fx - fw / 2.0, 0.0), 1.0 - fw)
        return ("crop", x0, 0.0, x0 + fw, 1.0)
    fh = src_ar / out_ar                # top/bottom cropped
    fy = 0.5 if not focus or focus[1] is None else float(focus[1])
    y0 = min(max(fy - fh / 2.0, 0.0), 1.0 - fh)
    return ("crop", 0.0, y0, 1.0, y0 + fh)


def _atempo_chain(rate):
    """atempo stage(s) for an insert's rate — one stage covers [0.5, 4];
    below 0.5 two stages chain (atempo's own floor is 0.5). Pitch-corrected
    by the filter itself."""
    if rate >= 0.5:
        return f"atempo={rate:.4f}"
    return f"atempo=0.5,atempo={rate / 0.5:.4f}"


def _clip01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _path_value_at(pts, key, t, a, b, default, ease):
    """Python mirror of travel.path_value_expr — keyframe interpolation as a
    running sum of eased deltas, matching the emitted expression term for
    term (including its skip of near-zero segments and deltas)."""
    v = float(pts[0].get(key, default))
    for p0, p1 in zip(pts, pts[1:]):
        t0 = a + float(p0["f"]) * (b - a)
        t1 = a + float(p1["f"]) * (b - a)
        dt = t1 - t0
        if dt <= 1e-4:
            continue
        dv = float(p1.get(key, default)) - float(p0.get(key, default))
        if abs(dv) < 1e-4:
            continue
        v += dv * travel.ease_value((t - t0) / dt, ease)
    return v


def zoom_state_at(zooms, t, out_duration):
    """(z, cx, cy) of the shared zoompan at output second t — the python
    mirror of the term emission below (punch/ease/push_in/pull_out plus the
    travelling follow/path shapes), so a caller can know the zoomed viewport
    without rendering. Round 72: look_at(output_times) crops frames through
    this, which is what lets the agent SEE a zoom's framing before a render
    instead of discovering it from the user's complaint.

    Only the zooms list is evaluated — a screen takeover's push and an
    aspect shift's compensation ride the same zoompan at render time but are
    their own subsystems; callers overlapping those windows say so instead
    of approximating them. The viewport at (z, cx, cy) is
    x0=(1-1/z)*cx, width 1/z (zoompan's own clamp is mirrored by clamping
    cx/cy to 0-1)."""
    z = 1.0
    cxo = 0.0
    cyo = 0.0
    for zm in zooms or []:
        a = max(0.0, float(zm["start"]))
        b = min(float(out_duration), float(zm["end"]))
        if b - a < 0.05:
            continue
        st = float(zm.get("strength", 0.25))
        mode = zm.get("mode") or "punch"
        inside = a <= t <= b            # between() is inclusive
        if mode in ("follow", "path"):
            pts = travel.path_points(zm)
            if len(pts) < 2:
                continue
            ease = zm.get("ease")
            if mode == "follow":
                r = max(0.15, min(0.4, (b - a) / 4.0))
                z += st * _clip01((t - a) / r) * _clip01((b - t) / r)
            elif inside:
                z += _path_value_at(pts, "s", t, a, b, st, ease)
            if inside:
                cxo += _path_value_at(pts, "cx", t, a, b, 0.5, ease) - 0.5
                cyo += _path_value_at(pts, "cy", t, a, b, 0.5, ease) - 0.5
            continue
        if mode == "ease":
            r = max(0.15, min(0.4, (b - a) / 4.0))
            z += st * _clip01((t - a) / r) * _clip01((b - t) / r)
        elif mode == "push_in":
            if inside:
                z += st * (t - a) / (b - a)
        elif mode == "pull_out":
            if inside:
                z += st * (1.0 - (t - a) / (b - a))
        elif inside:                    # punch
            z += st
        if inside:
            cx = zm.get("cx")
            cy = zm.get("cy")
            if cx is not None and abs(float(cx) - 0.5) > 1e-6:
                cxo += float(cx) - 0.5
            if cy is not None and abs(float(cy) - 0.5) > 1e-6:
                cyo += float(cy) - 0.5
    return z, _clip01(0.5 + cxo), _clip01(0.5 + cyo)


def _region_parts(parts, in_label, out_label, regions, sw, sh,
                  seg_start, seg_dur, uid):
    """Censor-region chain for ONE source segment, in SOURCE-frame pixels
    (the coordinate space the agent measures with look_at). Windowed regions
    are mapped from program time to segment-local time; regions whose window
    misses this segment entirely are skipped. Always ends on out_label."""
    todo = []
    for rg in regions:
        if rg.get("start") is not None and rg.get("end") is not None:
            a = max(0.0, float(rg["start"]) - seg_start)
            b = min(seg_dur, float(rg["end"]) - seg_start)
            if b - a < 0.02:
                continue
            win = None if (a <= 0.01 and b >= seg_dur - 0.01) else (a, b)
        else:
            win = None
        todo.append((rg, win))
    if not todo:
        parts.append(f"[{in_label}]null[{out_label}]")
        return
    cur = in_label
    for k, (rg, win) in enumerate(todo):
        rx = min(int(round(float(rg["x"]) * sw)), sw - 4)
        ry = min(int(round(float(rg["y"]) * sh)), sh - 4)
        rw = min(max(4, int(round(float(rg["w"]) * sw))), sw - rx)
        rh = min(max(4, int(round(float(rg["h"]) * sh))), sh - ry)
        enable = (f":enable='between(t,{win[0]:.2f},{win[1]:.2f})'"
                  if win else "")
        last = out_label if k == len(todo) - 1 else f"rgc{uid}_{k}"
        if rg.get("mode") == "black":
            parts.append(f"[{cur}]drawbox=x={rx}:y={ry}:w={rw}:h={rh}:"
                         f"color=black:t=fill{enable}[{last}]")
        else:
            if rg.get("mode") == "pixelate":
                pf = max(2, min(rw, rh) // 8)
                obscure = (f"scale={max(2, rw // pf)}:{max(2, rh // pf)},"
                           f"scale={rw}:{rh}:flags=neighbor")
            else:                       # blur
                # gblur, not boxblur: boxblur's radius must stay under the
                # CHROMA plane's min(w,h)/2 (half the pixel dims on
                # yuv420p), which small regions violate; gblur has no such
                # constraint
                sigma = max(3, min(min(rw, rh) // 6, 30))
                obscure = f"gblur=sigma={sigma}:steps=2"
            parts.append(f"[{cur}]split[rgA{uid}_{k}][rgB{uid}_{k}]")
            parts.append(f"[rgA{uid}_{k}]crop={rw}:{rh}:{rx}:{ry},"
                         f"{obscure}[rgF{uid}_{k}]")
            parts.append(f"[rgB{uid}_{k}][rgF{uid}_{k}]overlay={rx}:{ry}"
                         f"{enable}[{last}]")
        cur = last


def _speech_spans_out(index, tl):
    spans = []
    for sent in index.get("sentences", []):
        spans.extend(tl.span_to_out(sent["t0"], sent["t1"]))
    gap = 0.3
    merged = merge_spans(spans, gap)
    while len(merged) > MAX_ENABLE_SPANS:
        gap *= 2
        merged = merge_spans(merged, gap)
    return merged


ENDCARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "brand", "endcard.png")
# The mark itself: the same white robot the end card and the site navbar use,
# so the corner watermark and the brand are one character.
ROBOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "brand", "robot.png")


def _screen_frame_input(edl, workdir, W, H, fps, out_dur, extra_inputs,
                        next_idx):
    """Build the floating-window plate and append it as a looped still input.

    Returns (input_index, (pw, ph, ox, oy)) or (None, None). A plate that
    cannot be drawn degrades to NO framing rather than failing the render —
    the same contract endcard_path() documents: the user loses a cosmetic
    treatment, not their export.
    """
    sf = ((edl.get("effects") or {}).get("screen_frame")) or None
    if not sf or not W or not H:
        return None, None
    path = screenframe.plate_path(workdir, W, H, sf)
    try:
        if not os.path.exists(path):
            screenframe.build_plate(path, W, H, sf)
        box = screenframe.picture_box(W, H, float(sf.get("inset", 0.08)))
    except Exception as e:
        print(f"[render] screen_frame plate could not be built "
              f"({str(e)[:160]}) — rendering without it", flush=True)
        return None, None
    extra_inputs += ["-loop", "1", "-t", f"{out_dur + 5.0:.3f}",
                     "-r", f"{fps:.3f}", "-i", path]
    return next_idx, box


def robot_path():
    """The bundled robot, or None. Same contract as endcard_path(): a missing
    brand asset degrades the render rather than failing it."""
    return ROBOT_PATH if os.path.exists(ROBOT_PATH) else None


def endcard_path():
    """The bundled end-card image, or None if this image does not carry one.

    Returning None (rather than raising) is deliberate. A missing brand asset
    means a broken build, and the two ways to react are: ship exports without
    branding, or ship no exports at all. Failing every export takes the product
    down over a cosmetic asset, and main.py's failure notes would tell the user
    to "press Download to try again" — advice that could not possibly help,
    which that module's own rule forbids. So the render proceeds and the
    caller logs loudly instead; the miss is visible in logs and in the job
    result, not buried in a customer's confusing failure.
    """
    return ENDCARD_PATH if os.path.exists(ENDCARD_PATH) else None


def clean_source_key(edl_json, variant, src_sha=None):
    """Round 39 — the REPAINTED source this EDL renders from, or None.

    When the agent erased burned-in text or an object, the pixels were fixed in
    a copy of the source and the EDL points at it: full-res for the final, the
    repainted 540p proxy for the preview, so the preview the user approves is
    the one that ships. A preview with no repainted proxy falls back to the
    full-res clean rather than to the ORIGINAL — slower, but showing the user
    the text they just watched disappear would be a lie the reply would not
    catch. Nothing else about rendering changes: the cleaned file has the same
    duration, rate and audio, so every EDL and index timestamp still lands on
    the same frame.
    """
    clean = (edl_json or {}).get("source_clean") or {}
    key = clean.get("asset_key")
    if not key:
        return None
    # An EDL outlives a video REPLACEMENT (uploading a new file keeps the
    # project's edit), so a repaint of the old upload would otherwise render
    # the old footage entirely — the user swaps their video and watches the
    # previous one come back. The fingerprint ties the cleaned file to the
    # source sha it was made from: when it no longer matches, this project's
    # current video was simply never cleaned, so render it as it is.
    if src_sha and clean.get("fp") and clean["fp"] != clean_fingerprint(
            src_sha, clean.get("regions") or []):
        print(f"[render] ignoring cleaned source {key}: it is a repaint of a "
              "different upload (the video was replaced)", flush=True)
        return None
    if variant == "preview":
        return clean.get("proxy_key") or key
    return key


def outro_seconds(preview):
    """How much time the end card adds to a render of this variant.

    The single source of truth for the outro's length. Everything that must
    agree — the duration check, the progress estimate, the result-sheet
    sampling window, the filtergraph — reads it from here, because these have
    to move together or the render fails verification.
    """
    if preview and not config.OUTRO_ON_PREVIEW:
        return 0.0
    return config.OUTRO_DURATION_S if endcard_path() else 0.0


def outro_current(meta, variant):
    """Does this cached render carry the end card this variant should have?

    An ABSENT stamp means 0 — "no card baked in" — not "unknown". That
    distinction is the whole grandfathering rule: a pre-card FINAL (wants 1)
    busts and re-encodes, while a pre-card PREVIEW (wants 0) still matches and
    is served. Treating absent as unknown would re-render every cached preview
    on the platform for nothing, which on a ~1 vCPU box is exactly how real
    customers got starved before.

    A module-level function, not an inline comparison, so the rule is
    testable — the same reason sfx_source/music_source exist.
    """
    want = config.OUTRO_VERSION if outro_seconds(variant == "preview") else 0
    return ((meta or {}).get("outro_v") or 0) == want


def edl_has_shaped_text(edl):
    """Does this EDL burn any text in a script that needs complex shaping?"""
    return any(graphics.needs_shaping(t.get("text") or "")
               for t in ((edl or {}).get("texts") or []))


def shaping_current(meta, edl):
    """Does this cached render predate the complex-script text fix?

    Only EDLs that actually contain a shaped script are ever busted. For
    everything else this is unconditionally True, so Latin renders keep their
    cache — same reasoning as outro_current's grandfathering, and the same
    reason it is a named function rather than an inline comparison.
    """
    if not edl_has_shaped_text(edl):
        return True
    return ((meta or {}).get("gfx_shape_v") or 0) == config.GFX_SHAPING_VERSION


def music_tail_ext(edl, out_duration):
    """Round 79j — seconds of BLACK the program extends by, so unmuted music
    past the last scene plays to its end instead of being cut off with the
    picture. 0 for every timeline whose music fits inside the video, which
    keeps all of those renders byte-identical."""
    ends = [float(m.get("end") or 0.0) for m in (edl or {}).get("music") or []
            if not m.get("mute")]
    end = max(ends) if ends else 0.0
    return max(0.0, min(end, out_duration + 3600.0) - out_duration)


def music_tail_current(meta, edl, out_duration):
    """Does this cached render predate the music-tail extension?

    Same grandfathering discipline as transitions_current: only EDLs whose
    music actually outlives the program are ever busted."""
    if music_tail_ext(edl, out_duration) <= 0.05:
        return True
    return ((meta or {}).get("tail_v") or 0) == config.MUSIC_TAIL_VERSION


def transitions_current(meta, edl):
    """Does this cached render predate scene-scoped transitions?

    Renders are cached by (project, variant, EDL VERSION), not by content, so
    a render-pipeline change is invisible to the cache — the same problem the
    end card had. Every render made before round 48 put a junction effect on
    EVERY cut, which on a silence-cut talking head is a full-screen effect
    every couple of seconds through one continuous shot. A real customer's
    preview is sitting in that cache right now; leaving it there means she
    keeps being served the broken video no matter what she does next.

    Only EDLs that actually carry a transition are ever busted — everything
    else keeps its cache, same grandfathering discipline as shaping_current
    and outro_current, and the same reason this is a named function rather
    than an inline comparison.
    """
    if not ((edl or {}).get("effects") or {}).get("transition"):
        return True
    return ((meta or {}).get("trans_v") or 0) == config.TRANSITION_VERSION


def watermark_font_path():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fonts", "PlusJakartaSans-ExtraBold.ttf")
    return p if os.path.exists(p) else None


def wants_watermark(variant, is_paid, settings=None):
    """Should THIS render carry the free-tier mark?

    Finals only (a preview is a working artefact, not the thing the user
    keeps), and only when the user has no paid plan. Also requires both the
    robot and the wordmark font to be present: half a watermark — a robot
    with no text, or text in a fallback font that is not the site's — would
    be worse than none, so a missing asset means no mark at all rather than
    a degraded one.

    `settings` are the admin panel's live toggles (db.video_settings):
      enabled — master switch, off means nobody is marked.
      force   — mark EVERY final, paid included. This exists because the
                owner of the product is on a paid plan and therefore cannot
                see the feature on their own exports; without it the only
                way to check the mark is to register a throwaway account.
                It overrides the plan check, NOT the master switch.
    When settings is None the config default stands, so a worker that cannot
    reach the table still behaves sensibly.
    """
    s = settings or {"enabled": config.WATERMARK_ENABLED, "force": False}
    if variant != "final" or not s.get("enabled"):
        return False
    if is_paid and not s.get("force"):
        return False
    return bool(robot_path() and watermark_font_path())


def watermark_version(variant, is_paid, settings=None):
    """The `wm_v` stamp for a render: the version burned in, or 0 for none.
    0 is a real answer ("this file carries no mark"), never "unknown"."""
    return (config.WATERMARK_VERSION
            if wants_watermark(variant, is_paid, settings) else 0)


def watermark_current(meta, variant, is_paid, settings=None):
    """Does this cached render carry the mark THIS user should have now?

    The upgrade path is the whole point. A free user exports (wm_v=1), pays,
    and downloads again: without this the cached marked file is served
    forever and they are still looking at the watermark they just paid to
    remove. It busts downward too — a lapsed subscriber's clean export
    re-encodes with the mark.

    An ABSENT stamp means 0, matching outro_current: renders predating the
    feature carry no mark, which is correct for a paid user and stale for a
    free one, so only free users re-encode.
    """
    return ((meta or {}).get("wm_v") or 0) == watermark_version(
        variant, is_paid, settings)


def watermark_geometry(W, H):
    """Pixel geometry of the mark for an output frame: the robot's box, and
    where the text sits relative to it. One function so the ffmpeg overlay
    and the ASS text agree on where the robot ends and the words begin —
    computing them separately is how the two drift into overlapping.

    The corner offset is per-axis (v3): Instagram's full-screen "cover" zoom
    crops ~9% off each side of a 9:16 reel and overlays its header on the
    top strip, so x clears the side-crop (10% of the width on vertical) and
    y drops below the UI band (6% of the height) — still reading as the
    top-left corner, no longer under the knife."""
    rh = max(24, _even(H * config.WATERMARK_ROBOT_H_FRAC))
    rw = max(16, _even(rh * config.WATERMARK_ROBOT_ASPECT))
    margin_x = max(10, int(round(min(W, H)
                                 * config.WATERMARK_MARGIN_X_FRAC)))
    margin_y = max(10, int(round(H * config.WATERMARK_MARGIN_Y_FRAC)))
    fs = max(9, int(round(H * config.WATERMARK_TEXT_H_FRAC)))
    gap = max(6, int(round(fs * 0.72)))
    slide = max(4, int(round(W * config.WATERMARK_SLIDE_FRAC)))
    return {"rw": rw, "rh": rh, "margin_x": margin_x, "margin_y": margin_y,
            "fontsize": fs,
            # tucked against the robot, then slid clear
            "x_in": margin_x + rw + gap,
            "x_out": margin_x + rw + gap + slide,
            "y": margin_y + max(0, (rh - fs) // 2)}


def build_watermark_ass(path, out_duration_s, W, H):
    g = watermark_geometry(W, H)
    return graphics.build_watermark_ass(
        path, out_duration_s, (W, H), config.WATERMARK_TEXT,
        g["x_in"], g["x_out"], g["y"], g["fontsize"],
        config.WATERMARK_FONT_NAME, config.WATERMARK_PERIOD_S,
        config.WATERMARK_SHOW_S, config.WATERMARK_FADE_S)


def _watermark_parts(vlabel, out_label, robot_idx, wm_ass_path, W, H):
    """Filtergraph for the corner mark: the robot pinned top-left, then the
    wordmark burned over it from its own .ass layer.

    Applied to the PROGRAM stream before the end card is concatenated, so the
    mark never lands on the card (which is already branded).

    The robot is an overlay and the text is libass — not one mechanism for
    both — because a PNG needs no shaping and a string does. libass is the
    pipeline's proven text burner (captions and graphics both ride it) and
    gives \\move/\\fad for free; drawtext would need ffmpeg built with
    libfreetype, which is not guaranteed.
    """
    g = watermark_geometry(W, H)
    parts = [f"[{robot_idx}:v]scale={g['rw']}:{g['rh']}[wmbot]"]
    tail = out_label if not wm_ass_path else "wmv"
    # shortest=1 is load-bearing: the robot is a LOOPED still, so with
    # overlay's default shortest=0 ("do not end when the shortest input
    # ends") the filter keeps emitting frames forever after the programme
    # finishes and ffmpeg encodes into the void. That is not theoretical --
    # it hung a real export at 89% for 8 minutes with a healthy heartbeat,
    # which looks exactly like "slow" and never ends. The main stream drives
    # the length; the input is ALSO bounded with -t as a second line of
    # defence, generously, so it can never be the shorter one and truncate.
    parts.append(f"[{vlabel}][wmbot]overlay={g['margin_x']}:{g['margin_y']}:"
                 f"format=auto:shortest=1[{tail}]")
    if wm_ass_path:
        parts.append(f"[wmv]subtitles=filename='{wm_ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[{out_label}]")
    return parts


def sfx_source(key, fetch):
    """Local path for an sfx item's audio — every key is a bucket object.

    The bundled `sfx:` scheme is gone: its 18 sounds were copied to R2
    under legacy-sfx/ and every EDL row rewritten to those plain storage
    keys (2026-08-08), so this is the same one-door wiring as
    music_source."""
    return fetch(key)


def music_source(key, fetch):
    """Local path for a music item's audio — every key is a bucket object.

    The bundled `library:` scheme is gone: its 24 tracks were copied to R2
    under legacy-music/ and every EDL row in the database was rewritten to
    those plain storage keys (2026-08-08), so the render path has exactly
    one kind of music reference again."""
    return fetch(key)


def build_filtergraph(edl, src_dur, has_audio, tl, ass_path,
                      music_inputs, index, preview,
                      W=None, H=None, fps=30.0, frame_mode=None,
                      insert_inputs=None, vo_inputs=None, silence_idx=None,
                      src_w=None, src_h=None, src_pad=0.0,
                      sfx_inputs=None, outro_s=0.0, card_idx=None,
                      stem_inputs=None,
                      src_sar=1.0, src_fps=None,
                      overlay_inputs=None, gfx_ass_path=None,
                      frame_focus=None, robot_idx=None, wm_ass_path=None,
                      plate_idx=None, plate_box=None, behind_inputs=None,
                      patch_inputs=None, cap_burn_offset=None):
    """Input layout: [0] main source video; anullsrc at silence_idx when
    needed (no main audio, image inserts, or silent clip inserts); then one
    input per music item, insert item and voiceover item in EDL order.

    insert_inputs: [(input_idx, item, has_audio)] aligned with the sorted
    EDL inserts (same order as tl.insert_positions()).
    vo_inputs: [(input_idx, item, vo_duration_s)].
    src_pad: seconds of the source whose picture track ran out early (a phone
    screen recording stops writing frames while the screen is static). The last
    frame is held across them, matching what a player shows, what the proxy
    holds and therefore what the user approved in the preview — trimming a keep
    span that lands in there would otherwise yield no picture at all.
    """
    # Round 79j — the SEQUENCE is as long as its content: unmuted music past
    # the last scene extends the render over BLACK to its own end, so a song
    # laid on the workbench simply plays. 0 whenever music fits the video,
    # which keeps every such graph byte-identical.
    tail_ext = music_tail_ext(edl, tl.out_duration)
    total_dur = tl.out_duration + tail_ext
    keep = [(max(0.0, s), min(e, src_dur)) for s, e in edl["keep"]]
    keep = [(s, e) for s, e in keep if e - s > 0.01]
    # focus_track is source-time composition state. A later word-safe cut can
    # legitimately merge two adjacent keep spans back across a camera cut;
    # if rendering then chooses one mode from the merged segment's midpoint,
    # the whole segment inherits either fit or crop. Project 632 had the right
    # EDL ([wide=pad_blur, close-up=crop]) yet rendered the close-up as a tiny
    # inset for exactly this reason. Re-split the *local render blocks* at
    # every focus boundary. This does not alter the EDL, duration or timeline;
    # it only guarantees one normalization mode/aim per ffmpeg block.
    focus_track = ((edl.get("frame") or {}).get("focus_track")
                   if isinstance(edl.get("frame"), dict) else None) or []
    if keep and focus_track:
        raw_focus_edges = set()
        for span in focus_track:
            for key in ("t0", "t1"):
                try:
                    raw_focus_edges.add(float(span[key]))
                except (KeyError, TypeError, ValueError):
                    continue
        # PySceneDetect reports a cut at the PTS of the first new-shot frame,
        # but ffmpeg's second-based trim at that exact decimal admits the
        # preceding frame on real CFR sources (project 642: trim start 5.480
        # cropped frame 136 from the WIDE shot before frame 137's close-up).
        # Move only INTERNAL composition handoffs one source/output frame
        # forward. Keeping both local blocks' shared edge shifted preserves
        # duration while making the old composition own the last old-shot
        # frame and the new composition own the first new-shot frame. Do not
        # shift the track's outer bounds: that would invent 40ms blocks at the
        # beginning/end of every video.
        ordered_focus_edges = sorted(raw_focus_edges)
        frame_step = 1.0 / max(float(fps), 1.0)
        focus_edges = {
            edge + frame_step for edge in ordered_focus_edges[1:-1]}
        split_keep = []
        for s, e in keep:
            edges = [s] + sorted(
                x for x in focus_edges if s + 0.01 < x < e - 0.01) + [e]
            split_keep.extend((a, b) for a, b in zip(edges, edges[1:])
                              if b - a > 0.01)
        keep = split_keep
    # A canvas program (image/clip-only, no main video) has no keep segments and
    # no input [0]: its program is the inserts alone, concatenated on the canvas.
    canvas_prog = not (edl.get("keep") or []) and bool(edl.get("canvas"))
    if not keep and not canvas_prog:
        raise EDLValidationError("All keep segments fall outside the video.")
    insert_inputs = insert_inputs or []
    vo_inputs = vo_inputs or []
    n = len(keep)
    parts = []

    if n > 0:
        if stem_inputs:
            # Round 97: the source audio IS the premixed stems. Everything
            # downstream — volume automation, per-segment trims, ducking,
            # music, loudnorm — reads [asrc] exactly as before, so the split
            # changes the source of truth and nothing else.
            svi, sai, vdb, mdb = stem_inputs
            parts.append(f"[{svi}:a]volume={vdb}dB[stv]")
            parts.append(f"[{sai}:a]volume={mdb}dB[stm]")
            parts.append("[stv][stm]amix=inputs=2:duration=longest:"
                         "normalize=0[stemsrc]")
            asrc = "stemsrc"
        else:
            asrc = "0:a" if has_audio else f"{silence_idx}:a"
        # Source-time volume automation runs before trimming, so between(t,a,b)
        # windows are in source seconds — exactly what the agent wrote.
        vol_filters = "".join(
            f",volume={v['gain_db']}dB:enable='between(t,{v['start']:.2f},{v['end']:.2f})'"
            for v in edl.get("volume", []))
        parts.append(f"[{asrc}]anull{vol_filters}[asrc]")

    # anullsrc slices for silent blocks (image inserts / clips without audio)
    n_silent_blocks = sum(1 for _idx, _it, hs in insert_inputs if not hs)
    if n_silent_blocks:
        if n_silent_blocks == 1:
            parts.append(f"[{silence_idx}:a]anull[sil0]")
        else:
            parts.append(f"[{silence_idx}:a]asplit={n_silent_blocks}"
                         + "".join(f"[sil{i}]" for i in range(n_silent_blocks)))

    # A plain single-source cut needs no per-segment normalization (the old,
    # cheap graph). The moment a frame is set, foreign material is spliced
    # in, or a zoom needs exact CFR WxH frames, EVERY block must land on
    # identical dims/fps/audio before concat. Round 35 widens the list:
    # speed pieces need CFR so their concat is seamless; overlays compute
    # their geometry from exact WxH; the whip/zoom_punch/glitch junctions
    # run per-block zoompan/overlay math that assumes CFR WxH; shake is a
    # zoompan.
    fx = edl.get("effects") or {}
    zooms = fx.get("zooms") or []
    regions = fx.get("regions") or []
    speed = edl.get("speed") or []
    stylize = fx.get("stylize") or []
    custom_filters = fx.get("custom") or []
    grade_custom = fx.get("grade_custom") or {}
    overlay_inputs = overlay_inputs or []
    # A screen takeover is an overlay that carries a ScreenLock. It leaves the
    # ordinary PIP loop entirely: it needs the zoom stage to have already run
    # (its corner path is expressed in POST-push screen space) and it must not
    # pick up the PIP loop's static scale/position, which the pin overrides.
    takeovers = [(i, it) for i, it in overlay_inputs if it.get("screen")]
    overlay_inputs = [(i, it) for i, it in overlay_inputs
                      if not it.get("screen")]
    master = edl.get("master") or {}
    transition = fx.get("transition") or None
    tstyle = (transition or {}).get("style")
    shifts = fx.get("frame_shifts") or []
    screen_frame = fx.get("screen_frame") or None
    # An aspect shift pushes the picture in via the same zoompan the zooms use,
    # and the floating frame scales the finished picture to an exact WxH box —
    # both need the CFR WxH guarantee every other geometry effect needs.
    # A behind-subject composite is per-pixel geometry against a mask measured
    # at exact WxH, so it needs the same CFR WxH guarantee everything else here
    # needs. Without this a plain single-source cut would take the cheap graph
    # and alphamerge a WxH mask onto frames of some other size.
    do_norm = (bool(insert_inputs) or frame_mode is not None or bool(zooms)
               or bool(speed) or bool(overlay_inputs) or bool(takeovers)
               or tstyle in ("whip_left", "whip_right", "zoom_punch")
               or bool(shifts) or screen_frame is not None
               or bool(behind_inputs)
               or any(s.get("kind") == "shake" for s in stylize))
    mode = frame_mode or "crop"

    # Censor regions are burned into each SOURCE segment BEFORE any
    # reframe/normalization: their fractions are of the SOURCE frame
    # (exactly what look_at showed the agent), a later crop/pad moves the
    # censored footage as one, and inserted material is never censored.
    # Pieces per segment: with no speed spans every segment is one piece at
    # factor 1 and the classic emission below runs untouched. seg_out_len is
    # the segment's PROGRAM length (speed-remapped) — the number every block
    # duration and program-time accumulation must use.
    seg_pcs = [speed_pieces(s, e, speed) for s, e in keep]
    seg_out_len = [sum((pe - ps) / f for ps, pe, f in pcs)
                   for pcs in seg_pcs]

    sw = sh = None
    seg_prog = []
    if regions:
        sw, sh = int(src_w or W), int(src_h or H)
        # program-time start of every keep segment (inserts included), for
        # mapping windowed regions into segment-local time — mirrors the
        # block-order loop below. Uses the SPED segment lengths: a windowed
        # region's program times only line up when the accumulation matches
        # what the viewer's clock does.
        _at = [tl.ins[j][0] for j in range(len(insert_inputs))]
        _pre = _prog = 0.0
        _j = 0
        for i, (s, e) in enumerate(keep):
            while _j < len(_at) and _at[_j] <= _pre + 1e-6:
                _prog += float(insert_inputs[_j][1]["duration_s"])
                _j += 1
            seg_prog.append(_prog)
            _pre += seg_out_len[i]
            _prog += seg_out_len[i]

    def _seg_video(i, in_label, s, e):
        vlab = f"segv{i}" if do_norm else f"v_seg{i}"
        if regions:
            parts.append(f"[{in_label}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[segraw{i}]")
            _region_parts(parts, f"segraw{i}", vlab, regions, sw, sh,
                          seg_prog[i], e - s, f"s{i}")
        else:
            parts.append(f"[{in_label}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[{vlab}]")

    def _seg_pieces_video_audio(i, v_in, a_in, s, e):
        """Speed path: one keep segment -> constant-rate pieces, each
        trimmed, censored (windows mapped into the piece's own pre-speed
        clock), retimed with setpts/atempo, then concatenated back into
        [segv{i}]/[a_seg{i}]. Only reached when the EDL carries speed spans
        (do_norm is forced on, so segv{i} is normalized right after)."""
        pcs = seg_pcs[i]
        k = len(pcs)
        if k > 1:
            parts.append(f"[{v_in}]split={k}"
                         + "".join(f"[pv{i}_{j}]" for j in range(k)))
            parts.append(f"[{a_in}]asplit={k}"
                         + "".join(f"[pa{i}_{j}]" for j in range(k)))
        else:
            parts.append(f"[{v_in}]null[pv{i}_0]")
            parts.append(f"[{a_in}]anull[pa{i}_0]")
        p_acc = 0.0        # sped seconds consumed within this segment so far
        for j, (ps, pe, f) in enumerate(pcs):
            spts = "" if abs(f - 1.0) < 1e-9 else f"/{f:.4f}"
            if regions:
                # Region windows arrive in PROGRAM time; inside a sped piece
                # the local pre-speed clock runs `f` times program speed.
                # The piece's program start accumulates from seg_prog — the
                # same insert-aware walk the non-speed path uses — NEVER via
                # tl.src_to_out(ps): at a contiguous keep boundary (the
                # mid-take-insert split shape) src_to_out first-matches the
                # EARLIER segment and returns a time missing the insert's
                # duration, landing the censor window on the wrong footage.
                p0 = seg_prog[i] + p_acc
                p1 = p0 + (pe - ps) / f
                local_rgs = []
                for rg in regions:
                    if rg.get("start") is not None:
                        a = max(float(rg["start"]), p0)
                        b = min(float(rg["end"]), p1)
                        if b - a < 0.02:
                            continue
                        local_rgs.append(dict(rg, start=(a - p0) * f,
                                              end=(b - p0) * f))
                    else:
                        local_rgs.append(dict(rg, start=None, end=None))
                parts.append(f"[pv{i}_{j}]trim=start={ps:.3f}:end={pe:.3f},"
                             f"setpts=PTS-STARTPTS[pvr{i}_{j}]")
                _region_parts(parts, f"pvr{i}_{j}", f"pvt{i}_{j}", local_rgs,
                              sw, sh, 0.0, pe - ps, f"sp{i}_{j}")
                parts.append(f"[pvt{i}_{j}]setpts=PTS{spts}[pvz{i}_{j}]")
            else:
                parts.append(f"[pv{i}_{j}]trim=start={ps:.3f}:end={pe:.3f},"
                             f"setpts=(PTS-STARTPTS){spts}[pvz{i}_{j}]")
            tempo = "" if abs(f - 1.0) < 1e-9 else f",{_atempo_chain(f)}"
            parts.append(f"[pa{i}_{j}]atrim=start={ps:.3f}:end={pe:.3f},"
                         f"asetpts=PTS-STARTPTS{tempo},{AUDIO_NORM}"
                         f"[paz{i}_{j}]")
            p_acc += (pe - ps) / f
        if k == 1:
            parts.append(f"[pvz{i}_0]null[segv{i}]")
            parts.append(f"[paz{i}_0]anull[a_seg{i}]")
        else:
            pairs = "".join(f"[pvz{i}_{j}][paz{i}_{j}]" for j in range(k))
            parts.append(f"{pairs}concat=n={k}:v=1:a=1[segv{i}][a_seg{i}]")

    # main segments: trim (+ censor regions), then (when needed) normalize
    # to the output frame. Skipped entirely for a canvas program (no [0]).
    if n >= 1:
        vsrc = "0:v"
        if src_pad > 0:
            parts.append(f"[0:v]tpad=stop_mode=clone:"
                         f"stop_duration={src_pad:.3f}[vpad]")
            vsrc = "vpad"
        # Repainted windows (round 92): each patch clip replaces its span of
        # the SOURCE stream, before any trim, speed, zoom or grade — so every
        # downstream stage reads the repainted pixels, exactly as it read a
        # cleaned source. setpts shifts the clip onto the source clock;
        # scale2ref pins it to the main stream's exact frame size (whatever
        # SAR/rotation the decoder produced); enable bounds the replacement
        # to the patch's own window and eof_action=pass hands the stream
        # back to the untouched source after the last patch frame.
        for pj, (pidx, pit) in enumerate(patch_inputs or []):
            pps = float(pit["src_start"])
            ppe = float(pit["src_end"])
            parts.append(f"[{pidx}:v]setpts=PTS+{pps:.3f}/TB[ptc{pj}]")
            parts.append(f"[ptc{pj}][{vsrc}]scale2ref[pts{pj}][pref{pj}]")
            parts.append(f"[pref{pj}][pts{pj}]overlay=eof_action=pass"
                         f":enable='between(t,{pps:.3f},{ppe:.3f})'"
                         f"[vptc{pj}]")
            vsrc = f"vptc{pj}"
    if speed and n >= 1:
        # Speed path: every segment needs its own video AND audio tap.
        if n == 1:
            parts.append(f"[{vsrc}]null[vin0]")
            parts.append("[asrc]anull[ain0]")
        else:
            parts.append(f"[{vsrc}]split=" + str(n)
                         + "".join(f"[vin{i}]" for i in range(n)))
            parts.append("[asrc]asplit=" + str(n)
                         + "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            _seg_pieces_video_audio(i, f"vin{i}", f"ain{i}", s, e)
    elif n == 1:
        _seg_video(0, vsrc, keep[0][0], keep[0][1])
        parts.append(f"[asrc]atrim=start={keep[0][0]:.3f}:end={keep[0][1]:.3f},"
                     f"asetpts=PTS-STARTPTS"
                     + (f",{AUDIO_NORM}" if do_norm else "") + "[a_seg0]")
    elif n > 1:
        parts.append(f"[{vsrc}]split=" + str(n)
                     + "".join(f"[vin{i}]" for i in range(n)))
        parts.append("[asrc]asplit=" + str(n)
                     + "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            _seg_video(i, f"vin{i}", s, e)
            parts.append(f"[ain{i}]atrim=start={s:.3f}:end={e:.3f},"
                         f"asetpts=PTS-STARTPTS"
                         + (f",{AUDIO_NORM}" if do_norm else "")
                         + f"[a_seg{i}]")
    if do_norm:
        # focus_track (round 100): a kept segment whose midpoint falls inside
        # a span crops on that span's own aim — how the crop FOLLOWS a subject
        # that sits in different places in different shots (the seeder splits
        # keep segments at shot cuts, so an aim change lands on a cut). No
        # track, or no covering span, keeps the single frame_focus exactly.
        # build_filtergraph splits LOCAL render blocks at focus edges above;
        # the EDL keep list remains the user's real editorial cuts and never
        # gains fake mid-word boundaries merely to express composition.
        _ftrack = focus_track

        def _frame_for(s, e):
            if not _ftrack:
                return frame_focus, mode
            m = (s + e) / 2.0
            for sp in _ftrack:
                try:
                    if float(sp.get("t0", 0)) <= m <= float(sp.get("t1", 0)):
                        fx, fy = sp.get("x"), sp.get("y")
                        span_mode = sp.get("mode") or mode
                        if fx is None and fy is None:
                            return frame_focus, span_mode
                        return ((fx if fx is not None else
                                 (frame_focus[0] if frame_focus else None),
                                 fy if fy is not None else
                                 (frame_focus[1] if frame_focus else None)),
                                span_mode)
                except (TypeError, ValueError):
                    continue
            return frame_focus, mode

        for i in range(n):
            # frame_focus reaches ONLY the main footage: the focus point was
            # measured on the source video, so inserts (below) keep the
            # center crop.
            seg_focus, seg_mode = _frame_for(*keep[i])
            _normalize_video(parts, f"segv{i}", f"v_seg{i}", W, H, fps,
                             seg_mode, f"s{i}", focus=seg_focus,
                             seg_dur=seg_out_len[i])

    # insert blocks: trim to their window (source_start_s picks where in
    # the clip the window starts), normalize like everything else
    sil_i = 0
    for j, (idx, item, ins_audio) in enumerate(insert_inputs):
        dur = float(item["duration_s"])
        off = float(item.get("source_start_s") or 0.0) \
            if item["kind"] != "image" else 0.0
        # rate (round 76): the spliced clip plays FASTER in place — the
        # block still occupies duration_s of the program, but consumes
        # duration_s*rate of source. rate None/1.0 emits the exact legacy
        # strings (tests pin whole filtergraphs, and cached renders match
        # by EDL signature that _sig_canon keeps stable for None).
        rate = float(item.get("rate") or 1.0) if item["kind"] != "image" \
            else 1.0
        if abs(rate - 1.0) > 1e-6:
            parts.append(
                f"[{idx}:v]trim=start={off:.3f}:end={off + dur * rate:.3f},"
                f"setpts=(PTS-STARTPTS)/{rate:.4f}[insv{j}]")
        else:
            parts.append(f"[{idx}:v]trim=start={off:.3f}:end={off + dur:.3f},"
                         f"setpts=PTS-STARTPTS[insv{j}]")
        # crop (round 77): the scene IS one region of the clip — cut it out
        # before normalizing, and normalize with 'pad' so the strip lands
        # letterboxed at full width instead of being cover-cropped back to
        # the canvas (which would undo the crop). trunc(.../2)*2 keeps the
        # dims legal for yuv420p. crop None emits the exact legacy chain.
        # fit (round 79): a per-insert override of the program frame mode —
        # 'pad' shows the whole picture letterboxed instead of cover-cropping
        # it to the canvas. None emits the exact legacy chain.
        crop = item.get("crop")
        ins_in, imode = f"insv{j}", (item.get("fit") or mode)
        if crop:
            cx0, cy0, cx1, cy1 = (float(c) for c in crop)
            parts.append(
                f"[insv{j}]crop=trunc(iw*{cx1 - cx0:.4f}/2)*2"
                f":trunc(ih*{cy1 - cy0:.4f}/2)*2"
                f":trunc(iw*{cx0:.4f}/2)*2"
                f":trunc(ih*{cy0:.4f}/2)*2[insvc{j}]")
            ins_in, imode = f"insvc{j}", "pad"
        # Ken Burns motion on image inserts: a per-block zoompan that
        # drifts across the still instead of freezing it.
        motion = item.get("motion") if item["kind"] == "image" else None
        norm_out = f"v_insn{j}" if motion else f"v_ins{j}"
        _normalize_video(parts, ins_in, norm_out, W, H, fps,
                         imode, f"i{j}", seg_dur=dur)
        if motion:
            nframes = max(1, int(round(dur * fps)))
            prog = f"(on/{nframes})"
            cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            if motion == "zoom_in":
                z, x, y = f"1+0.25*{prog}", cx, cy
            elif motion == "zoom_out":
                z, x, y = f"1.25-0.25*{prog}", cx, cy
            elif motion == "pan_left":
                z, x, y = "1.15", f"(iw-iw/zoom)*(1-{prog})", cy
            else:                       # pan_right
                z, x, y = "1.15", f"(iw-iw/zoom)*{prog}", cy
            parts.append(f"[{norm_out}]zoompan=z='{z}':x='{x}':y='{y}'"
                         f":d=1:s={W}x{H}:fps={fps:.3f}[v_ins{j}]")
        if ins_audio:
            if abs(rate - 1.0) > 1e-6:
                parts.append(
                    f"[{idx}:a]atrim=start={off:.3f}"
                    f":end={off + dur * rate:.3f},"
                    f"asetpts=PTS-STARTPTS,{_atempo_chain(rate)},"
                    f"{AUDIO_NORM},apad=whole_dur={dur:.3f}[a_ins{j}]")
            else:
                parts.append(f"[{idx}:a]atrim=start={off:.3f}"
                             f":end={off + dur:.3f},"
                             f"asetpts=PTS-STARTPTS,{AUDIO_NORM},"
                             f"apad=whole_dur={dur:.3f}[a_ins{j}]")
        else:
            parts.append(f"[sil{sil_i}]atrim=start=0:end={dur:.3f},"
                         f"asetpts=PTS-STARTPTS,{AUDIO_NORM}[a_ins{j}]")
            sil_i += 1

    # program order: inserts splice at their keep boundary, before the
    # segment that starts there (mirrors Timeline)
    blocks, ins_j, pre = [], 0, 0.0
    at_list = [tl.ins[j][0] for j in range(len(insert_inputs))]
    for i, (s, e) in enumerate(keep):
        while ins_j < len(insert_inputs) and at_list[ins_j] <= pre + 1e-6:
            blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}",
                           float(insert_inputs[ins_j][1]["duration_s"])))
            ins_j += 1
        # seg_out_len, not e - s: a sped segment's block duration is its
        # REMAPPED length (identical to e - s when no speed spans exist).
        blocks.append((f"v_seg{i}", f"a_seg{i}", seg_out_len[i]))
        pre += seg_out_len[i]
    while ins_j < len(insert_inputs):
        blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}",
                       float(insert_inputs[ins_j][1]["duration_s"])))
        ins_j += 1

    # Transitions: a junction effect at every cut/insert boundary, chosen
    # from TRANSITION_STYLES. Every style is duration-preserving by
    # construction — each block animates within its own footage (video only;
    # audio concat is untouched), so no timeline math anywhere changes.
    # Styles that manufacture geometry (whip/zoom_punch) forced do_norm
    # above, so W/H/fps here are the real per-block dimensions.
    trans_post = None    # (style, tdur) applied ONCE after concat, not per block
    # WHICH junctions get one. Not all of them, by default — after cut_silences
    # nearly every junction is a jump cut inside one continuous shot, and a
    # full-screen effect every couple of seconds through footage that never
    # changed scene is what a real user shipped and called broken. The set is
    # resolved by timeline.transition_junctions so this and set_transitions'
    # own count can never disagree; scope 'every_cut' returns all of them.
    junctions = (transition_junctions(edl, index, n_blocks=len(blocks))
                 if transition and len(blocks) > 1 else set())
    if transition and len(blocks) > 1 and not junctions:
        transition = None       # nothing qualified — emit clean hard cuts
    if transition and len(blocks) > 1:
        tdur = float(transition.get("duration_s") or 0.3)
        style = transition.get("style") or "dip_black"
        nb = len(blocks)
        if style in ("whip_left", "whip_right", "zoom_punch"):
            # Geometry-manufacturing styles run as ONE post-concat instance
            # (below): per-block emission put a full-resolution overlay/
            # color/zoompan chain in the graph for EVERY block, so graph
            # size and filter frame queues scaled with the cut count on the
            # OOM-prone 1-vCPU worker. dip/flash/glitch stay per block —
            # fade/eq/rgbashift are cheap and enable-gated.
            trans_post = (style, tdur)
        else:
            faded = []
            for k, (vlab, alab, bd) in enumerate(blocks):
                td = min(tdur, max(0.0, bd / 2 - 0.05))
                # `first`/`last` have always meant "no incoming edge" / "no
                # outgoing edge" — they were just spelled as the ends of the
                # timeline because every junction qualified. Now a junction
                # only exists where transition_junctions says so, and the two
                # ends fall out for free (junction -1 and junction nb-1 are
                # never in the set).
                first = (k - 1) not in junctions
                last = k not in junctions
                if td < 0.05 or (first and last):
                    faded.append((vlab, alab, bd))
                    continue
                out_lab = f"vtr{k}"
                # edge windows this block participates in (an interior block
                # has both: an incoming edge at t=0, an outgoing edge at bd)
                spans = []
                if not first:
                    spans.append((0.0, td))
                if not last:
                    spans.append((max(0.0, bd - td), bd))
                en = "+".join(f"between(t,{a:.3f},{b:.3f})"
                              for a, b in spans)
                if style in ("dip_black", "dip_white"):
                    tcolor = "white" if style == "dip_white" else "black"
                    chain = []
                    if not first:
                        chain.append(f"fade=t=in:st=0:d={td:.2f}:c={tcolor}")
                    if not last:
                        chain.append(f"fade=t=out:st={max(0.0, bd - td):.2f}:"
                                     f"d={td:.2f}:c={tcolor}")
                    parts.append(f"[{vlab}]{','.join(chain)}[{out_lab}]")
                elif style == "flash":
                    # Additive white pop peaking exactly ON the cut — eq's
                    # brightness accepts a per-frame expression, so the ramp
                    # is continuous, unlike a dip's fade-through.
                    terms = []
                    if not last:
                        terms.append(
                            f"0.85*max(0,1-({bd:.3f}-t)/{td:.3f})")
                    if not first:
                        terms.append(f"0.85*max(0,1-t/{td:.3f})")
                    parts.append(f"[{vlab}]eq=brightness='{'+'.join(terms)}'"
                                 f":eval=frame[{out_lab}]")
                elif style == "glitch":
                    rr = max(4, int(round((W or 1280) * 0.008)))
                    parts.append(
                        f"[{vlab}]rgbashift=rh={rr}:bh=-{rr}:enable='{en}',"
                        f"noise=alls=18:allf=t:enable='{en}'[{out_lab}]")
                else:
                    faded.append((vlab, alab, bd))
                    continue
                faded.append((out_lab, alab, bd))
            blocks = faded

    pairs = "".join(f"[{v}][{a}]" for v, a, _d in blocks)
    parts.append(f"{pairs}concat=n={len(blocks)}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    if trans_post:
        # Junction list in PROGRAM time. Each side of a junction keeps the
        # per-block rule: it participates only when its own block affords
        # td >= 0.05 (min(tdur, bd/2 - 0.05)). Terms are half-open-windowed
        # (gte*lt) so the junction frame belongs to the incoming side — the
        # exact frame ownership concat gave the per-block version.
        style, tdur = trans_post
        cum, juncs = 0.0, []
        for k in range(len(blocks) - 1):
            bd_k, bd_n = blocks[k][2], blocks[k + 1][2]
            cum += bd_k
            # `cum` must keep accumulating for EVERY block — it is the junction's
            # position in program time. Only whether we emit a whip here is
            # conditional. Skipping the accumulation instead of the append would
            # slide every later transition onto the wrong moment.
            if k not in junctions:
                continue
            td_o = min(tdur, max(0.0, bd_k / 2 - 0.05))
            td_i = min(tdur, max(0.0, bd_n / 2 - 0.05))
            juncs.append((cum, td_o if td_o >= 0.05 else None,
                          td_i if td_i >= 0.05 else None))

        def _win(tvar, a, b):
            return f"gte({tvar},{a:.3f})*lt({tvar},{b:.3f})"

        if style in ("whip_left", "whip_right"):
            # The frame whips off in the cut direction over a black backdrop
            # while a directional blur smears the motion; the next block
            # whips in from the opposite edge. Quadratic easing so the move
            # accelerates INTO the cut.
            dirn = -1 if style == "whip_left" else 1
            xterms, enspans = [], []
            for c, td_o, td_i in juncs:
                if td_o:
                    xterms.append(
                        f"{dirn}*{W}*pow(max(0,"
                        f"(t-{c - td_o:.3f})/{td_o:.3f}),2)"
                        f"*{_win('t', c - td_o, c)}")
                    enspans.append((c - td_o, c))
                if td_i:
                    # ({-dirn}) not -({dirn}): terms are '+'-joined, and
                    # ffmpeg's expression parser rejects the '+-(' sequence
                    # a leading unary minus would produce.
                    xterms.append(
                        f"({-dirn})*{W}*pow(max(0,"
                        f"1-(t-{c:.3f})/{td_i:.3f}),2)"
                        f"*{_win('t', c, c + td_i)}")
                    enspans.append((c, c + td_i))
            if xterms:
                total_bd = sum(b for _, _, b in blocks)
                blur_r = max(6, int(round((W or 1280) * 0.012)))
                en = "+".join(f"between(t,{a:.3f},{b:.3f})"
                              for a, b in merge_spans(enspans, gap=0.0))
                parts.append(f"color=c=black:s={W}x{H}:r={fps:.3f}:"
                             f"d={total_bd:.3f}[wbg]")
                parts.append(f"[wbg][{vlabel}]overlay="
                             f"x='{'+'.join(xterms)}':y=0:"
                             f"eof_action=pass[wov]")
                parts.append(f"[wov]dblur=angle=0:radius={blur_r}:"
                             f"enable='{en}'[vwhip]")
                vlabel = "vwhip"
        else:                          # zoom_punch
            # Accelerating push INTO the cut; the next block lands from an
            # over-zoom and settles. zoompan needs CFR (do_norm forced), so
            # on/fps is program time.
            #
            # Round 63 — what makes this read as ONE camera move instead of
            # two zooms with a cut between them ("smooth transition... without
            # the user even noticing", from a real user's brief):
            #   * VELOCITY CONTINUITY. The incoming over-zoom is scaled so its
            #     initial rate matches the outgoing side's final rate
            #     (B = A*td_i/td_o): the apparent camera decelerates through
            #     the cut instead of changing speed exactly where the eye
            #     must not be given a reason to look.
            #   * MOTION BLUR at the peak, from tmix frame-blending — the same
            #     real blur the motion_blur stylize uses. Around the junction
            #     the frames are moving fastest, so averaging them yields a
            #     radial smear that peaks exactly ON the cut — and because
            #     tmix's window spans the junction, the last outgoing and
            #     first incoming frames blend INTO EACH OTHER: the content
            #     switch happens inside the smear, which is the entire trick
            #     of the professional zoom-through.
            T = f"on/{fps:.3f}"
            A = 0.55
            zterms = []
            blur_spans = []
            for c, td_o, td_i in juncs:
                if td_o:
                    zterms.append(
                        f"{A}*pow(max(0,({T}-{c - td_o:.3f})"
                        f"/{td_o:.3f}),2)*{_win(T, c - td_o, c)}")
                    blur_spans.append((c - min(td_o * 0.6, 0.25), c))
                if td_i:
                    B = min(A * (td_i / td_o) if td_o else 0.35, 0.6)
                    zterms.append(
                        f"{B:.3f}*pow(max(0,1-({T}-{c:.3f})"
                        f"/{td_i:.3f}),2)*{_win(T, c, c + td_i)}")
                    blur_spans.append((c, c + min(td_i * 0.5, 0.2)))
            if zterms:
                parts.append(f"[{vlabel}]zoompan=z='1+{'+'.join(zterms)}'"
                             f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                             f":d=1:s={W}x{H}:fps={fps:.3f}[vpunch]")
                vlabel = "vpunch"
                en = "+".join(f"between(t,{a:.3f},{b:.3f})"
                              for a, b in merge_spans(blur_spans, gap=0.0))
                parts.append(f"[{vlabel}]tmix=frames=5:enable='{en}'"
                             f"[vpunchb]")
                vlabel = "vpunchb"
    # effects: grade -> custom grade -> stylize -> zooms -> overlays ->
    # (captions burn) -> (graphics burn) -> fades. Zooms use one zoompan
    # whose z steps up inside each window; do_norm guarantees the frames
    # entering it are exact CFR WxH so on/fps is program time. Overlays sit
    # ABOVE zooms deliberately (a corner PIP must not scale when the footage
    # punches) and BELOW both text layers (words always win).
    grade = fx.get("grade")
    if grade and grade in GRADE_FILTERS:
        # gradelut turns the per-pixel grade math into baked table filters —
        # same values, several times cheaper — or returns the chain untouched
        # when it can't (see worker/gradelut.py).
        parts.append(f"[{vlabel}]{gradelut.fast_chain(GRADE_FILTERS[grade])}"
                     "[vgrade]")
        vlabel = "vgrade"
    if grade_custom:
        chain = grade_custom_chain(grade_custom)
        if chain:
            parts.append(f"[{vlabel}]{gradelut.fast_chain(chain)}[vgcust]")
            vlabel = "vgcust"
    for si, styl in enumerate(stylize):
        kind = styl.get("kind")
        i_ = float(styl.get("intensity") or 0.5)
        a = styl.get("start")
        b = styl.get("end")
        if a is not None:
            a = max(0.0, float(a))
            b = min(tl.out_duration, float(b))
            if b - a < 0.05:
                continue
            en = f":enable='between(t,{a:.3f},{b:.3f})'"
            win = f"between(t,{a:.3f},{b:.3f})"
        else:
            en = ""
            win = "1"
        out_lab = f"vsty{si}"
        if kind == "grain":
            parts.append(f"[{vlabel}]noise=alls={5 + int(25 * i_)}"
                         f":allf=t+u{en}[{out_lab}]")
        elif kind == "vignette":
            parts.append(f"[{vlabel}]vignette=a=PI/{4.8 - 2.2 * i_:.2f}"
                         f"{en}[{out_lab}]")
        elif kind == "chromatic":
            r = 2 + int(10 * i_)
            parts.append(f"[{vlabel}]rgbashift=rh={r}:bh=-{r}{en}"
                         f"[{out_lab}]")
        elif kind == "dream_blur":
            parts.append(f"[{vlabel}]gblur=sigma={2 + 8 * i_:.1f}{en}"
                         f"[{out_lab}]")
        elif kind == "vhs":
            parts.append(f"[{vlabel}]rgbashift=rh=3:bh=-3{en},"
                         f"noise=alls=12:allf=t{en},"
                         f"eq=saturation=0.8:contrast=1.05{en}[{out_lab}]")
        elif kind == "flash":
            parts.append(f"[{vlabel}]eq=brightness={0.15 + 0.45 * i_:.2f}"
                         f"{en}[{out_lab}]")
        elif kind == "glow":
            # split -> blur -> screen-blend. blend's enable passes the TOP
            # (first) input through when off, which is the ungraded main —
            # exactly the off state a windowed glow needs.
            parts.append(f"[{vlabel}]split[glA{si}][glB{si}]")
            parts.append(f"[glB{si}]gblur=sigma={10 + 25 * i_:.1f}[glG{si}]")
            parts.append(f"[glA{si}][glG{si}]blend=all_mode=screen"
                         f":all_opacity={0.25 + 0.4 * i_:.2f}{en}"
                         f"[{out_lab}]")
        elif kind == "sharpen":
            # unsharp with a 5x5 kernel: the honest answer to "make it
            # clearer". It recovers apparent detail a phone's encoder smeared;
            # it does NOT add resolution, and past ~0.8 it starts ringing on
            # edges, which is why the amount tops out below the filter's own
            # maximum.
            parts.append(f"[{vlabel}]unsharp=5:5:{0.35 + 1.05 * i_:.2f}"
                         f":5:5:{0.15 + 0.45 * i_:.2f}{en}[{out_lab}]")
        elif kind == "denoise":
            # hqdn3d before sharpening is what stops a sharpen pass from
            # amplifying sensor noise into crawling grain. Low-light phone
            # footage is the case; it costs detail, so the default is gentle.
            parts.append(f"[{vlabel}]hqdn3d={1.5 + 3.5 * i_:.2f}"
                         f":{1.0 + 2.5 * i_:.2f}:{4.0 + 6.0 * i_:.2f}"
                         f":{3.0 + 5.0 * i_:.2f}{en}[{out_lab}]")
        elif kind == "motion_blur":
            # Real frame blending — each output frame is the average of the
            # last N — which is what "motion blur" means on already-shot
            # footage. Only reads on MOVEMENT (a whip, a sprint, a speed ramp);
            # on a static shot it does nothing, which is correct and worth
            # telling the user.
            n = 2 + int(round(3 * i_))
            parts.append(f"[{vlabel}]tmix=frames={n}:weights='"
                         + " ".join(["1"] * n) + f"'{en}[{out_lab}]")
        elif kind == "shake":
            # Windowed handheld wobble via zoompan: z=1 outside the window,
            # so there is NO hidden crop on the rest of the program (a naive
            # crop-shift shakes cheaply but permanently zooms everything).
            T = f"on/{fps:.3f}"
            winT = win.replace("t,", f"{T},") if win != "1" else "1"
            amp = (W or 1280) * 0.006 * (0.5 + i_)
            z_amt = 0.04 + 0.06 * i_
            parts.append(
                f"[{vlabel}]zoompan=z='1+{z_amt:.3f}*({winT})'"
                f":x='iw/2-(iw/zoom/2)+{amp:.1f}*sin({T}*{13 + 8 * i_:.1f})"
                f"*({winT})'"
                f":y='ih/2-(ih/zoom/2)+{amp * 0.7:.1f}*cos({T}*{11 + 6 * i_:.1f})"
                f"*({winT})'"
                f":d=1:s={W}x{H}:fps={fps:.3f}[{out_lab}]")
        else:
            continue
        vlabel = out_lab

    # Round 96 — agent-written chains, spliced after the preset stylize so a
    # custom look composes on top of the same picture a preset would get.
    # The chain went through custom_chain_error at write time (no graph
    # syntax, no file access), so it can never restructure this graph; the
    # add tool's dry run pinned geometry/fps unchanged, so downstream stages
    # (zoompan, overlays) see the CFR WxH they assume.
    for ci, cf in enumerate(custom_filters):
        chain = (cf.get("chain") or "").strip().strip(",")
        if not chain:
            continue
        a = cf.get("start")
        out_lab = f"vcusf{ci}"
        if a is None:
            parts.append(f"[{vlabel}]{chain}[{out_lab}]")
        else:
            a = max(0.0, float(a))
            b = min(tl.out_duration, float(cf.get("end") or 0.0))
            if b - a < 0.05:
                continue
            # The agent's chain may contain filters with no timeline
            # support, so ':enable=' cannot be appended to it — process a
            # split branch and composite it back only inside the window
            # instead (the glow trick above, generalized).
            parts.append(f"[{vlabel}]split[cusA{ci}][cusB{ci}]")
            parts.append(f"[cusB{ci}]{chain}[cusP{ci}]")
            parts.append(f"[cusA{ci}][cusP{ci}]overlay=eof_action=pass"
                         f":enable='between(t,{a:.3f},{b:.3f})'[{out_lab}]")
        vlabel = out_lab

    # ---- words BEHIND the subject (round 60) -----------------------------
    # The one composite that has to happen BEFORE the zoom stage.
    #
    # Everything else about text is drawn late, on top of everything, because
    # words win. These words lose on purpose: they are in the SCENE, painted on
    # the wall the subject walks past. Three consequences, all deliberate:
    #
    #   * It is emitted here, after the grade and before the zoom, because the
    #     mask was measured on source frames in output geometry — the same
    #     geometry the picture has at this point in the graph, and NOT the
    #     geometry it has after zoompan. A mask composited after a punch zoom is
    #     a subject cut out of the wrong part of the frame.
    #   * The text therefore rides the zoom with the picture, which is what it
    #     should do: a push into a wall pushes into the writing on it too.
    #   * The subject's pixels come from the render's OWN picture (split, then
    #     alphamerge the mask onto the copy), never from the mask clip. That is
    #     what lets the mask be measured on the 540p proxy and still composite
    #     into a 4K export: scaling a MASK softens an edge, where scaling a
    #     cut-out subject would drop a blurry patch into a sharp frame.
    for j, (idx, item, win) in enumerate(behind_inputs or []):
        b_start, b_end = win
        b_dur = max(0.05, b_end - b_start)
        tail_pad = max(0.0, tl.out_duration - b_end)
        # tpad black-fills the mask to the WHOLE programme so alphamerge — a
        # framesync filter — has a frame to pair with every frame of the
        # picture, from t=0. Black in a gray mask is alpha 0: outside the
        # window the "subject" layer is fully transparent, so the composite is
        # the picture, exactly. Padding in the graph rather than encoding
        # thousands of black frames into the artifact.
        chain = [f"trim=start=0:end={b_dur:.3f}", "setpts=PTS-STARTPTS",
                 f"scale={W}:{H}", "format=gray",
                 f"fps={fps:.3f}"]
        if b_start > 0.001:
            chain.append(f"tpad=start_duration={b_start:.3f}"
                         f":start_mode=add:color=black")
        if tail_pad > 0.001:
            chain.append(f"tpad=stop_duration={tail_pad + 1.0:.3f}"
                         f":stop_mode=add:color=black")
        parts.append(f"[{idx}:v]{','.join(chain)}[bhm{j}]")
        parts.append(f"[{vlabel}]split[bhb{j}][bhf{j}]")
        # The words, burned on a copy of the picture...
        parts.append(f"[bhb{j}]subtitles=filename='{item['ass']}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[bht{j}]")
        # ...and the subject, lifted off the OTHER copy by the mask and laid
        # back over them.
        parts.append(f"[bhf{j}][bhm{j}]alphamerge[bhfa{j}]")
        parts.append(f"[bht{j}][bhfa{j}]overlay=0:0:format=auto"
                     f":eof_action=pass[vbh{j}]")
        vlabel = f"vbh{j}"

    zoom_terms = []
    # A takeover always aims (at the screen), so it forces the targeted branch
    # for the whole zoompan. It must be decided BEFORE the zoom loop: that loop
    # only emits a zoom's own cx/cy terms when the graph is already targeted,
    # so flipping this afterwards would silently drop them.
    zoom_targeted = bool(takeovers) or any(
        z.get("cx") is not None or z.get("cy") is not None or z.get("path")
        for z in zooms)
    cx_terms, cy_terms = [], []
    for z in zooms:
        a = max(0.0, float(z["start"]))
        b = min(tl.out_duration, float(z["end"]))
        if b - a < 0.05:
            continue
        st = float(z.get("strength", 0.25))
        t = f"on/{fps:.3f}"
        zmode = z.get("mode") or "punch"
        if zmode in ("follow", "path"):
            # Round 45 / round 51. The travelling zoom: the CENTRE glides
            # along the waypoints while the frame stays pushed in, instead of
            # cutting out and back between two subjects. 'follow' holds one
            # strength and ramps it at the window edges; 'path' keyframes the
            # strength too. Both shapes — and both callers, showcase_demo and
            # add_zoom_path — go through worker/travel.py, which emits ONE
            # expression per axis. Legacy 'follow' zooms come back out of it
            # character-for-character, so their cached renders still match.
            zoom_terms.append(travel.strength_term(z, t, a, b))
            cxe, cye = travel.centre_terms(z, t, a, b)
            if cxe:
                cx_terms.append(cxe)
            if cye:
                cy_terms.append(cye)
            continue
        if zmode == "ease":
            # smooth ramp in and out inside the window (0 outside it)
            r = max(0.15, min(0.4, (b - a) / 4.0))
            zoom_terms.append(
                f"{st:.2f}*clip(({t}-{a:.3f})/{r:.3f},0,1)"
                f"*clip(({b:.3f}-{t})/{r:.3f},0,1)")
        elif zmode == "push_in":
            # Ken Burns drift: zoom grows 0 -> strength across the window
            zoom_terms.append(
                f"{st:.2f}*(({t}-{a:.3f})/{b - a:.3f})"
                f"*between({t},{a:.3f},{b:.3f})")
        elif zmode == "pull_out":
            zoom_terms.append(
                f"{st:.2f}*(1-(({t}-{a:.3f})/{b - a:.3f}))"
                f"*between({t},{a:.3f},{b:.3f})")
        else:                           # punch: instant step in/out
            zoom_terms.append(f"{st:.2f}*between({t},{a:.3f},{b:.3f})")
        if zoom_targeted:
            # target expressions: 0.5 (center) outside every window, the
            # zoom's own cx/cy inside its window — so multiple zooms can
            # each punch toward their own subject.
            cx = z.get("cx")
            cy = z.get("cy")
            if cx is not None and abs(float(cx) - 0.5) > 1e-6:
                cx_terms.append(f"{float(cx) - 0.5:.3f}"
                                f"*between({t},{a:.3f},{b:.3f})")
            if cy is not None and abs(float(cy) - 0.5) > 1e-6:
                cy_terms.append(f"{float(cy) - 0.5:.3f}"
                                f"*between({t},{a:.3f},{b:.3f})")
    # An aspect shift optionally pushes the picture in as the frame narrows, so
    # the subject holds its size instead of just losing its sides. It rides the
    # SAME zoompan as the zooms (one geometry filter, not two) and is emitted
    # as one more term — which also means a zoom and a shift over the same
    # moment compose rather than fight.
    # A screen takeover's camera push is a zoom term like any other — one
    # geometry filter, and the same resolver the corner pin below reads, so the
    # shot and the content it is pinned to can never be computed differently.
    for idx, item in takeovers:
        a = max(0.0, float(item["start"]))
        b = min(tl.out_duration, a + float(item["duration_s"]))
        if b - a < 0.05:
            continue
        st, cxt, cyt = _screen_lock_terms(item["screen"], f"on/{fps:.3f}",
                                          a, b, fps)
        zoom_terms.append(st)
        if cxt:
            cx_terms.append(cxt)
        if cyt:
            cy_terms.append(cyt)
        # The landing (round 62b, reshaped in 63b): momentum through the cut.
        # ONE sin profile starts at the ARRIVAL (b minus the hold — where the
        # push actually lands now), rises past full frame and settles. Its
        # pre-cut half is applied by the OVERLAY's own corner growth (the
        # program picture is covered there); this term is the post-cut half,
        # picking up the SAME curve at the SAME value on the frame after the
        # handoff — both sides of the cut show the same content at the same
        # magnification, so the join sits inside one uninterrupted zoom.
        tvar = f"on/{fps:.3f}"
        hold = screen_lock_hold(b - a)
        bp = b - hold
        le = min(tl.out_duration, bp + hold + SCREEN_LAND_S)
        # land=False: no post-cut settle — see the matching gate on the
        # overlay-side `grow` term in _screen_pin_filter.
        if le - b > 0.05 and (item.get("screen") or {}).get("land") \
                is not False:
            p = f"clip(({tvar}-{bp:.3f})/{le - bp:.5f},0,1)"
            zoom_terms.append(f"{SCREEN_LAND_ZOOM:.3f}*sin(PI*{p})"
                              f"*gt({tvar},{b:.3f})*lt({tvar},{le:.3f})")
    shift_w, shift_h, shift_z = ([], [], [])
    if shifts:
        shift_w, shift_h, shift_z = screenframe.shift_tracks(
            shifts, W or 1280, H or 720, tl.out_duration)
        if screenframe.track_varies(shift_z):
            zexp = travel.path_value_expr(
                shift_z, "v", f"on/{fps:.3f}", 0.0, tl.out_duration,
                default=0.0, ease="cubic_in_out")
            if zexp:
                zoom_terms.append(f"({zexp})")
    if zoom_terms:
        zexpr = "1+" + "+".join(zoom_terms)
        if zoom_targeted:
            cxe = "0.5" + ("+" + "+".join(cx_terms) if cx_terms else "")
            cye = "0.5" + ("+" + "+".join(cy_terms) if cy_terms else "")
            xexpr = f"(iw-iw/zoom)*({cxe})"
            yexpr = f"(ih-ih/zoom)*({cye})"
        else:
            # the exact legacy strings — mathematically (iw-iw/zoom)*0.5
            xexpr = "iw/2-(iw/zoom/2)"
            yexpr = "ih/2-(ih/zoom/2)"
        parts.append(f"[{vlabel}]zoompan=z='{zexpr}'"
                     f":x='{xexpr}':y='{yexpr}'"
                     f":d=1:s={W}x{H}:fps={fps:.3f}[vzoom]")
        vlabel = "vzoom"
    # ---- screen takeovers (round 55): content pinned INTO the footage ----
    # Emitted after the zoom because the corner path is written in POST-push
    # screen space, and before the PIP overlays because a takeover is the
    # PICTURE for its window — a logo or a caption still belongs on top of it.
    for j, (idx, item) in enumerate(takeovers):
        o_start = float(item["start"])
        o_dur = float(item["duration_s"])
        lock = item["screen"]
        pad = SCREEN_PAD_PX
        iw_, ih_ = _even((W or 1280) - 2 * pad), _even((H or 720) - 2 * pad)
        chain = []
        if item["kind"] != "image":
            off = float(item.get("source_start_s") or 0.0)
            # The content supply runs PAST the window end (round 66). Trimmed
            # to exactly o_dur, fractional frame rates (59.969...) leave the
            # pinned branch exhausted one or two frames BEFORE the cut, and
            # `overlay eof_action=pass` then shows the zoomed BASE for those
            # frames — a 16-33ms flash of the room between the full-frame
            # content and the incoming clip, which a real user described as
            # "a frame of the old screen reappears and goes off very fast".
            # The extra frames are the same pixels the spliced clip opens
            # with (source time runs continuously across the handoff), and
            # the enable window below still gates what is DISPLAYED.
            chain.append(
                f"trim=start={off:.3f}"
                f":end={off + o_dur + SCREEN_SUPPLY_PAD_S:.3f}")
            chain.append("setpts=PTS-STARTPTS")
        # The content is cover-fitted to the OUTPUT frame, not to the screen's
        # shape: the takeover ENDS full-frame, and an asset that changed aspect
        # on the way there would have to squash to get out of the glass.
        chain.append(f"scale={iw_}:{ih_}:force_original_aspect_ratio=increase")
        chain.append(f"crop={iw_}:{ih_}")
        chain.append("format=rgba")
        # The content FADES onto the glass (round 62b) — but LATE in the dive
        # (round 64). At the window's start the push has not moved and the
        # viewer is looking at a wide shot of the room: content appearing
        # there is the next scene playing on a laptop across the room, which
        # a real user called out as exactly wrong. The dissolve is placed on
        # the PUSH'S PROGRESS instead — from SCREEN_APPEAR_E0 to
        # SCREEN_APPEAR_E1 of the eased zoom travel, converted to
        # branch-local seconds through the ease's inverse — so the glass
        # shows what was filmed until it dominates the frame, and the content
        # is fully opaque with the last stretch of the push still to run
        # (plus the whole full-frame hold) before the cut.
        f0, f1 = screen_appear_window(lock, o_dur, fps)
        if f1 - f0 >= 0.05:
            chain.append(
                f"fade=t=in:st={f0:.3f}:d={f1 - f0:.3f}:alpha=1")
        chain.append(f"pad={W}:{H}:{pad}:{pad}:color=black@0")
        # vf_perspective has no `t` — only `on` — so the frames reaching it
        # have to be at the render rate or every corner expression is off by
        # the ratio of the asset's fps to ours.
        chain.append(f"fps={fps:.3f}")
        cs = screen_lock_corner_paths(lock, W or 1280, H or 720, fps, o_dur)
        chain.append(
            "perspective=" + ":".join(
                f"{k}='{v}'" for k, v in zip(
                    ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"), cs))
            + ":sense=destination:eval=frame:interpolation=cubic")
        op = item.get("opacity")
        if op is not None and float(op) < 0.999:
            chain.append(f"colorchannelmixer=aa={float(op):.3f}")
        chain.append(f"setpts=PTS+{o_start:.3f}/TB")
        parts.append(f"[{idx}:v]{','.join(chain)}[tko{j}]")
        parts.append(
            f"[{vlabel}][tko{j}]overlay=0:0:eof_action=pass"
            f":enable='between(t,{o_start:.3f},{o_start + o_dur:.3f})'"
            f"[vtk{j}]")
        vlabel = f"vtk{j}"
    # ---- overlays (round 35): PIP / b-roll / logo layer -----------------
    for j, (idx, item) in enumerate(overlay_inputs):
        o_start = float(item["start"])
        o_dur = float(item["duration_s"])
        ow = _even((W or 1280) * float(item.get("scale") or 0.4))
        chain = []
        if item["kind"] != "image":
            off = float(item.get("source_start_s") or 0.0)
            chain.append(f"trim=start={off:.3f}:end={off + o_dur:.3f}")
            chain.append("setpts=PTS-STARTPTS")
        if item.get("fit") == "cover":
            # B-roll cutaway (round 36): fill the WHOLE output frame — scale
            # up + center-crop the overflow. The position expression below
            # still runs; with w == main_w and the default x/y of 0.5 it
            # resolves to 0, and entrances/exits/opacity keep working.
            chain.append(f"scale={W or 1280}:{H or 720}:"
                         f"force_original_aspect_ratio=increase,"
                         f"crop={W or 1280}:{H or 720}")
        else:
            chain.append(f"scale={ow}:-2")
        chain.append("format=rgba")
        op = item.get("opacity")
        if op is not None and float(op) < 0.999:
            chain.append(f"colorchannelmixer=aa={float(op):.3f}")
        ent, ext = item.get("entrance"), item.get("exit")
        ed = min(0.35, o_dur / 3)
        if ent == "fade":
            chain.append(f"fade=t=in:st=0:d={ed:.2f}:alpha=1")
        if ext == "fade":
            chain.append(f"fade=t=out:st={max(0.0, o_dur - ed):.2f}"
                         f":d={ed:.2f}:alpha=1")
        rot = item.get("rotation")
        if rot:
            rad = float(rot) * 3.14159265 / 180.0
            chain.append(f"rotate={rad:.4f}:c=black@0.0"
                         f":ow=rotw({rad:.4f}):oh=roth({rad:.4f})")
        chain.append(f"setpts=PTS+{o_start:.3f}/TB")
        parts.append(f"[{idx}:v]{','.join(chain)}[ovp{j}]")
        # position: keyframed fractions of the MAIN frame, center-anchored.
        # Slide entrance/exit rides the position expression (quadratic ease
        # from/to one frame-width/height away).
        lt = f"(t-{o_start:.3f})"
        xe = f"main_w*({_anim_expr(item.get('x', 0.5), lt)})-w/2"
        ye = f"main_h*({_anim_expr(item.get('y', 0.5), lt)})-h/2"
        if ent == "slide_left":       # arrives moving leftward: from right
            xe += f"+main_w*pow(max(0,1-{lt}/{ed:.2f}),2)"
        elif ent == "slide_right":
            xe += f"-main_w*pow(max(0,1-{lt}/{ed:.2f}),2)"
        elif ent == "slide_up":       # arrives moving upward: from below
            ye += f"+main_h*pow(max(0,1-{lt}/{ed:.2f}),2)"
        xt0 = max(0.0, o_dur - ed)
        if ext == "slide_left":
            xe += f"-main_w*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        elif ext == "slide_right":
            xe += f"+main_w*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        elif ext == "slide_up":
            ye += f"-main_h*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        parts.append(
            f"[{vlabel}][ovp{j}]overlay=x='{xe}':y='{ye}'"
            f":eof_action=pass"
            f":enable='between(t,{o_start:.3f},{o_start + o_dur:.3f})'"
            f"[vov{j}]")
        vlabel = f"vov{j}"
    if ass_path:
        # fontsdir points libass at the premium fonts bundled with the
        # worker (worker/fonts) — system fontconfig still supplies DejaVu
        # and the Noto fallbacks for scripts the bundled fonts lack.
        #
        # cap_burn_offset (round 93): a stitched-preview PIECE burns its
        # captions from the FULL program's ASS at full-program TIMESTAMPS —
        # the frames time-travel to output clock w0 for the burn, then come
        # back. libass picks the frame's events statelessly, so an event
        # already mid-display at the piece boundary renders exactly as the
        # full render would — karaoke, transforms and fades included. This
        # is what freed stitch boundaries from caption timing entirely (a
        # dynamic-caption project tiles the whole program with animated
        # events; no boundary could avoid them).
        if cap_burn_offset:
            parts.append(
                f"[{vlabel}]setpts=PTS+{cap_burn_offset:.4f}/TB,"
                f"subtitles=filename='{ass_path}'"
                f":fontsdir='{caplib.FONTS_DIR}',"
                f"setpts=PTS-STARTPTS[vsub]")
        else:
            parts.append(f"[{vlabel}]subtitles=filename='{ass_path}'"
                         f":fontsdir='{caplib.FONTS_DIR}'[vsub]")
        vlabel = "vsub"
    if gfx_ass_path:
        # The motion-graphics layer burns on its own pass ABOVE captions —
        # a title always wins over a caption crossing it (see graphics.py
        # for why it is a separate file, not extra caption events).
        parts.append(f"[{vlabel}]subtitles=filename='{gfx_ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[vgfx]")
        vlabel = "vgfx"
    # ---- mid-video aspect change (round 51) -----------------------------
    # The bars go on ABOVE the captions on purpose: when the frame narrows to
    # 9:16, anything outside the new window is outside the FRAME, and a caption
    # half-hanging into the letterbox is the single artefact that would give
    # the effect away as a matte rather than a reframe.
    if shifts:
        # NOT drawbox. drawbox evaluates x/y/w/h ONCE, at configuration time —
        # verified against ffmpeg, where a box with w='320*t/2' renders at a
        # constant width for the whole clip (and w=0 means "the input's full
        # width", so a zero-thickness bar blanks the frame instead of
        # disappearing). overlay DOES re-evaluate its position per frame with
        # eval=frame, so each bar is a full-frame colour plate slid in from
        # off-screen: the visible part of it IS the bar, and its thickness
        # animates because its position does.
        col = (shifts[0].get("color") or "#000000").lstrip("#")
        # The expressions are resolved BEFORE the split is emitted. Deciding
        # how many bar copies to fan out and then skipping one in the loop
        # would leave a split output unconnected, which ffmpeg rejects — the
        # whole render dies rather than one effect being missing.
        axes = []
        for axis, pts, dim in (("w", shift_w, W or 1280),
                               ("h", shift_h, H or 720)):
            if not screenframe.track_varies(pts):
                continue
            expr = travel.path_value_expr(pts, "v", "t", 0.0, tl.out_duration,
                                          default=1.0, ease="cubic_in_out")
            if expr:
                axes.append((axis, dim, expr))
        if axes:
            n_bars = 2 * len(axes)
            parts.append(
                f"color=c=0x{col}:s={W or 1280}x{H or 720}:r={fps:.3f}:"
                f"d={tl.out_duration + 1.0:.3f},format=rgba,split"
                + (f"={n_bars}" if n_bars > 2 else "")
                + "".join(f"[fsb{i}]" for i in range(n_bars)))
        bar_i = 0
        for axis, dim, expr in axes:
            # bar thickness = half of what the target aspect gives up
            thick = f"({dim}*(1-({expr}))/2)"
            for k, pos in enumerate((f"({thick})-{dim}", f"{dim}-({thick})")):
                out_lab = f"vfs{axis}{k}"
                xy = (f"x='{pos}':y=0" if axis == "w"
                      else f"x=0:y='{pos}'")
                parts.append(
                    f"[{vlabel}][fsb{bar_i}]overlay={xy}:eval=frame:"
                    f"format=auto[{out_lab}]")
                vlabel = out_lab
                bar_i += 1
    # ---- the floating rounded window (round 51) -------------------------
    # ONE composite: the finished picture is scaled into the plate's hole and
    # the plate — backdrop, shadow and rounded corners already drawn — is laid
    # over it. Applied after captions and graphics so the whole finished video
    # is what floats, which is what the look means.
    if plate_idx is not None and plate_box:
        pw, ph, ox, oy = plate_box
        parts.append(f"[{vlabel}]scale={pw}:{ph},setsar=1[vsfp]")
        parts.append(f"[vsfp]pad={W}:{H}:{ox}:{oy}:color=black[vsfb]")
        parts.append(f"[{plate_idx}:v]format=rgba[vsfpl]")
        parts.append("[vsfb][vsfpl]overlay=0:0:format=auto[vsf]")
        vlabel = "vsf"
    if tail_ext > 0.05:
        parts.append(f"[{vlabel}]tpad=stop_mode=add:"
                     f"stop_duration={tail_ext:.3f}:color=black[vext]")
        vlabel = "vext"
    fade_in = float(fx.get("fade_in_s") or 0.0)
    fade_out = float(fx.get("fade_out_s") or 0.0)
    if fade_in:
        parts.append(f"[{vlabel}]fade=t=in:st=0:d={fade_in:.2f}[vfi]")
        vlabel = "vfi"
    if fade_out:
        st = max(0.0, total_dur - fade_out)
        parts.append(f"[{vlabel}]fade=t=out:st={st:.2f}:d={fade_out:.2f}[vfo]")
        vlabel = "vfo"
    # ---- branded end card ---------------------------------------------
    # Placed AFTER the grade, captions and fades. Upstream of the grade it
    # would be recoloured (GRADE_FILTERS['bw'] desaturates the brand red,
    # 'vintage' tints it); upstream of fade_out a user's fade-to-black would
    # swallow the branding instead of ending the programme. It is deliberately
    # NOT routed through the music amix: that mix is `duration=first`, keyed to
    # the programme stream, so appending the card's silence there would
    # silently extend every music item's span.
    #
    # The preview downscale happens AFTER the concat, not before. Doing it
    # first and then forcing the programme back to WxH for concat compatibility
    # would scale a 480p preview back UP to full resolution — a preview that is
    # slower to encode and larger than the final it is standing in for.
    # Free-tier mark on the PROGRAM stream, before any end card is
    # concatenated — the card is already branded, and marking it would stack
    # two logos on one frame.
    if robot_idx is not None:
        parts.extend(_watermark_parts(vlabel, "vwm", robot_idx,
                                      wm_ass_path, W, H))
        vlabel = "vwm"

    outro_here = outro_s > 0.0 and card_idx is not None
    v_final = "vout"
    # Only when the programme is still taller than the preview height —
    # preview_geometry now caps H there, so on every path that goes through it
    # this is a no-op and is skipped rather than emitted as an extra
    # full-frame pass. Kept for any caller that builds a graph without it.
    if not outro_here and preview and _needs_preview_downscale(H):
        parts.append(rf"[{vlabel}]scale=-2:min({config.PREVIEW_MAX_HEIGHT}\,"
                     r"floor(ih/2)*2)[vsc]")
        vlabel = "vsc"
    if outro_here:
        # Force exact geometry before concat. concat demands identical
        # dimensions, SAR and pixel format across segments, and the cheap
        # graph (no inserts, no reframe, no zoom) makes no such guarantee.
        #
        # But "exact" must not mean "different from what this render would
        # otherwise have produced". Two properties the cheap graph passes
        # through untouched, both measured as regressions before this:
        #
        #  * SAR. Anamorphic sources (a 16:9 picture stored in 4:3 pixels)
        #    carry a non-1 pixel aspect. A blanket setsar=1 at the coded width
        #    squashes the picture, so the width is widened to the DISPLAY
        #    width first and the result genuinely is square-pixel.
        #  * Frame rate. `fps` is capped at 60 for the normalized path; the
        #    cheap path keeps the source's own rate. Forcing the cap turned a
        #    120fps export into 60fps only because it gained an end card.
        #
        # When do_norm already ran, the programme is W x H, SAR 1, at `fps` —
        # so those are the right targets and no correction applies.
        sar = 1.0 if do_norm else (float(src_sar) or 1.0)
        oW = W if abs(sar - 1.0) < 0.001 else _even(W * sar)
        ofps = fps if (do_norm or not src_fps) else float(src_fps)
        parts.append(f"[{vlabel}]scale={oW}:{H},setsar=1,"
                     f"format=yuv420p[vprog]")
        # v6: the card is a full 9:16 sheet that carries its own margins,
        # so it fills the frame rather than being inset a second time.
        cw, ch = _even(oW * 0.98), _even(H * 0.98)
        parts.append(f"color=c=black:s={oW}x{H}:r={ofps:.3f}:d={outro_s:.3f},"
                     f"format=rgba[obg]")
        # One 9:16 card fits every aspect ratio: scaled to FIT (never crop)
        # inside a box that is a fraction of BOTH dimensions, it lands
        # proportionate on 9:16, 16:9, 1:1 and 4:5 without a per-ratio asset —
        # height-bound on the wide ratios, edge-to-edge on vertical.
        parts.append(f"[{card_idx}:v]scale={cw}:{ch}:"
                     f"force_original_aspect_ratio=decrease,format=rgba[ocard]")
        parts.append("[obg][ocard]overlay=(W-w)/2:(H-h)/2:shortest=0[ocomp]")
        fi = min(config.OUTRO_FADE_IN_S, outro_s / 3)
        fo = min(config.OUTRO_FADE_OUT_S, outro_s / 3)
        parts.append(f"[ocomp]fade=t=in:st=0:d={fi:.2f},"
                     f"fade=t=out:st={outro_s - fo:.2f}:d={fo:.2f},"
                     f"format=yuv420p,setsar=1[ovid]")
        v_final = "vprog"
    else:
        parts.append(f"[{vlabel}]format=yuv420p[vout]")

    # program audio: duck under active voiceover, then mix music + voiceover
    alabel = "ac"
    if tail_ext > 0.05:
        # The mix is duration=first keyed to the program audio — pad it with
        # silence to the extended duration or every tail note is cut off.
        parts.append(f"[ac]apad=pad_dur={tail_ext:.3f}[acx]")
        alabel = "acx"
    duck_wins = merge_spans(
        [(max(0.0, float(vo["start_output_s"])),
          min(tl.out_duration, float(vo["start_output_s"]) + vd))
         for _idx, vo, vd in vo_inputs if vo.get("duck_others", True)], 0.05)
    duck_wins = [(s, e) for s, e in duck_wins if e - s > 0.05]
    if duck_wins:
        parts.append(f"[{alabel}]volume={DUCK_DB}dB:"
                     f"enable='{_enable_expr(duck_wins)}'[aduck]")
        alabel = "aduck"

    mix_labels = []
    # Smooth (sidechain) ducking: each opted-in music item compresses
    # against a copy of the program audio, so the bed dips WITH the voice
    # and swells back in the gaps instead of the legacy -12dB step. Split
    # the program feed once, before the mix consumes it.
    smooth_js = [j for j, (_i, item, _d) in enumerate(music_inputs or [])
                 if item.get("duck", True)
                 and item.get("duck_mode") == "smooth"]
    if smooth_js:
        taps = "".join(f"[dref{j}]" for j in smooth_js)
        parts.append(f"[{alabel}]asplit={len(smooth_js) + 1}"
                     f"[aduckm]{taps}")
        alabel = "aduckm"
    if music_inputs:
        speech = _speech_spans_out(index, tl)
        for j, (input_idx, item, track_dur) in enumerate(music_inputs):
            m_start = max(0.0, min(item["start"], total_dur - 0.05))
            m_end = max(m_start + 0.05, min(item["end"], total_dur))
            dur = m_end - m_start
            # Offset seeks INTO the track — start on the drop instead of the
            # intro. With -stream_loop the trim window runs straight across
            # repeats, so this one atrim expresses both "seek in" and
            # "loop until the span is full".
            off = max(0.0, float(item.get("offset_s") or 0.0))
            if track_dur and off >= track_dur - 0.05:
                off = 0.0          # past the end would render pure silence
            smooth = j in smooth_js
            duck = ""
            if item.get("duck", True) and not smooth and speech:
                win = [(max(s, m_start), min(e, m_end)) for s, e in speech
                       if min(e, m_end) - max(s, m_start) > 0.05]
                if win:
                    duck = f",volume={DUCK_DB}dB:enable='{_enable_expr(win)}'"
            # Fades are the music item's OWN, and must land before adelay
            # while t=0 still means "the music's first sample". Clamped to
            # half the span so a 2s sting can't fade in past its own end.
            fades = ""
            fi = min(max(0.0, float(item.get("fade_in_s") or 0.0)), dur / 2)
            fo = min(max(0.0, float(item.get("fade_out_s") or 0.0)), dur / 2)
            if fi > 0.01:
                fades += f",afade=t=in:st=0:d={fi:.2f}"
            if fo > 0.01:
                fades += (f",afade=t=out:st={max(0.0, dur - fo):.2f}"
                          f":d={fo:.2f}")
            delay_ms = int(m_start * 1000)
            delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
            parts.append(
                f"[{input_idx}:a]atrim=start={off:.3f}:end={off + dur:.3f},"
                f"asetpts=PTS-STARTPTS{fades},"
                f"volume={item.get('gain_db', -18)}dB,"
                f"aresample=48000{delay}{duck}[mus{j}]")
            if smooth:
                # After adelay both streams share the program clock, so the
                # compressor reacts to the words playing at that instant.
                # threshold 0.03 ~= -30dBFS: real speech, not room tone.
                parts.append(f"[mus{j}][dref{j}]sidechaincompress="
                             f"threshold=0.03:ratio=12:attack=180:"
                             f"release=550[musc{j}]")
                mix_labels.append(f"[musc{j}]")
            else:
                mix_labels.append(f"[mus{j}]")
    for j, (input_idx, vo, vd) in enumerate(vo_inputs):
        delay_ms = int(max(0.0, float(vo["start_output_s"])) * 1000)
        delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        parts.append(f"[{input_idx}:a]volume={vo.get('gain_db', 0.0)}dB,"
                     f"aresample=48000{delay}[vo{j}]")
        mix_labels.append(f"[vo{j}]")
    for j, (input_idx, item, _sdur) in enumerate(sfx_inputs or []):
        at = max(0.0, min(float(item.get("at") or 0.0), tl.out_duration))
        delay_ms = int(at * 1000)
        delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        # No ducking and no atrim, unlike music. An accent that dips under the
        # very word it is punctuating is not an accent, and a one-shot plays
        # for exactly as long as the file is — amix's duration=first already
        # stops a late boom from running past the end of the programme.
        parts.append(f"[{input_idx}:a]volume={item.get('gain_db', -6.0)}dB,"
                     f"aresample=48000{delay}[sfx{j}]")
        mix_labels.append(f"[sfx{j}]")

    outro_on = outro_here          # one predicate, so the video and audio
    loud = (master or {}).get("loudness") == "social"
    a_prog = "aprog" if (outro_on or loud) else "aout"
    a_final = "apre" if (fade_in or fade_out or outro_on) else a_prog
    if mix_labels:
        parts.append(f"[{alabel}]" + "".join(mix_labels) +
                     f"amix=inputs={1 + len(mix_labels)}:duration=first:"
                     f"normalize=0[{a_final}]")
    else:
        parts.append(f"[{alabel}]anull[{a_final}]")
    if a_final == "apre":
        # Deliberately NO limiter on the sfx mix. The obvious guard against a
        # one-shot summing past 0 dBFS is an alimiter, but alimiter has 5ms of
        # lookahead and therefore DELAYS the whole programme audio by 5ms
        # against the picture — measured, by differencing two renders that
        # should have been identical outside the sfx. Trading a global A/V
        # offset for a hypothetical transient clip is a bad deal, and the
        # pipeline already sums voiceover at 0 dB with no limiter. Headroom is
        # handled where it belongs instead: the pack is normalized to -16 LUFS
        # and sfx default to -6 dB, so the loudest one peaks near -7 dBFS.
        chain = []
        if fade_in:
            chain.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out:
            st = max(0.0, total_dur - fade_out)
            chain.append(f"afade=t=out:st={st:.2f}:d={fade_out:.2f}")
        elif outro_on:
            # Without this the programme's music or speech cuts dead into the
            # card's silence. Skipped when the EDL sets its own fade_out,
            # which already lands the programme in silence.
            d = min(config.OUTRO_AUDIO_TAIL_FADE_S, total_dur / 2)
            if d > 0.01:
                chain.append(f"afade=t=out:st={total_dur - d:.2f}"
                             f":d={d:.2f}")
        parts.append(f"[apre]{','.join(chain) or 'anull'}[{a_prog}]")

    if loud:
        # Master loudness: -14 LUFS / -1.5 dBTP (the social/streaming
        # target), single-pass dynamic loudnorm on the PROGRAM only — the
        # end card's silence must not drag the integrated measurement, and
        # normalizing before the concat keeps it out. loudnorm internally
        # resamples to 192k, so the format is pinned back after.
        nxt = "amst" if outro_on else "aout"
        parts.append(f"[{a_prog}]loudnorm=I=-14:TP=-1.5:LRA=11,"
                     f"{AUDIO_NORM}[{nxt}]")
        a_prog = nxt

    if outro_on:
        parts.append(f"anullsrc=r=48000:cl=stereo:d={outro_s:.3f},"
                     "aformat=sample_fmts=fltp:channel_layouts=stereo[osil]")
        shrink = preview and _needs_preview_downscale(H)
        cat_v = "vcat" if shrink else "vout"
        parts.append(f"[{v_final}][{a_prog}][ovid][osil]"
                     f"concat=n=2:v=1:a=1[{cat_v}][aout]")
        if shrink:
            parts.append(rf"[vcat]scale=-2:min({config.PREVIEW_MAX_HEIGHT}\,"
                         r"floor(ih/2)*2),format=yuv420p[vout]")

    return ";".join(parts)


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _render_canvas_edl(edl_dict, out_path, workdir, preview, progress_cb=None,
                       want_wm=False, cancelled_cb=None):
    """Render a canvas program (round 34): a timeline with NO main video, where
    the ordered inserts (clips/images) are concatenated on the canvas, plus
    music / sfx / voiceover / manual captions / effects. Mirrors render_edl but
    assembles the ffmpeg inputs with NO input [0] main video — every input
    (silence, music, sfx, inserts, voiceover, end card) starts at index 0 — and
    takes the output geometry from the canvas rather than probing a source."""
    edl = validate_edl(edl_dict).model_dump()
    canvas = edl["canvas"]
    W, H = int(canvas["width"]), int(canvas["height"])
    fps = max(1.0, min(float(canvas.get("fps") or 30.0), 60.0))
    if preview:
        W, H, fps = preview_geometry(W, H, fps)

    inserts = edl.get("inserts") or []
    voiceover = edl.get("voiceover") or []
    # keep=[] -> Timeline.out_duration == sum of insert durations (the program).
    tl = Timeline(edl["keep"], inserts)
    ass_path = caplib.build_ass(edl, {}, tl,
                                os.path.join(workdir, "captions.ass"),
                                play_res=(W, H))
    gfx_path = graphics.build_gfx_ass(edl, tl.out_duration,
                                      os.path.join(workdir, "graphics.ass"),
                                      play_res=(W, H))

    def _fetch(key, tag, idx):
        cached = _cached_source(key)
        if cached:
            return cached
        local = os.path.join(workdir, f"{tag}_{idx}"
                             + os.path.splitext(key)[1].lower())
        storage.download_to(key, local)
        return local

    music_inputs, insert_inputs, vo_inputs, sfx_inputs = [], [], [], []
    extra_inputs = []
    next_idx = 0                       # no main video: inputs start at [0]

    # Shared anullsrc: the program audio base and the silence under image
    # inserts / silent clips. Always present on a canvas program.
    max_len = tl.out_duration + 1
    extra_inputs += ["-f", "lavfi", "-t", f"{max_len:.2f}",
                     "-i", "anullsrc=r=48000:cl=stereo"]
    silence_idx = next_idx
    next_idx += 1

    for item in edl.get("music", []):
        # Round 79i — a MUTED piece is the other half of A/B listening:
        # on the timeline, silent, skipped before it is fetched. (79j made
        # the beyond-the-program skip obsolete: the render now EXTENDS to
        # cover unmuted music, so those pieces play over black.)
        if item.get("mute"):
            continue
        local = music_source(item["storage_key"],
                             lambda k: _fetch(k, "music", next_idx))
        # Round 79L — the item may be a VIDEO whose soundtrack plays (a song
        # dropped on the music lane); a file with NO audio stream is skipped
        # or the graph would reference a stream that does not exist.
        if not media.has_audio_stream(local):
            continue
        try:
            track_dur = media.probe_audio_duration(local)
        except Exception:
            track_dur = None
        span = max(0.05, float(item.get("end") or 0.0)
                   - float(item.get("start") or 0.0))
        offset = max(0.0, float(item.get("offset_s") or 0.0))
        if (item.get("loop") and track_dur
                and (track_dur - offset) < span - 0.05):
            extra_inputs += ["-stream_loop", "-1"]
        extra_inputs += ["-i", local]
        music_inputs.append((next_idx, item, track_dur))
        next_idx += 1

    for item in edl.get("sfx", []):
        local = sfx_source(item["storage_key"],
                           lambda k: _fetch(k, "sfx", next_idx))
        extra_inputs += ["-i", local]
        sfx_inputs.append((next_idx, item, None))
        next_idx += 1

    for item in inserts:               # sorted by validate_edl = tl.ins order
        local = _fetch(item["asset_key"], "insert", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
            has_ins_audio = False
        else:
            extra_inputs += ["-i", local]
            # mute (round 78): a muted scene takes the silence branch, as if
            # the clip never had a track. anullsrc is guaranteed whenever any
            # insert exists, on both program paths.
            has_ins_audio = media.probe(local)["has_audio"] \
                and not item.get("mute")
        insert_inputs.append((next_idx, item, has_ins_audio))
        next_idx += 1

    for item in voiceover:
        local = _fetch(item["asset_key"], "vo", next_idx)
        extra_inputs += ["-i", local]
        vo_dur = media.probe_audio_duration(local)
        vo_inputs.append((next_idx, item, vo_dur))
        next_idx += 1

    overlay_inputs = []
    for item in edl.get("overlays") or []:
        local = _fetch(item["asset_key"], "overlay", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
        else:
            extra_inputs += ["-i", local]
        overlay_inputs.append((next_idx, item))
        next_idx += 1

    outro_s = outro_seconds(preview)
    card_idx = None
    if outro_s > 0.0:
        extra_inputs += ["-loop", "1", "-t", f"{outro_s:.3f}",
                         "-r", f"{fps:.3f}", "-i", endcard_path()]
        card_idx = next_idx
        next_idx += 1

    robot_idx, wm_ass_path = None, None
    if want_wm:
        extra_inputs += ["-loop", "1",
                         "-t", f"{tl.out_duration + 5.0:.3f}",
                         "-r", f"{fps:.3f}", "-i", robot_path()]
        robot_idx = next_idx
        next_idx += 1
        wm_ass_path = build_watermark_ass(
            os.path.join(workdir, "watermark.ass"), tl.out_duration, W, H)

    plate_idx, plate_box = _screen_frame_input(
        edl, workdir, W, H, fps, tl.out_duration, extra_inputs, next_idx)
    if plate_idx is not None:
        next_idx += 1

    graph = build_filtergraph(edl, tl.out_duration, False, tl, ass_path,
                              music_inputs, {}, preview,
                              W=W, H=H, fps=fps, frame_mode=None,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx,
                              src_w=W, src_h=H, src_pad=0.0,
                              sfx_inputs=sfx_inputs, outro_s=outro_s,
                              card_idx=card_idx, src_sar=1.0, src_fps=fps,
                              overlay_inputs=overlay_inputs,
                              gfx_ass_path=gfx_path, robot_idx=robot_idx,
                              wm_ass_path=wm_ass_path,
                              plate_idx=plate_idx, plate_box=plate_box)

    if preview:
        encode = ["-c:v", "libx264", "-preset", config.PREVIEW_PRESET,
                  "-crf", "27", "-g", "48", "-keyint_min", "24",
                  "-c:a", "aac", "-b:a", "128k"]
    else:
        encode = ["-c:v", "libx264", "-preset", config.FINAL_PRESET,
                  "-crf", str(config.FINAL_CRF), "-g", "120",
                  "-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", *extra_inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode, *_output_clock(fps), "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    media.run(cmd, progress_cb=progress_cb,
              expected_out_s=tl.out_duration
                              + music_tail_ext(edl, tl.out_duration) + outro_s,
              cancelled_cb=cancelled_cb)
    return media.duration_of(out_path)


def _prune_graph_to_audio(graph, target="aout"):
    """The filter_complex with every chain the audio output does not need
    removed — the whole video pipeline drops away, and with it its cost.

    The audio chains and the video chains of our graphs never share a filter
    node (they share INPUT FILES only), so backward reachability from the
    audio output label yields a self-contained audio graph whose semantics
    are byte-identical to the full render's audio — which is the entire
    point: render_edl(audio_only=True) must produce EXACTLY the track the
    full render would have muxed (round 97, timeline-stitched previews).

    The one node our graphs genuinely share across domains is the segment
    `concat=v=1:a=1`, which eats interleaved [v][a] pairs and produces both
    a video and an audio output from one filter. When only its audio side
    is needed, the node is REWRITTEN to `v=0` with just its audio inputs and
    outputs — and the whole video decode/trim tree above it prunes away on
    the second reachability pass, so the audio render never decodes a frame.

    Raises when the pruned graph would still leave a dangling output — an
    unexpected cross-domain filter — and the caller's fallback is the full
    render: pruning failures cost speed, never correctness."""

    def _parse(g):
        out = []
        for chain in g.split(";"):
            s = chain.strip()
            if not s:
                continue
            ins = []
            while True:
                m = re.match(r"^\[([^\]]+)\]", s)
                if not m:
                    break
                ins.append(m.group(1))
                s = s[m.end():]
            outs = []
            while True:
                m = re.search(r"\[([^\]]+)\]$", s)
                if not m:
                    break
                outs.append(m.group(1))
                s = s[:m.start()].rstrip()
            out.append((chain.strip(), ins, list(reversed(outs)), s))
        return out

    def _reach(parsed):
        need = {target}
        keep = [False] * len(parsed)
        changed = True
        while changed:
            changed = False
            for i, (_c, ins, outs, _f) in enumerate(parsed):
                if not keep[i] and any(o in need for o in outs):
                    keep[i] = True
                    need.update(ins)
                    changed = True
        return keep, need

    parsed = _parse(graph)
    keep, need = _reach(parsed)

    rewritten = []
    for i, (chain, ins, outs, body) in enumerate(parsed):
        if not keep[i]:
            rewritten.append(chain)                 # dropped on second pass
            continue
        m = re.match(r"^concat(?:=(\S*))?$", body)
        if m:
            args = dict(kv.split("=", 1) for kv in (m.group(1) or "").split(
                ":") if "=" in kv)
            n = int(args.get("n", 2))
            nv = int(args.get("v", 1))
            na = int(args.get("a", 0))
            v_outs, a_outs = outs[:nv], outs[nv:nv + na]
            if na > 0 and nv > 0 and not any(o in need for o in v_outs):
                a_ins = []
                for seg in range(n):
                    base = seg * (nv + na)
                    a_ins += ins[base + nv:base + nv + na]
                rewritten.append(
                    "".join(f"[{x}]" for x in a_ins)
                    + f"concat=n={n}:v=0:a={na}"
                    + "".join(f"[{x}]" for x in a_outs))
                continue
        rewritten.append(chain)
    parsed = _parse(";".join(rewritten))
    keep, need = _reach(parsed)
    kept = [p for i, p in enumerate(parsed) if keep[i]]
    if not kept:
        raise media.MediaError(f"audio-only prune found no [{target}]")
    consumed = set()
    for _c, ins, _outs, _f in kept:
        consumed.update(ins)
    for _c, _ins, outs, _f in kept:
        for o in outs:
            if o != target and o not in consumed:
                raise media.MediaError(
                    f"audio-only prune left [{o}] dangling")
    return ";".join(c for c, _i, _o, _f in kept)


def _resolve_asset_local(key, asset_locals, fallback):
    """Return a known byte-identical local path before fetching ``key``."""
    aliased = (asset_locals or {}).get(key)
    if aliased and os.path.exists(aliased):
        return aliased
    return fallback(key)


def _repair_legacy_insert_boundaries(edl_dict):
    """Snap only legacy off-boundary inserts so an old broken EDL can render.

    The backend used the last kept SOURCE timestamp when a trimmed project
    submitted a second tray.  Those versions are already in production and a
    schema fix cannot rewrite history.  Rendering that exact legacy signature
    at the edited end is what the corrected submit path would have done; every
    other validation rule remains strict in ``validate_edl`` below.
    """
    if is_canvas_program(edl_dict):
        return edl_dict
    bounds = keep_boundaries(edl_dict.get("keep") or [],
                             edl_dict.get("speed") or [])
    if not bounds:
        return edl_dict
    try:
        legacy_source_end = float((edl_dict.get("keep") or [])[-1][1])
    except (TypeError, ValueError, IndexError):
        return edl_dict
    edited_end = bounds[-1]
    repaired = None
    for n, item in enumerate(edl_dict.get("inserts") or []):
        try:
            at = float(item.get("at_output_s"))
        except (TypeError, ValueError, AttributeError):
            continue                      # normal validation names the defect
        if any(abs(boundary - at) <= 0.02 for boundary in bounds):
            continue
        # The old path always wrote keep[-1][1]. Do not disguise any other
        # invalid anchor as legacy data; normal validation must reject it.
        if abs(at - legacy_source_end) > 0.02:
            continue
        if repaired is None:
            repaired = dict(edl_dict)
            repaired["inserts"] = [dict(x)
                                   for x in edl_dict.get("inserts") or []]
        repaired["inserts"][n]["at_output_s"] = edited_end
        print(f"[render] recovered legacy insert {item.get('id', n)}: "
              f"{at:g}s -> boundary {edited_end:g}s", flush=True)
    return repaired or edl_dict


def render_edl(edl_dict, index, src_path, out_path, workdir, preview,
               progress_cb=None, want_wm=False, cancelled_cb=None,
               patch_locals=None, cap_ass_override=None,
               suppress_outro=False, cap_burn_offset=None,
               audio_only=False, asset_locals=None):
    """Render an EDL against a source file. Returns output duration (s).

    patch_locals (round 92): {patch id: local file} for the EDL's `patches` —
    repainted windows the graph overlays on the source clock before anything
    else touches the frames. Resolved by run_render_job (which picks the
    proxy-res clip for previews and materializes the full-res twin for
    finals); a patch with no local file is skipped with a log line rather
    than failing the render — the un-patched source is the honest fallback.

    audio_only (round 97): build the IDENTICAL graph, prune it to the audio
    output, and encode just the track (AAC in an mp4/m4a container). Used by
    timeline-mode stitched previews, whose video is spliced from stream
    copies and windowed re-encodes but whose audio must be rebuilt whole —
    audio cannot be spliced (adaptive loudnorm, output-anchored music, no
    clean AAC cut points). Same inputs, same filters, same order: the track
    is the one the full render would have produced, by construction.
    """
    if is_canvas_program(edl_dict):
        # No main video: the program is built on the canvas from inserts alone.
        return _render_canvas_edl(edl_dict, out_path, workdir, preview,
                                  progress_cb, want_wm=want_wm,
                                  cancelled_cb=cancelled_cb)
    info = media.probe(src_path)
    src_dur = info["duration"]
    render_dict = _repair_legacy_insert_boundaries(edl_dict)
    edl = validate_edl(render_dict,
                       max(src_dur, max(e for _, e in render_dict["keep"]))
                       ).model_dump()

    frame = edl.get("frame") or None
    W, H = frame_dims(info["width"], info["height"],
                      (frame or {}).get("ratio"))
    frame_mode = (frame or {}).get("mode", "crop") if frame else None
    frame_focus = ((frame.get("focus_x"), frame.get("focus_y"))
                   if frame and (frame.get("focus_x") is not None or
                                 frame.get("focus_y") is not None) else None)
    fps = max(1.0, min(float(info["fps"]) or 30.0, 60.0))
    if preview:
        W, H, fps = preview_geometry(W, H, fps)

    inserts = edl.get("inserts") or []
    voiceover = edl.get("voiceover") or []
    tl = Timeline(edl["keep"], inserts, edl.get("speed"))
    # A stitched-preview PIECE (round 93) burns captions from the FULL
    # program's ASS, time-shifted by the stitcher — the piece's own windowed
    # timeline would regroup caption lines at its edges. "" means the caller
    # decided nothing burns here.
    if cap_ass_override is not None:
        ass_path = cap_ass_override or None
    else:
        ass_path = caplib.build_ass(edl, index, tl,
                                    os.path.join(workdir, "captions.ass"),
                                    play_res=(W, H))
    # TWO text layers, not one. A behind-subject text is burned early (under the
    # subject); everything else is burned last (over everything). Splitting the
    # LIST rather than teaching graphics.py about depth keeps the ASS builder
    # exactly as it was — including its concurrent-text stacking, which is now
    # per-layer: a behind text and a front text that overlap in time no longer
    # de-stack against each other, which is the right trade (they are at
    # different depths, so they read as different planes anyway).
    behind_texts = [t for t in (edl.get("texts") or []) if t.get("behind")]
    front_texts = [t for t in (edl.get("texts") or []) if not t.get("behind")]
    gfx_path = graphics.build_gfx_ass(dict(edl, texts=front_texts),
                                      tl.out_duration,
                                      os.path.join(workdir, "graphics.ass"),
                                      play_res=(W, H))

    def _fetch(key, tag, idx):
        cached = _resolve_asset_local(key, asset_locals, _cached_source)
        if cached:
            return cached
        local = os.path.join(workdir, f"{tag}_{idx}"
                             + os.path.splitext(key)[1].lower())
        storage.download_to(key, local)
        return local

    music_inputs, insert_inputs, vo_inputs, sfx_inputs = [], [], [], []
    extra_inputs = []
    next_idx = 1

    # Repainted windows (round 92) — one short clip per patch, overlaid on
    # the source clock BEFORE segments/speed/zoom/grade, so every later
    # stage sees the repainted pixels exactly as it would a cleaned source.
    patch_inputs = []
    for item in edl.get("patches") or []:
        local = (patch_locals or {}).get(item["id"])
        if not local or not os.path.exists(local):
            print(f"[render] patch {item['id']} has no local clip — "
                  "rendering that window un-repainted", flush=True)
            continue
        extra_inputs += ["-i", local]
        patch_inputs.append((next_idx, item))
        next_idx += 1

    # Separated stems (round 97): when the EDL rebalances music vs voice,
    # the two stem files become inputs and build_filtergraph premixes them
    # in place of the original track. Any failure here DEGRADES to the
    # original audio with a log line — a missing stem object must never
    # fail a render the untouched track can honestly serve.
    stem_inputs = None
    sm = edl.get("stem_mix") or None
    if sm:
        try:
            v_local = _fetch(sm["vocals_key"], "stemv", next_idx)
            a_local = _fetch(sm["accomp_key"], "stema", next_idx + 1)
            extra_inputs += ["-i", v_local, "-i", a_local]
            stem_inputs = (next_idx, next_idx + 1,
                           float(sm.get("voice_gain_db") or 0.0),
                           float(sm.get("music_gain_db") or 0.0))
            next_idx += 2
        except Exception as e:
            print(f"[render] stem_mix set but stems unavailable "
                  f"({str(e)[:120]}) — rendering the original audio",
                  flush=True)
            stem_inputs = None

    # one shared anullsrc covers a silent main track AND silent insert blocks
    needs_silence = (not info["has_audio"]) or any(
        i["kind"] == "image" for i in inserts) or bool(inserts)
    silence_idx = None
    if needs_silence:
        max_len = max(src_dur, tl.out_duration) + 1
        extra_inputs += ["-f", "lavfi", "-t", f"{max_len:.2f}",
                         "-i", "anullsrc=r=48000:cl=stereo"]
        silence_idx = next_idx
        next_idx += 1

    for item in edl.get("music", []):
        # Round 79i — a MUTED piece is the other half of A/B listening:
        # on the timeline, silent, skipped before it is fetched. (79j made
        # the beyond-the-program skip obsolete: the render now EXTENDS to
        # cover unmuted music, so those pieces play over black.)
        if item.get("mute"):
            continue
        local = music_source(item["storage_key"],
                             lambda k: _fetch(k, "music", next_idx))
        # Round 79L — the item may be a VIDEO whose soundtrack plays (a song
        # dropped on the music lane); a file with NO audio stream is skipped
        # or the graph would reference a stream that does not exist.
        if not media.has_audio_stream(local):
            continue
        try:
            track_dur = media.probe_audio_duration(local)
        except Exception:
            track_dur = None      # unknown: never loop, just play what's there
        span = max(0.05, float(item.get("end") or 0.0)
                   - float(item.get("start") or 0.0))
        offset = max(0.0, float(item.get("offset_s") or 0.0))
        # -stream_loop repeats the file at the demuxer, so a short track can
        # fill a long span. Only ask for it when the track genuinely cannot
        # cover the span from its offset: looping is a MUSICAL compromise (the
        # seam lands wherever the phrase happens to end), so we never pay it
        # when the track is long enough. Deliberately not aloop, which buffers
        # the whole track in RAM — this worker has OOM-crashed before, and
        # measured output was identical (no gaps, no seam discontinuity).
        # Opt-IN, never defaulted on: an EDL written before loop existed must
        # render exactly as it always did, or a cached render and a fresh one
        # of the SAME version would differ. add_music opts new music in.
        if (item.get("loop") and track_dur
                and (track_dur - offset) < span - 0.05):
            extra_inputs += ["-stream_loop", "-1"]
        extra_inputs += ["-i", local]
        music_inputs.append((next_idx, item, track_dur))
        next_idx += 1

    for item in edl.get("sfx", []):
        local = sfx_source(item["storage_key"],
                           lambda k: _fetch(k, "sfx", next_idx))
        # No duration probe, unlike music: nothing in the graph needs it (a
        # one-shot is never trimmed or looped, and amix's duration=first
        # already stops a late tail overrunning the programme). Probing would
        # spawn an ffprobe per sound per render on a ~1 vCPU box for a number
        # that is then discarded. add_sfx warns about an over-long tail at
        # write time, where the duration is already known.
        extra_inputs += ["-i", local]
        sfx_inputs.append((next_idx, item, None))
        next_idx += 1

    for item in inserts:                      # sorted by validate_edl = tl.ins order
        local = _fetch(item["asset_key"], "insert", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
            has_ins_audio = False
        else:
            extra_inputs += ["-i", local]
            # mute (round 78): a muted scene takes the silence branch, as if
            # the clip never had a track. anullsrc is guaranteed whenever any
            # insert exists, on both program paths.
            has_ins_audio = media.probe(local)["has_audio"] \
                and not item.get("mute")
        insert_inputs.append((next_idx, item, has_ins_audio))
        next_idx += 1

    for item in voiceover:
        local = _fetch(item["asset_key"], "vo", next_idx)
        extra_inputs += ["-i", local]
        vo_dur = media.probe_audio_duration(local)
        vo_inputs.append((next_idx, item, vo_dur))
        next_idx += 1

    overlay_inputs = []
    for item in edl.get("overlays") or []:
        local = _fetch(item["asset_key"], "overlay", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
        else:
            extra_inputs += ["-i", local]
        overlay_inputs.append((next_idx, item))
        next_idx += 1

    # Finals render from the ORIGINAL, previews from the proxy — and the proxy
    # already holds its last frame across a short picture track. Without the
    # same hold here the two would disagree: the user approves a preview and
    # exports something else.
    src_pad = 0.0
    vdur = info.get("video_duration")
    if vdur and src_dur - vdur > max(media.PROXY_SHORT_MIN_S,
                                     media.PROXY_SHORT_FRAC * src_dur):
        src_pad = src_dur - vdur

    # The end card is its own ffmpeg input — no filter conjures a bundled PNG
    # out of nothing. -loop 1 -t gives it a real duration and framerate so the
    # overlay does not depend on eof_action to hold a single frame.
    # A stitched-preview piece suppresses it: the card belongs to the end of
    # the PROGRAM, and the tail that carries it is stream-copied from the
    # previous preview.
    outro_s = 0.0 if suppress_outro else outro_seconds(preview)
    card_idx = None
    if outro_s > 0.0:
        extra_inputs += ["-loop", "1", "-t", f"{outro_s:.3f}",
                         "-r", f"{fps:.3f}", "-i", endcard_path()]
        card_idx = next_idx
        next_idx += 1

    # Same deal for the free-tier robot: a looped still input, held for the
    # PROGRAM's length (not the outro's — the mark stops before the card).
    robot_idx, wm_ass_path = None, None
    if want_wm:
        extra_inputs += ["-loop", "1",
                         "-t", f"{tl.out_duration + 5.0:.3f}",
                         "-r", f"{fps:.3f}", "-i", robot_path()]
        robot_idx = next_idx
        next_idx += 1
        wm_ass_path = build_watermark_ass(
            os.path.join(workdir, "watermark.ass"), tl.out_duration, W, H)

    plate_idx, plate_box = _screen_frame_input(
        edl, workdir, W, H, fps, tl.out_duration, extra_inputs, next_idx)
    if plate_idx is not None:
        next_idx += 1

    # ---- behind-subject text: one mask input + one ASS per item -------------
    # The window comes from the mask's SOURCE span, not from the text's program
    # window, because the mask is pixels of particular footage and has to land
    # wherever that footage now plays. tl.span_to_out is the same map captions
    # and duck windows use, so a cut made after the mask was measured moves the
    # composite with the shot instead of leaving it behind.
    #
    # Every failure here DEGRADES to an ordinary title rather than failing the
    # render: footage cut away, a mask object that will not download, an ASS
    # with nothing to burn. A user who loses the depth still gets their words.
    behind_inputs = []
    for bi, item in enumerate(behind_texts):
        b = item["behind"]
        pieces = tl.span_to_out(float(b["src_start"]), float(b["src_end"]))
        ass = None
        if pieces:
            ass = graphics.build_gfx_ass(
                dict(edl, texts=[item]), tl.out_duration,
                os.path.join(workdir, f"behind_{bi}.ass"), play_res=(W, H))
        if not pieces or not ass:
            print(f"[render] behind-text {item.get('id')}: "
                  f"{'its footage is no longer in the edit' if not pieces else 'nothing to burn'}"
                  f" — burning it as a plain title", flush=True)
            front_texts.append(item)
            continue
        try:
            local = _fetch(b["asset_key"], "matte", next_idx)
        except Exception as e:
            print(f"[render] behind-text {item.get('id')}: mask unavailable "
                  f"({str(e)[:120]}) — burning it as a plain title", flush=True)
            front_texts.append(item)
            continue
        extra_inputs += ["-i", local]
        behind_inputs.append((next_idx, {"ass": ass}, (round(pieces[0][0], 3),
                                                      round(pieces[-1][1], 3))))
        next_idx += 1
    if behind_texts:
        # Rebuild the front layer — anything that degraded above belongs in it.
        gfx_path = graphics.build_gfx_ass(dict(edl, texts=front_texts),
                                          tl.out_duration,
                                          os.path.join(workdir,
                                                       "graphics.ass"),
                                          play_res=(W, H))

    graph = build_filtergraph(edl, src_dur, info["has_audio"], tl, ass_path,
                              music_inputs, index, preview,
                              W=W, H=H, fps=fps, frame_mode=frame_mode,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx,
                              src_w=info["width"], src_h=info["height"],
                              src_pad=src_pad, sfx_inputs=sfx_inputs,
                              outro_s=outro_s, card_idx=card_idx,
                              stem_inputs=stem_inputs,
                              src_sar=info.get("sar") or 1.0,
                              src_fps=float(info["fps"]) or fps,
                              overlay_inputs=overlay_inputs,
                              gfx_ass_path=gfx_path,
                              frame_focus=frame_focus, robot_idx=robot_idx,
                              wm_ass_path=wm_ass_path,
                              plate_idx=plate_idx, plate_box=plate_box,
                              behind_inputs=behind_inputs,
                              patch_inputs=patch_inputs,
                              cap_burn_offset=cap_burn_offset)

    if audio_only:
        # The same graph the full render would run, minus every chain the
        # audio does not need. Unconsumed inputs are probed but never
        # decoded, so the video pipeline's whole cost (the reason previews
        # were slow) drops away and this finishes in seconds.
        graph = _prune_graph_to_audio(graph)
        cmd = ["ffmpeg", "-y", "-i", src_path, *extra_inputs,
               "-filter_complex", graph, "-map", "[aout]",
               "-c:a", "aac", "-b:a", "128k" if preview else "192k",
               "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", out_path]
        media.run(cmd, progress_cb=progress_cb,
                  expected_out_s=tl.out_duration
                  + music_tail_ext(edl, tl.out_duration) + outro_s,
                  cancelled_cb=cancelled_cb)
        return media.probe_audio_duration(out_path)

    if preview:
        # Dense keyframes so Safari scrubbing lands precisely (~1.6s GOP).
        encode = ["-c:v", "libx264", "-preset", config.PREVIEW_PRESET,
                  "-crf", "27", "-g", "48", "-keyint_min", "24",
                  "-c:a", "aac", "-b:a", "128k"]
    else:
        # veryfast/CRF 20 is visually transparent for this content and cuts
        # export wall time hard vs the old medium/CRF 18 (see README timings).
        encode = ["-c:v", "libx264", "-preset", config.FINAL_PRESET,
                  "-crf", str(config.FINAL_CRF), "-g", "120",
                  "-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", "-i", src_path, *extra_inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode, *_output_clock(fps), "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    # Progress is percent-of-expected, so it must be the RENDERED length. Left
    # at the programme duration the bar hits 99.9% at programme end and then
    # flatlines through the whole end card.
    media.run(cmd, progress_cb=progress_cb,
              expected_out_s=tl.out_duration
                              + music_tail_ext(edl, tl.out_duration) + outro_s,
              cancelled_cb=cancelled_cb)
    return media.duration_of(out_path)


# ------------------------------------------------------------------ #
#  Job entrypoint (types: preview | final)                             #
# ------------------------------------------------------------------ #

def _output_clock(fps):
    """Output timing options for every render.

    Round 52. The rendered file's length should come from the filtergraph's
    TIMESTAMPS and nothing else. Left unpinned it also depends on the frame
    rate the last filter link happens to ADVERTISE, because ffmpeg's default
    frame handling re-times the stream to that declared rate — so a link whose
    declared rate disagrees with the PTS it is carrying stretches or squeezes
    the whole video, which is exactly what a wrong-length export is.

    Two real exports failed verification on 2026-07-27 — 102.40s for a 93.50s
    edit, 47.11s for a 45.38s one — and one user pressed Download ten times for
    the same message. BE PRECISE ABOUT WHAT IS KNOWN: their PREVIEWS were
    correct and repeatable, both stored EDLs render to the exact expected
    length here against synthetic CFR, VFR and short-picture-track sources, and
    the customers' originals are not reachable from a dev box, so the specific
    property of those two files was never reproduced. What IS established is
    the structural asymmetry — a preview renders from our own proxy, whose
    encode already pins `-fps_mode cfr -r` for anything it detected as VFR, and
    a final renders from the customer's original, which nothing pinned.

    So this closes the whole family rather than one instance: the output clock
    is now declared, both paths agree, and frames are duplicated or dropped
    only where the timestamps say to. If a length mismatch survives it,
    _stream_report names the offending stream in the worker log — which is the
    datum this investigation did not have.
    """
    return ["-fps_mode", "cfr", "-r", f"{max(1.0, min(float(fps or 30.0), 120.0)):.3f}"]


def _stream_report(path):
    """Per-stream durations, rates and frame counts of a rendered file, as one
    log line. Best-effort: a diagnostic must never be the thing that fails."""
    try:
        out = media.run(["ffprobe", "-v", "error", "-print_format", "json",
                         "-show_format", "-show_streams",
                         "-count_frames" if os.path.getsize(path) < 60_000_000
                         else "-show_format", path], timeout=120)
        data = json.loads(out)
    except Exception as e:
        return f"stream probe failed ({str(e)[:80]})"
    bits = [f"container={float(data.get('format', {}).get('duration') or 0):.2f}s"]
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind not in ("video", "audio"):
            continue
        piece = f"{kind}={float(st.get('duration') or 0):.2f}s"
        if kind == "video":
            piece += (f" r={st.get('r_frame_rate')} avg={st.get('avg_frame_rate')}"
                      f" frames={st.get('nb_read_frames') or st.get('nb_frames') or '?'}")
        bits.append(piece)
    return " ".join(bits)


def _verify_render(edl_json, out_path, out_dur, job_id, variant,
                   src_path=None, src_dur=None):
    """Fail a render whose output is the wrong length or newly-black. The EDL
    gives the expected program duration, but keep spans may legitimately extend
    past the real source content (render_edl validates against the larger of
    src_dur / max keep end, and ffmpeg's trim truncates at real content end),
    so each keep end is clamped to the actual source duration before computing
    the expectation — otherwise a container whose metadata overstates its
    content would falsely fail forever. The black check only fails when the
    output is black where the SOURCE was not, so legitimately-black uploads
    (podcast audio over a black screen) render fine. Raises media.MediaError on
    a real defect -> worker retries once, then surfaces."""
    keep = edl_json["keep"]
    if src_dur:
        keep = [[s, min(e, src_dur)] for s, e in keep if s < src_dur]
        keep = keep or edl_json["keep"]     # never let clamping empty it out
    program = Timeline(keep, edl_json.get("inserts") or [],
                       edl_json.get("speed")).out_duration
    # Round 79j — the render legitimately extends past the scenes when
    # unmuted music outlives them; the expectation must extend with it or
    # the verifier rejects exactly the length the graph was asked to build.
    tail = music_tail_ext(edl_json, program)
    program += tail
    # The rendered file is the programme PLUS the branded end card. The
    # tolerance does not absorb it: 2.5s exceeds max(0.75s, 3%) for anything
    # under ~83s, so without this every short export fails verification and
    # retries forever.
    outro = outro_seconds(variant == "preview")
    expected = program + outro
    tol = max(config.RENDER_DURATION_TOLERANCE_S,
              config.RENDER_DURATION_TOLERANCE_FRAC * expected)
    if abs(out_dur - expected) > tol:
        # Name the stream before failing. "The render is the wrong length" was
        # all a user got, ten times in a row, while pressing Download — and it
        # was all WE got too, which is why the cause took a day of forensics to
        # narrow. Which stream is long (and by how many frames) separates the
        # three possible causes — a stretched picture clock, an audio tail that
        # outruns the programme, or a genuinely mis-built graph — in one line.
        print(f"[render {job_id}] LENGTH MISMATCH {variant}: "
              f"{_stream_report(out_path)} | expected {expected:.2f}s "
              f"(programme {program:.2f}s + outro {outro:.2f}s)", flush=True)
        raise media.MediaError(
            f"{variant} render duration check failed: output is "
            f"{out_dur:.2f}s but the edit is {expected:.2f}s "
            f"(tolerance {tol:.2f}s) — the render is the wrong length")
    if out_dur > 1.0:
        # Measure the PROGRAMME only. The end card is black by design, and the
        # source it is compared against has none, so counting it is pure
        # unmatched numerator in the out_black - src_black comparison below.
        # The music tail is black BY DESIGN — measure the scenes only, or a
        # short video under a long song reads as "the render looks broken".
        prog_dur = max(0.1, out_dur - outro - tail)
        out_black = media.black_seconds(out_path, prog_dur) / prog_dur
        if out_black > config.RENDER_BLACK_MAX_RATIO:
            # The output is mostly black — but that's only a DEFECT if the
            # source wasn't. Probe the source (once, only in this rare case).
            src_black = 0.0
            if src_path and src_dur and src_dur > 1.0:
                src_black = media.black_seconds(src_path, src_dur) / src_dur
            elif src_dur is None and out_black < 0.98:
                # Canvas program (no source to compare): a lyric/caption or dark
                # program can be legitimately black. Only a near-total black
                # frame is a real defect, so treat anything less as intended.
                src_black = out_black
            if out_black - src_black > config.RENDER_BLACK_MAX_RATIO:
                raise media.MediaError(
                    f"{variant} render black-frame check failed: output is "
                    f"{100 * out_black:.0f}% black vs {100 * src_black:.0f}% in "
                    "the source — the render looks broken")
    print(f"[render {job_id}] verified {variant}: {out_dur:.2f}s "
          f"(expected {expected:.2f}s)", flush=True)


def _caption_index_fp(edl_json, index):
    """Fingerprint of the inputs that decide from_transcript caption TEXT.

    from_transcript captions are burned from the index words at render time, and
    the index row is mutable (self-heal re-index, Deepgram heal, transcript
    edits all upsert it in place). The render cache is otherwise keyed only by
    (version, sha), so a version once rendered against an empty/old transcript
    was served forever — a re-render was a silent no-op and captions never
    updated. Mixing this fingerprint into the cache guard invalidates exactly
    those renders. Returns None when captions don't depend on the transcript, so
    caption-off and explicit-item renders keep the cheap (version, sha) cache.
    """
    caps = edl_json.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return None
    h = hashlib.sha256()
    for w in (index.get("words") or []):
        h.update(f"{w.get('w', '')}|{w.get('t0')}|{w.get('t1')};"
                 .encode("utf-8"))
    return h.hexdigest()[:16]


def _prune_source_cache(cache_dir, protect=None):
    """Bound the persistent executor cache by age, item size and total bytes.

    Cloud Run reuses an instance between unrelated customers.  That makes a
    source cache valuable, but it also means a huge file from the previous job
    can consume the scratch reservation of the next one.  Concurrency is one,
    so no other request can be using an unprotected cache entry while this
    runs; ``protect`` keeps the file this call is about to return.
    """
    now = time.time()
    entries = []
    for fn in os.listdir(cache_dir):
        fp = os.path.join(cache_dir, fn)
        if fp == protect:
            continue
        try:
            st = os.stat(fp)
        except OSError:
            continue
        # Interrupted downloads have no reuse value.  The remaining rules
        # are inclusive limits: an exactly-4-GiB source is still cacheable.
        if ".part" in fn or now - st.st_mtime > 6 * 3600 \
                or st.st_size > config.SOURCE_CACHE_MAX_ITEM_BYTES:
            try:
                os.remove(fp)
            except OSError:
                pass
            continue
        entries.append((st.st_mtime, st.st_size, fp))

    protected_size = 0
    if protect:
        try:
            protected_size = os.path.getsize(protect)
        except OSError:
            protected_size = 0
    total = protected_size + sum(size for _mtime, size, _fp in entries)
    for _mtime, size, fp in sorted(entries):
        if total <= config.SOURCE_CACHE_MAX_BYTES:
            break
        try:
            os.remove(fp)
            total -= size
        except OSError:
            pass


def _cached_source(storage_key):
    """A sha-keyed local copy of an IMMUTABLE storage object (proxies are
    content-addressed, originals/clean sources never change under a key), so
    the second and third render of an agent turn stop re-downloading the
    same file. Returns a local path OUTSIDE any job workdir. Falls back to
    None on any failure — the caller downloads into its workdir as before.

    Round 84: an agent turn typically renders 2-3 times (edit → check → fix
    → check); the source download was the fixed tax on every one of them.
    """
    try:
        cache_dir = os.path.join(config.TMP_DIR, "srccache")
        os.makedirs(cache_dir, exist_ok=True)
        _prune_source_cache(cache_dir)
        name = hashlib.sha256(storage_key.encode()).hexdigest()[:32] + \
            os.path.splitext(storage_key)[1]
        local = os.path.join(cache_dir, name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            os.utime(local, None)      # LRU touch
            return local
        object_size = storage.object_bytes(storage_key)
        # Unknown-size objects take the ordinary workdir path, where the
        # download remains job-owned and is removed in finally.  Large files
        # do the same: caching them is precisely what starved the next final.
        if not object_size or object_size > config.SOURCE_CACHE_MAX_ITEM_BYTES:
            return None
        tmp = local + f".part{uuid.uuid4().hex[:6]}"
        storage.download_to(storage_key, tmp)
        os.replace(tmp, local)
        _prune_source_cache(cache_dir, protect=local)
        return local
    except Exception as e:
        print(f"[render] source cache miss-path failed ({e}) — "
              "downloading into the workdir", flush=True)
        return None


def _fetch_into(workdir, key, tag):
    """Plain download into the job workdir — the fallback when the sha cache
    is unavailable. Raises on failure; callers decide how loud to be."""
    local = os.path.join(workdir, f"fetch_{tag}"
                         + os.path.splitext(key)[1].lower())
    storage.download_to(key, local)
    return local


def _timeline_stitch(job_id, prev_edl, new_edl, tl_prev, tl_new, index,
                     src_local, workdir, patch_locals, out_path, prev_asset,
                     duration):
    """Timeline-mode stitch (round 97): the edit users make MOST — a trim, a
    cut, a splice, a music change — used to force the full re-render every
    time (round 96c: 134-206s per preview, 13 times in one real session).
    Here the new program is matched span-by-span against the previous
    preview (stitch.plan_timeline), matched spans stream-copy FROM THEIR OLD
    POSITION, only genuinely-changed seconds re-encode, and the audio track
    is rebuilt whole through the pruned render graph (audio cannot be
    spliced; rebuilding it costs seconds). Returns the output duration or
    None — the caller then runs the full render, which is always correct."""
    dur_out = tl_new.out_duration
    if outro_seconds(True) > 0:
        return None                    # previews with an end card: rare, out
    if music_tail_ext(new_edl, dur_out) > 0 or \
            music_tail_ext(prev_edl, tl_prev.out_duration) > 0:
        return None                    # music outliving the program moves art
    if is_canvas_program(new_edl) or is_canvas_program(prev_edl):
        return None

    info = media.probe(src_local)
    W, H = frame_dims(info["width"], info["height"],
                      (new_edl.get("frame") or {}).get("ratio"))
    fps = max(1.0, min(float(info["fps"]) or 30.0, 60.0))
    W, H, fps = preview_geometry(W, H, fps)

    # Both programs' burned captions, with payloads: plan_timeline PAIRS the
    # events modulo each run's shift and re-encodes any span where the two
    # programs would not burn the same picture. Never assumed — checked.
    cap_new = caplib.build_ass(new_edl, index, tl_new,
                               os.path.join(workdir, "stitch_cap.ass"),
                               play_res=(W, H))
    cap_prev = caplib.build_ass(prev_edl, index, tl_prev,
                                os.path.join(workdir, "stitch_cap_prev.ass"),
                                play_res=(W, H))
    ev_new = stitch.ass_events(cap_new, with_payload=True) if cap_new else []
    ev_prev = stitch.ass_events(cap_prev, with_payload=True) \
        if cap_prev else []

    windows, runs, why = stitch.plan_timeline(
        prev_edl, new_edl, tl_prev, tl_new, dur_out,
        cap_events_prev=ev_prev, cap_events_new=ev_new)
    if windows is None:
        print(f"[render {job_id}] stitch(timeline): full render ({why})",
              flush=True)
        return None

    item_spans = [(a, b) for a, b, _k in
                  stitch._item_windows(new_edl, tl_new, duration)]
    item_spans += list(timeline_mod.insert_windows(
        new_edl.get("inserts") or [], tl_new).values())
    fx = new_edl.get("effects") or {}
    junction_zones = []
    tr = fx.get("transition") or None
    if tr:
        blocks = timeline_mod.program_blocks(new_edl)
        juncs = transition_junctions(new_edl, index, n_blocks=len(blocks))
        zd = float(tr.get("duration_s") or 0.5) + 0.2
        for k in juncs:
            if 0 <= k < len(blocks) - 1:
                t = blocks[k]["out_end"]
                junction_zones.append((t - zd, t + zd))
    fades = []
    fi = float(fx.get("fade_in_s") or 0.0)
    fo = float(fx.get("fade_out_s") or 0.0)
    if fi > 0:
        fades.append((0.0, fi + 0.3))
    if fo > 0:
        fades.append((dur_out - fo - 0.3, dur_out))

    expanded = stitch.expand(windows, item_spans, [], [], fades, dur_out)
    if expanded is None:
        print(f"[render {job_id}] stitch(timeline): full render (windows "
              "would not settle)", flush=True)
        return None

    prev_local = _cached_source(prev_asset["storage_key"])
    if not prev_local:
        prev_local = _fetch_into(workdir, prev_asset["storage_key"], "prevpv")
    kfs = stitch.keyframe_times(prev_local)
    runs = stitch.carve_runs(runs, [(a, b) for a, b in expanded])
    parts = stitch.snap_parts(expanded, runs, kfs, dur_out,
                              item_spans + junction_zones, [])
    if parts is None:
        print(f"[render {job_id}] stitch(timeline): full render (snap gave "
              "up)", flush=True)
        return None

    pinfo = media.probe(prev_local)
    pieces = []
    for i, part in enumerate(parts):
        if part[0] != "win":
            continue
        _k, a, b = part
        wedl = stitch.window_edl(new_edl, tl_new, a, b)
        piece = os.path.join(workdir, f"stitch_tp_{i}.mp4")
        pdur = render_edl(wedl, index, src_local, piece, workdir,
                          preview=True, want_wm=False,
                          patch_locals=patch_locals,
                          cap_ass_override=(cap_new or ""),
                          cap_burn_offset=(a if cap_new else None),
                          suppress_outro=True)
        if abs(pdur - (b - a)) > max(0.15, 2.0 / fps):
            print(f"[render {job_id}] stitch(timeline): full render (piece "
                  f"{i} came out {pdur:.3f}s for a {b - a:.3f}s window)",
                  flush=True)
            return None
        pi = media.probe(piece)
        if (pi["width"], pi["height"]) != (pinfo["width"], pinfo["height"]):
            print(f"[render {job_id}] stitch(timeline): full render (piece "
                  f"is {pi['width']}x{pi['height']}, previous preview is "
                  f"{pinfo['width']}x{pinfo['height']})", flush=True)
            return None
        pieces.append(piece)

    audio_path = os.path.join(workdir, "stitch_audio.m4a")
    render_edl(new_edl, index, src_local, audio_path, workdir,
               preview=True, want_wm=False, patch_locals=patch_locals,
               cap_ass_override="", suppress_outro=True, audio_only=True)

    out_dur = stitch.assemble_offset(prev_local, parts, pieces, audio_path,
                                     dur_out, workdir, out_path)
    total_re = sum(b - a for _k, a, b in
                   [p for p in parts if p[0] == "win"])
    n_copy = sum(1 for p in parts if p[0] == "copy")
    print(f"[render {job_id}] STITCHED preview (timeline): re-encoded "
          f"{total_re:.1f}s of {dur_out:.1f}s across {len(pieces)} "
          f"window(s), {n_copy} span(s) stream-copied at their old "
          "positions, audio rebuilt", flush=True)
    return out_dur


def _stitched_preview(job_id, new_row, prev_row, prev_asset, index,
                      src_local, workdir, patch_locals, out_path):
    """Try to build this preview by re-encoding only the changed windows and
    stream-copying the rest from the previous preview (round 93 — see
    worker/stitch.py). Returns the output duration, or None for ANY reason
    at all — the caller then runs the ordinary full render, which is always
    correct. Never raises."""
    try:
        # Both EDLs go through TODAY'S validator first: a version stored
        # before a schema field existed lacks keys a fresh dump carries with
        # defaults, and that read as a "structural change" on the very first
        # prod attempt (v192, stored in July, vs v193). Validation is what
        # the renderer does to both anyway.
        duration0 = float((index.get("video") or {}).get("duration") or 0.0)
        new_edl = validate_edl(
            new_row["json"],
            max(duration0, max(e for _, e in new_row["json"]["keep"]))
        ).model_dump()
        prev_edl = validate_edl(
            prev_row["json"],
            max(duration0, max(e for _, e in prev_row["json"]["keep"]))
        ).model_dump()
        tl_new = Timeline(new_edl["keep"], new_edl.get("inserts") or [],
                          new_edl.get("speed") or [])
        tl_prev = Timeline(prev_edl["keep"], prev_edl.get("inserts") or [],
                           prev_edl.get("speed") or [])
        duration = float((index.get("video") or {}).get("duration") or 0.0)
        windows, why = stitch.plan(prev_edl, new_edl, tl_prev, tl_new,
                                   duration, tl_new.out_duration)
        if windows is None:
            # Round 97: the refusal that used to end here IS the churn case —
            # a trim, a cut, a music change. Timeline mode handles those by
            # matching content across the two programs and copying it from
            # its old position; anything it cannot prove safe still falls
            # through to the full render.
            out2 = _timeline_stitch(job_id, prev_edl, new_edl, tl_prev,
                                    tl_new, index, src_local, workdir,
                                    patch_locals, out_path, prev_asset,
                                    duration)
            if out2 is None:
                print(f"[render {job_id}] stitch: full render ({why})",
                      flush=True)
            return out2

        info = media.probe(src_local)
        W, H = frame_dims(info["width"], info["height"],
                          (new_edl.get("frame") or {}).get("ratio"))
        fps = max(1.0, min(float(info["fps"]) or 30.0, 60.0))
        W, H, fps = preview_geometry(W, H, fps)

        # Zones a window boundary must never land in: every item span (old
        # and new), every caption event, every junction's transition zone,
        # and the program's fade ends (those refuse stitching outright).
        item_spans = [(a, b) for a, b, _k in
                      stitch._item_windows(prev_edl, tl_prev, duration)
                      + stitch._item_windows(new_edl, tl_new, duration)]
        # Inserted clips are containment zones too: a window boundary inside
        # one would make the piece re-play the insert from its start.
        item_spans += list(timeline_mod.insert_windows(
            new_edl.get("inserts") or [], tl_new).values())
        full_cap = caplib.build_ass(new_edl, index, tl_new,
                                    os.path.join(workdir, "stitch_cap.ass"),
                                    play_res=(W, H))
        fx = new_edl.get("effects") or {}
        junction_zones = []
        tr = fx.get("transition") or None
        if tr:
            blocks = timeline_mod.program_blocks(new_edl)
            juncs = transition_junctions(new_edl, index,
                                         n_blocks=len(blocks))
            d = float(tr.get("duration_s") or 0.5) + 0.2
            for k in juncs:
                if 0 <= k < len(blocks) - 1:
                    t = blocks[k]["out_end"]
                    junction_zones.append((t - d, t + d))
        fades = []
        fi = float(fx.get("fade_in_s") or 0.0)
        fo = float(fx.get("fade_out_s") or 0.0)
        if fi > 0:
            fades.append((0.0, fi + 0.3))
        if fo > 0:
            fades.append((tl_new.out_duration - fo - 0.3,
                          tl_new.out_duration))
        # Items must be CONTAINED (they are carried whole into the windowed
        # EDL); caption events and junction zones only need window BOUNDARIES
        # kept out of them, which the keyframe snap enforces below — feeding
        # them to the expansion made densely-captioned projects (a caption
        # event every second) balloon past the coverage cap and never stitch.
        expanded = stitch.expand(windows, item_spans, [], [],
                                 fades, tl_new.out_duration)
        if expanded is None:
            print(f"[render {job_id}] stitch: full render (windows would "
                  "not settle)", flush=True)
            return None

        prev_local = _cached_source(prev_asset["storage_key"])
        if not prev_local:
            prev_local = _fetch_into(workdir, prev_asset["storage_key"],
                                     "prevpv")
        file_dur = media.duration_of(prev_local)
        # Caption events impose NO boundary constraint at all: pieces burn
        # from the full-program ASS at full-program timestamps (see
        # cap_burn_offset in build_filtergraph), so an event mid-display at
        # a boundary renders exactly as the full render would — animation
        # and karaoke included. Attempts 2-4 on a dynamic-caption project
        # proved no boundary policy can dodge events that tile the whole
        # program; timestamp-true burning removes the problem instead.
        kfs = stitch.keyframe_times(prev_local)
        snapped = stitch.snap_windows(
            expanded, kfs, file_dur,
            forbidden=item_spans + junction_zones)
        if snapped is None:
            print(f"[render {job_id}] stitch: full render (keyframe snap "
                  "gave up)", flush=True)
            return None

        pieces = []
        for i, (a, b) in enumerate(snapped):
            wedl = stitch.window_edl(new_edl, tl_new, a, b)
            piece = os.path.join(workdir, f"stitch_piece_{i}.mp4")
            pdur = render_edl(wedl, index, src_local, piece, workdir,
                              preview=True, want_wm=False,
                              patch_locals=patch_locals,
                              cap_ass_override=(full_cap or ""),
                              cap_burn_offset=(a if full_cap else None),
                              suppress_outro=True)
            if abs(pdur - (b - a)) > max(0.15, 2.0 / fps):
                print(f"[render {job_id}] stitch: full render (piece {i} "
                      f"came out {pdur:.3f}s for a {b - a:.3f}s window)",
                      flush=True)
                return None
            pieces.append(piece)

        out_dur = stitch.assemble(prev_local, pieces, snapped, file_dur,
                                  workdir, out_path)
        total_re = sum(b - a for a, b in snapped)
        print(f"[render {job_id}] STITCHED preview: re-encoded "
              f"{total_re:.1f}s of {file_dur:.1f}s across "
              f"{len(snapped)} window(s), rest stream-copied from "
              f"v{prev_row['version']}", flush=True)
        return out_dur
    except Exception as e:
        print(f"[render {job_id}] stitch failed ({str(e)[:200]}) — running "
              "the full render", flush=True)
        return None


def _render_stamp(job_id):
    """Name fragment for a render object. Unique PER RENDER, and carrying no
    word a client-side content blocker can pattern-match. Both properties are
    load-bearing:

    (1) The old key was `renders/{pid}/{variant}_v{version}.mp4` — the SAME key
        for every re-render of a version. Bytes mutated behind live 6h
        presigned URLs, and a re-render meant to FIX an object the user could
        not play simply overwrote it at the same address, so recovery could
        never produce genuinely new bytes at a new URL.
    (2) `renders/` and `preview_` are ad-blocker / AV-shield bait; the proxy key
        (an opaque sha) is not — and proxies have played in sessions where a
        render did not. Nothing anywhere parses these keys (they are only
        stored on the asset row and presigned), so opacity is free.

    job_id keeps the object traceable back to the job that wrote it.
    """
    return f"{job_id}-{uuid.uuid4().hex[:12]}"


def run_render_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    # Which run of this job we are. The dispatcher ships it (remote._job_payload)
    # so an abandoned executor can tell "still mine" from "the retry has already
    # claimed it and I am rendering for nobody". See _still_ours below.
    my_attempt = job.get("attempts")
    variant = "preview" if job["type"] == "preview" else "final"
    version = int(job["payload"].get("edl_version"))
    # A render the USER could not play is the one case where re-encoding the
    # same EDL is the point: the stored object is what failed them, so serving
    # it back from cache makes every retry a guaranteed no-op. force=1 (set by
    # the studio's "couldn't load" recovery) re-encodes to a FRESH key.
    force = bool(job["payload"].get("force"))
    # Entitlement is read HERE, once, at render time — not from anything
    # stored on the project. A user who upgraded a minute ago must get a
    # clean file for this export, and a lapsed one must get the mark back.
    is_paid = bool(worker_db.run(dbx.user_is_paid, job.get("user_id")))
    wm_settings = worker_db.run(dbx.video_settings)
    want_wm = wants_watermark(variant, is_paid, wm_settings)

    edl_row = worker_db.run(dbx.get_edl_version, project_id, version)
    if not edl_row:
        raise RuntimeError(f"EDL version {version} not found")
    original = worker_db.run(dbx.latest_asset, project_id, "original")
    # A canvas program (no main video) renders purely from its inserts on the
    # canvas — there is no original/proxy/index to require or download.
    is_canvas = is_canvas_program(edl_row["json"])
    if not is_canvas and (not original or not original["sha256"]):
        raise RuntimeError("No indexed original video for this project")
    src_sha = original["sha256"] if original else "canvas"

    # Cache: this exact EDL version was already rendered in this variant against
    # this exact source file — serve the stored asset instead of re-encoding.
    # (EDL versions are append-only, so version N's geometry can never change;
    # the sha guard covers video replacement.) For from_transcript captions the
    # burned TEXT also depends on the mutable index, so a caption fingerprint
    # must match too (see _caption_index_fp) — otherwise a caption-less render
    # is served forever after the transcript gains words.
    cached = (None if force else
              worker_db.run(dbx.find_render_asset, project_id, variant, version))
    if cached and (cached.get("meta") or {}).get("src_sha256") == \
            src_sha and storage.exists(cached["storage_key"]):
        caps = edl_row["json"].get("captions")
        needs_fp = isinstance(caps, dict) and caps.get("mode") == "from_transcript"
        stored_fp = (cached.get("meta") or {}).get("caption_fp")
        fp_ok = True
        # Grandfather renders made before fingerprinting existed (stored_fp is
        # None): trust them rather than force-re-encode every cached preview AND
        # final on this box (a long final re-render is minutes on ~1 vCPU). New
        # renders all carry a fingerprint, so the stale-transcript guard applies
        # going forward; only a PRESENT fingerprint that no longer matches busts.
        if needs_fp and stored_fp is not None:
            idx_c = worker_db.run(dbx.get_index_by_sha, original["sha256"])
            want_fp = _caption_index_fp(edl_row["json"],
                                        (idx_c or {}).get("json") or {})
            fp_ok = (want_fp == stored_fp)
        # The end card is a render-pipeline constant, so nothing about adding
        # it moves the EDL version or the source sha — every already-rendered
        # version would keep serving un-branded bytes forever. Grandfathering
        # here must therefore be the OPPOSITE of caption_fp's above: a MISSING
        # stamp means the render predates the card and must be re-encoded,
        # where a missing caption fingerprint is trusted.
        _tail_out = Timeline(edl_row["json"].get("keep") or [],
                             edl_row["json"].get("inserts") or [],
                             edl_row["json"].get("speed")).out_duration
        if fp_ok and outro_current(cached.get("meta"), variant) \
                and shaping_current(cached.get("meta"), edl_row["json"]) \
                and transitions_current(cached.get("meta"), edl_row["json"]) \
                and music_tail_current(cached.get("meta"), edl_row["json"],
                                       _tail_out) \
                and watermark_current(cached.get("meta"), variant, is_paid,
                                      wm_settings):
            return {"render_asset_id": cached["id"],
                    "sheet_key": (cached.get("meta") or {}).get("sheet_key"),
                    "duration_s": cached["duration_s"], "edl_version": version,
                    "variant": variant, "cached": True}
    if is_canvas:
        index = {}
        src_asset = None
    else:
        index_row = worker_db.run(dbx.get_index_by_sha, original["sha256"])
        if not index_row:
            raise RuntimeError("Video index missing — re-run indexing")
        index = index_row["json"]

        src_asset = original
        if variant == "preview":
            proxy = worker_db.run(dbx.latest_asset, project_id, "proxy")
            if proxy:
                src_asset = proxy
        elif (original.get("meta") or {}).get("upload_state") == "pending":
            # Proxy-first upload: the original is registered but its bytes are
            # still in flight. /render/final refuses this with a percentage, so
            # reaching here means a non-studio caller (the MCP surface) asked
            # for an export — give it the same answer rather than a download
            # failure from inside ffmpeg.
            pct = int(round(float((original.get("meta") or {})
                                  .get("upload_progress") or 0) * 100))
            raise RuntimeError(
                "Exports render from the full-resolution original, which is "
                f"still uploading ({pct}% done). The edit is saved — export "
                "again once it lands.")
        clean_key = clean_source_key(edl_row["json"], variant, src_sha)
        if clean_key:
            if not storage.exists(clean_key):
                raise RuntimeError(
                    "The cleaned source for this edit is missing from storage "
                    f"({clean_key}) — re-run the erase so the repainted video "
                    "exists before rendering")
            src_asset = {"storage_key": clean_key}

    workdir = os.path.join(config.TMP_DIR, f"render_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    timings, t0 = {}, time.monotonic()

    def _mark(stage):
        nonlocal t0
        timings[stage] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    # Set the moment a progress write reports the row is no longer ours. The
    # encode watchdog reads it every 2s (media.run cancelled_cb) and kills
    # ffmpeg — nothing here ever queries the DB on its own account.
    _abandoned = [False]

    def _still_ours(progress):
        """Write progress; return False once this run has been superseded.

        The answer costs nothing: set_progress' UPDATE already carries
        `state='running' AND attempts=%s`, so its rowcount IS the answer and
        was previously discarded. On a DB error assume we are still ours —
        losing the connection for one tick must never kill a healthy export.
        """
        try:
            ok = worker_db.run(dbx.set_progress, job_id, progress, my_attempt)
        except Exception:
            return True
        if ok is False:
            _abandoned[0] = True
        return ok is not False

    try:
        if src_asset:
            # Checked BEFORE the download, which for a 14 GB original is the
            # single most expensive thing this instance can be asked to do for
            # a job that no longer exists.
            if not _still_ours(5):
                raise RuntimeError(
                    "job was cancelled or handed to another worker")
            src_local = _cached_source(src_asset["storage_key"])
            if not src_local:
                src_local = os.path.join(
                    workdir,
                    "src" + os.path.splitext(src_asset["storage_key"])[1])
                try:
                    storage.download_to(src_asset["storage_key"], src_local)
                except storage.WorkdirTooSmall as e:
                    # A source bigger than the instance's whole scratch (a
                    # 32-min DJI original needed 25.4 GB against 20.3 free,
                    # job 3602) used to end the story: --memory is already at
                    # Cloud Run's 32Gi ceiling, so there is no bigger box.
                    # Feed ffmpeg/ffprobe the object over HTTPS instead —
                    # every consumer of src_local (probe, render_edl, stitch)
                    # is ffmpeg-family and range-reads remote inputs. Slower
                    # per pass, but it renders; the download path stays the
                    # default for everything that fits.
                    print(f"[render {job_id}] {e} — streaming the source "
                          "from storage instead of staging it", flush=True)
                    src_local = storage.presign_get(
                        src_asset["storage_key"], expires=21600)
        else:
            src_local = None            # canvas program: nothing to download
        if not _still_ours(10):
            raise RuntimeError("job was cancelled or handed to another worker")
        _mark("download_s")

        out_local = os.path.join(workdir, f"{variant}_v{version}.mp4")

        # Repainted windows (round 92). Previews composite the proxy-res
        # patch clips as stored; a FINAL needs each patch's full-resolution
        # twin, materialized here (window-sized work on the already-download-
        # ed original) exactly once — the key is content-addressed by the
        # patch fingerprint, so every later export finds it. A patch whose
        # fingerprint no longer matches this upload is a repaint of a
        # REPLACED video and is dropped, same rule as clean_source_key.
        patch_locals = {}
        for pt in (edl_row["json"].get("patches") or []):
            if src_sha != "canvas" and pt.get("fp") != patch_fingerprint(
                    src_sha, pt.get("regions") or [],
                    (pt.get("src_start"), pt.get("src_end"))):
                print(f"[render {job_id}] ignoring patch {pt['id']}: it "
                      "repaints a different upload (the video was replaced)",
                      flush=True)
                continue
            try:
                if variant == "preview":
                    patch_locals[pt["id"]] = _cached_source(pt["asset_key"]) \
                        or _fetch_into(workdir, pt["asset_key"], pt["id"])
                else:
                    fkey = pt.get("full_key") \
                        or f"patches/{project_id}/{pt['fp'][:16]}_full.mp4"
                    if not storage.exists(fkey):
                        import inpaint as _inp
                        flocal = os.path.join(workdir,
                                              f"patch_{pt['id']}_full.mp4")
                        _inp.build_patch(
                            src_local,
                            [dict(r) for r in pt.get("regions") or []],
                            (float(pt["src_start"]), float(pt["src_end"])),
                            flocal, crf=18)
                        storage.upload_file(flocal, fkey, "video/mp4")
                        patch_locals[pt["id"]] = flocal
                    else:
                        patch_locals[pt["id"]] = _cached_source(fkey) \
                            or _fetch_into(workdir, fkey, pt["id"])
            except Exception as pe_:
                # A missing patch must not kill an export — but it must be
                # LOUD: the window renders un-repainted, which the verify
                # sheet and the user can both see.
                print(f"[render {job_id}] patch {pt['id']} unavailable "
                      f"({str(pe_)[:160]}) — that window renders "
                      "un-repainted", flush=True)

        # Throttled: ffmpeg emits -progress a couple of times a second, and
        # unthrottled that was ~2 UPDATE/s against the shared DB for the
        # whole encode (a long final = ~1700 writes). set_progress also
        # refreshes the job heartbeat, so a few seconds apart is plenty.
        _last_prog = [0.0]

        def _prog(frac):
            now = time.monotonic()
            if now - _last_prog[0] < 3.0 and frac < 0.99:
                return
            _last_prog[0] = now
            _still_ours(10 + int(frac * 80))

        if variant == "final" and not endcard_path():
            # Exports keep working, but this must never be silent: it means a
            # build shipped without its brand asset, and nobody downstream
            # would otherwise notice that every export lost its end card.
            print(f"[render {job_id}] BRAND CARD MISSING at {ENDCARD_PATH} — "
                  "exporting WITHOUT the Valmera end card", flush=True)

        # STITCHED PREVIEW (round 93): when only video-local layers changed
        # since the last rendered preview, re-encode those windows and
        # stream-copy the rest. Gated hard: never for finals (they render
        # from the original at full fidelity), never on force (that path
        # exists to produce genuinely fresh bytes), never for the
        # watermarked free tier (pieces would need the mark reproduced
        # seam-exactly), and only from a previous render that every cache
        # guard agrees is still a true render of its EDL.
        out_dur = None
        stitched_from = None
        # A tray upload can appear twice in assets under different storage
        # keys (main original + reusable video clip) while carrying the exact
        # same sha256.  On a final the original is already local; let every
        # byte-identical key reuse it.  This is not enabled for previews (their
        # main input is a proxy, not byte-identical) or cleaned sources.
        asset_locals = {}
        if variant == "final" and src_local and src_asset \
                and src_asset.get("storage_key") == original.get("storage_key") \
                and src_sha and src_sha != "canvas":
            for key in worker_db.run(dbx.project_asset_keys_by_sha,
                                     project_id, src_sha):
                asset_locals[key] = src_local
        if variant == "preview" and not force and not want_wm \
                and not is_canvas:
            try:
                prev_asset = worker_db.run(dbx.latest_render_asset,
                                           project_id, "preview")
                pm = (prev_asset or {}).get("meta") or {}
                prev_v = pm.get("edl_version")
                if prev_asset and prev_v is not None \
                        and int(prev_v) != version \
                        and pm.get("src_sha256") == src_sha \
                        and storage.exists(prev_asset["storage_key"]):
                    prev_row = worker_db.run(dbx.get_edl_version, project_id,
                                             int(prev_v))
                    _pout = Timeline(
                        prev_row["json"].get("keep") or [],
                        prev_row["json"].get("inserts") or [],
                        prev_row["json"].get("speed")).out_duration \
                        if prev_row else 0.0
                    fp_now = _caption_index_fp(prev_row["json"], index) \
                        if prev_row else None
                    if prev_row \
                            and outro_current(pm, "preview") \
                            and shaping_current(pm, prev_row["json"]) \
                            and transitions_current(pm, prev_row["json"]) \
                            and music_tail_current(pm, prev_row["json"],
                                                   _pout) \
                            and watermark_current(pm, "preview", is_paid,
                                                  wm_settings) \
                            and (fp_now is None
                                 or pm.get("caption_fp") == fp_now):
                        out_dur = _stitched_preview(
                            job_id, edl_row, prev_row, prev_asset, index,
                            src_local, workdir, patch_locals, out_local)
                        if out_dur is not None:
                            stitched_from = int(prev_v)
            except Exception as se:
                print(f"[render {job_id}] stitch eligibility failed "
                      f"({str(se)[:160]}) — full render", flush=True)
                out_dur = None
        if out_dur is None:
            out_dur = render_edl(edl_row["json"], index, src_local,
                                 out_local, workdir,
                                 preview=(variant == "preview"),
                                 progress_cb=_prog, want_wm=want_wm,
                                 cancelled_cb=lambda: _abandoned[0],
                                 patch_locals=patch_locals,
                                 asset_locals=asset_locals)
        _mark("encode_s")
        # Render verification: the output must be the expected length and must
        # not be newly-black vs the source. On failure this raises, so the
        # worker retries the encode once (MAX_ATTEMPTS_MEDIA) before surfacing a
        # real error — a visually broken render never uploads silently.
        try:
            src_dur = media.duration_of(src_local) if src_local else None
        except Exception:
            src_dur = None
        try:
            _verify_render(edl_row["json"], out_local, out_dur, job_id,
                           variant, src_path=src_local, src_dur=src_dur)
        except media.MediaError as ve:
            # Round 97 (#3): a STITCHED preview that fails verification must
            # never surface — the full render is the always-correct fallback
            # and it belongs INSIDE this job. Before this, a bad stitch
            # failed the job, the queue retried it onto the same bad path,
            # and one user watched the same red error three times in a row.
            # Only a stitch gets this second chance: a full render that
            # verifies wrong is a real defect and must keep raising.
            if stitched_from is None:
                raise
            print(f"[render {job_id}] stitched preview failed verification "
                  f"({str(ve)[:160]}) — running the full render in-job",
                  flush=True)
            stitched_from = None
            out_dur = render_edl(edl_row["json"], index, src_local,
                                 out_local, workdir,
                                 preview=(variant == "preview"),
                                 progress_cb=_prog, want_wm=want_wm,
                                 cancelled_cb=lambda: _abandoned[0],
                                 patch_locals=patch_locals,
                                 asset_locals=asset_locals)
            _verify_render(edl_row["json"], out_local, out_dur, job_id,
                           variant, src_path=src_local, src_dur=src_dur)
        _mark("verify_s")

        sheet_local = os.path.join(workdir, "result_sheet.jpg")
        try:
            # The PROGRAMME duration, not the file duration. build_result_sheet
            # samples at duration*(i+0.5)/9, so with the file duration the last
            # tile of any render under ~45s lands on the end card — and the
            # vision self-check that reads this sheet is told to flag
            # unexpected black frames. It would report the branding as a defect
            # and the agent would tell the user their video is broken.
            sheets.build_result_sheet(
                out_local, sheet_local,
                max(0.1, out_dur - outro_seconds(variant == "preview")))
        except Exception:
            sheet_local = None
        # Round 81: the dispatcher may name the exact output seconds its edit
        # changed (edl_diff.verify_plan); frames pulled HERE cost a few seeks
        # on a file we already hold, where pulling them dispatcher-side would
        # mean downloading the whole render to the box that must never decode
        # video. Best-effort like the sheet — a render never fails over its
        # own review artwork, and an executor that predates this field simply
        # ignores it.
        verify_local = None
        vtimes = (job["payload"].get("verify_times") or [])
        if vtimes:
            try:
                verify_local = os.path.join(workdir, "verify_sheet.jpg")
                sheets.build_frames_sheet(out_local, verify_local, vtimes)
            except Exception:
                verify_local = None
        _mark("sheet_s")

        stamp = _render_stamp(job_id)
        render_key = f"media/{project_id}/{stamp}.mp4"
        storage.upload_file(out_local, render_key, "video/mp4")
        sheet_key = None
        if sheet_local and os.path.exists(sheet_local):
            sheet_key = f"media/{project_id}/{stamp}_s.jpg"
            storage.upload_file(sheet_local, sheet_key, "image/jpeg")
        verify_sheet_key = None
        if verify_local and os.path.exists(verify_local):
            verify_sheet_key = f"media/{project_id}/{stamp}_vf.jpg"
            storage.upload_file(verify_local, verify_sheet_key, "image/jpeg")
        worker_db.run(dbx.set_progress, job_id, 96)
        _mark("upload_s")

        out_info = media.probe(out_local)
        # Round 98 — the render reviews its own SOUND. One ffmpeg pass
        # measures the mix (LUFS / true peak / dead air -> plain findings the
        # agent must act on, exactly like the taste audit), and the changed
        # seconds are cut as tiny mono mp3s the agent LISTENS to when its
        # model has ears (llm.agent_hears). Both best-effort: a preview never
        # fails over its own review, and an executor that predates the fields
        # simply returns neither.
        audio_qc_res = None
        listen_keys = []
        if variant == "preview":
            try:
                audio_qc_res = audio_qc.measure(out_local, duration_s=out_dur)
            except Exception:
                audio_qc_res = None
            try:
                for i, (ls, le) in enumerate(
                        audio_qc.listen_windows(vtimes, out_dur)):
                    lp = os.path.join(workdir, f"listen_{i}.mp3")
                    media.extract_audio_clip(out_local, ls, le, lp)
                    lk = f"media/{project_id}/{stamp}_l{i}.mp3"
                    storage.upload_file(lp, lk, "audio/mpeg")
                    listen_keys.append({"key": lk, "t0": round(ls, 2),
                                        "t1": round(le, 2)})
            except Exception:
                listen_keys = []
        asset_id = worker_db.run(
            dbx.insert_asset, project_id, "render", render_key,
            bytes_=os.path.getsize(out_local), duration_s=out_dur,
            width=out_info["width"], height=out_info["height"],
            fps=out_info["fps"],
            meta={"variant": variant, "edl_version": version,
                  "sheet_key": sheet_key, "verify_sheet_key": verify_sheet_key,
                  "listen_keys": [k["key"] for k in listen_keys],
                  "src_sha256": src_sha,
                  **({"stitched_from": stitched_from}
                     if stitched_from is not None else {}),
                  "caption_fp": _caption_index_fp(edl_row["json"], index),
                  "outro_v": (config.OUTRO_VERSION
                              if outro_seconds(variant == "preview") else 0),
                  "gfx_shape_v": config.GFX_SHAPING_VERSION,
                  "trans_v": config.TRANSITION_VERSION,
                  "tail_v": config.MUSIC_TAIL_VERSION,
                  "wm_v": watermark_version(variant, is_paid,
                                            wm_settings)})
        # Reclaim the renders this one just replaced. Unique-per-render keys
        # made recovery possible but left every superseded object in the bucket
        # forever; only this exact (variant, version) is pruned, so pinned older
        # VERSIONS still play. Best-effort — never fail a finished render over
        # cleanup.
        try:
            old = worker_db.run(dbx.superseded_renders, project_id, variant,
                                version, asset_id)
            if old:
                keys = []
                for a in old:
                    keys.append(a["storage_key"])
                    keys.append((a.get("meta") or {}).get("sheet_key"))
                    keys.append((a.get("meta") or {}).get("verify_sheet_key"))
                    keys.extend((a.get("meta") or {}).get("listen_keys")
                                or [])
                storage.delete_keys(keys)
                worker_db.run(dbx.delete_assets, [a["id"] for a in old])
                print(f"[render {job_id}] pruned {len(old)} superseded "
                      f"render(s) for v{version}", flush=True)
        except Exception as e:
            print(f"[render {job_id}] prune skipped: {e}", flush=True)
        # Deterministic mid-word audit: keep boundaries that clip a word,
        # computed straight from the index — visible in logs and to the
        # agent even if it ignored the write-time warnings. Meaningless (and
        # unsafe: index is {} with no ['video']) for a canvas program.
        mw = [] if is_canvas else audit.midword_audit(
            edl_row["json"]["keep"], index.get("words", []),
            index["video"]["duration"])
        if mw:
            print(f"[render {job_id}] MID-WORD AUDIT: {'; '.join(mw)}",
                  flush=True)
        return {"render_asset_id": asset_id, "sheet_key": sheet_key,
                "verify_sheet_key": verify_sheet_key,
                "duration_s": out_dur, "edl_version": version,
                "variant": variant, "timings": timings,
                "midword_audit": mw,
                "audio_qc": audio_qc_res, "listen_keys": listen_keys}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
