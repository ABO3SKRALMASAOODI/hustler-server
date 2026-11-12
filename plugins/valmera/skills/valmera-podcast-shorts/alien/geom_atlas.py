#!/usr/bin/env python3
"""Depth of a film's picture. Runs under the atlas venv (torch); signal only.

    $ATLAS_PY geom_atlas.py depth <film.mp4> <alien dir> <worker dir> <l1.json>   depth.npy, depth_kf.npy

Depth is Depth Anything v2 Small (Apache-2.0, the worker's pinned checkpoint) on every second frame of
the film, stored near = 1 and normalised per shot, so a cut is another room.
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sidecars as S  # noqa: E402

CKPT = f"{S.MODELS}/depth/depth_anything_v2_vits.pth"
STEP = 2
DW, DH = 270, 480


def depth(film, adir, worker, l1_path):
    import torch
    sys.path.insert(0, worker)
    from depth_anything.dpt import DepthAnythingV2
    torch.set_num_threads(4)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    net = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    net.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=True))
    net = net.to(dev).eval()
    l1 = json.load(open(l1_path))
    n = int(l1["dur_f"])
    cuts = sorted({int(it["f"][0]) for it in l1["items"] if it.get("k") == "shot"})
    bounds = S.shot_bounds(cuts, n)
    kfs, out = [], []
    for i, fr in enumerate(S.frames_of(film, 2 * DW, 2 * DH)):
        if i >= n:
            break
        if i % STEP and not any(i == b - 1 or i == a for a, b in bounds):
            continue
        img, _ = net.image2tensor(fr, 518)
        with torch.no_grad():
            d = net.forward(img.to(dev))
            d = torch.nn.functional.interpolate(d[:, None], (DH, DW), mode="bilinear", align_corners=True)[0, 0]
        out.append(d.float().cpu().numpy())
        kfs.append(i)
    D = np.stack(out)
    kfs = np.array(kfs, np.int32)
    for a, b in bounds:                                    # near = 1, far = 0, per shot
        m = (kfs >= a) & (kfs < b)
        if m.any():
            lo, hi = np.percentile(D[m], 1), np.percentile(D[m], 99)
            D[m] = np.clip((D[m] - lo) / max(hi - lo, 1e-6), 0, 1)
    np.save(os.path.join(adir, "depth.npy"), D.astype(np.float16))
    np.save(os.path.join(adir, "depth_kf.npy"), kfs)
    print("depth", len(kfs), "keyframes on", dev)


if __name__ == "__main__":
    depth(*sys.argv[2:6])
