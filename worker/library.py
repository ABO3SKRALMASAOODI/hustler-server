"""The footage library: model-free shot descriptors (Wave 2, track 2E).

INTERFACES_W2 §6. Every lane can compute it (numpy + OpenCV + ffmpeg; no
model, no vision lane): a clip is split into SHOTS at its own cuts (a
montage never cuts into the middle of somebody else's edit), and each shot
gets a descriptor ``embed.classic.v1``:

  luma     8x8 luma layout (zero-mean, unit norm): framing / composition
  lab      4x4 Lab colour layout: palette and where it sits
  edges    edge-orientation histogram in 2x2 quadrants (GIST-like): the
           structure (verticals of a corridor, the arc of a horizon)
  faces    face boxes (Haar cascades, profile included: follow.detect)
  circles  Hough circles (shape chains: a wheel -> a planet -> a ring)
  tone     brightness, colour temperature (b*), saturation; day / night
  sharp    focus (Laplacian variance) and centre/border focus ratio
  motion   mean frame difference inside the shot (a held vs a moving shot)

From those: a cluster id (shots that look alike, across clips), a scale
(close / medium / wide: face height, else shallow depth of field) and a
montage score. Stored per clip sha in the sidecar store as kind
``embed.classic`` so a clip is described once.
"""

import json
import math
import os
import subprocess

import numpy as np

FORMAT = "embed.classic.v1"
KIND = "embed.classic"
MODEL_ID = "classic.cv"
VERSION = 1
CUT_FPS = 10.0
CUT_W, CUT_H = 96, 54
DESC_W, DESC_H = 192, 108
CUT_MIN_S = 0.4                 # a shot shorter than this is a flash
CLUSTER_SIM = 0.88
SHOT_PAD_S = 0.12               # keep off a detected cut by this much


def _ffmpeg():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def decode(path, fps, w, h, pix="gray", max_s=600.0):
    """[n, h, w(, 3)] uint8 frames sampled at ``fps``."""
    cmd = [_ffmpeg(), "-v", "error", "-i", path, "-t", f"{max_s:.3f}",
           "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-f",
           "rawvideo", "-pix_fmt", "gray" if pix == "gray" else "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    c = 1 if pix == "gray" else 3
    n = len(raw) // (w * h * c)
    arr = np.frombuffer(raw[:n * w * h * c], np.uint8)
    return arr.reshape((n, h, w) if c == 1 else (n, h, w, 3))


def cuts(gray, fps=CUT_FPS):
    """Cut times (s) in a gray frame stack: a frame difference far above
    its neighbourhood (hard cuts; a fast pan raises the neighbourhood
    too, so it is not a cut)."""
    if len(gray) < 3:
        return []
    f = gray.astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    out = []
    for i, v in enumerate(d):
        lo, hi = max(0, i - 5), min(len(d), i + 6)
        ctx = np.concatenate([d[lo:i], d[i + 1:hi]])
        base = float(np.median(ctx)) if ctx.size else 0.0
        if v > max(12.0, 3.0 * base + 5.0):
            out.append((i + 1) / float(fps))
    return out


def shots_of(duration, cut_times):
    """[(t0, t1)] shots between cuts, shorter than CUT_MIN_S dropped."""
    edges = [0.0] + sorted(cut_times) + [float(duration)]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= CUT_MIN_S]


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:  # noqa: BLE001
        return None


def describe(rgb):
    """The classic descriptor of one RGB frame (uint8 [h, w, 3])."""
    cv2 = _cv2()
    img = rgb.astype(np.float32) / 255.0
    gray = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    h, w = gray.shape

    def pool(a, gy, gx):
        ys = np.linspace(0, a.shape[0], gy + 1).astype(int)
        xs = np.linspace(0, a.shape[1], gx + 1).astype(int)
        return np.array([[a[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].mean(axis=(0, 1))
                          for j in range(gx)] for i in range(gy)])
    luma = pool(gray, 8, 8).ravel()
    luma = luma - luma.mean()
    luma = luma / (np.linalg.norm(luma) + 1e-6)
    if cv2 is not None:
        lab_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        L = lab_img[..., 0] * (100.0 / 255.0)
        A = lab_img[..., 1] - 128.0
        B = lab_img[..., 2] - 128.0
    else:
        L, A, B = gray * 100.0, (img[..., 0] - img[..., 1]) * 100.0, \
            (img[..., 1] - img[..., 2]) * 100.0
    lab = np.concatenate([pool(L, 4, 4).ravel() / 100.0,
                          pool(A, 4, 4).ravel() / 64.0,
                          pool(B, 4, 4).ravel() / 64.0])
    gy, gx = np.gradient(gray)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) % math.pi) / math.pi * 8.0
    bins = np.minimum(7, ang.astype(int))
    edges = []
    for qy in (slice(0, h // 2), slice(h // 2, h)):
        for qx in (slice(0, w // 2), slice(w // 2, w)):
            hist = np.bincount(bins[qy, qx].ravel(),
                               weights=mag[qy, qx].ravel(), minlength=8)
            edges.append(hist)
    edges = np.concatenate(edges)
    edges = edges / (edges.sum() + 1e-6)
    # focus: Laplacian variance, centre vs border
    lap = np.abs(np.gradient(gx, axis=1) + np.gradient(gy, axis=0))
    c = lap[h // 4:3 * h // 4, w // 4:3 * w // 4]
    border = np.concatenate([lap[:h // 6].ravel(), lap[-h // 6:].ravel(),
                             lap[:, :w // 8].ravel(), lap[:, -w // 8:].ravel()])
    sharp = float(lap.var() * 1e4)
    dof = float((border.mean() + 1e-6) / (c.mean() + 1e-6))
    bright = float(L.mean() / 100.0)
    temp = float(B.mean())
    sat = float(np.hypot(A, B).mean() / 64.0)
    faces = []
    circles = []
    if cv2 is not None:
        g8 = (gray * 255).astype(np.uint8)
        try:
            import follow
            big = cv2.resize(g8, (w * 2, h * 2))
            faces = [list(map(lambda v: round(float(v), 3), b))
                     for b, _look in follow.detect(big)][:4]
        except Exception:  # noqa: BLE001
            faces = []
        try:
            cs = cv2.HoughCircles(cv2.GaussianBlur(g8, (5, 5), 1.5),
                                  cv2.HOUGH_GRADIENT, dp=1.5,
                                  minDist=max(8, w // 6), param1=120,
                                  param2=38, minRadius=max(4, h // 12),
                                  maxRadius=h // 2)
            if cs is not None:
                circles = [[round(float(x) / w, 3), round(float(y) / h, 3),
                            round(float(r) / h, 3)] for x, y, r in cs[0][:4]]
        except Exception:  # noqa: BLE001
            circles = []
    vec = np.concatenate([luma * 1.0, lab * 0.9, edges * 3.0])
    vec = vec / (np.linalg.norm(vec) + 1e-6)
    night = bright < 0.22 or (bright < 0.32 and temp < -4.0)
    return {"vec": [round(float(v), 4) for v in vec],
            "faces": faces, "circles": circles,
            "bright": round(bright, 3), "temp": round(temp, 2),
            "sat": round(sat, 3), "sharp": round(sharp, 3),
            "dof": round(dof, 3), "night": bool(night)}


def scale_of(desc):
    """close | medium | wide."""
    faces = desc.get("faces") or []
    if faces:
        fh = max(float(b[3]) - float(b[1]) for b in faces)
        return "close" if fh >= 0.22 else "medium" if fh >= 0.08 else "wide"
    return "close" if desc.get("dof", 1.0) < 0.45 else "wide"


def score_of(desc, motion):
    s = 0.5 + 0.2 * min(1.0, desc.get("sharp", 0.0) / 6.0)
    if desc.get("bright", 0.5) < 0.12:
        s -= 0.2
    # a shot that moves a little beats a dead or a smeared one
    s += 0.15 * math.exp(-((motion - 6.0) / 6.0) ** 2)
    return round(max(0.0, min(1.0, s)), 3)


def analyze_clip(path, max_s=600.0):
    """{format, duration, shots: [{t0, t1, mid, motion, desc}]} of a clip
    file (or a still image: one shot)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        cv2 = _cv2()
        img = cv2.imread(path) if cv2 is not None else None
        if img is None:
            raise ValueError("unreadable image")
        rgb = cv2.cvtColor(cv2.resize(img, (DESC_W, DESC_H),
                                      interpolation=cv2.INTER_AREA),
                           cv2.COLOR_BGR2RGB)
        d = describe(rgb)
        return {"format": FORMAT, "duration": None, "image": True,
                "size": [int(img.shape[1]), int(img.shape[0])],
                "shots": [{"t0": 0.0, "t1": 0.0, "mid": 0.0, "motion": 0.0,
                           "desc": d}]}
    gray = decode(path, CUT_FPS, CUT_W, CUT_H, "gray", max_s)
    dur = len(gray) / CUT_FPS
    cts = cuts(gray)
    sh = shots_of(dur, cts)
    rgb = decode(path, 2.0, DESC_W, DESC_H, "rgb", max_s)
    out = []
    for a, b in sh:
        mid = (a + b) / 2.0
        k = min(len(rgb) - 1, max(0, int(round(mid * 2.0))))
        d = describe(rgb[k]) if len(rgb) else {}
        g = gray[int(a * CUT_FPS):max(int(a * CUT_FPS) + 2,
                                      int(b * CUT_FPS))].astype(np.float32)
        motion = float(np.abs(np.diff(g, axis=0)).mean()) if len(g) > 1 \
            else 0.0
        out.append({"t0": round(a, 3), "t1": round(b, 3),
                    "mid": round(mid, 3), "motion": round(motion, 3),
                    "desc": d})
    return {"format": FORMAT, "duration": round(dur, 3), "cuts": cts,
            "shots": out}


def _key(sha, params=None):
    import sidecars
    return sidecars.sidecar_key(KIND, sha, MODEL_ID, VERSION, params or {},
                                (0.0, 0.0))


def cached(sha):
    try:
        import sidecars
        blob = sidecars._store().get_bytes(
            sidecars.data_key(KIND, sha, _key(sha), "classic.json"))
        if not blob:
            return None
        d = json.loads(blob if isinstance(blob, str) else blob.decode())
        return d if d.get("format") == FORMAT else None
    except Exception:  # noqa: BLE001
        return None


def store(sha, doc):
    try:
        import sidecars
        st = sidecars._store()
        st.put_bytes(sidecars.data_key(KIND, sha, _key(sha), "classic.json"),
                     json.dumps(doc, separators=(",", ":")).encode(),
                     "application/json")
        st.put_bytes(sidecars.manifest_key(KIND, sha, _key(sha)),
                     json.dumps({"key": _key(sha), "kind": KIND,
                                 "model_id": MODEL_ID,
                                 "model_version": str(VERSION),
                                 "params": {}, "src_range": [0.0, 0.0],
                                 "format": FORMAT}).encode(),
                     "application/json")
        return True
    except Exception:  # noqa: BLE001
        return False


def clip_doc(path, sha=None):
    if sha:
        hit = cached(sha)
        if hit:
            return hit
    doc = analyze_clip(path)
    if sha:
        store(sha, doc)
    return doc


def cosine(a, b):
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def cluster(entries, sim=CLUSTER_SIM):
    """Greedy clustering of [{..., desc: {vec}}] in place: ``cluster``
    ids 'c1', 'c2' ... (shots that look alike, across clips)."""
    cents = []
    for e in entries:
        v = np.asarray((e.get("desc") or {}).get("vec") or [0.0], np.float32)
        best, bi = -1.0, -1
        for i, (c, n) in enumerate(cents):
            if c.shape != v.shape:
                continue
            s = cosine(c / max(1, n), v)
            if s > best:
                best, bi = s, i
        if bi >= 0 and best >= sim:
            c, n = cents[bi]
            cents[bi] = (c + v, n + 1)
            e["cluster"] = f"c{bi + 1}"
        else:
            cents.append((v.copy(), 1))
            e["cluster"] = f"c{len(cents)}"
    return entries


def pool_entries(docs):
    """{storage_key: doc} -> {storage_key: [montage shot dicts]} (shots
    padded off their cuts, clustered across clips, scaled, scored)."""
    flat = []
    for key, doc in docs.items():
        for k, sh in enumerate(doc.get("shots") or []):
            if doc.get("image"):
                t0, t1 = 0.0, 600.0
            else:
                t0 = sh["t0"] + (SHOT_PAD_S if sh["t0"] > 0 else 0.05)
                t1 = sh["t1"] - (SHOT_PAD_S if sh["t1"] < (doc.get(
                    "duration") or sh["t1"]) - 0.05 else 0.05)
                if t1 - t0 < 0.25:
                    continue
            flat.append({"key": key, "t0": round(t0, 3), "t1": round(t1, 3),
                         "desc": sh.get("desc") or {},
                         "scale": scale_of(sh.get("desc") or {}),
                         "score": score_of(sh.get("desc") or {},
                                           sh.get("motion", 0.0))})
    cluster(flat)
    out = {}
    for e in flat:
        out.setdefault(e["key"], []).append(
            {"t0": e["t0"], "t1": e["t1"], "cluster": e["cluster"],
             "scale": e["scale"], "score": e["score"]})
    return out


def shots_for(ctx, keys):
    """{storage_key: [shot dicts]} for project clips/images (downloads
    each once; descriptors cached by sha)."""
    import agent_tools
    docs = {}
    for key in keys or []:
        if not key:
            continue
        asset, err = agent_tools._resolve_media_asset(
            ctx, key, ("video_clip", "generated_video", "image_ref"))
        if asset is None:
            continue
        try:
            path = agent_tools._asset_local_path(ctx, asset)
            docs[key] = clip_doc(path, asset.get("sha256"))
        except Exception:  # noqa: BLE001 — that clip is one shot then
            continue
    return pool_entries(docs)
