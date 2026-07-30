"""Person segmentation for the text-behind matte (round 64).

WHY A MODEL, FINALLY. matte.py's photometric pipeline (plate median, per-pixel
noise thresholds, frame alignment, per-blob shadow tests — versions 2 through
5) failed FOUR consecutive rounds on the same real customer footage: a dark
walker in a dark lamp-lit room. The physics is unfixable from the pixels'
differences alone: a dark body over a dark wall differs from the plate by less
than the sensor noise the thresholds must sit above, so the mask found 2.7% of
a subject who fills ~15% of the frame, and the title printed straight across
his chest. Meanwhile u2net_human_seg — run on the exact frame of the exact
failing render — returns the full, clean silhouette in one pass.

Round 60 declined a model for three reasons; all three are answered now:
  * "a model file in the image" — the image already bakes a ~0.5 GB whisper
    model and a whole Chromium; 168 MB is in keeping.
  * "a CPU forward pass per frame on the box that also runs the agent turn" —
    it does NOT run there: the matte build moves to the executor (the round-61
    OOM-class rule), where 8 vCPUs make a pass ~100-300 ms. The dispatcher
    keeps the photometric path as its only local fallback.
  * "output nobody can check" — the same coverage/box measurements are taken
    from the produced mask either way, and the refusal bounds still apply.

The model outputs a calibrated per-pixel person probability (sigmoid; verified
on real frames: an empty room peaks at 0.001, the walker at 1.000), so the raw
output is used directly — NO per-frame min-max normalization, which would
stretch an empty frame's noise floor to full opacity the moment the subject
steps out of frame.
"""

import os
import threading

import numpy as np

import config

# ImageNet normalization, as the model was trained (rembg's preprocessing).
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
# The ONNX graph's input is a fixed 1x3x320x320.
INPUT_SIZE = 320

_lock = threading.Lock()
_session = None
_dead = None      # first load error, latched — a broken model file should
#                   fail to the photometric path once, not once per frame


def model_path():
    return config.MATTE_SEG_MODEL


def available():
    """Model file present and onnxruntime importable. Honest-off contract:
    when this is False the matte quietly builds the photometric way, exactly
    as it always did — nothing breaks, one capability degrades."""
    if _dead is not None:
        return False
    p = model_path()
    if not p or not os.path.exists(p):
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


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


def segment(frame_bgr, cv2):
    """Soft person probability at model resolution: float32 (320, 320) in
    0..1 for ONE BGR frame. The caller owns upscaling — masks are gated and
    interpolated at model resolution first, where the memory for a whole
    window of them is a few MB instead of a few hundred."""
    sess = _get_session()
    x = cv2.resize(frame_bgr, (INPUT_SIZE, INPUT_SIZE),
                   interpolation=cv2.INTER_AREA)
    x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = (x - _MEAN) / _STD
    x = np.ascontiguousarray(x.transpose(2, 0, 1))[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    return np.clip(out.astype(np.float32), 0.0, 1.0)
