"""Track 1E colour math: LUTs, CIEDE2000, the hue key, shot match.

Pure numpy where it can be, and where the renderer evaluates a formula in
ffmpeg (the hue key and the luma-weighted chroma tables are ``lut2``
expressions), the same numbers are checked in both: one formula, so what a
verb measures at write time is what the render draws.
"""

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gradewin  # noqa: E402
import lutlib  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="needs ffmpeg")


# ── .cube I/O ────────────────────────────────────────────────────────────

def test_cube_round_trip_is_exact_to_six_decimals():
    lut = lutlib.identity(17)
    text = lutlib.cube_text(lut, title="t")
    back = lutlib.parse_cube(text)
    assert back.size == 17 and back.source_kind == "3d"
    assert np.abs(back.table - lut.table).max() < 1e-6
    assert lutlib.cube_text(back, title="t") == text     # stable bytes


def test_cube_rows_are_red_fastest():
    text = "LUT_3D_SIZE 2\n" + "\n".join(
        f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1))
    lut = lutlib.parse_cube(text)
    # table[b, g, r] holds (r, g, b): an identity
    assert lut.table[0, 0, 1].tolist() == [1, 0, 0]
    assert lut.table[1, 0, 0].tolist() == [0, 0, 1]
    assert np.allclose(lutlib.apply(lut, [[0.25, 0.5, 0.75]]),
                       [[0.25, 0.5, 0.75]])


@pytest.mark.parametrize("text, match", [
    ("", "no LUT_3D_SIZE"),
    ("LUT_3D_SIZE 66\n", "outside 2..65"),
    ("LUT_3D_SIZE 2\n0 0 0\n", "needs 8 rows"),
    ("LUT_3D_SIZE 2\n" + "nan 0 0\n" * 8, "NaN"),
    ("LUT_3D_SIZE 2\nLUT_3D_SIZE 2\n", "one size"),
    ("LUT_3D_SIZE 2\n" + "0 0 0\n" * 7 + "9 9 9\n", "far outside"),
    ("LUT_3D_SIZE 2\nhello world\n", "not a row"),
    ("DOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\nLUT_3D_SIZE 2\n"
     + "0 0 0\n" * 8, "DOMAIN_MAX"),
])
def test_bad_cubes_are_refused_with_a_reason(text, match):
    with pytest.raises(lutlib.LutError, match=match):
        lutlib.parse_cube(text)


def test_a_65_cube_is_accepted_and_1d_curves_expand():
    n = 65
    lut = lutlib.identity(n)
    assert lutlib.parse_cube(lutlib.cube_text(lut)).size == 65
    one_d = "LUT_1D_SIZE 3\n0 0 0\n0.25 0.5 0.75\n1 1 1\n"
    got = lutlib.parse_cube(one_d)
    assert got.source_kind == "1d" and got.size == lutlib.MATCH_SIZE
    out = lutlib.apply(got, [[0.5, 0.5, 0.5]])[0]
    assert np.allclose(out, [0.25, 0.5, 0.75], atol=1e-3)


def test_mix_compose_resample():
    neg = lutlib.Lut(1.0 - lutlib.identity(9).table)
    half = lutlib.mix(neg, 0.5)
    assert np.allclose(lutlib.apply(half, [[0.2, 0.4, 0.9]]), [[0.5] * 3])
    twice = lutlib.compose(neg, neg)
    assert np.allclose(twice.table, lutlib.identity(9).table, atol=1e-6)
    assert lutlib.resample(neg, 17).size == 17
    assert np.allclose(lutlib.apply(lutlib.resample(neg, 17), [[0.3, 0.6,
                                                                0.1]]),
                       [[0.7, 0.4, 0.9]], atol=1e-6)


# ── CIEDE2000 (Sharma, Wu & Dalal 2005 test data) ───────────────────────

@pytest.mark.parametrize("lab1, lab2, de", [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
])
def test_ciede2000_matches_the_published_pairs(lab1, lab2, de):
    assert lutlib.delta_e2000(np.array(lab1), np.array(lab2)) == \
        pytest.approx(de, abs=1e-4)


def test_lab_round_trip():
    rgb = np.random.default_rng(1).random((500, 3))
    back = lutlib.lab_to_rgb(lutlib.rgb_to_lab(rgb))
    assert np.abs(back - rgb).max() < 1e-6


# ── the hue key ──────────────────────────────────────────────────────────

def _ycc(hexs):
    r, g, b = lutlib.hex_rgb(hexs)
    return lutlib.rgb_to_ycc8([r / 255, g / 255, b / 255])


def test_hue_key_keeps_its_colour_and_drops_greys_skin_and_neighbours():
    key = gradewin.hue_key("#c8202a")
    for col, want in (("#c8202a", 1.0), ("#d42a20", 1.0),    # red, red
                      ("#808080", 0.0), ("#202020", 0.0),    # greys
                      ("#2050c8", 0.0), ("#20c840", 0.0),    # blue, green
                      ("#e8b090", 0.0),                      # light skin
                      ("#e8d020", 0.0)):                     # yellow
        _y, cb, cr = _ycc(col)
        w = float(lutlib.hue_weight(key, cb, cr))
        assert w == pytest.approx(want, abs=0.02), (col, w)


def test_skin_cluster():
    for col in ("#e0ac8c", "#c68642", "#8d5524", "#f1c27d"):
        _y, cb, cr = _ycc(col)
        assert float(lutlib.skin_weight(cb, cr)) > 0.9, col
    for col in ("#2050c8", "#20c840", "#808080"):
        _y, cb, cr = _ycc(col)
        assert float(lutlib.skin_weight(cb, cr)) < 0.05, col
    assert lutlib.is_skin_hue("#d09070") and not lutlib.is_skin_hue("#c8202a")


def test_dominant_hue_ignores_skin_and_grey():
    rgb = np.zeros((60, 60, 3), np.uint8)
    rgb[:] = (128, 128, 128)
    rgb[:30, :30] = (224, 172, 140)          # skin, a quarter
    rgb[40:, 40:] = (200, 32, 42)            # red, smaller
    hexv, share = lutlib.dominant_hue(rgb)
    r, g, b = lutlib.hex_rgb(hexv)
    assert r > 180 and g < 60 and b < 70 and share > 0.1
    grey = np.full((20, 20, 3), 120, np.uint8)
    assert lutlib.dominant_hue(grey)[0] is None


def _lut2(expr, x, y):
    """Evaluate an ffmpeg lut2 expression on two 8-bit planes."""
    import tempfile
    h, w = x.shape
    with tempfile.TemporaryDirectory() as d:
        px, py, po = (os.path.join(d, n) for n in ("x.raw", "y.raw",
                                                   "o.raw"))
        x.astype(np.uint8).tofile(px)
        y.astype(np.uint8).tofile(py)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-s", f"{w}x{h}", "-i", px,
                        "-f", "rawvideo", "-pix_fmt", "gray", "-s",
                        f"{w}x{h}", "-i", py, "-filter_complex",
                        f"[0:v][1:v]lut2=c0='{expr}'", "-f", "rawvideo",
                        "-pix_fmt", "gray", po], check=True)
        return np.fromfile(po, np.uint8).reshape(h, w).astype(float)


@needs_ffmpeg
def test_hue_key_formula_is_one_formula_in_numpy_and_ffmpeg():
    cb, cr = np.meshgrid(np.arange(256), np.arange(256))
    for colour in ("#c8202a", "#2a7fd4", "#b07020"):
        key = gradewin.hue_key(colour, 0.1, 0.15)
        ff = _lut2(lutlib.hue_expr(key), cb, cr)
        np_w = np.clip(np.round(lutlib.hue_weight(key, cb, cr) * 255), 0,
                       255)
        assert np.abs(ff - np_w).max() <= 1, colour
    ff = _lut2(lutlib.skin_expr(), cb, cr)
    sk = np.round(lutlib.skin_weight(cb, cr) * 255)
    assert np.abs(ff - sk).max() <= 1


@needs_ffmpeg
def test_luma_weighted_chroma_is_one_formula_in_numpy_and_ffmpeg():
    item = {"saturation": 0.8, "temperature": 0.5, "tint": -0.3,
            "split_tone": {"shadows": "#1f4e79", "highlights": "#e9b44c",
                           "balance": 0.2}}
    yy, uu = np.meshgrid(np.arange(256), np.arange(256))
    for plane in ("u", "v"):
        ff = _lut2(gradewin.chroma_expr(item, plane), yy, uu)
        mine = np.vectorize(lambda x, y: gradewin.chroma_value(
            item, plane, x, y))(yy, uu)
        assert np.abs(ff - np.round(mine)).max() <= 1, plane


def test_chroma_offsets_vanish_at_black():
    item = {"temperature": 1.0, "split_tone": {"shadows": "#ff0000",
                                               "highlights": "#0000ff"}}
    for plane in ("u", "v"):
        assert gradewin.chroma_value(item, plane, 16, 128) == 128


def test_y_table_identity_and_direction():
    assert gradewin.y_table() == list(range(256))
    up, down = gradewin.y_table(ev=1.0), gradewin.y_table(ev=-1.0)
    mid = 16 + 219 // 2
    assert up[mid] > mid > down[mid]
    assert all(a <= b for a, b in zip(up, up[1:]))         # monotone
    assert up[235] <= 235 and up[16] == 16
    hard = gradewin.y_table(contrast=1.5)
    assert hard[40] < 40 and hard[200] > 200


# ── shot match ───────────────────────────────────────────────────────────

def _scene(seed=3, n=4000):
    rng = np.random.default_rng(seed)
    base = rng.random((n, 3)) * 0.8 + 0.1
    return base


@pytest.mark.parametrize("method", ["mkl", "reinhard"])
def test_match_lut_undoes_a_colour_cast(method):
    hero = _scene()
    # the same scene shot warmer, darker and flatter
    cast = np.clip(hero * [1.08, 0.97, 0.82] * 0.9 + 0.03, 0, 1)
    before = lutlib.mean_delta_e(cast, hero)
    lut = lutlib.match_lut(cast, hero, method=method)
    fixed = lutlib.apply(lut, cast)
    after = lutlib.mean_delta_e(fixed, hero)
    assert before > 5 and after < (0.4 if method == "mkl" else 0.5) * \
        before, (before, after)
    assert after < (3.0 if method == "mkl" else 3.5)


def test_match_limit_caps_the_move():
    hero = _scene()
    wild = np.clip(1 - hero, 0, 1)
    lut = lutlib.match_lut(wild, hero, limit=10)
    grid = lutlib.identity(lutlib.MATCH_SIZE).table
    d = lutlib.delta_e2000(lutlib.rgb_to_lab(grid),
                           lutlib.rgb_to_lab(lut.table))
    assert d.max() < 12
