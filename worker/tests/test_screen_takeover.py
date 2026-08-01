"""Round 55 — the screen takeover, pinned against real ffmpeg output.

The move is: push the camera into a device screen in the shot while the clip
that will take over plays ON that screen, and hand off to the clip full-frame
on the exact frame the push lands. Every claim in the tool's own reply is a
pixel claim, so every one of them is checked here against a rendered file
rather than against the filtergraph string:

  1. GLUED. At the start of the window the content sits exactly inside the
     screen quad and nowhere else. This is the claim an ordinary overlay
     could never make — it is drawn above the zoom, so it would be a
     rectangle floating in front of the shot.
  2. RIDES THE PUSH. Halfway through, the content is still on the glass: its
     centre tracks the quad's centre and its area has grown by roughly the
     square of the camera's zoom. A pin that ignored the zoom would sit still
     while the shot moved under it, which is exactly what "it doesn't look
     smooth" means.
  3. ARRIVES. On the last frame of the window the content covers the WHOLE
     frame, to the pixel. Not "nearly" — the handoff depends on it.
  4. THE JOIN IS INVISIBLE. The last frame of the push and the first frame of
     the spliced clip are the same picture. This is the whole point of
     building the takeover backwards from where insert_media actually put the
     clip, and it is the one thing a version of this feature that "looked
     right" in isolation would still get wrong.
  5. UNTOUCHED OUTSIDE. Before the window the program is byte-for-byte the
     shot; the push does not leak.

Plus the arithmetic and the refusals: quad ordering, the resolver both the
camera and the pin read, and the detector finding a screen in synthetic
footage (and declining to invent one in footage with no screen in it).

Run:  python -m pytest tests/test_screen_takeover.py -q     (from worker/)
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import renderer                                               # noqa: E402
import screendet                                              # noqa: E402
from schemas import (EDLValidationError, quad_bbox,           # noqa: E402
                     quad_is_sane, validate_edl)
from timeline import Timeline                                 # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS = 640, 360, 30
ROOM_S, CLIP_S = 6.0, 6.0
# The "laptop screen": a frontal rectangle, deliberately NOT the output's
# aspect, so the correction term in the corner path is exercised rather than
# being identically zero.
QUAD = [0.25, 0.20, 0.72, 0.20, 0.25, 0.68, 0.72, 0.68]
TAKE_S = 1.2


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:
        return None


# ------------------------------------------------------------------ #
#  Fixtures                                                           #
# ------------------------------------------------------------------ #

def _room_frame():
    """A dim room with a bright, detailed rectangle standing in for a screen.

    Detail inside the rectangle matters: the detector scores content, because
    a blank bright panel is a window or a whiteboard, not a screen.
    """
    img = np.full((H, W, 3), 26, np.uint8)
    img[:, :, 2] = 44                                   # a warm-ish room
    x0, y0 = int(QUAD[0] * W), int(QUAD[1] * H)
    x1, y1 = int(QUAD[6] * W), int(QUAD[7] * H)
    img[y0 - 4:y1 + 4, x0 - 4:x1 + 4] = 12              # dark bezel
    img[y0:y1, x0:x1] = 210                             # lit glass
    for k in range(y0 + 6, y1 - 4, 9):                  # "UI" on the glass
        img[k:k + 3, x0 + 8:x1 - 8] = 70
    return img


def _clip_frame(i, n):
    """The content: four solid quadrants, so where it lands is unambiguous."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 2, :W // 2] = (255, 40, 40)
    img[:H // 2, W // 2:] = (40, 255, 40)
    img[H // 2:, :W // 2] = (40, 40, 255)
    img[H // 2:, W // 2:] = (255, 255, 40)
    # A moving band, so a frame from the wrong instant is detectable. Kept
    # SATURATED (not white) because _content_mask separates content from room
    # by saturation, and a white band would read as "not content" and make a
    # fully-covered frame measure 98% covered.
    y = int((i / max(1, n - 1)) * (H - 8))
    img[y:y + 8, :] = (255, 0, 255)
    return img


def _encode(path, frames, fps=FPS):
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={len(frames) / fps:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "replace")
    assert p.wait() == 0, err


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round55_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def room(workdir):
    path = os.path.join(workdir, "room.mp4")
    n = int(ROOM_S * FPS)
    _encode(path, [_room_frame() for _ in range(n)])
    return path


@pytest.fixture(scope="module")
def clip(workdir):
    path = os.path.join(workdir, "clip.mp4")
    n = int(CLIP_S * FPS)
    _encode(path, [_clip_frame(i, n) for i in range(n)])
    return path


def _takeover_edl(clip_key, hand=3.0, dur=TAKE_S, push=1.0, ease="smooth"):
    """The EDL add_screen_takeover writes: a pinned overlay ending exactly
    where the spliced clip begins."""
    return {
        # Split where the clip lands: an insert splices at a keep boundary,
        # which is exactly why insert_media splits the take and why the tool
        # reads back where the clip REALLY went instead of assuming.
        "keep": [[0.0, hand], [hand, ROOM_S]],
        "inserts": [{"id": "ins1", "asset_key": clip_key, "kind": "video",
                     "at_output_s": hand, "duration_s": 2.0,
                     "source_start_s": round(dur, 2)}],
        "overlays": [{"id": "tk1", "asset_key": clip_key, "kind": "video",
                      "start": round(hand - dur, 2), "duration_s": dur,
                      "x": 0.5, "y": 0.5, "scale": 1.0,
                      "source_start_s": None,
                      "screen": {"corners": list(QUAD), "push": push,
                                 "ease": ease}}],
    }


def _render(src, clip_path, edl_dict, out, fps=None):
    edl = validate_edl(dict(edl_dict), ROOM_S).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    index = {"video": {"duration": ROOM_S}, "words": [], "sentences": []}
    # Input layout mirrors render_edl: [0] main, then inserts, then overlays.
    graph = renderer.build_filtergraph(
        edl, ROOM_S, True, tl, None, [], index, preview=False,
        W=W, H=H, fps=float(fps or FPS),
        insert_inputs=[(1, edl["inserts"][0], True)],
        overlay_inputs=[(2, edl["overlays"][0])])
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-i", clip_path,
           "-i", clip_path, "-filter_complex", graph,
           "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
           "-pix_fmt", "yuv420p", "-c:a", "aac", out]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-3000:]
    return out


def _frame_at(path, t):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", path,
         "-frames:v", "1", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    buf = p.stdout
    assert len(buf) >= W * H * 3, f"short frame at {t}s ({len(buf)} bytes)"
    return np.frombuffer(buf[:W * H * 3], np.uint8).reshape(H, W, 3)


def _content_mask(frame):
    """Where the CONTENT is: its quadrants are saturated primaries, the room
    is a flat dim grey. Saturation separates them with no threshold tuning."""
    f = frame.astype(np.int16)
    return (f.max(axis=2) - f.min(axis=2)) > 70


# ------------------------------------------------------------------ #
#  1. The arithmetic both the camera and the pin read                 #
# ------------------------------------------------------------------ #

def _ev(expr, on):
    import math
    return eval(expr.replace("on", str(float(on))),
                {"clip": lambda v, a, b: max(a, min(b, v)),
                 "between": lambda t, a, b: 1.0 if a <= t <= b else 0.0,
                 "lt": lambda a, b: 1.0 if a < b else 0.0,
                 "gt": lambda a, b: 1.0 if a > b else 0.0,
                 "gte": lambda a, b: 1.0 if a >= b else 0.0,
                 "sin": math.sin, "PI": math.pi,
                 "__builtins__": {}})


def _content_corners(lock, on, dur=TAKE_S):
    """Where the content's own corners land, undoing the transparent-border
    compensation the destination quad carries."""
    cs = renderer.screen_lock_corner_paths(lock, W, H, float(FPS), dur)
    pad = renderer.SCREEN_PAD_PX
    kx, ky = W / (W - 2 * pad), H / (H - 2 * pad)
    out = []
    for i in range(4):
        x = _ev(cs[2 * i], on)
        y = _ev(cs[2 * i + 1], on)
        out.append((W * 0.5 + (x - W * 0.5) / kx,
                    H * 0.5 + (y - H * 0.5) / ky))
    return out


def test_pin_starts_exactly_on_the_quad():
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    got = _content_corners(lock, 0)
    want = [(QUAD[2 * i] * W, QUAD[2 * i + 1] * H) for i in range(4)]
    for (gx, gy), (wx, wy) in zip(got, want):
        assert abs(gx - wx) < 0.05 and abs(gy - wy) < 0.05


def test_pin_lands_identity_at_the_arrival_then_punches_through():
    """Round 63b: the push completes screen_lock_hold BEFORE the handoff.
    At the ARRIVAL instant the pin is the exact full-frame identity (the
    round-55 invariant, relocated); through the hold the content keeps
    moving — grown past the frame on the same sin curve the program-side
    landing continues after the cut — so the cut never sits at a first-
    full-frame instant or a dead stop."""
    import math
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    hold = renderer.screen_lock_hold(TAKE_S)
    assert hold > 0.02
    arrive = int(round((TAKE_S - hold - 1.0 / FPS) * FPS))
    got = _content_corners(lock, arrive)
    want = [(0, 0), (W, 0), (0, H), (W, H)]
    for (gx, gy), (wx, wy) in zip(got, want):
        assert abs(gx - wx) < 0.05 and abs(gy - wy) < 0.05
    # last emitted frame: identity grown by the through-cut punch, exactly
    # on the shared curve — the frame stays covered and still moving
    last = int(round((TAKE_S - 1.0 / FPS) * FPS))
    t = last / FPS
    g = 1.0 + renderer.SCREEN_LAND_ZOOM * math.sin(
        math.pi * (t - (TAKE_S - hold)) / (hold + renderer.SCREEN_LAND_S))
    got_last = _content_corners(lock, last)
    want_last = [(W * 0.5 + (wx - W * 0.5) * g, H * 0.5 + (wy - H * 0.5) * g)
                 for wx, wy in want]
    for (gx, gy), (wx, wy) in zip(got_last, want_last):
        assert abs(gx - wx) < 0.5 and abs(gy - wy) < 0.5
    assert g > 1.001, "the hold is a dead stop"


def test_camera_and_pin_come_from_one_resolver():
    """The zoom the shot gets and the zoom the pin assumes are the same
    number. A takeover built as a separate ZoomItem beside an overlay is
    exactly the bug this asserts against."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    _cx, _cy, z_end = renderer.screen_lock_geometry(lock)
    st, _cxt, _cyt = renderer._screen_lock_terms(
        lock, "on/30.000", 0.0, TAKE_S, float(FPS))
    last = int(round((TAKE_S - 1.0 / FPS) * FPS))
    assert abs(_ev(st, last) - (z_end - 1.0)) < 1e-4
    assert abs(_ev(st, 0)) < 1e-9


def test_push_dial_shortens_the_camera_move_only():
    """push=0 leaves the shot alone; the content still has to arrive (at the
    arrival instant — the hold rides full frame after it)."""
    lock = {"corners": list(QUAD), "push": 0.0, "ease": "smooth"}
    _cx, _cy, z_end = renderer.screen_lock_geometry(lock)
    assert abs(z_end - 1.0) < 1e-9
    hold = renderer.screen_lock_hold(TAKE_S)
    arrive = int(round((TAKE_S - hold - 1.0 / FPS) * FPS))
    got = _content_corners(lock, arrive)
    for (gx, gy), (wx, wy) in zip(got, [(0, 0), (W, 0), (0, H), (W, H)]):
        assert abs(gx - wx) < 0.05 and abs(gy - wy) < 0.05


def test_quad_order_is_checked():
    ok, _ = quad_is_sane(QUAD)
    assert ok
    # Clockwise instead of the filter's order = a bow-tie, and `perspective`
    # renders that as a torn smear rather than refusing.
    clockwise = [QUAD[0], QUAD[1], QUAD[2], QUAD[3],
                 QUAD[6], QUAD[7], QUAD[4], QUAD[5]]
    ok2, why = quad_is_sane(clockwise)
    assert not ok2 and "crossed" in why


def test_tiny_screen_is_refused():
    edl = {"keep": [[0.0, 10.0]],
           "overlays": [{"id": "tk1", "asset_key": "a.mp4", "kind": "video",
                         "start": 1.0, "duration_s": 1.2,
                         "screen": {"corners": [0.40, 0.40, 0.45, 0.40,
                                                0.40, 0.45, 0.45, 0.45]}}]}
    with pytest.raises(EDLValidationError) as e:
        validate_edl(edl, 10.0)
    assert "of the frame" in str(e.value)


def test_window_length_is_bounded():
    for bad in (0.25, 6.0):
        edl = {"keep": [[0.0, 20.0]],
               "overlays": [{"id": "tk1", "asset_key": "a.mp4",
                             "kind": "video", "start": 1.0,
                             "duration_s": bad,
                             "screen": {"corners": list(QUAD)}}]}
        with pytest.raises(EDLValidationError):
            validate_edl(edl, 20.0)


def test_pixel_coordinates_are_rejected_not_silently_used():
    edl = {"keep": [[0.0, 10.0]],
           "overlays": [{"id": "tk1", "asset_key": "a.mp4", "kind": "video",
                         "start": 1.0, "duration_s": 1.2,
                         "screen": {"corners": [160, 72, 480, 72,
                                                160, 288, 480, 288]}}]}
    with pytest.raises(EDLValidationError) as e:
        validate_edl(edl, 10.0)
    assert "FRACTIONS" in str(e.value)


# ------------------------------------------------------------------ #
#  2. What actually comes out of ffmpeg                               #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def rendered(workdir, room, clip):
    out = os.path.join(workdir, "takeover.mp4")
    return _render(room, clip, _takeover_edl("clip.mp4"), out)


@needs_ffmpeg
def test_content_is_glued_inside_the_screen_once_faded_in(rendered):
    """The claim an ordinary overlay cannot make: the content is IN the shot,
    filling the glass and nothing outside it. Sampled just PAST the dissolve:
    round 64 places the content's appearance on the push's PROGRESS (the
    window's opening stretch deliberately shows the filmed screen — a scene
    switch visible in a wide shot is what users call 'it switches too
    early'), so the sample time comes from the same helper the renderer
    emits, and the glued check compares against the resolver's own corners
    for that frame, not the static quad."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    _f0, f1 = renderer.screen_appear_window(lock, TAKE_S, float(FPS))
    dt = f1 + 0.05                  # first fully opaque moment
    f = _frame_at(rendered, 3.0 - TAKE_S + dt)
    m = _content_mask(f)
    ys, xs = np.where(m)
    assert m.sum() > 200, "no content visible on the screen at all"
    # The corners move ~10px/frame here (the round-65 shape correction is
    # deliberately concentrated late in the dive), so the prediction is an
    # ENVELOPE over the sampled frame and its neighbours — a one-frame seek
    # offset must not read as a detached pin.
    on = int(round(dt * FPS))
    cands = [_content_corners(lock, o) for o in (on - 1, on, on + 1)]

    def _near(measured, picks):
        return any(abs(measured - p) < 12 for p in picks)

    assert _near(xs.min(), [max(0, min(px for px, _ in c)) for c in cands])
    assert _near(xs.max(), [min(W - 1, max(px for px, _ in c))
                            for c in cands])
    assert _near(ys.min(), [max(0, min(py for _, py in c)) for c in cands])
    assert _near(ys.max(), [min(H - 1, max(py for _, py in c))
                            for c in cands])


@needs_ffmpeg
def test_the_window_opens_on_the_filmed_screen_not_a_pop(rendered):
    """Round 62b: the window's first frame shows essentially the base footage
    — the content FADES onto the glass instead of snapping on at an opacity
    the filmed screen never had."""
    f = _frame_at(rendered, 3.0 - TAKE_S + 0.02)
    assert _content_mask(f).sum() < 200


@needs_ffmpeg
def test_no_scene_switch_while_the_shot_is_still_wide(rendered):
    """Round 64, from a real render: the recording was opaque on the laptop
    while the camera had covered ~10% of its travel, so the viewer watched
    the next scene playing on a screen across the room. Right up to the
    dissolve's start the glass must show only what was filmed."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    f0, _f1 = renderer.screen_appear_window(lock, TAKE_S, float(FPS))
    assert f0 > 0.25 * TAKE_S, "the dissolve starts with the shot still wide"
    f = _frame_at(rendered, 3.0 - TAKE_S + f0 - 0.05)
    assert _content_mask(f).sum() < 200


def test_appear_window_completes_before_the_arrival():
    """The dissolve must be done — content fully opaque — strictly before the
    push lands, for every ease, or the handoff's frame-identical invariant
    dissolves with it. And it must start late: at least a third of the zoom
    travel has to happen with the filmed screen still showing."""
    for kind in ("smooth", "accelerate", "linear"):
        lock = {"corners": list(QUAD), "push": 1.0, "ease": kind}
        for dur in (0.8, 1.2, 1.5, 3.0):
            f0, f1 = renderer.screen_appear_window(lock, dur, 30.0)
            hold = renderer.screen_lock_hold(dur)
            span = max(dur - hold - 1.0 / 30.0, (dur - hold) * 0.5)
            assert f1 <= span + 1e-6, (kind, dur, f1, span)
            assert f1 - f0 >= 0.05, (kind, dur)
            # e at the dissolve's start is at least SCREEN_APPEAR_E0 minus
            # the minimum-duration slide
            p0 = f0 / span
            e0 = eval(renderer._ease_expr(kind, str(p0)),
                      {"__builtins__": {}})
            assert e0 >= 0.30 or f0 == 0.0, (kind, dur, e0)


def test_matched_corners_appear_from_the_window_start():
    """Round 65: corners that came from finding the CONTENT'S OWN PIXELS on
    the glass mean the screen is already displaying this very content — so
    the pin lives on it from the start, and the late dissolve (which exists
    to hide a scene switch a wide shot would betray) would only hide the
    continuity the match proved. Anything less trustworthy keeps it late."""
    matched = {"corners": list(QUAD), "push": 1.0, "ease": "smooth",
               "corners_source": "matched"}
    f0, f1 = renderer.screen_appear_window(matched, TAKE_S, 30.0)
    assert f0 == 0.0
    assert f1 <= renderer.SCREEN_FADE_IN_S + 1e-6
    for src in ("measured", "read", "user", None):
        lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth",
                "corners_source": src}
        l0, _l1 = renderer.screen_appear_window(lock, TAKE_S, 30.0)
        assert l0 > 0.25 * TAKE_S, (src, l0)


def test_corners_source_survives_validation():
    """corners_source must be a schema field or validate_edl silently drops
    it (pydantic ignores unknown fields — the round-60 lesson) and every
    takeover renders as untrusted."""
    edl = _takeover_edl("clip.mp4")
    edl["overlays"][0]["screen"]["corners_source"] = "matched"
    out = validate_edl(dict(edl), ROOM_S).model_dump()
    assert out["overlays"][0]["screen"]["corners_source"] == "matched"


def test_ease_inverse_round_trips():
    for kind in ("smooth", "accelerate", "linear"):
        for e in (0.0, 0.2, 0.45, 0.85, 1.0):
            p = renderer._ease_inv(kind, e)
            got = eval(renderer._ease_expr(kind, str(p)),
                       {"__builtins__": {}})
            assert abs(got - e) < 1e-6, (kind, e, p, got)


@needs_ffmpeg
def test_content_rides_the_push_instead_of_floating_over_it(rendered):
    """Mid-dissolve the content is already on the glass — same centre, grown
    by the camera — and it keeps growing to the fully-opaque frame. An
    overlay drawn above the zoom would hold its size here."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    f0, f1 = renderer.screen_appear_window(lock, TAKE_S, float(FPS))
    dt_mid = f0 + 0.5 * (f1 - f0)   # half-faded: visible, still mid-move
    f = _frame_at(rendered, 3.0 - TAKE_S + dt_mid)
    m = _content_mask(f)
    ys, xs = np.where(m)
    assert m.sum() > 500
    on = int(round(dt_mid * FPS))
    want = _content_corners(lock, on)
    wx = [p[0] for p in want]
    wy = [p[1] for p in want]
    # Predicted from the resolver, clipped to the frame the same way the
    # renderer's output is.
    assert abs(xs.min() - max(0, min(wx))) < 12, (xs.min(), min(wx))
    assert abs(ys.min() - max(0, min(wy))) < 12, (ys.min(), min(wy))
    # and it really did grow between mid-dissolve and fully opaque
    later = _content_mask(_frame_at(rendered, 3.0 - TAKE_S + f1 + 0.05)).sum()
    assert later > m.sum() * 1.1


@needs_ffmpeg
def test_content_covers_the_whole_frame_when_the_push_lands(rendered):
    f = _frame_at(rendered, 3.0 - 1.5 / FPS)
    m = _content_mask(f)
    assert m.mean() > 0.985, f"only {m.mean():.3f} of the frame is content"


@needs_ffmpeg
def test_content_supply_never_runs_out_before_the_cut(workdir, room, clip):
    """Round 66, from a real render: trimmed to exactly the window length,
    the pinned branch runs out of frames one or two frames before the cut at
    fractional rates, and `overlay eof_action=pass` flashes the zoomed BASE
    between the full-frame content and the incoming clip — "a frame of the
    old screen reappears and goes off very fast". The supply now runs
    SCREEN_SUPPLY_PAD_S past the window, so every frame up to the cut shows
    content. Rendered at a real phone rate (59.969) to force the rounding."""
    out = os.path.join(workdir, "takeover_frac.mp4")
    _render(room, clip, _takeover_edl("clip.mp4"), out, fps=59.969)
    for k in (1, 2, 3):
        f = _frame_at(out, 3.0 - k / 59.969)
        m = _content_mask(f)
        assert m.mean() > 0.95, \
            f"base showing {1 - m.mean():.2%} at {k} frame(s) before the cut"


@needs_ffmpeg
def test_the_join_is_invisible(rendered):
    """The last frame of the push and the first frame of the spliced clip are
    the same picture. This is the reason add_screen_takeover places the clip
    FIRST and builds the window backwards from where it actually landed."""
    before = _frame_at(rendered, 3.0 - 1.5 / FPS).astype(np.int16)
    after = _frame_at(rendered, 3.0 + 0.5 / FPS).astype(np.int16)
    diff = np.abs(before - after).mean()
    assert diff < 12, f"the cut shows: mean abs difference {diff:.1f}"


@needs_ffmpeg
def test_the_shot_is_untouched_before_the_takeover(rendered):
    """The push does not leak backwards into footage nobody asked it to
    touch — the frame a second earlier is still the plain room."""
    f = _frame_at(rendered, 3.0 - TAKE_S - 0.5)
    assert _content_mask(f).mean() < 0.001
    room = _room_frame().astype(np.int16)
    assert np.abs(f.astype(np.int16) - room).mean() < 14


# ------------------------------------------------------------------ #
#  3. The couplings a later edit must not break                       #
# ------------------------------------------------------------------ #

def test_takeover_follows_its_clip_through_a_cut():
    """Trim four seconds off the front and the push moves WITH the clip. An
    overlay clamped in program time (the generic path) would stay put and the
    push would land on nothing."""
    from timeline import remap_program_items
    edl = validate_edl(_takeover_edl("clip.mp4", hand=3.0), ROOM_S).model_dump()
    old_tl = Timeline(edl["keep"], edl["inserts"], [])
    edl["keep"] = [[1.0, 3.0], [3.0, ROOM_S]]
    new_tl = Timeline(edl["keep"], edl["inserts"], [])
    remap_program_items(edl, old_tl, new_tl)
    ov = edl["overlays"][0]
    hand = insert_windows_for(edl, new_tl)["ins1"][0]
    assert abs(round(ov["start"] + ov["duration_s"], 2) - hand) < 0.011, (
        ov, hand)


def test_takeover_dies_with_its_clip():
    from timeline import remap_program_items
    edl = validate_edl(_takeover_edl("clip.mp4", hand=3.0), ROOM_S).model_dump()
    old_tl = Timeline(edl["keep"], edl["inserts"], [])
    edl["inserts"] = []
    new_tl = Timeline(edl["keep"], [], [])
    remap_program_items(edl, old_tl, new_tl)
    assert edl["overlays"] == []


def insert_windows_for(edl, tl):
    from timeline import insert_windows
    return insert_windows(edl.get("inserts"), tl)


def test_no_transition_lands_on_the_handoff():
    """A dip to black in the exact middle of the one join the takeover exists
    to hide. The handoff is a splice, so every scene-change rule calls it a
    cut — it has to be excluded explicitly, and 'every_cut' must not
    reinstate it."""
    from timeline import transition_junctions
    base = _takeover_edl("clip.mp4", hand=3.0)
    index = {"shots": [{"start": 0.0, "end": ROOM_S}]}
    for scope in ("scene", "every_cut"):
        edl = validate_edl(dict(base), ROOM_S).model_dump()
        edl["effects"] = {"transition": {"style": "dip_black",
                                         "scope": scope}}
        js = transition_junctions(edl, index)
        # blocks: [seg0][ins1][seg1] -> junction 0 is seg0|ins1, the handoff
        assert 0 not in js, (scope, js)


def test_a_plain_insert_still_gets_its_transition():
    """The exclusion is surgical: it must not switch transitions off for
    ordinary spliced b-roll in the same edit."""
    from timeline import transition_junctions
    edl = validate_edl(
        {"keep": [[0.0, 3.0], [3.0, ROOM_S]],
         "inserts": [{"id": "ins1", "asset_key": "broll.mp4", "kind": "video",
                      "at_output_s": 3.0, "duration_s": 2.0}],
         "effects": {"transition": {"style": "dip_black"}}},
        ROOM_S).model_dump()
    assert 0 in transition_junctions(edl, {"shots": [{"start": 0.0,
                                                      "end": ROOM_S}]})


# ------------------------------------------------------------------ #
#  4. The detector: measures, or says it could not                    #
# ------------------------------------------------------------------ #

@pytest.mark.skipif(_cv2() is None, reason="OpenCV not installed")
def test_detector_finds_the_screen(workdir):
    cv2 = _cv2()
    paths = []
    for i in range(3):
        p = os.path.join(workdir, f"det_{i}.png")
        cv2.imwrite(p, _room_frame())
        paths.append(p)
    res = screendet.find_screen(paths)
    assert not res.get("error"), res
    assert res["confidence"] >= screendet.MIN_CONFIDENCE
    got = res["corners"]
    for i in range(8):
        assert abs(got[i] - QUAD[i]) < 0.04, (i, got[i], QUAD[i])


@pytest.mark.skipif(_cv2() is None, reason="OpenCV not installed")
def test_detector_declines_on_footage_with_no_screen(workdir):
    """The honest floor. Inventing a rectangle here is worse than refusing:
    the corners are the whole effect, and a wrong one slides visibly once the
    push magnifies it."""
    cv2 = _cv2()
    rng = np.random.default_rng(7)
    paths = []
    for i in range(3):
        p = os.path.join(workdir, f"noise_{i}.png")
        cv2.imwrite(p, rng.integers(0, 90, (H, W, 3), dtype=np.uint8))
        paths.append(p)
    res = screendet.find_screen(paths)
    assert res.get("error") or res["confidence"] < screendet.MIN_CONFIDENCE


# ------------------------------------------------------------------ #
#  5. Round 60: when the pixels decline, the corners are READ         #
# ------------------------------------------------------------------ #
#
# screendet declines on real footage — dark content on the screen, a bezel that
# blends into a dark desk, a hand across a corner. That used to end the tool
# call with a REJECTED telling the AGENT to go and call look_at, read the
# corners and pass them back: two more round trips at ~13s each, mid-request,
# which the model frequently abandoned in favour of a plain cut. The same work
# now happens inside the same call — but a read is an ESTIMATE, and the reply
# has to say so.

import agent_tools                                             # noqa: E402
import llm                                                     # noqa: E402


class _VisionStub:
    def __init__(self, answer, available=True):
        self.answer = answer
        self.available = available
        self.calls = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(llm, "vision_available", lambda *a, **k: self.available)

        def _ask(prompt, paths, **kw):
            self.calls += 1
            self.prompt = prompt
            return self.answer
        monkeypatch.setattr(llm, "ask_vision", _ask)
        return self


@pytest.mark.parametrize("answer, ok", [
    ("[0.25, 0.20, 0.72, 0.20, 0.25, 0.68, 0.72, 0.68]", True),
    ("Here you go:\n[0.1,0.1, 0.9,0.12, 0.12,0.8, 0.88,0.82]\nHope that helps",
     True),
    ("none", False),                       # no device in the frame
    ("I'm not able to determine that", False),
    ("[0.1, 0.2, 0.3]", False),            # not 8 numbers
    ("[0.1,0.1, 2.4,0.1, 0.1,0.9, 0.9,0.9]", False),   # off the frame
    ("[a,b,c,d,e,f,g,h]", False),          # not numbers
])
def test_vision_corner_read_accepts_only_a_real_quad(monkeypatch, answer, ok):
    stub = _VisionStub(answer).install(monkeypatch)
    quad, why = agent_tools._vision_screen_corners(None, "frame.jpg")
    assert stub.calls == 1
    if ok:
        assert quad is not None and len(quad) == 8 and why is None
        assert all(0.0 <= v <= 1.0 for v in quad)
    else:
        assert quad is None and why


def test_no_vision_provider_is_an_honest_no_not_a_crash(monkeypatch):
    stub = _VisionStub("[0,0,1,0,0,1,1,1]", available=False).install(monkeypatch)
    quad, why = agent_tools._vision_screen_corners(None, "frame.jpg")
    assert quad is None
    assert "unavailable" in why
    assert stub.calls == 0          # never asked a provider that cannot answer


class _Ctx:
    """The three things _detect_screen touches."""

    def __init__(self, workdir):
        self.workdir = workdir
        self.duration = 10.0
        self.index = {"video": {"width": W, "height": H}}

    def proxy_path(self):
        return os.path.join(self.workdir, "proxy.mp4")


def _stub_frames(monkeypatch, workdir):
    import media
    monkeypatch.setattr(media, "frame_at",
                        lambda path, t, fp, **k: open(fp, "wb").write(b"x"))


def test_a_declining_detector_falls_through_to_the_read(monkeypatch, workdir):
    """The wiring. Without this the fallback exists and is never reached."""
    _stub_frames(monkeypatch, workdir)
    monkeypatch.setattr(screendet, "find_screen",
                        lambda frames: {"confidence": 0.1, "corners": QUAD})
    stub = _VisionStub(
        "[0.25, 0.20, 0.72, 0.20, 0.25, 0.68, 0.72, 0.68]").install(monkeypatch)
    quad, info = agent_tools._detect_screen(_Ctx(workdir), {}, 3.0)
    assert stub.calls == 1
    assert quad is not None
    assert info["method"] == "vision"
    # ...and it remembers WHY it had to, so the reply can say so
    assert "confidence" in info["read_not_measured"]


def test_a_measured_screen_never_calls_vision(monkeypatch, workdir):
    """Cost and honesty both: the measurement is better AND cheaper, so the
    model is only asked when the pixels have already declined."""
    _stub_frames(monkeypatch, workdir)
    monkeypatch.setattr(screendet, "find_screen",
                        lambda frames: {"confidence": 0.9, "corners": QUAD,
                                        "method": "quad", "agreement": 3,
                                        "n_frames": 3})
    stub = _VisionStub("[0,0,1,0,0,1,1,1]").install(monkeypatch)
    quad, info = agent_tools._detect_screen(_Ctx(workdir), {}, 3.0)
    assert stub.calls == 0
    assert quad is not None and not info.get("read_not_measured")


def test_a_read_quad_that_is_not_sane_is_still_refused(monkeypatch, workdir):
    """quad_is_sane runs on the read exactly as on the measurement — the
    fallback lowers the number of round trips, never the bar. A bow-tie (one
    corner folded past another) renders as a torn smear rather than failing, so
    it has to die here."""
    _stub_frames(monkeypatch, workdir)
    monkeypatch.setattr(screendet, "find_screen",
                        lambda frames: {"error": "no candidates"})
    _VisionStub("[0.2,0.2, 0.8,0.2, 0.8,0.8, 0.2,0.8]").install(monkeypatch)
    quad, why = agent_tools._detect_screen(_Ctx(workdir), {}, 3.0)
    assert quad is None
    assert "not a usable quadrilateral" in why


@pytest.mark.parametrize("answer, label", [
    ("[0.2,0.8, 0.8,0.8, 0.2,0.2, 0.8,0.2]", "bottom row first"),
    ("[0.8,0.2, 0.2,0.2, 0.8,0.8, 0.2,0.8]", "right column first"),
    ("[0.8,0.8, 0.2,0.8, 0.8,0.2, 0.2,0.2]", "both"),
])
def test_a_read_in_the_wrong_ORDER_is_corrected_not_pinned_mirrored(
        monkeypatch, answer, label):
    """The mistake geometry cannot catch. A model that answers bottom-row-first
    hands back a quad that is convex and consistently wound — quad_is_sane
    passes it — and the content lands upside down on the glass. Rows and columns
    are un-swapped by their own coordinates."""
    _VisionStub(answer).install(monkeypatch)
    quad, why = agent_tools._vision_screen_corners(None, "f.jpg")
    assert why is None, label
    assert quad == [0.2, 0.2, 0.8, 0.2, 0.2, 0.8, 0.8, 0.8], label


def test_a_correctly_ordered_read_is_left_alone(monkeypatch):
    """Including an ANGLED screen, where the corners are deliberately not a
    rectangle — the un-swap must not "tidy" a real perspective."""
    _VisionStub("[0.10,0.14, 0.86,0.05, 0.12,0.79, 0.88,0.93]").install(monkeypatch)
    quad, why = agent_tools._vision_screen_corners(None, "f.jpg")
    assert why is None
    assert quad == [0.10, 0.14, 0.86, 0.05, 0.12, 0.79, 0.88, 0.93]


def test_a_portrait_region_cannot_host_a_landscape_recording():
    """Round 62, project 246: the bright detector latched a tall shelf —
    0.34x0.66 of a 16:9 frame — at 0.66 confidence, and a LANDSCAPE Mac
    recording was flattened onto the furniture beside the laptop. The
    contradiction was checkable: foreshortening narrows a screen, it does
    not turn it portrait."""
    shelf = [0.60, 0.15, 0.94, 0.15, 0.60, 0.81, 0.94, 0.81]  # 0.34 x 0.66
    ok, why = agent_tools._quad_plausible_for(shelf, 16 / 9, 2880 / 1800)
    assert not ok and "taller" in why
    # the real laptop screen in the same shot: landscape, modestly angled
    laptop = [0.30, 0.55, 0.62, 0.52, 0.31, 0.75, 0.63, 0.74]
    ok, _ = agent_tools._quad_plausible_for(laptop, 16 / 9, 2880 / 1800)
    assert ok
    # no known content shape -> the check stands aside rather than guessing
    ok, _ = agent_tools._quad_plausible_for(shelf, 16 / 9, None)
    assert ok


def test_the_read_prompt_asks_for_the_glass_not_the_laptop():
    """The single most common way this goes wrong: corners around the whole
    device body rather than the lit display, which pins the content over the
    keyboard and the bezel."""
    p = agent_tools._SCREEN_VISION_PROMPT
    assert "inside the bezel" in p
    assert "not the whole laptop" in p
    # and the corner ORDER, which is the other way it silently goes wrong
    assert "bottom_left_x" in p and p.index("top_right_x") < p.index("bottom_left_x")


# ------------------------------------------------------------------ #
#  5. Round 63 — the pin RIDES a tracked screen                       #
# ------------------------------------------------------------------ #

def _wobble_path(dur=TAKE_S, amp=0.02, knots=7):
    """A corner_path that drifts the whole quad sideways like hand shake and
    returns to the measured quad at the arrival frame (the tool's contract:
    `corners` is the arrival quad, the path's last entry equals it)."""
    path = []
    for i in range(knots):
        t = dur * i / (knots - 1)
        dx = amp * np.sin(2 * np.pi * i / (knots - 1))
        q = list(QUAD)
        for j in range(0, 8, 2):
            q[j] = round(q[j] + float(dx), 5)
        path.append([round(t, 3)] + [round(v, 5) for v in q])
    # last knot back on the measured quad exactly
    path[-1] = [round(dur, 3)] + [round(v, 5) for v in QUAD]
    return path


def test_piecewise_linear_expr_lerps_and_holds():
    e = renderer._piecewise_linear_expr("on", [(0.0, 0.2), (1.0, 0.4),
                                              (2.0, 0.1)])
    assert abs(_ev(e, -0.5) - 0.2) < 1e-6          # held before first knot
    assert abs(_ev(e, 0.0) - 0.2) < 1e-6
    assert abs(_ev(e, 0.5) - 0.3) < 1e-6           # mid-lerp
    assert abs(_ev(e, 1.5) - 0.25) < 1e-6
    assert abs(_ev(e, 2.5) - 0.1) < 1e-6           # held after last knot


def test_corner_path_pin_follows_the_path_and_still_lands_identity():
    """With a corner_path, the pin's start sits on the path's FIRST quad, the
    middle follows the lerped quad, and the arrival frame is still the exact
    full-frame identity — the round-55 handoff invariant survives tracking."""
    dur = TAKE_S
    path = _wobble_path(dur)
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth",
            "corner_path": path}
    static = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    # start: first path quad == QUAD here (sin(0)=0), so it matches static
    got0 = _content_corners(lock, 0, dur)
    want0 = _content_corners(static, 0, dur)
    for (gx, gy), (wx, wy) in zip(got0, want0):
        assert abs(gx - wx) < 0.75 and abs(gy - wy) < 0.75
    # mid-window: the pin has moved with the path (knot 1.5/6 of the wobble
    # is off-quad), so it must NOT match the static pin
    on_mid = int(round(dur * FPS * 0.25))
    got_mid = _content_corners(lock, on_mid, dur)
    want_static = _content_corners(static, on_mid, dur)
    assert any(abs(g[0] - s[0]) > 1.5 for g, s in zip(got_mid, want_static)), \
        "corner_path had no effect on the mid-window pin"
    # the ARRIVAL instant (round 63b: the window end minus the hold) is the
    # exact identity, exactly as without a path
    hold = renderer.screen_lock_hold(dur)
    on_end = int(round((dur - hold) * FPS)) - 1
    got_end = _content_corners(lock, on_end, dur)
    frame_corners = [(0.0, 0.0), (W, 0.0), (0.0, H), (W, H)]
    for (gx, gy), (fx, fy) in zip(got_end, frame_corners):
        assert abs(gx - fx) < 1.0 and abs(gy - fy) < 1.0, \
            f"tracked pin does not land identity: {(gx, gy)} vs {(fx, fy)}"


@needs_ffmpeg
def test_corner_path_renders_and_content_follows(workdir, room, clip):
    """Pixel claim: rendered with a wobbling corner_path, the content's left
    edge mid-window sits where the PATH says, not where the static quad says."""
    dur = TAKE_S
    edl = _takeover_edl("clip.mp4")
    edl["overlays"][0]["screen"]["corner_path"] = _wobble_path(dur)
    out = os.path.join(workdir, "takeover_tracked.mp4")
    _render(room, clip, edl, out)
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth",
            "corner_path": _wobble_path(dur)}
    _f0, f1 = renderer.screen_appear_window(lock, dur, float(FPS))
    dt = f1 + 0.05                  # first fully opaque moment (round 64)
    f = _frame_at(out, 3.0 - dur + dt)
    m = _content_mask(f)
    ys, xs = np.where(m)
    assert m.sum() > 200
    on = int(round(dt * FPS))
    want = _content_corners(lock, on, dur)
    wx = [p[0] for p in want]
    wy = [p[1] for p in want]
    assert abs(xs.min() - max(0, min(wx))) < 12, (xs.min(), min(wx))
    assert abs(ys.min() - max(0, min(wy))) < 12, (ys.min(), min(wy))


@needs_ffmpeg
def test_frame_is_all_content_well_before_the_cut(rendered):
    """Round 63b, the user's own words: 'it doesn't make sense to switch
    scenes unless the first one is zoomed close to full frame'. The room must
    be fully covered from the ARRIVAL on — the whole hold rides full-frame
    content, so the scene switch happens inside an already-complete picture,
    never in the final blink of the dive."""
    hold = renderer.screen_lock_hold(TAKE_S)
    f = _frame_at(rendered, 3.0 - hold + 2.0 / FPS)
    m = _content_mask(f)
    assert m.mean() > 0.985, \
        f"room still visible at the arrival: {m.mean():.3f} content"


def test_land_false_removes_both_settle_terms():
    """settle:false (ScreenLock.land=False, round 71g): the filtergraph must
    carry NO through-cut overshoot — neither the program-side post-cut sin
    nor the overlay-side grow. A real user watched the render 'zoom on the
    screen recording then return' after the handoff."""
    land_tag = f"{renderer.SCREEN_LAND_ZOOM:.3f}*sin"

    def _graph(extra):
        e = _takeover_edl("clips/1/c.mov")
        e["overlays"][0]["screen"].update(extra)
        edl = validate_edl(dict(e), ROOM_S).model_dump()
        tl = Timeline(edl["keep"], edl["inserts"], [])
        index = {"video": {"duration": ROOM_S}, "words": [], "sentences": []}
        return renderer.build_filtergraph(
            edl, ROOM_S, True, tl, None, [], index, preview=False,
            W=W, H=H, fps=float(FPS),
            insert_inputs=[(1, edl["inserts"][0], True)],
            overlay_inputs=[(2, edl["overlays"][0])])

    assert land_tag in _graph({})               # default: the settle exists
    assert land_tag not in _graph({"land": False})
