"""Read anchors: where an anchor target is, second by second (3A).

Every lane: pure numpy over READY sidecar chunks (sidecars.ready_chunks:
a status read, never a launch). Everything is on the SOURCE clock in
SOURCE frame fractions; world_layer.py maps it to program pixels.

A ``Series`` is the target over time: ``t`` (source seconds), ``xy`` (N x
2), ``size`` (N, the target's own scale: face height, hand span, in
fractions of the frame HEIGHT), ``angle`` (N, degrees, clockwise), ``quad``
(N x 4 x 2, for frame and plane targets) and ``ok`` (N). NaN = not seen.
"""

import math

import numpy as np

from . import smooth as smoothmod

FACE_NAMES = ("iris_r", "iris_l", "eye_r", "eye_l", "eye_r_out",
              "eye_l_out", "brow_r", "brow_l", "nose", "nose_bridge",
              "mouth", "mouth_r", "mouth_l", "chin", "forehead", "ear_r",
              "ear_l", "cheek_r", "cheek_l")
# screen-side names -> the anatomical pair (the screen-left one is the one
# with the smaller x on each sample)
_PAIRS = {"eye": ("eye_r", "eye_l"), "iris": ("iris_r", "iris_l"),
          "brow": ("brow_r", "brow_l"), "ear": ("ear_r", "ear_l")}
HAND = {"wrist": (0,), "thumb_tip": (4,), "index_tip": (8,),
        "middle_tip": (12,), "palm": (0, 5, 9, 13, 17),
        "pinch": (4, 8)}
POSE = {"nose": (0,), "shoulder_l": (11,), "shoulder_r": (12,),
        "chest": (11, 12), "elbow_l": (13,), "elbow_r": (14,),
        "wrist_l": (15,), "wrist_r": (16,), "hip_l": (23,), "hip_r": (24,),
        "knee_l": (25,), "knee_r": (26,), "ankle_l": (27,), "ankle_r": (28,)}
# pose names are the PERSON's sides (MediaPipe): screen sides are mapped
# per sample like the face's


class Series:
    def __init__(self, t, xy, size=None, angle=None, quad=None, ok=None,
                 kind=""):
        self.t = np.asarray(t, np.float64)
        n = len(self.t)
        self.xy = np.asarray(xy, np.float64).reshape(n, 2)
        self.size = (np.full(n, np.nan) if size is None
                     else np.asarray(size, np.float64))
        self.angle = (np.zeros(n) if angle is None
                      else np.asarray(angle, np.float64))
        self.quad = None if quad is None else np.asarray(quad, np.float64)
        self.ok = (np.all(np.isfinite(self.xy), axis=1) if ok is None
                   else np.asarray(ok, bool))
        self.kind = kind

    def __len__(self):
        return len(self.t)

    @property
    def coverage(self):
        return float(self.ok.mean()) if len(self.t) else 0.0

    def smoothed(self, smooth=0.5, aspect=1.0):
        """A copy with xy (and size, angle, quad) One-Euro filtered; short
        gaps bridged. ``aspect`` = frame W/H, so x and y move in the same
        units."""
        if len(self.t) < 2:
            return self
        scale = np.nanmedian(self.size) if np.any(np.isfinite(self.size)) \
            else 0.1
        xy = self.xy.copy()
        xy[:, 0] *= aspect
        xy = smoothmod.bridge(self.t, xy)
        xy = smoothmod.one_euro(self.t, xy, smooth, scale=max(1e-3, scale))
        xy[:, 0] /= aspect
        size = smoothmod.one_euro(self.t, smoothmod.bridge(self.t,
                                                           self.size),
                                  min(1.0, smooth + 0.2))[:, 0]
        ang = np.unwrap(np.radians(np.nan_to_num(self.angle)))
        ang = np.degrees(smoothmod.one_euro(self.t, ang, min(1.0,
                                                             smooth + 0.2))
                         [:, 0])
        quad = None
        if self.quad is not None:
            q = self.quad.reshape(len(self.t), 8).copy()
            q[:, 0::2] *= aspect
            q = smoothmod.one_euro(self.t, smoothmod.bridge(self.t, q),
                                   smooth, scale=max(1e-3, scale))
            q[:, 0::2] /= aspect
            quad = q.reshape(-1, 4, 2)
        ok = np.all(np.isfinite(xy), axis=1)
        return Series(self.t, xy, size, ang, quad, ok, self.kind)

    def at(self, ts):
        """(xy, size, angle, quad, ok) linearly sampled at source seconds
        ``ts`` (NaN where not seen)."""
        xy = smoothmod.sample(self.t, self.xy, ts)
        size = smoothmod.sample(self.t, self.size, ts)[:, 0]
        ang = smoothmod.sample(self.t, self.angle, ts)[:, 0]
        quad = None
        if self.quad is not None:
            quad = smoothmod.sample(self.t, self.quad.reshape(len(self.t), 8),
                                    ts).reshape(-1, 4, 2)
        ok = np.all(np.isfinite(xy), axis=1)
        return xy, size, ang, quad, ok


# ── loading ──────────────────────────────────────────────────────────────

def load(kind, source, ranges, params=None):
    """(samples, ready share, meta): the samples ([{t, ...}], sorted,
    de-duplicated) of the READY chunks of
    ``kind`` over SOURCE ``ranges``, and the share of wanted chunks that
    were ready."""
    import sidecars
    try:
        spans = sidecars.chunks_for(source.shots, source.duration_s, ranges)
        rows = sidecars.ready_chunks(kind, source.sha256, source.shots,
                                     source.duration_s, ranges, params)
    except Exception:  # noqa: BLE001 — unknown reads as not measured
        return [], 0.0, {}
    samples = []
    meta = {}
    for _c, m in rows:
        data = sidecars.read_json_chunk(kind, source.sha256, m) or {}
        meta = {k: v for k, v in data.items() if k != "samples"}
        samples += data.get("samples") or []
    samples.sort(key=lambda s: float(s.get("t", 0.0)))
    out, last = [], None
    for s in samples:
        t = round(float(s.get("t", 0.0)), 3)
        if last is not None and abs(t - last) < 1e-4:
            continue
        out.append(s)
        last = t
    share = len(rows) / float(len(spans)) if spans else 0.0
    return out, share, meta


def _pick(cands, which, prev_xy, key_x):
    """One candidate per sample: ``which`` any (continuity with the previous
    pick, else the largest / first), left / right (screen), or an index in
    screen order."""
    if not cands:
        return None
    if which in ("left", "right"):
        return sorted(cands, key=key_x)[0 if which == "left" else -1]
    if isinstance(which, int):
        c = sorted(cands, key=key_x)
        return c[which] if which < len(c) else None
    if prev_xy is not None and len(cands) > 1:
        return min(cands, key=lambda c: abs(key_x(c) - prev_xy))
    return cands[0]


def face_series(samples, part="face", which="any", meta=None):
    """A face target: ``face`` (the face centre, size = face height),
    ``face.<part>`` (screen-sided names), ``head`` (the head ellipse:
    centre, size = its height, angle = its roll)."""
    names = list((meta or {}).get("names") or FACE_NAMES)
    t, xy, size, ang, quad = [], [], [], [], []
    prev = None
    for s in samples:
        faces = s.get("faces") or []

        def cx(f):
            return (f["b"][0] + f["b"][2]) / 2.0
        if which == "any" and faces and prev is None:
            faces = sorted(faces, key=lambda f: -(f["b"][2] - f["b"][0])
                           * (f["b"][3] - f["b"][1]))
        f = _pick(faces, which, prev, cx)
        t.append(float(s["t"]))
        if f is None:
            xy.append([np.nan, np.nan])
            size.append(np.nan)
            ang.append(np.nan)
            continue
        prev = cx(f)
        pts = {n: (f["p"][2 * i], f["p"][2 * i + 1])
               for i, n in enumerate(names) if 2 * i + 1 < len(f["p"])}
        fh = f["b"][3] - f["b"][1]
        roll = float((f.get("hp") or [0, 0, 0])[2])
        if part == "head":
            cxh, cyh, rx, ry, deg = f["he"]
            xy.append([cxh, cyh])
            size.append(2.0 * ry)
            ang.append(float(deg))
            continue
        if part in ("face", None):
            xy.append([(f["b"][0] + f["b"][2]) / 2.0,
                       (f["b"][1] + f["b"][3]) / 2.0])
        else:
            base, _, side = part.rpartition("_")
            if base in _PAIRS and side in ("l", "r"):
                a, b = (pts[_PAIRS[base][0]], pts[_PAIRS[base][1]])
                left, right = (a, b) if a[0] <= b[0] else (b, a)
                xy.append(list(left if side == "l" else right))
            elif part in pts:
                xy.append(list(pts[part]))
            else:
                xy.append([np.nan, np.nan])
        size.append(fh)
        ang.append(roll)
    return Series(t, xy, size, ang, kind="face")


def _hand_pts(h):
    p = h["p"]
    return np.array(p, np.float64).reshape(-1, 2)


def hand_series(samples, part="index_tip", which="any"):
    """A hand target. ``frame``: the quad of BOTH hands' thumb and index
    tips (a window between the fingers), xy = its centre."""
    t, xy, size, ang, quad = [], [], [], [], []
    prev = None
    for s in samples:
        hands = s.get("hands") or []
        t.append(float(s["t"]))
        if part == "frame":
            if len(hands) < 2:
                xy.append([np.nan, np.nan])
                size.append(np.nan)
                ang.append(np.nan)
                quad.append(np.full((4, 2), np.nan))
                continue
            hs = sorted(hands, key=lambda h: _hand_pts(h)[:, 0].mean())
            L, R = _hand_pts(hs[0]), _hand_pts(hs[-1])
            q = order_convex([L[4], L[8], R[4], R[8]])
            quad.append(q)
            xy.append(list(q.mean(axis=0)))
            size.append(float(np.hypot(*(q[2] - q[0]))))
            ang.append(0.0)
            continue

        def cx(h):
            return float(_hand_pts(h)[:, 0].mean())
        h = _pick(hands, which, prev, cx)
        if h is None:
            xy.append([np.nan, np.nan])
            size.append(np.nan)
            ang.append(np.nan)
            continue
        prev = cx(h)
        P = _hand_pts(h)
        xy.append(list(P[list(HAND[part])].mean(axis=0)))
        span = float(np.hypot(*(P[9] - P[0]))) * 2.2
        size.append(span)
        v = P[9] - P[0]
        ang.append(math.degrees(math.atan2(v[0], -v[1])))
    return Series(t, xy, size, ang, quad if part == "frame" else None,
                  kind="hand")


def order_convex(pts):
    """Four points as a convex quad, clockwise from the top-left."""
    p = np.asarray(pts, np.float64)
    c = p.mean(axis=0)
    a = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(a)]
    # start at the corner nearest the top-left
    k = int(np.argmin(p[:, 0] + p[:, 1]))
    return np.roll(p, -k, axis=0)


def pose_series(samples, joint="chest", which="any", min_vis=0.5):
    """A body joint (the person's own sides, as MediaPipe names them; the
    verbs map screen sides). size = shoulder width x 2."""
    t, xy, size, ang = [], [], [], []
    for s in samples:
        people = s.get("people") or []
        t.append(float(s["t"]))
        if not people:
            xy.append([np.nan, np.nan])
            size.append(np.nan)
            ang.append(np.nan)
            continue
        P = np.array(people[0]["p"], np.float64).reshape(-1, 3)
        idx = list(POSE[joint])
        if np.min(P[idx, 2]) < min_vis:
            xy.append([np.nan, np.nan])
        else:
            xy.append(list(P[idx, :2].mean(axis=0)))
        sw = float(np.hypot(*(P[11, :2] - P[12, :2])))
        size.append(sw * 2.0)
        v = P[12, :2] - P[11, :2]
        ang.append(math.degrees(math.atan2(v[1], v[0])) if v[0] < 0
                   else math.degrees(math.atan2(-v[1], -v[0])))
    return Series(t, xy, size, ang, kind="pose")


def track_series(data_rows, fid=None):
    """A tracked point (track.points chunks' rows for one seed id)."""
    rows = []
    for d in data_rows:
        for tr in d.get("tracks") or []:
            if fid is not None and str(tr.get("id")) != str(fid):
                continue
            rows += [r for r in tr.get("rows") or []]
    rows.sort(key=lambda r: r[0])
    t = [r[0] for r in rows]
    xy = [[r[1], r[2]] if r[3] else [np.nan, np.nan] for r in rows]
    return Series(t, xy, kind="track")


def plane_series(samples, quad):
    """A plane: per sample the seed quad carried by H (seed -> frame)."""
    q0 = np.asarray(quad, np.float64)
    t, xy, quads = [], [], []
    for s in samples:
        H = np.array(s["H"], np.float64).reshape(3, 3)
        p = np.c_[q0, np.ones(4)] @ H.T
        q = p[:, :2] / p[:, 2:3]
        t.append(float(s["t"]))
        quads.append(q)
        xy.append(list(q.mean(axis=0)))
    return Series(t, xy, quad=quads, kind="plane")


def static_plane(quad, t0, t1, step=0.5):
    """A plane on a locked-off shot: the seed quad, constant."""
    q0 = np.asarray(quad, np.float64)
    ts = list(np.arange(float(t0), float(t1) + step, step))
    return Series(ts, [q0.mean(axis=0)] * len(ts), quad=[q0] * len(ts),
                  kind="plane")
