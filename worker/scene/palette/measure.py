"""What a rendered film did, measured by signal, set beside the bar (the @metromedia.house reels and the owner's 41
references). A record only: nothing here vetoes, repairs or re-renders anything.

    python -m scene.palette.measure --film FILM.mp4 [--l1 L1.json] [--log devices_log.json] [--ledger devices.jsonl] --out OUT.json

Signal, on the exported pixels (decoded small, every frame):
    cuts_10s         new pictures a 10 s (a frame that differs from the last by more than a hard cut does)
    short_shots      share of shots that last 1-2 frames
    flips_10s        polarity flips a 10 s (a frame much nearer the inverse of its neighbour than the neighbour itself)
    white_10s        white frames a 10 s; black_10s: black frames a 10 s
    contour_3s       cuts in each 3 s window: p10, median, p90 (strobe against hold)
    first_device_f   the first frame a laid device is on screen (from the log), and the first flip, white or black frame
    moves_10s        camera moves a 10 s, as the judge's teardown measures them: DIS optical flow on every frame at 192 px,
                     a similarity fit a frame (zoom and pan), summed over each shot; a move is 2% or more of zoom or pan
                     inside one shot with a rigid fit (half the flow or more on it). zoom_s: the median move's size a second.
From the scene: layers (share of frames with three or more drawn layers), display shots, the eased moves, punches and
whips the pace engine planned (eased_10s: planned, not seen; the slow pushes it gives every picture are not in it).
From the log: asks, landed, fell back, and the device sequence's edit distance to the recent films in the ledger.
"""
import argparse
import json
import os
import subprocess

import numpy as np

# the bar: medians the creativity plan measured on the metromedia reels (MM) and the owner's 41 references (refs41)
MM = {"cuts_10s": 16.1, "flips_10s": 0.74, "white_10s": 0.8, "black_10s": 0.25, "moves_10s": 4.3, "display_share": 0.17,
      "three_layer_share": 0.28, "short_shots": 0.086}
REFS41 = {"cuts_10s": 15.2, "short_shots": 0.43, "contour_3s": [1, 12]}


def ff():
    from .. import reader
    return os.environ.get("FFMPEG_BIN", reader.FF)


def frames(path, w=72, h=128):
    """Every frame of a film, small, as an (n, h, w, 3) uint8 array, and its rate."""
    from .. import reader
    info = reader.probe(path)
    fps = float(info.get("fps") or 30.0)
    p = subprocess.run([ff(), "-nostdin", "-v", "error", "-i", path, "-an", "-vf", f"scale={w}:{h}:flags=area", "-pix_fmt", "rgb24",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    a = np.frombuffer(p.stdout, np.uint8)
    n = a.size // (w * h * 3)
    return a[:n * w * h * 3].reshape(n, h, w, 3), fps


def signal(arr, fps):
    n = len(arr)
    if n < 3:
        return {}
    g = arr.astype(np.float32).mean(axis=3)
    luma = g.reshape(n, -1).mean(axis=1)
    d = np.abs(g[1:] - g[:-1]).reshape(n - 1, -1).mean(axis=1)
    flips = []                             # a frame whose picture is the inverse of the last one (structure, not light)
    flat = g.reshape(n, -1)
    z = flat - flat.mean(axis=1, keepdims=True)
    sd = np.sqrt((z * z).mean(axis=1)) + 1e-6
    for i in range(n - 1):
        corr = float((z[i] * z[i + 1]).mean() / (sd[i] * sd[i + 1]))
        if corr < -0.45 and sd[i] > 18 and sd[i + 1] > 18 and not (flips and i + 1 - flips[-1] <= 3):
            flips.append(i + 1)
    white = [i for i in range(n) if luma[i] > 232 and g[i].std() < 18]
    black = [i for i in range(n) if luma[i] < 10 and g[i].std() < 6]
    cut = [i + 1 for i in range(n - 1) if d[i] > 28]
    shots = np.diff([0] + cut + [n])
    secs = n / fps
    w3 = int(round(3 * fps))
    cs = np.zeros(n, np.int32)
    cs[[c for c in cut if c < n]] = 1
    win = [int(cs[k:k + w3].sum()) for k in range(0, max(n - w3, 1), max(w3 // 2, 1))]
    return {"seconds": round(secs, 2), "cuts_10s": round(len(cut) / secs * 10, 2),
            "short_shots": round(float((shots <= 2).mean()), 3) if len(shots) else 0.0,
            "flips_10s": round(len(flips) / secs * 10, 2), "white_10s": round(len(white) / secs * 10, 2),
            "black_10s": round(len(black) / secs * 10, 2),
            "contour_3s": [int(np.percentile(win, 10)), int(np.median(win)), int(np.percentile(win, 90))] if win else [0, 0, 0],
            "first_punct_f": min(flips + white + black) if (flips or white or black) else None,
            "flips_f": flips[:40], "white_f": white[:40], "black_f": black[:40]}


def camera(path, shots_at=None):
    """Camera moves inside shots, by signal on the exported pixels (the judge's teardown): {"moves_10s", "moving_share",
    "zoom_s"}. ``shots_at``: the shot edges in frames (else cut where a frame differs from the last by a hard cut)."""
    import cv2
    from .. import reader
    info = reader.probe(path)
    fps = float(info.get("fps") or 30.0)
    W0, H0 = int(info.get("w") or 1080), int(info.get("h") or 1920)
    k = 192.0 / max(W0, H0)
    w, h = max(32, int(round(W0 * k / 2)) * 2), max(32, int(round(H0 * k / 2)) * 2)
    p = subprocess.run([ff(), "-nostdin", "-v", "error", "-i", path, "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    a = np.frombuffer(p.stdout, np.uint8)
    g = a[:a.size // (w * h) * w * h].reshape(-1, h, w)
    n = len(g)
    if n < 3:
        return {}
    if shots_at is None:
        sm = np.stack([cv2.resize(x, (72, 128) if h > w else (128, 72), interpolation=cv2.INTER_AREA) for x in g]).astype(np.float32)
        d = np.abs(sm[1:] - sm[:-1]).reshape(n - 1, -1).mean(axis=1)
        shots_at = [i + 1 for i in range(n - 1) if d[i] > 28]
    edges = [0] + [int(c) for c in shots_at if 0 < c < n] + [n]
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    ys, xs = np.mgrid[4:h - 4:6, 4:w - 4:6]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    moved, sizes, counted = 0, [], 0
    for s0, s1 in zip(edges, edges[1:]):
        if s1 - s0 < 3:
            continue
        counted += 1
        lz, px, py, rig = 0.0, 0.0, 0.0, []
        for i in range(s0 + 1, s1):
            v = dis.calc(g[i - 1], g[i], None)[pts[:, 1].astype(int), pts[:, 0].astype(int)]
            if np.abs(v).mean() < 0.02:
                rig.append(1.0)
                continue
            M, inl = cv2.estimateAffinePartial2D(pts, pts + v, method=cv2.RANSAC, ransacReprojThreshold=0.6)
            if M is None:
                continue
            lz += float(np.log(max(float(np.hypot(M[0, 0], M[1, 0])), 1e-3)))
            px += float(M[0, 2] + (M[0, 0] - 1) * w / 2 + M[0, 1] * h / 2) / w
            py += float(M[1, 2] + M[1, 0] * w / 2 + (M[1, 1] - 1) * h / 2) / w
            rig.append(float(inl.mean()) if inl is not None else 0.0)
        z, pan = float(np.expm1(abs(lz))), float(np.hypot(px, py))
        if (z >= 0.02 or pan >= 0.02) and rig and float(np.median(rig)) >= 0.5:
            moved += 1
            sizes.append(max(z, pan) / ((s1 - s0) / fps))
    secs = n / fps
    return {"moves_10s": round(moved / secs * 10, 2), "moving_share": round(moved / max(counted, 1), 3),
            "zoom_s": round(float(np.median(sizes)), 3) if sizes else 0.0}


def scene_side(l1):
    """Layers, display and moves as the scene declares them."""
    dur = int(l1.get("dur_f") or 1)
    layers = np.zeros(dur, np.int32)
    disp = np.zeros(dur, bool)
    for it in l1.get("items") or []:
        a, b = int(it["f"][0]), int(it["f"][1])
        if it.get("k") in ("shot", "card") or (it.get("k") == "word" and it.get("role") in ("big", "stat")):
            layers[a:b] += 1
        if it.get("k") == "word" and it.get("role") in ("big", "stat"):
            disp[a:b] = True
        if it.get("k") == "flash" and not it.get("rect"):
            layers[a:b] += 1
    pace = l1.get("pace") or {}
    secs = dur / float(l1.get("fps") or 30)
    moves = int(pace.get("eased") or 0) + int(pace.get("punches") or 0) + int(pace.get("whips") or 0)
    return {"three_layer_share": round(float((layers >= 3).mean()), 3), "display_share": round(float(disp.mean()), 3),
            "eased_10s": round(moves / max(secs, 1e-6) * 10, 2)}


def edit_distance(a, b):
    m, n = len(a), len(b)
    row = list(range(n + 1))
    for i in range(1, m + 1):
        prev, row[0] = row[0], i
        for j in range(1, n + 1):
            cur = min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev, row[j] = row[j], cur
    return row[n]


def log_side(log, ledger=None, n=12):
    done = [r for r in log or [] if r.get("done")]
    seq = [r.get("landed") or r.get("dev") for r in sorted(done, key=lambda r: (r.get("f") or [0])[0])]
    out = {"asks": len(log or []), "landed": len(done), "fell_back": sum(1 for r in done if r.get("fallback")),
           "kinds": sorted(set(seq)), "sequence": seq,
           "first_device_f": min(((r.get("f") or [10 ** 9])[0] for r in done), default=None)}
    if ledger and os.path.exists(ledger):
        rows = []
        for line in open(ledger, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        dist = []
        for r in rows[-n:]:
            other = [s.split("@")[0] for s in r.get("seq") or []]
            if other:
                dist.append(round(edit_distance(seq, other) / max(len(seq), len(other), 1), 3))
        out["ledger_distance"] = {"min": min(dist), "median": float(np.median(dist))} if dist else None
    return out


def measure(film, l1=None, log=None, ledger=None):
    arr, fps = frames(film)
    out = {"film": film, "signal": signal(arr, fps)}
    try:
        out["signal"].update(camera(film))
    except Exception as e:      # noqa: BLE001 - a record only: the moves left unmeasured say why
        out["signal"]["camera_fault"] = repr(e)[:200]
    if l1:
        out["scene"] = scene_side(l1)
    if log is not None:
        out["devices"] = log_side(log, ledger)
    got = dict(out.get("signal") or {}, **(out.get("scene") or {}))
    out["against_mm"] = {k: dict(ours=got.get(k), mm=v) for k, v in MM.items() if k in got}
    out["against_refs41"] = {k: dict(ours=got.get(k), refs41=v) for k, v in REFS41.items() if k in got}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scene.palette.measure")
    ap.add_argument("--film", required=True)
    ap.add_argument("--l1")
    ap.add_argument("--log")
    ap.add_argument("--ledger")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    l1 = json.load(open(a.l1)) if a.l1 and os.path.exists(a.l1) else None
    log = json.load(open(a.log)) if a.log and os.path.exists(a.log) else None
    out = measure(a.film, l1, log, a.ledger)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    s = out.get("signal") or {}
    print(json.dumps({k: s.get(k) for k in ("cuts_10s", "short_shots", "flips_10s", "white_10s", "black_10s", "contour_3s", "moves_10s", "zoom_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
