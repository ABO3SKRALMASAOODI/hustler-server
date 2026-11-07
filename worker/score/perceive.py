"""The footage measured, signal only, on any worker. No model call, no word list, no Apple Vision.

Writes <work>/perception.json in the shape the compiler and the Brief read:
  samples   every step seconds: face, luma, clipping, sharpness, change
  shots     per index shot: framing, camera drift, exposure, sharpness, noise, free regions
  text      text-like regions that stay put, from the index's spatial track
  palette   dominant colours and the accents really in the frame
Faces and text regions come from the index (spatial.py). There is no body or pose track on this
path, so `body` stays None and the gestures that ride one are not offered.
"""
import json
import os
import subprocess

import cv2
import numpy as np

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
MAX_SAMPLES = 120
MAX_W = 540


def step_for(dur):
    return max(0.5, dur / MAX_SAMPLES)


def frames(src, step, w, h):
    """Yield (t, BGR frame) at one frame per step, decoded once, in order."""
    cmd = [FF, "-nostdin", "-v", "error", "-threads", "2", "-i", src, "-vf", f"fps=1/{step:.4f},scale={w}:{h}:flags=area",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    i = 0
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield round((i + 0.5) * step, 3), np.frombuffer(b, np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def stats(gray):
    h, w = gray.shape
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    blur = cv2.GaussianBlur(gray, (0, 0), 1.2)
    # noise: the quietest quarter of the frame's high-pass energy, so edges don't count
    hp = np.abs(gray.astype(np.float32) - blur.astype(np.float32))
    cells = hp[: h - h % 16, : w - w % 16].reshape(h // 16, 16, w // 16, 16).mean(axis=(1, 3))
    return dict(luma=round(float(gray.mean()) / 255, 3), hi=round(float((gray >= 250).mean()), 3), lo=round(float((gray <= 6).mean()), 3),
                sharp=round(float(lap.var()), 1), noise=round(float(np.percentile(cells, 25)), 2))


def hex_of(c):
    return "#%02X%02X%02X" % tuple(int(v) for v in c)


def palette(frames_bgr, k=6):
    px = np.vstack([cv2.resize(f, (96, 170), interpolation=cv2.INTER_AREA).reshape(-1, 3) for f in frames_bgr]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    cv2.setRNGSeed(7)
    _, lab, cen = cv2.kmeans(px, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    share = np.bincount(lab.ravel(), minlength=k) / len(lab)
    order = np.argsort(-share)
    main = [dict(hex=hex_of(cen[i][::-1]), share=round(float(share[i]), 3)) for i in order]
    # accents: saturated colours, by hue family, smallest things included
    full = np.vstack([f.reshape(-1, 3) for f in frames_bgr])
    hsv = cv2.cvtColor(full.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sat = (hsv[:, 1] > 110) & (hsv[:, 2] > 70)
    accents = []
    if sat.sum() >= 12:
        fam = hsv[sat, 0].astype(np.int32) // 15
        for b in np.argsort(-np.bincount(fam, minlength=13)):
            n = int((fam == b).sum())
            if n / len(full) < 0.0004:
                break
            pick = full[sat][fam == b]
            hot = pick[np.argsort(-cv2.cvtColor(pick.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)[:, 1])[: max(8, n // 3)]]
            accents.append(dict(hex=hex_of(np.median(hot, axis=0)[::-1]), share=round(n / len(full), 4)))
    return main, accents[:4]


def free_regions(frames_bgr, subject_boxes):
    """Cells that stay calm in every sampled frame and never hold the subject: greedy largest rectangles."""
    H, W = 32, 18
    edge = np.zeros((H, W), np.float32)
    for f in frames_bgr:
        g = cv2.cvtColor(cv2.resize(f, (W * 8, H * 8), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        e = np.abs(cv2.Laplacian(g, cv2.CV_32F))
        edge = np.maximum(edge, cv2.resize(e, (W, H), interpolation=cv2.INTER_AREA))
    busy = edge > max(3.0, float(np.percentile(edge, 55)))
    for b in subject_boxes:
        x0, y0, x1, y1 = int(b[0] * W), int(b[1] * H), int(np.ceil(b[2] * W)), int(np.ceil(b[3] * H))
        busy[max(0, y0):y1, max(0, x0):x1] = True
    out = []
    grid = ~busy
    for _ in range(3):
        best, area = None, 0
        heights = np.zeros(W, int)
        for y in range(H):
            heights = (heights + 1) * grid[y]
            for x0 in range(W):
                m = H + 1
                for x1 in range(x0, W):
                    m = min(m, heights[x1])
                    a = m * (x1 - x0 + 1)
                    if a > area and m >= 3 and x1 - x0 >= 4:
                        area, best = a, (x0, y - m + 1, x1 + 1, y + 1)
        if not best:
            break
        out.append([round(best[0] / W, 2), round(best[1] / H, 2), round(best[2] / W, 2), round(best[3] / H, 2)])
        grid[best[1]:best[3], best[0]:best[2]] = False
    return out


def audio_facts(src, has_audio):
    """Loudness over time and sharp onsets. Wordless films are anchored on these."""
    if not has_audio:
        return dict(note="no audio stream")
    p = subprocess.run([FF, "-nostdin", "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, timeout=120)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if not len(x):
        return dict(note="audio stream decodes empty")
    win = 800                                    # 50 ms
    n = len(x) // win
    env = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1)) + 1e-6
    db = 20 * np.log10(env)
    per = 10                                     # 0.5 s bins
    bins = [round(float(db[i: i + per].mean()), 1) for i in range(0, n, per)]
    flux = np.maximum(np.diff(db), 0)
    thr = max(6.0, float(flux.mean() + 3 * flux.std()))
    onsets = []
    for i in np.argsort(-flux):
        if flux[i] < thr or len(onsets) >= 24:
            break
        t = (i + 1) * 0.05
        if all(abs(t - o) > 0.25 for o in onsets):
            onsets.append(round(float(t), 2))
    dyn = float(np.percentile(db, 95) - np.percentile(db, 10))
    return dict(step=0.5, rms_db=bins, onsets=sorted(onsets), mean_db=round(float(db.mean()), 1), range_db=round(dyn, 1),
                note=f"mean {db.mean():.0f} dB, range {dyn:.0f} dB, {len(onsets)} onsets")


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def text_clusters(spatial, limit=6):
    """Text-like regions that stay where they are. A region seen once is usually a pattern or a
    hand, and keeping type off it would push titles around for nothing."""
    rows = (spatial or {}).get("samples") or []
    found = []
    for r in rows:
        for b in r.get("text") or []:
            hit = next((c for c in found if iou(c["box"], b) >= 0.35), None)
            if hit is None:
                found.append(dict(box=list(b), t=[r["t"], r["t"]], seen=1))
            else:
                hit["seen"] += 1
                hit["t"][1] = max(hit["t"][1], r["t"])
                hit["box"] = [min(hit["box"][0], b[0]), min(hit["box"][1], b[1]), max(hit["box"][2], b[2]), max(hit["box"][3], b[3])]
    need = max(2, int(0.3 * len(rows)))
    keep = [c for c in found if c["seen"] >= need and (c["box"][2] - c["box"][0]) * (c["box"][3] - c["box"][1]) < 0.25]
    keep.sort(key=lambda c: -c["seen"])
    return [dict(read="", box=[round(v, 4) for v in c["box"]], t=c["t"], seen=c["seen"]) for c in keep[:limit]]


def face_at(spatial_rows, t):
    if not spatial_rows:
        return None
    r = min(spatial_rows, key=lambda q: abs(q["t"] - t))
    if abs(r["t"] - t) > 1.5 or not r.get("faces"):
        return None
    return max(r["faces"], key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))


def run(index, src, work):
    vid = index.get("video") or {}
    dur, W, H = float(vid.get("duration") or 0), int(vid.get("width") or 0), int(vid.get("height") or 0)
    if dur <= 0 or W <= 0 or H <= 0:
        raise ValueError("the index holds no video facts")
    step = step_for(dur)
    w = min(W, MAX_W) // 2 * 2
    h = int(round(H * w / W)) // 2 * 2
    rows = (index.get("spatial") or {}).get("samples") or []
    samples, keep, prev = [], [], None
    for t, f in frames(src, step, w, h):
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        st = stats(gray)
        face = face_at(rows, t)
        diff = None if prev is None else round(float(np.abs(gray.astype(np.int16) - prev).mean()), 2)
        prev = gray.astype(np.int16)
        samples.append(dict(t=t, face=[round(float(v), 4) for v in face] if face else None, body=None, diff=diff, persons=1 if face else 0, **st))
        keep.append((t, f))
    if not samples:
        raise ValueError("no frames could be read from the footage")
    spaced = keep[:: max(1, len(keep) // 24)]
    shots = []
    for sh in index.get("shots") or [dict(id=1, start=0, end=dur)]:
        ss = [s for s in samples if sh["start"] <= s["t"] < sh["end"]]
        fr = [f for t, f in keep if sh["start"] <= t < sh["end"]]
        if not ss or not fr:
            near = min(range(len(samples)), key=lambda i: abs(samples[i]["t"] - sh["start"]))
            ss, fr = ss or [samples[near]], fr or [keep[near][1]]
        faces = [s["face"] for s in ss if s["face"]]
        ph = []
        sub = fr[:: max(1, len(fr) // 8)]
        for a, b in zip(sub, sub[1:]):
            (dx, dy), _ = cv2.phaseCorrelate(np.float32(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)), np.float32(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)))
            ph.append((abs(dx) / w, abs(dy) / h))
        drift = round(float(np.median([p[0] + p[1] for p in ph])) / 2.0, 4) if ph else 0.0

        def med(key, q=ss):
            return round(float(np.median([s[key] for s in q])), 3)
        shots.append(dict(id=sh["id"], t=[sh["start"], sh["end"]], faces_seen=round(len(faces) / len(ss), 2), bodies_seen=0.0, persons=1 if faces else 0, drift_per_s=drift,
                          face_h=round(float(np.median([f[3] - f[1] for f in faces])), 3) if faces else None,
                          face_c=[round(float(np.median([(f[0] + f[2]) / 2 for f in faces])), 3), round(float(np.median([(f[1] + f[3]) / 2 for f in faces])), 3)] if faces else None,
                          body_h=None, luma=med("luma"), hi=med("hi"), lo=med("lo"), sharp=round(float(np.median([s["sharp"] for s in ss])), 1),
                          noise=round(float(np.median([s["noise"] for s in ss])), 2), change=round(float(np.median([s["diff"] for s in ss if s["diff"] is not None] or [0])), 2),
                          free=free_regions(fr[:: max(1, len(fr) // 6)], faces[:: max(1, len(faces) // 4)])))
    pal, accents = palette([f for _, f in spaced])
    res = dict(step=step, W=W, H=H, dur=dur, fps=vid.get("fps"), shots=shots, events=[], text=text_clusters(index.get("spatial")), palette=pal, accents=accents,
               audio=audio_facts(src, vid.get("has_audio", True)), samples=samples)
    with open(os.path.join(work, "perception.json"), "w") as f:
        json.dump(res, f, ensure_ascii=False)
    return res
