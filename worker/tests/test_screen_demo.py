"""Round 51 — first-class screen-recording demos.

The three additive tools, pinned against real ffmpeg output wherever a claim
is about pixels:

  1. add_zoom_path — a zoom whose centre AND strength travel through
     keyframes. Asserted by RENDERING 3s fixtures and comparing the frame at
     each keyframe boundary against the crop the keyframes describe, computed
     independently in Python. That is what makes "no hidden ramp" a fact
     rather than a comment: if the renderer added a courtesy ease at the
     window edges, the frame at t=0 would not be the untouched source frame.
  2. A zoom_path spanning a CUT stays on its footage. Path points are stored
     as fractions of their own window precisely so remap_program_items can
     slide the window without stranding them; this proves it end to end.
  3. enhance_cursor — locates a drawn pointer in synthetic footage, smooths a
     deliberately jittered path, and redraws it bigger. Also proves the
     honest floor: footage with NO pointer reports a low found_frac rather
     than inventing one.
  4. set_screen_frame / add_aspect_shift — plate geometry and the emitted
     graph, plus a real render that lands the picture inside the inset box
     with the backdrop colour outside it.
  5. showcase_demo's degradation ladder, at the level that does not need a
     database: which shape of zoom each kind of click track produces.

Run:  python -m pytest tests/test_screen_demo.py -q     (from worker/)
"""
import hashlib
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
import screenframe                                            # noqa: E402
import travel                                                 # noqa: E402
from schemas import EDLValidationError, validate_edl          # noqa: E402
from timeline import Timeline, remap_program_items            # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS, DUR = 640, 360, 30, 3.0
NFRAMES = int(W * 0)  # placeholder, real count computed per fixture


# ------------------------------------------------------------------ #
#  Fixtures: 3-second clips with unambiguous spatial structure        #
# ------------------------------------------------------------------ #

def _grid_frame(i, n):
    """A static, high-frequency grid with a unique colour per cell.

    Static on purpose: every assertion below compares a RENDERED frame against
    a crop of the SOURCE frame, and a moving background would make a
    one-frame timing difference look like a geometry bug.
    """
    img = np.zeros((H, W, 3), np.uint8)
    cols, rows = 8, 5
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * H // rows, (r + 1) * H // rows
            x0, x1 = c * W // cols, (c + 1) * W // cols
            img[y0:y1, x0:x1] = ((c * 31 + 20) % 256,
                                 (r * 47 + 40) % 256,
                                 ((c + r) * 23 + 60) % 256)
            img[y0:y0 + 2, x0:x1] = 255          # crisp cell borders
            img[y0:y1, x0:x0 + 2] = 255
    return img


def _encode(path, frames, fps=FPS, dims=None):
    w, h = dims or (W, H)
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={len(frames) / fps:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
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
    d = tempfile.mkdtemp(prefix="round51_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def grid_clip(workdir):
    n = int(DUR * FPS)
    path = os.path.join(workdir, "grid.mp4")
    _encode(path, [_grid_frame(i, n) for i in range(n)])
    return path


def _render(src, edl_dict, out, dur=DUR, workdir=None):
    """Build the graph the real renderer would and run it. Deliberately calls
    build_filtergraph rather than reimplementing the chain — a test that
    rendered its own graph would pass while the product broke."""
    edl = validate_edl(dict(edl_dict), dur).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    index = {"video": {"duration": dur}, "words": [], "sentences": []}
    extra, plate_idx, plate_box = [], None, None
    if (edl.get("effects") or {}).get("screen_frame"):
        plate_idx, plate_box = renderer._screen_frame_input(
            edl, workdir, W, H, FPS, tl.out_duration, extra, 1)
    graph = renderer.build_filtergraph(
        edl, dur, True, tl, None, [], index, preview=False,
        W=W, H=H, fps=float(FPS), plate_idx=plate_idx, plate_box=plate_box)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, *extra,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
           "-pix_fmt", "yuv420p", "-c:a", "aac", out]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    return out


def _frame_at(path, t, w=W, h=H):
    """One decoded frame, seeking to the FRAME whose presentation time is t."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", path,
         "-frames:v", "1", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    buf = p.stdout
    assert len(buf) >= w * h * 3, f"short frame at {t}s ({len(buf)} bytes)"
    return np.frombuffer(buf[:w * h * 3], np.uint8).reshape(h, w, 3)


def _phash(frame, size=16):
    """A perceptual hash: coarse luma blocks against their own mean. Stable
    across the H.264 requantisation that makes an exact byte hash useless for
    comparing a render with a locally-computed expectation."""
    import cv2
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA)
    bits = (small > small.mean()).flatten()
    return hashlib.sha1(np.packbits(bits).tobytes()).hexdigest()[:16]


def _hamming(a, b, size=16):
    import cv2
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (size, size),
                    interpolation=cv2.INTER_AREA)
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (size, size),
                    interpolation=cv2.INTER_AREA)
    return int(((ga > ga.mean()) != (gb > gb.mean())).sum())


def _expected_zoom(src_frame, z, cx, cy):
    """The frame zoompan produces: crop iw/z x ih/z at (iw-iw/z)*cx and scale
    back to WxH. Computed here from the keyframes alone — the renderer's
    expressions are never consulted, which is the point."""
    import cv2
    if z <= 1.0001:
        return src_frame
    cw, ch = int(round(W / z)), int(round(H / z))
    x = int(round((W - cw) * cx))
    y = int(round((H - ch) * cy))
    x = max(0, min(x, W - cw))
    y = max(0, min(y, H - ch))
    crop = src_frame[y:y + ch, x:x + cw]
    return cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR)


# ------------------------------------------------------------------ #
#  1. travel.py — the shared core                                     #
# ------------------------------------------------------------------ #

def test_waypoints_become_window_fractions():
    pts, err = travel.waypoints_to_path(
        [{"t": 2.0, "cx": 0.2, "cy": 0.3, "strength": 0.1},
         {"t": 3.0, "cx": 0.5, "cy": 0.5, "strength": 0.8},
         {"t": 4.0, "cx": 0.9, "cy": 0.7, "strength": 0.0}],
        2.0, 4.0, with_strength=True)
    assert err is None
    assert [p["f"] for p in pts] == [0.0, 0.5, 1.0]
    assert [p["s"] for p in pts] == [0.1, 0.8, 0.0]


def test_follow_paths_carry_no_strength_key():
    """A 'follow' path must dump exactly the keys round 45 wrote, or every
    showcase_demo EDL ever written changes signature and re-renders."""
    pts, err = travel.waypoints_to_path(
        [{"t": 0.0, "cx": 0.2, "cy": 0.2}, {"t": 1.0, "cx": 0.8, "cy": 0.8}],
        0.0, 1.0)
    assert err is None
    assert all("s" not in p for p in pts)


def test_a_single_position_is_not_a_path():
    _pts, err = travel.waypoints_to_path(
        [{"t": 1.0, "cx": 0.2, "cy": 0.2}, {"t": 1.0, "cx": 0.8, "cy": 0.8}],
        1.0, 5.0)
    assert err and "two keyframes" in err
    # a window with no duration is its own refusal, not a crash
    _pts, err = travel.waypoints_to_path(
        [{"t": 1.0, "cx": 0.2, "cy": 0.2}, {"t": 1.0, "cx": 0.8, "cy": 0.8}],
        1.0, 1.0)
    assert err


def test_ease_curves_are_what_they_claim():
    E = "cubic_in_out"
    assert travel.ease_value(0.0, E) == 0.0
    assert travel.ease_value(1.0, E) == 1.0
    assert travel.ease_value(0.5, E) == pytest.approx(0.5)
    # cubic in-out is slower than linear early and faster in the middle
    assert travel.ease_value(0.25, E) < 0.25
    assert travel.ease_value(0.75, E) > 0.75
    assert travel.ease_value(0.25, "linear") == 0.25
    # ease=None is LINEAR, not the default curve — that is what keeps a
    # round-45 'follow' zoom rendering exactly as it always did.
    assert travel.ease_value(0.25) == 0.25


def test_legacy_follow_expression_is_byte_identical():
    """The round-45 strings, reproduced exactly. Renders are cached by EDL
    fingerprint; a cosmetic change here silently re-encodes every demo."""
    z = {"id": "z", "start": 1.0, "end": 5.0, "strength": 0.4,
         "mode": "follow",
         "path": [{"f": 0.0, "cx": 0.2, "cy": 0.2},
                  {"f": 1.0, "cx": 0.8, "cy": 0.8}]}
    t = "on/30.000"
    assert travel.strength_term(z, t, 1.0, 5.0) == (
        "0.40*clip((on/30.000-1.000)/0.400,0,1)"
        "*clip((5.000-on/30.000)/0.400,0,1)")
    cx, _cy = travel.centre_terms(z, t, 1.0, 5.0)
    assert cx == ("(-0.3000+0.6000*clip((on/30.000-1.000)/4.000,0,1))"
                  "*between(on/30.000,1.000,5.000)")


# ------------------------------------------------------------------ #
#  2. add_zoom_path — rendered frames at the keyframe boundaries      #
# ------------------------------------------------------------------ #

PATH_EDL = {
    "keep": [[0.0, DUR]],
    "effects": {"zooms": [{
        "id": "zp1", "start": 0.0, "end": 3.0, "strength": 0.8,
        "mode": "path", "ease": "cubic_in_out",
        "path": [{"f": 0.0, "cx": 0.15, "cy": 0.5, "s": 0.0},
                 {"f": 0.5, "cx": 0.5, "cy": 0.5, "s": 0.8},
                 {"f": 1.0, "cx": 0.85, "cy": 0.5, "s": 0.0}]}]}}


def _path_state(f):
    """The (zoom, cx, cy) the keyframes describe at window fraction f —
    computed from the EDL, independently of the renderer."""
    pts = PATH_EDL["effects"]["zooms"][0]["path"]
    def val(key, default):
        v = pts[0].get(key, default)
        for a, b in zip(pts, pts[1:]):
            if b["f"] <= a["f"]:
                continue
            u = travel.ease_value((f - a["f"]) / (b["f"] - a["f"]),
                                  PATH_EDL["effects"]["zooms"][0]["ease"])
            v += (b.get(key, default) - a.get(key, default)) * u
        return v
    return 1.0 + val("s", 0.0), val("cx", 0.5), val("cy", 0.5)


@needs_ffmpeg
def test_zoom_path_frame_matches_its_keyframes(grid_clip, workdir):
    out = _render(grid_clip, PATH_EDL, os.path.join(workdir, "path.mp4"),
                  workdir=workdir)
    src0 = _frame_at(grid_clip, 0.5)          # the source is static
    for t in (0.0, 0.75, 1.5, 2.25, 2.9):
        z, cx, cy = _path_state(t / 3.0)
        got = _frame_at(out, t)
        want = _expected_zoom(src0, z, cx, cy)
        d = _hamming(got, want)
        assert d <= 24, (f"t={t}s: rendered frame is not the crop the "
                         f"keyframes describe (z={z:.3f} cx={cx:.3f}), "
                         f"hamming={d}")


@needs_ffmpeg
def test_no_hidden_ramp_at_the_window_edges(grid_clip, workdir):
    """The keyframes say strength 0 at both ends. If the renderer added an
    'ease' courtesy ramp — which mode 'follow' deliberately does — the first
    and last frames would be pushed in, and every assertion a caller makes
    about where the frame is would be off."""
    out = _render(grid_clip, PATH_EDL, os.path.join(workdir, "path2.mp4"),
                  workdir=workdir)
    src0 = _frame_at(grid_clip, 0.5)
    assert _hamming(_frame_at(out, 0.0), src0) <= 6
    assert _hamming(_frame_at(out, 2.95), src0) <= 8


@needs_ffmpeg
def test_the_frame_actually_travels(grid_clip, workdir):
    """A guard against the whole thing collapsing to a static zoom: the
    picture at a quarter through and three quarters through must differ."""
    out = _render(grid_clip, PATH_EDL, os.path.join(workdir, "path3.mp4"),
                  workdir=workdir)
    assert _hamming(_frame_at(out, 0.75), _frame_at(out, 2.25)) >= 10


def test_per_point_strength_needs_mode_path():
    bad = {"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "mode": "follow",
         "path": [{"f": 0.0, "cx": 0.2, "cy": 0.2, "s": 0.1},
                  {"f": 1.0, "cx": 0.8, "cy": 0.8, "s": 0.9}]}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 10.0)


def test_ease_only_applies_to_mode_path():
    bad = {"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "mode": "ease",
         "ease": "linear"}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 10.0)


# ------------------------------------------------------------------ #
#  3. A zoom_path spanning a cut stays on its footage                 #
# ------------------------------------------------------------------ #

def test_zoom_path_stays_anchored_across_a_cut():
    """The zoom sits on source 10-14s. Cutting 0-5s out of the front moves
    that footage to output 5-9s, and the zoom must go with it — window AND
    waypoints. Fractions are what make the waypoints ride along; if they were
    absolute times they would be stranded on the old positions."""
    edl = validate_edl({
        "keep": [[0.0, 20.0]],
        "effects": {"zooms": [{
            "id": "zp1", "start": 10.0, "end": 14.0, "strength": 0.8,
            "mode": "path", "ease": "cubic_in_out",
            "path": [{"f": 0.0, "cx": 0.2, "cy": 0.5, "s": 0.0},
                     {"f": 0.25, "cx": 0.5, "cy": 0.5, "s": 0.8},
                     {"f": 1.0, "cx": 0.9, "cy": 0.5, "s": 0.0}]}]}},
        20.0).model_dump()
    old_tl = Timeline(edl["keep"], [], [])
    new_keep = [[5.0, 20.0]]
    new_tl = Timeline(new_keep, [], [])
    edl["keep"] = new_keep
    notes = remap_program_items(edl, old_tl, new_tl)
    z = edl["effects"]["zooms"][0]
    assert (z["start"], z["end"]) == (5.0, 9.0), (z, notes)
    # the waypoints are untouched — they are relative to the window
    assert [p["f"] for p in z["path"]] == [0.0, 0.25, 1.0]
    assert [p["cx"] for p in z["path"]] == [0.2, 0.5, 0.9]
    # and it still validates against the SHORTER program
    validate_edl(edl, 20.0)


def test_zoom_path_dies_with_its_footage():
    edl = validate_edl({
        "keep": [[0.0, 20.0]],
        "effects": {"zooms": [{
            "id": "zp1", "start": 10.0, "end": 14.0, "strength": 0.5,
            "mode": "path",
            "path": [{"f": 0.0, "cx": 0.2, "cy": 0.5},
                     {"f": 1.0, "cx": 0.9, "cy": 0.5}]}]}},
        20.0).model_dump()
    old_tl = Timeline(edl["keep"], [], [])
    new_tl = Timeline([[0.0, 9.0]], [], [])
    edl["keep"] = [[0.0, 9.0]]
    notes = remap_program_items(edl, old_tl, new_tl)
    assert not (edl.get("effects") or {}).get("zooms")
    assert any("no longer in the edit" in n for n in notes), notes


# ------------------------------------------------------------------ #
#  4. enhance_cursor                                                  #
# ------------------------------------------------------------------ #

# The cursor fixtures are 720p, not the 640x360 the zoom fixtures use, and
# that is not incidental: a pointer is ~26 pixels of a real screen recording
# whatever its resolution, and at 360p it would be ten. Testing the detector on
# a ten-pixel arrow would be testing a case the product never sees.
CW, CH = 1280, 720
CURSOR_PX = 26.0


def _cursor_clip(path, n, jitter=True, draw=True):
    """A screen-like background with a pointer tracking left to right, plus
    per-frame tremor — the thing smoothing is supposed to remove."""
    import cv2
    import cursor as cursorlib
    rng = np.random.RandomState(11)
    sp = cursorlib.sprite(CURSOR_PX)
    frames = []
    for i in range(n):
        img = np.full((CH, CW, 3), 245, np.uint8)
        for k in range(8):                       # window chrome / text rows
            y = 60 + k * 70
            cv2.rectangle(img, (60, y), (60 + 200 + k * 90, y + 26),
                          (205, 205, 205), -1)
        cv2.rectangle(img, (0, 0), (CW, 40), (60, 60, 60), -1)
        cv2.rectangle(img, (900, 120), (1200, 600), (230, 240, 255), -1)
        if draw:
            x = 200 + (CW - 500) * i / max(1, n - 1)
            y = CH * 0.55
            if jitter:
                x += rng.normal(0, 2.2)
                y += rng.normal(0, 2.2)
            cursorlib._blend(img, sp, x, y)
        frames.append(img)
    _encode(path, frames, dims=(CW, CH))
    return path


@needs_ffmpeg
def test_cursor_is_located_and_redrawn_bigger(workdir):
    import cursor as cursorlib
    n = int(2.0 * FPS)
    src = _cursor_clip(os.path.join(workdir, "cur.mp4"), n)
    out = os.path.join(workdir, "cur_big.mp4")
    stats = cursorlib.enhance(src, out, scale=3.0, smoothing=0.6,
                              click_times=[1.0], click_highlight=True)
    assert stats["found_frac"] > 0.8, stats
    assert stats["frames_drawn"] > 0.8 * stats["frames"]
    assert stats["clicks"] == 1
    # The redrawn pointer covers materially more dark-on-light area than the
    # original did: measure the ink in the band the cursor crosses.
    def ink(p, t):
        import cv2
        f = _frame_at(p, t, CW, CH)
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        band = g[int(CH * 0.42):int(CH * 0.72), :]
        return int((band < 120).sum())
    assert ink(out, 1.4) > ink(src, 1.4) * 1.8, (ink(out, 1.4), ink(src, 1.4))


@needs_ffmpeg
def test_the_pass_preserves_duration_and_audio(workdir):
    import cursor as cursorlib
    import media
    n = int(2.0 * FPS)
    src = _cursor_clip(os.path.join(workdir, "cur2.mp4"), n)
    out = os.path.join(workdir, "cur2_out.mp4")
    cursorlib.enhance(src, out, scale=2.0, smoothing=0.4)
    a, b = media.probe(src), media.probe(out)
    assert abs(float(a["duration"]) - float(b["duration"])) < 0.1
    assert (int(a["width"]), int(a["height"])) == (int(b["width"]),
                                                   int(b["height"]))
    assert b["has_audio"], "the cursor pass must not drop the audio track"


@needs_ffmpeg
def test_footage_with_no_pointer_reports_it(workdir):
    """The honest floor. A phone capture or a tap-driven demo has no cursor;
    the pass must say so instead of enlarging a piece of the UI."""
    import cursor as cursorlib
    n = int(1.5 * FPS)
    src = _cursor_clip(os.path.join(workdir, "nocur.mp4"), n, draw=False)
    tr = cursorlib.track(src)
    found = sum(1 for p in tr["points"] if p is not None) / len(tr["points"])
    assert tr["cursor_h"] is None or found < 0.15
    assert found < 0.15, f"found a pointer in {found:.0%} of cursor-less frames"


def test_smoothing_removes_tremor_but_keeps_the_move():
    """One-euro, not a moving average: the filtered path must be much
    steadier frame to frame while still covering the same ground."""
    import cursor as cursorlib
    rng = np.random.RandomState(3)
    n = 90
    # A hand DRAGGING and shaking: 45 px/s of travel with 2.5px of tremor.
    raw = [(100 + 1.5 * i + rng.normal(0, 2.5), 200 + rng.normal(0, 2.5), 0.9)
           for i in range(n)]
    sm = cursorlib.smooth_track(raw, 30.0, 0.8)

    def jerk(track):
        d = [abs(track[i + 1][0] - track[i][0]) for i in range(len(track) - 1)]
        return float(np.std(d))
    assert jerk(sm) < jerk([(p[0], p[1]) for p in raw]) * 0.5
    # …and it still travelled: end minus start within a few px of the truth
    assert abs((sm[-1][0] - sm[0][0]) - (1.5 * (n - 1))) < 20


def test_a_fast_flick_is_not_smeared():
    """The reason this is a one-euro filter and not a moving average. A
    deliberate 600 px/s throw across the screen must arrive when it arrives —
    an average heavy enough to kill tremor would land it late and short."""
    import cursor as cursorlib
    n = 60
    raw = [(100.0, 200.0, 0.9)] * 20 + \
          [(100.0 + 20.0 * (i - 19), 200.0, 0.9) for i in range(20, n)]
    sm = cursorlib.smooth_track(raw, 30.0, 1.0)
    truth = raw[-1][0] - raw[0][0]
    assert (sm[-1][0] - sm[0][0]) > truth * 0.9, (sm[-1][0], truth)


def test_gaps_are_interpolated_not_held():
    import cursor as cursorlib
    pts = [(0.0, 0.0, 0.9)] + [None] * 3 + [(40.0, 0.0, 0.9)]
    sm = cursorlib.smooth_track(pts, 30.0, 0.0)
    xs = [p[0] for p in sm]
    assert xs == [0.0, 10.0, 20.0, 30.0, 40.0]


def test_cursor_pass_changes_the_derived_source_fingerprint():
    """One derived file, one identity. And the pre-round-51 fingerprint must
    be untouched, or every cleaned object already in storage is orphaned."""
    from schemas import clean_fingerprint
    regions = [{"id": "r1", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1,
                "fill": "text"}]
    base = clean_fingerprint("sha", regions)
    assert clean_fingerprint("sha", regions, None) == base
    with_cursor = clean_fingerprint("sha", regions,
                                    {"scale": 2.0, "smoothing": 0.5,
                                     "click_highlight": True,
                                     "click_times": [1.0]})
    assert with_cursor != base
    # a different scale is a different file
    assert clean_fingerprint("sha", regions,
                             {"scale": 3.0, "smoothing": 0.5,
                              "click_highlight": True,
                              "click_times": [1.0]}) != with_cursor


def test_a_derived_source_that_derives_nothing_is_rejected():
    with pytest.raises(EDLValidationError):
        validate_edl({"keep": [[0.0, 10.0]],
                      "source_clean": {"asset_key": "k", "fp": "f",
                                       "regions": []}}, 10.0)


# ------------------------------------------------------------------ #
#  5. set_screen_frame + add_aspect_shift                             #
# ------------------------------------------------------------------ #

def test_plate_geometry_is_a_centred_uniform_inset():
    pw, ph, ox, oy = screenframe.picture_box(1920, 1080, 0.1)
    assert (pw, ph) == (1728, 972)
    assert ox == (1920 - pw) // 2 or abs(ox - (1920 - pw) / 2) <= 1
    # uniform: the inset picture keeps the output's aspect exactly
    assert abs((pw / ph) - (1920 / 1080)) < 0.01


def test_the_plate_has_a_transparent_hole_and_an_opaque_border(workdir):
    from PIL import Image
    spec = {"inset": 0.12, "radius": 0.05, "shadow": 0.6,
            "background": "#101018", "background2": "#402060",
            "direction": "diagonal"}
    path, (pw, ph, ox, oy) = screenframe.build_plate(
        os.path.join(workdir, "plate.png"), 640, 360, spec)
    a = np.array(Image.open(path).convert("RGBA"))
    assert a[oy + ph // 2, ox + pw // 2, 3] == 0, "picture area must be a hole"
    assert a[2, 2, 3] == 255, "the backdrop must be opaque"
    # rounded: the hole's own corner pixel is still (partly) covered
    assert a[oy + 1, ox + 1, 3] > 0, "corners are not rounded"
    # gradient: the two ends of the diagonal differ
    assert abs(int(a[2, 2, 0]) - int(a[-3, -3, 0])) > 20


@needs_ffmpeg
def test_screen_frame_renders_a_floating_window(grid_clip, workdir):
    edl = {"keep": [[0.0, DUR]],
           "effects": {"screen_frame": {
               "inset": 0.2, "radius": 0.04, "shadow": 0.5,
               "background": "#FF0000"}}}
    out = _render(grid_clip, edl, os.path.join(workdir, "sf.mp4"),
                  workdir=workdir)
    f = _frame_at(out, 1.5)
    # outside the inset box: the backdrop colour (BGR red = (0,0,255))
    b, g, r = f[4, 4]
    assert r > 150 and g < 90 and b < 90, f"backdrop is {f[4, 4]}"
    # inside: the grid, not the backdrop
    assert not (f[H // 2, W // 2][2] > 150 and f[H // 2, W // 2][1] < 90)


@needs_ffmpeg
def test_aspect_shift_closes_the_frame_in(grid_clip, workdir):
    edl = {"keep": [[0.0, DUR]],
           "effects": {"frame_shifts": [
               {"id": "as1", "at": 0.6, "ratio": "9:16", "duration_s": 0.6,
                "zoom": False, "color": "#000000"}]}}
    out = _render(grid_clip, edl, os.path.join(workdir, "as.mp4"),
                  workdir=workdir)
    before = _frame_at(out, 0.3)
    after = _frame_at(out, 2.5)
    # before the shift the full width is picture; after it, the sides are bars
    assert int(before[H // 2, 3].max()) > 30, "frame was already barred"
    assert int(after[H // 2, 3].max()) < 25, f"no pillarbox: {after[H//2, 3]}"
    # 9:16 inside 16:9 leaves a narrow strip — the centre is still picture
    assert int(after[H // 2, W // 2].max()) > 30
    # mid-morph the bar exists but is narrower than its final width
    mid = _frame_at(out, 0.75)
    def bar_w(fr):
        row = fr[H // 2]
        k = 0
        while k < W and int(row[k].max()) < 25:
            k += 1
        return k
    assert 0 < bar_w(mid) < bar_w(after), (bar_w(mid), bar_w(after))


def test_overlapping_aspect_shifts_are_rejected():
    bad = {"keep": [[0.0, 20.0]], "effects": {"frame_shifts": [
        {"id": "a", "at": 2.0, "ratio": "9:16", "duration_s": 1.0},
        {"id": "b", "at": 2.5, "ratio": "1:1", "duration_s": 1.0}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 20.0)


def test_ratio_window_maths():
    # 9:16 inside a 16:9 canvas: pillars, full height
    wf, hf = screenframe.ratio_window("9:16", 1920, 1080)
    assert wf == pytest.approx((9 / 16) / (16 / 9))
    assert hf == 1.0
    # 16:9 inside a 9:16 canvas: letterbox, full width
    wf, hf = screenframe.ratio_window("16:9", 1080, 1920)
    assert wf == 1.0 and hf == pytest.approx((9 / 16) / (16 / 9))
    # 'source' is the whole frame
    assert screenframe.ratio_window("source", 1920, 1080) == (1.0, 1.0)


def test_aspect_shift_is_content_anchored():
    """'Go vertical when he says X' belongs to the moment he says it."""
    edl = validate_edl({
        "keep": [[0.0, 20.0]],
        "effects": {"frame_shifts": [
            {"id": "as1", "at": 12.0, "ratio": "9:16"}]}}, 20.0).model_dump()
    old_tl = Timeline(edl["keep"], [], [])
    new_tl = Timeline([[4.0, 20.0]], [], [])
    edl["keep"] = [[4.0, 20.0]]
    remap_program_items(edl, old_tl, new_tl)
    assert edl["effects"]["frame_shifts"][0]["at"] == 8.0


# ------------------------------------------------------------------ #
#  6. Graph-level guards                                              #
# ------------------------------------------------------------------ #

def _graph(edl_dict, dur=10.0, gw=1280, gh=720):
    edl = validate_edl(dict(edl_dict), dur).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    index = {"video": {"duration": dur}, "words": [], "sentences": []}
    return renderer.build_filtergraph(edl, dur, True, tl, None, [], index,
                                      preview=False, W=gw, H=gh, fps=30.0)


def test_path_zoom_emits_an_eased_interpolation():
    g = _graph({"keep": [[0.0, 10.0]], "effects": {"zooms": [{
        "id": "z", "start": 1.0, "end": 5.0, "strength": 0.8, "mode": "path",
        "ease": "cubic_in_out",
        "path": [{"f": 0.0, "cx": 0.2, "cy": 0.5, "s": 0.0},
                 {"f": 1.0, "cx": 0.8, "cy": 0.5, "s": 0.8}]}]}})
    assert "zoompan" in g
    # the cubic Hermite curve, applied to the segment's own clip()
    assert "(3-2*(clip((on/30.000-1.000)/4.000,0,1)))" in g, g
    # the STRENGTH interpolates too (this is what 'path' adds over 'follow')
    assert "0.8000*" in g and "between(on/30.000,1.000,5.000)" in g


def test_linear_ease_emits_no_curve():
    g = _graph({"keep": [[0.0, 10.0]], "effects": {"zooms": [{
        "id": "z", "start": 1.0, "end": 5.0, "strength": 0.8, "mode": "path",
        "ease": "linear",
        "path": [{"f": 0.0, "cx": 0.2, "cy": 0.5, "s": 0.4},
                 {"f": 1.0, "cx": 0.8, "cy": 0.5, "s": 0.4}]}]}})
    assert "(3-2*" not in g


def test_an_edl_with_no_new_fields_emits_no_new_filters():
    """The regression that matters most: nothing here may touch an edit that
    uses none of it."""
    g = _graph({"keep": [[0.0, 10.0]], "effects": {"grade": "warm"}})
    assert "drawbox" not in g
    assert "overlay=0:0:format=auto" not in g


def test_aspect_shift_emits_time_varying_bars():
    g = _graph({"keep": [[0.0, 10.0]], "effects": {"frame_shifts": [
        {"id": "a", "at": 2.0, "ratio": "9:16", "duration_s": 0.8}]}})
    # overlay, NOT drawbox: drawbox evaluates its geometry once at config
    # time, so a bar whose width is an expression in t never moves.
    assert "drawbox" not in g
    assert g.count("eval=frame") == 2, "a pillarbox is two bars, no more"
    assert "clip((t-2.000)/0.800,0,1)" in g, g


def test_aspect_shift_zoom_rides_the_existing_zoompan():
    """One geometry filter, not two — and a zoom over the same moment must
    compose with it rather than be replaced by it."""
    g = _graph({"keep": [[0.0, 10.0]], "effects": {
        "zooms": [{"id": "z", "start": 1.0, "end": 3.0, "strength": 0.3}],
        "frame_shifts": [{"id": "a", "at": 5.0, "ratio": "1:1",
                          "duration_s": 0.5, "zoom": True}]}})
    assert g.count("zoompan") == 1, g
    assert "0.30*between(on/30.000,1.000,3.000)" in g


def test_both_axes_can_shift_without_stranding_a_split_output():
    """A 4:3 canvas is between 9:16 and 16:9, so a sequence through both
    varies the width AND the height track — four bars off one split. An
    unconnected split output is not a missing effect, it is a dead render."""
    g = _graph({"keep": [[0.0, 20.0]], "effects": {"frame_shifts": [
        {"id": "a", "at": 2.0, "ratio": "9:16", "duration_s": 0.5},
        {"id": "b", "at": 8.0, "ratio": "16:9", "duration_s": 0.5}]}},
        dur=20.0, gw=1440, gh=1080)
    assert "split=4" in g
    assert g.count("eval=frame") == 4
    for i in range(4):
        assert f"[fsb{i}]" in g, i
    # every declared split output is consumed exactly once as an overlay input
    for i in range(4):
        assert g.count(f"[fsb{i}]") == 2, i


def test_screen_frame_needs_its_plate_to_render():
    """No plate input (a Pillow failure, a missing file) must degrade to no
    framing — never to a graph referencing an input that is not there."""
    g = _graph({"keep": [[0.0, 10.0]], "effects": {"screen_frame": {
        "inset": 0.1, "radius": 0.03, "shadow": 0.4,
        "background": "#000000"}}})
    assert "overlay=0:0:format=auto" not in g


# ------------------------------------------------------------------ #
#  7. showcase_demo's degradation ladder                              #
# ------------------------------------------------------------------ #

def test_showcase_demo_accepts_any_clip_now():
    """It used to be gated on the browser recorder being available, which
    made it invisible on a deployment where the commonest input — a screen
    recording the user uploaded — is the only input."""
    import agent_tools
    assert not agent_tools._tool_disabled("showcase_demo")
    assert "click_times" in agent_tools.TOOLS["showcase_demo"][2]


def test_the_new_tools_are_registered_with_their_trigger_phrases():
    import agent_tools
    for name, phrase in (
            ("add_zoom_path", "make the zoom follow the cursor"),
            ("enhance_cursor", "the cursor is too small"),
            ("set_screen_frame", "floating rounded window on a gradient"),
            ("add_aspect_shift", "go vertical for this bit")):
        assert name in agent_tools.TOOLS, name
        assert name in agent_tools.WRITE_TOOLS, name
        assert phrase in agent_tools.TOOLS[name][1], (name, phrase)
    for name in ("remove_zoom_path", "remove_cursor_enhance",
                 "remove_screen_frame", "remove_aspect_shift"):
        assert name in agent_tools.TOOLS, f"{name} has no paired remover"


def test_every_new_tool_is_in_the_capabilities_digest():
    """The digest is what the model checks a request against; a write tool
    missing from it is a capability the agent will deny having."""
    import agent_tools
    digest = agent_tools.capabilities_digest()
    for name in ("add_zoom_path", "enhance_cursor", "set_screen_frame",
                 "add_aspect_shift"):
        assert f"- {name}(" in digest, name
