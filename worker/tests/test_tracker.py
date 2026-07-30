"""Round 63 — the tracker that lets the takeover pin ride a wobbling screen.

The claims a track makes are position claims, so they are checked against
synthetic footage whose true motion is known to the pixel: a textured screen
drifting like hand shake must come back as quads that follow it; a tripod
shot must come back as "static — keep the plain pin"; a screen that gets
covered must fail HONESTLY rather than returning a guess.

Run:  python -m pytest tests/test_tracker.py -q     (from worker/)
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

import tracker                                                # noqa: E402

pytest.importorskip("cv2")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS, DUR = 640, 360, 30, 1.6
# the screen at t=0, in fractions (frontal rectangle)
SX0, SY0, SW, SH = 160, 90, 240, 150
QUAD0 = [SX0 / W, SY0 / H, (SX0 + SW) / W, SY0 / H,
         SX0 / W, (SY0 + SH) / H, (SX0 + SW) / W, (SY0 + SH) / H]


def _screen_tile():
    rng = np.random.default_rng(5)
    tile = np.kron(rng.integers(60, 220, (SH // 6 + 1, SW // 6 + 1, 3),
                                np.uint8),
                   np.ones((6, 6, 1), np.uint8))[:SH, :SW]
    tile[::12, :, :] = 240                     # "UI" lines to track
    tile[:, ::16, :] = 30
    return tile.astype(np.uint8)


def _frames(motion):
    """Frames with the screen at (SX0+dx(t), SY0+dy(t)); the room is a static
    textured wall so the tracker has DISTRACTORS outside the quad."""
    rng = np.random.default_rng(9)
    room = np.kron(rng.integers(20, 70, (H // 8 + 1, W // 8 + 1, 3), np.uint8),
                   np.ones((8, 8, 1), np.uint8))[:H, :W].astype(np.uint8)
    tile = _screen_tile()
    n = int(DUR * FPS)
    for i in range(n):
        t = i / float(n - 1)
        dx, dy = motion(t)
        f = room.copy()
        x, y = SX0 + int(round(dx)), SY0 + int(round(dy))
        f[y - 4:y + SH + 4, x - 4:x + SW + 4] = 12       # bezel
        f[y:y + SH, x:x + SW] = tile
        yield f


def _encode(path, frames):
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
         "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "replace")
    assert p.wait() == 0, err


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round63trk_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@needs_ffmpeg
def test_track_follows_a_wobbling_screen(workdir):
    """Hand-shake motion (+-8px sinusoid) comes back as quads that follow the
    true position within a couple of pixels, ending near the true end."""
    def motion(t):
        return 8.0 * np.sin(2 * np.pi * t), 5.0 * np.sin(2 * np.pi * t * 1.7)

    src = os.path.join(workdir, "wobble.mp4")
    _encode(src, _frames(motion))
    quads, quality = tracker.track_quad(src, 0.0, DUR, list(QUAD0))
    assert quads, f"track refused: {quality}"
    assert quality["alive"] > 0.5
    # spot-check three sample times against the true motion
    n = int(DUR * FPS)
    for entry in quads[1::max(1, len(quads) // 4)]:
        t_rel = entry[0]
        tt = t_rel / ((n - 1) / float(FPS))
        dx, dy = motion(min(tt, 1.0))
        got_x = entry[1] * W          # top-left corner x, px
        got_y = entry[2] * H
        assert abs(got_x - (SX0 + dx)) < 4.0, (t_rel, got_x, SX0 + dx)
        assert abs(got_y - (SY0 + dy)) < 4.0, (t_rel, got_y, SY0 + dy)


@needs_ffmpeg
def test_static_screen_says_static(workdir):
    src = os.path.join(workdir, "still.mp4")
    _encode(src, _frames(lambda t: (0.0, 0.0)))
    quads, quality = tracker.track_quad(src, 0.0, DUR, list(QUAD0))
    assert quads is None
    assert "still" in (quality.get("why") or "")


@needs_ffmpeg
def test_covered_screen_fails_honestly(workdir):
    """A hand (a big flat rectangle) covers the screen mid-window: the track
    must refuse, not hand back a quad it invented."""
    def cover_frames():
        for i, f in enumerate(_frames(lambda t: (6.0 * t, 0.0))):
            if i > int(DUR * FPS * 0.4):
                f = f.copy()
                f[SY0 - 20:SY0 + SH + 20, SX0 - 20:SX0 + SW + 40] = 128
            yield f

    src = os.path.join(workdir, "covered.mp4")
    _encode(src, cover_frames())
    quads, quality = tracker.track_quad(src, 0.0, DUR, list(QUAD0))
    assert quads is None, "returned a track through a covered screen"
