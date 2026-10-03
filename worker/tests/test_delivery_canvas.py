"""Archival footage cannot lower the raster used for independent graphics."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
import renderer


@pytest.mark.parametrize("source,ratio,expected", [
    ((638, 360), "9:16", (1080, 1920)),
    ((358, 638), "9:16", (1080, 1920)),
    ((640, 360), "source", (1920, 1080)),
    ((640, 480), None, (1440, 1080)),
    ((360, 360), "1:1", (1080, 1080)),
    ((638, 360), "4:5", (1080, 1350)),
    ((3840, 2160), "16:9", (3840, 2160)),
])
def test_delivery_resolution_is_independent_of_source(source, ratio, expected):
    assert renderer.frame_dims(*source, ratio, delivery=True) == expected


def test_preview_and_explicit_canvas_remain_bounded():
    assert renderer.frame_dims(358, 638, "source") == (358, 638)
    edl = {"canvas": {"width": 640, "height": 360, "fps": 25},
           "keep": [], "inserts": [{"kind": "image", "asset_key": "x",
                                      "at": 0, "duration_s": 1}]}
    assert renderer._composition_geometry(edl, None, False) == (640, 360, 25)


def test_low_resolution_final_cannot_be_reused_as_fixed():
    small = {"width": 358, "height": 638, "meta": {}}
    assert not renderer.delivery_canvas_current(small, "final")
    assert renderer.delivery_canvas_current(small, "preview")
    assert renderer.delivery_canvas_current({"width": 1080, "height": 1920}, "final")
    assert renderer.delivery_canvas_current(dict(small, meta={"delivery_v": 1}), "final")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="real media tools required")
def test_real_archival_final_rasterizes_captions_and_branding_at_hd(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=358x638:r=12:d=1", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264",
                    "-c:a", "aac", "-pix_fmt", "yuv420p", str(source)], check=True)
    edl = {"keep": [[0, 1]], "frame": {"ratio": "9:16", "mode": "pad"},
           "captions": [{"start": 0, "end": 1, "text": "Sharp independent text"}]}
    renderer.render_edl(edl, {"video": {"duration": 1}}, str(source),
                        str(output), str(tmp_path), preview=False, want_wm=True)
    info = renderer.media.probe(str(output))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert 5.9 <= info["duration"] <= 6.2  # full five-second branding preserved
    for name in ("captions.ass", "watermark.ass"):
        ass = (tmp_path / name).read_text()
        assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    # Decode a real frame. This also catches missing/late composition output.
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i",
                                   str(output), "-frames:v", "1", "-f", "rawvideo",
                                   "-pix_fmt", "gray", "-"])
    assert len(raw) == 1080 * 1920
    assert sum(pixel > 180 for pixel in raw[:1080 * 300]) > 100  # visible brand
    assert sum(pixel > 180 for pixel in raw[1080 * 300:]) > 100  # visible caption
