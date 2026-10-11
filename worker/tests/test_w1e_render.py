"""Track 1E on real renders: grade windows and finishes through the renderer.

Every render runs through ``renderer.render_edl`` in a child process
(tests/w0b_probe.py) with LOSSLESS encodes, so frame md5s are the
filtergraph's own frames. It pins:

  * frame-exact windows on 25 / 29.97 / 24 / 59.94 fps programs: a grade
    or a finish changes exactly its frames [round(start*fps), round(end*fps));
  * region_locality = 0: outside a region, and outside the window, the
    picture is bit-exact;
  * ramps: mono -> colour lands ON the frame after the window (the drop),
    with eased weights in between; chroma is exactly neutral inside;
  * the hue key keeps its colour bit-exact and sends the rest to exact grey;
  * an imported LUT is applied; a missing LUT degrades, never fails;
  * every finish kind renders, only in its window, deterministically;
  * brand integrity (AT-0B-5 pattern): with a whole-program grade and a
    full-frame over_type finish, the end card is md5-identical and the
    watermark robot's opaque pixels are untouched.
"""

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import w0b_probe                                              # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": 12.0}}
KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear", "film",
         "thermal", "xray", "blueprint", "line_art", "solarise", "posterise",
         "kaleidoscope", "pixel_stretch", "night_vision")
LUT_KEY = "media/w1e/negative.cube"


def _base(keep=((0.0, 4.0), (5.0, 9.0)), ratio="9:16", captions=False):
    edl = {"keep": [list(k) for k in keep],
           "captions": {"enabled": bool(captions)}}
    if ratio:
        edl["frame"] = {"ratio": ratio}
    return edl


def _gw(fid, start, end, stage="world", **kw):
    it = {"id": fid, "type": "grade_window", "stage": stage, "start": start,
          "end": end}
    it.update(kw)
    return it


def _fi(fid, kind, start, end, stage="pre_captions", **kw):
    it = {"id": fid, "type": "finish", "stage": stage, "start": start,
          "end": end, "kind": kind}
    it.update(kw)
    return it


def _diff(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


def _yuv(path, W, H):
    a = np.fromfile(path, np.uint8).astype(int)
    y = a[:W * H].reshape(H, W)
    u = a[W * H:W * H + (W // 2) * (H // 2)].reshape(H // 2, W // 2)
    v = a[W * H + (W // 2) * (H // 2):].reshape(H // 2, W // 2)
    return y, u, v


def _chart(path, w=640, h=360, rate="25", dur=12.0):
    """Flat colour patches over a moving testsrc2 strip: red, blue, grey,
    skin and green, so the hue key can be checked patch by patch."""
    if os.path.exists(path):
        return path
    boxes = [("0xc8202a", 0, 0), ("0x2050c8", 1, 0), ("0x808080", 2, 0),
             ("0xe0ac8c", 0, 1), ("0x20c840", 1, 1), ("0xc8202a", 2, 1)]
    bw, bh = w // 3, (h * 2 // 3) // 2
    draws = ",".join(f"drawbox=x={c * bw}:y={r * bh}:w={bw}:h={bh}:"
                     f"color={col}:t=fill" for col, c, r in boxes)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:r={rate}:d={dur}", "-f", "lavfi",
                    "-i", f"sine=frequency=440:sample_rate=48000:"
                          f"duration={dur}", "-vf", draws,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                    "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", path], check=True)
    return path


def _negative_cube(path, n=17):
    import lutlib
    lut = lutlib.Lut(1.0 - lutlib.identity(n).table, title="negative")
    with open(path, "w") as f:
        f.write(lutlib.cube_text(lut))
    return path


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1e_render")
    srcs = {r: w0b_probe.make_source(str(d / f"src{n}.mp4"), rate=r,
                                     dur=8.0 if r != "25" else 12.0)
            for n, r in (("25", "25"), ("2997", "30000/1001"),
                         ("24", "24"), ("5994", "60000/1001"))}
    cube = _negative_cube(str(d / "negative.cube"))
    return {"dir": str(d), "src": srcs, "chart": _chart(str(d / "chart.mp4")),
            "cube": cube}


def _case(work, name, edl, src=None, **kw):
    c = {"name": name, "src": src or work["src"]["25"], "idx": IDX,
         "edl": edl}
    c.update(kw)
    return c


def _run(work, cases, sub):
    old = os.environ.get("VALMERA_FX_ASSET_LOCALS")
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps({LUT_KEY:
                                                        work["cube"]})
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], sub))
    finally:
        if old is None:
            os.environ.pop("VALMERA_FX_ASSET_LOCALS", None)
        else:
            os.environ["VALMERA_FX_ASSET_LOCALS"] = old


# ── one 25 fps child: windows, ramps, regions, LUT, every kind ───────────

@pytest.fixture(scope="module")
def r25(work):
    b = _base()
    cases = [
        _case(work, "base", b, dump_frames=[26, 40, 72, 73, 74, 75]),
        _case(work, "mono", dict(b, fx=[_gw("g1", 1.0, 3.0, saturation=0.0,
                                            ramp_out_f=3)]),
              dump_frames=[40, 72, 73, 74, 75]),
        _case(work, "ramp_in", dict(b, fx=[_gw("g2", 1.0, 3.0,
                                               saturation=0.0,
                                               ramp_in_f=2)]),
              dump_frames=[25, 26, 27]),
        _case(work, "region", dict(b, fx=[_gw(
            "g3", 1.0, 3.0, ev=1.5, contrast=1.2,
            region={"shape": "rect", "box": [0.25, 0.25, 0.75, 0.75]})]),
              dump_frames=[40]),
        _case(work, "lut", dict(b, fx=[_gw(
            "g4", 1.0, 3.0, lut={"asset_key": LUT_KEY, "strength": 1.0})]),
              dump_frames=[40]),
        _case(work, "lut_gone_reduced", dict(b, fx=[_gw(
            "g5", 1.0, 3.0, saturation=0.0,
            lut={"asset_key": "media/w1e/missing.cube"})])),
        _case(work, "lut_gone_omit", dict(b, fx=[_gw(
            "g6", 1.0, 3.0, lut={"asset_key": "media/w1e/missing.cube"})])),
        _case(work, "thermal_matte", dict(b, fx=[_fi(
            "t1", "thermal", 1.0, 2.0, stage="world",
            matte_weighted=True)])),
        _case(work, "over_type", dict(_base(captions=True), fx=[_fi(
            "o1", "crt", 1.0, 2.0, stage="over_type")])),
        _case(work, "screen_grade", dict(b, fx=[_gw(
            "s1", 1.0, 2.0, stage="screen", temperature=0.6,
            split_tone={"shadows": "#1f4e79", "highlights": "#e9b44c"},
            protect=["skin", "brand"], brand_hue="#e9b44c")])),
        _case(work, "lut_iso", dict(b, fx=[_gw(
            "s2", 2.0, 3.0, isolate={"hue": "#ff0000", "tolerance": 0.06},
            lut={"asset_key": LUT_KEY, "strength": 0.5},
            protect=["skin"])])),
    ]
    for k in KINDS:
        cases.append(_case(work, f"k_{k}", dict(b, fx=[_fi(
            f"f_{k}", k, 1.0, 2.0)]), dump_frames=[30]))
    seeded = [_fi("d1", "band_tear", 1.0, 1.6), _fi("d2", "film", 2.0, 2.6),
              _fi("d3", "xerox", 3.0, 3.4),
              _fi("d4", "night_vision", 5.0, 5.4)]
    cases.append(_case(work, "det_a", dict(b, fx=seeded)))
    cases.append(_case(work, "det_b", dict(b, fx=seeded)))
    return _run(work, cases, "r25")


def _ok(res, name):
    r = res[name]
    assert r["error"] is None, (name, r["error"])
    return r


def test_mono_window_is_frame_exact_and_lands_colour_on_the_drop(r25):
    base, mono = _ok(r25, "base"), _ok(r25, "mono")
    assert _diff(base, mono) == list(range(25, 75))     # [1.0, 3.0) @ 25
    W, H = mono["probe"]["width"], mono["probe"]["height"]
    _y, u, v = _yuv(mono["dumps"]["40"], W, H)
    assert u.min() == u.max() == 128 and v.min() == v.max() == 128
    by, bu, _bv = _yuv(base["dumps"]["40"], W, H)
    assert np.array_equal(by, _y)                        # luma untouched
    # ramp-out: 3 eased steps, the frame after the window in full colour
    devs = []
    for k in ("72", "73", "74", "75"):
        _y2, u2, _v2 = _yuv(mono["dumps"][k], W, H)
        _y3, ub, _v3 = _yuv(base["dumps"][k], W, H)
        devs.append(np.abs(u2 - 128).mean() / np.abs(ub - 128).mean())
    assert devs[0] < devs[1] < devs[2] < 1.0 and devs[3] == 1.0
    # smoothstep weights 0.84 / 0.5 / 0.16 of the grade -> colour share
    for got, want in zip(devs[:3], (0.156, 0.5, 0.844)):
        assert got == pytest.approx(want, abs=0.03)
    assert [r["rung"] for r in mono["report"]] == ["full"]


def test_ramp_in_eases_from_the_first_frame(r25):
    ri = _ok(r25, "ramp_in")
    W, H = ri["probe"]["width"], ri["probe"]["height"]
    shares = []
    for k in ("25", "26", "27"):
        _y, u, _v = _yuv(ri["dumps"][k], W, H)
        shares.append(np.abs(u - 128).mean())
    # frame 25 keeps the most colour, 27 none (full mono)
    assert shares[0] > shares[1] > shares[2] == 0.0


def test_region_grade_is_bit_exact_outside_the_region(r25):
    base, reg = _ok(r25, "base"), _ok(r25, "region")
    assert _diff(base, reg) == list(range(25, 75))
    W, H = reg["probe"]["width"], reg["probe"]["height"]
    a = _yuv(base["dumps"]["40"], W, H)
    b = _yuv(reg["dumps"]["40"], W, H)
    import fxprims
    x, y, w, h = fxprims.rect_box([0.25, 0.25, 0.75, 0.75], W, H)
    inside = np.zeros((H, W), bool)
    inside[y:y + h, x:x + w] = True
    assert np.abs(a[0] - b[0])[~inside].max() == 0      # region_locality
    assert (b[0][inside] - a[0][inside]).mean() > 8      # brighter inside
    ci = inside[::2, ::2]
    assert np.abs(a[1] - b[1])[~ci].max() == 0


def test_imported_lut_is_applied_and_a_missing_one_degrades(r25):
    base, lut = _ok(r25, "base"), _ok(r25, "lut")
    W, H = lut["probe"]["width"], lut["probe"]["height"]
    ya, _ua, _va = _yuv(base["dumps"]["40"], W, H)
    yb, _ub, _vb = _yuv(lut["dumps"]["40"], W, H)
    # the negative cube: luma mirrors about the video-range midpoint
    assert np.abs((ya + yb) - 251).mean() < 4
    red = _ok(r25, "lut_gone_reduced")
    assert red["report"][0]["rung"] == "reduced"
    assert "LUT" in red["report"][0]["reason"]
    assert _diff(base, red) == list(range(25, 75))
    om = _ok(r25, "lut_gone_omit")
    assert om["report"][0]["rung"] == "omit" and _diff(base, om) == []


def test_matte_weighted_thermal_degrades_honestly_without_the_matte(r25):
    t = _ok(r25, "thermal_matte")
    assert t["report"][0]["rung"] == "reduced"
    assert "unweighted" in t["report"][0]["reason"]
    assert _diff(_ok(r25, "base"), t) == list(range(25, 50))


@pytest.mark.parametrize("kind", KINDS)
def test_every_finish_kind_changes_exactly_its_window(r25, kind):
    base, r = _ok(r25, "base"), _ok(r25, f"k_{kind}")
    assert _diff(base, r) == list(range(25, 50)), kind
    assert [x["rung"] for x in r["report"]] == ["full"]


def test_over_type_and_screen_stages(r25):
    base = _ok(r25, "base")
    ot = _ok(r25, "over_type")
    assert ot["report"][0]["rung"] == "full"
    st = _ok(r25, "screen_grade")
    assert _diff(base, st) == list(range(25, 50))
    li = _ok(r25, "lut_iso")                 # LUT + key + protect, planar
    assert _diff(base, li) == list(range(50, 75))
    assert [r["rung"] for r in li["report"]] == ["full"]


def test_seeded_finishes_are_deterministic(r25):
    a, b = _ok(r25, "det_a"), _ok(r25, "det_b")
    assert a["frames"] == b["frames"]
    base = _ok(r25, "base")
    d = _diff(base, a)
    assert d == (list(range(25, 40)) + list(range(50, 65))
                 + list(range(75, 85)) + list(range(125, 135)))


# ── the hue key on a colour chart ────────────────────────────────────────

@pytest.fixture(scope="module")
def chart(work):
    b = _base(keep=((0.0, 4.0),), ratio=None)
    cases = [_case(work, "chart0", b, src=work["chart"], dump_frames=[40]),
             _case(work, "chart_iso", dict(b, fx=[_gw(
                 "i1", 1.0, 3.0, isolate={"hue": "#c8202a"})]),
                 src=work["chart"], dump_frames=[40]),
             _case(work, "chart_skin", dict(b, fx=[_gw(
                 "i2", 1.0, 3.0, isolate={"hue": "#c8202a"},
                 protect=["skin"])]), src=work["chart"], dump_frames=[40])]
    return _run(work, cases, "chart")


def _patch(img, col, row, W, H, sub=1):
    bw, bh = W // 3 // sub, (H * 2 // 3) // 2 // sub
    m = 6 // sub + 2
    return img[row * bh + m:(row + 1) * bh - m, col * bw + m:(col + 1) * bw - m]


def test_hue_key_keeps_red_exactly_and_greys_the_rest(chart):
    a, b = _ok(chart, "chart0"), _ok(chart, "chart_iso")
    W, H = a["probe"]["width"], a["probe"]["height"]
    ya, ua, va = _yuv(a["dumps"]["40"], W, H)
    yb, ub, vb = _yuv(b["dumps"]["40"], W, H)
    assert np.array_equal(ya, yb)                         # luma untouched
    for col, row in ((0, 0), (2, 1)):                     # the two reds
        assert np.abs(_patch(ub, col, row, W, H, 2)
                      - _patch(ua, col, row, W, H, 2)).max() == 0
        assert np.abs(_patch(vb, col, row, W, H, 2)
                      - _patch(va, col, row, W, H, 2)).max() == 0
    for col, row in ((1, 0), (2, 0), (0, 1), (1, 1)):     # blue grey skin green
        assert np.abs(_patch(ub, col, row, W, H, 2) - 128).max() <= 1
        assert np.abs(_patch(vb, col, row, W, H, 2) - 128).max() <= 1
    # the moving testsrc2 strip below: the key decides per pixel from the
    # SOURCE chroma: not the colour -> exact grey, the colour -> untouched
    # (2 chroma pixels from a kept one the smoothed key may blend)
    import gradewin
    import lutlib
    top = H // 3
    ys = ya.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
    w_all = lutlib.hue_weight(gradewin.hue_key("#c8202a"), ua, va, ys)
    near = w_all > 0.02
    for _ in range(2):
        near = (near | np.roll(near, 1, 0) | np.roll(near, -1, 0)
                | np.roll(near, 1, 1) | np.roll(near, -1, 1))
    off = ~near[top:]
    on = w_all[top:] > 0.98
    sat = np.hypot(ub[top:] - 128.0, vb[top:] - 128.0)
    assert off.sum() > 1000 and sat[off].max() <= 2
    if on.sum():
        assert np.abs(ub[top:][on] - ua[top:][on]).mean() < 2


def test_protect_skin_keeps_faces_inside_a_mono_world(chart):
    a, b = _ok(chart, "chart0"), _ok(chart, "chart_skin")
    W, H = a["probe"]["width"], a["probe"]["height"]
    _ya, ua, va = _yuv(a["dumps"]["40"], W, H)
    _yb, ub, vb = _yuv(b["dumps"]["40"], W, H)
    skin = (0, 1)
    assert np.abs(_patch(ub, *skin, W, H, 2) - _patch(ua, *skin, W, H, 2)
                  ).max() <= 1
    assert np.abs(_patch(ub, 1, 0, W, H, 2) - 128).max() <= 1    # blue mono


# ── frame exactness at every rate ────────────────────────────────────────

RATES = {"2997": ("30000/1001", 29.97002997), "24": ("24", 24.0),
         "5994": ("60000/1001", 59.94005994)}


@pytest.fixture(scope="module")
def rates(work):
    cases = []
    for tag, (r, fps) in RATES.items():
        k = 97
        s, e = round(k / fps, 2), round((k + 1) / fps, 2)
        b = _base(keep=((0.0, 4.0), (5.0, 7.0)))
        cases.append(_case(work, f"b{tag}", b, src=work["src"][r]))
        cases.append(_case(work, f"g{tag}", dict(b, fx=[
            _gw("g", s, e, ev=1.0),
            _fi("f", "solarise", round((k + 10) / fps, 2),
                round((k + 12) / fps, 2))]), src=work["src"][r]))
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"     # previews at the source rate
    try:
        return _run(work, cases, "rates")
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old


@pytest.mark.parametrize("tag", sorted(RATES))
def test_frame_exact_at_every_rate(rates, tag):
    b, g = _ok(rates, f"b{tag}"), _ok(rates, f"g{tag}")
    num, den = (int(x) for x in b["probe"]["r_frame_rate"].split("/"))
    assert abs(num / den - RATES[tag][1]) < 0.01
    assert _diff(b, g) == [97, 107, 108], tag


# ── the window clock: no frame shown twice, at any rate ────────────────
#
# A finish that reads a precomputed map (a looped still), a synthesized
# plate (black, grain) or a region still syncs those streams with the
# window's frames. Stamped on different time bases (1/25 for a PNG, 1/fps
# for a lavfi source, microseconds for the program after its concat) they
# disagreed by a rounding microsecond, framesync emitted BOTH events and the
# window played its frames twice — an LCD window at 30 fps ran at half
# speed (found on the AT-1E-5 FX-UI capture; 25 fps hid it). testsrc2
# changes every frame and the encodes are lossless, so a repeated frame is
# a repeated md5.

CLOCKED = (("lcd", {}), ("crt", {}), ("led", {}), ("halftone", {}),
           ("film", {}), ("kaleidoscope", {}), ("blueprint", {}),
           ("night_vision", {"palette": "goggles"}))


@pytest.fixture(scope="module")
def clocked(work):
    cases = []
    for tag, r in (("2997", "30000/1001"), ("5994", "60000/1001"),
                   ("24", "24")):
        b = _base(keep=((0.0, 4.0), (5.0, 7.0)))
        for i, (k, kw) in enumerate(CLOCKED):
            cases.append(_case(work, f"{tag}_{k}", dict(b, fx=[_fi(
                f"c_{k}", k, 1.0, 2.0, **kw)]), src=work["src"][r]))
        cases.append(_case(work, f"{tag}_region", dict(b, fx=[_gw(
            "c_rg", 1.0, 2.0, ev=1.0,
            region={"shape": "ellipse", "box": [0.2, 0.2, 0.8, 0.8]})]),
            src=work["src"][r]))
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"
    try:
        return _run(work, cases, "clocked")
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old


@pytest.mark.parametrize("tag", ("2997", "5994", "24"))
@pytest.mark.parametrize("kind", [k for k, _ in CLOCKED] + ["region"])
def test_a_window_never_shows_a_frame_twice(clocked, tag, kind):
    r = _ok(clocked, f"{tag}_{kind}")
    assert all(x["rung"] == "full" for x in r["report"]), r["report"]
    fr = r["frames"]
    fps = {"2997": 29.97002997, "5994": 59.94005994, "24": 24.0}[tag]
    f0, f1 = round(1.0 * fps), round(2.0 * fps)
    repeats = [k for k in range(f0, f1 + 2) if fr[k] == fr[k - 1]]
    assert repeats == [], (kind, tag, repeats)


# ── brand integrity (AT-0B-5 pattern) ────────────────────────────────────

@pytest.fixture(scope="module")
def brand(work):
    base = _base()
    fx = [_gw("bw", 0.0, 8.0, saturation=0.0, contrast=1.3),
          _fi("bc", "crt", 0.0, 8.0, stage="over_type"),
          _fi("bl", "led", 2.0, 4.0, stage="pre_captions",
              region={"shape": "rect", "box": [0, 0, 1, 1]})]
    cases = [_case(work, "plain0", base, want_wm=True,
                   dump_frames=[60, 150]),
             _case(work, "plain1", dict(base, fx=fx), want_wm=True,
                   dump_frames=[60, 150])]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        return _run(work, cases, "brand")
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def test_brand_end_card_is_md5_identical(brand):
    a, b = _ok(brand, "plain0"), _ok(brand, "plain1")
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert a["frames"][200:] == b["frames"][200:]
    assert set(_diff(a, b)) == set(range(200))
    assert [r["rung"] for r in b["report"]] == ["full"] * 3


def test_brand_watermark_robot_is_drawn_after_every_finish(brand):
    from test_w0b_render import _robot_opaque
    W, H = 270, 480
    m = _robot_opaque(W, H)
    assert m.sum() > 40
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    for k in ("60", "150"):
        a = _yuv(brand["plain0"]["dumps"][k], W, H)
        b = _yuv(brand["plain1"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0
        # The mark's CHROMA is the renderer's own 4:2:0 composite: its
        # chroma samples take a few levels from the picture next to the
        # robot (measured: a W0 fx_probe world negate shifts them by up to
        # 7, this grade + CRT by 3). Luma is the exact check; chroma is
        # held to the probe's own figure.
        assert np.abs(a[1] - b[1])[cm].max() <= 7
        outside = ~m
        outside[:60, :] = False
        assert np.abs(a[0] - b[0])[outside].mean() > 5
