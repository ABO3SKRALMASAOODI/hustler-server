"""Track 1D on real renders: accents, strobes and junctions, frame-exact,
local, length-preserving and below the brand stages.

Renders go through the real ``renderer.render_edl`` with LOSSLESS encodes
(w1d_support.lossless), so a decoded frame is the filtergraph's own frame.

  * every accent kind changes exactly its planned frames (peak and decay)
    and, inside a region, no pixel outside it (region_locality = 0);
  * a 1-frame accent lands on its frame at 25, 29.97, 24 and 59.94 fps;
  * a white hit peaks at white and falls off with tau = 2 frames;
  * a card-clipped hit never touches the backdrop around the card;
  * a strobe holds every image exactly its frames, the sound untouched;
  * every junction style replaces only [cut - h, cut + h), keeps the
    program's frame count and its audio bit-identical (duration_exact);
  * seeded devices render md5-identically twice (determinism);
  * the end card is md5-identical and the watermark robot untouched with a
    full-frame over_type device on every frame (brand integrity, AT-0B-5's
    pattern for every 1D compiler).
"""

import os
import shutil

import numpy as np
import pytest

from w1d_support import (IDX, accent, audio_md5, changed, frame_md5s,
                         frames, lossless, make_montage, make_source, probe,
                         render)

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

BASE = {"keep": [[0.0, 4.0], [5.0, 9.0]], "captions": {"enabled": False},
        "frame": {"ratio": "9:16"}}


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1d_render")
    return {"dir": str(d), "src": make_source(str(d / "src25.mp4")),
            "montage": make_montage(str(d / "montage25.mp4"))}


@pytest.fixture(scope="module")
def base25(work):
    mp = pytest.MonkeyPatch()
    lossless(mp)
    out = os.path.join(work["dir"], "base25.mp4")
    try:
        render(BASE, work["src"], out)
    finally:
        mp.undo()
    return out


def _run(work, name, edl, monkeypatch, **kw):
    lossless(monkeypatch)
    out = os.path.join(work["dir"], f"{name}.mp4")
    dur, rep = render(edl, kw.pop("src", work["src"]), out, **kw)
    return out, dur, rep


# ── accents ─────────────────────────────────────────────────────────────

KINDS = {
    "negative": {}, "white_hit": {"decay_f": 4}, "colour_hit":
    {"decay_f": 2, "colour": "#ff2020"}, "threshold": {}, "posterize": {},
    "mono": {}, "mirror": {}, "hue_step": {}, "rgb_slice": {"seed": 2},
    "box_invert": {"seed": 3, "bands": 4}}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_accent_kind_changes_exactly_its_frames(kind, work, base25,
                                                     monkeypatch):
    extra = dict(KINDS[kind])
    it = accent("a1", 50, 25.0, kind=kind, n=2, **extra)
    out, dur, rep = _run(work, f"k_{kind}", dict(BASE, fx=[it]), monkeypatch)
    assert dur == pytest.approx(8.0, abs=0.05)
    assert [r["rung"] for r in rep] == ["full"]
    _, a = frames(base25)
    _, b = frames(out)
    want = list(range(50, 52 + int(extra.get("decay_f", 0))))
    assert changed(a, b) == want
    assert probe(out)["pix_fmt"] == "yuv420p"
    assert probe(out).get("color_range") in (None, "unknown", "tv")


def test_many_accents_stay_one_chain_per_kind(work, base25, monkeypatch):
    """Graph size is flat in the accent count (ROADMAP 1D): 12 negatives
    compile to one timeline filter, and land on exactly their frames."""
    fs = [10, 14, 17, 19, 60, 61, 62, 70, 80, 120, 150, 190]
    fx = [accent(f"n{k}", f, 25.0) for k, f in enumerate(fs)]
    import renderer
    graphs = []
    real = renderer.build_filtergraph

    def spy(*a, **kw):
        g = real(*a, **kw)
        graphs.append(g)
        return g
    monkeypatch.setattr(renderer, "build_filtergraph", spy)
    out, _dur, rep = _run(work, "many", dict(BASE, fx=fx), monkeypatch)
    _, a = frames(base25)
    _, b = frames(out)
    assert changed(a, b) == fs
    g = graphs[-1] if isinstance(graphs[-1], str) else str(graphs[-1])
    assert g.count("negate=enable") == 1
    assert all(r["rung"] == "full" for r in rep)


@pytest.mark.parametrize("rate", ["25", "30000/1001", "24", "60000/1001"])
def test_a_one_frame_accent_lands_on_its_frame_at_every_rate(
        rate, work, monkeypatch, tmp_path):
    src = make_source(str(tmp_path / "src.mp4"), rate=rate, dur=8.0)
    fps = eval(rate) if "/" in rate else float(rate)  # noqa: S307
    edl = {"keep": [[0.0, 6.0]], "captions": {"enabled": False}}
    idx = dict(IDX, video=dict(IDX["video"], fps=fps, duration=8.0))
    lossless(monkeypatch)
    o0, o1 = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    render(edl, src, o0, idx=idx, preview=False, duration=8.0)
    render(dict(edl, fx=[accent("a", 120, fps)]), src, o1, idx=idx,
           preview=False, duration=8.0)
    _, a = frames(o0)
    _, b = frames(o1)
    assert changed(a, b) == [120]


def test_white_hit_peaks_white_and_decays_with_tau_two(work, base25,
                                                       monkeypatch):
    it = accent("w", 40, 25.0, kind="white_hit", decay_f=5)
    out, *_ = _run(work, "wdecay", dict(BASE, fx=[it]), monkeypatch)
    y0, _ = frames(base25)
    y1, _ = frames(out)
    got = []
    for f in range(40, 46):
        b, w = y0[f].astype(float), y1[f].astype(float)
        m = (235 - b) > 40
        got.append(float(np.median((w[m] - b[m]) / (235 - b[m]))))
    assert got[0] == pytest.approx(1.0, abs=0.02)
    want = [np.exp(-k / 2.0) for k in range(1, 6)]
    assert np.allclose(got[1:], want, atol=0.03), got
    assert y1[46].tolist() == y0[46].tolist()


def test_a_region_accent_touches_no_pixel_outside_it(work, base25,
                                                     monkeypatch):
    box = [0.2, 0.3, 0.7, 0.6]
    it = accent("r", 60, 25.0, kind="negative", n=2,
                region={"shape": "rect", "box": box})
    out, *_ = _run(work, "region", dict(BASE, fx=[it]), monkeypatch)
    y0, a = frames(base25)
    y1, b = frames(out)
    assert changed(a, b) == [60, 61]
    H, W = y0.shape[1:]
    inside = np.zeros((H, W), bool)
    inside[int(box[1] * H):int(np.ceil(box[3] * H)),
           int(box[0] * W):int(np.ceil(box[2] * W))] = True
    for f in (60, 61):
        d = np.abs(y1[f].astype(int) - y0[f])
        assert d[~inside].max() == 0              # region_locality = 0
        assert d[inside].mean() > 40


def test_card_clipped_white_hit_never_touches_the_backdrop(work,
                                                          monkeypatch):
    card = {"id": "pc", "start": 0.5, "end": 7.5, "box": [0.06, 0.24,
                                                          0.94, 0.74],
            "entrance": "none", "exit": "none"}
    edl = dict(BASE, effects={"picture_cards": [card]})
    it = accent("ch", 75, 25.0, kind="white_hit", decay_f=3,
                region={"shape": "card"})
    o0, *_ = _run(work, "card0", edl, monkeypatch)
    o1, _d, rep = _run(work, "card1", dict(edl, fx=[it]), monkeypatch)
    assert [r["rung"] for r in rep] == ["full"]
    y0, a = frames(o0)
    y1, b = frames(o1)
    assert changed(a, b) == [75, 76, 77, 78]
    H, W = y0.shape[1:]
    import wipe_masks
    m = np.asarray(wipe_masks.card_mask(W, H, card)) > 0
    for f in (75, 76):
        d = np.abs(y1[f].astype(int) - y0[f])
        assert d[~m].max() == 0                  # backdrop untouched
    assert np.median(y1[75][np.asarray(wipe_masks.card_mask(W, H, card))
                            == 255]) >= 234      # 1-frame white peak


def test_card_region_without_a_card_is_omitted_not_failed(work, base25,
                                                          monkeypatch):
    it = accent("nc", 75, 25.0, kind="white_hit", region={"shape": "card"})
    out, dur, rep = _run(work, "nocard", dict(BASE, fx=[it]), monkeypatch)
    assert rep[0]["rung"] == "omit" and "no picture card" in rep[0]["reason"]
    _, a = frames(base25)
    _, b = frames(out)
    assert changed(a, b) == []


def test_over_type_accents_keep_the_alpha_group_exact(work, monkeypatch):
    """scope 'all' composites after the motion graphics (an alpha format
    group): every kind changes only its own frames."""
    import motion_engine
    if not motion_engine.available():
        pytest.skip("needs Chromium for the motion graphic")
    edl = dict(BASE, motion=[{"id": "m1", "template": "word_slam",
                              "start": 1.0, "end": 3.5,
                              "params": {"text": "FLYING"}}])
    fx, want, f = [], [], 30
    for k, kind in enumerate(sorted(KINDS)):
        extra = dict(KINDS[kind])
        fx.append(accent(f"o{k}", f, 25.0, kind=kind, stage="over_type",
                         **extra))
        want += list(range(f, f + 1 + int(extra.get("decay_f", 0))))
        f += 15
    o0, *_ = _run(work, "ot0", edl, monkeypatch)
    o1, _d, rep = _run(work, "ot1", dict(edl, fx=fx), monkeypatch)
    _, a = frames(o0)
    _, b = frames(o1)
    assert changed(a, b) == sorted(want)


# ── strobes ─────────────────────────────────────────────────────────────

def test_strobe_holds_every_image_its_frames_and_keeps_the_sound(
        work, base25, monkeypatch, tmp_path):
    import subprocess
    stills = []
    for k, c in enumerate(("0x3060c0", "0xc03030", "0x30c060")):
        p = str(tmp_path / f"s{k}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={c}:s=320x180:d=1", "-frames:v", "1", p],
                       check=True)
        stills.append(p)
    keys = {f"media/p1/s{k}.png": p for k, p in enumerate(stills)}
    st = {"id": "st", "type": "strobe", "stage": "pre_captions",
          "start": 2.0, "end": 2.6, "fit": "register",
          "frames": [{"source": {"kind": "asset",
                                 "asset_key": "media/p1/s0.png"}, "hold_f": 2},
                     {"source": {"kind": "asset",
                                 "asset_key": "media/p1/s1.png"}, "hold_f": 2,
                      "negate": True},
                     {"source": {"kind": "program"}, "hold_f": 1,
                      "negate": True},
                     {"source": {"kind": "source", "src_t": 6.0},
                      "hold_f": 2}]}
    out, dur, rep = _run(work, "strobe", dict(BASE, fx=[st]), monkeypatch,
                         asset_locals=keys)
    assert [r["rung"] for r in rep] == ["full"]
    assert dur == pytest.approx(8.0, abs=0.05)
    _, a = frames(base25)
    y1, b = frames(out)
    assert changed(a, b) == list(range(50, 65))
    assert audio_md5(out) == audio_md5(base25)
    md5 = frame_md5s(out)[50:65]
    # 2 + 2 + 1 + 2 = 7-frame cycle: the stills repeat exactly per hold
    assert md5[0] == md5[1] and md5[2] == md5[3] and md5[0] != md5[2]
    assert md5[0] == md5[7] == md5[8] and md5[2] == md5[9]
    assert md5[5] == md5[6] and md5[4] != md5[11]   # the live frame moves


def test_a_strobe_with_no_reachable_image_is_omitted(work, monkeypatch):
    st = {"id": "st", "type": "strobe", "stage": "pre_captions",
          "start": 2.0, "end": 2.4, "frames": [
              {"source": {"kind": "asset", "asset_key": "media/p1/gone.png"}}]}
    import renderer
    monkeypatch.setattr(renderer, "_render_asset_source",
                        lambda *a, **k: None)
    out, dur, rep = _run(work, "strobe_missing", dict(BASE, fx=[st]),
                         monkeypatch)
    assert rep[0]["rung"] == "omit" and dur == pytest.approx(8.0, abs=0.05)


# ── junctions ───────────────────────────────────────────────────────────

JBASE = {"keep": [[0.4, 2.6], [3.4, 5.6]], "captions": {"enabled": False},
         "frame": {"ratio": "9:16"}}
STYLES = ["dissolve", "fadegrays", "pixelize", "radial", "slices",
          "wipe_left", "wipe_right", "wipe_up", "wipe_down", "iris",
          "columns", "staircase", "box_steps", "bands", "letter_window",
          "matte_cut", "double_exposure", "pixel_stretch", "hit_flash",
          "whip_up", "whip_down", "zoom_blur", "radial_blur", "datamosh"]


@pytest.fixture(scope="module")
def jbase(work):
    mp = pytest.MonkeyPatch()
    lossless(mp)
    out = os.path.join(work["dir"], "jbase.mp4")
    try:
        render(JBASE, work["montage"], out)
    finally:
        mp.undo()
    return out


@pytest.mark.parametrize("style", STYLES)
def test_every_junction_style_is_duration_preserving_and_local(
        style, work, jbase, monkeypatch):
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
          "style": style, "duration_s": 0.48}
    edl = dict(JBASE)
    if style == "letter_window":
        it["origin"] = {"kind": "glyph", "ref": "m1"}
        edl = dict(JBASE, motion=[{"id": "m1", "template": "word_slam",
                                   "start": 1.5, "end": 3.0,
                                   "params": {"text": "NEXT"}}])
    out, dur, rep = _run(work, f"j_{style}", dict(edl, fx=[it]), monkeypatch,
                         src=work["montage"])
    assert dur == pytest.approx(4.4, abs=0.05)
    row = rep[0]
    assert row["rung"] in ("full", "reduced", "substitute"), row
    if style == "datamosh":
        assert row["rung"] == "reduced"
    if style == "matte_cut":                  # no matte here: the flash
        assert row["rung"] == "substitute"
    if style not in ("letter_window",):
        _, a = frames(jbase)
        _, b = frames(out)
        assert len(a) == len(b) == 110
        cut, h = 55, 6
        ch = changed(a, b)
        assert ch and set(ch) <= set(range(cut - h, cut + h)), ch
        assert audio_md5(out) == audio_md5(jbase)       # duration_exact


def test_one_junction_per_cut(work, monkeypatch):
    fx = [{"id": "j1", "type": "junction", "stage": "junction", "at": 2.2,
           "style": "iris"},
          {"id": "j2", "type": "junction", "stage": "junction", "at": 2.2,
           "style": "dissolve"}]
    _o, _d, rep = _run(work, "two_at_cut", dict(JBASE, fx=fx), monkeypatch,
                       src=work["montage"])
    rungs = {r["id"]: r["rung"] for r in rep}
    assert rungs == {"j1": "full", "j2": "omit"}


def test_a_junction_with_no_cut_is_omitted(work, monkeypatch):
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 1.0,
          "style": "iris"}
    _o, dur, rep = _run(work, "nocut", dict(JBASE, fx=[it]), monkeypatch,
                        src=work["montage"])
    assert rep[0]["rung"] == "omit" and "no cut" in rep[0]["reason"]


def test_an_iris_from_a_face_falls_back_to_the_centre_and_says_so(
        work, monkeypatch):
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
          "style": "iris", "origin": {"kind": "face"}}
    _o, _d, rep = _run(work, "irisface", dict(JBASE, fx=[it]), monkeypatch,
                       src=work["montage"])
    assert rep[0]["rung"] == "reduced" and "face" in rep[0]["reason"]


def test_iris_origin_from_a_face_box_peer(work, monkeypatch):
    """1A's matte_track:face_box answers: the iris opens there (the mask's
    first lit pixel sits at the box centre, ±1 px)."""
    import sys
    import types
    mod = types.ModuleType("matte_track")
    mod.face_box = lambda plan, t, index=None: [0.6, 0.2, 0.8, 0.4]
    monkeypatch.setitem(sys.modules, "matte_track", mod)
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
          "style": "iris", "origin": {"kind": "face"}, "duration_s": 0.48}
    out, _d, rep = _run(work, "irisbox", dict(JBASE, fx=[it]), monkeypatch,
                        src=work["montage"])
    assert rep[0]["rung"] == "full"
    import glob
    d = glob.glob(os.path.join(out + ".wd", "w1d_jn_*_m"))[0]
    from PIL import Image
    first = None
    for k in range(12):
        m = np.asarray(Image.open(os.path.join(d, f"f_{k:05d}.png")))
        if m.max() > 0:
            first = m
            break
    ys, xs = np.nonzero(first == first.max())
    H, W = first.shape
    assert abs(xs.mean() - 0.7 * W) <= 1.5 and abs(ys.mean() - 0.3 * H) <= 1.5


def test_matte_cut_uses_the_handle_matte_when_1a_provides_it(work,
                                                             monkeypatch):
    import fxgraph

    def handle(ctx, side):
        u = ctx.uid("hm")
        ctx.parts.append(f"color=c=black:s={ctx.W}x{ctx.H}:r={ctx.fps3:.3f},"
                         f"trim=end_frame={2 * ctx.h},format=gray,"
                         f"drawbox=x={ctx.W // 3}:y={ctx.H // 4}:"
                         f"w={ctx.W // 3}:h={ctx.H // 2}:color=white:t=fill"
                         f"[{u}]")
        return u
    monkeypatch.setattr(fxgraph, "handle_matte", handle)
    monkeypatch.setattr(fxgraph, "matte_info", lambda plan, label="person": {
        "label": label, "status": "ready", "source": "rvm", "reason": "ok",
        "windows": [], "qa": {}})
    for direction in (None, "out"):
        it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
              "style": "matte_cut", "duration_s": 0.48}
        if direction:
            it["direction"] = direction
        _o, dur, rep = _run(work, f"mattecut_{direction}",
                            dict(JBASE, fx=[it]), monkeypatch,
                            src=work["montage"])
        assert rep[0]["rung"] == "full", rep
        assert dur == pytest.approx(4.4, abs=0.05)


@pytest.mark.parametrize("style", ["datamosh", "zoom_blur", "box_steps"])
def test_seeded_and_precomputed_devices_are_deterministic(style, work,
                                                          monkeypatch):
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
          "style": style, "duration_s": 0.48, "seed": 3}
    acc = accent("b", 20, 25.0, kind="box_invert", n=2, seed=5, bands=5)
    md5s = []
    for k in range(2):
        out, *_ = _run(work, f"det_{style}_{k}", dict(JBASE, fx=[it, acc]),
                       monkeypatch, src=work["montage"])
        md5s.append(frame_md5s(out))
    assert md5s[0] == md5s[1]


# ── brand integrity (owner rule 4) ──────────────────────────────────────

def _robot_opaque(W, H):
    import subprocess
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


def test_end_card_and_watermark_are_untouched_by_every_1d_compiler(
        work, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTRO_ON_PREVIEW", True)
    fx = [accent("all", 0, 25.0, kind="negative", n=110, stage="over_type"),
          accent("w", 30, 25.0, kind="white_hit", decay_f=3),
          {"id": "st", "type": "strobe", "stage": "over_type", "start": 1.0,
           "end": 1.4, "frames": [{"source": {"kind": "colour",
                                              "colour": "white"}},
                                  {"source": {"kind": "program"},
                                   "negate": True}]},
          {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
           "style": "hit_flash"}]
    o0, d0, _ = _run(work, "brand0", JBASE, monkeypatch,
                     src=work["montage"], want_wm=True, suppress_outro=False)
    o1, d1, rep = _run(work, "brand1", dict(JBASE, fx=fx), monkeypatch,
                       src=work["montage"], want_wm=True,
                       suppress_outro=False)
    assert all(r["rung"] == "full" for r in rep), rep
    m0, m1 = frame_md5s(o0), frame_md5s(o1)
    assert len(m0) == len(m1) > 110
    assert m0[110:] == m1[110:]                    # the end card
    # every program frame changed, except the strobe's negated live frames
    # (negated twice: under the full-frame negative they are the picture)
    same = [i for i in range(110) if m0[i] == m1[i]]
    assert same == [27, 28], same   # 31-32 carry the white hit's decay
    y0, _ = frames(o0)
    y1, _ = frames(o1)
    H, W = y0.shape[1:]
    m = _robot_opaque(W, H)
    assert m.sum() > 40
    for f in (10, 30, 55, 100):
        assert np.abs(y1[f].astype(int) - y0[f])[m].max() == 0


def test_a_junction_window_ending_on_a_truncating_frame_shows_no_pad(
        work, monkeypatch):
    """fxgraph.junction_overlay re-pads a compiled window with
    ``setpts=N/fps/TB``, which truncates: on a 1/25 time base frames 57-59
    took their predecessors' stamps and the overlay showed the black pad
    frame on the window's last frame. The compiled window rides a prime
    time base (junctions_v2._settle), so every window frame is the
    transition's own: a dissolve between two bright bars never goes black."""
    edl = {"keep": [[6.4, 8.6], [9.4, 11.6]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    it = {"id": "j", "type": "junction", "stage": "junction", "at": 2.2,
          "style": "dissolve", "duration_s": 0.4}
    o0, *_ = _run(work, "pad0", edl, monkeypatch, src=work["montage"])
    o1, _d, rep = _run(work, "pad1", dict(edl, fx=[it]), monkeypatch,
                       src=work["montage"])
    y0, a = frames(o0)
    y1, b = frames(o1)
    ch = changed(a, b)
    assert ch and set(ch) <= set(range(50, 60)), ch
    lum = [float(y1[f].mean()) for f in range(50, 60)]
    lo = min(float(y0[49].mean()), float(y0[60].mean()))
    assert min(lum) >= lo - 3, (lum, lo)        # never a black pad frame
    # two static bars: every window frame is a distinct step of the eased
    # dissolve (measured without the fix: frames 57-59 read 71, 56, 53, 53
    # — one skipped, one doubled — instead of 71, 62, 56, 53)
    assert all(q < p - 0.5 for p, q in zip(lum, lum[1:])), lum
