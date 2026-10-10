"""OPTIONAL jump-cut concealment (judges, Oct 2026; worker/cut_steps.py).

Same-angle jump cuts left by pause removal pop (the Jobs card: the head and
hands jumped inside a constant frame four times in the first 4 s). The
owner's rule is that zooms are optional, never a rule, so the fix is a TOOL
the editor chooses — conceal_jump_cuts — never a default: hard (ramp 0)
alternating framing steps on just the bare same-angle cuts that measurably
pop, a cut-step zoom on full-frame footage and a scale of the SOURCE crop on
a picture card (wide when the source is already near its upscale cap).

Run:  python -m pytest tests/test_cut_steps.py -q     (from worker/)
"""
import json
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402
import cut_steps  # noqa: E402
import motion_tools  # noqa: E402
import picture_cards  # noqa: E402
import renderer  # noqa: E402
import taste  # noqa: E402
from schemas import default_edl, edl_signature, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

FPS = 30.0
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── measuring the pop ─────────────────────────────────────────────────────

def _frame(x, seed=0, w=160, h=120):
    rng = np.random.default_rng(seed)
    g = (40 + rng.integers(0, 6, size=(h, w))).astype(np.uint8)
    g[30:90, int(x):int(x) + 40] = 220              # the speaker
    return g


def test_a_jump_across_the_cut_pops_and_the_speakers_own_motion_does_not():
    rect = [0.0, 0.0, 1.0, 1.0]
    before = [_frame(40 + k, seed=k) for k in range(3)]       # 1 px a frame
    jumped = [_frame(75 + k, seed=10 + k) for k in range(3)]  # 32 px across the cut
    score = cut_steps.pop_score(before, jumped, rect)
    assert cut_steps.pops(score), score
    flowing = [_frame(43 + k, seed=10 + k) for k in range(3)]  # it just kept moving
    score = cut_steps.pop_score(before, flowing, rect)
    assert not cut_steps.pops(score), score
    # a fast gesture on both sides is motion, not a pop
    waving = [_frame(40 + 12 * k, seed=k) for k in range(3)]
    after = [_frame(40 + 12 * (k + 3), seed=10 + k) for k in range(3)]
    assert not cut_steps.pops(cut_steps.pop_score(waving, after, rect))
    # measured only inside what the frame shows: a jump outside it is no pop
    assert not cut_steps.pops(cut_steps.pop_score(before, jumped, [0.0, 0.0, 0.2, 1.0]))
    assert cut_steps.pop_score(before[:1], jumped, rect) is None


# ── what a step does to a card ────────────────────────────────────────────

JOBS_BOX = [0.06, 0.3042, 0.94, 0.6758]
JOBS_SRC = [0.19, 0.077, 0.955, 0.85]


def test_a_card_steps_tight_with_room_and_wide_at_its_upscale_cap():
    card = {"box": JOBS_BOX, "source": JOBS_SRC}
    k, why = cut_steps.card_step_scale(card, 10.0, 0.08, 646, 480, 1080, 1920)
    assert k == pytest.approx(1 / 1.08, abs=1e-3) and why.startswith("wide")
    hd = {"box": JOBS_BOX, "source": [0.3, 0.1, 0.7, 0.6]}
    k, why = cut_steps.card_step_scale(hd, 10.0, 0.08, 1920, 1080, 1080, 1920)
    assert k == pytest.approx(1.08) and why.startswith("tight")
    whole = {"box": JOBS_BOX, "source": [0.0, 0.0, 1.0, 1.0]}
    k, why = cut_steps.card_step_scale(whole, 10.0, 0.08, 480, 360, 1080, 1920)
    assert k is None and "no room" in why


def test_source_at_scales_the_rect_inside_its_step_and_stays_in_the_frame():
    card = validate_edl(dict(default_edl(60.0), effects={"picture_cards": [{
        "id": "c", "start": 0, "end": 30, "box": JOBS_BOX, "source": [0.0, 0.0, 0.8, 0.8],
        "cut_steps": [{"t0": 5.0, "t1": 8.0, "scale": 0.86}]}]}),
        60.0).model_dump()["effects"]["picture_cards"][0]
    assert picture_cards.source_at(card, 4.0) == [0.0, 0.0, 0.8, 0.8]
    wide = picture_cards.source_at(card, 6.0)
    assert wide[2] - wide[0] == pytest.approx(0.8 / 0.86) and wide[0] == pytest.approx(0.0)
    tight = picture_cards.step_rect([0.2, 0.2, 0.6, 0.6], 1.25)
    assert tight == pytest.approx([0.24, 0.24, 0.56, 0.56])
    assert picture_cards.step_scale_at(card, 9.0) is None


def test_cut_steps_are_optional_schema_fields_that_keep_old_signatures():
    edl = default_edl(60.0)
    edl["effects"] = {"zooms": [{"id": "z", "start": 1.0, "end": 2.0, "strength": 0.1}],
                      "picture_cards": [{"id": "c", "start": 0, "end": 10, "box": JOBS_BOX,
                                         "source": JOBS_SRC}]}
    before = edl_signature(validate_edl(json.loads(json.dumps(edl)), 60.0).model_dump())
    assert "cut_step" not in before
    out = validate_edl(json.loads(json.dumps(edl)), 60.0).model_dump()
    assert out["effects"]["zooms"][0]["cut_step"] is None
    assert out["effects"]["picture_cards"][0]["cut_steps"] is None
    # a program card or a stack has no source rect to step: dropped
    edl["effects"]["picture_cards"][0].pop("source")
    edl["effects"]["picture_cards"][0]["cut_steps"] = [{"t0": 1, "t1": 2, "scale": 1.08}]
    assert validate_edl(edl, 60.0).model_dump()["effects"]["picture_cards"][0]["cut_steps"] is None
    for bad in ({"t0": 2, "t1": 1, "scale": 1.08}, {"t0": 1, "t1": 2, "scale": 1.5}):
        edl["effects"]["picture_cards"][0]["source"] = JOBS_SRC
        edl["effects"]["picture_cards"][0]["cut_steps"] = [bad]
        with pytest.raises(Exception):
            validate_edl(edl, 60.0)


# ── the critics ───────────────────────────────────────────────────────────

def _bare(edl):
    edl = validate_edl(edl, 60.0).model_dump(exclude_none=True)
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return [r["t"] for r in taste.uncovered_jump_cuts(edl, {"video": {"fps": FPS}}, tl)]


KEEP = [[0, 4], [5, 8], [9, 12]]


def test_a_written_step_covers_its_cut_and_its_release():
    step = {"id": "cs1", "start": 4.0, "end": 7.0, "strength": 0.12, "ramp_s": 0.0,
            "cut_step": True}
    assert _bare({"keep": KEEP}) == [4.0, 7.0]
    assert _bare({"keep": KEEP, "effects": {"zooms": [step]}}) == []
    # round 4: a step under 10% stutters (the judged 7.4% card steps) — no cover
    small = dict(step, strength=0.07)
    assert _bare({"keep": KEEP, "effects": {"zooms": [small]}}) == [4.0, 7.0]
    card = {"id": "c", "start": 0, "end": 10, "box": JOBS_BOX, "source": JOBS_SRC,
            "cut_steps": [{"t0": 5.0, "t1": 8.0, "scale": round(1 / 1.12, 4)}]}
    assert _bare({"keep": KEEP, "effects": {"picture_cards": [card]}}) == []
    assert _bare({"keep": KEEP, "effects": {"picture_cards": [dict(card, cut_steps=[
        {"t0": 5.0, "t1": 8.0, "scale": 0.926}])]}}) == [4.0, 7.0]
    assert _bare({"keep": KEEP, "effects": {"picture_cards": [
        dict(card, cut_steps=None)]}}) == [4.0, 7.0]


def test_cut_steps_are_not_zoom_moves_for_the_rhythm_critics():
    import quality_verifier
    keep = [[i * 4.0, i * 4.0 + 3.5] for i in range(10)]
    tl = Timeline(keep, [], [])
    steps = [{"id": f"cs{i}", "start": round(3.5 * i, 2), "end": round(3.5 * (i + 1), 2),
              "strength": 0.08, "ramp_s": 0.0, "cx": 0.5, "cy": 0.4, "cut_step": True,
              "purpose": cut_steps.PURPOSE, "target_measured": True}
             for i in range(1, 9, 2)]
    edl = validate_edl({"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"},
                        "effects": {"zooms": steps}}, 120.0).model_dump(exclude_none=True)
    index = {"video": {"width": 1080, "height": 1920, "fps": 30.0}, "words": [],
             "shots": [{"id": 0, "start": 0.0, "end": 120.0}]}
    found = taste.critique(edl, index, tl, 1080, 1920, "")
    assert not any("zoom" in f for f in found), found
    rows = quality_verifier._zoom_findings(edl, index)
    assert rows == [], rows


# ── the tool ──────────────────────────────────────────────────────────────

class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, edl, w=1920, h=1080, duration=60.0):
        self.duration = duration
        self.index = {"video": {"width": w, "height": h, "fps": FPS},
                      "words": [], "shots": [{"id": 1, "start": 0.0, "end": duration}]}
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []
        self.workdir = "/nonexistent"

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"

    def proxy_path(self):
        return __file__                         # "decodable"; measure_pop is stubbed


@pytest.fixture
def stubbed(monkeypatch):
    """Pops at the joins named in `popping` (by the source second after them);
    a face at (0.5, 0.35) of the output frame."""
    state = {"popping": set(), "measured": []}

    def measure_pop(path, e0, s1, rect, fps, w, h):
        state["measured"].append(s1)
        hot = round(s1, 2) in state["popping"]
        return {"cut": 0.05 if hot else 0.004, "natural": 0.005,
                "ratio": 10.0 if hot else 0.8}
    monkeypatch.setattr(cut_steps, "measure_pop", measure_pop)
    monkeypatch.setattr(motion_tools, "jump_cut_measure", lambda ctx: None)
    monkeypatch.setattr(agent_tools, "_face_at_source_moments",
                        lambda ctx, edl, ts: {t: (0.5, 0.35) for t in ts})
    return state


def _full(keep):
    return {"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"}}


def test_full_frame_cuts_that_pop_get_hard_alternating_zoom_steps(stubbed):
    keep = [[0, 4], [5, 8], [9, 12], [13, 16], [17, 20]]     # cuts at 4, 7, 10, 13
    stubbed["popping"] = {5.0, 9.0, 17.0}                   # 4, 7 and 13 pop; 10 does not
    ctx = _Ctx(_full(keep))
    out = cut_steps.conceal_jump_cuts(ctx)
    assert out.startswith("EDL v1"), out
    zooms = ctx.latest_edl()["json"]["effects"]["zooms"]
    assert [(z["start"], z["end"]) for z in zooms] == [(4.0, 7.0), (13.0, 16.0)]
    for z in zooms:
        assert z["cut_step"] is True and z["ramp_s"] == 0.0 and z["strength"] == 0.12
        assert (z.get("mode") or "punch") == "punch" and z["cy"] == 0.35
        assert z["cx"] in (None, 0.5)           # a centred x is stored canonically
        assert z["target_measured"] is True and z["id"].startswith("cs")
    # 7.0 pops and is covered by the step releasing there; 10 is left bare
    assert "7.00s: steps back to the base framing here" in out
    assert "Left bare (no measurable pop): 10s" in out
    assert "optional cut hygiene" in out and "mode='off'" in out
    # re-running replaces the earlier steps instead of stacking them
    out = cut_steps.conceal_jump_cuts(ctx)
    assert len(ctx.latest_edl()["json"]["effects"]["zooms"]) == 2
    assert "Replaced the 2 earlier cut step(s)" in out
    # off removes them all; a second off has nothing to do and writes nothing
    out = cut_steps.conceal_jump_cuts(ctx, mode="off")
    assert out.startswith("EDL v3")
    assert not (ctx.latest_edl()["json"].get("effects") or {}).get("zooms")
    n = len(ctx.writes)
    out = cut_steps.conceal_jump_cuts(ctx, mode="off")
    assert "Nothing to remove" in out and len(ctx.writes) == n


def test_steps_never_touch_an_editors_own_zooms(stubbed):
    keep = [[0, 4], [5, 8], [9, 12]]
    stubbed["popping"] = {5.0, 9.0}
    edl = _full(keep)
    edl["effects"] = {"zooms": [{"id": "zm1", "start": 8.0, "end": 9.5, "strength": 0.12,
                                 "mode": "ease"}]}
    ctx = _Ctx(edl)
    cut_steps.conceal_jump_cuts(ctx)
    cut_steps.conceal_jump_cuts(ctx, mode="off")
    assert [z["id"] for z in ctx.latest_edl()["json"]["effects"]["zooms"]] == ["zm1"]


def test_a_card_alternates_its_source_crop_wide_on_a_capped_source(stubbed):
    keep = [[0, 4], [5, 8], [9, 12], [13, 16]]
    stubbed["popping"] = {5.0, 13.0}
    edl = _full(keep)
    edl["effects"] = {"picture_cards": [{"id": "card", "start": 0, "end": 13, "box": JOBS_BOX,
                                         "source": JOBS_SRC, "entrance": "none", "exit": "none"}]}
    ctx = _Ctx(edl, 646, 480)
    out = cut_steps.conceal_jump_cuts(ctx)
    assert out.startswith("EDL v1"), out
    fx = ctx.latest_edl()["json"]["effects"]
    assert not fx.get("zooms")
    steps = fx["picture_cards"][0]["cut_steps"]
    assert steps == [{"t0": 5.0, "t1": 8.0, "scale": pytest.approx(1 / 1.12, abs=1e-3)},
                     {"t0": 13.0, "t1": 16.0, "scale": pytest.approx(1 / 1.12, abs=1e-3)}]
    assert "wide 12%" in out and "tighter would soften it" in out
    # every stepped cut now reads as a framing change to the critic
    edl = validate_edl(ctx.latest_edl()["json"], 60.0).model_dump(exclude_none=True)
    tl = Timeline(edl["keep"])
    assert taste.uncovered_jump_cuts(edl, ctx.index, tl) == []
    # 7.0 did not pop, but the first step ends there: the framing steps back
    assert "Also changes framing on (a step ending there): 7s" in out


def test_at_names_the_cuts_and_bad_arguments_are_refused(stubbed):
    keep = [[0, 4], [5, 8], [9, 12]]
    ctx = _Ctx(_full(keep))
    out = cut_steps.conceal_jump_cuts(ctx)
    assert "No bare jump cut measurably pops" in out and not ctx.writes
    out = cut_steps.conceal_jump_cuts(ctx, at=[7.02, 30.0])
    assert out.startswith("EDL v1"), out
    assert [(z["start"], z["end"]) for z in ctx.latest_edl()["json"]["effects"]["zooms"]] == [
        (7.0, 10.0)]
    assert "Not a bare same-angle jump cut: 30s" in out
    for kw, why in (({"step": 0.03}, "outside"), ({"step": 0.2}, "outside"),
                    ({"mode": "zoom"}, "mode is"), ({"at": ["x"]}, "program seconds")):
        assert cut_steps.conceal_jump_cuts(ctx, **kw).startswith("REJECTED"), kw


def test_conceal_is_a_registered_optional_tool_and_never_a_default():
    assert "conceal_jump_cuts" in agent_tools.TOOLS
    assert "conceal_jump_cuts" in agent_tools.TOOL_DOMAINS["motion"]
    assert "conceal_jump_cuts" in agent_tools.WRITE_TOOLS
    desc = agent_tools.TOOLS["conceal_jump_cuts"][1]
    assert "OPTIONAL" in desc and "never a default" in desc
    assert agent_tools.TOOLS["conceal_jump_cuts"][2]["mode"]["enum"] == [
        "report", "scale_step", "off"]
    # nothing that runs by default writes a cut step: looks, planners,
    # directors and the shorts pipeline never name the tool or the flag
    for name in ("motion_planner.py", "director.py", "shorts.py", "motion_captions.py",
                 "renderer.py", "edit_batch.py"):
        src = open(os.path.join(HERE, name), encoding="utf-8").read()
        assert "conceal_jump_cuts" not in src and "cut_step=True" not in src, name
    looks = agent_tools.LOOKS
    assert "cut_step" not in json.dumps(looks, default=str)
    assert not re.search(r"cut_steps?\b", json.dumps(default_edl(30.0)))


def test_taste_and_the_tool_agree_on_the_smallest_step():
    assert taste.CUT_STEP_MIN == cut_steps.CUT_STEP_MIN == cut_steps.STEP_MIN


# ── the render ────────────────────────────────────────────────────────────

def test_a_stepped_follow_block_keeps_its_rect_inside_the_source():
    parts = []
    tmap, kspan = None, None
    fb = (tmap, kspan, [0.0, 1.0], [0.55, 0.6], [0.39, 0.39], None)
    rect = picture_cards.step_rect([0.19, 0.0, 0.955, 0.773], 1 / 1.08)
    picture_cards.layout_filter(parts, "in", "out", 1080, 1920, 30.0,
                                [(JOBS_BOX, rect)], "t", src_size=(646, 480),
                                seg_dur=1.0, follow_block=fb, bounded=True)
    free = []
    picture_cards.layout_filter(free, "in", "out", 1080, 1920, 30.0,
                                [(JOBS_BOX, rect)], "t", src_size=(646, 480),
                                seg_dur=1.0, follow_block=fb, bounded=False)
    assert parts != free                         # the wide rect was held inside
    unstepped = []
    picture_cards.layout_filter(unstepped, "in", "out", 1080, 1920, 30.0,
                                [(JOBS_BOX, [0.19, 0.0, 0.955, 0.773])], "t",
                                src_size=(646, 480), seg_dur=1.0, follow_block=fb)
    again = []
    picture_cards.layout_filter(again, "in", "out", 1080, 1920, 30.0,
                                [(JOBS_BOX, [0.19, 0.0, 0.955, 0.773])], "t",
                                src_size=(646, 480), seg_dur=1.0, follow_block=fb,
                                bounded=True)
    assert again == unstepped                    # in bounds: nothing changes


def test_the_render_composes_a_stepped_card_block_from_the_scaled_rect():
    edl = validate_edl({"keep": [[0, 4], [5, 8]], "frame": {"ratio": "9:16", "mode": "crop"},
                        "effects": {"picture_cards": [{
                            "id": "card", "start": 0, "end": 7, "box": JOBS_BOX,
                            "source": JOBS_SRC, "entrance": "none", "exit": "none",
                            "cut_steps": [{"t0": 5.0, "t1": 8.0, "scale": 0.926}]}]}},
                       60.0).model_dump()
    tl = Timeline(edl["keep"])
    card = edl["effects"]["picture_cards"][0]
    graph = renderer.build_filtergraph(
        edl, 60.0, False, tl, None, [], {}, False, W=1080, H=1920, fps=30,
        frame_mode="crop", src_w=646, src_h=480, silence_idx=1,
        picture_card_inputs=[(2, card)])
    base = picture_cards._single_panel(1080, 1920, JOBS_BOX, JOBS_SRC, (646, 480))
    wide = picture_cards._single_panel(1080, 1920, JOBS_BOX,
                                       picture_cards.step_rect(JOBS_SRC, 0.926), (646, 480))
    assert base != wide and base in graph and wide in graph


def test_a_card_edge_inside_the_shot_takes_no_step_and_no_zoom(stubbed):
    keep = [[0, 4], [5, 8], [9, 12]]
    stubbed["popping"] = {9.0}                              # the cut at 7.0 pops
    edl = _full(keep)
    edl["effects"] = {"picture_cards": [{"id": "card", "start": 0, "end": 8.5, "box": JOBS_BOX,
                                         "source": JOBS_SRC, "entrance": "none", "exit": "none"}]}
    ctx = _Ctx(edl, 646, 480)
    out = cut_steps.conceal_jump_cuts(ctx)
    assert "No cut could take a step" in out and "starts or ends inside that shot" in out
    assert not ctx.writes


def test_without_decodable_footage_it_says_so_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(motion_tools, "jump_cut_measure", lambda ctx: None)
    ctx = _Ctx(_full([[0, 4], [5, 8]]))
    ctx.proxy_path = lambda: None
    out = cut_steps.conceal_jump_cuts(ctx)
    assert "could not be measured here" in out and "at=[...]" in out and not ctx.writes


def test_a_step_never_flattens_the_punch_an_editor_put_on_the_next_cut(stubbed):
    keep = [[0, 4], [5, 8], [9, 12]]                       # cuts at 4 and 7
    stubbed["popping"] = {5.0}
    edl = _full(keep)
    edl["effects"] = {"zooms": [{"id": "zm1", "start": 7.0, "end": 9.0, "strength": 0.12,
                                 "ramp_s": 0.0}]}
    ctx = _Ctx(edl)
    out = cut_steps.conceal_jump_cuts(ctx)
    assert "would shrink that cut's framing change" in out and "zm1" in out, out
    assert not ctx.writes


# ── review fixes (Oct 2026) ───────────────────────────────────────────────

def test_a_card_step_holds_its_whole_render_block_through_a_camera_change(stubbed):
    keep = [[0, 4], [5, 8], [9, 12]]                   # joins at program 4 and 7
    stubbed["popping"] = {5.0}
    edl = _full(keep)
    edl["effects"] = {"picture_cards": [{"id": "card", "start": 0, "end": 10, "box": JOBS_BOX,
                                         "source": JOBS_SRC, "entrance": "none", "exit": "none"}]}
    ctx = _Ctx(edl, 646, 480)
    # a second camera angle starts at source 6.5 (program 5.5), INSIDE the
    # kept span [5, 8]: the renderer composes that span as one card block
    ctx.index["shots"] = [{"id": 1, "start": 0.0, "end": 6.5},
                          {"id": 2, "start": 6.5, "end": 60.0}]
    out = cut_steps.conceal_jump_cuts(ctx)
    assert out.startswith("EDL v1"), out
    steps = ctx.latest_edl()["json"]["effects"]["picture_cards"][0]["cut_steps"]
    assert [(st["t0"], st["t1"]) for st in steps] == [(5.0, 8.0)]
    assert "until 7.00s" in out
    graph_card = ctx.latest_edl()["json"]["effects"]["picture_cards"][0]
    assert picture_cards.step_scale_at(graph_card, 6.5) is not None   # the block's midpoint


def test_a_recut_that_removes_a_steps_cut_drops_the_step():
    from timeline import remap_program_items
    step = {"id": "cs1", "start": 4.0, "end": 7.0, "strength": 0.08, "ramp_s": 0.0,
            "cut_step": True, "cx": 0.5, "cy": 0.35}
    plain = {"id": "zm1", "start": 8.0, "end": 9.0, "strength": 0.1}
    keep = [[0, 4], [5, 8], [9, 12], [13, 16]]
    edl = validate_edl({"keep": keep, "effects": {"zooms": [step, plain]}},
                       60.0).model_dump(exclude_none=True)
    old = Timeline(edl["keep"])
    # an unrelated trim earlier: the step rides its footage and keeps its cuts
    moved = json.loads(json.dumps(edl))
    moved["keep"] = [[0, 3], [5, 8], [9, 12], [13, 16]]
    notes = remap_program_items(moved, old, Timeline(moved["keep"]))
    z = {x["id"]: x for x in moved["effects"]["zooms"]}
    assert (z["cs1"]["start"], z["cs1"]["end"]) == (3.0, 6.0), notes
    # restoring the pause it concealed: the jump cut is gone, so is the step
    healed = json.loads(json.dumps(edl))
    healed["keep"] = [[0, 8], [9, 12], [13, 16]]
    notes = remap_program_items(healed, old, Timeline(healed["keep"]))
    assert [x["id"] for x in healed["effects"]["zooms"]] == ["zm1"]
    assert any("cut step cs1 was removed" in n and "conceal_jump_cuts" in n for n in notes)
    # restoring the pause it released on: it would step back mid-shot
    released = json.loads(json.dumps(edl))
    released["keep"] = [[0, 4], [5, 12], [13, 16]]
    remap_program_items(released, old, Timeline(released["keep"]))
    assert [x["id"] for x in released["effects"]["zooms"]] == ["zm1"]
