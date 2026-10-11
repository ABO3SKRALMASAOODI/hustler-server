"""refsig detectors on a synthetic reel whose events are known frame by frame.

The reel (lossless FFV1, 30 fps, 160x90) is built from textured "takes"
(seeded random blobs, moving slowly), so cuts change structure and colour
while a take only drifts. Events planted:
  - hard cuts at frames 30, 60, 90, 120;
  - single-frame negatives at 40 and 100, a 2-frame negative run at 70-71;
  - single-frame white flashes at 50 (inside take 2) and at 90 (ON a cut);
  - a B&W stretch over frames 120-149 (take 5 desaturated);
  - a strobe of alternating takes every 2 frames over 150-179.
CI-safe: no fixture, no reference; OCR and RVM are not needed.
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import refsig                                                 # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and \
    shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")

W, H, FPS = 160, 90, 30


def _take(seed, t, colour):
    """A textured take: random blobs on a coloured plate, drifting 1 px per
    frame. Different seeds -> different structure."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.float32)
    img[:] = colour
    yy, xx = np.mgrid[:H, :W]
    for _ in range(14):
        cx, cy = rng.uniform(0, W), rng.uniform(0, H)
        r = rng.uniform(5, 18)
        c = rng.uniform(0, 255, 3)
        m = ((xx - cx - t) ** 2 + (yy - cy) ** 2) <= r * r
        img[m] = c
    return img


def _build(path):
    takes = [(1, (40, 60, 90)), (2, (120, 40, 30)), (3, (30, 110, 50)),
             (4, (90, 90, 160)), (5, (150, 120, 60)), (6, (60, 30, 100))]
    frames = []
    for f in range(180):
        if f < 150:
            k = f // 30
        else:
            k = 5 if (f // 2) % 2 == 0 else 2      # strobe between 2 takes
        img = _take(*takes[k], t=f % 30, ) if False else \
            _take(takes[k][0], f % 30, takes[k][1])
        if 120 <= f < 150:
            g = img @ np.array([0.299, 0.587, 0.114], np.float32)
            img = np.repeat(g[..., None], 3, axis=2)
        if f in (40, 100, 70, 71):
            img = 255.0 - img
        if f in (50, 90):
            img[:] = 250.0
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "ffv1", "-pix_fmt", "bgr0", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in frames:
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    assert p.wait() == 0


@pytest.fixture(scope="module")
def reel(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("refsig") / "reel.mkv")
    _build(p)
    return p


@pytest.fixture(scope="module")
def feats(reel):
    return refsig.Feats(reel, feat_w=W)


def test_feats_shape(feats):
    assert feats.n == 180
    assert round(feats.fps) == 30


def test_negatives(feats):
    assert refsig.negatives(feats) == [40, 70, 71, 100]


def test_white_flashes(feats):
    neg = refsig.negatives(feats)
    assert refsig.white_flashes(feats, neg=neg) == [50, 90]


def test_cuts_bridge_punctuation(feats):
    neg = refsig.negatives(feats)
    wf = refsig.white_flashes(feats, neg=neg)
    cuts = refsig.cuts(feats, punct=set(neg) | set(wf), soft=False)
    # the in-take flash (50) and negatives are bridged; the flash ON the
    # cut at 90 collapses into that cut; the strobe makes every switch a cut
    head = [c for c in cuts if c < 150]
    assert head == [30, 60, 90, 120]
    # an A/B strobe reads as one take with inserts to the cut detector (the
    # first switch is the cut); strobe_sections reports it instead
    assert [c for c in cuts if c >= 150][:1] == [150]


def test_bw_section(feats):
    frames, undecided, sections = refsig.bw(feats)
    assert sections == [[120, 149]]
    assert set(range(120, 150)) <= set(frames)


def test_strobe_section(feats):
    neg = refsig.negatives(feats)
    wf = refsig.white_flashes(feats, neg=neg)
    cuts = refsig.cuts(feats, punct=set(neg) | set(wf), soft=False)
    ch = refsig.changes(feats)
    assert len([c for c in ch if c >= 150]) >= 14
    ev = refsig.punctuation_events(feats, cuts, set(neg) | set(wf))
    assert [e for e in ev if e < 150] == [30, 40, 50, 60, 70, 90, 100, 120]
    secs = refsig.strobe_sections(feats, ev)
    assert any(s["f"][0] <= 152 and s["f"][1] >= 175 for s in secs)
    assert not any(s["f"][1] < 120 for s in secs)


def test_analyze_is_json_safe_and_consistent(reel):
    import json
    sig = refsig.analyze(reel)
    json.dumps(sig)
    assert sig["version"] == refsig.SIG_VERSION
    assert sig["negatives"]["f"] == [40, 70, 71, 100]
    assert sig["white_flashes"]["f"] == [50, 90]
    assert sig["bw"]["sections"] == [[120, 149]]
    assert sig["layout"]["layout"] == "full"
    assert sig["shots"] == len(sig["cuts"]["f"]) + 1


def test_layout_letterbox_and_card(tmp_path):
    def clip(path, box):
        x0, y0, x1, y1 = box
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
               "rgb24", "-s", f"{W}x{H}", "-r", "30", "-i", "-",
               "-c:v", "ffv1", "-pix_fmt", "bgr0", path]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in range(48):
            img = np.zeros((H, W, 3), np.uint8)
            img[y0:y1, x0:x1] = _take(f // 6 + 1, f % 6,
                                      (80, 80, 80)).astype(np.uint8)[
                y0:y1, x0:x1]
            p.stdin.write(img.tobytes())
        p.stdin.close()
        assert p.wait() == 0
    lb = str(tmp_path / "lb.mkv")
    clip(lb, (0, 15, W, 75))
    out = refsig.layout(lb)
    assert out["layout"] == "letterbox"
    x, y, w, h = out["rects"][0]
    assert abs(y - 15) <= 3 and abs(h - 60) <= 4
    card = str(tmp_path / "card.mkv")
    clip(card, (40, 20, 120, 70))
    assert refsig.layout(card)["layout"] in ("card", "card_rounded")


def test_ocr_is_honest_off_without_package(monkeypatch, reel):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("rapidocr_onnxruntime"):
            raise ImportError("not here")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    ok, why = refsig.ocr_status()
    assert ok is False and "not installed" in why
    sig = refsig.analyze(reel, ocr=True)
    assert sig["text_boxes"] == {"available": False, "why": why}


def test_ocr_models_are_pinned():
    assert set(refsig.OCR_MODELS) == {
        "models/ch_PP-OCRv4_det_infer.onnx",
        "models/ch_PP-OCRv4_rec_infer.onnx",
        "models/ch_ppocr_mobile_v2.0_cls_infer.onnx"}
    for sha in refsig.OCR_MODELS.values():
        assert len(sha) == 64 and int(sha, 16) >= 0


# ── take-level checks (cuts(takes=True)) ──────────────────────────────────

def _feats_from(frames, tmp_path, name):
    p = str(tmp_path / name)
    h, w = frames[0].shape[:2]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
           "-c:v", "ffv1", "-pix_fmt", "bgr0", p]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in frames:
        pr.stdin.write(fr.tobytes())
    pr.stdin.close()
    assert pr.wait() == 0
    return refsig.Feats(p, feat_w=w)


def test_a_fast_pan_is_one_take(tmp_path):
    """A whip-fast pan across a wide textured plate changes every frame a
    lot, but steadily: the take checks read it as one take."""
    rng = np.random.default_rng(7)
    plate = np.zeros((H, W * 8, 3), np.uint8)
    for _ in range(160):
        cx, cy = rng.integers(0, W * 8), rng.integers(0, H)
        r = rng.integers(4, 14)
        yy, xx = np.mgrid[:H, :W * 8]
        plate[((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r] = \
            rng.integers(0, 255, 3)
    frames = [plate[:, 30 * f:30 * f + W].copy() for f in range(36)]
    F = _feats_from(frames, tmp_path, "pan.mkv")
    assert refsig.cuts(F, soft=False) == []


def test_a_small_inset_blink_is_not_a_cut(tmp_path):
    """An inset box that blinks into one corner for two frames changes a
    small share of the picture and the take resumes: an effect inside the
    take, not a cut (ref 15's inset boxes are don't-care, not cuts)."""
    base = [_take(11, f, (60, 70, 80)).astype(np.uint8) for f in range(40)]
    for f in (20, 21):
        base[f][4:34, 110:156] = (250, 220, 30)
    F = _feats_from(base, tmp_path, "inset.mkv")
    assert refsig.cuts(F, soft=False, takes=False) == [20]   # raw: a cut
    assert refsig.cuts(F, soft=False) == []                  # a blink
    S, _coh = refsig.take_step(F, 20)
    assert S < 0.5 * refsig._dc(F, 19, 20)


def test_take_step_separates_cuts_from_moves(feats):
    S, coh = refsig.take_step(feats, 60)              # a hard cut
    assert S > 3 * (coh + 0.5)
    S2, coh2 = refsig.take_step(feats, 45)            # inside a take
    assert S2 < 2.0
