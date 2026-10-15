"""Wave 3 model children on real footage (local only: the Valmera Footage
Library fixtures and the pinned model files; CI has neither and skips).

Each check runs the tracker class a sidecar child runs, on frames of a
VFL fixture, through the pinned file (sidecar_kinds.model_file verifies its
models.lock sha256): the face mesh, the hand landmarks, the pose and the
any-object segmenter. Production lanes never run these (sidecars only).
"""

import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

from localfix import fixture, local_only                  # noqa: E402

MODELS = os.environ.get("VALMERA_MODELS_DIR") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/models")
V09 = fixture("V09", "jensen_windows.mp4")
V12 = fixture("V12", "gestures.mp4")
V02 = fixture("V02", "thiel_flying_cars.mp4")
FACE = os.path.join(MODELS, "face_landmarker.task")
HAND = os.path.join(MODELS, "hand_landmarker.task")
POSE = os.path.join(MODELS, "pose_landmarker_full.task")
YUNET = os.path.join(MODELS, "face_detection_yunet_2023mar.onnx")
SAM = os.path.join(MODELS, "image_segmentation_efficientsam_ti_2025april.onnx")


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("VALMERA_MODELS_DIR", MODELS)


def _pinned(name):
    import sidecar_kinds
    path, why = sidecar_kinds.model_file(name)
    assert path, why
    return path


def _frames(path, t, n=1, fps=15.0, w=1280):
    h = int(round(w * 9 / 16 / 2)) * 2
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-an",
         "-vf", f"fps={fps},scale={w}:{h}", "-frames:v", str(n), "-f",
         "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


@local_only(V09, FACE, YUNET)
def test_the_face_mesh_sits_inside_the_detected_face():
    from anchors import faces
    ft = faces.FaceTracker(YUNET, _pinned("mediapipe_face_landmarker"))
    got = 0
    for fr in _frames(V09, 3.0, n=8):
        found = ft.step(fr)
        if not found:
            continue
        f = found[0]
        mesh = np.asarray(f["mesh"])
        assert mesh.shape[0] == 478
        x0, y0, x1, y1 = f["box"]
        pad = 0.25 * (x1 - x0)
        inside = ((mesh[:, 0] > x0 - pad) & (mesh[:, 0] < x1 + pad)
                  & (mesh[:, 1] > y0 - pad) & (mesh[:, 1] < y1 + pad))
        assert inside.mean() > 0.95
        nm = faces.named_points(f["mesh"])
        # the irises lie between the brows and the mouth, left of right
        assert nm["iris_l"][1] < nm["mouth"][1]
        got += 1
    assert got >= 6


@local_only(V12, HAND)
def test_hands_are_found_with_21_landmarks_when_raised():
    from anchors import hands
    ht = hands.HandTracker(_pinned("mediapipe_hand_landmarker"))
    hits = 0
    for fr in _frames(V12, 10.0, n=10):
        found = ht.step(fr)
        if found:
            p = np.asarray(found[0]["pts"])
            assert p.shape == (21, 2)
            assert found[0]["score"] > 0.5
            hits += 1
    assert hits >= 7


@local_only(V12, POSE, YUNET, FACE)
def test_the_pose_follows_the_speaker():
    from anchors import faces, pose
    det = faces.FaceTracker(YUNET, _pinned("mediapipe_face_landmarker"))
    pt = pose.PoseTracker(_pinned("mediapipe_pose_landmarker"))
    got = 0
    for fr in _frames(V12, 10.0, n=6):
        box = None
        if pt.roi is None:
            d = det.detect(fr)
            box = d[0][1] if d else None
        out = pt.step(fr, box)
        if out is not None:
            assert len(out["pts"]) == 33
            nose, lsh = out["pts"][0], out["pts"][11]
            assert nose[1] < lsh[1]          # the head above the shoulders
            got += 1
    assert got >= 4


@local_only(V02, SAM)
def test_the_laptop_is_segmented_inside_its_box():
    from objseg import sam
    seg = sam.Segmenter(_pinned("efficientsam_ti"))
    fr = _frames(V02, 43.1, n=1, w=1920)[0]
    H, W = fr.shape[:2]
    box = [0.398 * W, 0.774 * H, 0.664 * W, 1.0 * H]
    mask, score = seg.segment(fr, box=box)
    m = mask > 127
    assert m.sum() > 0.25 * (box[2] - box[0]) * (box[3] - box[1])
    ys, xs = np.where(m)
    pad = 0.06 * W
    assert xs.min() > box[0] - pad and xs.max() < box[2] + pad
    assert ys.min() > box[1] - pad
    assert score > 0.5
