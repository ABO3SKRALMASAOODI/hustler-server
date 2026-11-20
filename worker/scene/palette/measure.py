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
From the scene: layers (share of frames with three or more drawn layers), display shots, moves the pace engine made.
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
            "moves_10s": round(moves / max(secs, 1e-6) * 10, 2)}


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
    print(json.dumps({k: s.get(k) for k in ("cuts_10s", "short_shots", "flips_10s", "white_10s", "black_10s", "contour_3s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
