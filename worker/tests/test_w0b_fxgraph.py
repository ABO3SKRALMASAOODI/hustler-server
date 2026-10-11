"""Track 0B unit tests: fxprims and fxgraph, in process.

No fx type is registered in the main test process (the seams test pins the
empty registry), so these drive fxgraph with a stand-in compiler patched in
over ``compiler_for``. The real end-to-end path is tests/test_w0b_render.py.
"""

import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import faultinject                                          # noqa: E402
import fxgraph                                              # noqa: E402
import fxprims                                              # noqa: E402
import renderer                                             # noqa: E402
import schemas                                              # noqa: E402
from timeline import Timeline                               # noqa: E402

RATES = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)


# ── fxprims ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fps", RATES)
def test_centisecond_times_name_every_frame_exactly(fps):
    f3 = fxprims.fps3(fps)
    for k in range(0, 3000):
        start = round(k / f3, 2)
        end = round((k + 1) / f3, 2)
        assert fxprims.frame_at(start, fps) == k
        assert fxprims.frame_span(start, end, fps) == (k, k + 1)


def test_frame_at_rounds_half_up_and_spans_hold_a_frame():
    assert fxprims.frame_at(0.02, 25) == 1          # 0.5 -> 1 (not banker's)
    assert fxprims.frame_at(0.1, 25) == 3           # 2.5 -> 3
    assert fxprims.frame_span(1.0, 1.0, 25) == (25, 26)
    assert fxprims.frame_span(-1.0, 0.0, 25) == (0, 1)


def test_enable_expression_is_one_union_over_merged_frame_spans():
    assert fxprims.enable_expr([(10, 11)]) == "between(n,10,10)"
    assert fxprims.enable_expr([(5, 8), (0, 2), (2, 4), (20, 21)]) == \
        "between(n,0,3)+between(n,5,7)+between(n,20,20)"
    assert fxprims.enable_expr([]) == "0"


def test_chain_splitting_respects_quotes_parens_and_escapes():
    assert fxprims.split_chain("negate") == ["negate"]
    assert fxprims.split_chain(
        "eq=brightness='0.1,0.2',hue=s=0") == ["eq=brightness='0.1,0.2'",
                                              "hue=s=0"]
    assert fxprims.split_chain("geq=lum=between(X\\,1\\,2),null") == [
        "geq=lum=between(X\\,1\\,2)", "null"]
    assert fxprims.split_chain(["a", " ", "b"]) == ["a", "b"]
    assert fxprims.filter_names("eq=b=1,negate,eq=c=2") == ["eq", "negate"]
    assert fxprims.with_enable("negate,eq=b=1", "E") == \
        "negate=enable='E',eq=b=1:enable='E'"


def test_window_timeline_path_is_one_gated_chain():
    parts = []
    out = fxprims.window(parts, "v", "o", [(120, 121), (130, 132)],
                         "negate", uid="u")
    assert out == "o"
    assert parts == ["[v]negate=enable='between(n,120,120)+"
                     "between(n,130,131)'[o]"]


def test_window_generic_path_trims_repads_restamps_and_gates_on_n():
    parts = []
    fxprims.window(parts, "v", "o", [(120, 121)], "hflip", uid="u",
                   timeline=False, fps=59.94)
    assert parts == [
        "[v]split[ua][ub]",
        "[ub]trim=start_frame=120:end_frame=121,setpts=PTS-STARTPTS,hflip,"
        "format=yuv420p,tpad=start=120:stop=1:start_mode=add:stop_mode=add:"
        "color=black,setpts=N/59.940/TB[uw]",
        "[ua][uw]overlay=eof_action=pass:enable='between(n,120,120)'[o]"]
    parts = []
    fxprims.window(parts, "v", "o", [(0, 3)], "hflip", uid="u",
                   timeline=False, fps=25)
    assert "tpad=stop=1:" in parts[1]           # nothing before frame 0
    with pytest.raises(ValueError):
        fxprims.window([], "v", "o", [(1, 2)], "hflip", uid="u",
                       timeline=False)


def test_window_with_no_frames_appends_nothing():
    parts = []
    assert fxprims.window(parts, "v", "o", [], "negate", uid="u") == "v"
    assert parts == []


def test_region_and_blend_composite_through_alpha_never_the_main_stream():
    parts = []
    fxprims.region_fx(parts, "v", "o", "3:v", "negate", [(10, 12)], uid="r",
                      fps=25, bbox=(4, 6, 20, 30))
    g = ";".join(parts)
    assert "[v]split[ra][rb]" in g
    assert "crop=20:30:4:6" in g and "alphamerge" in g
    assert "[ra][rw]overlay=x=4:y=6:eof_action=pass" in g
    assert "maskedmerge" not in g and "out_range" not in g
    parts = []
    fxprims.blend(parts, "v", "l", "o", [(10, 12)], uid="b", mode="screen",
                  fps=25, opacity=0.5, bbox=(0, 0, 8, 8))
    g = ";".join(parts)
    assert "blend=all_mode=screen:all_opacity=0.5000" in g
    assert "format=gbrp" in g and "[ba][bw]overlay=x=0:y=0" in g
    with pytest.raises(ValueError):
        fxprims.blend([], "v", "l", "o", [(0, 1)], uid="b", mode="glow",
                      fps=25)
    # nothing to do: the layer and the mask are still consumed
    parts = []
    assert fxprims.blend(parts, "v", "l", "o", [], uid="b", mode="screen",
                         fps=25, mask_label="m") == "v"
    assert parts == ["[l]nullsink", "[m]nullsink"]


def test_mask_upsample_is_guided_on_finals_only():
    parts = []
    fxprims.mask_upsample(parts, "m", "o", W=1080, H=1920, uid="g",
                          preview=False, guide_label="pic")
    assert "guided=radius=8" in parts[-1] and "guidance=on" in parts[-1]
    parts = []
    fxprims.mask_upsample(parts, "m", "o", W=540, H=960, uid="g",
                          preview=True, guide_label="pic")
    assert not any("guided" in p for p in parts)
    assert "[pic]nullsink" in parts


def test_rect_boxes_are_even_and_clamped():
    assert fxprims.rect_box([0.25, 0.25, 0.75, 0.75], 270, 480) == \
        (66, 120, 138, 240)
    assert fxprims.rect_box([0.0, 0.0, 1.0, 1.0], 271, 481) == \
        (0, 0, 270, 480)
    assert fxprims.rect_box([0.5, 0.5, 0.5001, 0.5001], 100, 100) == \
        (50, 50, 2, 2)                           # never below a pixel pair
    assert fxprims.rect_box([0.5, 0.5, 0.5, 0.5], 100, 100) is None


def test_primitives_only_use_filters_the_pinned_set_checks():
    parts = []
    fxprims.window(parts, "a", "b", [(1, 2)], "null", uid="w",
                   timeline=False, fps=25)
    fxprims.region_fx(parts, "b", "c", "m", "null", [(1, 2)], uid="r",
                      fps=25)
    fxprims.blend(parts, "c", "l", "d", [(1, 2)], uid="x", mode="screen",
                  fps=25, mask_label="m2")
    fxprims.track_matte(parts, "f", "g", "t", uid="t", invert=True,
                        matte_has_alpha=True)
    fxprims.mask_upsample(parts, "m3", "u", W=8, H=8, uid="u",
                          preview=False, guide_label="p")
    used = set()
    for p in parts:
        body = p
        while body.startswith("["):
            body = body[body.index("]") + 1:]
        used.update(fxprims.filter_names(body.rsplit("[", 1)[0]))
    assert used <= set(fxprims.PRIMITIVE_FILTERS), used


# ── fxgraph with a stand-in compiler ─────────────────────────────────────

class _Fake:
    """A compiler that tags each stage with a null filter it can be found
    by, optionally failing on demand."""
    FILTERS = ("null", "anull", "negate")

    def __init__(self):
        self.fail = False
        self.prep = None

    def prepare(self, plan, item):
        return self.prep

    def stage(self, ctx, items):
        if self.fail:
            ctx.parts.append("[half]written")
            raise RuntimeError("boom")
        out = f"FX_{ctx.stage}"
        ctx.parts.append(f"[{ctx.label}]null[{out}]")
        return out

    def junction(self, ctx, items):
        out = ctx.uid("fj")
        ctx.parts.append(f"[{ctx.a_label}]trim=end_frame={ctx.h}[{out}a]")
        ctx.parts.append(f"[{ctx.b_label}]trim=start_frame={ctx.h},"
                         f"setpts=PTS-STARTPTS[{out}b]")
        ctx.parts.append(f"[{out}a][{out}b]concat=n=2:v=1:a=0[{out}]")
        return out

    def junction_frames(self, plan, item):
        return item.get("frames")

    def music_bus(self, ctx, items):
        out = ctx.uid("fb")
        ctx.parts.append(f"[{ctx.label}]anull[{out}]")
        return out


@pytest.fixture
def fake(monkeypatch):
    comp = _Fake()
    monkeypatch.setattr(fxgraph, "compiler_for",
                        lambda t: comp if t == "fake" else None)
    return comp


def _item(stage, fid, **kw):
    it = {"id": fid, "type": "fake", "stage": stage}
    it.update(kw)
    return it


def _plan(edl, W=270, H=480, fps=25.0):
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    return fxgraph.prepare(edl, tl=tl, W=W, H=H, fps=fps, preview=True,
                           workdir="/tmp"), tl


def _graph(edl, plan, tl, **kw):
    return renderer.build_filtergraph(
        edl, 20.0, True, tl, None, kw.pop("music_inputs", []),
        {"words": [], "silences": []}, True, W=270, H=480, fps=25.0,
        fx_plan=plan, **kw)


BASES = [
    {"keep": [[0.0, 10.0]]},
    {"keep": [[0.0, 4.0], [5.0, 9.0]], "frame": {"ratio": "9:16"}},
    {"keep": [[0.0, 4.0], [5.0, 9.0]],
     "effects": {"zooms": [{"id": "z", "start": 1.0, "end": 2.0,
                            "strength": 0.3}],
                 "transition": {"style": "dip_black", "duration_s": 0.3,
                                "scope": "every_cut"}}},
]


@pytest.mark.parametrize("raw", BASES)
def test_an_empty_plan_emits_the_exact_legacy_graph(raw):
    edl = schemas.validate_edl(copy.deepcopy(raw), 20.0).model_dump()
    plan, tl = _plan(edl)
    assert plan.empty and not plan.needs_cfr and plan.report() == []
    assert _graph(edl, plan, tl) == _graph(edl, None, tl)
    parts = ["x"]
    assert fxgraph.apply_stage("world", parts, "v", plan) == "v"
    assert fxgraph.apply_music_bus(parts, "a", "m1", plan) == "a"
    assert fxgraph.junction_specs(plan, [(0, 100, 100, 100)]) == []
    assert parts == ["x"]


def test_every_stage_hook_sits_at_its_landmark(fake):
    edl = {"keep": [[0.0, 4.0], [5.0, 9.0]],
           "frame": {"ratio": "9:16"},
           "effects": {"zooms": [{"id": "z", "start": 1.0, "end": 2.0,
                                  "strength": 0.3}],
                       "frame_shifts": [{"id": "fs", "at": 2.0,
                                         "ratio": "1:1"}]},
           "fx": [_item(s, s, start=1.0, end=3.0)
                  for s in fxgraph.PICTURE_STAGES]
           + [_item("music_bus", "mb", music_id="m1")]}
    plan, tl = _plan(edl)
    assert [r["rung"] for r in plan.report()] == ["full"] * 5
    music = [(1, {"id": "m1", "start": 0.0, "end": 8.0, "gain_db": -18},
              30.0)]
    g = _graph(edl, plan, tl, robot_idx=5, wm_ass_path=None, outro_s=2.0,
               card_idx=6, plate_idx=7, plate_box=(200, 400, 35, 40),
               music_inputs=music)
    parts = g.split(";")

    def at(token):
        hits = [i for i, p in enumerate(parts) if token in p]
        assert hits, token
        return hits[0]
    # world: after the grade/stylize/custom block, before the camera
    assert at("[FX_world]") < at("perspective")
    # screen: after the camera, before captions / overlays
    assert at("perspective") < at("[FX_screen]") < at("[FX_pre_captions]")
    # over_type: the last creative stage, before every brand layer
    ot = at("[FX_over_type]")
    assert at("[FX_pre_captions]") < ot
    assert ot < at("[vfs")                               # aspect bars
    assert ot < at("[vsf]")                              # screen-frame plate
    assert ot < at("[wmbot]overlay")                     # watermark
    assert ot < at("[ovid]")                             # end card
    # music_bus: after the item's own chain, feeding the mix
    bus = parts[at("[mus0]anull[fb")]
    out = bus[bus.rindex("[") + 1:-1]
    assert any("amix" in p and f"[{out}]" in p for p in parts)
    assert not any("amix" in p and "[mus0]" in p for p in parts)
    # the block clock is forced (fx address program frames)
    assert plan.needs_cfr and "trim=end_frame=" in g


def test_a_compiler_that_raises_is_rolled_back_and_reported(fake):
    edl = {"keep": [[0.0, 10.0]], "fx": [_item("world", "a", start=1.0,
                                                end=2.0)]}
    plan, _tl = _plan(edl)
    fake.fail = True
    parts = ["keep"]
    assert fxgraph.apply_stage("world", parts, "v", plan) == "v"
    assert parts == ["keep"]
    assert plan.report()[0]["rung"] == "omit"
    assert "boom" in plan.report()[0]["reason"]


def test_prepare_degrades_never_raises(fake, monkeypatch):
    edl = {"keep": [[0.0, 10.0]], "fx": [
        _item("world", "ok", start=1.0, end=2.0),
        _item("world", "late", start=10.0, end=10.5),
        {"id": "u", "type": "nobody_compiles_me", "stage": "world",
         "start": 1.0, "end": 2.0},
        _item("world", "dev", start=1.0, end=2.0, device="subject_fill"),
        _item("music_bus", "bus")]}
    plan, _tl = _plan(edl)
    rows = {r["id"]: r for r in plan.report()}
    assert rows["ok"]["rung"] == "full" and rows["ok"]["est_ms"] > 0
    assert rows["late"]["rung"] == "omit"
    assert "no compiler" in rows["u"]["reason"]
    # the device resolver (0C) omits a device no card names
    assert rows["dev"]["rung"] == "omit"
    assert rows["dev"]["reason"] == "unknown device"
    assert rows["bus"]["rung"] == "omit"
    with faultinject.inject("ffmpeg_filter_missing:null"):
        p2, _ = _plan(edl)
    assert "lacks null" in {r["id"]: r for r in p2.report()}["ok"]["reason"]
    fake.prep = ("substitute", "swap for the cheap look")
    p3, _ = _plan(edl)
    assert {r["id"]: r for r in p3.report()}["ok"]["rung"] == "substitute"
    fake.prep = ("sideways", "?")
    p4, _ = _plan(edl)
    assert {r["id"]: r for r in p4.report()}["ok"]["rung"] == "omit"


def test_junction_items_match_cuts_and_cap_their_windows(fake):
    edl = {"keep": [[0.0, 4.0], [5.0, 9.0]], "fx": [
        _item("junction", "j", at=4.0, frames=40),
        _item("junction", "k", at=4.02),
        _item("junction", "far", at=2.0)]}
    plan, _tl = _plan(edl)
    specs = fxgraph.junction_specs(plan, [(0, 100, 100, 100)])
    assert len(specs) == 1 and specs[0]["frame"] == 100
    # half the shorter shot at most (no neighbouring window can overlap)
    assert specs[0]["h"] == 40
    specs = fxgraph.junction_specs(plan, [(0, 100, 30, 100)])
    assert specs[0]["h"] == 15
    rows = {r["id"]: r for r in plan.report()}
    assert rows["far"]["rung"] == "omit" and "no cut" in rows["far"]["reason"]
    tiny = fxgraph.junction_specs(plan, [(0, 100, 1, 100)])
    assert tiny == []


def test_a_junction_without_a_label_sinks_both_handles(fake, monkeypatch):
    edl = {"keep": [[0.0, 4.0], [5.0, 9.0]],
           "fx": [_item("junction", "j", at=4.0)]}
    plan, _tl = _plan(edl)
    monkeypatch.setattr(fake, "junction", lambda ctx, items: None)
    parts = []
    out = fxgraph.apply_junction(parts, "A", "B",
                                 {"k": 0, "frame": 100, "h": 5,
                                  "items": plan.junction_items}, plan)
    assert out is None and parts == ["[A]nullsink", "[B]nullsink"]


def test_the_junction_stage_builds_handles_and_overlays_the_window(fake):
    raw = {"keep": [[0.0, 4.0], [5.0, 9.0]], "frame": {"ratio": "9:16"},
           "fx": [_item("junction", "j", at=4.0, frames=5)]}
    edl = dict(schemas.validate_edl(
        {k: v for k, v in raw.items() if k != "fx"}, 20.0).model_dump(),
        fx=raw["fx"])
    plan, tl = _plan(edl)
    g = _graph(edl, plan, tl)
    # one more tap of the source for each side's handle...
    assert "split=3[fxsrc][fxh0][fxh1]" in g
    # ...the outgoing handle continues the block's own frame grid
    assert "[fxh0]trim=start=0.000:" in g and "trim=start_frame=100:" \
        "end_frame=105" in g
    assert "[fxh1]trim=start=4.800:" in g
    # the window replaces frames 95..104 after the concat
    assert "enable='between(n,95,104)'" in g
    assert "tpad=start=95:stop=1:start_mode=add" in g


def test_the_report_merges_and_covers_every_item(fake):
    edl = {"keep": [[0.0, 10.0]], "fx": [
        _item("world", "a", start=1.0, end=2.0),
        _item("world", "b", start=3.0, end=4.0)]}
    rows = []
    tok = fxgraph.collect(rows)
    try:
        plan, _tl = _plan(edl)
        plan.note(edl["fx"][1], "omit", "later decision")
    finally:
        fxgraph.stop_collecting(tok)
    merged = fxgraph.merge_report(rows)
    assert [(r["id"], r["rung"]) for r in merged] == [("a", "full"),
                                                      ("b", "omit")]
    assert set(merged[0]) == set(fxgraph.REPORT_KEYS)
    # an item the render never compiled gets its static decision
    rep = fxgraph.job_report(dict(edl, fx=edl["fx"] + [
        _item("over_type", "c", start=1.0, end=2.0)]), rows)
    assert [(r["id"], r["rung"]) for r in rep] == [
        ("a", "full"), ("b", "omit"), ("c", "full")]
    assert fxgraph.job_report({"keep": [[0, 1]]}, []) == []


def test_dropped_types_and_failed_graphs_are_reported_omit(fake):
    rows = []
    tok = fxgraph.collect(rows)
    try:
        fxgraph.note_dropped({"fx": [{"id": "n", "type": "newer"}]}, ["n"])
    finally:
        fxgraph.stop_collecting(tok)
    assert rows[0]["rung"] == "omit" and "'newer'" in rows[0]["reason"]
    edl = {"keep": [[0.0, 10.0]], "fx": [_item("world", "a", start=1.0,
                                                end=2.0)]}
    plan, _tl = _plan(edl)
    fresh = fxgraph.without_fx(plan, RuntimeError("line\nInvalid option"))
    assert fresh.empty
    assert fresh.report()[0]["rung"] == "omit"
    assert "Invalid option" in fresh.report()[0]["reason"]
    assert fxgraph.without_fx(None, RuntimeError()) is None


def test_masks_resolve_by_label_and_the_person_matte_is_a_stub(fake):
    plan, _tl = _plan({"keep": [[0.0, 10.0]]})
    assert fxgraph.mask(plan, "person") is None
    assert fxgraph.program_matte(plan) is None
    assert fxgraph.mask(plan, "glyph:t1@world") is None
    seen = []

    def provider(p, name, space, ctx):
        seen.append((name, space))
        return "pad0"
    fxgraph.register_mask("w0btest", provider)
    try:
        assert fxgraph.mask(plan, "w0btest:x@world") == "pad0"
        assert fxgraph.mask(plan, "w0btest:y", space="screen") == "pad0"
    finally:
        fxgraph._MASK_PROVIDERS.pop("w0btest", None)
    assert seen == [("w0btest:x", "world"), ("w0btest:y", "screen")]


def test_lanes_map_from_the_worker_role(monkeypatch):
    import config
    import devices
    monkeypatch.delenv("CLOUDFLARE_CONTAINER_PROFILE", raising=False)
    for role, lane in (("executor", "interactive"),
                       ("batch_executor", "batch"),
                       ("agent_executor", "agent"),
                       ("mcp_executor", "mcp"), ("shorts_executor", "shorts"),
                       ("worker", "local")):
        monkeypatch.setattr(config, "WORKER_ROLE", role)
        assert fxgraph.lane() == lane
        assert lane in devices.LANES       # the resolver knows every lane
    # Cloudflare's batch containers run the same executor role as the
    # interactive ones; the container profile tells them apart
    monkeypatch.setattr(config, "WORKER_ROLE", "executor")
    monkeypatch.setenv("CLOUDFLARE_CONTAINER_PROFILE", "standard-4")
    assert fxgraph.lane() == "batch"
    monkeypatch.setenv("CLOUDFLARE_CONTAINER_PROFILE", "standard-3")
    assert fxgraph.lane() == "interactive"


def test_the_probe_type_is_absent_from_production_registries():
    # registered only on probe lanes (VALMERA_FX_PROBE=1)
    assert "fx_probe" not in schemas.FX_TYPES
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "w0b_core_check", os.path.join(os.path.dirname(HERE), "fx_schemas",
                                       "core.py"),
        submodule_search_locations=None)
    assert spec is not None
    src = open(spec.origin).read()
    assert "AUTHORABLE: ClassVar[bool] = False" in src
    assert 'os.environ.get(PROBE_ENV, "").strip() == "1"' in src


# ── renderer helpers for junctions ───────────────────────────────────────

def test_cuts_skip_pieces_of_one_kept_span():
    order = [("seg", 0, 2.0), ("seg", 1, 2.0), ("ins", 0, 1.0),
             ("seg", 2, 3.0)]
    keep = [(0.0, 2.0), (2.0, 4.0), (6.0, 9.0)]
    frames = {("seg", 0): 50, ("seg", 1): 50, ("ins", 0): 25,
              ("seg", 2): 75}
    cuts = renderer._fx_cuts(order, keep, [[0.0, 4.0], [6.0, 9.0]], frames)
    assert cuts == [(1, 100, 50, 25), (2, 125, 25, 75)]
    assert renderer._fx_cuts(order, keep, [], {}) == []


def test_handles_come_from_footage_or_freeze():
    keep = [(1.0, 4.0), (5.0, 9.0)]
    frames = {("seg", 0): 75, ("seg", 1): 100}
    a = renderer._fx_handle_src(("seg", 0, 3.0), keep, frames, 5, 25.0,
                                "out", False, {}, 20.0)
    b = renderer._fx_handle_src(("seg", 1, 4.0), keep, frames, 5, 25.0,
                                "in", False, {}, 20.0)
    assert a == {"seg": 0, "frames": 75} and b == {"seg": 1, "start": 4.8}
    # no footage past the out-point / before the floor / not plain footage
    assert renderer._fx_handle_src(("seg", 0, 3.0), keep, frames, 5, 25.0,
                                   "out", False, {}, 4.1) is None
    assert renderer._fx_handle_src(("seg", 0, 3.0), [(0.05, 4.0)],
                                   {("seg", 0): 99}, 5, 25.0, "in", False,
                                   {}, 20.0) is None
    assert renderer._fx_handle_src(("ins", 0, 1.0), keep, frames, 5, 25.0,
                                   "out", False, {}, 20.0) is None
    assert renderer._fx_handle_src(("seg", 0, 3.0), keep, frames, 5, 25.0,
                                   "out", True, {}, 20.0) is None
    assert renderer._fx_handle_src(("seg", 0, 3.0), keep, frames, 5, 25.0,
                                   "out", False, {0: ()}, 20.0) is None


def test_world_and_screen_keep_card_layout_tags_when_a_card_needs_them(fake):
    edl = {"keep": [[0.0, 10.0]], "fx": [_item("world", "w", start=1.0,
                                                end=2.0)]}
    plan, _tl = _plan(edl)
    parts = []
    out = renderer._fx_picture_stage("world", parts, "v", plan,
                                     {"c": [["c0r0", 0.0, 5.0]]}, 25.0)
    g = ";".join(parts)
    assert out == "ltfxwo" and "[v]settb=AVTB" in g and "[FX_world]" in g
    parts = []
    assert renderer._fx_picture_stage("world", parts, "v", plan, {},
                                      25.0) == "FX_world"
    assert parts == ["[v]null[FX_world]"]
    empty, _tl = _plan({"keep": [[0.0, 10.0]]})
    parts = []
    assert renderer._fx_picture_stage("world", parts, "v", empty,
                                      {"c": []}, 25.0) == "v"
    assert parts == []
