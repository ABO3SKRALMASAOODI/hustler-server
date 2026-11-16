"""The mark's two inks (owner, 16 Nov 2026: "when the video is white it appears in black"): the same drawing in the same
place, white over most footage and its dark twin while the picture under it is bright. Never a flicker: hysteresis, a
minimum hold and a short cross-fade (brandmark.ink_spans / ink_parts); the customer's export gets the same through
renderer.adaptive_ink_pass, the workflow films through the shell's brand finish."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brandmark  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None and not os.path.exists(FF), reason="needs ffmpeg")
W, H, FPS = 360, 640, 30


def test_the_dark_twin_is_the_same_drawing_in_dark_ink():
    light, dark = np.asarray(brandmark.render(W, H)), np.asarray(brandmark.render(W, H, dark=True))
    assert light.shape == dark.shape
    core = (light[..., 3] == 255) & (light[..., :3].min(axis=2) > 240)        # the white type's cores
    assert core.sum() > 50
    assert (dark[core][:, 3] >= 230).all() and np.percentile(dark[core][:, :3], 95) < 60   # the same pixels, inked dark


def test_the_ink_turns_dark_only_for_a_bright_stretch_that_holds():
    lv = [0.2] * 60 + [0.95] * 20 + [0.2] * 60          # a 0.7 s white flash: the ink holds white
    assert brandmark.ink_spans(lv, FPS) == []
    lv = [0.2] * 60 + [0.95] * 120 + [0.62] * 20 + [0.95] * 60 + [0.2] * 60
    sp = brandmark.ink_spans(lv, FPS)
    assert len(sp) == 1, sp                              # the dip inside the band (hysteresis) keeps the dark ink
    a, b = sp[0]
    assert abs(a - 2.0) < 0.3 and abs(b - 8.67) < 0.3, sp
    flick = [0.95 if (i // 10) % 2 else 0.2 for i in range(300)]       # bright and dark every third of a second
    sp = brandmark.ink_spans(flick, FPS)
    assert all(q - p >= brandmark.INK_HOLD_S - 1e-6 for p, q in sp), sp  # never a flicker
    assert brandmark.ink_spans([0.9] * 90, FPS) == [(0.0, 3.0)]


def test_without_a_bright_stretch_the_mark_is_the_one_overlay_it_always_was():
    assert brandmark.ink_parts("pv", "vm", 1, None, 10, 20, []) == [
        "[1:v]format=rgba[wmbot]", "[pv][wmbot]overlay=10:20:format=auto:shortest=1[vm]"]


def _clip(path, colours, seconds=2.0):
    """A clip of solid colours (0-255 grey), `seconds` each."""
    src = ";".join(f"color=c=0x{c:02x}{c:02x}{c:02x}:s={W}x{H}:r={FPS}:d={seconds}[c{i}]" for i, c in enumerate(colours))
    cat = "".join(f"[c{i}]" for i in range(len(colours))) + f"concat=n={len(colours)}:v=1:a=0,format=yuv420p[v]"
    subprocess.run([FF, "-v", "error", "-y", "-filter_complex", src + ";" + cat, "-map", "[v]", "-c:v", "libx264",
                    "-crf", "12", path], check=True)


def _cores(path, t, g, light):
    """Mean luma of the white type's cores at t."""
    r = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    fr = np.frombuffer(r.stdout, np.uint8)[:W * H].reshape(H, W)
    x, y = g["image"][0], g["image"][1]
    core = (light[..., 3] == 255) & (light[..., :3].min(axis=2) > 240)
    ys, xs = np.nonzero(core)
    return float(fr[ys + y, xs + x].mean())


@needs_ffmpeg
def test_a_white_picture_gets_the_dark_ink_and_a_dark_one_the_white(tmp_path):
    import renderer
    prog = str(tmp_path / "p.mp4")
    _clip(prog, [20, 250, 20], 2.5)
    g = renderer.watermark_geometry(W, H)
    lv = brandmark.ink_levels(prog, W, H, FPS, ff=FF, geometry=g)
    sp = brandmark.ink_spans(lv, FPS)
    assert len(sp) == 1 and abs(sp[0][0] - 2.5) < 0.3 and abs(sp[0][1] - 5.0) < 0.3, sp
    light = np.asarray(brandmark.render(W, H))
    x, y = g["image"][:2]
    out = str(tmp_path / "m.mp4")
    parts = brandmark.ink_parts("0:v", "vout", 1, 2, x, y, sp)
    subprocess.run([FF, "-v", "error", "-y", "-i", prog, "-loop", "1", "-r", str(FPS), "-i", brandmark.image_path(H, str(tmp_path)),
                    "-loop", "1", "-r", str(FPS), "-i", brandmark.image_path(H, str(tmp_path), dark=True),
                    "-filter_complex", ";".join(parts), "-map", "[vout]", "-c:v", "libx264", "-crf", "12", out], check=True)
    assert _cores(out, 1.2, g, light) > 200              # white ink on the dark picture
    assert _cores(out, 3.8, g, light) < 60               # dark ink on the white picture
    assert _cores(out, 6.3, g, light) > 200              # and white again


@needs_ffmpeg
def test_a_finished_export_reads_the_picture_under_its_mark_and_turns_dark_on_white(tmp_path):
    """renderer.adaptive_ink_pass: the export already carries the white mark; the picture under it is read back
    through the mark and the dark twin is laid over the bright stretch only."""
    import renderer
    prog = str(tmp_path / "p.mp4")
    _clip(prog, [30, 245], 3.0)
    g = renderer.watermark_geometry(W, H)
    x, y = g["image"][:2]
    marked = str(tmp_path / "marked.mp4")
    subprocess.run([FF, "-v", "error", "-y", "-i", prog, "-loop", "1", "-r", str(FPS), "-i", brandmark.image_path(H, str(tmp_path)),
                    "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:shortest=1,format=yuv420p[v]", "-map", "[v]",
                    "-c:v", "libx264", "-crf", "12", marked], check=True)
    lv = brandmark.ink_levels(marked, W, H, FPS, ff=FF, geometry=g, under=np.asarray(brandmark.render(W, H)))
    assert abs(np.median(lv[:80]) - 30 / 255) < 0.08 and abs(np.median(lv[100:]) - 245 / 255) < 0.08
    if shutil.which("ffmpeg") is None:
        pytest.skip("adaptive_ink_pass calls ffmpeg by name")
    sp = renderer.adaptive_ink_pass(marked, str(tmp_path), W, H, FPS, 6.0)
    assert len(sp) == 1 and abs(sp[0][0] - 3.0) < 0.3, sp
    light = np.asarray(brandmark.render(W, H))
    assert _cores(marked, 1.5, g, light) > 200 and _cores(marked, 5.0, g, light) < 70
