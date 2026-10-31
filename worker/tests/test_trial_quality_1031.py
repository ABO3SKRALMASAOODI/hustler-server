"""TRIAL_QUALITY_BUILD (31 Oct 2026): the trial lane's Opus planner, sentence-
boundary snapping of the editor's cuts, the designed open / middle / close of
a single-shot cinematic clip, and ask_user recorded as awaiting_user."""
import copy
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import claude_trial
import config
import sentence_snap
import single_shot_design as ssd


# ── 1. the trial lane's planner ──────────────────────────────────────────

def test_planner_is_on_by_default_for_the_trial_flag():
    f = claude_trial.parse_flag({"on": True})
    assert f["planner_model"] == "claude-opus-5-5"
    assert f["planner_effort"] == "xhigh"
    assert f["executor_effort"] == "high"
    assert f["model"] == config.CLAUDE_TRIAL_MODEL == "claude-haiku-5-5"


@pytest.mark.parametrize("raw", ["", "off", None, False, "gpt-6-luna", "none"])
def test_planner_switch_off(raw):
    assert claude_trial.parse_flag({"on": True, "planner_model": raw})[
        "planner_model"] is None


def test_planner_overrides_from_the_row():
    f = claude_trial.parse_flag({"on": True, "planner_model": "claude-sonnet-5-5",
                                 "planner_effort": "max",
                                 "executor_effort": "bogus"})
    assert (f["planner_model"], f["planner_effort"], f["executor_effort"]) == \
        ("claude-sonnet-5-5", "max", "high")


def _scope(planner="claude-opus-5-5"):
    s = claude_trial.Scope(1, 0.4, 0.0, "claude-haiku-5-5")
    return s.with_planner({"planner_model": planner, "planner_effort": "xhigh",
                           "executor_effort": "high"})


def test_planning_call_runs_on_opus_xhigh_and_the_rest_on_haiku_high():
    ctx = SimpleNamespace(claude_trial=_scope())
    assert agent_loop._trial_planner_step(
        ctx, "claude-haiku-5-5", 0, "xhigh") == ("claude-opus-5-5", "xhigh")
    assert agent_loop._trial_planner_step(
        ctx, "claude-haiku-5-5", 1, "medium") == ("claude-haiku-5-5", "high")
    assert agent_loop._trial_planner_step(
        ctx, "claude-haiku-5-5", 7, "medium") == ("claude-haiku-5-5", "high")


def test_new_subscribers_and_other_lanes_are_unchanged():
    paid = SimpleNamespace(claude_trial=None, claude_paid={"lane": {}})
    assert agent_loop._trial_planner_step(
        paid, "claude-haiku-5-5", 0, "xhigh") == ("claude-haiku-5-5", "xhigh")
    assert agent_loop._trial_planner_step(
        paid, "claude-haiku-5-5", 3, "medium") == ("claude-haiku-5-5", "medium")
    legacy = SimpleNamespace(claude_trial=None)
    assert agent_loop._trial_planner_step(
        legacy, "gpt-6-luna", 0, "high") == ("gpt-6-luna", "high")
    off = SimpleNamespace(claude_trial=_scope(planner=None))
    assert agent_loop._trial_planner_step(
        off, "claude-haiku-5-5", 0, "xhigh") == ("claude-haiku-5-5", "xhigh")


def test_little_budget_left_plans_on_the_lane_model():
    s = claude_trial.Scope(1, 0.40, 0.25, "claude-haiku-5-5").with_planner(
        claude_trial.parse_flag({"on": True}))
    ctx = SimpleNamespace(claude_trial=s)
    # $0.15 left < CLAUDE_TRIAL_PLANNER_MIN_LEFT_USD: no Opus call
    assert agent_loop._trial_planner_step(
        ctx, "claude-haiku-5-5", 0, "xhigh") == ("claude-haiku-5-5", "xhigh")
    assert agent_loop._trial_planner_step(
        ctx, "claude-haiku-5-5", 1, "medium") == ("claude-haiku-5-5", "medium")
    # a turn that planned on Opus keeps its executor effort as it spends
    s2 = claude_trial.Scope(1, 0.40, 0.0, "claude-haiku-5-5").with_planner(
        claude_trial.parse_flag({"on": True}))
    ctx2 = SimpleNamespace(claude_trial=s2)
    assert agent_loop._trial_planner_step(
        ctx2, "claude-haiku-5-5", 0, "xhigh")[0] == "claude-opus-5-5"
    s2.spent_usd = 0.35
    assert agent_loop._trial_planner_step(
        ctx2, "claude-haiku-5-5", 2, "medium") == ("claude-haiku-5-5", "high")


def test_scope_carries_the_planner_from_resolve_flag():
    s = claude_trial.Scope(5, 0.4, 0.1, "claude-haiku-5-5").with_planner(
        claude_trial.parse_flag({"on": True}))
    assert s.planner_model == "claude-opus-5-5"
    assert s.as_dict()["planner_model"] == "claude-opus-5-5"


# ── 2. sentence-boundary snapping ─────────────────────────────────────────

def _w(text, t0, t1):
    return {"w": text, "t0": t0, "t1": t1}


# "So here is the plan. We go left, then right. Done."
WORDS = [_w("So", 1.0, 1.2), _w("here", 1.25, 1.5), _w("is", 1.55, 1.7),
         _w("the", 1.75, 1.9), _w("plan.", 1.95, 2.4),
         _w("We", 2.8, 3.0), _w("go", 3.05, 3.3), _w("left,", 3.35, 3.7),
         _w("then", 3.9, 4.1), _w("right.", 4.15, 4.6),
         _w("Done.", 5.2, 5.6)]


def test_a_mid_sentence_start_moves_to_the_sentence_start():
    keep, notes = sentence_snap.snap_keep([[1.6, 10.0]], WORDS, 10.0)
    # 1.6 is inside "So here is the plan."; the nearest start boundary is
    # just before "We" (2.72) or before "So" (0.92): 0.68 vs 0.68 -> tie,
    # the earlier one found first keeps the whole sentence
    assert keep[0][0] in (0.92, 2.72)
    assert notes and notes[0][2] == "start"


def test_a_mid_sentence_end_moves_to_the_sentence_end():
    keep, notes = sentence_snap.snap_keep([[0.0, 3.1]], WORDS, 10.0)
    # 3.1 is inside "We go left,"; the clause end after "left," (3.85) is
    # 0.75 away x1.6 = 1.2; the sentence end after "plan." (2.55) 0.55 away
    assert keep == [[0.0, 2.55]]
    assert notes[0][3] == "sentence"


def test_clean_edges_and_silence_stay_exactly():
    k = [[0.5, 2.6], [2.75, 4.65], [5.0, 6.0]]
    keep, notes = sentence_snap.snap_keep(k, WORDS, 10.0)
    assert notes == [] and keep == k


def test_edges_the_previous_keep_had_never_move():
    prev = [[1.6, 3.1]]
    keep, notes = sentence_snap.snap_keep([[1.6, 3.1]], WORDS, 10.0,
                                          prev_keep=prev)
    assert keep == prev and notes == []


def test_a_cut_range_snaps_only_its_new_edges():
    prev = [[0.0, 10.0]]
    # cut 3.2-4.0 (inside "go left, then"): the kept pieces end at 3.2 and
    # start at 4.0, both mid-sentence
    keep, notes = sentence_snap.snap_keep([[0.0, 3.2], [4.0, 10.0]], WORDS,
                                          10.0, prev_keep=prev)
    for s, e in keep:
        for t in (s, e):
            assert sentence_snap.is_clean(t, sentence_snap.boundaries(WORDS))
    assert keep[0][0] == 0.0 and keep[-1][1] == 10.0


def test_sentences_from_the_index_mark_boundaries_without_punctuation():
    words = [_w("hello", 0.0, 0.3), _w("there", 0.35, 0.6),
             _w("next", 0.7, 0.9), _w("one", 0.95, 1.2)]
    sents = [{"wi0": 0, "wi1": 1}, {"wi0": 2, "wi1": 3}]
    b = sentence_snap.boundaries(words, sents)
    assert any(k == "sentence" and a == 0.6 for a, _b, k in b)
    assert not any(k == "sentence" for a, _b, k in
                   sentence_snap.boundaries(words) if a == 0.6)


def test_no_edge_moves_further_than_the_cap():
    words = [_w(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(60)]
    words[-1]["w"] = "end."
    keep, notes = sentence_snap.snap_keep([[9.0, 17.95]], words, 20.0)
    # inside one 18 s sentence with no clause: the start may only reach the
    # opening (9 s away > cap) -> unchanged; the end reaches the last word
    assert keep[0][0] == 9.0
    assert all(abs(a - b) <= sentence_snap.MAX_SHIFT_S for a, b, _s, _k in notes)


def test_snap_runs_only_for_the_claude_lanes_editor_cut_tools():
    trial = SimpleNamespace(claude_trial=object(), claude_paid=None,
                            _executing_tool="cut_range", _editor_tool="cut_range")
    assert agent_tools._sentence_snap_on(trial)
    paid = SimpleNamespace(claude_trial=None, claude_paid={"lane": 1},
                           _executing_tool="keep_segments",
                           _editor_tool="keep_segments")
    assert agent_tools._sentence_snap_on(paid)
    legacy = SimpleNamespace(claude_trial=None, claude_paid=None,
                             _executing_tool="cut_range", _editor_tool="cut_range")
    assert not agent_tools._sentence_snap_on(legacy)
    engine = SimpleNamespace(claude_trial=object(), claude_paid=None,
                             _executing_tool="cut_output_range",
                             _editor_tool=None)
    assert not agent_tools._sentence_snap_on(engine)
    fillers = SimpleNamespace(claude_trial=object(), claude_paid=None,
                              _executing_tool="remove_filler_words",
                              _editor_tool="remove_filler_words")
    assert not agent_tools._sentence_snap_on(fillers)


def test_exact_times_kept_for_part_lengths():
    ctx = SimpleNamespace(claude_trial=object(), claude_paid=None,
                          _executing_tool="keep_segments",
                          _editor_tool="keep_segments",
                          user_message="for the first 8.2 seconds someone standing "
                                       "stationary, and then a first touch")
    assert not agent_tools._sentence_snap_on(ctx, snap_to_words=False)
    assert agent_tools._sentence_snap_on(ctx, snap_to_words=None)


# ── 3. designed open / middle / close for a single-shot clip ──────────────

def _edl(keep, **kw):
    e = {"keep": keep, "inserts": [], "speed": [], "motion": [], "effects": {},
         "fx": [], "overlays": [], "captions": {}, "frame": None}
    e.update(kw)
    return e


BIRTHDAY = {
    "shots": [{"id": 1, "start": 0.0, "end": 37.13}],
    "words": [{"w": "Happy", "t0": 5.36, "t1": 6.02, "confidence": 0.78},
              {"w": "Birthday!", "t0": 6.1, "t1": 6.88, "confidence": 0.95},
              {"w": "Happy", "t0": 9.39, "t1": 9.9, "confidence": 0.8},
              {"w": "Birthday!", "t0": 10.0, "t1": 10.6, "confidence": 0.9}],
    "sentences": [{"t0": 5.36, "t1": 6.88, "wi0": 0, "wi1": 1,
                   "text": "Happy Birthday!"},
                  {"t0": 9.39, "t1": 10.6, "wi0": 2, "wi1": 3,
                   "text": "Happy Birthday!"}],
}


@pytest.mark.parametrize("text,want", [
    ("I want cinmatic", "whole"),                                   # 2668
    ("make it cinematic", "whole"),
    ("Make the video clearer and give a cinematic colour grading", None),   # 2675
    ("Give colour gradings cinematic vibes", None),
    ("Give colour gradings cinematic vibes . Correct the angle (if possible) "
     "make it looj mire dramatic but not to lose the actual original outlines .",
     "whole"),                                                      # 2665
    ("cinematic grade, more dramatic framing", "framing"),
    ("Enhance this Dead by Daylight gaming video with professional cinematic "
     "colors. Make the colors vibrant ... do not change the original content.", None),
    ("just the colours, cinematic", None),
    ("add b-roll", None),
    ("", None),
])
def test_who_asks_for_it(text, want):
    assert ssd.scope(text) == want
    assert ssd.asks_for_it(text) is (want is not None)


def _check_rhythm(p):
    prog = p["prog"]
    marks = [0.0]
    if p["hook"]:
        assert p["hook"]["start"] == 0.0
        assert ssd.HOOK_MIN_S - 0.01 <= p["hook"]["end"] <= ssd.HOOK_MAX_S + 0.01
    for c in p["cutins"]:
        assert c["strength"] in (ssd.CLOSE, ssd.MEDIUM)
        assert 1.2 <= c["end"] - c["start"] <= ssd.BEAT_MAX_S + 0.01
    # the camera changes at least every ~4 s: every gap between changes
    changes = sorted({0.0} | {c["start"] for c in p["cutins"]}
                     | {c["end"] for c in p["cutins"]}
                     | {p["ending"]["start"], prog})
    gaps = [b - a for a, b in zip(changes, changes[1:])]
    assert max(gaps) <= ssd.BEAT_MAX_S + 0.01, gaps
    e = p["ending"]
    assert e["end"] == prog and ssd.END_MIN_S - 0.01 <= prog - e["start"] <= ssd.END_MAX_S + 0.01
    # the same zoom strength never twice in a row
    st = [c["strength"] for c in p["cutins"]]
    assert all(a != b for a, b in zip(st, st[1:]))


def test_a_37s_single_shot_gets_title_cutins_and_a_live_ending():
    p = ssd.plan(_edl([[0.0, 37.13]]), BIRTHDAY, "make it cinematic")
    assert p and p["hook"]["text"] == "Happy Birthday!"
    assert p["hook"]["kind"] == "spoken_line"
    assert len(p["cutins"]) >= 4
    _check_rhythm(p)


def test_the_first_line_is_the_title_when_it_opens_the_clip():
    ix = {"shots": [{"start": 0, "end": 20}],
          "words": [{"w": "This", "t0": 0.1, "t1": 0.3, "confidence": 0.9},
                    {"w": "changed", "t0": 0.35, "t1": 0.8, "confidence": 0.9},
                    {"w": "everything.", "t0": 0.85, "t1": 1.5, "confidence": 0.9}],
          "sentences": [{"t0": 0.1, "t1": 1.5, "wi0": 0, "wi1": 2,
                         "text": "This changed everything."}]}
    p = ssd.plan(_edl([[0.0, 20.0]]), ix, "make it cinematic")
    assert p["hook"] == {"text": "This changed everything.", "kind": "first_line",
                         "start": 0.0, "end": ssd.HOOK_MIN_S}
    _check_rhythm(p)


def test_a_wordless_shot_is_never_titled_from_a_label_of_the_picture():
    ix = {"shots": [{"start": 0, "end": 10.54}], "words": [], "sentences": [],
          "visual_storyboard": {"chapters": [{"description":
              "A wooden boardwalk with railings descends a hillside"}]}}
    p = ssd.plan(_edl([[0.0, 10.54]]), ix, "make it cinematic")
    assert p["hook"] is None and p["cutins"] and p["ending"]
    _check_rhythm(p)


def test_a_colour_and_framing_ask_gets_no_title():
    p = ssd.plan(_edl([[0.0, 37.13]]), BIRTHDAY, "cinematic grade, more dramatic framing")
    assert p["hook"] is None and p["cutins"] and p["ending"]
    assert ssd.plan(_edl([[0.0, 37.13]]), BIRTHDAY, "cinematic colour grading") is None


def test_low_confidence_or_rtl_speech_is_never_a_title():
    ix = {"shots": [{"start": 0, "end": 61}],
          "words": [{"w": "¿Tú", "t0": 51.9, "t1": 52.1, "confidence": 0.15},
                    {"w": "otra?.", "t0": 52.2, "t1": 52.9, "confidence": 0.2}],
          "sentences": [{"t0": 51.9, "t1": 52.9, "wi0": 0, "wi1": 1,
                         "text": "¿Tú eres la otra?."}]}
    p = ssd.plan(_edl([[0.0, 60.7]]), ix, "I want cinmatic")
    assert p["hook"] is None
    assert p["cutins"] and p["cutins"][0]["start"] >= ssd.BEAT_MIN_S
    _check_rhythm(p)
    rtl = {"shots": [], "words": [{"w": "برای", "t0": 0.1, "t1": 0.5,
                                   "confidence": 0.9},
                                  {"w": "زندگی", "t0": 0.6, "t1": 0.9,
                                   "confidence": 0.9}],
           "sentences": [{"t0": 0.1, "t1": 0.9, "wi0": 0, "wi1": 1,
                          "text": "برای زندگی"}]}
    assert ssd.plan(_edl([[0.0, 18.0]]), rtl, "make it cinematic")["hook"] is None


def test_not_for_multi_shot_long_or_already_designed_edits():
    many = {"shots": [{"start": i * 3.0, "end": i * 3.0 + 3.0} for i in range(12)]}
    assert ssd.plan(_edl([[0.0, 36.0]]), many, "make it cinematic") is None
    long_ix = {"shots": [{"start": 0, "end": 230}]}
    assert ssd.plan(_edl([[0.0, 230.0]]), long_ix, "make it cinematic") is None
    short = {"shots": [{"start": 0, "end": 6}]}
    assert ssd.plan(_edl([[0.0, 6.0]]), short, "make it cinematic") is None
    broll = _edl([[0.0, 20.0]], overlays=[{"id": "o1", "fit": "cover"}])
    assert ssd.plan(broll, {"shots": []}, "make it cinematic") is None
    titled = _edl([[0.0, 20.0]], motion=[{"id": "m1", "template": "hook_title"}],
                  effects={"zooms": [{"id": "z1"}]})
    assert ssd.plan(titled, {"shots": []}, "make it cinematic") is None


def test_no_text_and_no_zoom_asks_are_respected():
    p = ssd.plan(_edl([[0.0, 37.13]]), BIRTHDAY, "cinematic, no text please")
    assert p["hook"] is None and p["cutins"]
    p = ssd.plan(_edl([[0.0, 37.13]]), BIRTHDAY, "cinematic but no zooms")
    assert p["hook"] and not p["cutins"] and p["ending"] is None


def test_apply_writes_through_the_editor_tools_once():
    calls = []

    class Ctx:
        index = BIRTHDAY

        def latest_edl(self):
            return {"version": 3, "json": _edl([[0.0, 37.13]])}

    def run(name, args):
        calls.append((name, copy.deepcopy(args)))
        return "EDL v4 saved"

    note = ssd.apply(Ctx(), "make it cinematic", run)
    names = [n for n, _a in calls]
    assert names[0] == "add_motion_graphic"
    assert calls[0][1]["params"]["entrance"] == "cut"
    assert calls[0][1]["start"] == 0.0
    assert names.count("add_zoom") >= 5
    assert calls[-1][1]["mode"] == "push_in"
    assert all(a["mode"] == "punch" for n, a in calls[1:-1])
    assert "DESIGNED STRUCTURE" in note


def test_design_runs_once_per_request_on_claude_lanes_only(monkeypatch):
    seen = []
    monkeypatch.setattr(ssd, "apply", lambda ctx, text, run: seen.append(text) or "x")
    legacy = SimpleNamespace(claude_trial=None, claude_paid=None)
    assert agent_loop._single_shot_design(legacy, "make it cinematic") == ""
    trial = SimpleNamespace(claude_trial=object(), claude_paid=None)
    assert agent_loop._single_shot_design(trial, "make it cinematic") == "x"
    assert agent_loop._single_shot_design(trial, "make it cinematic") == ""
    other = SimpleNamespace(claude_trial=object(), claude_paid=None)
    assert agent_loop._single_shot_design(other, "add captions") == ""
    assert seen == ["make it cinematic"]


# ── 4. a question to the user is awaiting_user ────────────────────────────

def test_ask_user_records_awaiting_user_not_blocked():
    import inspect
    src = inspect.getsource(agent_loop)
    i = src.index('{"ask_user": True,')
    window = src[i:i + 600]
    assert '"outcome": AWAITING_USER' in window
    assert '"outcome": "blocked"' not in window
    assert agent_loop.AWAITING_USER == "awaiting_user"


def test_the_title_sits_in_the_top_band():
    calls = []

    class Ctx:
        index = BIRTHDAY

        def latest_edl(self):
            return {"version": 3, "json": _edl([[0.0, 37.13]])}

    ssd.apply(Ctx(), "make it cinematic",
              lambda n, a: calls.append((n, a)) or "EDL v4 saved")
    assert calls[0][1]["params"]["y"] == ssd.TITLE_Y


def test_the_reply_says_what_the_design_added_once():
    applied = {"title": "Wooden boardwalk", "cutins": 2, "ending": True}
    line = ssd.reply_line(applied, "I gave it a cinematic grade.")
    assert line == ('To make it feel cinematic I also opened it on the title '
                    '"Wooden boardwalk", cut in closer on its beats and end on '
                    'a slow push-in.')
    assert ssd.reply_line(applied, 'Added the title "Wooden boardwalk", '
                          "cut-ins and a slow push-in.") == ""
    assert ssd.reply_line(None, "x") == ""
    out = agent_loop._insert_after_lead(
        "The cut is silent with a cinematic grade. It's playing in the player."
        "\n\nFor the music, pick one.", line)
    assert out.startswith("The cut is silent with a cinematic grade. To make it")
    assert out.endswith("For the music, pick one.")



# ── 5. a look-only request keeps every second ─────────────────────────────

@pytest.mark.parametrize("text,refused", [
    ("Make the video clearer and give a cinematic colour grading", True),     # 2675
    ("Cut out the audio and play something else relating to nature. Give colour "
     "gradings cinematic vibes. Correct the angle (if possible)", True),        # 2665
    ("I want cinmatic", False),                                                # no named ask
    ("make it clearer and cut the boring parts", False),
    ("cinematic grade and trim the ending", False),
    ("give it a cinematic grade and make it 30 seconds", False),
    ("add b-roll and a grade", False),                                         # b-roll is not look-only
])
def test_look_only_requests_refuse_cut_tools(text, refused):
    ctx = SimpleNamespace(claude_trial=object(), claude_paid=None)
    got = agent_loop._look_only_cut_refusal(ctx, "cut_output_range", text)
    assert (got is not None) is refused
    if refused:
        assert got.startswith("REJECTED (not run): NOT ASKED")


def test_look_only_refusal_is_for_cut_tools_on_claude_lanes_only():
    t = "Make the video clearer and give a cinematic colour grading"
    trial = SimpleNamespace(claude_trial=object(), claude_paid=None)
    assert agent_loop._look_only_cut_refusal(trial, "set_color_grade", t) is None
    assert agent_loop._look_only_cut_refusal(trial, "keep_segments", t)
    assert agent_loop._look_only_cut_refusal(trial, "cut_silences", t)
    legacy = SimpleNamespace(claude_trial=None, claude_paid=None)
    assert agent_loop._look_only_cut_refusal(legacy, "cut_output_range", t) is None
