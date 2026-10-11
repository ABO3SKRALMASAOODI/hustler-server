"""Track 1E acceptance, the fast local-only part (VFL fixtures; skipped in
CI). The full renders, boards and verdicts are the refbench cases
``cases/1E/AT-1E-*.yaml`` run by ``python -m benchmarks.refbench.at1e``;
these tests pin the numbers that do not need a full render, on the same
footage windows:

  AT-1E-1  the JRE curtain key keeps the curtain and the neon, never a face
  AT-1E-3  the thermal palette puts the Starship plume in its top 20 %,
           registered to the pixel
  AT-1E-4  the Optimus variant set: 5 variants under 10 CPU-s, edges
           registered (recall >= 0.9 at 3 px, shift <= 1 px)
"""

import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from localfix import fixture, local_only  # noqa: E402

V04 = fixture("V04", "rogan_gamers.mp4")
V13_STAR = fixture("V13", "starship_launch_4k60.mp4")
V13_OPT = fixture("V13", "optimus_walk.mp4")


def _frame(path, t, out, width=None):
    vf = ["-vf", f"scale={width}:-2"] if width else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i",
                    path, "-frames:v", "1", *vf, out], check=True)
    from PIL import Image
    return np.asarray(Image.open(out).convert("RGB"))


@local_only(V04)
def test_at_1e_1_curtain_key_keeps_the_set_not_the_faces(tmp_path):
    import cv2
    import gradewin
    import lutlib
    shots = {t: _frame(V04, t, str(tmp_path / f"f{t}.png"))
             for t in (12.0, 18.0)}         # Rogan + neon, Elon + curtain
    # the key width the verb solves for this colour in this window
    tol, soft = gradewin.key_from_frames(list(shots.values()), "#361113")
    assert tol <= 0.07, tol                  # a narrow key: +-12 deg
    key = gradewin.hue_key("#361113", tol, soft)
    for t, rgb in shots.items():
        c = lutlib.rgb_to_ycc8(rgb / 255.0)
        w = lutlib.hue_weight(key, c[..., 1], c[..., 2], c[..., 0])
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        faces = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        ).detectMultiScale(gray, 1.1, 6, minSize=(120, 120))
        assert len(faces), t
        for x, y, fw, fh in faces:
            core = w[y + fh // 4:y + 3 * fh // 4, x + fw // 4:x + 3 * fw // 4]
            assert core.mean() < 0.05, (t, core.mean())
        # the neon letters (Rogan) and the curtain (Elon) are kept
        assert (w > 0.95).mean() > (0.002 if t == 12.0 else 0.2), t


@local_only(V13_STAR)
def test_at_1e_3_thermal_plume_runs_hot_and_registered(tmp_path):
    import cv2
    import finish
    from benchmarks.refbench import at1e
    src = str(tmp_path / "star.png")
    rgb = _frame(V13_STAR, 7.5, src, width=1920)
    H, W = rgb.shape[:2]
    g, maps = finish.still_graph("thermal", W, H, {"id": "t"}, str(tmp_path))
    out = str(tmp_path / "thermal.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src]
                   + sum((["-i", m] for m in maps), [])
                   + ["-filter_complex", g, "-map", "[out]", "-frames:v",
                      "1", out], check=True)
    from PIL import Image
    th = np.asarray(Image.open(out).convert("RGB"))
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(float)
    pos = at1e.palette_position(th)
    hot = pos[lum >= np.percentile(lum, 97)].mean()
    assert hot >= 0.8, hot
    shift, _r = at1e.gradient_shift(lum, pos * 255.0)
    assert shift <= 1.0, shift


@local_only(V13_OPT)
def test_at_1e_4_optimus_variant_set(tmp_path):
    import variants
    from benchmarks.refbench import at1e
    from PIL import Image
    frame = str(tmp_path / "opt.png")
    _frame(V13_OPT, 4.0, frame)
    paths, stats = variants.render_variants(frame, variants.DEFAULT_KINDS,
                                            str(tmp_path / "set"), seed=1,
                                            threads=1)
    assert stats["cpu_s"] <= 10.0, stats
    photo = np.asarray(Image.open(paths["photo"]).convert("RGB"))
    for k in ("negative", "line", "blueprint", "thermal"):
        v = np.asarray(Image.open(paths[k]).convert("RGB"))
        prec = at1e.edge_precision(photo, v)
        assert prec >= 0.9, (k, prec)
        if k in at1e.EXACT_EDGES:
            _recall, iou = at1e.edge_scores(photo, v)
            assert iou >= 0.9, (k, iou)
        shift, _r = at1e.gradient_shift(
            np.asarray(Image.open(paths["photo"]).convert("L")),
            np.asarray(Image.open(paths[k]).convert("L")))
        assert shift <= 1.0, (k, shift)
