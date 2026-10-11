"""Track 1C on real renders: region effects and motion-graphic composites.

Every frame-level case renders through ``renderer.render_edl`` in a child
(tests/w0b_probe.py) with LOSSLESS encodes, so decoded frames are the
filtergraph's own:

  * region effects (every effect, every stage, static and keyframed
    shapes, a glyph region) change only their own frames and, inside them,
    only their region: ``region_locality`` = 0 outside;
  * a 1-frame region effect at program frame 120 changes exactly that
    frame on 25, 29.97, 24 and 59.94 fps programs;
  * a composite lands on exactly the frames the plain graphic does
    (``{"blend": "normal"}`` is frame-md5-identical to no composite), a
    difference blend is |plate - ink|, screen light never darkens, a fill
    shows its footage only inside the glyphs (outside: the picture's own),
    fill_mode outside makes the glyphs windows, a field covers the rest, the
    clip region clips;
  * the ghost pass (composite_behind, the seam 1A's renderer calls) draws
    outlines only where the subject covers the letters;
  * brand integrity: with composites on both motion layers and region
    effects at every stage, the end card is md5-identical and the
    watermark robot untouched;
  * the fault matrix (AT-0C-3): every pinned fault x every 1C spec renders
    with the program's exact length.

Motion-graphic cases need Chromium (CI installs the headless shell).
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

W, H = 270, 480                       # the 9:16 preview of the probe lane
IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": 12.0}}
FILL_KEY = "media/w1c/fill.mp4"
BOX = [0.2, 0.3, 0.7, 0.6]


def _chromium():
    try:
        import motion_engine
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not _chromium(),
                                   reason="needs the motion engine")


def _base(keep=((0.0, 4.0), (5.0, 9.0))):
    return {"keep": [list(k) for k in keep], "captions": {"enabled": False},
            "frame": {"ratio": "9:16"}}


def _region(fid, effect, stage="pre_captions", start=1.0, end=1.4, **kw):
    d = {"id": fid, "type": "region", "stage": stage, "start": start,
         "end": end, "effect": effect,
         "region": kw.pop("region", {"shape": "rect", "box": BOX})}
    d.update(kw)
    return d


def _word(mid="m1", start=1.0, end=3.0, **kw):
    d = {"id": mid, "template": "word_slam", "start": start, "end": end,
         "params": {"text": "HELLO", "entrance": "pop", "exit": "cut",
                    "color": "#FFFFFF", "y": 0.45}}
    d.update(kw)
    return d


def _yuv(path, w=W, h=H):
    a = np.fromfile(path, np.uint8).astype(int)
    y = a[:w * h].reshape(h, w)
    u = a[w * h:w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2)
    v = a[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2)
    return y, u, v


def _rgb(path, w=W, h=H):
    y, u, v = _yuv(path, w, h)
    u = np.repeat(np.repeat(u, 2, 0), 2, 1) - 128.0
    v = np.repeat(np.repeat(v, 2, 0), 2, 1) - 128.0
    yy = (y - 16.0) * 1.164
    r = yy + 1.596 * v
    g = yy - 0.392 * u - 0.813 * v
    b = yy + 2.017 * u
    return np.clip(np.stack([r, g, b], -1), 0, 255)


def _box_mask(box=BOX, pad=0):
    import fxprims
    x, y, w, h = fxprims.rect_box(box, W, H)
    m = np.zeros((H, W), bool)
    m[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = True
    return m


def _diff(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1c_render")
    src = w0b_probe.make_source(str(d / "src25.mp4"), rate="25")
    fill = w0b_probe.make_source(str(d / "fill.mp4"), w=480, h=270,
                                 rate="30", dur=10.0, freq=300)
    return {"dir": str(d), "src": src, "fill": fill,
            "cut": _cut_source(str(d / "cut25.mp4"))}


def _cut_source(path):
    """A source whose every frame differs (luma steps with the frame index)
    and whose two halves differ in colour (red-ish before 4.48 s, blue-ish
    after): fills that read another moment or the shot across a cut are
    told apart by colour and by luma."""
    if os.path.exists(path):
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=640x360:r=25:d=12", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=12",
         "-vf", "geq=lum='if(lt(N,112),50+mod(N*4,80),120+mod(N*4,80))'"
         ":cb='if(lt(N,112),96,200)':cr='if(lt(N,112),210,90)'",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "25",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)
    return path


def _case(work, name, edl, **kw):
    c = {"name": name, "src": kw.pop("src", work["src"]), "idx": IDX,
         "edl": edl, "asset_locals": {FILL_KEY: work["fill"]}}
    c.update(kw)
    return c


# ── region effects (no browser) ─────────────────────────────────────────

EFFECTS = {
    "negate": {}, "solarise": {}, "solarise_c": {"colour": "#ff00ff"},
    "mono": {}, "posterize": {}, "threshold": {}, "blur": {},
    "pixelate": {}, "exposure": {"ev": -1.5}, "tint": {"colour": "#2266ff"},
    "ribbed_glass": {}, "refraction": {}, "heat": {}}


@pytest.fixture(scope="module")
def regions_run(work):
    cases = [_case(work, "base", _base(), dump_frames=[30, 50])]
    for name, kw in EFFECTS.items():
        eff = name.split("_c")[0] if name != "solarise_c" else "solarise"
        cases.append(_case(work, f"e_{name}", dict(_base(), fx=[
            _region(f"r_{name}", eff, **kw)]), dump_frames=[30]))
    keyed = {"shape": "ellipse", "keys": [
        {"t": 0, "points": [[0.1, 0.2], [0.5, 0.5]]},
        {"t": 2.0, "points": [[0.4, 0.4], [0.9, 0.8]], "ease": "in_out"}]}
    cases.append(_case(work, "keyed", dict(_base(), fx=[
        _region("rk", "negate", start=1.0, end=3.0, region=keyed)]),
        dump_frames=[50]))
    cases.append(_case(work, "stages", dict(_base(), fx=[
        _region("w", "negate", stage="world",
                region={"shape": "rect", "box": [0.0, 0.0, 0.3, 0.3]}),
        _region("s", "negate", stage="screen",
                region={"shape": "rect", "box": [0.35, 0.0, 0.65, 0.3]}),
        _region("o", "negate", stage="over_type",
                region={"shape": "rect", "box": [0.7, 0.0, 1.0, 0.3]})]),
        dump_frames=[30]))
    cases.append(_case(work, "subst", dict(_base(), fx=[
        _region("rm", "ribbed_glass", device="region_material")]),
        faults=["ffmpeg_filter_missing:displace"], dump_frames=[30]))
    cases.append(_case(work, "material_dev", dict(_base(), fx=[
        _region("rm", "refraction", device="region_material")]),
        dump_frames=[30]))
    cases.append(_case(work, "no_face", dict(_base(), fx=[
        _region("rf", "blur", region={"shape": "face"})])))
    return w0b_probe.run(cases, os.path.join(work["dir"], "regions"))


@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_region_effect_touches_only_its_frames_and_region(regions_run, name):
    base, r = regions_run["base"], regions_run[f"e_{name}"]
    assert base["error"] is None and r["error"] is None, r["error"]
    assert [x["rung"] for x in r["report"]] == ["full"], r["report"]
    assert len(r["frames"]) == len(base["frames"]) == 200
    changed = _diff(base, r)
    assert changed and set(changed) <= set(range(25, 35)), changed
    a = _yuv(base["dumps"]["30"])
    b = _yuv(r["dumps"]["30"])
    inside = _box_mask()
    # region_locality = 0: luma outside the box is the picture's own
    assert np.abs(a[0] - b[0])[~inside].max() == 0, name
    # chroma outside the box (4:2:0 cells clear of its edge) untouched too
    ci = _box_mask(pad=2)[::2, ::2]
    assert np.abs(a[1] - b[1])[~ci].max() == 0, name
    # ...and the effect is really there
    d = np.abs(a[0] - b[0])[inside].mean() + \
        np.abs(a[1] - b[1])[_box_mask()[::2, ::2]].mean()
    assert d > 1.0, (name, d)


def test_negate_and_solarise_are_the_documented_maps(regions_run):
    import fxprims
    x, y, w, h = fxprims.rect_box(BOX, W, H)
    a = _rgb(regions_run["base"]["dumps"]["30"])[y + 4:y + h - 4,
                                                 x + 4:x + w - 4]
    n = _rgb(regions_run["e_negate"]["dumps"]["30"])[y + 4:y + h - 4,
                                                     x + 4:x + w - 4]
    assert np.abs((255 - a) - n).mean() < 12
    s = _rgb(regions_run["e_solarise_c"]["dumps"]["30"])[y + 4:y + h - 4,
                                                         x + 4:x + w - 4]
    # magenta duotone: green stays below red and blue
    assert (s[..., 1] <= np.minimum(s[..., 0], s[..., 2]) + 12).mean() > 0.9


def test_keyframed_region_follows_its_keys(regions_run):
    import compositor
    base, r = regions_run["base"], regions_run["keyed"]
    assert r["error"] is None and r["report"][0]["rung"] == "full"
    a = _yuv(base["dumps"]["50"])[0]
    b = _yuv(r["dumps"]["50"])[0]
    pts = compositor.keyed_points(
        {"keys": [{"t": 0, "points": [[0.1, 0.2], [0.5, 0.5]]},
                  {"t": 2.0, "points": [[0.4, 0.4], [0.9, 0.8]],
                   "ease": "in_out"}]}, 1.0)
    (x0, y0), (x1, y1) = pts
    cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
    rx, ry = (x1 - x0) / 2 * W, (y1 - y0) / 2 * H
    yy, xx = np.mgrid[0:H, 0:W]
    e = ((xx + 0.5 - cx) / rx) ** 2 + ((yy + 0.5 - cy) / ry) ** 2
    assert np.abs(a - b)[e > 1.15].max() == 0
    assert np.abs(a - b)[e < 0.8].mean() > 20


def test_region_effects_run_at_every_picture_stage(regions_run):
    r = regions_run["stages"]
    assert r["error"] is None
    assert sorted(x["rung"] for x in r["report"]) == ["full"] * 3
    a = _yuv(regions_run["base"]["dumps"]["30"])[0]
    b = _yuv(r["dumps"]["30"])[0]
    for box in ([0.0, 0.0, 0.3, 0.3], [0.35, 0.0, 0.65, 0.3],
                [0.7, 0.0, 1.0, 0.3]):
        m = _box_mask(box)
        assert np.abs(a - b)[m].mean() > 20, box
    assert np.abs(a - b)[int(H * 0.32):, :].max() == 0


def test_refraction_bevels_the_edges_of_its_own_region(regions_run):
    """The bevel comes from the region's mask: the edges facing up-left
    catch light, the far edges fall into shadow, and outside the region
    nothing moves (the parametrised locality test above)."""
    import fxprims
    x, y, w, h = fxprims.rect_box(BOX, W, H)
    a = _yuv(regions_run["base"]["dumps"]["30"])[0].astype(float)
    b = _yuv(regions_run["e_refraction"]["dumps"]["30"])[0].astype(float)
    d = b - a
    # the two pixel rows inside the top and bottom edges (the probe's
    # bars are vertical, so the vertical bend leaves them as they were and
    # what changes there is the light)
    top = d[y:y + 2, x + 6:x + w - 6].mean()
    bottom = d[y + h - 2:y + h, x + 6:x + w - 6].mean()
    assert top > 10 and bottom < -2, (top, bottom)
    # deep inside, away from every edge, no sheen at all (the lens and
    # ripple bend the picture; its mean level stays)
    core = d[y + 12:y + h - 12, x + 12:x + w - 12]
    assert abs(core.mean()) < 6, core.mean()


def test_a_material_degrades_to_its_substitute_never_the_render(regions_run):
    r = regions_run["subst"]
    assert r["error"] is None
    row = r["report"][0]
    assert row["rung"] == "substitute" and "region_soften" in row["reason"]
    d = regions_run["material_dev"]
    assert d["error"] is None and d["report"][0]["rung"] == "full"
    f = regions_run["no_face"]
    assert f["error"] is None and f["report"][0]["rung"] == "omit"
    assert "face region is not available" in f["report"][0]["reason"]
    assert f["frames"] == regions_run["base"]["frames"]


# ── one frame at four rates ─────────────────────────────────────────────

RATES = {"25": 25.0, "30000/1001": 29.97, "24": 24.0, "60000/1001": 59.94}


@pytest.fixture(scope="module")
def rates(work):
    cases = []
    for rate, fps in RATES.items():
        src = w0b_probe.make_source(
            os.path.join(work["dir"], f"src_{rate.replace('/', '_')}.mp4"),
            rate=rate, dur=8.0)
        f3 = round(fps, 3)
        base = _base(keep=((0.0, 3.0), (4.0, 7.0)))
        item = _region("r1", "negate", stage="world",
                       start=round(120 / f3, 2), end=round(121 / f3, 2))
        cases.append(_case(work, f"b{fps}", base, src=src))
        cases.append(_case(work, f"n{fps}", dict(base, fx=[item]), src=src))
        cases.append(_case(work, f"k{fps}", dict(base, fx=[dict(
            item, stage="over_type", region={"shape": "ellipse", "keys": [
                {"t": 0, "points": [[0.2, 0.2], [0.8, 0.8]]},
                {"t": 1, "points": [[0.3, 0.3], [0.7, 0.7]]}]})]), src=src))
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], "rates"))
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old


@pytest.mark.parametrize("fps", list(RATES.values()))
def test_one_frame_region_effect_on_every_rate(rates, fps):
    b, n, k = rates[f"b{fps}"], rates[f"n{fps}"], rates[f"k{fps}"]
    assert b["error"] is None and n["error"] is None and k["error"] is None
    assert len(b["frames"]) == len(n["frames"]) >= 140
    assert _diff(b, n) == [120]
    assert _diff(b, k) == [120]


# ── composites on motion graphics (browser) ─────────────────────────────

@pytest.fixture(scope="module")
def comp(work):
    if not _chromium():
        pytest.skip("needs the motion engine")
    base = _base()
    cut = work["cut"]
    cases = [_case(work, "base", base,
                   dump_frames=[38, 40, 50, 95, 98, 101, 102, 110]),
             _case(work, "cbase", base, src=cut,
                   dump_frames=[38, 50, 95, 98, 101, 102, 110]),
             _case(work, "plain", dict(base, motion=[_word()]),
                   dump_frames=[50])]
    comps = {
        # the picture as a composite passes it on (every motion graphic
        # puts the program into an alpha pixel-format group; Wave 0 §4.6),
        # with nothing drawn: the reference for "outside, untouched"
        "zero": {"opacity": 0.0},
        "normal": {"blend": "normal"},
        "diff": {"blend": "difference"},
        "half": {"opacity": 0.5},
        "red": {"fill": {"kind": "colour", "colour": "#ff0000"}},
        "outside": {"fill": {"kind": "colour", "colour": "black"},
                    "fill_mode": "outside"},
        "field": {"fill": {"kind": "colour", "colour": "#ff0000"},
                  "field": "black"},
        "asset": {"fill": {"kind": "asset", "asset_key": FILL_KEY}},
        "clip": {"region": {"shape": "rect", "box": [0.0, 0.0, 0.5, 1.0]}},
    }
    for name, c in comps.items():
        cases.append(_case(work, f"c_{name}", dict(base, motion=[
            _word(composite=c)]), dump_frames=[50]))
    cases += [
        _case(work, "c_program", dict(base, motion=[_word(composite={
            "fill": {"kind": "program", "offset_s": -0.5}})]), src=cut,
            dump_frames=[50]),
        _case(work, "c_cplain", dict(base, motion=[_word()]), src=cut,
              dump_frames=[50])]
    leak = {"id": "lk", "template": "light_leak", "start": 1.0, "end": 2.2,
            "params": {"palette": "warm", "intensity": 1.0}}
    cases += [_case(work, "leak", dict(base, motion=[leak]),
                    dump_frames=[40]),
              _case(work, "leak_screen", dict(base, motion=[dict(
                  leak, composite={"blend": "screen"})]), dump_frames=[40]),
              _case(work, "leak_zero", dict(base, motion=[dict(
                  leak, composite={"opacity": 0.0})]), dump_frames=[40])]
    tw = {"id": "tw", "template": "type_window", "start": 3.0, "end": 4.0,
          "params": {"text": "AB", "reveal": "hold", "size": 0.9}}
    cases += [
        _case(work, "tw_plain", dict(base, motion=[tw]), src=cut,
              dump_frames=[95]),
        _case(work, "tw_next", dict(base, motion=[dict(
            tw, composite={"fill": {"kind": "next"}})]), src=cut,
            dump_frames=[95]),
        _case(work, "tw_prev", dict(base, motion=[dict(
            tw, start=4.0, end=5.0, composite={"fill": {"kind": "previous"}}
        )]), src=cut, dump_frames=[110]),
        _case(work, "tw_plain_prev", dict(base, motion=[dict(
            tw, start=4.0, end=5.0)]), src=cut, dump_frames=[110]),
        _case(work, "glyph", dict(base, motion=[_word()], fx=[_region(
            "rg", "negate", stage="over_type", start=1.0, end=3.0,
            region={"shape": "glyph", "ref": "m1"})]), dump_frames=[50])]
    return w0b_probe.run(cases, os.path.join(work["dir"], "comp"))


def _plate(comp, fmt=_rgb):
    """Frame 50 of the picture with nothing drawn, through the same
    pixel-format path as every composite (opacity 0)."""
    return fmt(comp["c_zero"]["dumps"]["50"])


def _ink(comp):
    """Where the plain graphic draws (its glyph pixels, eroded), from the
    plain render against the bare plate."""
    a = _plate(comp)
    p = _rgb(comp["plain"]["dumps"]["50"])
    ink = (p.min(-1) > 235) & (np.abs(p - a).max(-1) > 30)
    core = ink & np.roll(ink, 1, 0) & np.roll(ink, -1, 0) & \
        np.roll(ink, 1, 1) & np.roll(ink, -1, 1)
    core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & \
        np.roll(core, 1, 1) & np.roll(core, -1, 1)
    return ink, core


def _touched(comp, grow=3):
    a = _plate(comp, _yuv)[0]
    p = _yuv(comp["plain"]["dumps"]["50"])[0]
    t = np.abs(a - p) > 0
    for _ in range(grow):
        t = t | np.roll(t, 1, 0) | np.roll(t, -1, 0) | np.roll(t, 1, 1) | \
            np.roll(t, -1, 1)
    return t


@needs_browser
def test_a_normal_composite_lands_on_the_plain_graphics_frames(comp):
    for k in ("base", "plain", "c_normal"):
        assert comp[k]["error"] is None, (k, comp[k]["error"])
    assert comp["c_normal"]["frames"] == comp["plain"]["frames"]
    assert _diff(comp["base"], comp["plain"]) == \
        _diff(comp["base"], comp["c_normal"])


@needs_browser
def test_difference_blend_is_plate_minus_ink(comp):
    a = _plate(comp)
    d = _rgb(comp["c_diff"]["dumps"]["50"])
    _ink_px, core = _ink(comp)
    assert core.sum() > 200
    want = np.abs(a - 255.0)                       # white ink
    assert np.abs(d - want)[core].mean() < 10
    t = _touched(comp)
    ya, yd = _plate(comp, _yuv)[0], _yuv(comp["c_diff"]["dumps"]["50"])[0]
    assert np.abs(ya - yd)[~t].max() == 0         # nothing else moved


@needs_browser
def test_opacity_and_clip_region(comp):
    a = _plate(comp, _yuv)[0].astype(float)
    p = _yuv(comp["plain"]["dumps"]["50"])[0].astype(float)
    h = _yuv(comp["c_half"]["dumps"]["50"])[0].astype(float)
    _i, core = _ink(comp)
    assert np.abs(h - (a + p) / 2)[core].mean() < 6
    c = _yuv(comp["c_clip"]["dumps"]["50"])[0].astype(float)
    left = np.zeros((H, W), bool)
    left[:, :W // 2 - 2] = True
    right = np.zeros((H, W), bool)
    right[:, W // 2 + 2:] = True
    assert np.abs(c - p)[left].max() <= 2
    assert np.abs(c - a)[right].max() == 0


@needs_browser
def test_fills_inside_outside_and_field(comp):
    a = _plate(comp)
    ink, core = _ink(comp)
    red = _rgb(comp["c_red"]["dumps"]["50"])
    assert np.abs(red - [255, 0, 0])[core].mean() < 20
    t = _touched(comp)
    assert np.abs(red - a)[~t].max() <= 3
    out = _rgb(comp["c_outside"]["dumps"]["50"])
    # glyph windows show the picture; everything else is the black fill
    assert np.abs(out - a)[core].mean() < 8
    far = ~t
    assert out[far].max() < 24
    fld = _rgb(comp["c_field"]["dumps"]["50"])
    assert np.abs(fld - [255, 0, 0])[core].mean() < 20
    assert fld[far].max() < 24


@needs_browser
def test_asset_fill_is_the_footage_inside_the_glyphs(comp, work):
    got = _rgb(comp["c_asset"]["dumps"]["50"])
    # the fill as the compositor frames it: item-local 1.0 s, cover-scaled
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", work["fill"], "-vf",
         f"fps=25,select=eq(n\\,25),scale={W}:{H}:force_original_aspect_"
         f"ratio=increase,crop={W}:{H},format=rgb24", "-frames:v", "1",
         "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    want = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(float)
    _i, core = _ink(comp)
    mse = ((got - want) ** 2)[core].mean()
    psnr = 10 * np.log10(255.0 ** 2 / max(mse, 1e-9))
    assert psnr > 24, psnr                     # chroma 4:2:0 in thin glyphs


@needs_browser
def test_program_fill_is_the_picture_half_a_second_earlier(comp):
    got = _yuv(comp["c_program"]["dumps"]["50"])[0].astype(float)
    _i, core = _ink(comp)
    then = _yuv(comp["cbase"]["dumps"]["38"])[0]   # 0.5 s = 12.5 frames back
    now = _yuv(comp["cbase"]["dumps"]["50"])[0]
    err_then = np.abs(got - then)[core].mean()
    assert err_then < 3 and err_then < np.abs(got - now)[core].mean()


@needs_browser
def test_screen_light_never_darkens(comp):
    import qa_metrics
    import compositor  # noqa: F401 — registers no_darker
    assert comp["leak"]["error"] is None
    assert comp["leak_screen"]["error"] is None
    a = _yuv(comp["leak_zero"]["dumps"]["40"])[0]
    s = _yuv(comp["leak_screen"]["dumps"]["40"])[0]
    r = qa_metrics.evaluate("no_darker", with_fx=s, without_fx=a)
    assert r["pass"], r
    assert (s - a).max() > 30                     # the light is there


@needs_browser
def test_next_and_previous_fills_cross_the_cut(comp):
    """The cut is at program 4.0 s (frame 100). 'next' before it shows the
    shot after the cut (its edge frame held); 'previous' after it the shot
    before (held)."""
    base = comp["cbase"]["dumps"]
    nxt = _rgb(comp["tw_next"]["dumps"]["95"])
    ink = _rgb(comp["tw_plain"]["dumps"]["95"]).min(-1) > 235
    assert ink.sum() > 500
    b95, b101, b102 = (_rgb(base[k]) for k in ("95", "101", "102"))
    after = np.minimum(np.abs(nxt - b101), np.abs(nxt - b102))[ink].mean()
    before = np.abs(nxt - b95)[ink].mean()
    assert after < 6 and before > 40, (after, before)
    prv = _rgb(comp["tw_prev"]["dumps"]["110"])
    pink = _rgb(comp["tw_plain_prev"]["dumps"]["110"]).min(-1) > 235
    assert pink.sum() > 500
    b98, b110 = _rgb(base["98"]), _rgb(base["110"])
    assert np.abs(prv - b98)[pink].mean() < 6
    assert np.abs(prv - b110)[pink].mean() > 40


@needs_browser
def test_glyph_region_draws_inside_the_letters_only(comp):
    p = _yuv(comp["plain"]["dumps"]["50"])[0]
    g = _yuv(comp["glyph"]["dumps"]["50"])[0]
    assert comp["glyph"]["error"] is None
    assert comp["glyph"]["report"][0]["rung"] == "full"
    ink, core = _ink(comp)
    assert np.abs(p - g)[core].mean() > 100       # white letters negated
    t = _touched(comp)
    # the effect follows the glyph alpha (soft shadow fringe included):
    # pixels the plain graphic left untouched move at most a level there,
    # and nothing moves beyond the graphic's reach
    assert np.abs(p - g)[~t].max() <= 2
    assert np.abs(p - g)[~_touched(comp, 14)].max() == 0


# ── degrade, never fail: fills and clip regions the render cannot use ──

GIF_KEY, MP3_KEY = "media/w1c/loop.gif", "media/w1c/voice.m4a"


@pytest.fixture(scope="module")
def degrade(work):
    """Real renders of composites whose ingredients the render cannot use:
    an audio upload as a fill (no picture: dropped), an animated GIF fill
    (a video: its demuxer refuses -loop) and a face clip region nothing
    serves (the graphic is left out, never drawn unclipped)."""
    if not _chromium():
        pytest.skip("needs the motion engine")
    gif = os.path.join(work["dir"], "loop.gif")
    mp3 = os.path.join(work["dir"], "voice.m4a")
    for path, src in ((gif, "testsrc=s=96x54:r=10:d=1"),
                      (mp3, "sine=d=2")):
        if not os.path.exists(path):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", src, path], check=True,
                           capture_output=True)
    locals_ = {FILL_KEY: work["fill"], GIF_KEY: gif, MP3_KEY: mp3}
    base = _base()
    cases = [
        _case(work, "d_base", base, dump_frames=[50]),
        _case(work, "d_plain", dict(base, motion=[_word()]),
              dump_frames=[50]),
        _case(work, "d_gif", dict(base, motion=[_word(composite={
            "fill": {"kind": "asset", "asset_key": GIF_KEY, "loop": True}})]),
            asset_locals=locals_, dump_frames=[50]),
        _case(work, "d_mp3", dict(base, motion=[_word(composite={
            "fill": {"kind": "asset", "asset_key": MP3_KEY}})]),
            asset_locals=locals_, dump_frames=[50]),
        _case(work, "d_face", dict(base, motion=[_word(composite={
            "region": {"shape": "face"}})]), dump_frames=[50])]
    return w0b_probe.run(cases, os.path.join(work["dir"], "degrade"))


@needs_browser
def test_unusable_fill_media_never_fails_the_render(degrade):
    for k in ("d_base", "d_plain", "d_gif", "d_mp3", "d_face"):
        assert degrade[k]["error"] is None, (k, degrade[k]["error"])
    # an audio fill: dropped, the graphic in its own colour (the plain one)
    assert degrade["d_mp3"]["frames"] == degrade["d_plain"]["frames"]
    # a GIF fill: its picture inside the letters (not the white ink)
    plain = _rgb(degrade["d_plain"]["dumps"]["50"])
    gif = _rgb(degrade["d_gif"]["dumps"]["50"])
    ink = plain.min(-1) > 235
    assert ink.sum() > 200
    assert np.abs(gif - plain)[ink].mean() > 40
    # a face clip region nothing serves: the graphic is left out
    assert degrade["d_face"]["frames"] == degrade["d_base"]["frames"]


# ── the ghost pass (composite_behind, 1A's renderer seam) ────────────────

@needs_browser
def test_ghost_outlines_only_where_the_subject_hides_letters(tmp_path):
    import compositor
    import motion_engine
    import motion_layer
    import motion_templates
    fps = 25.0
    item = {"id": "g1", "template": "word_slam", "start": 0.0, "end": 1.6,
            "layer": "behind_subject",
            "params": {"text": "DREAM", "entrance": "pop", "exit": "cut",
                       "color": "#FF2020", "y": 0.42, "width": 0.85},
            "composite": {"ghost": {"opacity": 0.8, "width_px": 3}}}
    job = motion_templates.build_job(dict(item), W, H, fps)
    clip = motion_engine.render_jobs([job], str(tmp_path / "motion"))[0]
    pic = str(tmp_path / "pic.mp4")
    mask = str(tmp_path / "mask.mp4")
    run = dict(check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x303030:s={W}x{H}:r={fps}:d=2", "-pix_fmt",
                    "yuv420p", "-c:v", "libx264", "-qp", "0", pic], **run)
    # the "subject": a white block over the middle of the word
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s={W}x{H}:r={fps}:d=2", "-vf",
                    f"drawbox=x={W // 3}:y=0:w={W // 3}:h={H}:c=white:t=fill,"
                    "format=gray", "-c:v", "ffv1", mask], **run)
    args = ["-i", pic, "-i", mask, "-i", clip.path]
    inputs, nxt = compositor.prepare_composites(
        {}, [(2, item, clip)], workdir=str(tmp_path), W=W, H=H, fps=fps,
        args=args, next_idx=3)
    side = inputs[0][2].composite
    assert side.stroke and side.ghost, side.notes

    def render(with_ghost, out):
        parts = ["[1:v]format=gray[m]"]
        it, cl = inputs[0][1], inputs[0][2]
        if not with_ghost:
            cl = __import__("dataclasses").replace(cl)
            cl.composite = __import__("dataclasses").replace(
                side, stroke=None, ghost=None)
        lab = motion_layer.composite_behind(
            parts, "0:v", "m", 0, fps=fps, motion=(2, it, cl))
        parts.append(f"[{lab}]format=yuv420p[out]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args,
                        "-filter_complex", ";".join(parts), "-map", "[out]",
                        "-frames:v", "30", "-c:v", "libx264", "-qp", "0",
                        out], **run)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf",
                              "select=eq(n\\,20),format=rgb24", "-frames:v",
                              "1", "-f", "rawvideo", "-"], **run).stdout
        return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(float)
    g = render(True, str(tmp_path / "g.mp4"))
    n = render(False, str(tmp_path / "n.mp4"))
    subject = np.zeros((H, W), bool)
    subject[:, W // 3 + 2:2 * W // 3 - 2] = True
    d = np.abs(g - n).max(-1)
    assert d[subject].max() > 40                       # outlines drawn
    outside = np.zeros((H, W), bool)
    outside[:, :W // 3 - 2] = True
    outside[:, 2 * W // 3 + 2:] = True
    assert d[outside].max() == 0                       # nothing else
    # thin outlines, not filled letters: few subject pixels change
    assert (d[subject] > 40).mean() < 0.08


# ── brand integrity (owner rule 4) ──────────────────────────────────────

@pytest.fixture(scope="module")
def brand(work):
    if not _chromium():
        pytest.skip("needs the motion engine")
    base = _base()
    every = [_region("w", "negate", stage="world", start=0.0, end=8.0,
                     region={"shape": "rect", "box": [0.0, 0.0, 1.0, 1.0]}),
             _region("s", "ribbed_glass", stage="screen", start=0.0, end=8.0,
                     region={"shape": "rect", "box": [0.0, 0.0, 1.0, 1.0]}),
             _region("p", "solarise", stage="pre_captions", start=0.0,
                     end=8.0, region={"shape": "rect",
                                      "box": [0.0, 0.0, 1.0, 1.0]}),
             _region("o", "negate", stage="over_type", start=0.0, end=8.0,
                     region={"shape": "rect", "box": [0.0, 0.0, 1.0, 1.0]})]
    motion = [_word(start=0.5, end=3.0, composite={"blend": "difference"}),
              _word("m2", start=3.0, end=8.0, layer="below_captions",
                    composite={"fill": {"kind": "asset",
                                        "asset_key": FILL_KEY},
                               "field": "#ffffff"})]
    # the reference carries the same graphics without composites: a motion
    # graphic moves the program into an alpha pixel-format group, and the
    # watermark overlay negotiates its format from what precedes it (Wave 0
    # §6 item 14) — the comparison isolates what 1C adds
    plain = [{k: v for k, v in m.items() if k != "composite"}
             for m in motion]
    # the leakage reference: a Wave 0 probe device (accepted at AT-0B-5)
    # changing the same picture around the robot in the same program
    probe = [{"id": f"p{st}", "type": "fx_probe", "stage": st,
              "op": "negate", "start": 0.0, "end": 8.0}
             for st in ("over_type",)]
    cases = [_case(work, "b0", dict(base, motion=plain), want_wm=True,
                   dump_frames=[60, 150]),
             _case(work, "b1", dict(base, fx=every, motion=motion),
                   want_wm=True, dump_frames=[60, 150]),
             _case(work, "bp", dict(base, fx=probe, motion=plain),
                   want_wm=True, dump_frames=[60, 150])]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], "brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def _robot_opaque():
    import renderer
    g = renderer.watermark_geometry(W, H)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", renderer.robot_path(), "-vf",
         f"scale={g['rw']}:{g['rh']},format=rgba", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(g["rh"], g["rw"], 4)[..., 3]
    core = a == 255
    core = (core & np.roll(core, 1, 0) & np.roll(core, -1, 0)
            & np.roll(core, 1, 1) & np.roll(core, -1, 1))
    m = np.zeros((H, W), bool)
    m[g["margin_y"]:g["margin_y"] + g["rh"],
      g["margin_x"]:g["margin_x"] + g["rw"]] = core
    return m


@needs_browser
def test_brand_end_card_and_watermark_are_untouched(brand):
    a, b = brand["b0"], brand["b1"]
    assert a["error"] is None and b["error"] is None, b["error"]
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert a["frames"][200:] == b["frames"][200:]       # the 5 s card
    assert set(range(200)) - set(_diff(a, b)) == set()
    assert all(r["rung"] == "full" for r in b["report"]), b["report"]
    m = _robot_opaque()
    assert m.sum() > 40
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    p = brand["bp"]
    assert p["error"] is None
    for k in ("60", "150"):
        ya, yb, yp = (_yuv(r["dumps"][k]) for r in (a, b, p))
        # the robot's own pixels: luma bit-exact
        assert np.abs(ya[0] - yb[0])[m].max() == 0, k
        # chroma under it moves only by the 4:2:0 conversion leakage of a
        # program in an alpha pixel-format group whose picture around the
        # robot changed (Wave 0 §6 item 14): no more than a Wave 0 device
        # changing the same picture leaks
        for c in (1, 2):
            leak = np.abs(ya[c] - yp[c])[cm].max()
            assert np.abs(ya[c] - yb[c])[cm].max() <= leak + 1, (k, c, leak)
        assert np.abs(ya[0] - yb[0])[~m].mean() > 10, k


# ── the fault matrix (AT-0C-3) on 1C's devices ──────────────────────────

def _spec_regions(work):
    edl = _base()
    edl["fx"] = [
        _region("r1", "ribbed_glass", device="region_material", start=1.0,
                end=2.0),
        _region("r2", "solarise", colour="#ff00ff", device="region_tone",
                start=2.0, end=3.0, region={"shape": "polygon", "keys": [
                    {"t": 0, "points": [[0.2, 0.2], [0.8, 0.3], [0.5, 0.8]]},
                    {"t": 1, "points": [[0.3, 0.2], [0.7, 0.4], [0.5, 0.7]]}
                ]}),
        _region("r3", "blur", device="region_soften", start=3.0, end=4.0,
                region={"shape": "face"})]
    return edl


def _spec_composites(work):
    edl = _base()
    edl["motion"] = [
        _word(composite={"blend": "auto", "opacity": 0.9}),
        {"id": "tw", "template": "type_window", "start": 3.0, "end": 4.0,
         "params": {"text": "GO", "reveal": "open"},
         "composite": {"fill": {"kind": "next"}, "field": "#c8102e"}},
        _word("m3", start=5.0, end=6.0, composite={
            "fill": {"kind": "asset", "asset_key": FILL_KEY,
                     "loop": True}})]
    edl["fx"] = [_region("rg", "negate", stage="over_type", start=1.0,
                         end=2.0, region={"shape": "glyph", "ref": "m1"})]
    return edl


def _faults():
    import faultinject
    return ("none",) + tuple(faultinject.FAULTS)


@pytest.mark.parametrize("fault", _faults())
@pytest.mark.parametrize("spec", ["regions", "composites"])
def test_every_fault_times_every_1c_spec_renders(spec, fault, work,
                                                 tmp_path):
    import faultinject
    import renderer
    from schemas import program_duration, validate_edl
    if spec == "composites" and not _chromium():
        pytest.skip("needs the motion engine")
    edl = {"regions": _spec_regions, "composites": _spec_composites}[spec](
        work)
    norm = validate_edl(edl, 12.0).model_dump()
    want = float(program_duration(norm))
    out = str(tmp_path / "out.mp4")
    names = () if fault == "none" else (fault,)
    with faultinject.inject(*names):
        dur = renderer.render_edl(norm, IDX, work["src"], out, str(tmp_path),
                                  True, suppress_outro=True,
                                  asset_locals={FILL_KEY: work["fill"]})
    assert dur == pytest.approx(want, abs=0.07), (spec, fault, dur, want)
    probe = renderer.media.probe(out)
    assert float(probe.get("duration") or 0) == pytest.approx(want,
                                                              abs=0.12)
