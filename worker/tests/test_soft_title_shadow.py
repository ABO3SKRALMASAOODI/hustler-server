"""The p5724 serif title needs a soft shadow while the letters stay sharp."""
import re
import shutil
import subprocess

import numpy as np
import pytest

import graphics
import renderer


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
def test_libass_softens_the_explicit_shadow_without_blurring_the_ink(tmp_path):
    item = dict(id="sub", template="subtitle", font="Playfair Display Black",
                font_size=.032, text="The mountain light", start=0, end=2,
                entrance="none", exit="none", shadow=4.0)
    path = tmp_path / "subtitle.ass"
    graphics.build_gfx_ass({"texts": [item]}, 2, str(path), (720, 1280))
    generated = path.read_text()

    def pixels(ass):
        path.write_text(ass)
        vf = f"ass='{path}':fontsdir='{graphics.FONTS_DIR}'"
        frame = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=gray:s=720x1280:r=1", "-vf", vf, "-frames:v", "1",
            "-pix_fmt", "gray", "-f", "rawvideo", "-"])
        return np.frombuffer(frame, dtype=np.uint8)

    # The comparison retains the identical typeface, position and shadow offset.
    hard = pixels(re.sub(r"\\blur[\d.]+", "", generated))
    soft = pixels(generated)
    def fringe(frame):
        return np.count_nonzero((frame >= 113) & (frame < 125))
    assert fringe(soft) > 1.5 * fringe(hard)
    assert np.count_nonzero(soft > 200) >= .90 * np.count_nonzero(hard > 200)


def test_only_renders_with_the_affected_title_treatment_are_stale():
    edl = {"texts": [{"template": "subtitle", "shadow": 4}]}
    assert not renderer.title_shadow_current({}, edl)
    assert renderer.title_shadow_current({"title_shadow_v": 1}, edl)
    for extra in ({"shadow": 0}, {"outline_width": 2}, {"box": True}, {"entrance": "zoom_blur"}):
        assert renderer.title_shadow_current({}, {"texts": [dict(edl["texts"][0], **extra)]})
