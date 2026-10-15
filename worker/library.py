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
WINDOW_S = 4.0                  # long shots are described per window


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


RGB_FPS = 2.0


def decode_once(path, max_s=600.0, start=0.0):
    """ONE decode pass (a 4K60 clip is decode-bound): RGB frames at
    CUT_FPS and DESC size, streamed, from ``start`` seconds; returns (gray
    [n, CUT_H, CUT_W] at CUT_FPS, rgb [m, DESC_H, DESC_W, 3] at RGB_FPS)
    on the window's own clock (0 = ``start``)."""
    seek = ["-ss", f"{float(start):.3f}"] if start and start > 0 else []
    cmd = [_ffmpeg(), "-v", "error", "-threads", "0"] + seek + [
           "-i", path, "-t",
           f"{max_s:.3f}", "-an", "-vf",
           f"fps={CUT_FPS},scale={DESC_W}:{DESC_H}:flags=area", "-f",
           "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    size = DESC_W * DESC_H * 3
    step = int(round(CUT_FPS / RGB_FPS))
    grays, rgbs, i = [], [], 0
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, np.uint8).reshape(DESC_H, DESC_W, 3)
        g = f.mean(axis=2)
        g = g.reshape(CUT_H, DESC_H // CUT_H, CUT_W,
                      DESC_W // CUT_W).mean(axis=(1, 3))
        grays.append(g.astype(np.uint8))
        if i % step == 0:
            rgbs.append(f.copy())
        i += 1
    proc.wait()
    if not grays:
        return (np.zeros((0, CUT_H, CUT_W), np.uint8),
                np.zeros((0, DESC_H, DESC_W, 3), np.uint8))
    return np.stack(grays), np.stack(rgbs)


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


_YUNET = {}
YUNET_MAX_SIDE = 640


def yunet_faces(rgb, score=0.6):
    """[{box, eyes: [[lx, ly], [rx, ry]], score}] by YuNet (the pinned
    model, when this lane has it) in frame fractions; None when the model
    is not here (the caller falls back to Haar boxes)."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    try:
        import analysis_jobs
        path, _why = analysis_jobs.model_ready("yunet")
    except Exception:  # noqa: BLE001
        path = None
    if not path:
        return None
    h, w = rgb.shape[:2]
    # YuNet's anchors stop at ~300 px faces: a close-up on a full-size frame
    # comes back as half a face. Detect at <= MAX_SIDE (fractions out).
    if max(w, h) > YUNET_MAX_SIDE:
        k = YUNET_MAX_SIDE / float(max(w, h))
        w, h = max(2, int(round(w * k))), max(2, int(round(h * k)))
        rgb = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
    key = (path, w, h)
    det = _YUNET.get(key)
    if det is None:
        det = cv2.FaceDetectorYN.create(path, "", (w, h), score, 0.3, 20)
        _YUNET.clear()
        _YUNET[key] = det
    _ok, found = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    out = []
    for f in found if found is not None else ():
        x, y, bw, bh = (float(v) for v in f[:4])
        out.append({"box": [round(max(0.0, x / w), 4), round(max(0.0, y / h), 4),
                            round(min(1.0, (x + bw) / w), 4),
                            round(min(1.0, (y + bh) / h), 4)],
                    "eyes": [[round(float(f[4]) / w, 4),
                              round(float(f[5]) / h, 4)],
                             [round(float(f[6]) / w, 4),
                              round(float(f[7]) / h, 4)]],
                    "score": round(float(f[14]), 3)})
    out.sort(key=lambda d: -(d["box"][2] - d["box"][0]) *
             (d["box"][3] - d["box"][1]))
    return out


CIRCLE_SUPPORT = 0.6


def circle_support(gx, gy, cx, cy, r, n=72):
    """Share of the circumference with a strong RADIAL edge: a real disc,
    wheel or hatch rim, not an accumulator artefact of busy texture."""
    h, w = gx.shape
    mag = np.hypot(gx, gy)
    thr = max(8.0, float(np.percentile(mag, 75)))
    ok = 0
    tot = 0
    for k in range(n):
        a = 2 * math.pi * k / n
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= xi < w and 0 <= yi < h):
            continue
        tot += 1
        g = mag[yi, xi]
        if g < thr:
            continue
        cos = abs(gx[yi, xi] * math.cos(a) + gy[yi, xi] * math.sin(a)) / g
        ok += cos > 0.7
    return ok / float(max(tot, 1)) if tot >= n * 0.6 else 0.0


def find_circles(g8, max_n=4):
    """[[cx, cy, r]] verified circles (frame fractions; r of the height)."""
    cv2 = _cv2()
    h, w = g8.shape
    blur = cv2.GaussianBlur(g8, (5, 5), 1.5)
    cs = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2,
                          minDist=max(8, w // 6), param1=120, param2=30,
                          minRadius=max(5, h // 10), maxRadius=int(h * 0.48))
    if cs is None:
        return []
    f = blur.astype(np.float32)
    gy, gx = np.gradient(f)
    out = []
    for x, y, r in cs[0][:12]:
        sup = circle_support(gx, gy, float(x), float(y), float(r))
        if sup >= CIRCLE_SUPPORT:
            out.append([round(float(x) / w, 3), round(float(y) / h, 3),
                        round(float(r) / h, 3), round(sup, 2)])
    out.sort(key=lambda c: -c[3])
    return [c[:3] for c in out[:max_n]]


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
    eyes = []
    circles = []
    if cv2 is not None:
        g8 = (gray * 255).astype(np.uint8)
        yn = None
        try:
            big = cv2.resize(rgb, (w * 3, h * 3))
            yn = yunet_faces(big)
        except Exception:  # noqa: BLE001
            yn = None
        if yn is not None:
            faces = [d["box"] for d in yn][:4]
            eyes = [d["eyes"] for d in yn][:4]
        else:
            try:
                import follow
                big = cv2.resize(g8, (w * 2, h * 2))
                faces = [list(map(lambda v: round(float(v), 3), b))
                         for b, _look in follow.detect(big)][:4]
            except Exception:  # noqa: BLE001
                faces = []
        try:
            # a head is round: a circle centred inside a face is that face
            circles = [c for c in find_circles(g8)
                       if not any(b[0] <= c[0] <= b[2] and
                                  b[1] - 0.1 <= c[1] <= b[3] + 0.05
                                  for b in faces)]
        except Exception:  # noqa: BLE001
            circles = []
    frame_vec = np.concatenate([luma, edges * 3.0])
    frame_vec = frame_vec / (np.linalg.norm(frame_vec) + 1e-6)
    vec = np.concatenate([luma * 1.0, lab * 0.9, edges * 3.0])
    vec = vec / (np.linalg.norm(vec) + 1e-6)
    night = bright < 0.22 or (bright < 0.32 and temp < -4.0)
    return {"vec": [round(float(v), 4) for v in vec],
            "layout": [round(float(v), 4) for v in frame_vec],
            "faces": faces, "eyes": eyes, "circles": circles,
            "bright": round(bright, 3), "temp": round(temp, 2),
            "sat": round(sat, 3), "sharp": round(sharp, 3),
            "dof": round(dof, 3), "night": bool(night)}


def frame_at(path, t, width=960):
    """One RGB frame of ``path`` at ``t`` seconds, ``width`` px wide."""
    cmd = [_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, float(t)):.3f}",
           "-i", path, "-frames:v", "1", "-an", "-vf",
           f"scale={width}:-2:flags=area", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    h = len(raw) // (width * 3)
    if h <= 0:
        raise ValueError("no frame there")
    return np.frombuffer(raw[:width * h * 3], np.uint8).reshape(h, width, 3)


def refine_eyes(entry, path, width=960):
    """The entry's first face re-measured at ``width`` px on its middle
    frame (the descriptor's 192 px sample puts an eye within ~1% of the
    height; a conform zooms that error 3-4x). Updates desc in place;
    returns True when refined."""
    d = entry.get("desc") or {}
    if not d.get("eyes"):
        return False
    try:
        found = yunet_faces(frame_at(path, entry["mid"], width))
    except Exception:  # noqa: BLE001
        return False
    if not found:
        return False
    (lx, ly), (rx, ry) = d["eyes"][0]
    m = ((lx + rx) / 2, (ly + ry) / 2)
    best = min(found, key=lambda f: (
        ((f["eyes"][0][0] + f["eyes"][1][0]) / 2 - m[0]) ** 2 +
        ((f["eyes"][0][1] + f["eyes"][1][1]) / 2 - m[1]) ** 2))
    d["eyes"] = [best["eyes"]] + list(d["eyes"][1:])
    d["faces"] = [best["box"]] + list((d.get("faces") or [])[1:])
    return True


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


def analyze_clip(path, max_s=600.0, start=0.0):
    """{format, duration, shots: [{t0, t1, mid, motion, desc}]} of a clip
    file (or a still image: one shot); with ``start``, of the ``max_s``
    window from there, in the file's own seconds."""
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
    gray, rgb = decode_once(path, max_s, start)
    dur = len(gray) / CUT_FPS
    cts = cuts(gray)
    sh = []
    # a long shot is described window by window (a 60 s macro reel holds
    # many pictures; one mid frame would see one of them)
    for a, b in shots_of(dur, cts):
        n = max(1, int(round((b - a) / WINDOW_S)))
        for k in range(n):
            sh.append((a + (b - a) * k / n, a + (b - a) * (k + 1) / n))
    out = []
    for a, b in sh:
        mid = (a + b) / 2.0
        k = min(len(rgb) - 1, max(0, int(round(mid * RGB_FPS))))
        d = describe(rgb[k]) if len(rgb) else {}
        g = gray[int(a * CUT_FPS):max(int(a * CUT_FPS) + 2,
                                      int(b * CUT_FPS))].astype(np.float32)
        motion = float(np.abs(np.diff(g, axis=0)).mean()) if len(g) > 1 \
            else 0.0
        out.append({"t0": round(a + start, 3), "t1": round(b + start, 3),
                    "mid": round(mid + start, 3), "motion": round(motion, 3),
                    "desc": d})
    doc = {"format": FORMAT, "duration": round(dur, 3),
           "cuts": [round(c + start, 3) for c in cts] if start else cts,
           "shots": out}
    if start:
        doc["window"] = [round(float(start), 3), round(start + dur, 3)]
    return doc


def _key(sha, params=None):
    import sidecars
    return sidecars.sidecar_key(KIND, sha, MODEL_ID, VERSION, params or {},
                                (0.0, 0.0))


def cached(sha, params=None):
    try:
        import sidecars
        blob = sidecars._store().get_bytes(
            sidecars.data_key(KIND, sha, _key(sha, params), "classic.json"))
        if not blob:
            return None
        d = json.loads(blob if isinstance(blob, str) else blob.decode())
        return d if d.get("format") == FORMAT else None
    except Exception:  # noqa: BLE001
        return None


def store(sha, doc, params=None):
    try:
        import sidecars
        st = sidecars._store()
        k = _key(sha, params)
        st.put_bytes(sidecars.data_key(KIND, sha, k, "classic.json"),
                     json.dumps(doc, separators=(",", ":")).encode(),
                     "application/json")
        st.put_bytes(sidecars.manifest_key(KIND, sha, k),
                     json.dumps({"key": k, "kind": KIND,
                                 "model_id": MODEL_ID,
                                 "model_version": str(VERSION),
                                 "params": params or {},
                                 "src_range": [0.0, 0.0],
                                 "format": FORMAT}).encode(),
                     "application/json")
        return True
    except Exception:  # noqa: BLE001
        return False


def clip_doc(path, sha=None, start=0.0, max_s=600.0):
    """The (cached) description of a clip, or of the ``max_s`` window
    from ``start`` (a short cut from deep inside a long podcast is
    described where it is, not over the file's first ten minutes)."""
    start = max(0.0, round(float(start or 0.0), 1))
    params = {"t0": start, "t1": round(start + float(max_s), 1)} \
        if start else None
    if sha:
        hit = cached(sha, params)
        if hit:
            return hit
    doc = analyze_clip(path, max_s, start)
    if sha:
        store(sha, doc, params)
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
