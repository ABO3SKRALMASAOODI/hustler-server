"""Round 64 — the text-behind matte finds the subject with a person model.

Four consecutive matte versions (v2-v5) failed on the same real footage: a
dark walker in a dark lamp-lit room differs from the background plate by less
than the sensor noise the photometric thresholds must clear, so the shipped
mask held 2.7% of a subject who visibly fills ~15% of the frame, and the title
printed straight across him. u2net_human_seg, run on the exact frame of the
exact failing render, returned the full silhouette — so v6 makes segmentation
the primary path (executor-side in production) and demotes the photometric
pipeline to the fallback for deployments without the model.

The model itself was validated against real pixels during round 64; what these
tests pin is everything AROUND it, with personseg monkeypatched to a
deterministic stand-in: the motion gate that drops furniture the model likes,
the standing-subject exception, the sampling budget's interpolation, the
refusal bounds' new meanings, and the executor job plumbing.

Run:  python -m pytest tests/test_matte_seg.py -q        (from worker/)
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

import matte                                                  # noqa: E402
import personseg                                              # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS = 640, 360, 30
DUR = 3.0
N = int(DUR * FPS)

# The scene: a dark room. The "walker" is a dark bar sliding left to right
# (the exact luminance regime the photometric path could not see), and the
# "armchair" is a static block the fake model also claims — u2net_human_seg
# really does this, watched on the round-64 validation frame.
WALKER = (48, 48, 58)          # BGR — nearly the background's own darkness
CHAIR = (60, 50, 120)
BG = (34, 34, 38)
BAR_W, BAR_Y = 80, (H // 4, 3 * H // 4)
# Below the walker's band with clear air between them: the union-blob gate
# merges overlapping regions BY DESIGN (a person passing in front of the
# furniture is one blob, and keeping it whole is correct), so the furniture
# case under test is the separated one.
CHAIR_BOX = (int(W * 0.70), int(H * 0.82), int(W * 0.95), int(H * 0.97))


def _frame(i, n, walker=True, chair=True, pan=0):
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    if pan:
        # a crude camera move: roll the background sideways
        img[:, :, 0] = np.roll(img[:, :, 0], i * pan, axis=1)
        img[10:20, (i * pan) % W:(i * pan) % W + 30] = (200, 200, 200)
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


def _fake_segment(frame_bgr, cv2):
    """The stand-in model: 'person probability' 1.0 wherever the frame shows
    the walker's or the chair's exact colour (the codec smears edges, so match
    with tolerance), 0 elsewhere — deterministic, colour-keyed, and blind to
    motion, exactly like the real model."""
    f = frame_bgr.astype(np.int16)
    m = np.zeros(f.shape[:2], np.float32)
    for c in (WALKER, CHAIR):
        m[np.all(np.abs(f - np.array(c, np.int16)) < 5, axis=2)] = 1.0
    return cv2.resize(m, (personseg.INPUT_SIZE, personseg.INPUT_SIZE),
                      interpolation=cv2.INTER_AREA)


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round64_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def walk(workdir):
    path = os.path.join(workdir, "walk.mp4")
    _encode(path, [_frame(i, N) for i in range(N)])
    return path


@pytest.fixture(scope="module")
def standing(workdir):
    """Only the static 'chair' — which here plays a person standing still."""
    path = os.path.join(workdir, "stand.mp4")
    _encode(path, [_frame(i, N, walker=False) for i in range(N)])
    return path


@pytest.fixture(scope="module")
def panning(workdir):
    path = os.path.join(workdir, "pan64.mp4")
    _encode(path, [_frame(i, N, chair=False, pan=3) for i in range(N)])
    return path


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    monkeypatch.setattr(personseg, "available", lambda: True)
    monkeypatch.setattr(personseg, "segment", _fake_segment)


def _decode_mask(path):
    frames = list(matte._decode(path, 0.0, DUR + 1.0, W, H))
    assert frames
    return [f[:, :, 0] for f in frames]


@needs_ffmpeg
def test_the_walker_is_masked_and_the_furniture_is_not(walk, workdir):
    """The motion gate: the model claims walker AND chair; the chair sat in
    the same pixels through every sampled frame, so it is furniture and comes
    out — decided once for the whole window, so nothing flickers."""
    out = os.path.join(workdir, "seg_walk.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H,
                                  box=(0.1, 0.35, 0.8, 0.3))
    assert res["ok"], res
    assert res["method"] == "person"
    masks = _decode_mask(out)
    x0, y0, x1, y1 = CHAIR_BOX
    # sample well inside the chair (away from its feathered edge)
    chair_on = max(float((m[y0 + 12:y1 - 12, x0 + 12:x1 - 12] > 127).mean())
                   for m in masks)
    assert chair_on < 0.05, f"static furniture survived the gate: {chair_on}"
    # the walker is present mid-window, roughly where he should be
    mid = masks[len(masks) // 2]
    band = mid[BAR_Y[0] + 10:BAR_Y[1] - 10, :]
    assert float((band > 127).mean()) > 0.05, "the walker is missing"
    # and the coverage numbers speak about the walker alone
    walker_frac = (BAR_W * (BAR_Y[1] - BAR_Y[0])) / float(W * H)
    assert res["coverage"] == pytest.approx(walker_frac, rel=0.5), res
    assert res["text_covered"] > 0.02


@needs_ffmpeg
def test_a_standing_subject_is_kept(standing, workdir):
    """The exception that makes the gate safe: when NOTHING moves, the static
    blob is not furniture — it is a person standing still, and dropping them
    would refuse the one thing in the shot."""
    out = os.path.join(workdir, "seg_stand.mp4")
    res = matte.measure_and_build(standing, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    masks = _decode_mask(out)
    x0, y0, x1, y1 = CHAIR_BOX
    on = float((masks[0][y0 + 12:y1 - 12, x0 + 12:x1 - 12] > 127).mean())
    assert on > 0.9, f"the standing subject was gated away: {on}"


@needs_ffmpeg
def test_a_moving_camera_is_fine_with_the_model(panning, workdir):
    """The photometric path REFUSES a pan (everything differs from the
    plate). The model segments each frame on its own, so the same footage now
    measures cleanly — the round-60 'still camera only' limit retires."""
    out = os.path.join(workdir, "seg_pan.mp4")
    res = matte.measure_and_build(panning, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    assert res["method"] == "person"


@needs_ffmpeg
def test_the_budget_still_yields_every_frame(walk, workdir, monkeypatch):
    """A pathological window gets a strided model run; the mask stream is
    still one mask per OUTPUT frame, interpolated between samples, and the
    interpolated masks follow the motion instead of freezing."""
    monkeypatch.setattr(matte, "SEG_BUDGET", 6)
    out = os.path.join(workdir, "seg_budget.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    assert res["frames"] >= N - 2, res
    masks = _decode_mask(out)
    band = slice(BAR_Y[0] + 10, BAR_Y[1] - 10)

    def centroid(m):
        xs = np.where(m[band, :] > 127)[1]
        return float(xs.mean()) if xs.size else None

    c_first = centroid(masks[2])
    c_last = centroid(masks[-3])
    assert c_first is not None and c_last is not None
    assert c_last - c_first > W * 0.4, (c_first, c_last)


@needs_ffmpeg
def test_flickering_weak_furniture_is_dropped(walk, workdir, monkeypatch):
    """Round 66, from the first real render off v6: 'like a corrupt screen
    that goes off and on in some places'. A chair the model detects in HALF
    the frames has presence ~0.5 — under the 0.85 static rule, so v6 kept it
    as 'motion' and it strobed over the title. Those detections hover near
    the decision threshold (that is WHY they flicker), so the flicker gate
    drops static-ish blobs with weak mean confidence — for the WHOLE window,
    no strobing possible."""
    def flickery(frame_bgr, cv2):
        m = _fake_segment(frame_bgr, cv2)
        f = cv2.resize(frame_bgr, (personseg.INPUT_SIZE, personseg.INPUT_SIZE),
                       interpolation=cv2.INTER_AREA).astype(np.int16)
        chair = np.all(np.abs(f - np.array(CHAIR, np.int16)) < 5, axis=2)
        m[chair] = 0.0
        # the chair comes back WEAK (0.6) in just over half the frames
        if not hasattr(flickery, "_i"):
            flickery._i = 0
        flickery._i += 1
        if flickery._i % 5 != 0:
            m[chair] = 0.6
        return m
    monkeypatch.setattr(personseg, "segment", flickery)
    out = os.path.join(workdir, "seg_flicker.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    masks = _decode_mask(out)
    x0, y0, x1, y1 = CHAIR_BOX
    chair_on = max(float((m[y0 + 12:y1 - 12, x0 + 12:x1 - 12] > 127).mean())
                   for m in masks)
    assert chair_on < 0.05, f"flickering furniture strobed: {chair_on}"


@needs_ffmpeg
def test_single_sample_flicker_dies_in_the_vote(walk, workdir, monkeypatch):
    """A CONFIDENT detection that exists for one sample is still flicker —
    the vote kills what the confidence gate cannot."""
    blob_frame = {"n": 0}

    def one_frame_blob(frame_bgr, cv2):
        m = _fake_segment(frame_bgr, cv2)
        blob_frame["n"] += 1
        if blob_frame["n"] == 45:          # one strong blob, one sample
            m[40:110, 40:110] = 1.0
        return m
    monkeypatch.setattr(personseg, "segment", one_frame_blob)
    out = os.path.join(workdir, "seg_oneflick.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert res["ok"], res
    # the blob region (model res 40:110 -> frame fractions) stays empty
    # around the flicker's own moment — the walker crosses the same region
    # legitimately EARLIER in the window, so only frames 40-50 are checked
    fy0, fy1 = int(40 / 320 * H), int(110 / 320 * H)
    fx0, fx1 = int(40 / 320 * W), int(110 / 320 * W)
    masks = _decode_mask(out)[40:50]
    assert masks
    worst = max(float((m[fy0 + 4:fy1 - 4, fx0 + 4:fx1 - 4] > 127).mean())
                for m in masks)
    assert worst < 0.05, f"one-sample flicker survived the vote: {worst}"


@needs_ffmpeg
def test_no_person_is_an_honest_refusal(workdir, monkeypatch):
    def nobody(frame_bgr, cv2):
        return np.zeros((personseg.INPUT_SIZE, personseg.INPUT_SIZE),
                        np.float32)
    monkeypatch.setattr(personseg, "segment", nobody)
    path = os.path.join(workdir, "empty64.mp4")
    _encode(path, [_frame(i, N, walker=False, chair=False) for i in range(N)])
    out = os.path.join(workdir, "seg_empty.mp4")
    res = matte.measure_and_build(path, out, 0.0, DUR, width=W, height=H)
    assert not res["ok"]
    assert "person" in res["why"]
    assert not os.path.exists(out)


@needs_ffmpeg
def test_a_frame_filling_subject_is_refused(walk, workdir, monkeypatch):
    def everyone(frame_bgr, cv2):
        return np.ones((personseg.INPUT_SIZE, personseg.INPUT_SIZE),
                       np.float32)
    monkeypatch.setattr(personseg, "segment", everyone)
    out = os.path.join(workdir, "seg_full.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H)
    assert not res["ok"]
    assert "fills" in res["why"]
    assert not os.path.exists(out)


@needs_ffmpeg
def test_allow_model_false_forces_the_photometric_path(walk, workdir):
    """The dispatcher's fallback lane: with the executor configured but
    unreachable, add_text_behind builds locally with allow_model=False — the
    model must NOT run beside agent turns even though the file ships in the
    image (one image, two roles)."""
    out = os.path.join(workdir, "seg_forced_plate.mp4")
    res = matte.measure_and_build(walk, out, 0.0, DUR, width=W, height=H,
                                  allow_model=False)
    assert res.get("method") == "plate", res


@needs_ffmpeg
def test_run_matte_job_uploads_on_success(walk, workdir, monkeypatch):
    import storage

    copied = {}

    def fake_download(key, dest):
        shutil.copyfile(walk, dest)

    def fake_upload(path, key, ctype):
        copied["key"] = key
        copied["bytes"] = os.path.getsize(path)

    monkeypatch.setattr(storage, "download_to", fake_download)
    monkeypatch.setattr(storage, "upload_file", fake_upload)
    res = matte.run_matte_job(None, {
        "payload": {"storage_key": "proxies/1/x.mp4",
                    "out_key": "matte/1/abc.mp4",
                    "start": 0.0, "dur": DUR,
                    "box": [0.1, 0.35, 0.8, 0.3],
                    "width": W, "height": H}})
    assert res["ok"], res
    assert copied["key"] == "matte/1/abc.mp4"
    assert copied["bytes"] > 1000


@needs_ffmpeg
def test_run_matte_job_refusal_uploads_nothing(walk, workdir, monkeypatch):
    import storage

    def nobody(frame_bgr, cv2):
        return np.zeros((personseg.INPUT_SIZE, personseg.INPUT_SIZE),
                        np.float32)
    monkeypatch.setattr(personseg, "segment", nobody)
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dest: shutil.copyfile(walk, dest))
    calls = []
    monkeypatch.setattr(storage, "upload_file",
                        lambda *a, **k: calls.append(a))
    res = matte.run_matte_job(None, {
        "payload": {"storage_key": "proxies/1/x.mp4",
                    "out_key": "matte/1/none.mp4",
                    "start": 0.0, "dur": DUR, "width": W, "height": H}})
    assert not res["ok"]
    assert not calls, "a refused matte must upload nothing"
