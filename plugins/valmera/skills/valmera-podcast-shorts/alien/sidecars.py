"""What the alien passes know about a film's picture, measured once on the picture itself.

The passes run in programme space: on the engine's own film of the Director's pictures (pass 1),
before any type is drawn. So nothing here maps source time to film time: a cut is a cut of the
plan, a face is where it is on the frame the viewer sees.

Signal only. No model reads a meaning here: YuNet gives the face and its five points, RVM the
person's matte, numpy the voice's level. Everything is written inside the run's supply folder
(``alien/``) and is deleted with the run.

    track.npy   per frame: face x, y, w, h, mouth x, y in frame fractions (nan = nobody)
    matte.npy   per frame, uint8, GW x GH: the person
    level.npy   per frame: the sound's level, 0..1 (p95 of speech = 1)
"""
import json
import os
import subprocess

import cv2
import numpy as np

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")
MODELS = f"{VV}/models"
FPS = 30
GW, GH = 270, 480            # the geometry grid: light is low frequency


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_read_packets:format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    n, den = s["r_frame_rate"].split("/")
    return dict(w=int(s["width"]), h=int(s["height"]), fps=float(n) / float(den), frames=int(s.get("nb_read_packets") or 0),
                dur=float(d["format"]["duration"]))


def frames_of(path, w, h, pix="bgr24", crop=None):
    """Yield the frames of a file at w x h, straight through one decoder, every frame once.
    crop = (cw, ch, x, y) reads only that window of each frame, unscaled."""
    vf = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}" if crop else f"scale={w}:{h}:flags=area"
    cmd = [FF, "-nostdin", "-v", "error", "-i", path, "-an", "-vf", vf, "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", pix, "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = w * h * 3
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def shot_bounds(cuts, n):
    edges = sorted({0, n, *[int(c) for c in cuts if 0 < int(c) < n]})
    return list(zip(edges, edges[1:]))


# ----------------------------------------------------------------- faces

def faces(video, n, step=2):
    """YuNet on every ``step``-th frame at 540 wide. Per frame: rows of [x, y, w, h, mouth x, mouth y, score], fractions."""
    dw, dh = 540, 960
    det = cv2.FaceDetectorYN.create(f"{MODELS}/face_detection_yunet_2023mar.onnx", "", (dw, dh), 0.6, 0.3, 50)
    out = [None] * n
    for i, fr in enumerate(frames_of(video, dw, dh)):
        if i >= n:
            break
        if i % step:
            continue
        _, fs = det.detect(fr)
        rows = []
        for f in (fs if fs is not None else []):
            mx, my = (f[10] + f[12]) / 2 / dw, (f[11] + f[13]) / 2 / dh
            rows.append([f[0] / dw, f[1] / dh, f[2] / dw, f[3] / dh, mx, my, float(f[14])])
        out[i] = rows
    return out


def track(det, cuts, n):
    """Per frame [face x, y, w, h, mouth x, y]: the largest face of each shot, gaps filled along the shot and
    smoothed, so a light pinned to the mouth does not shake with the detector. A shot nobody is in stays nan."""
    T = np.full((n, 6), np.nan, np.float32)
    for a, b in shot_bounds(cuts, n):
        seen = [i for i in range(a, b) if det[i]]
        if len(seen) < max(2, (b - a) // 8):
            continue
        for i in seen:
            r = max(det[i], key=lambda r: r[2] * r[3])
            T[i] = r[:6]
        seg = T[a:b]
        ok = ~np.isnan(seg[:, 0])
        idx = np.arange(b - a)
        k = min(9, max(1, (ok.sum() // 2) * 2 - 1))
        win = np.hanning(k + 2)[1:-1]
        win /= win.sum()
        for c in range(6):
            col = np.interp(idx, idx[ok], seg[ok, c])
            seg[:, c] = np.convolve(np.pad(col, k // 2, mode="edge"), win, mode="valid")
    return T


# ----------------------------------------------------------------- matte (RVM)

def matte(video, cuts, n, out_path, threads=4):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(f"{MODELS}/rvm_mobilenetv3_fp32.onnx", so, providers=["CPUExecutionProvider"])
    mw, mh = 2 * GW, 2 * GH
    M = np.lib.format.open_memmap(out_path + ".part.npy", mode="w+", dtype=np.uint8, shape=(n, GH, GW))
    starts = {a for a, _ in shot_bounds(cuts, n)}
    rec = None
    ds = np.array([0.45], np.float32)                     # a person fills a podcast frame: 243 x 432 inside is enough for light
    for i, fr in enumerate(frames_of(video, mw, mh, pix="rgb24")):
        if i >= n:
            break
        if i in starts or rec is None:                       # a cut is another room: the recurrent state starts over
            rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
        x = (fr.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        _, pha, *rec = sess.run(None, {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": ds})
        M[i] = np.clip(cv2.resize(pha[0, 0], (GW, GH), interpolation=cv2.INTER_AREA) * 255.0, 0, 255).astype(np.uint8)
    M.flush()
    del M
    os.replace(out_path + ".part.npy", out_path)
    return out_path


# ----------------------------------------------------------------- the sound's level and the lamp's envelope

def level(media, n):
    """Level per frame from the film's own mixed sound: RMS over a frame and a half, as a fraction of the p95 of speech."""
    sr = 24000
    r = subprocess.run([FF, "-nostdin", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    hop = sr // FPS
    win = int(hop * 1.5)
    lev = np.zeros(n, np.float32)
    for i in range(n):
        c = i * hop + hop // 2
        seg = x[max(0, c - win // 2): c + win // 2]
        if len(seg):
            lev[i] = float(np.sqrt(np.mean(seg * seg)))
    loud = lev[lev > lev.max() * 0.05]
    ref = float(np.percentile(loud, 95)) if len(loud) else 1.0
    return np.clip(lev / max(ref, 1e-6), 0, 1.5)


def envelope(lev, attack_f=1.5, release_f=7.0, gamma=0.6, floor=0.06):
    """A lamp that follows the voice: up in a frame or two, down over a quarter second, dark in a real pause.
    Phrases, not syllables: the release keeps it from strobing."""
    e = np.clip((lev - floor) / (1.0 - floor), 0, 1.25) ** gamma
    out = np.zeros_like(e)
    v = 0.0
    ka, kr = 1.0 - np.exp(-1.0 / attack_f), 1.0 - np.exp(-1.0 / release_f)
    for i, t in enumerate(e):
        v += (t - v) * (ka if t > v else kr)
        out[i] = v
    lead = np.concatenate([out[1:], out[-1:]])            # the light leads the sound by a frame, as a lip does
    return np.maximum(out, 0.6 * lead)


def flashes_per_second(lum):
    """The health rule's count on a luminance series (0..1 per frame): the most pairs of opposing changes of a
    tenth of full scale or more, in any one second."""
    ev, last, direction = [], float(lum[0]), 0
    for i in range(1, len(lum)):
        d = float(lum[i]) - last
        if abs(d) >= 0.10:
            s = 1 if d > 0 else -1
            if direction and s != direction:
                ev.append(i)
            direction, last = s, float(lum[i])
        elif (d > 0) == (direction > 0) and direction:
            last = float(lum[i])
    worst = 0
    for i in ev:
        worst = max(worst, sum(1 for j in ev if i <= j < i + FPS))
    return worst / 2.0


def gray_frames(video, wanted, w=135, h=240):
    """{frame: relative luminance, h x w float32} for the frames asked for, in one decode."""
    wanted = set(int(f) for f in wanted)
    out = {}
    if not wanted:
        return out
    last = max(wanted)
    for i, fr in enumerate(frames_of(video, w, h)):
        if i in wanted:
            out[i] = ((fr.astype(np.float32) / 255.0) ** 2.2) @ np.array([0.0722, 0.7152, 0.2126], np.float32)
        if i >= last:
            break
    return out
