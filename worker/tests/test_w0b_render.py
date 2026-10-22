"""Track 0B acceptance on real renders: the FX stage seams, end to end.

Every render here goes through ``renderer.render_edl`` in a probe-lane child
(tests/w0b_probe.py, ``VALMERA_FX_PROBE=1``: the engine-only ``fx_probe``
type exists only there) with LOSSLESS encodes, so frame md5s are the
filtergraph's own frames.

  AT-0B-1  a no-op fx item at every stage (world, screen, pre_captions,
           over_type, junction with source handles, music_bus) gives
           frame-md5-identical video and identical audio
  AT-0B-2  a 1-frame negate at program frame 120 changes exactly that frame
           on 25, 29.97, 24 and 59.94 fps programs (synthetic here; the real
           V01/V04/V07/V11 windows in the local-only test)
  AT-0B-5  brand integrity with fx at every stage: the end card is
           md5-identical, the watermark robot's opaque pixels, the aspect
           bars and the screen-frame plate are untouched by a full-frame
           over_type negate (every creative stage compiles before them)
  plus     the window paths (timeline enable, split/trim/re-pad window,
           masked region, bbox blend) touch only their frames and pixels;
           a junction dissolve uses real source handles; music_bus reaches
           the mix; degrade paths (missing filter, OOM precompute, absent
           matte, unknown type, a failing graph) never fail the render.
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
from localfix import local_only                               # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": 12.0}}
MUSIC_KEY = "media/w0b/music.m4a"


def _probe(stage, fid="p", **kw):
    item = {"id": fid, "type": "fx_probe", "stage": stage}
    item.update(kw)
    return item


def _base(keep=((0.0, 4.0), (5.0, 9.0)), ratio="9:16", music=True):
    edl = {"keep": [list(k) for k in keep], "captions": {"enabled": False}}
    if ratio:
        edl["frame"] = {"ratio": ratio}
    if music:
        edl["music"] = [{"id": "m1", "storage_key": MUSIC_KEY, "start": 0.0,
                         "end": 8.0, "gain_db": -18}]
    return edl


def _diff(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


def _box(rect, W, H):
    import fxprims
    x, y, w, h = fxprims.rect_box(rect, W, H)
    m = np.zeros((H, W), bool)
    m[y:y + h, x:x + w] = True
    return m


def _yuv(path, W, H):
    a = np.fromfile(path, np.uint8).astype(int)
    y = a[:W * H].reshape(H, W)
    u = a[W * H:W * H + (W // 2) * (H // 2)].reshape(H // 2, W // 2)
    v = a[W * H + (W // 2) * (H // 2):].reshape(H // 2, W // 2)
    return y, u, v


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("w0b_render")
    src = w0b_probe.make_source(str(d / "src25.mp4"), rate="25")
    mus = w0b_probe.make_tone(str(d / "music.m4a"))
    return {"dir": str(d), "src": src, "music": mus}


def _case(work, name, edl, **kw):
    c = {"name": name, "src": kw.pop("src", work["src"]), "idx": IDX,
         "edl": edl, "asset_locals": {MUSIC_KEY: work["music"]}}
    c.update(kw)
    return c


# ── AT-0B-1 / window paths / degrade paths (one 25 fps child) ─────────────

@pytest.fixture(scope="module")
def r25(work):
    base = _base()
    every = [_probe("world", "w"), _probe("screen", "s"),
             _probe("pre_captions", "pc"), _probe("over_type", "ot")]
    for it in every:
        it.update(start=1.0, end=6.5)
    every += [_probe("junction", "j", at=4.0, frames=5),
              _probe("music_bus", "b", music_id="m1")]
    f120 = dict(start=4.8, end=4.84)
    cases = [
        _case(work, "base", base, graph=True, dump_frames=[120]),
        _case(work, "noop_all", dict(base, fx=every), graph=True),
        _case(work, "neg_world", dict(base, fx=[
            _probe("world", op="negate", **f120)]), dump_frames=[120]),
        _case(work, "negw_screen", dict(base, fx=[
            _probe("screen", op="negate_window", **f120)])),
        _case(work, "neg_over", dict(base, fx=[
            _probe("over_type", op="negate", **f120)])),
        _case(work, "region_pre", dict(base, fx=[
            _probe("pre_captions", op="region_negate",
                   rect=[0.25, 0.25, 0.75, 0.75], **f120)]),
            dump_frames=[120, 119]),
        _case(work, "blend_over", dict(base, fx=[
            _probe("over_type", op="blend_screen", rect=[0.1, 0.1, 0.5, 0.5],
                   start=4.8, end=4.88)]), dump_frames=[120]),
        _case(work, "dissolve", dict(base, fx=[
            _probe("junction", op="dissolve", at=4.0, frames=4)])),
        _case(work, "dissolve_freeze", dict(
            base, fx=[_probe("junction", op="dissolve", at=4.0, frames=4)],
            # a censored program reads no source handles: the edges freeze
            effects={"regions": [{"id": "r", "mode": "black", "x": 0.0,
                                  "y": 0.0, "w": 0.05, "h": 0.05,
                                  "start": 0.5, "end": 1.0}]})),
        _case(work, "bus_gain", dict(base, fx=[
            _probe("music_bus", op="gain", music_id="m1", gain_db=-12)])),
        _case(work, "matte", dict(base, fx=[
            _probe("world", op="matte_view", start=1.0, end=2.0)])),
        _case(work, "filter_missing", dict(base, fx=[
            _probe("world", op="negate", **f120)]),
            faults=["ffmpeg_filter_missing:negate"]),
        _case(work, "oom", dict(base, fx=[
            _probe("pre_captions", op="region_negate",
                   rect=[0.25, 0.25, 0.75, 0.75], **f120)]),
            faults=["oom:fx_precompute"]),
        _case(work, "unknown_type", dict(base, fx=[
            {"id": "z", "type": "from_a_newer_lane", "stage": "world",
             "start": 1.0, "end": 2.0}])),
        _case(work, "no_cut", dict(base, fx=[
            _probe("junction", at=2.0, frames=4)])),
    ]
    return w0b_probe.run(cases, os.path.join(work["dir"], "r25"))


def test_at_0b_1_noop_at_every_stage_is_md5_identical(r25):
    base, fx = r25["base"], r25["noop_all"]
    assert base["error"] is None and fx["error"] is None, (base, fx)
    assert len(base["frames"]) == len(fx["frames"]) == 200
    assert _diff(base, fx) == []
    assert base["audio"] == fx["audio"]
    rows = {r["id"]: r for r in fx["report"]}
    assert set(rows) == {"w", "s", "pc", "ot", "j", "b"}
    assert all(r["rung"] == "full" for r in rows.values())
    # ...and the plumbing really ran: every stage emitted its window
    g = fx["graphs"][-1]
    for marker in ("tpad=start=25:stop=1:start_mode=add", "fxj0A", "fxj0B",
                   "pm"):
        assert marker in g, marker
    # the base graph carries no trace of the seams
    assert "fxj" not in base["graphs"][-1] and "pb" not in base["graphs"][-1]


@pytest.mark.parametrize("case", ["neg_world", "negw_screen", "neg_over",
                                  "region_pre"])
def test_one_frame_device_changes_exactly_frame_120(r25, case):
    r = r25[case]
    assert r["error"] is None, r["error"]
    assert _diff(r25["base"], r) == [120]
    assert r["audio"] == r25["base"]["audio"]


def test_negate_is_a_true_inversion_and_region_stays_local(r25):
    W, H = 270, 480
    assert (r25["base"]["probe"]["width"], r25["base"]["probe"]["height"]) \
        == (W, H)
    by, bu, bv = _yuv(r25["base"]["dumps"]["120"], W, H)
    ny, _nu, _nv = _yuv(r25["neg_world"]["dumps"]["120"], W, H)
    # negate on the world stage: every luma sample inverted (no camera,
    # cards or text above it here)
    assert np.abs(ny - (255 - by)).max() <= 1
    ry, ru, rv = _yuv(r25["region_pre"]["dumps"]["120"], W, H)
    box = _box([0.25, 0.25, 0.75, 0.75], W, H)
    assert np.abs(ry - by)[~box].max() == 0          # region_locality = 0
    assert np.abs(ry - (255 - by))[box].max() <= 1
    cbox = box[::2, ::2]
    assert np.abs(ru - bu)[~cbox].max() == 0 and \
        np.abs(rv - bv)[~cbox].max() == 0
    # the neighbouring frame is untouched
    y119, _, _ = _yuv(r25["region_pre"]["dumps"]["119"], W, H)
    assert y119.shape == by.shape


def test_bbox_blend_touches_its_frames_and_box_only(r25):
    r = r25["blend_over"]
    assert r["error"] is None
    assert _diff(r25["base"], r) == [120, 121]
    W, H = 270, 480
    by, _, _ = _yuv(r25["base"]["dumps"]["120"], W, H)
    ly, _, _ = _yuv(r["dumps"]["120"], W, H)
    box = _box([0.1, 0.1, 0.5, 0.5], W, H)
    assert np.abs(ly - by)[~box].max() == 0
    # screen(a, a) at 0.5 opacity brightens what it changes (testsrc2's
    # saturated bars are fixed points of screen: only the rest moves)
    moved = box & (np.abs(ly - by) > 2)
    assert moved.sum() > 100
    assert (ly[moved] > by[moved]).mean() > 0.6


def test_junction_dissolve_uses_source_handles(r25):
    r = r25["dissolve"]
    assert r["error"] is None and r["report"][0]["rung"] == "full"
    # cut at program frame 100, 4 frames each side
    assert _diff(r25["base"], r) == list(range(96, 104))
    assert r["audio"] == r25["base"]["audio"]
    frz = r25["dissolve_freeze"]
    assert frz["error"] is None and len(frz["frames"]) == 200
    assert frz["report"][0]["rung"] == "full"


def test_music_bus_reaches_the_mix_and_leaves_the_picture(r25):
    r = r25["bus_gain"]
    assert r["error"] is None
    assert _diff(r25["base"], r) == []
    assert r["audio"] != r25["base"]["audio"]


@pytest.mark.parametrize("case,reason", [
    ("matte", "program_matte is a stub"),
    ("filter_missing", "lacks negate"),
    ("oom", "OOM"),
    ("unknown_type", "does not know fx type"),
    ("no_cut", "no cut at 2s")])
def test_degrade_paths_omit_the_item_and_still_render(r25, case, reason):
    r = r25[case]
    assert r["error"] is None, r["error"]
    assert len(r["frames"]) == 200
    assert _diff(r25["base"], r) == []          # the device is simply absent
    assert [x["rung"] for x in r["report"]] == ["omit"]
    assert reason in r["report"][0]["reason"]


# ── AT-0B-2 on four program rates ─────────────────────────────────────────

RATES = {"25": 25.0, "30000/1001": 29.97, "24": 24.0, "60000/1001": 59.94}


@pytest.fixture(scope="module")
def rates(work):
    cases = []
    for rate, fps in RATES.items():
        src = w0b_probe.make_source(
            os.path.join(work["dir"], f"src_{rate.replace('/', '_')}.mp4"),
            rate=rate, dur=8.0)
        f3 = round(fps, 3)
        base = _base(keep=((0.0, 3.0), (4.0, 7.0)), music=False)
        item = _probe("world", op="negate", start=round(120 / f3, 2),
                      end=round(121 / f3, 2))
        cases.append(_case(work, f"b{fps}", base, src=src))
        cases.append(_case(work, f"n{fps}", dict(base, fx=[item]), src=src))
        cases.append(_case(work, f"o{fps}", dict(base, fx=[dict(
            item, stage="over_type", op="negate_window")]), src=src))
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"     # previews at the source rate
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], "rates"))
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old


@pytest.mark.parametrize("fps", list(RATES.values()))
def test_at_0b_2_one_frame_negate_on_every_rate(rates, fps):
    b, n, o = rates[f"b{fps}"], rates[f"n{fps}"], rates[f"o{fps}"]
    assert b["error"] is None and n["error"] is None and o["error"] is None
    num, den = (int(x) for x in b["probe"]["r_frame_rate"].split("/"))
    assert abs(num / den - fps) < 0.01
    assert len(b["frames"]) == len(n["frames"]) >= 140
    assert _diff(b, n) == [120]
    assert _diff(b, o) == [120]


# ── AT-0B-5: brand integrity ──────────────────────────────────────────────

def _everywhere(n_s):
    """A device at EVERY stage. From 0.5 s the picture is inverted an odd
    number of times (world + screen + over_type), the top-left region once
    more — and over_type, the last creative stage, covers the WHOLE frame,
    the watermark corner included."""
    return [_probe("world", "w", op="negate", start=0.0, end=n_s),
            _probe("screen", "s", op="negate_window", start=0.5, end=n_s),
            _probe("pre_captions", "pc", op="region_negate",
                   rect=[0.05, 0.05, 0.25, 0.25], start=0.0, end=n_s),
            _probe("over_type", "ot", op="negate", start=0.0, end=n_s),
            _probe("junction", "j", op="dissolve", at=4.0, frames=4),
            _probe("music_bus", "b", op="gain", music_id="m1", gain_db=-6)]


@pytest.fixture(scope="module")
def brand(work):
    base = _base()
    fx = _everywhere(8.0)
    shift = dict(base, effects={"frame_shifts": [
        {"id": "fs", "at": 1.0, "ratio": "1:1", "duration_s": 0.4,
         "zoom": False, "color": "#000000"}]})
    plate = dict(base, effects={"screen_frame": {"inset": 0.12}})
    cases = []
    for name, edl in (("plain", base), ("shift", shift), ("plate", plate)):
        cases.append(_case(work, f"{name}0", edl, want_wm=True,
                           dump_frames=[60, 150]))
        cases.append(_case(work, f"{name}1", dict(edl, fx=fx), want_wm=True,
                           dump_frames=[60, 150]))
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"       # the end card on previews too
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], "brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def _mark_opaque(W, H):
    """The v6 corner mark's fully opaque pixels in a W x H frame (its
    image's alpha 255: the type's and the robot's cores), where it is laid
    (renderer.watermark_opaque_mask). The mark is one exact overlay, so no
    resampling softens those pixels: a marked frame shows the mark's own
    colour there whatever any stage drew under it."""
    import renderer
    return renderer.watermark_opaque_mask(W, H)


@pytest.mark.parametrize("name", ["plain", "shift", "plate"])
def test_at_0b_5_end_card_is_md5_identical(brand, name):
    a, b = brand[f"{name}0"], brand[f"{name}1"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    card = set(brand["plain0"]["frames"][200:])        # the 5 s card
    shown = [i for i, f in enumerate(a["frames"]) if f in card]
    assert all(b["frames"][i] == a["frames"][i] for i in shown)
    if name == "plain":
        assert shown == list(range(200, 325))
    assert set(range(200)) - set(_diff(a, b)) == set()  # fx touched program
    assert [r["rung"] for r in b["report"]] == ["full"] * 6


@pytest.mark.parametrize("name", ["shift", "plate"])
def test_end_card_survives_aspect_shifts_and_the_floating_plate(brand, name):
    """Owner rule 4. Found by this test before Wave 0 shipped: an aspect
    shift held the programme's last frame for 1 s and the floating-window
    plate for 5 s past its end (their overlays lacked shortest=1), so the
    end card started late and was cut (shift: 100/125 card frames) or never
    shown (plate: 0/125), on finals too. Fixed at integration."""
    card = brand["plain0"]["frames"][200:]
    frames = brand[f"{name}0"]["frames"]
    assert frames[200:] == card


def test_at_0b_5_watermark_robot_is_drawn_after_every_stage(brand):
    W, H = 270, 480
    m = _mark_opaque(W, H)
    assert m.sum() > 40
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    # The v6 mark is laid exact (no resampling), so its mask is its opaque
    # core itself, not eroded: its luma is bit-exact. Chroma is subsampled
    # with neighbours, so a chroma sample on the type's rim reads the
    # (here inverted) picture beside it by a few levels; one sample in, the
    # mark's chroma is exact too.
    inner = (cm & np.roll(cm, 1, 0) & np.roll(cm, -1, 0)
             & np.roll(cm, 1, 1) & np.roll(cm, -1, 1))
    for k in ("60", "150"):
        a = _yuv(brand["plain0"]["dumps"][k], W, H)
        b = _yuv(brand["plain1"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0
        assert np.abs(a[1] - b[1])[cm].max() <= 10
        if inner.any():
            assert np.abs(a[1] - b[1])[inner].max() == 0
        # ...while the picture around it IS inverted
        outside = ~m
        outside[:60, :] = False
        assert np.abs(a[0] - b[0])[outside].mean() > 20


def test_at_0b_5_aspect_bars_and_plate_are_untouched(brand):
    W, H = 270, 480
    for name in ("shift", "plate"):
        a = _yuv(brand[f"{name}0"]["dumps"]["150"], W, H)
        b = _yuv(brand[f"{name}1"]["dumps"]["150"], W, H)
        # the bars / plate: rows far from the picture (top band below the
        # watermark, bottom band), identical in the fx render
        band = np.zeros((H, W), bool)
        if name == "shift":       # 1:1 inside 9:16: bars above and below
            band[int(H * 0.13):int(H * 0.2), :] = True
            band[int(H * 0.8):, :] = True
        else:                     # floating window: the plate margins
            band[:, :8] = True
            band[:, -8:] = True
            band[-20:, :] = True
        assert np.abs(a[0] - b[0])[band].max() == 0, name
        centre = (slice(int(H * 0.4), int(H * 0.6)),
                  slice(int(W * 0.3), int(W * 0.7)))
        assert np.abs(a[0][centre] - b[0][centre]).mean() > 20, name


# ── the failing-graph fallback (a device can never fail a render) ─────────

def test_a_failing_fx_graph_rerenders_without_fx(work):
    # A graph ffmpeg rejects at RUN time (compile-time problems degrade in
    # prepare): the child's probe compiler emits an invalid option
    # (w0b_probe W0B_BREAK_PROBE), the encode fails, and the render runs
    # once more without fx — every item reported omit with the cause.
    base = _base(music=False)
    cases = [_case(work, "bad", dict(base, fx=[
        _probe("world", op="negate", start=1.0, end=1.04)]),
        graph=True)]
    os.environ["W0B_BREAK_PROBE"] = "1"
    try:
        res = w0b_probe.run(cases, os.path.join(work["dir"], "bad"))
    finally:
        os.environ.pop("W0B_BREAK_PROBE", None)
    r = res["bad"]
    assert r["error"] is None, r["error"]
    assert len(r["frames"]) == 200
    assert [x["rung"] for x in r["report"]] == ["omit"]
    assert "rendered without it" in r["report"][0]["reason"]
    assert len(r["graphs"]) >= 2                  # built twice: fx, then none


# ── AT-0B-2 on the real footage (local only) ──────────────────────────────

_TMP = "/Users/masaoodi/Documents/Valmera/.tmp/valmera-podcast-shorts"
REAL = {
    "V01": (f"{_TMP}/peter-thiel-20260927-B7yl7fEHeKM/source/original.mp4",
            149.44, 25.0),
    "V04": (f"{_TMP}/podcast-7qZl_5xHoBw-20261006-fresh/source/"
            "interview-1080p.mp4", 115.5, 29.97),
    "V07": (f"{_TMP}/elon-part3-quality-repair-20260923/source/"
            "u5w_VkAx6tc.2160p.mp4", 600.0, 24.0),
    "V11": (f"{_TMP}/karp-sorkin-20260906/source/podcast.mp4", 600.0,
            59.94),
}


@local_only(*[v[0] for v in REAL.values()])
def test_at_0b_2_on_v01_v04_v07_v11(work):
    cases = []
    for vid, (path, t0, fps) in REAL.items():
        f3 = round(fps, 3)
        base = {"keep": [[t0, t0 + 3.5], [t0 + 5.0, t0 + 8.5]],
                "frame": {"ratio": "9:16"}, "captions": {"enabled": False}}
        idx = {"words": [], "silences": [], "video": {"duration": t0 + 60}}
        item = _probe("world", op="negate", start=round(120 / f3, 2),
                      end=round(121 / f3, 2))
        cases.append({"name": f"{vid}b", "src": path, "idx": idx,
                      "edl": base})
        cases.append({"name": f"{vid}n", "src": path, "idx": idx,
                      "edl": dict(base, fx=[item])})
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"
    try:
        res = w0b_probe.run(cases, os.path.join(work["dir"], "real"))
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old
    for vid, (_p, _t, fps) in REAL.items():
        b, n = res[f"{vid}b"], res[f"{vid}n"]
        assert b["error"] is None and n["error"] is None, (vid, b, n)
        num, den = (int(x) for x in b["probe"]["r_frame_rate"].split("/"))
        assert abs(num / den - fps) < 0.01, vid
        assert _diff(b, n) == [120], vid
