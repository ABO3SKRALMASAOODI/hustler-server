"""Pose: MediaPipe pose landmarks (33 points), tracked (3A).

The official pose_landmarker_full.task's landmark graph runs through
cv2.dnn; its person DETECTOR does not (it stores sparse weights, the
DENSIFY op cv2.dnn cannot import). MediaPipe itself runs the detector only
to start a track, so we start it from the face instead: BlazePose's ROI is
centred on the hip midpoint (aux landmark 33) and sized by the distance to
its scale point (aux 34), and both sit at stable multiples of the face
height below a seated or standing speaker. Two refinement passes on the
first frame converge the ROI from those landmarks; later frames track from
the previous pose, exactly as MediaPipe does.
"""

import numpy as np

from . import models, roi as roimod

LM_SIZE = 256
NAMES = ("nose", "eye_inner_l", "eye_l", "eye_outer_l", "eye_inner_r",
         "eye_r", "eye_outer_r", "ear_l", "ear_r", "mouth_l", "mouth_r",
         "shoulder_l", "shoulder_r", "elbow_l", "elbow_r", "wrist_l",
         "wrist_r", "pinky_l", "pinky_r", "index_l", "index_r", "thumb_l",
         "thumb_r", "hip_l", "hip_r", "knee_l", "knee_r", "ankle_l",
         "ankle_r", "heel_l", "heel_r", "foot_l", "foot_r")
# MediaPipe numbers the person's LEFT side first (11 = left shoulder).
CENTRE, SCALE = 33, 34


class PoseTracker:
    def __init__(self, task_path, min_presence=0.5, threads=0):
        self.lm = models.tflite_net(task_path,
                                    "pose_landmarks_detector.tflite",
                                    threads)
        self.min_presence = float(min_presence)
        self.roi = None

    @staticmethod
    def roi_from_aux(centre, scale_pt):
        rot = roimod.rotation(centre, scale_pt, 90.0)
        d = float(np.hypot(*(np.asarray(scale_pt) - np.asarray(centre))))
        return (float(centre[0]), float(centre[1]), 2.0 * d * 1.25, rot)

    @staticmethod
    def roi_from_face(box):
        """A first ROI from a face box [x0, y0, x1, y1] px."""
        fh = box[3] - box[1]
        cx = (box[0] + box[2]) / 2.0
        hip = (cx, box[3] + 3.1 * fh)
        top = (cx, box[1] - 0.6 * fh)
        return PoseTracker.roi_from_aux(hip, top)

    def landmarks(self, frame, roi):
        """(presence, 39x4 [x px, y px, visibility, presence])."""
        import cv2
        crop, M = roimod.crop(frame, roi, LM_SIZE)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 255.0, (LM_SIZE, LM_SIZE),
                                     swapRB=True)
        out = models.outputs(self.lm, blob)
        lms = flag = None
        for _n, arr in out.items():
            if arr.size == 195:
                lms = arr.reshape(39, 5)
            elif arr.size == 1:
                flag = float(arr.reshape(-1)[0])
        if lms is None:
            return 0.0, None
        presence = flag if flag is not None and 0.0 <= flag <= 1.0 else \
            float(1.0 / (1.0 + np.exp(-(flag or 0.0))))
        xy = roimod.to_image(lms[:, :2], M)
        vis = 1.0 / (1.0 + np.exp(-np.clip(lms[:, 3], -60, 60)))
        pres = 1.0 / (1.0 + np.exp(-np.clip(lms[:, 4], -60, 60)))
        return presence, np.c_[xy, vis, pres]

    def step(self, frame, face_box=None):
        """The pose on this frame ({score, pts 33x4}) or None."""
        roi = self.roi
        if roi is None and face_box is not None:
            roi = self.roi_from_face(face_box)
            for _ in range(2):           # converge the cold-start ROI
                pres, lm = self.landmarks(frame, roi)
                if lm is None or pres < self.min_presence:
                    break
                roi = self.roi_from_aux(lm[CENTRE, :2], lm[SCALE, :2])
        if roi is None:
            return None
        pres, lm = self.landmarks(frame, roi)
        if lm is None or pres < self.min_presence:
            self.roi = None
            return None
        self.roi = self.roi_from_aux(lm[CENTRE, :2], lm[SCALE, :2])
        return {"score": pres, "pts": lm[:33]}
