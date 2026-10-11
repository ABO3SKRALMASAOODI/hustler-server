"""Track 1B compiler (subject_fx.py) without rendering: the rung each item
takes in ``prepare`` (matte absent / partial / unsteady, resolver
substitutes, regions, footage), the filters it emits (every one listed in
FILTERS, so a lane that lacks one omits the item instead of failing the
render), colour roles and the report hooks."""

import re

import pytest

import fxgraph
import fxprims
import subject_fx
from timeline import Timeline


def _plan(tmp_path, program_s=6.0, keep=((0.0, 6.0),), fps=25.0, W=270,
          H=480):
    return fxgraph.FxPlan(W=W, H=H, fps=fps, preview=True,
                          workdir=str(tmp_path), program_s=program_s,
                          tl=Timeline([list(k) for k in keep]))


def _matte(monkeypatch, status="ready", windows=()):
    def info(plan, label="person"):
        return {"label": label, "status": status, "source": "rvm",
                "reason": f"matte {status}", "windows": list(windows),
                "qa": {}}
    calls = []

    def request(plan, item, label="person", space="world"):
        calls.append((item.get("id"), label, space))
        return info(plan, label)
    monkeypatch.setattr(fxgraph, "matte_info", info)
    monkeypatch.setattr(fxgraph, "request_matte", request)
    counter = {"n": 0}

    def person(plan, name, space, ctx):
        counter["n"] += 1
        return f"pm{counter['n']}"
    monkeypatch.setitem(fxgraph._MASK_PROVIDERS, "person", person)
    return calls


def _sx(fid="a", **kw):
    it = {"id": fid, "type": "subject", "stage": "world", "start": 1.0,
          "end": 2.0, "kind": "fill", "colour": "white"}
    it.update(kw)
    return it


def _bg(fid="b", **kw):
    it = {"id": fid, "type": "background", "stage": "world", "start": 1.0,
          "end": 2.0, "kind": "solid", "colour": "black"}
    it.update(kw)
    return it


C = subject_fx.COMPILER


# ── rungs ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("item, want", [
    (_sx(), ("substitute", "full_frame_flash")),
    (_sx(kind="poster", field="accent"), ("substitute",
                                          "colour_field_card")),
    (_sx(kind="glow"), ("substitute", "halation")),
    (_sx(kind="outline"), ("omit", None)),
    (_sx(kind="negate"), ("omit", None)),
    (_sx(kind="echo", echo={"offsets_f": [4]}), ("omit", None)),
    (_sx(kind="isolate"), ("omit", None)),          # no hue, no matte
    (_bg(), ("omit", None)),
    (_bg(hold_subject=True), ("substitute", "flash_junction")),
])
def test_without_a_matte_each_kind_takes_its_substitute(tmp_path,
                                                        monkeypatch, item,
                                                        want):
    calls = _matte(monkeypatch, "absent")
    plan = _plan(tmp_path)
    got = C.prepare(plan, item)
    assert got[0] == want[0], got
    if want[1]:
        assert subject_fx._state(plan)[item["id"]]["substitute"] == want[1]
        assert want[1] in got[1]
    # the window is still ASKED for: the render that may launch sidecars
    # measures it, so the next render draws the real device (§1.7)
    assert calls == [(item["id"], "person", "world")]


def test_the_resolvers_substitute_is_drawn_and_its_window_asked_for(
        tmp_path, monkeypatch):
    calls = _matte(monkeypatch, "ready")
    plan = _plan(tmp_path)
    plan.devices["a"] = {"rung": "substitute", "substitute":
                         "full_frame_flash", "params": {"intensity": 0.6}}
    assert C.prepare(plan, _sx()) is None    # keeps the resolver's reason
    assert subject_fx._state(plan)["a"]["mode"] == "substitute"
    assert calls == [("a", "person", "world")]


def test_the_matte_is_asked_for_before_its_answer_is_read(tmp_path,
                                                          monkeypatch):
    """Track 1A's program matte answers for the windows ASKED so far
    (matte_track.ProgramMatte.info): read before this item's request, it
    says "no window asked" (every item took its substitute when merged
    with 1A), or describes another item's window."""
    wants = []

    def info(plan, label="person"):
        if not wants:
            return {"label": label, "status": "absent", "source": None,
                    "reason": "no window asked for the matte",
                    "windows": [], "qa": {}}
        return {"label": label, "status": "ready", "source": "rvm",
                "reason": "measured", "qa": {},
                "windows": [{"t0": a, "t1": b, "status": "ready",
                             "ok": True} for a, b in wants]}

    def request(plan, item, label="person", space="world"):
        wants.append((float(item["start"]), float(item["end"])))
        return info(plan, label)
    monkeypatch.setattr(fxgraph, "matte_info", info)
    monkeypatch.setattr(fxgraph, "request_matte", request)
    plan = _plan(tmp_path)
    assert C.prepare(plan, _sx()) is None
    assert subject_fx._state(plan)["a"]["mode"] == "full"
    assert wants == [(1.0, 2.0)]


def test_a_ready_matte_is_requested_in_the_items_space(tmp_path,
                                                       monkeypatch):
    calls = _matte(monkeypatch, "ready")
    plan = _plan(tmp_path)
    assert C.prepare(plan, _sx()) is None
    assert C.prepare(plan, _sx("s", stage="screen", kind="glow")) is None
    assert calls == [("a", "person", "world"), ("s", "person", "screen")]


def test_partial_and_unsteady_mattes_degrade_honestly(tmp_path,
                                                      monkeypatch):
    _matte(monkeypatch, "partial", windows=[
        {"t0": 0.0, "t1": 1.5, "status": "ready", "ok": True},
        {"t0": 1.5, "t1": 3.0, "status": "absent", "ok": True}])
    plan = _plan(tmp_path)
    rung, why = C.prepare(plan, _sx())
    assert rung == "reduced" and "missing for part" in why
    _matte(monkeypatch, "ready", windows=[
        {"t0": 0.0, "t1": 3.0, "status": "ready", "ok": False}])
    plan = _plan(tmp_path)
    rung, why = C.prepare(plan, _sx(kind="outline"))
    assert rung == "reduced" and "glow" in why
    assert subject_fx._state(plan)["a"]["mode"] == "soft"
    # a soft kind is not demoted
    assert C.prepare(plan, _sx("g", kind="glow")) is None


def test_hue_auto_and_a_missing_hue_key_keep_the_subject_only(
        tmp_path, monkeypatch):
    _matte(monkeypatch)
    plan = _plan(tmp_path)
    rung, why = C.prepare(plan, _sx(kind="isolate", hue="auto"))
    assert rung == "reduced" and "auto" in why
    monkeypatch.setitem(fxgraph.MASK_PEERS, "hue", "w1b_no_module:hue_mask")
    rung, why = C.prepare(plan, _sx("b", kind="isolate", hue="#c0392b"))
    assert rung == "reduced" and "1E" in why


def test_sources_outside_the_program_or_unreadable_are_dropped(tmp_path,
                                                               monkeypatch):
    _matte(monkeypatch)
    plan = _plan(tmp_path, keep=((2.0, 8.0),))
    got = C.prepare(plan, _bg(kind="source", source={"kind": "source",
                                                     "src_t": 0.5}))
    assert got[0] == "omit" and "not in this program" in got[1]
    tok = subject_fx.ASSET_LOCALS.set({})
    try:
        import renderer

        def boom(*a, **k):
            raise OSError("no such object")
        monkeypatch.setattr(renderer, "_render_asset_source", boom)
        got = C.prepare(plan, _bg("s", kind="strobe", sources=[
            {"kind": "asset", "asset_key": "media/p/x.mp4"},
            {"kind": "colour", "colour": "white"}]))
        assert got[0] == "reduced" and "dropped" in got[1]
    finally:
        subject_fx.ASSET_LOCALS.reset(tok)
    # in-program source seconds become a program offset
    C.prepare(plan, _bg("p", kind="source", source={"kind": "source",
                                                    "src_t": 4.0}))
    assert subject_fx._state(plan)["p"]["sources"] == [
        {"kind": "program", "from": 50}]


def test_oom_in_a_precompute_omits_through_fxgraph(tmp_path, monkeypatch):
    import faultinject
    _matte(monkeypatch)
    plan = _plan(tmp_path)
    with faultinject.inject("oom"):
        got = C.prepare(plan, _bg(kind="gradient", colour="white",
                                  colour2="black"))
        assert got[0] == "omit" and "MemoryError" in got[1]
        plan2 = _plan(tmp_path)
        # the colour-card substitute's precompute goes through fxgraph
        _matte(monkeypatch, "absent")
        edl = {"fx": [_sx(kind="poster", field="accent")]}
        plan2 = fxgraph.prepare(edl, tl=Timeline([[0.0, 6.0]]), W=270,
                                H=480, fps=25.0, preview=True,
                                workdir=str(tmp_path))
        row = plan2.report()[0]
        assert row["rung"] == "omit" and "MemoryError" in row["reason"]


# ── the graph ───────────────────────────────────────────────────────────

ITEMS = [
    _sx("f", decay_f=2, start=1.0, end=1.16),
    _sx("p", kind="poster", field="accent"),
    _sx("ps", kind="poster", field="white", keep_footage=False,
        colour="black", intensity=0.8),
    _sx("g", kind="glow", radius=0.05),
    _sx("o", kind="outline", width_px=4),
    _sx("n", kind="negate", scope="background"),
    _sx("e", kind="exposure", ev=-1.0, saturation=0.2),
    _sx("i", kind="isolate"),
    _sx("c", kind="echo", start=0.1, end=1.0,
        echo={"mode": "clone", "offsets_f": [4, 8], "tint": "#ff0000"}),
    _sx("r", kind="negate", region={"shape": "rect",
                                    "box": [0.1, 0.1, 0.6, 0.6]}),
    _bg("bs"), _bg("bg", kind="gradient", colour="accent",
                   colour2="#112233", angle=45),
    _bg("bb", kind="blur"), _bg("br", kind="radial_blur"),
    _bg("bp", kind="source", source={"kind": "program", "offset_s": -0.5}),
    _bg("bn", kind="source", source={"kind": "next"}),
    _bg("bh", kind="strobe", hold_subject=True, hold_t=0.5, hold_f=3,
        sources=[{"kind": "colour", "colour": "white"},
                 {"kind": "program", "offset_s": 1.0}]),
]


def _emit(tmp_path, monkeypatch, items, keep=((0.0, 3.0), (3.5, 6.5))):
    _matte(monkeypatch)
    plan = _plan(tmp_path, keep=keep)
    for it in items:
        got = C.prepare(plan, it)
        assert not got or got[0] != "omit", (it["id"], got)
        plan._compilers[it["type"]] = C
        plan.by_stage[it["stage"]].append(it)
    plan.bind_inputs([], 1)
    parts = []
    ctx = fxgraph.StageCtx(plan, "world", parts, "v0")
    out = C.stage(ctx, plan.by_stage["world"])
    return parts, out


def _names(parts):
    names = set()
    for p in parts:
        m = re.match(r"^((?:\[[^\]]+\])*)(.*?)((?:\[[^\]]+\])*)$", p, re.S)
        names |= set(fxprims.filter_names(m.group(2)))
    return names


def test_every_emitted_filter_is_declared(tmp_path, monkeypatch):
    parts, out = _emit(tmp_path, monkeypatch, ITEMS)
    used = _names(parts)
    declared = set(subject_fx.FILTERS)
    assert used <= declared, sorted(used - declared)
    assert out and out != "v0"


@pytest.mark.parametrize("item", ITEMS, ids=lambda i: i["id"])
def test_each_item_is_one_closed_composite(tmp_path, monkeypatch, item):
    """Every label an item emits is consumed exactly once (no dangling or
    double-used pads), and the program enters once and leaves once."""
    parts, out = _emit(tmp_path, monkeypatch, [item])
    made, used = [], []
    for p in parts:
        m = re.match(r"^((?:\[[^\]]+\])*)(.*?)((?:\[[^\]]+\])*)$", p, re.S)
        used += re.findall(r"\[([^\]]+)\]", m.group(1))
        made += re.findall(r"\[([^\]]+)\]", m.group(3))
    assert len(made) == len(set(made)), "a label is produced twice"
    external = {u for u in used if u not in made}
    assert external <= {"v0"} | {f"pm{i}" for i in range(1, 20)} | \
        {f"{i}:v" for i in range(1, 20)}, external
    dangling = set(made) - set(used) - {out}
    assert not dangling, dangling
    assert used.count("v0") == 1


def test_every_restamp_rounds(tmp_path, monkeypatch):
    """setpts truncates: N / 30.000 / TB is 244.99999 for N = 245."""
    parts, _ = _emit(tmp_path, monkeypatch, ITEMS)
    text = ";".join(parts)
    assert "setpts=round(N/" in text
    assert not re.search(r"setpts=N/", text)


def test_frames_are_gated_on_the_program_frame_index(tmp_path, monkeypatch):
    parts, _ = _emit(tmp_path, monkeypatch,
                     [_sx(start=1.0, end=1.08)])
    gate = [p for p in parts if "enable=" in p and "overlay" in p]
    assert gate and "between(n,25,26)" in gate[-1]
    assert not any("between(t" in p for p in parts)


def test_no_items_no_filters(tmp_path):
    plan = _plan(tmp_path)
    parts = []
    assert fxgraph.apply_stage("world", parts, "v0", plan) == "v0"
    assert parts == []


# ── colours and hooks ───────────────────────────────────────────────────

def test_colour_roles_resolve_from_the_look():
    edl = {"captions": {"style": {"highlight_color": "#FF5A36"}}}
    assert subject_fx.resolve_colour("accent", edl) == "#ff5a36"
    assert subject_fx.resolve_colour("white", edl) == "#ffffff"
    assert subject_fx.resolve_colour("#12aB34", edl) == "#12ab34"
    import captions
    plain = subject_fx.accent_hex({})
    assert plain == captions.DEFAULT_HIGHLIGHT.lower()
    pre = subject_fx.accent_hex({"captions": {"style": {"preset": "beast"}}})
    assert re.fullmatch(r"#[0-9a-f]{6}", pre)


def test_rgb_to_yuv_uses_the_hd_matrix():
    assert subject_fx.rgb_to_yuv((232, 69, 44), 1080) == (103, 101, 201)
    assert subject_fx.rgb_to_yuv((232, 69, 44), 480) == (115, 93, 201)
    assert subject_fx.rgb_to_yuv((255, 255, 255)) == (235, 128, 128)


def test_cost_and_landing_hooks(tmp_path):
    plan = _plan(tmp_path, fps=30.0)
    it = _sx(start=2.0, end=2.1)
    assert C.landing(plan, it) == [60]
    assert fxgraph.landing_frames(plan, it) == [60]
    small = C.cost_ms(plan, it)
    big = C.cost_ms(plan, _sx(kind="echo", start=2.0, end=4.0,
                              echo={"offsets_f": [2, 4, 6]}))
    assert 0 < small < big


def test_the_plan_edl_is_read_from_fxgraph_prepare(tmp_path, monkeypatch):
    """The accent needs the EDL; the plan does not carry it in Wave 1, so
    the compiler reads it once from fxgraph.prepare's own frame."""
    _matte(monkeypatch, "absent")
    seen = {}
    real = subject_fx._Compiler.prepare

    def spy(self, plan, item):
        seen["edl"] = subject_fx.plan_edl(plan)
        return real(self, plan, item)
    monkeypatch.setattr(subject_fx._Compiler, "prepare", spy)
    edl = {"keep": [[0.0, 6.0]], "captions": {"style": {
        "highlight_color": "#00ff88"}}, "fx": [_sx()]}
    fxgraph.prepare(edl, tl=Timeline([[0.0, 6.0]]), W=270, H=480, fps=25.0,
                    preview=True, workdir=str(tmp_path))
    assert seen["edl"]["captions"]["style"]["highlight_color"] == "#00ff88"


# ── time-shifted branches stay bounded (review) ─────────────────────────

def test_a_cut_away_source_second_is_read_from_the_main_video(tmp_path,
                                                             monkeypatch):
    """`source` = the main video from that source second (another shot):
    footage the program does not play is read from the render's main video
    (its own input), framed like the picture."""
    _matte(monkeypatch)
    plan = _plan(tmp_path, keep=((2.0, 8.0),))
    plan.media = {"src_path": "/x/main.mp4"}
    assert C.prepare(plan, _bg(kind="source", source={
        "kind": "source", "src_t": 0.5})) is None
    (src,) = subject_fx._state(plan)["b"]["sources"]
    assert src["kind"] == "main" and not src["freeze"]
    args = dict(plan.inputs)[src["key"]]
    assert args[:2] == ["-ss", "0.500"] and args[-2:] == ["-i", "/x/main.mp4"]
    assert "crop=270:480" in src["fit"]


def test_a_plate_far_ahead_is_never_queued_in_the_graph(tmp_path,
                                                        monkeypatch):
    """A plate from program frames far AHEAD makes split queue every frame
    up to it (+1.3 GB for 17 s at 1080x1920, measured): past the read-ahead
    budget it comes from the main video, or is dropped without one."""
    _matte(monkeypatch)
    plan = _plan(tmp_path, program_s=60.0, keep=((0.0, 60.0),))
    far = _bg(kind="source", source={"kind": "program", "offset_s": 40.0})
    near = _bg("n", kind="source", source={"kind": "program",
                                           "offset_s": 1.0})
    rung, why = C.prepare(plan, far)
    assert rung == "omit" and "away" in why
    assert C.prepare(plan, near) is None
    assert subject_fx._state(plan)["n"]["sources"] == [
        {"kind": "program", "from": 50}]
    # from the PAST a long plate holds its own frames until its window
    # starts (+1.36 GB for 10 s from 11 s earlier, measured)
    past = _bg("p", kind="source", start=40.0, end=50.0,
               source={"kind": "program", "offset_s": -30.0})
    rung, why = C.prepare(plan, past)
    assert rung == "omit" and "away" in why
    short = _bg("q", kind="source", start=40.0, end=40.5,
                source={"kind": "program", "offset_s": -30.0})
    assert C.prepare(plan, short) is None          # 13 frames: in the graph
    plan.media = {"src_path": "/x/main.mp4"}
    plan.__dict__.pop("_w1b_env", None)
    assert C.prepare(plan, far) is None
    (src,) = subject_fx._state(plan)["b"]["sources"]
    assert src["kind"] == "main"
    assert dict(plan.inputs)[src["key"]][:2] == ["-ss", "41.000"]
    assert C.prepare(plan, past) is None
    (src,) = subject_fx._state(plan)["p"]["sources"]
    assert src["kind"] == "main"
    assert dict(plan.inputs)[src["key"]][:2] == ["-ss", "10.000"]


def test_echo_copies_beyond_the_buffer_budget_are_left_out(tmp_path,
                                                           monkeypatch):
    """Copy k queues k frames (offsets 30..240 held +3 GB at 1080x1920,
    measured): the nearest copies within ECHO_BUFFER_BUDGET_MB are drawn,
    the same on every lane."""
    _matte(monkeypatch)
    cap = subject_fx.budget_frames(subject_fx.ECHO_BUFFER_BUDGET_MB,
                                   subject_fx.QUEUED_BYTES_PER_PX)
    offs = [30, 60, 90, 120, 150, 180, 210, 240]
    for W, H in ((270, 480), (1080, 1920)):
        plan = _plan(tmp_path, W=W, H=H)
        rung, why = C.prepare(plan, _sx(kind="echo", echo={
            "mode": "trail", "offsets_f": offs}))
        kept = subject_fx._state(plan)["a"]["offsets"]
        assert rung == "reduced" and "left out" in why
        assert kept == offs[:len(kept)] and sum(kept) <= cap < sum(
            offs[:len(kept) + 1])
    plan = _plan(tmp_path)
    assert C.prepare(plan, _sx(kind="echo", echo={"offsets_f": [4, 8]})) \
        is None


def test_a_held_frame_survives_dropped_plates_and_stays_bounded(
        tmp_path, monkeypatch):
    _matte(monkeypatch)
    plan = _plan(tmp_path, keep=((2.0, 8.0),))
    rung, why = C.prepare(plan, _bg(kind="strobe", hold_subject=True,
                                    hold_t=0.9, sources=[
                                        {"kind": "source", "src_t": 0.5},
                                        {"kind": "colour",
                                         "colour": "white"}]))
    assert rung == "reduced" and "dropped" in why
    assert subject_fx._state(plan)["b"]["hold"] == fxprims.frame_at(0.9,
                                                                    25.0)
    plan = _plan(tmp_path, program_s=60.0, keep=((0.0, 60.0),))
    rung, why = C.prepare(plan, _bg(hold_subject=True, hold_t=50.0))
    assert rung == "reduced" and "held on the window's first frame" in why
    assert subject_fx._state(plan)["b"]["hold"] == 25


def test_main_video_plates_compile_to_closed_declared_composites(
        tmp_path, monkeypatch):
    _matte(monkeypatch)
    plan = _plan(tmp_path, keep=((2.0, 8.0),))
    plan.media = {"src_path": "/x/main.mp4"}
    items = [_bg("m", kind="source", source={"kind": "source",
                                             "src_t": 0.5}),
             _bg("s", kind="strobe", start=3.0, end=4.0, sources=[
                 {"kind": "source", "src_t": 0.2},
                 {"kind": "colour", "colour": "white"}])]
    for it in items:
        assert C.prepare(plan, it) is None, it["id"]
        plan._compilers[it["type"]] = C
        plan.by_stage["world"].append(it)
    plan.bind_inputs([], 1)
    parts = []
    out = C.stage(fxgraph.StageCtx(plan, "world", parts, "v0"),
                  plan.by_stage["world"])
    assert _names(parts) <= set(subject_fx.FILTERS)
    made, used = [], []
    for p in parts:
        m = re.match(r"^((?:\[[^\]]+\])*)(.*?)((?:\[[^\]]+\])*)$", p, re.S)
        used += re.findall(r"\[([^\]]+)\]", m.group(1))
        made += re.findall(r"\[([^\]]+)\]", m.group(3))
    assert set(made) - set(used) == {out}
    assert used.count("v0") == 1


def test_an_item_that_fails_to_compile_is_rolled_back_alone(tmp_path,
                                                             monkeypatch):
    """fxgraph rolls back a whole compiler group: one bad item would omit
    every subject effect and background of the stage. The compiler rolls
    the item back alone, closes the matte pads it took, and the program
    label carries on."""
    def boom(self, label):
        self.raw_mask()                        # takes a pad, then fails
        raise RuntimeError("synthetic emit failure")
    monkeypatch.setattr(subject_fx._Emit, "k_glow", boom)
    items = [_sx("a", start=1.0, end=1.08),
             _sx("g", kind="glow", start=1.5, end=2.0),
             _bg("b", start=2.5, end=3.0)]
    parts, out = _emit(tmp_path, monkeypatch, items)
    made, used = [], []
    for p in parts:
        m = re.match(r"^((?:\[[^\]]+\])*)(.*?)((?:\[[^\]]+\])*)$", p, re.S)
        used += re.findall(r"\[([^\]]+)\]", m.group(1))
        made += re.findall(r"\[([^\]]+)\]", m.group(3))
    assert len(made) == len(set(made))
    external = {u for u in used if u not in made}
    assert external <= {"v0"} | {f"pm{i}" for i in range(1, 20)} | \
        {f"{i}:v" for i in range(1, 20)}, external
    assert set(made) - set(used) == {out}
    taken = {u for u in used if u.startswith("pm")}
    assert len(taken) == 3                     # the glow's pad is closed
    assert any(p.endswith("nullsink") and p.startswith("[pm")
               for p in parts)
    assert used.count("v0") == 1
