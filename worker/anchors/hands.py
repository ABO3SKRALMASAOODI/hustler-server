"""Hands: MediaPipe palm detection + 21 hand landmarks, tracked (3A).

The official hand_landmarker.task graphs through cv2.dnn (anchors/models).
Per frame, like MediaPipe's own graph:

  * a hand tracked on the previous frame gets its ROI from those landmarks
    (wrist -> middle PIP is "up"; MediaPipe's landmarks-to-rect: scale 2.0,
    shift -0.1);
  * when fewer hands are tracked than ``max_hands``, the palm detector runs
    on the letterboxed frame (SSD, 2016 anchors) and every new palm becomes
    a ROI (wrist -> middle MCP up; scale 2.6, shift -0.5);
  * the landmark model sees each ROI upright at 224 px; a presence under
    ``min_presence`` drops the track; two tracks on the same hand merge.

Coordinates out are PIXELS of the frame given; the sidecar child turns them
into source fractions.
"""

import math

import numpy as np

from . import models, roi as roimod

DET_SIZE = 192
LM_SIZE = 224
# hand landmark indices (MediaPipe)
WRIST, THUMB_TIP, INDEX_MCP, INDEX_PIP, INDEX_TIP = 0, 4, 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, PINKY_MCP = 14, 17
_PARTIAL = [0, 1, 2, 3, 5, 6, 9, 10, 13, 14, 17, 18]
NAMES = ("wrist", "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip",
         "index_mcp", "index_pip", "index_dip", "index_tip",
         "middle_mcp", "middle_pip", "middle_dip", "middle_tip",
         "ring_mcp", "ring_pip", "ring_dip", "ring_tip",
         "pinky_mcp", "pinky_pip", "pinky_dip", "pinky_tip")


def _anchors():
    """The palm detector's 2016 SSD anchor centres (fixed size)."""
    out = []
    for stride, per_cell in ((8, 2), (16, 6)):
        n = DET_SIZE // stride
        for y in range(n):
            for x in range(n):
                for _ in range(per_cell):
                    out.append(((x + 0.5) / n, (y + 0.5) / n))
    return np.array(out, np.float32)


_ANCHORS = None


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -80.0, 80.0)))


class HandTracker:
    def __init__(self, task_path, max_hands=2, min_det=0.5,
                 min_presence=0.5, threads=0):
        global _ANCHORS
        if _ANCHORS is None:
            _ANCHORS = _anchors()
        self.det = models.tflite_net(task_path, "hand_detector.tflite",
                                     threads)
        self.lm = models.tflite_net(task_path,
                                    "hand_landmarks_detector.tflite",
                                    threads)
        self.max_hands = int(max_hands)
        self.min_det = float(min_det)
        self.min_presence = float(min_presence)
        self.tracks = []          # [roi]
        self.det_every = 10
        self._since_det = 10**6

    # -- detection --------------------------------------------------------
    def detect(self, frame):
        """[(score, box px [x0,y0,x1,y1], kps px 7x2)] palms, NMS'd."""
        import cv2
        h, w = frame.shape[:2]
        scale = DET_SIZE / float(max(w, h))
        nw, nh = int(round(w * scale)), int(round(h * scale))
        img = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        padx, pady = (DET_SIZE - nw) // 2, (DET_SIZE - nh) // 2
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), np.uint8)
        canvas[pady:pady + nh, padx:padx + nw] = img
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 255.0,
                                     (DET_SIZE, DET_SIZE), swapRB=True)
        out = models.outputs(self.det, blob)
        arrs = sorted(out.values(), key=lambda a: a.shape[-1])
        scores = _sigmoid(arrs[0].reshape(-1).astype(np.float64))
        raw = arrs[1].reshape(-1, 18).astype(np.float64)
        keep = np.where(scores >= self.min_det)[0]
        if keep.size == 0:
            return []
        a = _ANCHORS[keep]
        r = raw[keep] / DET_SIZE
        cx, cy = r[:, 0] + a[:, 0], r[:, 1] + a[:, 1]
        bw, bh = r[:, 2], r[:, 3]
        kps = r[:, 4:].reshape(-1, 7, 2) + a[:, None, :]

        def to_px(xn, yn):
            return ((xn * DET_SIZE - padx) / scale, (yn * DET_SIZE - pady)
                    / scale)
        x0, y0 = to_px(cx - bw / 2, cy - bh / 2)
        x1, y1 = to_px(cx + bw / 2, cy + bh / 2)
        boxes = np.stack([x0, y0, x1, y1], 1)
        kx, ky = to_px(kps[..., 0], kps[..., 1])
        kps_px = np.stack([kx, ky], -1)
        sc = scores[keep]
        idx = cv2.dnn.NMSBoxes([[float(b[0]), float(b[1]),
                                 float(b[2] - b[0]), float(b[3] - b[1])]
                                for b in boxes], sc.tolist(), self.min_det,
                               0.3)
        idx = np.array(idx).reshape(-1)
        return [(float(sc[i]), boxes[i].tolist(), kps_px[i]) for i in idx]

    @staticmethod
    def palm_roi(box, kps):
        rot = roimod.rotation(kps[0], kps[2], 90.0)
        return roimod.from_box(box, rot, scale=2.6, shift=(0.0, -0.5))

    @staticmethod
    def landmarks_roi(pts):
        p = np.asarray(pts, np.float64)
        up = ((p[INDEX_PIP] + p[RING_PIP]) / 2.0 + p[MIDDLE_PIP]) / 2.0
        rot = roimod.rotation(p[WRIST], up, 90.0)
        return roimod.from_points(p[_PARTIAL], rot, scale=2.0,
                                  shift=(0.0, -0.1))

    # -- landmarks ----------------------------------------------------------
    def landmarks(self, frame, roi):
        """(presence, 21x2 px, handedness) for one ROI."""
        import cv2
        crop, M = roimod.crop(frame, roi, LM_SIZE)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 255.0, (LM_SIZE, LM_SIZE),
                                     swapRB=True)
        out = models.outputs(self.lm, blob)
        lms = scal = None
        flags = []
        for name, arr in sorted(out.items()):
            if arr.size == 63 and lms is None:
                lms = arr.reshape(21, 3)
            elif arr.size == 1:
                flags.append(float(arr.reshape(-1)[0]))
        # Identity: image landmarks; Identity_1: presence; Identity_2:
        # handedness (outputs sorted by name keep that order)
        presence = flags[0] if flags else 0.0
        if presence > 1.0 or presence < 0.0:
            presence = float(_sigmoid(np.array(presence)))
        handed = flags[1] if len(flags) > 1 else 0.5
        pts = roimod.to_image(lms[:, :2], M)
        del scal
        return presence, pts, handed

    def step(self, frame):
        """Hands on this frame: [{score, pts (21x2 px), box, side}]."""
        hands = []
        for r in self.tracks:
            pres, pts, handed = self.landmarks(frame, r)
            if pres >= self.min_presence:
                hands.append({"score": pres, "pts": pts, "handed": handed})
        self._since_det += 1
        if len(hands) < self.max_hands and (
                self._since_det >= self.det_every or not hands):
            self._since_det = 0
            for score, box, kps in self.detect(frame):
                if len(hands) >= self.max_hands:
                    break
                bx = np.array(box)
                c = (bx[:2] + bx[2:]) / 2.0
                if any(np.hypot(*(np.asarray(h["pts"])[MIDDLE_MCP] - c))
                       < 0.6 * max(bx[2] - bx[0], bx[3] - bx[1])
                       for h in hands):
                    continue
                pres, pts, handed = self.landmarks(frame,
                                                   self.palm_roi(box, kps))
                if pres >= self.min_presence:
                    hands.append({"score": pres, "pts": pts,
                                  "handed": handed})
        hands = self._dedupe(hands)
        self.tracks = [self.landmarks_roi(h["pts"]) for h in hands]
        for h in hands:
            p = np.asarray(h["pts"])
            h["box"] = [float(p[:, 0].min()), float(p[:, 1].min()),
                        float(p[:, 0].max()), float(p[:, 1].max())]
        hands.sort(key=lambda h: float(np.asarray(h["pts"])[:, 0].mean()))
        for k, h in enumerate(hands):
            h["side"] = ("only" if len(hands) == 1 else
                         "left" if k == 0 else "right")
        return hands

    @staticmethod
    def _dedupe(hands):
        out = []
        for h in sorted(hands, key=lambda h: -h["score"]):
            p = np.asarray(h["pts"])
            span = max(1.0, float(np.ptp(p[:, 0]) + np.ptp(p[:, 1])) / 2.0)
            if any(float(np.mean(np.hypot(*(p - np.asarray(o["pts"])).T)))
                   < 0.25 * span for o in out):
                continue
            out.append(h)
        return out


def hand_span(pts):
    """A hand's size in pixels: wrist to middle MCP, x 2.2 (a steady scale
    for graphics that follow the hand's size)."""
    p = np.asarray(pts)
    return float(np.hypot(*(p[MIDDLE_MCP] - p[WRIST]))) * 2.2


def hand_angle(pts):
    """The hand's roll in degrees (0 = fingers up, clockwise positive)."""
    p = np.asarray(pts)
    v = p[MIDDLE_MCP] - p[WRIST]
    return math.degrees(math.atan2(v[0], -v[1]))
