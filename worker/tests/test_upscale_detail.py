"""p6311: "sharpen, denoise and improve the detail (upscale)". The 2K export
was a plain lanczos scale and the ask checker read "no upscale step": the
numbers it saw held only the small preview's size. The delivery upscale now
ends in a contrast-adaptive detail pass (on real footage it brings the
picture closer to the true one, not merely crisper), and the checker's
numbers carry the export.
"""
import shutil
import subprocess

import numpy as np
import pytest

import renderer
from score import asks

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def _gray(path_or_lavfi, vf, size, lavfi=False):
    src = (["-f", "lavfi", "-i", path_or_lavfi] if lavfi
           else ["-i", path_or_lavfi])
    r = subprocess.run(["ffmpeg", "-v", "error", *src, "-frames:v", "1",
                        "-vf", vf + ",format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(size, size).astype(float)


def test_the_upscale_chain_ends_in_a_detail_pass():
    chain = renderer.upscale_filter(1440, 2560)
    assert chain.startswith("scale=1440:2560:flags=lanczos,cas=strength=")
    assert chain.endswith(",setsar=1")


@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")
def test_the_detail_pass_adds_detail_without_ringing_away_from_the_truth():
    # On real footage (three p6311 frames, 540->270->540 at 0.6) it also
    # lands closer to the truth: PSNR +0.12 to +0.16 dB over plain lanczos
    # (renderer.UPSCALE_DETAIL). On hard synthetic graphics it costs at
    # most a tenth of a dB.
    truth_src = "testsrc2=s=512x512:r=1:d=1"
    truth = _gray(truth_src, "null", 512, lavfi=True)
    low = "scale=256:256:flags=area,"
    plain = _gray(truth_src, low + "scale=512:512:flags=lanczos", 512,
                  lavfi=True)
    ours = _gray(truth_src, low + renderer.upscale_filter(512, 512), 512,
                 lavfi=True)

    def psnr(a):
        return 10 * np.log10(255 ** 2 / ((a - truth) ** 2).mean())

    def detail(a):
        return np.abs(4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1]
                      - a[1:-1, :-2] - a[1:-1, 2:]).mean()
    assert detail(ours) > detail(plain) * 1.05
    assert psnr(ours) >= psnr(plain) - 0.15


def test_the_ask_numbers_carry_the_upscaled_export():
    edl = {"keep": [[0, 10]], "frame": {"ratio": "9:16", "mode": "crop",
                                        "resolution": "2k"}}
    index = {"video": {"width": 1080, "height": 1080}}
    got = asks.measured(edl, [], 10.0, {"width": 608, "height": 1080},
                        index)
    assert got["render_size"] == [608, 1080]
    assert got["export"]["resolution"] == "2k"
    assert got["export"]["size"] == [1440, 2560]
    assert "detail" in got["export"]["upscale"]
    plain = asks.measured({"keep": [[0, 10]]}, [], 10.0, None, index)
    assert plain["export"]["resolution"] == "hd"


def test_a_malformed_detail_setting_never_breaks_the_renderer_import():
    assert renderer._upscale_detail("abc") == 0.6
    assert renderer._upscale_detail("nan") == 0.6
    assert renderer._upscale_detail("-1") == 0.6
    assert renderer._upscale_detail("0") == 0.0
    assert renderer._upscale_detail("0.4") == 0.4
