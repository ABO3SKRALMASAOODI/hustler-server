"""The trial agent's own turn on top of the First cut (Q3, 30 Oct 2026):
the deterministic head start (trial_turn), the trial lane's render and
continuation discipline, and the proof that none of it reaches a
grandfathered subscriber (owner decision D1) or the new-subscriber lane."""
import json
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import config
import trial_turn


# ── which requests get a head start ───────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("make it a vertical short with captions", "generic"),
    ("make this a reel with a strong hook and captions", "generic"),
    ("find the best moment and make a short", "generic"),
    ("cut the boring parts", "cleanup"),
    ("cut out the boring bits", "cleanup"),
    ("make a highlight", "generic"),
    ("clean up my footage", "cleanup"),
    ("Clean it up please", "cleanup"),
    ("remove the pauses", "cleanup"),
    # a specific instruction or a brief: the agent works it as before
    ("make the captions red", None),
    ("remove the part at 0:12", None),
    ("add some music", None),
    ("make it 30 seconds long", None),
    ("make it landscape", None),
    ("https://klosmo-store.com/ make a premium promo of the shop", None),
    ("", None),
])
def test_intent(text, kind):
    assert trial_turn.intent(text) == kind


# ── visible events and the static stretches between them ──────────────────

FC_EDL = {   # the phone First cut (C2 c2g): band crop, 3 shallow punches,
             # one concealed cut, a hook, two slams, one card; 27.14 s
    "keep": [[4.54, 11.12], [11.52, 16.24], [17.53, 23.45], [24.19, 26.22],
             [27.08, 29.47], [30.78, 36.28]],
    "motion": [{"id": "mg1", "start": 0.0, "end": 2.9, "template": "hook_title"},
               {"id": "mg2", "start": 7.18, "end": 8.78, "template": "word_slam",
                "params": {"y": 0.693}},
               {"id": "mg3", "start": 14.22, "end": 15.59,
                "template": "word_slam", "params": {"y": 0.5}}],
    "vectors": [{"id": "vec1", "start": 14.22, "end": 15.59, "width": 1.0,
                 "height": 1.0, "opacity": 1.0, "kind": "rectangle"}],
    "effects": {"zooms": [
        {"id": "zm1", "start": 0.0, "end": 27.14, "strength": 0.43},
        {"id": "zm2", "start": 7.02, "end": 9.62, "strength": 0.08,
         "cx": 0.456, "cy": 0.433},
        {"id": "cs1", "start": 17.22, "end": 19.25, "strength": 0.12,
         "cut_step": True},
        {"id": "zm4", "start": 22.2, "end": 24.8, "strength": 0.08}]},
}


def test_visible_events_ignore_the_band_crop_and_invisible_pushes():
    ev = trial_turn.visible_events(FC_EDL, 27.14)
    assert 0.0 in ev and 27.14 in ev
    # the judges read no change in 0.08 pushes or a 0.12 cut step
    assert 7.02 not in ev and 22.2 not in ev and 17.22 not in ev
    # the judges' "16-27 s: about 11 s of one composition"
    gaps = trial_turn.static_gaps(FC_EDL, 27.14)
    assert (15.59, 27.14) in gaps and (8.78, 14.22) in gaps


# ── the head start's steps, on a fake project ─────────────────────────────

class _Proj:
    """A ctx whose writes go through a fake agent_tools.execute."""

    def __init__(self, edl, words, sentences, duration=36.3, faces=0.9):
        self.edl = json.loads(json.dumps(edl))
        self.version = 12
        self.index = {"words": words, "sentences": sentences,
                      "spatial": {"samples": [
                          {"t": 1, "faces": [[0.2, 0.2, 0.8, 0.6]]}] * 9
                          + [{"t": 2, "faces": []}] * int(9 * (1 - faces) / faces)},
                      "video": {"width": 720, "height": 1280}}
        self.duration = duration
        self.has_main_video = True
        self.write_calls, self.versions_written = [], []
        self.calls = []

    def latest_edl(self):
        return {"version": self.version, "json": self.edl}


def _words(text, t0=4.6, step=0.42):
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "t0": round(t, 2), "t1": round(t + step - 0.06, 2)})
        t += step
    return out


@pytest.fixture
def fake_tools(monkeypatch):
    def execute(ctx, name, args):
        ctx.calls.append((name, args))
        e = ctx.edl
        if name == "add_zoom":
            e.setdefault("effects", {}).setdefault("zooms", []).append(
                dict(args, id=f"z{len(ctx.calls)}"))
        elif name == "add_motion_graphic":
            e.setdefault("motion", []).append(dict(args, id=f"m{len(ctx.calls)}"))
        elif name == "remove_vector_graphic":
            e["vectors"] = [v for v in e.get("vectors") or []
                            if v.get("id") != args["id"]]
        else:
            return "REJECTED: not faked"
        ctx.version += 1
        ctx.versions_written.append(ctx.version)
        return f"EDL v{ctx.version - 1} -> v{ctx.version}: {name}"
    monkeypatch.setattr(agent_tools, "execute", execute)

    def no_perception(ctx):
        raise RuntimeError("no proxy in tests")
    monkeypatch.setattr(agent_tools, "_get_perception", no_perception)
    return execute


SPEECH = ("since we are a trillionth of the sun's energy we could harness "
          "about one millionth of it and that would still be a million "
          "times more energy than our entire civilization uses today which "
          "is pretty magnificent when you think about what that means for "
          "everyone building rockets satellites factories and computers "
          "across the whole planet tomorrow and every solar panel mirror "
          "turbine battery engineer student teacher farmer doctor and dreamer "
          "who keeps looking up at that enormous burning star")


def test_lift_fills_each_static_stretch_with_one_event(fake_tools, monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_room",
                        lambda ctx, edl, s, e: (0.3, 1.2, 0.0))
    words = _words(SPEECH)
    ctx = _Proj(FC_EDL, words, [{"t0": words[0]["t0"],
                                 "t1": words[-1]["t1"], "text": SPEECH}])
    steps = trial_turn.Steps(ctx)
    n = trial_turn.lift_static(ctx, steps)
    added = [(name, a) for name, a in ctx.calls
             if name in ("add_zoom", "add_motion_graphic")]
    assert n == len(added) >= 3
    # the devices alternate: a punch, then a word in big type (a punch that
    # would land on another move becomes a word too)
    assert [name for name, _ in added][:2] == ["add_zoom", "add_motion_graphic"]
    for name, a in added:
        assert a["start"] >= 2.9 and a["end"] <= 27.14 - 0.3
        if name == "add_zoom":
            assert a["mode"] == "punch" and a["strength"] == trial_turn.PUNCH_STRENGTH
            assert a["end"] - a["start"] >= trial_turn.PUNCH_HOLD_S[0] - 1e-6
            assert (a["cx"], a["cy"]) == (0.456, 0.433)   # the First cut's aim
    starts = sorted(a["start"] for _, a in added)
    assert all(b - a >= 2 * trial_turn.LIFT_MIN_EDGE_S - 0.2
               for a, b in zip(starts, starts[1:]))       # sparse by rule
    assert len(ctx.write_calls) == n
    assert any("punch-in" in x for x in steps.applied)
    # idempotent: the stretches are filled, a second pass adds nothing
    assert trial_turn.lift_static(ctx, trial_turn.Steps(ctx)) == 0


def test_lift_slams_a_word_where_the_frame_has_no_zoom_room(fake_tools,
                                                            monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_room",
                        lambda ctx, edl, s, e: (0.04, 1.6, 0.0))
    words = _words(SPEECH)
    ctx = _Proj(FC_EDL, words, [])
    trial_turn.lift_static(ctx, trial_turn.Steps(ctx))
    slams = [a for name, a in ctx.calls if name == "add_motion_graphic"]
    assert slams and not [1 for name, _ in ctx.calls if name == "add_zoom"]
    spoken = {w["w"] for w in words}
    for a in slams:
        assert a["template"] == "word_slam"
        assert a["params"]["text"].strip("*") in spoken    # the speaker's word
        assert a["params"]["y"] == 0.693                   # the slams' own row
        assert a["params"]["text"].strip("*").lower() not in trial_turn._STOP


def test_an_empty_full_frame_card_is_removed(fake_tools):
    edl = json.loads(json.dumps(FC_EDL))
    edl["vectors"].append({"id": "vec2", "start": 20.0, "end": 21.4,
                           "width": 1.0, "height": 1.0, "opacity": 1.0})
    ctx = _Proj(edl, _words(SPEECH), [])
    assert trial_turn.repair_empty_cards(ctx, trial_turn.Steps(ctx)) == 1
    assert [v["id"] for v in ctx.edl["vectors"]] == ["vec1"]   # vec1 has its slam


class _DB:
    def __init__(self, meta):
        self.meta = meta

    def run(self, fn, *a):
        assert fn is trial_turn.first_cut_state
        return self.meta


def test_prepare_only_on_an_untouched_first_cut(fake_tools, monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_room",
                        lambda ctx, edl, s, e: (0.3, 1.2, 0.0))
    monkeypatch.setattr(config, "CLAUDE_TRIAL_LIFT_TALKING", True)
    ctx = _Proj(FC_EDL, _words(SPEECH), [])
    fc = {"kind": "first_cut", "edl_version": 12,
          "first_cut": {"picked": {"start_s": 4.5, "end_s": 36.3,
                                   "how": "scout"}}}
    # a specific instruction: nothing
    assert trial_turn.prepare(ctx, _DB(fc), 1, "make the captions red") is None
    # the user already changed the First cut: nothing
    assert trial_turn.prepare(ctx, _DB(dict(fc, edl_version=11)), 1,
                              "make it a short") is None
    # no First cut at all: nothing
    assert trial_turn.prepare(ctx, _DB(None), 1, "make it a short") is None
    prep = trial_turn.prepare(ctx, _DB(fc), 1, "make it a vertical short "
                              "with captions")
    assert prep["intent"] == "generic" and prep["applied"]
    assert "TRIAL TURN PREPARED" in prep["note"]
    assert "render_preview once" in prep["note"]


def test_a_highlight_montage_is_kept_as_cut(fake_tools):
    ctx = _Proj(FC_EDL, _words(SPEECH), [])
    fc = {"kind": "first_cut", "edl_version": 12,
          "first_cut": {"picked": {"how": "motion", "shots": 12}}}
    prep = trial_turn.prepare(ctx, _DB(fc), 1, "make a highlight")
    assert prep["intent"] == "kept"
    assert "do not re-cut" in prep["note"]
    # only the designed end beat is added: a push across the payoff shot
    assert [n for n, _a in ctx.calls] == ["add_zoom"]
    z = ctx.calls[0][1]
    assert z["mode"] == "push_in" and z["end"] == 27.14 and z["start"] == 26.14


def test_cleanup_of_a_kept_moment_rebuilds_the_whole_take(fake_tools,
                                                          monkeypatch):
    seen = {}

    def whole(ctx, worker_db, steps):
        seen["called"] = True
        steps.applied.append("the whole take cleaned up")
        return True
    monkeypatch.setattr(trial_turn, "whole_cleanup", whole)
    ctx = _Proj(FC_EDL, _words(SPEECH), [], duration=100.0, faces=0.2)
    fc = {"kind": "first_cut", "edl_version": 12,
          "first_cut": {"picked": {"start_s": 40.0, "end_s": 66.0,
                                   "how": "scout"}}}
    prep = trial_turn.prepare(ctx, _DB(fc), 1, "clean up my footage")
    assert seen.get("called") and prep["whole"]
    assert "whole take" in prep["note"]
    # a First cut that already kept most of the take is cleaned as it is
    seen.clear()
    fc["first_cut"]["picked"] = {"start_s": 2.0, "end_s": 95.0, "how": "rules"}
    ctx2 = _Proj(FC_EDL, _words(SPEECH), [], duration=100.0, faces=0.2)
    prep = trial_turn.prepare(ctx2, _DB(fc), 1, "clean up my footage")
    assert not seen and not prep["whole"]


# ── grandfathering (D1): only the trial lane gets any of it ───────────────

def _lane_ctx(kind):
    ctx = SimpleNamespace(claude_trial=None, claude_paid=None,
                          latest_edl=lambda: {"version": 12, "json": FC_EDL},
                          editing_metrics={})
    if kind == "trial":
        ctx.claude_trial = SimpleNamespace(model="claude-haiku-5-5")
    elif kind == "new_subscriber":
        ctx.claude_paid = {"lane": {"model": "claude-haiku-5-5"}}
    return ctx


@pytest.mark.parametrize("kind", ["grandfathered", "new_subscriber"])
def test_no_head_start_outside_the_trial_lane(kind, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("trial_turn.prepare ran outside the trial lane")
    monkeypatch.setattr(trial_turn, "prepare", boom)
    ctx = _lane_ctx(kind)
    assert agent_loop._trial_head_start(ctx, None, 1, {"content":
                                        "make it a short"}) == ""
    assert not ctx.editing_metrics


def test_the_trial_lane_gets_the_head_start(monkeypatch):
    monkeypatch.setattr(trial_turn, "prepare", lambda *a, **k: {
        "intent": "generic", "whole": False, "applied": ["x"], "s": 0.1,
        "note": "\n[system: TRIAL TURN PREPARED. x]", "steps": []})
    ctx = _lane_ctx("trial")
    note = agent_loop._trial_head_start(ctx, None, 1, {"content": "a short"})
    assert "TRIAL TURN PREPARED" in note
    assert ctx.prep_start_version == 12
    assert ctx.editing_metrics["trial_head_start"]["intent"] == "generic"


@pytest.mark.parametrize("kind", ["grandfathered", "new_subscriber"])
def test_render_cap_unchanged_outside_the_trial_lane(kind):
    # main (25 Oct): a Studio request has no render cap; the trial's cap
    # never reaches another lane
    ctx = _lane_ctx(kind)
    assert agent_loop._trial_preview_cap(ctx, None) is None
    assert not agent_loop._trial_lane(ctx)
    ctx.trial_preview_cap = agent_loop._trial_preview_cap(ctx, None)
    ctx.trial_previews = 99
    assert agent_loop._trial_render_refusal(ctx, "render_preview") is None
    assert agent_loop._trial_render_refusal(ctx, "keep_segments") is None


def test_trial_requests_render_at_most_twice():
    ctx = _lane_ctx("trial")
    assert config.CLAUDE_TRIAL_MAX_PREVIEWS == 2
    assert agent_loop._trial_preview_cap(ctx, None) == 2
    # a company-funded operator repair keeps its own envelope
    assert agent_loop._trial_preview_cap(ctx, None, operator_run=True) is None
    assert agent_loop._trial_lane(ctx)
    ctx.trial_preview_cap = 2
    ctx.trial_previews = 1
    assert agent_loop._trial_render_refusal(ctx, "render_preview") is None
    ctx.trial_previews = 2
    assert "REJECTED" in agent_loop._trial_render_refusal(ctx, "render_preview")
    assert "REJECTED" in agent_loop._trial_render_refusal(ctx, "keep_segments")
    # reads stay open after the last preview
    assert agent_loop._trial_render_refusal(ctx, "get_edl") is None


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6.1-sol", "gpt-5.4-mini",
                                   "grok-4.7"])
def test_openai_and_xai_lanes_still_yield_to_the_fleet(model):
    # every grandfathered subscriber's lane is one of these
    assert agent_loop._fresh_turn_yields_to_fleet(model)


def test_claude_lanes_do_not_wait_on_the_openai_fleet():
    assert not agent_loop._fresh_turn_yields_to_fleet("claude-haiku-5-5")
    assert not agent_loop._fresh_turn_yields_to_fleet("claude-sonnet-5-5")


def test_the_trial_note_no_longer_sends_the_model_to_restore_the_take():
    import claude_trial
    note = claude_trial.FIRST_RESULT_NOTE
    assert "keep_segments([[0" not in note
    assert "TRIAL TURN PREPARED" in note and "never a question" in note


def test_a_screen_recording_gets_a_reading_zoom_on_its_text(monkeypatch):
    import first_cut
    monkeypatch.setattr(first_cut, "_screen_like", lambda index: True)
    calls = []

    def execute(ctx, name, args):
        calls.append((name, args))
        ctx.version += 1
        return f"EDL v{ctx.version - 1} -> v{ctx.version}: {name}"
    monkeypatch.setattr(agent_tools, "execute", execute)
    edl = {"keep": [[0.0, 18.0]],
           "motion": [{"id": "mg1", "start": 0.0, "end": 2.9,
                       "template": "hook_title"}]}
    ctx = _Proj(edl, [], [], duration=18.0)
    ctx.index["video"] = {"width": 1280, "height": 800}
    # a docs page: the text column x 0.27-0.89, its body moving down
    ctx.index["spatial"] = {"samples": [
        {"t": float(t), "text": [[0.27, 0.10 + 0.02 * t + 0.05 * k, 0.89,
                                  0.13 + 0.02 * t + 0.05 * k] for k in range(6)]}
        for t in range(0, 18, 2)]}
    assert trial_turn.lift_screen(ctx, trial_turn.Steps(ctx))
    (name, args), = calls
    assert name == "add_zoom_path"
    keys = args["keyframes"]
    # eases in from the untouched frame, already aimed at the first block
    assert keys[0]["strength"] == 0.0 and keys[0]["t"] < 2.9
    assert keys[0]["rect"] == keys[1]["rect"]
    framed = keys[1:]
    # the column (0.62 wide) fills the frame: ~1.4-1.5x, readable
    assert all(0.35 <= k["strength"] <= trial_turn.SCREEN_MAX_STRENGTH
               for k in framed)
    for k in framed:      # the column, at the output's own aspect
        x0, y0, x1, y1 = k["rect"]
        assert x0 <= 0.27 and x1 >= 0.89
        assert abs((x1 - x0) / (y1 - y0) - 1280 / 800) < 0.05
    assert keys[-1]["t"] == 18.0                                 # to the end
    # a program that already has its own framing is left alone
    calls.clear()
    edl2 = dict(edl, frame={"ratio": "9:16"})
    assert not trial_turn.lift_screen(_Proj(edl2, [], [], duration=18.0),
                                      trial_turn.Steps(ctx))
    assert not calls


def test_the_note_names_the_delivered_shape():
    ctx = _Proj({"keep": [[0, 10]]}, [], [])
    ctx.index["video"] = {"width": 1280, "height": 800}
    assert trial_turn.shape_words(ctx).startswith("landscape")
    assert "not vertical" in trial_turn.shape_words(ctx)
    ctx.edl["frame"] = {"ratio": "9:16"}
    assert trial_turn.shape_words(ctx) == "vertical 9:16"
    ctx2 = _Proj({"keep": [[0, 10]]}, [], [])          # a 720x1280 phone clip
    assert trial_turn.shape_words(ctx2) == "vertical 9:16"


def test_a_prepared_trial_turn_takes_no_critic_repair_pass(monkeypatch):
    ctx = _lane_ctx("trial")
    ctx.trial_prep = {"intent": "generic"}
    ctx.last_visual_critic = {"verdict": "fix"}
    assert agent_loop._prepared_trial(ctx)
    assert agent_loop._quality_repair_pushback(ctx, [], 0, set()) is False
    # its reply carries no review-note appendix (the notes stay in the meta)
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda c: {
        "quality_status": "advisory", "quality_findings": ["opens mid-word"]})
    assert agent_loop._disclose_outstanding_quality(ctx, "Done.") == "Done."


@pytest.mark.parametrize("status", ["advisory", "repair_required"])
def test_a_prepared_trial_reply_never_lists_review_notes(status, monkeypatch):
    ctx = _lane_ctx("trial")
    ctx.trial_prep = {"intent": "generic"}
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda c: {
        "quality_status": status,
        "quality_findings": ["picture QC: FACE AT THE LEFT EDGE 11.2-11.8s"]})
    assert agent_loop._disclose_outstanding_quality(ctx, "Done.") == "Done."


@pytest.mark.parametrize("kind", ["grandfathered", "new_subscriber", "trial"])
def test_unprepared_turns_keep_the_verification_line(kind, monkeypatch):
    ctx = _lane_ctx(kind)              # no head start ran
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda c: {
        "quality_status": "repair_required",
        "quality_findings": ["picture QC: FACE AT THE LEFT EDGE"]})
    out = agent_loop._disclose_outstanding_quality(ctx, "Done.")
    assert out.startswith("Done.") and "Verification remains open" in out


@pytest.mark.parametrize("kind", ["grandfathered", "new_subscriber", "trial"])
def test_unprepared_turns_keep_the_review_appendix(kind, monkeypatch):
    ctx = _lane_ctx(kind)              # no head start ran
    assert not agent_loop._prepared_trial(ctx)
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda c: {
        "quality_status": "advisory", "quality_findings": ["opens mid-word"]})
    out = agent_loop._disclose_outstanding_quality(ctx, "Done.")
    assert out.startswith("Done.") and "opens mid-word" in out


def test_the_head_start_switches_the_second_opinions_off(monkeypatch):
    monkeypatch.setattr(trial_turn, "prepare", lambda *a, **k: {
        "intent": "generic", "whole": False, "applied": [], "s": 0.0,
        "note": "\n[system: TRIAL TURN PREPARED.]", "steps": []})
    ctx = _lane_ctx("trial")
    agent_loop._trial_head_start(ctx, None, 1, {"content": "a short"})
    assert ctx.second_opinions_off is True
    assert agent_tools._review_program_story(ctx, {"version": 13}) is None


def test_a_prepared_trial_turn_cannot_undo_the_first_cuts_craft():
    ctx = _lane_ctx("trial")
    ctx.trial_prep = {"intent": "generic"}
    ctx.trial_kept_ids = {"motion": {"mg1", "mg2"}, "zoom": {"zm5"},
                          "vector": {"vec1"}}
    for name, args in (("set_motion_graphic", {"id": "mg1", "params": {"y": 0.2}}),
                       ("remove_motion_graphic", {"id": "mg2"}),
                       ("remove_zoom", {"id": "zm5"}),
                       ("remove_vector_graphic", {"id": "vec1"})):
        out = agent_loop._kept_craft_refusal(ctx, name, args)
        assert out and out.startswith("REJECTED: KEPT")
    # what the editor added itself this turn stays editable
    assert agent_loop._kept_craft_refusal(
        ctx, "remove_zoom", {"id": "zm9"}) is None
    assert agent_loop._kept_craft_refusal(
        ctx, "cut_range", {"start": 1, "end": 2}) is None


@pytest.mark.parametrize("kind", ["grandfathered", "new_subscriber", "trial"])
def test_the_craft_guard_is_off_without_a_head_start(kind):
    ctx = _lane_ctx(kind)              # no trial_turn.prepare on this turn
    ctx.trial_kept_ids = {"motion": {"mg1"}}
    assert agent_loop._kept_craft_refusal(
        ctx, "set_motion_graphic", {"id": "mg1"}) is None


def test_a_punch_never_lands_on_another_move(fake_tools, monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_room",
                        lambda ctx, edl, s, e: (0.3, 1.2, 0.0))
    words = _words(SPEECH)
    ctx = _Proj(FC_EDL, words, [])
    trial_turn.lift_static(ctx, trial_turn.Steps(ctx))
    mine = [a for n, a in ctx.calls if n == "add_zoom"]
    theirs = [z for z in FC_EDL["effects"]["zooms"] if z["id"] != "zm1"]
    for a in mine:
        for z in theirs:
            assert a["end"] + 0.1 <= z["start"] or a["start"] - 0.1 >= z["end"]


def test_a_slam_without_a_first_cut_slam_sits_on_the_caption_band(fake_tools,
                                                                    monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_room",
                        lambda ctx, edl, s, e: (0.04, 1.6, 0.0))
    edl = json.loads(json.dumps(FC_EDL))
    for m in edl["motion"]:
        m.get("params", {}).pop("y", None)        # no slam row to copy
    edl["captions"] = {"mode": "from_transcript", "style": {"anchor_y": 0.64}}
    ctx = _Proj(edl, _words(SPEECH), [])
    trial_turn.lift_static(ctx, trial_turn.Steps(ctx))
    slams = [a for n, a in ctx.calls if n == "add_motion_graphic"]
    assert slams and all(a["params"]["y"] == 0.64 for a in slams)


def test_the_guard_keeps_only_the_first_cuts_own_pieces(monkeypatch):
    ctx = _lane_ctx("trial")
    ctx.latest_edl = lambda: {"version": 12, "json": FC_EDL}

    def prep(c, *a, **k):        # the head start adds zoom z99
        return {"intent": "generic", "whole": False, "applied": ["x"],
                "s": 0.0, "note": "\n[system: TRIAL TURN PREPARED.]",
                "steps": []}
    monkeypatch.setattr(trial_turn, "prepare", prep)
    agent_loop._trial_head_start(ctx, None, 1, {"content": "a short"})
    assert "zm2" in ctx.trial_kept_ids["zoom"]
    assert agent_loop._kept_craft_refusal(ctx, "remove_zoom", {"id": "zm2"})
    assert agent_loop._kept_craft_refusal(ctx, "remove_zoom",
                                          {"id": "z99"}) is None


def test_the_talking_head_lift_is_off_by_default(fake_tools, monkeypatch):
    """Measured: it never raised a talking head over its First cut."""
    assert config.CLAUDE_TRIAL_LIFT_TALKING is False
    ctx = _Proj(FC_EDL, _words(SPEECH), [])
    fc = {"kind": "first_cut", "edl_version": 12,
          "first_cut": {"picked": {"start_s": 4.5, "end_s": 36.3,
                                   "how": "scout"}}}
    prep = trial_turn.prepare(ctx, _DB(fc), 1, "make it a vertical short "
                              "with captions")
    assert prep["intent"] == "generic" and not prep["applied"]
    assert not [n for n, _a in ctx.calls if n in ("add_zoom",
                                                  "add_motion_graphic")]
    assert "already answers this request" in prep["note"]
