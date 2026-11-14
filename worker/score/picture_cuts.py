"""Picture changes on the fixed judge3/edit_delta pixel ruler.

The Director's source index can miss cuts (p5575 had two indexed shots for
four routine actions). Measure the actual proxy before its local brief is made.
This does not modify the stored project index or interpret any words.
"""
import copy
import os
import subprocess

import cv2
import numpy as np

FPS, MAX_FRAMES = 15.0, 5400


def boundaries(frames, fps):
    if len(frames) < 3:
        return [], []
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    hists = []
    for f in frames:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
        hists.append((h / (h.sum() + 1e-6)).ravel())
    hists = np.stack(hists)
    pixels = np.abs(gray[1:] - gray[:-1]).mean(axis=(1, 2)) / 64.0
    hist = np.abs(hists[1:] - hists[:-1]).sum(axis=1) / 2.0
    score = 0.5 * np.clip(pixels, 0, 1.5) + 0.5 * hist
    size = int(max(9, 1.2 * fps))
    # ndimage's nearest-edge median, including its upper median for an even
    # window. NumPy/OpenCV are on the production image; SciPy is not.
    padded = np.pad(score, (size // 2, (size - 1) // 2), mode='edge')
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    base = np.partition(windows, size // 2, axis=-1)[:, size // 2]
    hits = np.where((score > 0.22) & (score > 3.0 * base + 0.06))[0]
    cuts, flashes, i = [], [], 0
    while i < len(hits):
        k = hits[i]; j = i
        while j + 1 < len(hits) and hits[j + 1] - hits[j] <= 2:
            j += 1
        last = hits[j]
        if j > i and k >= 1 and last + 2 < len(frames):
            back = np.abs(gray[k] - gray[min(last + 1, len(frames) - 1)]).mean() / 64.0
            if back < 0.12:
                flashes.append(round((k + 1) / fps, 3)); i = j + 1
                continue
        cuts.append(round((last + 1) / fps, 3)); i = j + 1
    return cuts, flashes


def measure(path, video, timeout=12.0):
    dur = float(video['duration']); W, H = int(video['width']), int(video['height'])
    fps = min(FPS, MAX_FRAMES / max(dur, 1.0))
    scale = 96.0 / max(W, H)
    w, h = [max(16, int(round(n * scale / 2)) * 2) for n in (W, H)]
    cmd = [os.environ.get('FFMPEG_BIN', 'ffmpeg'), '-nostdin', '-v', 'error', '-threads', '2', '-i', str(path),
           '-map', '0:v:0', '-vf', f'fps={fps:.4f},scale={w}:{h}:flags=area',
           '-frames:v', str(MAX_FRAMES), '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=timeout).stdout
    n = len(raw) // (w * h * 3)
    if n < 3:
        raise ValueError('source cut measurement has fewer than three frames')
    frames = np.frombuffer(raw[:n*w*h*3], np.uint8).reshape(n, h, w, 3)
    cuts, flashes = boundaries(frames, fps)
    return dict(method='judge3_pixel_cuts', fps=fps, frames=n, cuts=cuts, flashes=flashes)


def refine(index, path):
    local = copy.deepcopy(index or {})
    try:
        rec = measure(path, local.get('video') or {})
        dur = float(local['video']['duration'])
        edges = [0.0] + [t for t in rec['cuts'] if 0 < t < dur] + [dur]
        local['shots'] = [dict(id=i+1, start=a, end=b, caption=None, thumb_key=None)
                          for i, (a, b) in enumerate(zip(edges, edges[1:]))]
        local['director_cut_measure'] = rec
    except Exception as exc:
        local['director_cut_measure'] = dict(unavailable=type(exc).__name__)
    return local
