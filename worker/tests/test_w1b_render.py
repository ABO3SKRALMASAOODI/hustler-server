"""Track 1B on real renders: every subject and background kind through
``renderer.render_edl`` with LOSSLESS encodes, on a synthetic figure whose
person matte is known exactly (tests/w1b_support.py), fed through the
track-1A stand-in (tests/w1b_stub1a.py) the way 1A's program matte will be.

  * every kind renders at rung full and touches only its side of the matte
    (region_locality = 0 outside it) and only its window (frames outside
    are md5-identical);
  * a fill is the colour on the subject (IoU vs the matte >= 0.95) with a
    decaying tail; a 1-frame fill lands on exactly its frame at 25, 29.97,
    24 and 59.94 fps;
  * a poster field is flat and the colour asked for; a glow only brightens
    (screen) and dies out within its radius; clones sit where the subject
    was k frames earlier; a held subject is frozen; plates swap on their
    frames;
  * brand integrity: the end card and the watermark robot are untouched;
  * without a matte each kind draws its stand-in or is omitted, and the
    render never fails.
"""

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import w1b_probe                                              # noqa: E402
import w1b_support                                            # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

W, H = 270, 480                       # preview geometry of a 640x360 source
DUR = 6.0


def _idx(fps=25.0, dur=DUR):
    return {"words": [], "silences": [], "sentences": [],
            "video": {"duration": dur, "fps": fps, "width": 640,
                      "height": 360}}


def _edl(fx=(), keep=((0.0, DUR),), **kw):
    e = {"keep": [list(k) for k in keep], "captions": {"enabled": False},
         "frame": {"ratio": "9:16"}, "fx": list(fx)}
    e.update(kw)
    return e


def _sx(fid, **kw):
    it = {"id": fid, "type": "subject", "stage": "world", "start": 2.0,
          "end": 2.8, "kind": "fill", "colour": "white"}
    it.update(kw)
    return it


def _bg(fid, **kw):
    it = {"id": fid, "type": "background", "stage": "world", "start": 2.0,
          "end": 2.8, "kind": "solid", "colour": "#204060"}
    it.update(kw)
    return it


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1b_render")
    src, mask = w1b_support.make_subject_source(str(d / "src.mp4"),
                                                str(d / "mask.mkv"),
                                                dur=DUR)
    plate = w1b_support.make_still(str(d / "plate.png"))
    clip = w1b_support.make_clip(str(d / "clip.mp4"))
    return {"dir": str(d), "src": src, "mask": mask,
            "locals": {"media/p1/plate.png": plate,
                       "media/p1/clip.mp4": clip}}


def _case(work, name, edl, matte=True, **kw):
    c = {"name": name, "src": work["src"], "src_fps": 25.0, "idx": _idx(),
         "edl": edl, "matte": work["mask"] if matte else None,
         "asset_locals": work["locals"]}
    c.update(kw)
    return c


def program_masks(work, frames, keep=((0.0, DUR),), fps=25.0):
    """The program-space matte the stand-in fed the compiler, by frame."""
    import renderer
    fit = renderer.frame_fit_filter("crop", W, H, None)
    out = {}
    for f in frames:
        t = keep[0][0] + f / fps
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", work["mask"],
             "-frames:v", "1", "-vf", f"format=yuv420p,{fit},scale={W}:{H},"
             "format=gray", "-f", "rawvideo", "-"], capture_output=True,
            check=True).stdout
        out[f] = np.frombuffer(raw, np.uint8).reshape(H, W).astype(int)
    return out


def _grow(b, r):
    out = b.copy()
    for _ in range(r):
        o = out.copy()
        o[1:] |= out[:-1]
        o[:-1] |= out[1:]
        o[:, 1:] |= out[:, :-1]
        o[:, :-1] |= out[:, 1:]
        out = o
    return out


def _shrink(b, r):
    return ~_grow(~b, r)


FRAMES = [50, 55, 60, 69]                # 2.0 s .. last frame of 2.0-2.8


@pytest.fixture(scope="module")
def r25(work):
    every = {
        "fill": [_sx("f", start=2.0, end=2.16, decay_f=2)],
        "poster": [_sx("p", kind="poster", field="#ff5a36")],
        "silhouette": [_sx("s", kind="poster", field="#f1ede4",
                           keep_footage=False, colour="black")],
        "glow": [_sx("g", kind="glow", colour="#ffd23f", radius=0.05)],
        "outline": [_sx("o", kind="outline", width_px=4)],
        "negs": [_sx("n", kind="negate")],
        "negb": [_sx("n", kind="negate", scope="background")],
        "expo": [_sx("e", kind="exposure", scope="background", ev=-1.5,
                     saturation=0.0)],
        "iso": [_sx("i", kind="isolate")],
        "echo": [_sx("c", kind="echo", start=2.0, end=2.8,
                     echo={"mode": "clone", "offsets_f": [4, 8],
                           "opacity": 1.0, "tint": "#ff0000"})],
        "solid": [_bg("b")],
        "blur": [_bg("bb", kind="blur", colour=None, radius=0.03)],
        "radial": [_bg("br", kind="radial_blur", colour=None)],
        "hold": [_bg("bh", kind="source", colour=None,
                     source={"kind": "asset",
                             "asset_key": "media/p1/clip.mp4"},
                     hold_subject=True, hold_t=1.9)],
        "strobe": [_bg("bs", kind="strobe", colour=None, hold_f=4,
                       sources=[{"kind": "colour", "colour": "#ff0000"},
                                {"kind": "colour", "colour": "#0000ff"},
                                {"kind": "asset",
                                 "asset_key": "media/p1/plate.png"}])],
        "screen": [_sx("sc", kind="negate", stage="screen")],
        "region": [_sx("rg", kind="negate", region={
            "shape": "rect", "box": [0.0, 0.0, 1.0, 0.5]})],
    }
    dumps = FRAMES + [48, 51, 54, 58, 62]
    cases = [_case(work, "base", _edl(), dump_frames=dumps)]
    for name, fx in every.items():
        cases.append(_case(work, name, _edl(fx), dump_frames=dumps))
    return w1b_probe.run(cases, os.path.join(work["dir"], "r25"))


def _yuv(res, k):
    return w1b_probe.yuv(res["dumps"][str(k)], W, H)


def _ok(res):
    assert res["error"] is None, res["error"]
    assert [r["rung"] for r in res["report"]] == ["full"] * len(
        res["report"]), res["report"]


def _outside_window_identical(base, res, f0=50, f1=70):
    diff = [i for i, (a, b) in enumerate(zip(base["frames"], res["frames"]))
            if a != b]
    assert len(base["frames"]) == len(res["frames"]) == 150
    assert diff and min(diff) >= f0 and max(diff) < f1, diff[:5]


@pytest.mark.parametrize("name", ["fill", "poster", "silhouette", "glow",
                                  "outline", "negs", "negb", "expo", "iso",
                                  "echo", "solid", "blur", "radial", "hold",
                                  "strobe", "screen", "region"])
def test_every_kind_renders_full_inside_its_window_only(r25, name):
    _ok(r25[name])
    _outside_window_identical(r25["base"], r25[name])
    assert r25[name]["duration"] == pytest.approx(DUR, abs=0.05)
    assert r25[name]["audio"] == r25["base"]["audio"]


# which side of the matte a kind may change: "in" = only where the matte
# is non-zero (grown 2 px for chroma), "out" = only where it is not solid
SIDE = {"fill": "in", "negs": "in", "negb": "out", "expo": "out",
        "iso": "out", "poster": "out", "solid": "out", "blur": "out",
        "radial": "out", "screen": "in"}


@pytest.mark.parametrize("name", sorted(SIDE))
def test_region_locality_is_zero_on_the_untouched_side(r25, work, name):
    masks = program_masks(work, [51, 55, 60])
    for k in ((51,) if name == "fill" else (55, 60)):
        y0, u0, v0 = _yuv(r25["base"], k)
        y1, u1, v1 = _yuv(r25[name], k)
        m = masks[k]
        if SIDE[name] == "in":
            keep = ~_grow(m > 0, 2)
        else:
            keep = _shrink(m >= 250, 3)
        assert keep.sum() > 500
        assert np.abs(y1 - y0)[keep].max() == 0, (name, k)
        ck = keep[::2, ::2] & keep[1::2, ::2] & keep[::2, 1::2] & \
            keep[1::2, 1::2]
        assert np.abs(u1 - u0)[ck].max() == 0 and \
            np.abs(v1 - v0)[ck].max() == 0, (name, k)
        changed = (np.abs(y1 - y0) > 6) | np.repeat(np.repeat(
            (np.abs(u1 - u0) > 6) | (np.abs(v1 - v0) > 6), 2, 0), 2, 1)
        assert changed.sum() > 200, (name, k)        # it did something


def test_a_fill_is_the_colour_on_the_subject_and_decays(r25, work):
    masks = program_masks(work, [50, 51, 52, 53])
    y0 = _yuv(r25["base"], 50)[0]
    y, u, v = _yuv(r25["fill"], 50)
    m = masks[50]
    core = _shrink(m >= 250, 2)
    assert y[core].min() >= 234 and y[core].mean() >= 234.5
    cc = core[::2, ::2] & core[1::2, 1::2]
    assert np.abs(u[cc] - 128).max() <= 1 and np.abs(v[cc] - 128).max() <= 1
    filled = (y >= 200) & (np.abs(y - y0) > 8)
    truth = m >= 128
    iou = (filled & truth).sum() / max(1, (filled | truth).sum())
    assert iou >= 0.95, iou
    # 4 frames: frame 50 full, 51 full, then a 2-frame decay (52, 53)
    res = r25["fill"]
    diff = [i for i, (a, b) in enumerate(zip(r25["base"]["frames"],
                                             res["frames"])) if a != b]
    assert diff == [50, 51, 52, 53]


@pytest.fixture(scope="module")
def decay(work):
    it = _sx("f", start=2.0, end=2.16, decay_f=2)
    cases = [_case(work, "base", _edl(), dump_frames=[50, 51, 52, 53]),
             _case(work, "fill", _edl([it]), dump_frames=[50, 51, 52, 53])]
    return w1b_probe.run(cases, os.path.join(work["dir"], "decay"))


def test_the_decay_is_exponential(decay, work):
    masks = program_masks(work, [50, 51, 52, 53])
    lift = []
    for k in (50, 51, 52, 53):
        y0 = _yuv(decay["base"], k)[0]
        y1 = _yuv(decay["fill"], k)[0]
        core = _shrink(masks[k] >= 250, 2)
        # share of the way to white (235) the subject moved
        lift.append(float(((y1 - y0)[core] / np.maximum(1, 235 - y0[core]))
                          .mean()))
    assert lift[0] > 0.98 and lift[1] > 0.98
    assert 0.35 < lift[2] < 0.65 and 0.12 < lift[3] < 0.35, lift
    assert lift[2] > lift[3]


# ── AT-0B-2 style: a 1-frame fill lands on exactly its frame ─────────────

RATES = {"25": 25.0, "30000/1001": 29.97, "24": 24.0, "60000/1001": 59.94}


@pytest.fixture(scope="module")
def rates(work):
    cases = []
    for rate, fps in RATES.items():
        tag = rate.replace("/", "_")
        src, mask = w1b_support.make_subject_source(
            os.path.join(work["dir"], f"src_{tag}.mp4"),
            os.path.join(work["dir"], f"mask_{tag}.mkv"), rate=rate,
            dur=6.0)
        f3 = round(fps, 3)
        it = _sx("f", start=round(120 / f3, 2), end=round(121 / f3, 2))
        for name, fx in (("b", []), ("f", [it])):
            cases.append({"name": f"{name}{fps}", "src": src,
                          "src_fps": fps, "idx": _idx(fps), "edl": _edl(fx),
                          "matte": mask})
    old = os.environ.get("PREVIEW_MAX_FPS")
    os.environ["PREVIEW_MAX_FPS"] = "60"
    try:
        return w1b_probe.run(cases, os.path.join(work["dir"], "rates"))
    finally:
        if old is None:
            os.environ.pop("PREVIEW_MAX_FPS", None)
        else:
            os.environ["PREVIEW_MAX_FPS"] = old


@pytest.mark.parametrize("fps", list(RATES.values()))
def test_a_one_frame_fill_lands_on_its_frame_at_every_rate(rates, fps):
    b, f = rates[f"b{fps}"], rates[f"f{fps}"]
    assert b["error"] is None and f["error"] is None, (b, f)
    num, den = (int(x) for x in b["probe"]["r_frame_rate"].split("/"))
    assert abs(num / den - fps) < 0.01
    diff = [i for i, (x, y) in enumerate(zip(b["frames"], f["frames"]))
            if x != y]
    assert diff == [120], diff
    assert f["report"][0]["rung"] == "full"


# ── poster, glow, echo, backgrounds ──────────────────────────────────────

def test_the_poster_field_is_flat_and_the_colour_asked_for(r25, work):
    import subject_fx
    masks = program_masks(work, [60])
    y, u, v = _yuv(r25["poster"], 60)
    field = ~_grow(masks[60] > 0, 4)
    want = subject_fx.rgb_to_yuv((0xff, 0x5a, 0x36), 360)   # untagged: 601
    assert np.std(y[field]) <= 1.0
    assert abs(np.median(y[field]) - want[0]) <= 2
    cf = field[::2, ::2] & field[1::2, 1::2]
    assert abs(np.median(u[cf]) - want[1]) <= 2
    assert abs(np.median(v[cf]) - want[2]) <= 2
    sy = _yuv(r25["silhouette"], 60)[0]
    core = _shrink(masks[60] >= 250, 2)
    assert sy[core].max() <= 17                   # flat black silhouette


def test_the_glow_only_brightens_and_dies_within_its_radius(r25, work):
    masks = program_masks(work, [60])
    y0 = _yuv(r25["base"], 60)[0]
    y1 = _yuv(r25["glow"], 60)[0]
    assert (y1 - y0).min() >= -2                  # screen never darkens
    radius_px = int(0.05 * H) + 3
    far = ~_grow(masks[60] > 0, radius_px)
    assert np.abs(y1 - y0)[far].max() <= 2
    near = _grow(masks[60] > 0, 3) & ~(masks[60] > 0)
    assert (y1 - y0)[near].mean() > 20            # a visible halo


def test_clones_sit_where_the_subject_was(r25, work):
    masks = program_masks(work, [60, 52, 56])
    y0, u0, v0 = _yuv(r25["base"], 60)
    y1, u1, v1 = _yuv(r25["echo"], 60)
    then = (masks[52] >= 250) & ~_grow(masks[60] > 0, 2)  # k = 8
    assert then.sum() > 100
    cv = then[::2, ::2]
    # opaque red duotone: V well above U where the subject was 8 frames ago
    assert (v1[cv[:v1.shape[0], :v1.shape[1]]] -
            u1[cv[:u1.shape[0], :u1.shape[1]]]).mean() > 40
    now = _shrink(masks[60] >= 250, 2)
    assert np.abs(y1 - y0)[now].max() == 0        # the subject on top


def test_a_held_subject_is_frozen_and_the_world_swaps(r25, work):
    masks = program_masks(work, [48, 60, 69])
    hold = _shrink(masks[48] >= 250, 2)       # hold_t 1.9 s = frame 48
    a = _yuv(r25["hold"], 60)[0]
    b = _yuv(r25["hold"], 69)[0]
    assert np.abs(a - b)[hold].max() <= 1          # frozen across the window
    base48 = None
    # the frozen cut-out is the picture of frame 47
    import subprocess as sp
    raw = sp.run(["ffmpeg", "-v", "error", "-i", r25["base"]["path"], "-vf",
                  "select=eq(n\\,48)", "-frames:v", "1", "-pix_fmt",
                  "yuv420p", "-f", "rawvideo", "-"], capture_output=True,
                 check=True).stdout
    base48 = np.frombuffer(raw[:W * H], np.uint8).reshape(H, W).astype(int)
    assert np.abs(a - base48)[hold].max() <= 1


def test_plates_swap_on_their_frames(r25, work):
    masks = program_masks(work, [50, 54, 58, 62])
    for k, want in ((50, "red"), (54, "blue"), (58, "plate"), (62, "red")):
        y, u, v = _yuv(r25["strobe"], k)
        world = ~_grow(masks[k] > 0, 3)
        cw = world[::2, ::2]
        du, dv = float(np.median(u[cw])), float(np.median(v[cw]))
        if want == "red":
            assert dv > 200 and du < 110, (k, du, dv)
        elif want == "blue":
            assert du > 200 and dv < 140, (k, du, dv)
        else:
            assert 110 < du < 200, (k, du, dv)


# ── brand integrity (AT-0B-5 pattern) ────────────────────────────────────

@pytest.fixture(scope="module")
def brand(work):
    fx = [_sx("f", start=0.5, end=0.62, decay_f=2),
          _sx("p", kind="poster", field="accent", start=1.0, end=2.0),
          _sx("g", kind="glow", stage="screen", start=2.0, end=3.5),
          _sx("o", kind="outline", start=3.5, end=4.0),
          _bg("b", kind="blur", colour=None, start=4.0, end=5.99)]
    cases = [_case(work, "b0", _edl(), want_wm=True, dump_frames=[60, 140]),
             _case(work, "b1", _edl(fx), want_wm=True,
                   dump_frames=[60, 140])]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        return w1b_probe.run(cases, os.path.join(work["dir"], "brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def test_end_card_and_watermark_are_untouched(brand):
    a, b = brand["b0"], brand["b1"]
    assert a["error"] is None and b["error"] is None, (a["error"],
                                                       b["error"])
    n = len(a["frames"])
    assert n == len(b["frames"]) == 150 + 125
    assert a["frames"][150:] == b["frames"][150:]       # the end card
    import renderer
    g = renderer.watermark_geometry(W, H)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", renderer.robot_path(), "-vf",
         f"scale={g['rw']}:{g['rh']},format=rgba", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    alpha = np.frombuffer(raw, np.uint8).reshape(g["rh"], g["rw"], 4)[..., 3]
    m = np.zeros((H, W), bool)
    core = alpha == 255
    core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & \
        np.roll(core, 1, 1) & np.roll(core, -1, 1)
    m[g["margin_y"]:g["margin_y"] + g["rh"],
      g["margin_x"]:g["margin_x"] + g["rw"]] = core
    assert m.sum() > 40
    for k in ("60", "140"):
        ya = w1b_probe.yuv(a["dumps"][k], W, H)[0]
        yb = w1b_probe.yuv(b["dumps"][k], W, H)[0]
        assert np.abs(ya - yb)[m].max() == 0, k
        assert np.abs(ya - yb)[~m].max() > 10, k        # fx did run there


# ── without a matte: stand-ins, never a failed render ────────────────────

@pytest.fixture(scope="module")
def nomatte(work):
    fx = {"fill": [_sx("f", start=2.0, end=2.16, decay_f=2,
                       device="subject_fill_flash")],
          "poster": [_sx("p", kind="poster", field="#ff5a36",
                         device="subject_poster")],
          "glow": [_sx("g", kind="glow", device="subject_glow")],
          "outline": [_sx("o", kind="outline", device="subject_outline")],
          "hold": [_bg("h", kind="solid", hold_subject=True,
                       device="background_subject_hold")],
          "blur": [_bg("b", kind="blur", colour=None,
                       device="background_blur")]}
    cases = [_case(work, "base", _edl(), matte=False, dump_frames=[50])]
    for name, items in fx.items():
        cases.append(_case(work, name, _edl(items), matte=False,
                           dump_frames=[50]))
    return w1b_probe.run(cases, os.path.join(work["dir"], "nomatte"))


@pytest.mark.parametrize("name, rung, drawn", [
    ("fill", "substitute", True), ("poster", "substitute", True),
    ("glow", "substitute", True), ("outline", "omit", False),
    ("hold", "substitute", True), ("blur", "omit", False)])
def test_without_a_matte_stand_ins_draw_or_the_item_is_omitted(
        nomatte, name, rung, drawn):
    r, base = nomatte[name], nomatte["base"]
    assert r["error"] is None, r["error"]
    assert r["report"][0]["rung"] == rung, r["report"]
    assert r["report"][0]["reason"]
    same = r["frames"] == base["frames"]
    assert same is (not drawn)
    if name == "fill":
        y0 = _yuv(base, 50)[0]
        y1 = _yuv(r, 50)[0]
        lift = ((y1 - y0) / np.maximum(1, 235 - y0)).mean()
        assert 0.5 < lift < 0.7, lift          # the 0.6 full-frame flash
