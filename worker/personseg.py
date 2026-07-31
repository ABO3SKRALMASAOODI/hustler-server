"""Person matting/segmentation for the text-behind matte.

TWO models live here, and the difference between them is the round-69 lesson:

RVM — RobustVideoMatting (rvm_mobilenetv3), the PRIMARY (round 69). u2net is a
per-frame SALIENT-OBJECT model: it has no memory, so every frame is a fresh
opinion, and matte v6-v9 were four rounds of gates (presence, confidence,
furniture zones, toggle budgets) trying to make a stack of independent
opinions stop flickering. On the round-69 footage (project 300, the dark
walker) the shipped v9 mask still jumped by 19% OF THE FRAME between two
consecutive frames, strobed the armchair on and off with 20 full flips, and
printed letters on the walker himself while a chair patch beside him was
masked. That is not a tuning problem: per-frame thresholded decisions on a
noisy per-frame signal CANNOT be temporally stable, however clever the gates.
RVM is architecturally the fix — a video matting network with four recurrent
state tensors carried frame to frame, so temporal coherence is what the MODEL
computes, not what post-processing hopes to recover. Measured on the exact
failing window, against v9: max frame-to-frame jump 19.4% -> 1.6% of the
frame, pixels toggling >=12 times 8174 -> 377, text columns flipping >=6
times 121 -> 14 — and the armchair is simply never claimed (it is not a
person), so nothing it does can flicker. Its output is a SOFT alpha (a real
matte, hair-level edges), consumed by the renderer's alphamerge exactly as
the binary masks were, so no render change rides this.

u2net_human_seg — the round-64 model, now the FALLBACK when the RVM file is
absent, with the whole v9 gate machinery still around it. Kept because it has
shipped and the honest-off ladder needs a middle rung; expected to retire.

Both are ONNX on CPUExecutionProvider, baked into the image by the Dockerfile
(never a runtime download), and both latch their first load error so a broken
file fails to the next rung once, not once per frame. In production the
forward passes run on the EXECUTOR only (the round-61 OOM-class rule).

The RVM weights are GPL-3.0 (PeterL1n/RobustVideoMatting). They are served
from our own machines and never distributed to users, which is use, not
conveyance — the same footing as every GPL tool already in the image.
"""

import os
import threading

import numpy as np

import config

# ImageNet normalization, as u2net was trained (rembg's preprocessing).
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
# The u2net ONNX graph's input is a fixed 1x3x320x320.
INPUT_SIZE = 320

_lock = threading.Lock()
_session = None
_dead = None      # first load error, latched — a broken model file should
#                   fail to the photometric path once, not once per frame

_rvm_lock = threading.Lock()
_rvm_session = None
_rvm_dead = None


def model_path():
    return config.MATTE_SEG_MODEL


def rvm_model_path():
    return config.MATTE_RVM_MODEL


def _ort():
    try:
        import onnxruntime
        return onnxruntime
    except Exception:
        return None


def available():
    """u2net present and onnxruntime importable. Honest-off contract:
    when this is False the matte quietly builds the photometric way, exactly
    as it always did — nothing breaks, one capability degrades."""
    if _dead is not None:
        return False
    p = model_path()
    if not p or not os.path.exists(p):
        return False
    return _ort() is not None


def rvm_available():
    """RVM present and onnxruntime importable. False -> the u2net rung."""
    if _rvm_dead is not None:
        return False
    p = rvm_model_path()
    if not p or not os.path.exists(p):
        return False
    return _ort() is not None


def _get_session():
    global _session, _dead
    with _lock:
        if _session is not None:
            return _session
        if _dead is not None:
            raise RuntimeError(_dead)
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            _session = ort.InferenceSession(
                model_path(), so, providers=["CPUExecutionProvider"])
        except Exception as e:
            _dead = f"person model failed to load: {e}"
            raise RuntimeError(_dead) from e
        return _session


def _get_rvm_session():
    global _rvm_session, _rvm_dead
    with _rvm_lock:
        if _rvm_session is not None:
            return _rvm_session
        if _rvm_dead is not None:
            raise RuntimeError(_rvm_dead)
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            _rvm_session = ort.InferenceSession(
                rvm_model_path(), so, providers=["CPUExecutionProvider"])
        except Exception as e:
            _rvm_dead = f"rvm model failed to load: {e}"
            raise RuntimeError(_rvm_dead) from e
        return _rvm_session


def segment(frame_bgr, cv2):
    """u2net: soft person probability at model resolution — float32
    (320, 320) in 0..1 for ONE BGR frame. The caller owns upscaling."""
    sess = _get_session()
    x = cv2.resize(frame_bgr, (INPUT_SIZE, INPUT_SIZE),
                   interpolation=cv2.INTER_AREA)
    x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = (x - _MEAN) / _STD
    x = np.ascontiguousarray(x.transpose(2, 0, 1))[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    return np.clip(out.astype(np.float32), 0.0, 1.0)


def rvm_stream(width, height):
    """A stateful matting stream for ONE window of ONE clip.

    Frames must be fed IN ORDER — the recurrent state is the whole point.
    step() takes a BGR uint8 frame at (height, width) and returns the soft
    alpha as float32 (height, width) in 0..1, full frame resolution.

    downsample_ratio sizes the network's coarse pass; the refiner runs at
    frame resolution either way. The authors' operating point is a coarse
    side around 512, hence 512/max(side) — for the 960x540 proxy that is
    0.533, the exact setting validated on the round-69 footage. The state
    needs no warm-up: measured on that window, frame 0's alpha differed from
    frame 30's by 0.3% of the frame, a smooth ramp, no cold-start artifact.
    """
    sess = _get_rvm_session()
    ds = np.array([min(1.0, 512.0 / float(max(width, height, 1)))],
                  np.float32)
    state = {"rec": [np.zeros([1, 1, 1, 1], np.float32)] * 4}

    class _Stream:
        def step(self, frame_bgr, cv2):
            src = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB) \
                .astype(np.float32) / 255.0
            src = np.ascontiguousarray(src.transpose(2, 0, 1))[None]
            rec = state["rec"]
            _fgr, pha, r1, r2, r3, r4 = sess.run(None, {
                "src": src, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2],
                "r4i": rec[3], "downsample_ratio": ds})
            state["rec"] = [r1, r2, r3, r4]
            return np.clip(pha[0, 0].astype(np.float32), 0.0, 1.0)

    return _Stream()
