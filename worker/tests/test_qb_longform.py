"""QB (30 Oct 2026): "cut the boring parts" of a LONG talk keeps the talk.

The First cut answers a 10-minute 16:9 talk with one 36 s vertical moment;
the judge's hard fault (5.0, twice) was "not what the user asked: drops 94%
of the source and changes the shape". QI's whole-take attempt through the
short-form chain made an 8.5-minute vertical program that ended mid-sentence
with the interviewer in bands (4.25) and was reverted. The long-form recipe:
the talk's own shape, a scout's picks (about half), whole sentences at both
ends, the craft on top, sparse closer cameras.
"""
import json
from types import SimpleNamespace

import pytest

import longform
import trial_turn


def _rows(n=40, step=15.0):
    """n sentences of `step` seconds; every fourth is a fragment."""
    out = []
    for k in range(n):
        text = ("and then we" if k % 4 == 3 else
                f"This is sentence {k} about the talk and it ends here.")
        out.append({"id": f"s{k}", "t0": k * step, "t1": (k + 1) * step - 0.5,
                    "text": text})
    return out


def test_the_scouts_ids_in_any_spelling_become_ordered_merged_runs():
    rows = _rows()
    text = ('here you go {"keep": [["s20","s22"], ["12","14"], ["s13","s15"], '
            '["s99","s100"]], "hook": "Deep *learning* works"}')
    runs, hook = longform.parse_runs(text, rows)
    # ordered, ids unknown to the transcript dropped, touching runs joined
    assert runs == [(12, 15), (20, 22)]
    assert hook == "Deep *learning* works"
    assert longform.parse_runs("no json", rows) == ([], None)
    assert longform.parse_runs('{"keep": "all"}', rows)[0] == []


def test_a_run_opens_where_the_sentence_begins_and_the_last_one_ends_a_thought():
    rows = _rows()
    # s2 starts upper case; s3 is a fragment "and then we" (lower case, the
    # row before it ends with a full stop): a run may open on s3's neighbour
    segs = longform.shape_runs(rows, [(4, 6), (8, 11)], 600.0)
    assert segs[0][0] == 60.0                       # s4's own start
    # the last run asked to end on s11, a fragment ("and then we"): it runs
    # on to the end of that thought (s12), never stops on the fragment
    assert segs[-1][1] == round(float(rows[12]["t1"]), 2)
    # with nothing within 20 s that ends, it walks back to one that does
    flat = [dict(r, text="and then we") for r in rows]
    flat[10]["text"] = "This one ends here with a full stop."
    back = longform.shape_runs(flat, [(8, 11)], 600.0)
    assert back[-1][1] == round(float(flat[10]["t1"]), 2)


def test_short_runs_go_and_runs_a_breath_apart_join():
    rows = _rows(step=4.0)           # 3.5 s sentences
    segs = longform.shape_runs(rows, [(0, 0), (4, 12), (13, 20)], 160.0)
    # (0,0) is 3.5 s: dropped; the other two are 1.5 s apart? s12 ends at
    # 51.5, s13 starts at 52.0: joined
    assert len(segs) == 1
    assert segs[0][0] == 16.0


def test_the_share_is_bounded_and_the_fallback_is_the_whole_take(monkeypatch):
    rows = _rows()
    ctx = SimpleNamespace(duration=600.0, index={"sentences": rows},
                          first_cut_model=None)
    segs, hook, how = longform.scout(ctx, rows, {"usd": 0.0})
    assert how == "whole" and hook is None
    assert len(segs) == 1 and segs[0][0] == 0.0
    assert segs[0][1] - segs[0][0] > 0.9 * 600.0 - 40
    assert longform.share_for(240) > longform.share_for(600) > longform.share_for(1500)


def test_only_a_long_source_with_speech_is_eligible():
    speech = {"words": [{"w": "hi", "t0": 1.0, "t1": 1.2}] * 400,
              "sentences": _rows()}
    ok = SimpleNamespace(duration=600.0, index=speech)
    assert longform.eligible(ok)
    assert not longform.eligible(SimpleNamespace(duration=90.0, index=speech))
    assert not longform.eligible(SimpleNamespace(duration=600.0, index={}))


class _FakeSteps:
    def __init__(self):
        self.calls, self.rows, self.applied = [], [], []

    def run(self, name, args):
        self.calls.append((name, args))
        return f"EDL v1 -> v2: {name}"


def test_the_chain_keeps_the_shape_and_never_reframes(monkeypatch):
    import first_cut
    import first_cut_visual as fcv
    rows = _rows()
    ctx = SimpleNamespace(
        duration=600.0, index={"sentences": rows, "words": [],
                               "spatial": {"samples": [
                                   {"t": 1, "faces": [[0.3, 0.2, 0.7, 0.6]]}] * 9},
                               "video": {"width": 1920, "height": 1080}},
        first_cut_model="claude-haiku-5-5", claude_trial=None,
        latest_edl=lambda: {"version": 2, "json": {"keep": [[0.0, 20.0],
                                                           [60.0, 90.0]]}})
    monkeypatch.setattr(longform, "scout", lambda c, r, s: (
        [[0.0, 120.0], [300.0, 420.0]], "Deep *learning*", "scout"))
    monkeypatch.setattr(first_cut, "shape_ends", lambda c, s, t: None)
    monkeypatch.setattr(first_cut, "spoken_opening",
                        lambda c: ("Deep learning was working", 2.4))
    cuts = {}

    def cut_ins(c, steps, started, run, busy, hook_end, **kw):
        cuts.update(busy=busy, hook_end=hook_end, **kw)
        return 3
    import talk_craft
    monkeypatch.setattr(talk_craft, "landscape_cut_ins", cut_ins)
    monkeypatch.setattr(talk_craft, "cut_audio_pauses", lambda *a, **k: None)
    steps = _FakeSteps()
    out = longform.tighten(ctx, None, steps)
    names = [n for n, _a in steps.calls]
    assert names[:2] == ["reset_edit", "keep_segments"]
    assert steps.calls[1][1] == {"segments": [[0.0, 120.0], [300.0, 420.0]],
                                 "snap_to_words": True}
    assert "auto_reframe" not in names                 # the talk's own shape
    assert {"cut_silences", "remove_filler_words", "set_master_loudness",
            "conceal_jump_cuts", "add_captions"} <= set(names)
    assert steps.facts["landscape"] and steps.facts["was"] == "10-minute"
    assert ctx.approval_previews is True
    title = dict(steps.calls)["add_motion_graphic"]
    assert title["template"] == "hook_title" and title["params"]["size"] <= 1.2
    # Q5: the long program changes camera about every 3 s (judges: "a
    # visual event every 2-3 s across the long stretches"), each close
    # framing aimed at the face measured on its own frame (talk_craft)
    assert talk_craft.LONG_CUT_TARGET_S == 3.0 and cuts["hook_end"] >= 1.0
    assert out["how"] == "scout" and out["segments"] == 2
    assert any("16:9" in a and "10-minute" in a for a in steps.applied)


def test_a_chain_that_cannot_reset_leaves_the_first_cut(monkeypatch):
    rows = _rows()
    ctx = SimpleNamespace(duration=600.0, index={"sentences": rows},
                          first_cut_model=None, claude_trial=None)
    monkeypatch.setattr(longform, "scout", lambda c, r, s: ([[0.0, 100.0]], None, "whole"))

    class Refusing(_FakeSteps):
        def run(self, name, args):
            self.calls.append((name, args))
            return None
    assert longform.tighten(ctx, None, Refusing()) is None


# ---- the trial turn routes the request here ---------------------------------

def test_cut_the_boring_parts_routes_a_long_talk_to_the_long_form(monkeypatch):
    import first_cut
    seen = {}

    def tighten(ctx, worker_db, steps):
        seen["called"] = True
        steps.applied.append("the 10-minute talk tightened to 5 min in its "
                             "own 16:9 shape")
        return {"how": "scout", "segments": 4, "program_s": 300.0, "usd": 0.001}
    monkeypatch.setattr(longform, "eligible", lambda ctx: True)
    monkeypatch.setattr(longform, "tighten", tighten)
    monkeypatch.setattr(trial_turn, "repair_empty_cards", lambda c, s: None)
    monkeypatch.setattr(trial_turn, "_talking", lambda c: False)
    monkeypatch.setattr(trial_turn, "lift_screen", lambda c, s: None)
    ctx = SimpleNamespace(
        has_main_video=True, duration=600.0, index={"video": {"width": 1920,
                                                              "height": 1080}},
        latest_edl=lambda: {"version": 12, "json": {"keep": [[1.0, 30.0]]}})
    fc = {"kind": "first_cut", "edl_version": 12,
          "first_cut": {"picked": {"start_s": 340.0, "end_s": 381.0,
                                   "how": "scout"}}}

    class DB:
        def run(self, fn, *a):
            return fc
    prep = trial_turn.prepare(ctx, DB(), 1, "cut the boring parts")
    assert seen.get("called") and prep["intent"] == "tighten"
    assert "DONE in code" in prep["note"] and "16:9" in prep["note"]
    assert "do not re-cut" in prep["note"]
    assert "never call it vertical, 9:16 or short" in prep["note"]
    # a short source keeps the First cut's own answer
    seen.clear()
    monkeypatch.setattr(longform, "eligible", lambda ctx: False)
    prep = trial_turn.prepare(ctx, DB(), 1, "cut the boring parts")
    assert not seen and prep["intent"] == "generic"
    # the same words on another request kind are untouched
    monkeypatch.setattr(longform, "eligible", lambda ctx: True)
    prep = trial_turn.prepare(ctx, DB(), 1, "make it a vertical short")
    assert not seen


def test_the_intent_of_boring_parts_is_still_generic():
    assert trial_turn.intent("cut the boring parts") == "generic"


def test_everything_the_long_form_chain_built_is_final_on_that_turn(monkeypatch):
    """A picture note must not send the model to move a title or a cut-in of
    the head start's own build (talk10: a 21-minute turn)."""
    import agent_loop
    edl = {"motion": [{"id": "mg1"}, {"id": "mg7"}],
           "effects": {"zooms": [{"id": "z1"}, {"id": "z2"}]},
           "vectors": [{"id": "v1"}]}
    monkeypatch.setattr(trial_turn, "prepare", lambda *a, **k: {
        "intent": "tighten", "whole": False, "applied": ["x"], "s": 1.0,
        "note": "\n[system: TRIAL TURN PREPARED. x]", "steps": []})
    ctx = SimpleNamespace(claude_trial=SimpleNamespace(model="claude-haiku-5-5"),
                          claude_paid=None, editing_metrics={},
                          latest_edl=lambda: {"version": 12, "json": edl})
    agent_loop._trial_head_start(ctx, None, 1, {"content": "cut the boring parts"})
    assert ctx.trial_kept_ids["motion"] == {"mg1", "mg7"}
    assert ctx.trial_kept_ids["zoom"] == {"z1", "z2"}
    assert ctx.trial_kept_ids["vector"] == {"v1"}


def test_a_run_never_opens_on_a_filler_row_nor_ends_on_the_files_own_cut_off():
    rows = _rows(n=30, step=15.0)
    rows[4]["text"] = "You know, so."                  # a filler sentence
    rows[5]["text"] = "Deep learning was working at Stanford then."
    segs = longform.shape_runs(rows, [(4, 8)], 450.0)
    assert segs[0][0] == float(rows[5]["t0"])           # opens on the claim
    # the talk's last sentence is the source's own cut-off at 10:00 (the file
    # stops mid-sentence; the transcript still ends it with a full stop)
    tail = _rows(n=40, step=15.0)
    tail[-1]["text"] = "And for society to, to benefit."
    segs = longform.shape_runs(tail, [(30, 39)], 600.0)
    assert segs[-1][1] <= float(tail[38]["t1"]) + 1e-6   # one sentence earlier
    assert segs[-1][1] < 600.0 - longform.END_GUARD_S


def test_you_know_is_never_the_title(monkeypatch):
    import first_cut
    words = [("you", 0.1), ("know", 0.4), ("the", 0.7), ("scaling", 1.0),
             ("laws", 1.4), ("held", 1.8), ("up", 2.2), ("well", 2.6)]
    index = {"words": [{"w": w, "t0": t, "t1": t + 0.3} for w, t in words]}
    ctx = SimpleNamespace(index=index, first_cut_q1=True,
                          latest_edl=lambda: {"json": {"keep": [[0.0, 6.0]]}})
    text, _end = first_cut.spoken_opening(ctx)
    assert not text.lower().startswith("you know") and "scaling" in text.lower()
    # a grandfathered First cut keeps its own opening exactly
    ctx.first_cut_q1 = False
    text, _end = first_cut.spoken_opening(ctx)
    assert text.lower().startswith("you know")


def test_the_brief_follow_up_turn_is_off_unless_asked_and_a_brief_gets_a_tenth_more_time():
    import config
    import first_cut
    assert config.FIRST_CUT_BRIEF_FOLLOW_UP is False
    hints = {"seconds": 40}
    assert first_cut.tour_steps(hints, brief=True)[-1]["seconds"] == 44.0
    assert first_cut.tour_steps(hints)[-1]["seconds"] == 40.0          # a tile
    # Q7: a short brief's range is an upper bound (a tenth less), and a
    # 60 s brief may tour to 66 s (its pages' loading screens are cut)
    assert first_cut.tour_steps({"seconds": 20}, brief=True)[-1]["seconds"] == 18.0
    assert first_cut.tour_steps({"seconds": 60}, brief=True)[-1]["seconds"] == 66.0


def test_the_program_ends_on_the_sentence_the_model_picks_among_the_last_few(
        monkeypatch):
    import claude_lane
    rows = _rows(n=40, step=15.0)
    rows[36]["text"] = "We were wrong about almost all of it, and that is the lesson."
    rows[39]["text"] = "Do the first thing, do the second thing, question mark."
    segs = [[0.0, 120.0], [500.0, float(rows[39]["t1"])]]
    ctx = SimpleNamespace(first_cut_model="claude-haiku-5-5")
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    seen = {}

    def create(model, messages, **k):
        seen["prompt"] = messages[1]["content"]
        msg = SimpleNamespace(content='{"end_id": "s36"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(provider_cost_usd=0.0005))
    monkeypatch.setattr(claude_lane, "create", create)
    import llm
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    spend = {"usd": 0.0}
    got = longform.pick_ending(ctx, rows, segs, spend)
    assert got[-1][1] == round(float(rows[36]["t1"]), 2) and got[0] == segs[0]
    # the candidates are real sentence ends of the last stretch, with context
    assert "s36" in seen["prompt"] and "s39" in seen["prompt"]
    assert "s38" not in seen["prompt"] or rows[38]["text"].endswith(".")
    assert spend["usd"] == 0.0005
    # an answer outside the candidates, or no lane: the end stands
    monkeypatch.setattr(claude_lane, "create", lambda *a, **k: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"end_id": "s2"}'))],
        usage=SimpleNamespace(provider_cost_usd=0.0)))
    assert longform.pick_ending(ctx, rows, segs, {"usd": 0.0}) == segs
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    assert longform.pick_ending(ctx, rows, segs, {"usd": 0.0}) == segs


def test_a_talking_head_first_cut_closes_on_the_picked_sentence(monkeypatch):
    """The scout's own last line stops on a setup ("didn't accept this at
    all"); the closing pick moves the end to the sentence that lands."""
    import claude_lane
    import first_cut
    import llm
    rows = [{"id": f"s{k}", "t0": k * 5.0, "t1": k * 5.0 + 4.6,
             "text": ("Scale proved us wrong, and that is the whole lesson here."
                      if k == 9 else
                      "The field did not accept this at all. " if k == 11 else
                      f"Sentence number {k} says something concrete today.")}
            for k in range(14)]
    ctx = SimpleNamespace(duration=70.0, first_cut_model="claude-haiku-5-5",
                          first_cut_q1=True, first_cut_hook=None,
                          index={"sentences": rows, "words": []})
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    calls = []

    def create(model, messages, **k):
        calls.append(messages[0]["content"][:30])
        if "pick the last line" in messages[0]["content"]:
            text = '{"end_id": "s9"}'
        else:
            text = ('{"start_id": "s2", "end_id": "s11", "title": "x", '
                    '"hook": "Scale *proved* it", "beats": []}')
        msg = SimpleNamespace(content=text)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(provider_cost_usd=0.0004))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    start, end, _t, how = first_cut.scout_range(ctx, None, {"usd": 0.0})
    assert how == "scout" and start == 10.0
    assert end == round(float(rows[9]["t1"]), 2) or end == float(rows[9]["t1"])
    assert len(calls) == 2
    # a grandfathered First cut (no engine) never makes the second call
    calls.clear()
    ctx.first_cut_q1 = False
    first_cut.scout_range(ctx, None, {"usd": 0.0})
    assert len(calls) == 1


def test_a_closing_line_is_followed_by_a_breath():
    """A full sentence that runs straight into the next one leaves the mouth
    open on the last frame (the judge: "ends mid-thought, tail silence 0.0 s")."""
    rows = _rows(n=20, step=15.0)
    for k, r in enumerate(rows):
        r["t1"] = r["t0"] + 14.5                    # a 0.5 s breath everywhere
    rows[10]["t1"] = rows[11]["t0"]                 # s10 runs into s11
    assert longform.has_breath(rows, 9) and not longform.has_breath(rows, 10)
    assert longform.has_breath(rows, len(rows) - 1)
    cands = longform.end_candidates(rows, 12, 0.0)
    assert 10 not in cands and 12 in cands
    # the last stretch does not end on the sentence with no breath after it
    segs = longform.shape_runs(rows, [(6, 10)], 600.0)
    assert segs[-1][1] <= float(rows[9]["t1"]) + 1e-6


def test_a_short_sources_trim_opens_on_the_speakers_sentence_start(monkeypatch):
    """The phone source (75 s) goes through the trim, not the scout: its span
    began mid-sentence, so its hook title was dropped as a fragment."""
    import claude_lane
    import first_cut
    import llm
    rows = [{"id": "s0", "t0": 0.0, "t1": 8.0, "text": "What is an economy of scale in the end."},
            {"id": "s1", "t0": 8.5, "t1": 11.0, "text": "We could harness only"},
            {"id": "s2", "t0": 11.0, "t1": 16.0, "text": "one millionth of the sun's energy today."},
            {"id": "s3", "t0": 17.0, "t1": 40.0, "text": "That would still be a million times more than we use."}]
    ctx = SimpleNamespace(duration=40.0, first_cut_model="claude-haiku-5-5",
                          first_cut_q1=True, index={"sentences": rows, "words": []})
    monkeypatch.setattr(claude_lane, "available", lambda: True)

    def create(model, messages, **k):
        msg = SimpleNamespace(content='{"start_id": "s2", "end_id": "s3", "hook": "One *millionth* of it"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(provider_cost_usd=0.0))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    start, _end, how = first_cut.trim_range(ctx, None, {"usd": 0.0})
    assert how == "scout" and start == 8.5            # "We could harness only" opens it
    ctx.first_cut_q1 = False
    start, _end, _how = first_cut.trim_range(ctx, None, {"usd": 0.0})
    assert start == 11.0                              # a grandfathered cut: as before


def test_only_a_long_form_turn_renders_its_previews_at_approval_quality():
    """The cut is the deliverable there; every other trial turn keeps its draft
    (approval previews on all of them measured no gain and two typed requests
    lower, QB 30 Oct)."""
    import agent_tools
    on = SimpleNamespace(approval_previews=True)
    assert agent_tools._deliverable_quality(on, "draft") == "approval"
    assert agent_tools._deliverable_quality(on, "approval") == "approval"
    for ctx in (SimpleNamespace(approval_previews=False), SimpleNamespace(),
                SimpleNamespace(claude_trial=object())):
        assert agent_tools._deliverable_quality(ctx, "draft") == "draft"
