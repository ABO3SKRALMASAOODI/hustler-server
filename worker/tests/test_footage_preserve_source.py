"""A workflow must be able to inspect and lay out the actual uncropped source."""
import os
import shutil
import subprocess

import pytest
from PIL import Image

from footage import cut


def test_preserved_supply_keeps_both_edges_and_source_aspect(tmp_path, monkeypatch):
    ff = os.environ.get('FOOTAGE_FFMPEG') or shutil.which('ffmpeg')
    if not ff:
        pytest.skip('ffmpeg unavailable')
    monkeypatch.setattr(cut.fetch, 'ffmpeg', lambda: ff)
    im = Image.new('RGB', (640, 360), '#00aa00')
    im.paste('#ff0000', (0, 0, 80, 360))
    im.paste('#0000ff', (560, 0, 640, 360))
    png = tmp_path / 'edges.png'
    im.save(png)
    src = tmp_path / 'source.mp4'
    subprocess.run([ff, '-v', 'error', '-loop', '1', '-i', str(png), '-t', '0.2', '-pix_fmt', 'yuv420p', str(src)], check=True)
    out = tmp_path / 'fitted.mp4'
    # Deliberately supply the old off-centre portrait crop and a trim. Neither
    # may discard pixels on the source-preserving path.
    cut.finish(str(src), str(out), 270, 480, x=.9, trim=(.2, .2), preserve_source=True)
    from io import BytesIO
    raw = subprocess.run([ff, '-v', 'error', '-i', str(out), '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
    got = Image.open(BytesIO(raw)).convert('RGB')
    assert abs(got.width / got.height - 640 / 360) < .03
    assert got.width <= 270 and got.height <= 480
    left, right = got.getpixel((3, got.height // 2)), got.getpixel((got.width-4, got.height // 2))
    assert left[0] > 180 and left[2] < 40
    assert right[2] > 180 and right[0] < 40


def test_legacy_supply_still_uses_requested_canvas(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cut, 'borders', lambda p: None)
    monkeypatch.setattr(cut.fetch, 'ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(cut.subprocess, 'run', lambda args, **kw: calls.append(args) or type('R', (), {'returncode': 0})())
    cut.finish('in.mp4', str(tmp_path/'out.mp4'), 270, 480, x=.8)
    vf = calls[0][calls[0].index('-vf')+1]
    assert 'crop=270:480' in vf
    assert 'iw*0.800' in vf
