"""Faces: YuNet detection + MediaPipe Face Landmarker (478 points) (3A).

YuNet (already baked on every lane for ``faces.yunet``) finds faces and
their eyes; the official face_landmarks_detector.tflite (from
face_landmarker.task, through cv2.dnn) measures the 478-point mesh with
irises inside a ROI rotated by the eye line (MediaPipe's convention: scale
1.5, square). A face tracked on the previous frame takes its ROI from the
mesh (outer eye corners set the roll), which is steadier than a fresh box;
YuNet still runs every frame to catch new faces and to stop a drifting
track.

Out: per face, a compact set of NAMED points (anatomical _r/_l: the
person's own right/left; query.py maps screen left/right), an 18-point face
oval, the head pose (yaw, pitch, roll in degrees), the head ellipse and the
box, all in PIXELS of the frame given.
"""

import math

import numpy as np

from . import models, roi as roimod

LM_SIZE = 256
# MediaPipe face mesh indices of the named points (anatomical sides).
NAMED = {
    "iris_r": (468,), "iris_l": (473,),
    "eye_r": (33, 133), "eye_l": (362, 263),
    "eye_r_out": (33,), "eye_l_out": (263,),
    "brow_r": (105,), "brow_l": (334,),
    "nose": (1,), "nose_bridge": (6,),
    "mouth": (13, 14), "mouth_r": (61,), "mouth_l": (291,),
    "chin": (152,), "forehead": (10,),
    "ear_r": (234,), "ear_l": (454,),
    "cheek_r": (50,), "cheek_l": (280,),
}
NAMED_ORDER = tuple(NAMED)
# The face oval (MediaPipe FACE_OVAL), every second point.
FACE_OVAL = (10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
             397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
             172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109)
OVAL = FACE_OVAL[::2]


class FaceTracker:
    def __init__(self, yunet_path, task_path, det_w=640, max_faces=4,
                 score=0.6, min_presence=0.5, threads=0):
        self.yunet_path = yunet_path
        self.det_w = int(det_w)
        self.max_faces = int(max_faces)
        self.score = float(score)
        self.min_presence = float(min_presence)
        self.lm = models.tflite_net(task_path,
                                    "face_landmarks_detector.tflite",
                                    threads)
        self._det = None
        self._det_size = None
        self.tracks = []           # [roi]

    def _detector(self, w, h):
        dw = min(self.det_w, w)
        dh = max(2, int(round(h * dw / float(w))))
        if self._det is None or self._det_size != (dw, dh):
            self._det = models.yunet(self.yunet_path, (dw, dh), self.score)
            self._det_size = (dw, dh)
        return self._det, dw / float(w)

    def detect(self, frame):
        """[(score, box px, 5x2 kps px)] (YuNet: right eye, left eye, nose,
        right mouth, left mouth — the person's own sides)."""
        import cv2
        h, w = frame.shape[:2]
        det, k = self._detector(w, h)
        small = cv2.resize(frame, self._det_size,
                           interpolation=cv2.INTER_AREA) if k != 1.0 \
            else frame
        _ok, faces = det.detect(small)
        out = []
        for f in faces if faces is not None else ():
            x, y, bw, bh = (float(v) / k for v in f[:4])
            kps = np.array(f[4:14], np.float64).reshape(5, 2) / k
            out.append((float(f[14]), [x, y, x + bw, y + bh], kps))
        out.sort(key=lambda d: -(d[1][2] - d[1][0]) * (d[1][3] - d[1][1]))
        return out[:self.max_faces]

    @staticmethod
    def det_roi(box, kps):
        rot = roimod.rotation(kps[0], kps[1], 0.0)
        return roimod.from_box(box, rot, scale=1.5)

    @staticmethod
    def mesh_roi(mesh):
        rot = roimod.rotation(mesh[33], mesh[263], 0.0)
        return roimod.from_points(mesh, rot, scale=1.5)

    def landmarks(self, frame, roi):
        """(presence, 478x2 px) inside ``roi``."""
        import cv2
        crop, M = roimod.crop(frame, roi, LM_SIZE)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 255.0, (LM_SIZE, LM_SIZE),
                                     swapRB=True)
        out = models.outputs(self.lm, blob)
        mesh = flag = None
        for _name, arr in sorted(out.items()):
            if arr.size == 1434:
                mesh = arr.reshape(478, 3)
            elif arr.size == 1 and flag is None:
                flag = float(arr.reshape(-1)[0])
        if mesh is None:
            return 0.0, None
        presence = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, flag or 0.0))))
        return presence, roimod.to_image(mesh[:, :2], M)

    def step(self, frame):
        """Faces on this frame: [{score, box, mesh (478x2 px), kps}]."""
        dets = self.detect(frame)
        faces = []
        used = set()
        for r in self.tracks:
            pres, mesh = self.landmarks(frame, r)
            if mesh is None or pres < self.min_presence:
                continue
            # the track must still sit on a detected face (no drift onto
            # a shoulder): its mesh box against YuNet's boxes
            mb = _box(mesh)
            best, bi = 0.0, None
            for i, (_s, box, _k) in enumerate(dets):
                v = _iou(mb, box)
                if v > best:
                    best, bi = v, i
            if dets and best < 0.25:
                continue
            if bi is not None:
                used.add(bi)
            faces.append({"score": pres, "mesh": mesh})
        for i, (score, box, kps) in enumerate(dets):
            if i in used or len(faces) >= self.max_faces:
                continue
            pres, mesh = self.landmarks(frame, self.det_roi(box, kps))
            if mesh is None or pres < self.min_presence:
                continue
            if any(_iou(_box(mesh), _box(f["mesh"])) > 0.4 for f in faces):
                continue
            faces.append({"score": min(pres, score), "mesh": mesh})
        self.tracks = [self.mesh_roi(f["mesh"]) for f in faces]
        for f in faces:
            f["box"] = _box(f["mesh"])
        faces.sort(key=lambda f: (f["box"][0] + f["box"][2]) / 2.0)
        return faces


def _box(mesh):
    p = np.asarray(mesh)
    return [float(p[:, 0].min()), float(p[:, 1].min()),
            float(p[:, 0].max()), float(p[:, 1].max())]


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def named_points(mesh):
    """{name: (x, y)} px for NAMED (means of their mesh indices)."""
    m = np.asarray(mesh)
    return {n: tuple(float(v) for v in m[list(ix)].mean(axis=0))
            for n, ix in NAMED.items()}


def head_pose(mesh):
    """(yaw, pitch, roll) degrees from the mesh's 2D geometry: roll from
    the outer eye corners; yaw from the nose tip's offset between the two
    cheek edges; pitch from the nose tip between the eye line and the chin.
    Positive yaw: the face turns to screen right; positive pitch: up."""
    m = np.asarray(mesh)
    er, el = m[33], m[263]
    roll = math.degrees(math.atan2(el[1] - er[1], el[0] - er[0]))
    a, b, nose = m[234], m[454], m[1]
    span = max(1e-6, float(np.hypot(*(b - a))))
    t = float(np.dot(nose - a, b - a)) / (span * span)
    yaw = (t - 0.5) * 2.0 * 60.0
    eyes = (er + el) / 2.0
    chin = m[152]
    v = max(1e-6, float(np.hypot(*(chin - eyes))))
    u = float(np.dot(nose - eyes, chin - eyes)) / (v * v)
    pitch = (0.42 - u) * 150.0
    return (round(yaw, 1), round(pitch, 1), round(roll, 1))


def head_ellipse(mesh):
    """(cx, cy, rx, ry, deg): the whole head, not just the face — the face
    oval's fitted ellipse grown for the cranium (the oval stops at the
    hairline): 12% wider, 30% taller, centre raised by 14% of its height,
    tilted with the eye line."""
    import cv2
    m = np.asarray(mesh, np.float32)
    pts = m[list(FACE_OVAL)]
    (cx, cy), (w, h), ang = cv2.fitEllipse(pts.reshape(-1, 1, 2))
    rx, ry = w / 2.0, h / 2.0
    if rx > ry:
        rx, ry = ry, rx
        ang = ang - 90.0
    roll = math.degrees(math.atan2(m[263][1] - m[33][1],
                                   m[263][0] - m[33][0]))
    up = math.radians(roll)
    lift = 0.14 * 2.0 * ry
    cx += lift * math.sin(up)
    cy -= lift * math.cos(up)
    return (float(cx), float(cy), float(rx * 1.12), float(ry * 1.30),
            float(roll))
