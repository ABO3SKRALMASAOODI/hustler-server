"""Track 4A on real renders: depth and time devices through the renderer.

CI-safe: the depth.small sidecar is written straight into a local store
(tests/w4a_support.py: a source whose left half is NEAR and right half FAR,
known exactly), so no model or torch runs. Every render goes through
``renderer.render_edl`` in the probe child (tests/w0b_probe.py) with
lossless encodes. Pinned:

  * every kind renders only in its window: frames outside it are
    md5-identical to the render without it (region_locality = 0);
  * depth of field keeps the near (subject) plane sharp and blurs the far
    plane; a rack focus lands its focus on the rack_at frame (+-1);
  * fog moves only the far plane; a subject point cloud draws points only
    where the subject is;
  * a still with measured depth moves in 2.5D (full); without depth the
    device takes its ken_burns substitute, never fails;
  * a precompute that runs out of memory omits the device and the render
    is the control's, frame for frame;
  * time devices re-time exactly the frames they name and keep the
    program's frame count;
  * the schema, the card gate and the stale-client path (apply_batch).
"""

import copy
import json
import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import w0b_probe                                              # noqa: E402
import w4a_support as sup                                     # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

FPS = 25


def _dp(fid, kind, start, end, stage="world", **kw):
    it = {"id": fid, "type": "depth", "stage": stage, "start": start,
          "end": end, "kind": kind, "device": f"depth_{kind}"}
    it.update(kw)
    return it


def _tm(fid, kind, start, end, **kw):
    it = {"id": fid, "type": "time", "stage": "world", "start": start,
          "end": end, "kind": kind, "device": f"time_{kind}"}
    it.update(kw)
    return it


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w4a")
    src = sup.make_split_source(str(d / "split.mp4"), dur=16.0)
    info = media.probe(src)
    ref = sidecars.source_from(media.sha256_file(src), src, info, 7, [])
    store = str(d / "store")
    sup.write_depth_chunks(store, ref)
    still = sup.make_split_still(str(d / "still.png"))
    sref = sidecars.source_from(media.sha256_file(still), still,
                                {"duration": 0.04, "fps": 25.0,
                                 "width": 960, "height": 540}, None, [])
    sup.write_depth_chunks(store, sref)
    bare = sup.make_split_still(str(d / "bare.png"), w=900, h=540)
    idx = sup.index_for(ref, info)
    base = {"keep": [[0.0, 16.0]]}
    locals_ = {"media/w4a/still.png": still, "media/w4a/bare.png": bare}

    def case(name, fx, faults=None):
        e = dict(copy.deepcopy(base), fx=fx)
        return {"name": name, "src": src, "idx": idx, "edl": e,
                "preview": True, "asset_locals": locals_,
                "faults": faults or []}
    cases = [
        case("control", []),
        case("dof", [_dp("d", "dof", 2.0, 4.0, focus={"on": "subject"},
                         blur=1.0)]),
        case("rack", [_dp("r", "rack_focus", 2.0, 4.0,
                          focus_from={"on": "background"},
                          focus={"on": "subject"}, rack_at=3.0, rack_f=10,
                          blur=1.0)]),
        case("fog", [_dp("f", "fog", 2.0, 4.0, strength=0.9,
                         colour="#d0d4dc")]),
        case("cloud", [_dp("c", "point_cloud", 2.0, 3.6,
                           focus={"on": "subject"}, backdrop="black",
                           move={"path": "orbit_left", "amount": 0.05},
                           seed=3)]),
        case("cloud2", [_dp("c", "point_cloud", 2.0, 3.6,
                            focus={"on": "subject"}, backdrop="black",
                            move={"path": "orbit_left", "amount": 0.05},
                            seed=3)]),
        case("light", [_dp("l", "light", 2.0, 3.6, strength=0.8)]),
        case("still", [_dp("p", "parallax", 2.0, 3.6, stage="screen",
                           source={"kind": "asset",
                                   "asset_key": "media/w4a/still.png"},
                           move={"path": "push_in", "amount": 0.5})]),
        case("bare", [_dp("b", "parallax", 2.0, 3.6, stage="screen",
                          source={"kind": "asset",
                                  "asset_key": "media/w4a/bare.png"})]),
        case("frame", [_dp("q", "parallax", 2.0, 3.6,
                           move={"path": "drift_left", "amount": 0.6})]),
        case("push", [_dp("u", "push_through", 2.0, 3.4, text="GO")]),
        case("oom", [_dp("d", "dof", 2.0, 4.0)],
             faults=["oom:fx_precompute"]),
    ]
    tsrc = sup.make_index_source(str(d / "index.mp4"))
    tinfo = media.probe(tsrc)
    tidx = {"words": [], "silences": [], "sentences": [],
            "video": {"width": tinfo["width"], "height": tinfo["height"],
                      "fps": tinfo["fps"], "duration": tinfo["duration"]}}
    tbase = {"keep": [[0.0, 6.0]]}

    def tcase(name, fx):
        return {"name": name, "src": tsrc, "idx": tidx,
                "edl": dict(copy.deepcopy(tbase), fx=fx), "preview": True}
    cases += [
        tcase("t_control", []),
        tcase("t_reverse", [_tm("tr", "reverse", 1.0, 2.0)]),
        tcase("t_poster", [_tm("tp", "posterize", 1.0, 2.0, frames=3)]),
        tcase("t_stutter", [_tm("ts", "stutter", 1.0, 2.6, frames=4,
                                repeats=3)]),
        tcase("t_remap", [_tm("tm", "remap", 1.0, 2.6,
                              curve="hold_then_catch")]),
        tcase("t_echo", [_tm("te", "echo", 1.0, 2.0,
                             echo={"count": 3, "spacing_f": 2,
                                   "decay": 0.5})]),
    ]
    old = os.environ.get("VALMERA_SIDECAR_DIR")
    os.environ["VALMERA_SIDECAR_DIR"] = store
    try:
        res = w0b_probe.run(cases, str(d / "out"))
    finally:
        if old is None:
            os.environ.pop("VALMERA_SIDECAR_DIR", None)
        else:
            os.environ["VALMERA_SIDECAR_DIR"] = old
    return {"res": res, "dir": str(d / "out")}


def _ok(runs, name):
    r = runs["res"][name]
    assert r["error"] is None, r["error"]
    return r


def _path(runs, name):
    return os.path.join(runs["dir"], f"{name}.mp4")


def _changed(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


def _rung(r, fid):
    return {row["id"]: row for row in r["report"]}[fid]


def _halves(frame):
    g = sup.gray(frame)
    w = g.shape[1]
    return g[:, 8: w // 2 - 8], g[:, w // 2 + 8: -8]


# ── windows are exact ────────────────────────────────────────────────────

@pytest.mark.parametrize("name,win", [
    ("dof", (50, 100)), ("rack", (50, 100)), ("fog", (50, 100)),
    ("cloud", (50, 90)), ("light", (50, 90)), ("still", (50, 90)),
    ("frame", (50, 90)), ("push", (50, 85))])
def test_each_kind_changes_only_its_window(runs, name, win):
    ctl, got = _ok(runs, "control"), _ok(runs, name)
    assert len(got["frames"]) == len(ctl["frames"])
    changed = _changed(ctl, got)
    assert changed, f"{name} drew nothing"
    assert min(changed) >= win[0] and max(changed) < win[1], (name, changed[:3], changed[-3:])
    assert _rung(got, list({r["id"] for r in got["report"]})[0])["rung"] \
        in ("full", "reduced", "substitute")


# ── depth of field and rack focus ───────────────────────────────────────

def test_dof_keeps_the_near_plane_sharp_and_blurs_the_far_plane(runs):
    _ok(runs, "dof")
    ctl = sup.frames_rgb(_path(runs, "control"))
    dof = sup.frames_rgb(_path(runs, "dof"))
    for k in (55, 75, 95):
        cn, cf = _halves(ctl[k])
        dn, df = _halves(dof[k])
        assert sup.lap_var(dn) >= 0.9 * sup.lap_var(cn)
        assert sup.lap_var(df) <= 0.35 * sup.lap_var(cf)
    assert _rung(_ok(runs, "dof"), "d")["rung"] == "full"


def test_rack_focus_lands_on_its_frame(runs):
    ctl = sup.frames_rgb(_path(runs, "control"))
    rk = sup.frames_rgb(_path(runs, "rack"))
    near_ratio = []
    for k in range(50, 100):
        cn, _cf = _halves(ctl[k])
        rn, _rf = _halves(rk[k])
        near_ratio.append(sup.lap_var(rn) / max(1e-6, sup.lap_var(cn)))
    # early in the window the near plane is soft (focus on the background)
    assert max(near_ratio[:5]) < 0.5
    land = 50 + next(i for i, v in enumerate(near_ratio) if v >= 0.9)
    assert abs(land - 75) <= 1, land               # rack_at 3.0 s at 25 fps
    _cn, cf = _halves(ctl[60])
    _rn, rf = _halves(rk[60])
    assert sup.lap_var(rf) >= 0.9 * sup.lap_var(cf)  # background sharp early


def test_fog_moves_only_the_far_plane(runs):
    ctl = sup.frames_rgb(_path(runs, "control"))
    fog = sup.frames_rgb(_path(runs, "fog"))
    cn, cf = _halves(ctl[80])
    fn, ff = _halves(fog[80])
    assert np.abs(fn - cn).mean() < 3.0
    assert np.abs(ff - 0.92 * 208).mean() < np.abs(cf - 208).mean() * 0.5


def test_a_subject_point_cloud_draws_points_only_on_the_subject(runs):
    cl = sup.frames_rgb(_path(runs, "cloud"))
    for k in (55, 70, 85):
        near, far = _halves(cl[k])
        assert near.mean() > 8.0
        # orbiting points may cross the seam a little, never fill the far
        assert (far > 40).mean() < 0.02
    # seeded: two renders are identical
    a, b = _ok(runs, "cloud"), _ok(runs, "cloud2")
    assert a["frames"] == b["frames"]


# ── stills ───────────────────────────────────────────────────────────────

def test_a_still_with_depth_moves_in_2_5d(runs):
    r = _ok(runs, "still")
    assert _rung(r, "p")["rung"] == "full"
    fr = sup.frames_rgb(_path(runs, "still"))
    moves = [np.abs(fr[k + 1].astype(int) - fr[k].astype(int)).mean()
             for k in range(52, 88)]
    assert sum(m > 0.05 for m in moves) > 28


def test_a_still_without_depth_takes_its_ken_burns_substitute(runs):
    r = _ok(runs, "bare")
    row = _rung(r, "b")
    assert row["rung"] == "substitute" and "ken_burns" in row["reason"]


def test_the_frame_at_start_parallax_reads_the_main_depth(runs):
    assert _rung(_ok(runs, "frame"), "q")["rung"] == "full"


def test_an_oom_precompute_omits_the_device_and_keeps_the_render(runs):
    r = _ok(runs, "oom")
    assert _rung(r, "d")["rung"] == "omit"
    assert r["frames"] == _ok(runs, "control")["frames"]


# ── time ────────────────────────────────────────────────────────────────

def _indices(runs, name):
    import qa_metrics
    _i, fr = qa_metrics.read_frames(_path(runs, name), gray=True)
    # gray reads the limited-range luma expanded to full range
    return [int(round((float(np.asarray(f).mean()) * 219.0 / 255.0 + 16.0
                       - 20.0) / 4.0)) % 50 for f in fr]


def test_time_devices_keep_the_frame_count_and_the_outside(runs):
    ctl = _ok(runs, "t_control")
    for name in ("t_reverse", "t_poster", "t_stutter", "t_remap", "t_echo"):
        r = _ok(runs, name)
        assert len(r["frames"]) == len(ctl["frames"]), name
        ch = _changed(ctl, r)
        assert ch and min(ch) >= 25 and max(ch) < 65, (name, ch[:3])


def test_reverse_posterize_stutter_and_remap_pick_their_frames(runs):
    import timewarp
    base = _indices(runs, "t_control")
    assert base[25:50] == list(range(25, 50))
    rev = _indices(runs, "t_reverse")
    assert rev[25:50] == list(range(49, 24, -1))
    pos = _indices(runs, "t_poster")
    assert pos[25:50] == [25 + i for i in timewarp.posterize_frames(25, 3)]
    stu = _indices(runs, "t_stutter")
    assert stu[25:65] == [(25 + i) % 50 for i in
                          timewarp.stutter_frames(40, 4, 3)]
    rem = _indices(runs, "t_remap")
    assert rem[25:65] == [(25 + i) % 50 for i in
                          timewarp.remap_frames(40, "hold_then_catch")]
    assert rem[65:70] == base[65:70]


# ── schema, cards and the stale-client path ─────────────────────────────

def test_fields_belong_to_their_kind_and_push_through_is_a_zoom():
    import devices
    import schemas
    edl = {"keep": [[0, 8]], "fx": [_dp("x", "dof", 1, 2, text="NO")]}
    with pytest.raises(schemas.EDLValidationError, match="text"):
        schemas.validate_edl(edl, 8.0)
    edl["fx"] = [_dp("x", "rack_focus", 1, 2, focus={"on": "subject"})]
    with pytest.raises(schemas.EDLValidationError, match="focus_from"):
        schemas.validate_edl(edl, 8.0)
    edl["fx"] = [_tm("t", "reverse", 1, 5)]
    with pytest.raises(schemas.EDLValidationError, match="at most"):
        schemas.validate_edl(edl, 8.0)
    card = devices.load_cards()["depth_push_through"]
    assert card["owner_flags"]["adds_zoom"] is True
    assert not devices.proposable(card, asked=())[0]
    assert devices.proposable(card, asked=("zoom",))[0]
    for cid, c in devices.load_cards().items():
        if cid.startswith(("depth_", "time_")) and cid != \
                "depth_push_through":
            assert not c["owner_flags"]["adds_zoom"]
            assert not c["owner_flags"]["adds_sound"]


def test_every_4a_device_is_written_and_removed_through_apply_batch():
    import edit_batch
    import schemas
    base = schemas.validate_edl({"keep": [[0, 8]]}, 8.0).model_dump()
    sig = schemas.edl_signature(base)
    items = [
        _dp("a", "parallax", 1.0, 3.0, move={"path": "orbit_left",
                                             "amount": 0.4}),
        _dp("b", "dof", 1.0, 3.0, focus={"on": "subject"}),
        _dp("c", "rack_focus", 1.0, 3.0, focus_from={"on": "background"},
            focus={"on": "subject"}, rack_at=2.0,
            bind={"on": "word", "ref": 4, "rhythm": "speech"}),
        _dp("d", "fog", 1.0, 3.0), _dp("e", "light", 1.0, 3.0),
        _dp("f", "point_cloud", 1.0, 3.0, backdrop="dim"),
        _dp("g", "ken_burns", 1.0, 3.0, stage="screen",
            source={"kind": "source", "src_t": 2.0}),
        _dp("h", "push_through", 1.0, 2.0, text="FAST"),
        _tm("i", "echo", 1.0, 2.0, echo={"count": 3}),
        _tm("j", "remap", 1.0, 2.0, curve="ease_in"),
        _tm("k", "interpolate", 1.0, 2.0),
    ]
    for it in items:
        out = edit_batch.apply_batch(base, [{"action": "upsert",
                                             "layer": "fx", "id": it["id"],
                                             "value": it}], 8.0, set())
        assert [i["id"] for i in out["fx"]] == [it["id"]]
        back = edit_batch.apply_batch(out, [{"action": "remove",
                                             "layer": "fx",
                                             "id": it["id"]}], 8.0, set())
        assert schemas.edl_signature(back) == sig
    bad = _dp("z", "dof", 1.0, 3.0, device="finish_crt")
    with pytest.raises(ValueError, match="fx type|writes"):
        edit_batch.apply_batch(base, [{"action": "upsert", "layer": "fx",
                                       "id": "z", "value": bad}], 8.0,
                               set())


def test_flash_times_only_for_short_replacing_windows():
    import flash_safety
    edl = {"keep": [[0, 8]], "fx": [
        _dp("a", "parallax", 1.0, 1.3), _dp("b", "parallax", 2.0, 4.0),
        _dp("c", "point_cloud", 5.0, 5.2, backdrop="dim"),
        _dp("d", "point_cloud", 6.0, 6.3)]}
    import schemas
    e = schemas.validate_edl(edl, 8.0).model_dump()
    times = sorted(ev["t"] for ev in flash_safety.flash_events(e))
    assert times == [1.0, 6.0]


def test_backend_style_load_registers_depth_and_time(tmp_path):
    """The backend loads schemas.py by path without the worker tree."""
    import importlib.util
    import subprocess
    worker = os.path.dirname(HERE)
    code = ("import importlib.util,sys;"
            f"spec=importlib.util.spec_from_file_location('worker_schemas',"
            f"'{worker}/schemas.py');m=importlib.util.module_from_spec(spec);"
            "sys.modules['worker_schemas']=m;spec.loader.exec_module(m);"
            "print(','.join(sorted(m.FX_TYPES)))")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                       capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k != "PYTHONPATH"})
    assert r.returncode == 0, r.stderr[-800:]
    assert {"depth", "time"} <= set(r.stdout.strip().split(","))
    del importlib
