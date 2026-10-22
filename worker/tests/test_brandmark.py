"""The v6 free-tier corner mark (owner, Oct 2026): a sponsor-style
signature at the TOP-RIGHT — "EDITED USING" in small tracked caps, the robot
and "Valmera AI" in the brand face, "valmera.io" — on one right edge, read
on any footage through soft shades (no box, no outline).

Pinned here: the design's geometry, the right-edge alignment of its three
lines, the brand faces it is set in, what a render actually burns in the
mark's region (the ROI equals the design), and that the 5-second end card
assets are the ones the card has always used.
"""
import hashlib
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import brandmark  # noqa: E402
import renderer  # noqa: E402

FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def test_the_mark_is_pinned_top_right_at_1080x1920():
    g = renderer.watermark_geometry(1080, 1920)
    # the design (px of a 1080x1920 final; +-2 px for another platform's
    # glyph rasteriser): ink right edge at the 10% inset, top at 6%
    assert g["ink"][2] == 972 and g["ink"][1] == 115
    assert g["ink"] == pytest.approx([610, 115, 972, 225], abs=2)
    assert [g["rx"], g["ry"], g["rw"], g["rh"]] == pytest.approx([610, 124, 60, 88], abs=2)
    rows = g["rows"]
    assert rows["kick"] == pytest.approx([752, 115, 972, 134], abs=2)
    assert rows["name"] == pytest.approx([685, 146, 973, 191], abs=2)
    assert rows["url"] == pytest.approx([826, 204, 972, 225], abs=2)
    # its floor stays the v5 mark's (y 0.128): the band layouts the runs
    # measured keep their height; only the side moved
    assert g["ink"][3] / 1920 <= 0.1185


@pytest.mark.parametrize("W,H", [(1080, 1920), (720, 1280), (1920, 1080), (1080, 1350)])
def test_three_lines_end_on_one_right_edge(W, H):
    """Every line's VISIBLE ink (opaque white, not its advance box) ends on
    the same column, and the robot sits left of the wordmark, centred on
    its caps."""
    g = renderer.watermark_geometry(W, H)
    im = np.asarray(brandmark.render(W, H)).astype(int)
    x, y = g["image"][0], g["image"][1]
    rgb, a = im[..., :3], im[..., 3]
    white = (rgb.min(axis=2) > 200) & (a >= 160)
    edges = {}
    for k, (x0, y0, x1, y1) in g["rows"].items():
        band = white[y0 - y:y1 - y, x0 - x:x1 - x]
        cols = np.where(band.any(axis=0))[0]
        edges[k] = x0 + int(cols[-1]) + 1
    assert max(edges.values()) - min(edges.values()) <= 1, edges
    assert abs(max(edges.values()) - g["right"]) <= 1, (edges, g["right"])
    name = g["rows"]["name"]
    assert g["rx"] + g["rw"] < name[0]
    robot_mid = g["ry"] + g["rh"] / 2.0
    assert name[1] - 2 <= robot_mid <= name[3] + 2


def test_the_shade_has_no_hard_edge():
    """No box: the image fades to nothing at its border, and away from the
    type the shade stays faint (a soft dimming, never a plate)."""
    im = np.asarray(brandmark.render(1080, 1920))
    a = im[..., 3].astype(int)
    assert max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max()) <= 2
    g = renderer.watermark_geometry(1080, 1920)
    x, y = g["image"][0], g["image"][1]
    # a point between the kicker's left end and the robot: no type there
    gx, gy = g["rows"]["kick"][0] - 40 - x, g["rows"]["kick"][1] + 6 - y
    assert a[gy, gx] < 0.45 * 255


def test_the_mark_is_set_in_the_brand_faces_with_pair_kerning():
    assert os.path.basename(brandmark.NAME_FONT) == "PlusJakartaSans-ExtraBold.ttf"
    assert os.path.basename(brandmark.SMALL_FONT) == "InterDisplay-Bold.ttf"
    assert (brandmark.TEXT_KICK, brandmark.TEXT_NAME, brandmark.TEXT_URL) == \
        ("EDITED USING", "Valmera AI", "valmera.io")
    pytest.importorskip("uharfbuzz")
    from PIL import ImageFont
    adv = brandmark._advances("Va", brandmark.NAME_FONT, 100.0)
    f = ImageFont.truetype(brandmark.NAME_FONT, 100)
    # the V-a pair is kerned in (the site's wordmark), not set on advances
    assert sum(adv) < f.getlength("V") + f.getlength("a") - 1


def test_the_robot_keeps_its_stalk_attached():
    """The dark antenna stalk is lifted to white (the end card's
    correction), so the red ball never floats on a dark plate; every other
    pixel is the brand asset."""
    from PIL import Image
    src = np.asarray(Image.open(brandmark.ROBOT_PNG).convert("RGBA")).astype(int)
    out = np.asarray(brandmark._robot_master()).astype(int)
    changed = np.any(src != out, axis=2)
    ys = np.where(changed.any(axis=1))[0]
    assert changed.sum() > 0 and ys.max() < src.shape[0] * 0.2
    assert (out[changed][:, :3] == 255).all()


@FFMPEG
@pytest.mark.parametrize("bg", ["white", "black", "0x6a8a5c"])
def test_the_rendered_corner_is_the_design(tmp_path, bg):
    """What a final burns in the mark's region is the mark as designed: the
    production overlay (renderer.watermark_image + _watermark_parts) on a
    yuv420p programme, read back, against the design composited over the
    same plate — compared as luma (chroma is subsampled), within the
    yuv420p round trip's rounding."""
    from PIL import Image
    W, H = 540, 960
    img = renderer.watermark_image(str(tmp_path), W, H)
    graph = ";".join(["[0:v]format=yuv420p[base]"]
                     + renderer._watermark_parts("base", "vout", 1, None, W, H))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}:d=0.2",
         "-loop", "1", "-t", "0.2", "-i", img, "-filter_complex", graph,
         "-map", "[vout]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    got = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(float)
    plate = Image.new("RGBA", (W, H), bg.replace("0x", "#"))
    g = renderer.watermark_geometry(W, H)
    plate.alpha_composite(Image.open(img).convert("RGBA"), tuple(g["image"][:2]))
    want = np.asarray(plate.convert("RGB")).astype(float)

    def luma(a):
        return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    x, y, w, h = g["image"]
    roi = (slice(y, y + h), slice(x, x + w))
    diff = np.abs(luma(got) - luma(want))
    assert diff[roi].mean() < 2.5 and np.percentile(diff[roi], 99.5) <= 4, \
        (diff[roi].mean(), diff[roi].max())
    # and nothing of it outside its own image
    outside = np.ones((H, W), bool)
    outside[roi] = False
    assert diff[outside].max() <= 3


def test_the_end_card_is_the_card_it_always_was():
    """The 5-second Valmera end card is not part of this redesign: its
    animated master and its poster are byte-for-byte the v14 assets."""
    brand = os.path.join(os.path.dirname(HERE), "brand")
    md5 = lambda p: hashlib.md5(open(os.path.join(brand, p), "rb").read()).hexdigest()  # noqa: E731
    assert md5("endcard.mp4") == "7e9fb0d0997c090947df44be32d106cb"
    assert md5("endcard.png") == "f14ed536cefd8392764dbf2416ac89e0"


def test_the_image_is_cached_per_height(tmp_path):
    a = renderer.watermark_image(str(tmp_path), 1080, 1920)
    b = renderer.watermark_image(str(tmp_path), 1080, 1920)
    c = renderer.watermark_image(str(tmp_path), 1920, 1080)
    assert a == b and a != c and os.path.basename(a) == "valmera_mark_v6_1920.png"
