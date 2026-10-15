"""Wave 3 sidecar kinds (devices/INTERFACES_W3.md §1): anchors (3A),
objects (3B), planes and clean plates (3C).

Every kind runs on the batch lane through the one ``sidecar`` job kind;
model kinds read pinned files (models/*.json, models.lock) through cv2.dnn
or onnxruntime, model-free kinds use OpenCV core. Coordinates are SOURCE
frame fractions on the SOURCE clock.
"""

from . import engine_for, identity

# The code versions are part of each key: a better algorithm never reads an
# old chunk as its own.
_FACE = identity(("yunet", "mediapipe_face_landmarker"), "w3a1")
_HAND = identity(("mediapipe_hand_landmarker",), "w3a1")
_POSE = identity(("yunet", "mediapipe_face_landmarker",
                  "mediapipe_pose_landmarker"), "w3a1")
_OBJ = identity(("efficientsam_ti",), "w3b1")
_CAM = identity(("yunet",), "w3a1")


def _json(fmt, ext):
    # the data's extension must not be ".json": the manifest is
    # <key>.json in the same folder (sidecars.manifest_key)
    return {"format": fmt, "formats": (fmt,), "ext": ext,
            "content_type": "application/json", "fallback_models": ()}


KIND_SPECS = {
    "landmarks.face": dict(
        _json("landmarks.face.v1", "face.json"), model=None,
        model_id=_FACE[0], model_version=_FACE[1],
        models=("yunet", "mediapipe_face_landmarker"),
        defaults={"fps": 15.0, "det_w": 640, "max_w": 1280, "max_faces": 4},
        rate="fps", ms_per_frame=18.0, group_s=60.0,
        child="anchors.jobs:child_face"),
    "landmarks.hand": dict(
        _json("landmarks.hand.v1", "hand.json"), model=None,
        model_id=_HAND[0], model_version=_HAND[1],
        models=("mediapipe_hand_landmarker",),
        defaults={"fps": 15.0, "max_w": 1280, "max_hands": 2},
        rate="fps", ms_per_frame=30.0, group_s=60.0,
        child="anchors.jobs:child_hand"),
    "landmarks.pose": dict(
        _json("landmarks.pose.v1", "pose.json"), model=None,
        model_id=_POSE[0], model_version=_POSE[1],
        models=("yunet", "mediapipe_face_landmarker",
                "mediapipe_pose_landmarker"),
        defaults={"fps": 15.0, "max_w": 1280},
        rate="fps", ms_per_frame=20.0, group_s=60.0,
        child="anchors.jobs:child_pose"),
    "track.points": dict(
        _json("track.points.v1", "track.json"), model=None,
        model_id="opencv_lk", model_version="w3a1", models=(),
        defaults={"fps": 30.0, "max_w": 960},
        rate="fps", ms_per_frame=3.0, group_s=15.0,
        child="anchors.jobs:child_points"),
    "camera.motion": dict(
        _json("camera.motion.v1", "camera.json"), model=None,
        model_id="opencv_lk_ransac+" + _CAM[0], model_version=_CAM[1],
        models=("yunet",),
        defaults={"fps": 15.0, "max_w": 640},
        rate="fps", ms_per_frame=8.0, group_s=60.0,
        child="anchors.jobs:child_camera"),
    "objects.sam2": dict(
        format="mask.h264.v1", formats=("mask.h264.v1",), ext="mp4",
        content_type="video/mp4", fallback_models=(), model=None,
        model_id=_OBJ[0], model_version=_OBJ[1],
        models=("efficientsam_ti",),
        defaults={"fps": 30.0, "max_w": 960},
        rate="fps", ms_per_frame=220.0, group_s=15.0,
        child="objseg.jobs:child_objects"),
    "planes.h": dict(
        _json("planes.h.v1", "plane.json"), model=None,
        model_id="opencv_planar", model_version="w3c1", models=(),
        defaults={"fps": 15.0, "max_w": 960},
        rate="fps", ms_per_frame=12.0, group_s=15.0,
        child="planes:child_planes"),
    "plate.clean": dict(
        format="plate.png.v1", formats=("plate.png.v1",), ext="png",
        content_type="image/png", fallback_models=(), model=None,
        model_id="temporal_median", model_version="w3c1", models=(),
        defaults={"samples": 25, "max_w": 960},
        rate="samples", ms_per_frame=3.0, group_s=60.0,
        child="cleanplate:child_clean"),
}


def engine(kind, faultinject):
    """analysis_jobs._engine for these kinds (every pinned file verified),
    or None (model_missing)."""
    return engine_for(kind, faultinject, KIND_SPECS[kind])
