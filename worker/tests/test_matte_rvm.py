"""Round 69 — the text-behind matte is a VIDEO matting model, not a stack of
per-frame opinions.

The user filmed matte v9 failing five ways in one afternoon on project 300,
and all five are one defect: per-frame thresholded decisions flicker at the
decision boundary. Measured on the exact failing window (see matte.py's v10
block): a 19.4%-of-frame mask change between two consecutive frames, the
armchair flipping fully-masked/fully-carved 20 times, letters printing ON the
walker while a chair patch strobed beside him. v10 replaces the gate cascade
with RobustVideoMatting — recurrent state carried frame to frame, run on
EVERY mask frame, soft alpha shipped untouched. Same window after: 1.6% max
change, chair never claimed, walker never dropped.

The model itself was validated against the real footage during round 69 (the
bench lives in the round notes); what THESE tests pin is everything around
it, with personseg.rvm_stream monkeypatched to a deterministic stand-in
faithful to the real model's measured habits (people ~1.0, furniture ~0.0,
soft edges): path selection, the soft alpha surviving to the encoded mask
untouched, the budget degrading RATE rather than sampling (no lerp ghosts),
the refusal bounds' wording, and the executor version handshake that keeps a
stale executor from poisoning the v10 cache with v9 masks.

Run:  python -m pytest tests/test_matte_rvm.py -q        (from worker/)
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

import config                                                 # noqa: E402
import matte                                                  # noqa: E402
import personseg                                              # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS = 640, 360, 30
DUR = 3.0
N = int(DUR * FPS)

WALKER = (48, 48, 58)          # BGR — the dark-on-dark regime of the real clip
CHAIR = (60, 50, 120)
BG = (34, 34, 38)
BAR_W, BAR_Y = 80, (H // 4, 3 * H // 4)
# Below the walker's band with clear air between them, so "the chair is never
# masked" can be asserted in EVERY frame — the walker crossing IN FRONT of
# furniture needs no test here: RVM has no gates for a crossing to confuse,
# which is the entire point of v10.
CHAIR_BOX = (int(W * 0.70), int(H * 0.82), int(W * 0.95), int(H * 0.97))


def _frame(i, n, walker=True, chair=True):
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    if chair:
        x0, y0, x1, y1 = CHAIR_BOX
        img[y0:y1, x0:x1] = CHAIR
    if walker:
        x = int((i / max(1, n - 1)) * (W - BAR_W))
        img[BAR_Y[0]:BAR_Y[1], x:x + BAR_W] = WALKER
    return img


def _encode(path, frames):
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
         "-pix_fmt", "yuv420p", "-an", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "replace")
    assert p.wait() == 0, err


class _FakeRVM:
    """Stand-in with the real model's measured habits: emphatic on the person
    (0.99), essentially silent on furniture (0.04 — the real chair measured
    0.008 of its region claimed), soft everywhere. Counts its steps so tests
    can assert EVERY encoded frame was inferred — the no-lerp property."""

    def __init__(self):
        self.steps = 0

    def alpha(self, f):
        a = np.zeros(f.shape[:2], np.float32)
        a[np.all(np.abs(f - np.array(CHAIR, np.int16)) < 6, axis=2)] = 0.04
        a[np.all(np.abs(f - np.array(WALKER, np.int16)) < 6, axis=2)] = 0.99
        return a

    def step(self, frame_bgr, cv2):
        self.steps += 1
        return self.alpha(frame_bgr.astype(np.int16))


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round69_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def walk(workdir):
    path = os.path.join(workdir, "walk.mp4")
    _encode(path, [_frame(i, N) for i in range(N)])
    return path


@pytest.fixture
def fake_rvm(monkeypatch):
    fake = _FakeRVM()
    monkeypatch.setattr(personseg, "rvm_available", lambda: True)
    monkeypatch.setattr(personseg, "rvm_stream", lambda w, h: fake)
    return fake


def _decode_mask(path):
    frames = list(matte._decode(path, 0.0, DUR + 1.0, W, H))
    assert frames
    return [f[:, :, 0] for f in frames]


@needs_ffmpeg
def test_rvm_is_the_primary_and_the_chair_is_never_claimed(walk, workdir,
                                                           fake_rvm):
    out = os.path.join(workdir, "rvm_walk.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H,
                                  box=(0.1, 0.35, 0.8, 0.3))
    assert res["ok"], res
    assert res["method"] == "person"
    assert res["engine"] == "rvm"
    assert res["matte_version"] == matte.VERSION
    assert res["fps"] == FPS                 # under budget: full mask rate
    masks = _decode_mask(out)
    # every encoded frame came from its own forward pass — no sampling, no
    # interpolation between two subject positions (the round-68 lerp ghost)
    assert fake_rvm.steps == len(masks) == res["frames"]
    x0, y0, x1, y1 = CHAIR_BOX
    chair_on = max(float((m[y0 + 8:y1 - 8, x0 + 8:x1 - 8] > 127).mean())
                   for m in masks)
    assert chair_on < 0.02, f"furniture in the mask: {chair_on}"
    mid = masks[len(masks) // 2]
    assert float((mid[BAR_Y[0] + 10:BAR_Y[1] - 10, :] > 127).mean()) > 0.05
    assert res["text_covered"] > 0.02


@needs_ffmpeg
def test_the_soft_alpha_ships_untouched(walk, workdir, monkeypatch):
    """No threshold, no vote, no morphology, no feather: a region the model
    holds at 0.35 must land in the encoded mask AT 0.35-of-255 — not 0, not
    255, not fattened. This is what makes the composite a real matte."""
    strip = (slice(20, 60), slice(20, 120))

    class _Soft(_FakeRVM):
        def step(self, frame_bgr, cv2):
            self.steps += 1
            a = self.alpha(frame_bgr.astype(np.int16))
            a[strip] = 0.35
            return a

    monkeypatch.setattr(personseg, "rvm_available", lambda: True)
    monkeypatch.setattr(personseg, "rvm_stream", lambda w, h: _Soft())
    out = os.path.join(workdir, "rvm_soft.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    mid = _decode_mask(out)[N // 2]
    v = float(mid[30:50, 40:100].mean())
    assert 60 < v < 115, f"soft alpha was mangled: {v} (expected ~89)"


@needs_ffmpeg
def test_the_budget_halves_the_rate_and_never_lerps(walk, workdir,
                                                    monkeypatch, fake_rvm):
    """A window over budget masks at HALF rate — every emitted frame is still
    its own inference, so the mask can trail a limb by one mask frame but can
    never show the subject half-transparent at two positions at once."""
    monkeypatch.setattr(config, "MATTE_RVM_BUDGET", 45)
    out = os.path.join(workdir, "rvm_budget.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    assert res["fps"] == FPS / 2.0
    assert abs(res["frames"] - DUR * FPS / 2.0) <= 2
    assert fake_rvm.steps == res["frames"]
    masks = _decode_mask(out)
    band = slice(BAR_Y[0] + 10, BAR_Y[1] - 10)

    def centroid(m):
        xs = np.where(m[band, :] > 127)[1]
        return float(xs.mean()) if xs.size else None

    c_first, c_last = centroid(masks[1]), centroid(masks[-2])
    assert c_first is not None and c_last is not None
    assert c_last - c_first > W * 0.4, (c_first, c_last)
    # binary-transparency check at the half-rate: the walker is SOLID where
    # he is and ABSENT where he is not — never a 50% ghost of two positions
    on_vals = masks[len(masks) // 2][band, :]
    ghost = ((on_vals > 60) & (on_vals < 190)).mean()
    assert float(ghost) < 0.02, f"double-exposure ghost pixels: {ghost}"


@needs_ffmpeg
def test_rvm_outranks_u2net_when_both_are_present(walk, workdir, monkeypatch,
                                                  fake_rvm):
    def must_not_run(frame_bgr, cv2):
        raise AssertionError("u2net ran although RVM was available")
    monkeypatch.setattr(personseg, "available", lambda: True)
    monkeypatch.setattr(personseg, "segment", must_not_run)
    out = os.path.join(workdir, "rvm_rank.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    assert res["engine"] == "rvm"


@needs_ffmpeg
def test_refusals_still_speak_person(walk, workdir, monkeypatch):
    class _Nobody(_FakeRVM):
        def step(self, frame_bgr, cv2):
            return np.zeros(frame_bgr.shape[:2], np.float32)

    class _Everyone(_FakeRVM):
        def step(self, frame_bgr, cv2):
            return np.ones(frame_bgr.shape[:2], np.float32)

    monkeypatch.setattr(personseg, "rvm_available", lambda: True)
    monkeypatch.setattr(personseg, "rvm_stream", lambda w, h: _Nobody())
    out = os.path.join(workdir, "rvm_nobody.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert not res["ok"] and "person" in res["why"]
    assert not os.path.exists(out)

    monkeypatch.setattr(personseg, "rvm_stream", lambda w, h: _Everyone())
    out2 = os.path.join(workdir, "rvm_everyone.mp4")
    res = matte.measure_and_build(walk, out2, 0.0, DUR, width=W, height=H)
    assert not res["ok"] and "fills" in res["why"]
    assert not os.path.exists(out2)


@needs_ffmpeg
def test_the_version_handshake_stops_a_stale_executor(walk, workdir,
                                                      monkeypatch, fake_rvm):
    """The dispatcher keys its cache on ITS matte.VERSION; an executor
    running other code must refuse rather than upload a differently-built
    mask under that key (the round-60 silent-false-claim class). A payload
    without the field is an older dispatcher mid-deploy — accepted."""
    import storage
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dest: shutil.copyfile(walk, dest))
    up = []
    monkeypatch.setattr(storage, "upload_file",
                        lambda p, k, ct: up.append(k))
    base = {"storage_key": "proxies/1/x.mp4", "out_key": "matte/1/v.mp4",
            "start": 0.0, "dur": DUR, "width": W, "height": H}

    with pytest.raises(ValueError, match="redeploy"):
        matte.run_matte_job(None, {"payload": dict(base,
                                                   matte_version=matte.VERSION
                                                   - 1)})
    assert up == []

    res = matte.run_matte_job(None, {"payload": dict(
        base, matte_version=matte.VERSION)})
    assert res["ok"] and up == ["matte/1/v.mp4"]

    res = matte.run_matte_job(None, {"payload": dict(base)})
    assert res["ok"], "an old dispatcher's payload must still build"
