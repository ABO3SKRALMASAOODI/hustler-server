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


def _render(src, clip_path, edl_dict, out):
    edl = validate_edl(dict(edl_dict), ROOM_S).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    index = {"video": {"duration": ROOM_S}, "words": [], "sentences": []}
    # Input layout mirrors render_edl: [0] main, then inserts, then overlays.
    graph = renderer.build_filtergraph(
        edl, ROOM_S, True, tl, None, [], index, preview=False,
        W=W, H=H, fps=float(FPS),
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
    return eval(expr.replace("on", str(float(on))),
                {"clip": lambda v, a, b: max(a, min(b, v)),
                 "between": lambda t, a, b: 1.0 if a <= t <= b else 0.0,
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


def test_pin_ends_exactly_on_the_frame():
    """The handoff is only invisible if the arrival is exact. The last frame
    a filter emits inside the window is at dur - 1/fps, which is why the
    progress denominator is the window minus one frame — with the naive
    denominator this lands over a pixel short."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    last = int(round((TAKE_S - 1.0 / FPS) * FPS))
    got = _content_corners(lock, last)
    want = [(0, 0), (W, 0), (0, H), (W, H)]
    for (gx, gy), (wx, wy) in zip(got, want):
        assert abs(gx - wx) < 0.02 and abs(gy - wy) < 0.02


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
    """push=0 leaves the shot alone; the content still has to arrive."""
    lock = {"corners": list(QUAD), "push": 0.0, "ease": "smooth"}
    _cx, _cy, z_end = renderer.screen_lock_geometry(lock)
    assert abs(z_end - 1.0) < 1e-9
    last = int(round((TAKE_S - 1.0 / FPS) * FPS))
    got = _content_corners(lock, last)
    for (gx, gy), (wx, wy) in zip(got, [(0, 0), (W, 0), (0, H), (W, H)]):
        assert abs(gx - wx) < 0.02 and abs(gy - wy) < 0.02


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
def test_content_is_glued_inside_the_screen_at_the_start(rendered):
    """The claim an ordinary overlay cannot make: the content is IN the shot,
    filling the glass and nothing outside it."""
    f = _frame_at(rendered, 3.0 - TAKE_S + 0.02)
    m = _content_mask(f)
    ys, xs = np.where(m)
    assert m.sum() > 200, "no content visible on the screen at all"
    qx, qy, qw, qh = quad_bbox(QUAD)
    assert abs(xs.min() / W - qx) < 0.03, xs.min() / W
    assert abs(xs.max() / W - (qx + qw)) < 0.03, xs.max() / W
    assert abs(ys.min() / H - qy) < 0.03, ys.min() / H
    assert abs(ys.max() / H - (qy + qh)) < 0.03, ys.max() / H


@needs_ffmpeg
def test_content_rides_the_push_instead_of_floating_over_it(rendered):
    """Mid-move the content is still on the glass — same centre, grown by the
    camera. An overlay drawn above the zoom would hold its size here."""
    lock = {"corners": list(QUAD), "push": 1.0, "ease": "smooth"}
    t_mid = 3.0 - TAKE_S / 2.0
    f = _frame_at(rendered, t_mid)
    m = _content_mask(f)
    ys, xs = np.where(m)
    assert m.sum() > 500
    on = int(round((TAKE_S / 2.0) * FPS))
    want = _content_corners(lock, on)
    wx = [p[0] for p in want]
    wy = [p[1] for p in want]
    # Predicted from the resolver, clipped to the frame the same way the
    # renderer's output is.
    assert abs(xs.min() - max(0, min(wx))) < 12, (xs.min(), min(wx))
    assert abs(ys.min() - max(0, min(wy))) < 12, (ys.min(), min(wy))
    # and it really did grow
    start = _content_mask(_frame_at(rendered, 3.0 - TAKE_S + 0.02)).sum()
    assert m.sum() > start * 1.6


@needs_ffmpeg
def test_content_covers_the_whole_frame_when_the_push_lands(rendered):
    f = _frame_at(rendered, 3.0 - 1.5 / FPS)
    m = _content_mask(f)
    assert m.mean() > 0.985, f"only {m.mean():.3f} of the frame is content"


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
