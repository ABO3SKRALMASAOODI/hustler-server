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
                      ("#e8b090", 0.0), ("#d09070", 0.0),    # skin, ruddy
                      ("#e8d020", 0.0)):                     # yellow
        y, cb, cr = _ycc(col)
        w = float(lutlib.hue_weight(key, cb, cr, y))
        assert w == pytest.approx(want, abs=0.02), (col, w)


def test_a_dark_red_key_keeps_the_curtain_but_never_the_faces():
    """The JRE curtain (median RGB 35,11,13) has skin's CHROMA; luma tells
    them apart, so the key keeps it while ruddy faces go grey."""
    key = gradewin.hue_key("#361113", 0.08)
    assert key.exclude_skin is True
    for col, want in (("#361113", 1.0), ("#2a0c0e", 1.0),    # curtain
                      ("#c8202a", 1.0),                      # neon red
                      ("#d09070", 0.0), ("#b05040", 0.0)):   # faces
        y, cb, cr = _ycc(col)
        w = float(lutlib.hue_weight(key, cb, cr, y))
        assert w == pytest.approx(want, abs=0.05), (col, w)


def test_skin_cluster():
    for col in ("#e0ac8c", "#c68642", "#8d5524", "#f1c27d"):
        y, cb, cr = _ycc(col)
        assert float(lutlib.skin_weight(cb, cr, y)) > 0.9, col
    y, cb, cr = _ycc("#361113")                      # dark red: not skin
    assert float(lutlib.skin_weight(cb, cr)) > 0.5
    assert float(lutlib.skin_weight(cb, cr, y)) < 0.05
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
    # the luma gate on top of a chroma-only weight
    yy, ss = np.meshgrid(np.arange(256), np.arange(256))
    ff = _lut2(lutlib.skin_luma_expr(), yy, ss)
    gate = np.clip((yy - 48.0) / 24.0, 0, 1)
    assert np.abs(ff - np.floor(ss * gate * gate * (3 - 2 * gate) + 0.5)
                  ).max() <= 1


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
    assert before > 5 and after < 0.5 * before, (before, after)
    assert after < (3.0 if method == "mkl" else 3.5)


def test_match_leaves_colours_beyond_the_fit_alone():
    """The fit is learnt on lit surfaces (walls, skin); a neon sign more
    saturated than anything it was learnt on keeps its own colour, while
    the surfaces are still corrected (AT-1E-6's set lights)."""
    rng = np.random.default_rng(5)
    hero = rng.random((4000, 3)) * 0.35 + 0.35            # muted surfaces
    cast = np.clip(hero * [1.06, 0.98, 0.88], 0, 1)
    lut = lutlib.match_lut(cast, hero)
    assert lutlib.mean_delta_e(lutlib.apply(lut, cast), hero) < \
        0.5 * lutlib.mean_delta_e(cast, hero)
    neon = np.array([[1.0, 0.82, 0.08], [0.95, 0.12, 0.10]])
    moved = lutlib.delta_e2000(lutlib.rgb_to_lab(neon),
                               lutlib.rgb_to_lab(lutlib.apply(lut, neon)))
    assert moved.max() < 1.0, moved


def test_match_limit_caps_the_move():
    hero = _scene()
    wild = np.clip(1 - hero, 0, 1)
    lut = lutlib.match_lut(wild, hero, limit=10)
    grid = lutlib.identity(lutlib.MATCH_SIZE).table
    d = lutlib.delta_e2000(lutlib.rgb_to_lab(grid),
                           lutlib.rgb_to_lab(lut.table))
    assert d.max() < 12


# ── gradelut's cube extension ────────────────────────────────────────────

@needs_ffmpeg
def test_bake_cube_runs_the_real_chain(tmp_path):
    import gradelut
    ident = gradelut.bake_cube("null", size=9)
    assert np.abs(ident.table - lutlib.identity(9).table).max() <= 1 / 255
    mono = gradelut.bake_cube("hue=s=0", size=9)
    flat = mono.table.reshape(-1, 3)
    assert np.abs(flat[:, 0] - flat[:, 1]).max() <= 2 / 255
    neg = gradelut.bake_cube("negate", size=5)
    assert np.allclose(neg.table, 1.0 - lutlib.identity(5).table,
                       atol=1 / 255)
    p = gradelut.write_cube(neg, str(tmp_path / "neg.cube"), title="neg")
    back = gradelut.read_cube(p)
    assert back.size == 5 and np.allclose(back.table, neg.table, atol=1e-6)
    big = lutlib.cube_text(lutlib.identity(9))
    (tmp_path / "big.cube").write_text(big)
    with pytest.raises(lutlib.LutError, match="outside 2..8"):
        gradelut.read_cube(str(tmp_path / "big.cube"), max_size=8)


def test_match_ignores_wardrobe_that_only_one_shot_has():
    """Same set, two days: the hero wears black, the other shot white and
    is lit warmer. Fitting on the shared colours undoes the cast without
    dragging the white shirt toward black."""
    rng = np.random.default_rng(11)
    set_px = np.clip(rng.normal([0.45, 0.12, 0.13], 0.04, (6000, 3)), 0, 1)
    skin = np.clip(rng.normal([0.78, 0.58, 0.48], 0.04, (3000, 3)), 0, 1)
    black = np.clip(rng.normal(0.08, 0.02, (4000, 3)), 0, 1)
    white = np.clip(rng.normal(0.9, 0.02, (4000, 3)), 0, 1)
    hero = np.concatenate([set_px, skin, black])
    warm = np.array([1.06, 0.98, 0.9])
    shot = np.clip(np.concatenate([set_px, skin, white]) * warm, 0, 1)
    for shared, worst in ((True, 2.5), (False, None)):
        lut = lutlib.match_lut(shot, hero, shared=shared)
        fixed = lutlib.apply(lut, shot)
        d_skin = lutlib.mean_delta_e(fixed[6000:9000], skin)
        d_white = lutlib.mean_delta_e(fixed[9000:], white)
        if worst is not None:
            assert d_skin < worst and d_white < 6, (d_skin, d_white)
        else:
            unshared = (d_skin, d_white)
    assert unshared[1] > 6          # the naive fit darkens the white shirt


@needs_ffmpeg
def test_the_luma_aware_key_is_one_formula_in_numpy_and_ffmpeg():
    """The render's chain (hue gate, radius, relative-saturation gate,
    skin) against lutlib.hue_weight(..., y), within the 8-bit steps the
    chain quantises through."""
    rng = np.random.default_rng(4)
    yy = rng.integers(16, 236, (96, 96))
    cb = rng.integers(60, 200, (96, 96))
    cr = rng.integers(60, 220, (96, 96))
    for colour in ("#8a1c27", "#361113", "#2a7fd4"):
        key = gradewin.hue_key(colour, 0.06, 0.08)
        wh = _lut2(lutlib.hue_angle_expr(key), cb, cr)
        r2 = _lut2(lutlib.RADIUS2_EXPR, cb, cr)
        g = _lut2(lutlib.rel_gate_expr(key), yy, r2)
        w = _lut2("x*y/255", wh, g)
        if key.exclude_skin:
            sk = _lut2(lutlib.skin_luma_expr(), yy,
                       _lut2(lutlib.skin_expr(near=key.near), cb, cr))
            w = _lut2("x*(255-y)/255", w, sk)
        mine = lutlib.hue_weight(key, cb, cr, yy) * 255
        assert np.abs(w - mine).max() <= 4, colour


def test_relative_saturation_keeps_a_coats_shadows():
    """A red coat's fold in shadow (a third of the chroma at a third of the
    brightness) stays in the key; a grey street never enters it."""
    key = gradewin.hue_key("#8a1c27", 0.04, 0.06)
    lit = lutlib.rgb_to_ycc8(np.array([138, 28, 39]) / 255.0)
    fold = lutlib.rgb_to_ycc8(np.array([60, 14, 18]) / 255.0)
    grey = lutlib.rgb_to_ycc8(np.array([120, 118, 116]) / 255.0)
    w = [float(lutlib.hue_weight(key, c[1], c[2], c[0]))
         for c in (lit, fold, grey)]
    assert w[0] > 0.95 and w[1] > 0.95 and w[2] < 0.01, w
    # the chroma-only gate (no luma) would have dropped the fold
    assert float(lutlib.hue_weight(key, fold[1], fold[2])) < 0.5
