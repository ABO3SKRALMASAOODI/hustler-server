"""Wave 3 (3E): video slots — real footage composited into the rects a page
declares, under the page's own pixels (slots.py; INTERFACES_W3.md §6).

Measured on synthetic clips (no Chromium): a transparent page with a white
frame line, a slots.json the engine would have written, and solid-colour
sources — every assertion is on exact pixels.
"""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import layouts                                            # noqa: E402
import motion_engine                                      # noqa: E402
import slots                                              # noqa: E402
import w3_support as w3s                                  # noqa: E402

W, H, FPS, N = 160, 120, 25.0, 10


def _colour_clip(path, colour, w=64, h=48, n=40):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s={w}x{h}:r={FPS}:d={n / FPS}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "1",
                    path], check=True)
    return path


def _page(path, rows, line_y=None):
    """A transparent qtrle page (optionally an opaque white line at
    ``line_y``), written by the compositor's own encoder, plus its
    slots.json."""
    import world_layer
    enc = world_layer._Encoder(path, W, H, FPS)
    try:
        for _k in range(N):
            f = np.zeros((H, W, 4), np.uint8)
            if line_y is not None:
                f[line_y:line_y + 2] = 255
            enc.write(f)
    finally:
        enc.close()
    with open(path + ".slots.json", "w") as f:
        json.dump({"scale": 1.0, "frames": rows}, f)
    return motion_engine.RenderedClip(path=path, x=0, y=0, w=W, h=H,
                                      frames=N, captured=N, cached=False,
                                      seconds=0.0)


class _Ctx:
    def __init__(self, tmp, assets, src_path=None):
        self.fps, self.workdir, self.src_path = FPS, str(tmp), src_path
        self.clock_offset = 0.0
        self.fetch_asset = lambda key: assets.get(key)


def test_two_tiles_fill_their_rects_under_the_page(tmp_path):
    red = _colour_clip(str(tmp_path / "r.mp4"), "red")
    blue = _colour_clip(str(tmp_path / "b.mp4"), "blue")
    row = [["t0", 10, 10, 60, 40, 0, 1, 1.0, "cover"],
           ["t1", 90, 10, 60, 40, 0, 2, 1.0, "cover"]]
    clip = _page(str(tmp_path / "page.mov"), [row] * N, line_y=30)
    item = {"id": "co1", "template": "mosaic",
            "params": {"tiles": ["asset:media/r.mp4", "asset:media/b.mp4"]}}
    assert slots.wants(item, clip)
    out = slots.fill(item, clip, _Ctx(tmp_path, {"media/r.mp4": red,
                                                 "media/b.mp4": blue}))
    fr = w3s.rgba_frames(out.path, W, H)
    assert len(fr) == N
    f = fr[5].astype(int)
    assert f[20, 40, 0] > 200 and f[20, 40, 2] < 60      # red tile
    assert f[20, 120, 2] > 200 and f[20, 120, 0] < 60    # blue tile
    assert f[20, 80, 3] == 0                              # gap: transparent
    assert f[31, 40, :3].min() > 240                      # page line on top
    assert f[70, 40, 3] == 0                              # outside: empty


def test_rounded_corners_and_z_order(tmp_path):
    red = _colour_clip(str(tmp_path / "r.mp4"), "red")
    blue = _colour_clip(str(tmp_path / "b.mp4"), "blue")
    row = [["t1", 30, 20, 80, 60, 0, 5, 1.0, "cover"],     # drawn last
           ["t0", 20, 10, 80, 60, 16, 1, 1.0, "cover"]]
    clip = _page(str(tmp_path / "page.mov"), [row] * N)
    item = {"id": "st1", "template": "stack",
            "params": {"panels": ["media/r.mp4", "media/b.mp4"]}}
    out = slots.fill(item, clip, _Ctx(tmp_path, {"media/r.mp4": red,
                                                 "media/b.mp4": blue}))
    f = w3s.rgba_frames(out.path, W, H)[3].astype(int)
    assert f[11, 21, 3] < 40                    # t0's rounded corner
    assert f[40, 60, 2] > 200                   # overlap: t1 (z 5) on top
    assert f[15, 50, 0] > 200                   # t0 where t1 does not reach


def test_an_unreadable_source_keeps_the_placeholder_and_says_so(tmp_path):
    row = [["t0", 10, 10, 60, 40, 0, 1, 1.0, "cover"]]
    clip = _page(str(tmp_path / "page.mov"), [row] * N)
    warned = []
    item = {"id": "co2", "template": "mosaic",
            "params": {"tiles": ["asset:media/gone.mp4"]}}
    out = slots.fill(item, clip, _Ctx(tmp_path, {}), warn=warned.append)
    assert out is None
    assert warned and "could not be filled" in warned[0]


def test_a_source_second_reads_the_main_footage(tmp_path):
    src = _colour_clip(str(tmp_path / "src.mp4"), "lime", n=100)
    row = [["screen", 20, 20, 100, 60, 0, 1, 1.0, "cover"]]
    clip = _page(str(tmp_path / "page.mov"), [row] * N)
    item = {"id": "ds1", "template": "device_stage",
            "params": {"screen": "source:1.0"}}
    out = slots.fill(item, clip, _Ctx(tmp_path, {}, src_path=src))
    f = w3s.rgba_frames(out.path, W, H)[2].astype(int)
    assert f[50, 70, 1] > 200 and f[50, 70, 0] < 80
    spill = slots.spill(dict(item, start=0.0, end=0.5),
                        _Ctx(tmp_path, {}, src_path=src))
    assert spill and spill[0][1] > 200


def test_the_breakdown_is_drawn_from_the_edl():
    from timeline import Timeline
    edl = {"keep": [[0.0, 4.0], [6.0, 9.0]],
           "fx": [{"id": "ob1", "type": "object", "start": 1.0, "end": 3.0,
                   "label": "laptop"},
                  {"id": "lt1", "type": "light", "start": 5.0, "end": 6.5,
                   "kind": "dissolve", "device": "light_dissolve"}],
           "motion": [{"id": "w1", "template": "word_slam", "start": 1.0,
                       "end": 2.5, "anchor": {"to": "screen"}},
                      {"id": "bd", "template": "breakdown", "start": 0.0,
                       "end": 7.0}]}

    class C:
        tl = Timeline(edl["keep"], [], [])
    doc = layouts.breakdown_doc(edl, C())
    assert doc["duration"] == pytest.approx(7.0)
    assert doc["cuts"] == [4.0]
    lanes = {ln["name"]: ln for ln in doc["lanes"]}
    assert [i["label"] for i in lanes["world"]["items"]] == \
        ["object: laptop"]
    assert lanes["devices"]["items"][0]["label"] == \
        "light_dissolve · dissolve"
    assert all("breakdown" not in i["label"]
               for i in lanes["graphics"]["items"])
    assert "sound" not in lanes            # nothing invented


def test_the_brand_palette_reads_a_logo(tmp_path):
    import cv2
    img = np.zeros((60, 120, 4), np.uint8)
    img[:, :60] = (40, 30, 200, 255)       # BGR red-ish half, opaque
    img[:, 60:] = (20, 20, 20, 255)        # near-black half
    img[:10, :10, 3] = 0                   # transparent corner ignored
    p = str(tmp_path / "logo.png")
    cv2.imwrite(p, img)
    pal = layouts.brand_palette(p, k=3)
    r, g, b = (int(pal["accent"][i:i + 2], 16) for i in (1, 3, 5))
    assert r > 150 and g < 80 and b < 80
    assert pal["ink"] != pal["accent"]
