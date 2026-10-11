"""Track 1B sprites (sprites.py): the subject as an RGBA cut-out, cropped,
with a decontaminated soft edge, from a frame + matte, a matte clip, or a
ready ``matte.person`` sidecar chunk; registered as a project image."""

import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sprites                                                # noqa: E402
import w1b_support                                            # noqa: E402


def _scene(h=120, w=160):
    """A green plate with a soft-edged orange disc."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(xs - 80, ys - 60)
    a = np.clip((30 - r) / 4 + 0.5, 0, 1)
    plate = np.array([20, 200, 40], np.float32)
    subj = np.array([230, 120, 40], np.float32)
    rgb = plate * (1 - a[..., None]) + subj * a[..., None]
    return rgb.astype(np.uint8), a


def test_a_cutout_is_cropped_rgba_with_the_mattes_alpha():
    rgb, a = _scene()
    img, box = sprites.cutout(rgb, a, pad_px=4)
    assert img.mode == "RGBA"
    x, y, w, h = box
    assert (w, h) == img.size
    assert 44 <= x <= 52 and 24 <= y <= 32 and 60 <= w <= 72
    arr = np.asarray(img).astype(int)
    assert arr[..., 3].max() == 255 and arr[0, 0, 3] == 0
    # the alpha is the matte's, unchanged
    assert np.abs(arr[..., 3] / 255.0 - a[y:y + h, x:x + w]).max() <= \
        sprites.CLEAR + 0.002


def test_the_soft_edge_carries_the_subjects_colour_not_the_plates():
    rgb, a = _scene()
    img, box = sprites.cutout(rgb, a)
    x, y, w, h = box
    arr = np.asarray(img).astype(float)
    soft = (arr[..., 3] > 30) & (arr[..., 3] < 200)
    assert soft.sum() > 50
    # in the raw frame the soft band is half plate (green); in the sprite
    # it is the subject's orange
    raw = rgb[y:y + h, x:x + w].astype(float)
    assert raw[soft][:, 1].mean() > 150
    assert arr[soft][:, 1].mean() < 135
    assert arr[soft][:, 0].mean() > 200


def test_an_empty_matte_gives_no_sprite():
    rgb, a = _scene()
    assert sprites.cutout(rgb, a * 0) == (None, None)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_from_a_matte_clip_and_from_a_ready_sidecar(tmp_path, monkeypatch):
    src, mask = w1b_support.make_subject_source(
        str(tmp_path / "s.mp4"), str(tmp_path / "m.mkv"), dur=2.0)
    rgb, alpha = sprites.decode_pair(src, mask, 1.0)
    img, box = sprites.cutout(rgb, alpha)
    assert img is not None and box[3] > 150
    import sidecars
    calls, handles = [], []
    # a sprite READS a measured chunk: it never launches analysis
    monkeypatch.setattr(sidecars, "ensure",
                        lambda *a, **k: calls.append((a, k)) or "h")

    def fetch(h, d):
        handles.append(h)
        return [{"t0": 0.5, "t1": 2.0, "path": mask, "key": "k", "meta": {}}]
    monkeypatch.setattr(sidecars, "fetch", fetch)
    ref = sidecars.source_from("b" * 64, "proxy.mp4",
                               {"duration": 2.0, "fps": 25.0, "width": 640,
                                "height": 360}, 1, [])
    got = sprites.from_sidecar(ref, src, 1.5, str(tmp_path))
    assert got is not None and got[0].mode == "RGBA"
    assert calls == []
    (h,) = handles
    assert h.kind == "matte.person" and h.source_sha == "b" * 64
    assert any(c.t0 <= 1.5 < c.t1 for c in h.chunks)
    # no chunk covers the second: None, never an exception
    monkeypatch.setattr(sidecars, "fetch", lambda h, d: [])
    assert sprites.from_sidecar(ref, src, 1.5, str(tmp_path)) is None
