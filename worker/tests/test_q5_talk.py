"""Q5 (30 Oct 2026): talking heads and long talks at the references' level.

The calibrated judge on the trial branch's talking heads: the opening is a
dependent clause ("Since we're a trillionth of the sun's energy..."), the
ending a list item ("...certainly one of them"), pauses the viewer hears
remain (whisper's words abut across them), a slam sat on the mouth, and the
10-minute "cut the boring parts" kept five minutes of one framing. Every
change here is engine-only (``first_cut_q1`` / the trial lane): a
grandfathered subscriber's First cut is unchanged (owner rule D1).
"""
from types import SimpleNamespace

import numpy as np
import pytest

import first_cut
import first_cut_visual as fcv
import longform
import talk_craft

# The phone fixture's own transcript rows (whisper splits long sentences).
PHONE = [
    ("s1", 0.0, 2.86, "economy by a factor of a million is still roughly a trillion."),
    ("s2", 3.98, 6.82, "So since we're a trillionth of the sun's energy, if you increase"),
    ("s3", 6.82, 10.86, "this economy in terms of electricity usage by roughly a million,"),
    ("s4", 11.6, 16.12, "you will be roughly one millionth only of the sun's energy harness."),
    ("s5", 17.61, 21.37, "But what is an economy or an intelligence using a million times"),
    ("s6", 21.37, 23.33, "more electricity than all of our civilization?"),
    ("s7", 24.27, 25.95, "Think about or look like or do."),
    ("s8", 27.16, 29.3, "It's going to be something pretty magnificent."),
    ("s9", 30.8, 35.78, "The challenge will be even vaguely appreciating in that level of intelligence."),
    ("s10", 37.94, 41.22, "But it's safe to say it will solve everything you can possibly"),
    ("s11", 41.22, 42.28, "think of."),
    ("s12", 42.28, 42.56, "Yes."),
    ("s13", 42.88, 46.1, "Long trip would be, you know, certainly one of them."),
    ("s14", 48.18, 51.42, "And I do enjoy your unrelenting optimism."),
]
SILENCES = [[16.09, 17.72], [35.64, 37.98], [41.62, 42.38], [46.47, 47.54]]


def _rows():
    return [{"id": i, "t0": a, "t1": b, "text": t} for i, a, b, t in PHONE]


def _by(rows):
    return {r["id"]: r for r in rows}


def _words(rows):
    out = []
    for r in rows:
        toks = r["text"].split()
        step = (r["t1"] - r["t0"]) / len(toks)
        for j, w in enumerate(toks):
            out.append({"w": w, "t0": r["t0"] + j * step, "t1": r["t0"] + (j + 1) * step})
    return out


# ── the opening ──────────────────────────────────────────────────────────

def test_a_dependent_clause_or_fragment_never_opens():
    assert not talk_craft.self_contained(PHONE[1][3])          # "So since..."
    assert not talk_craft.self_contained("Because it works, we did it.")
    assert not talk_craft.self_contained("you will be roughly one millionth.")
    assert not talk_craft.self_contained("You know, if they")
    assert talk_craft.self_contained(PHONE[4][3])              # "But what is..."
    assert talk_craft.self_contained("Deep learning was working.")


def test_the_span_opens_on_the_first_sentence_that_hooks_on_its_own():
    rows = _rows()
    by = _by(rows)
    # the scout picked s2 ("So since we're a trillionth..."): the opening
    # moves to s5's question; a row inside a running sentence is skipped
    assert talk_craft.opening_row(rows, by["s2"], by["s13"])["id"] == "s5"
    # nothing qualifies before too little is left: the pick stands
    assert talk_craft.opening_row(rows, by["s2"], by["s6"])["id"] == "s2"


# ── the ending ───────────────────────────────────────────────────────────

def test_a_list_item_an_aside_or_small_talk_never_closes():
    assert talk_craft.trailing(PHONE[12][3])                   # one of them
    assert talk_craft.trailing("Yes.")
    assert talk_craft.trailing("Isn't that wild?")
    assert talk_craft.trailing("We tried this and that and so on.")
    assert not talk_craft.trailing(
        "But it's safe to say it will solve everything you can possibly think of.")


def test_the_ending_is_judged_on_the_whole_sentence_and_needs_a_breath():
    rows = _rows()
    by = _by(rows)
    index = {"silences": SILENCES}
    # "think of." alone has two words; its sentence lands, and the measured
    # silence after it (41.62-42.38) is the breath whisper's abutting rows hide
    assert talk_craft.sentence_text(rows, 10).startswith("But it's safe")
    assert talk_craft.breath_after(index, rows, 10)
    end = talk_craft.closing_row(rows, by["s5"], by["s13"],
                                 breath=lambda m: talk_craft.breath_after(index, rows, m))
    assert end["id"] == "s11"
    # a pick ending inside the running sentence ("...you can possibly")
    # runs on to its end
    assert talk_craft.closing_row(rows, by["s5"], by["s10"],
                                  breath=lambda m: talk_craft.breath_after(index, rows, m))["id"] == "s11"
    # with no breath anywhere, nothing qualifies: the pick stands
    assert talk_craft.closing_row(rows, by["s5"], by["s13"],
                                  breath=lambda m: False)["id"] == "s13"


def test_a_talking_head_trim_opens_on_the_hook_and_ends_on_the_payoff(monkeypatch):
    import claude_lane
    import llm
    rows = _rows()
    ctx = SimpleNamespace(
        duration=75.0, first_cut_model="claude-haiku-5-5", first_cut_q1=True,
        first_cut_hook=None,
        index={"sentences": rows, "words": _words(rows), "silences": SILENCES,
               "spatial": {"samples": [{"t": 1, "faces": [[0.3, 0.3, 0.7, 0.6]]}] * 6}})
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    seen = []

    def create(model, messages, **k):
        seen.append(messages[0]["content"])
        msg = SimpleNamespace(content='{"start_id": "s2", "end_id": "s13", '
                                      '"hook": "A *million* times more power", "beats": []}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(provider_cost_usd=0.0003))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    start, end, how = first_cut.trim_range(ctx, None, {"usd": 0.0})
    assert (start, end) == (17.61, 42.28)
    assert talk_craft.OPENING_RULE in seen[0] and talk_craft.ENDING_RULE in seen[0]
    assert first_cut._TRIM_FIRST not in seen[0]


def test_a_grandfathered_first_cut_keeps_its_trim_exactly(monkeypatch):
    """D1: without the engine flag no Q5 prompt rule, guard or pause cut."""
    import claude_lane
    import llm
    rows = _rows()
    ctx = SimpleNamespace(
        duration=75.0, first_cut_model="claude-haiku-5-5", first_cut_q1=False,
        first_cut_hook=None,
        index={"sentences": rows, "words": _words(rows), "silences": SILENCES,
               "spatial": {"samples": [{"t": 1, "faces": [[0.3, 0.3, 0.7, 0.6]]}] * 6}})
    assert not first_cut._talk_source(ctx)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    seen = []

    def create(model, messages, **k):
        seen.append(messages[0]["content"])
        msg = SimpleNamespace(content='{"start_id": "s2", "end_id": "s13", '
                                      '"hook": "A *million* times", "beats": []}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(provider_cost_usd=0.0003))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)

    def boom(*a, **k):
        raise AssertionError("a Q5 guard ran for a grandfathered account")
    for name in ("opening_row", "closing_row", "cut_audio_pauses", "title_choice",
                 "clear_slams"):
        monkeypatch.setattr(talk_craft, name, boom)
    start, end, how = first_cut.trim_range(ctx, None, {"usd": 0.0})
    assert seen[0] == first_cut.TRIM_SYSTEM
    assert start == 3.98                       # the scout's own s2, as before


# ── the hook title ───────────────────────────────────────────────────────

def test_the_title_is_a_whole_clause_or_the_scouts_self_contained_hook():
    assert talk_craft.title_choice(("Deep learning was working", 2.1), True,
                                   "Deep *learning*") == ("Deep learning was working", 2.1)
    # the first words of a longer question are a fragment: the scout's hook
    assert talk_craft.title_choice(("what is an economy", 2.4), False,
                                   "A *million* times more electricity") == \
        ("A *million* times more electricity", 3.0)
    # a dependent clause is never the title, a dependent scout hook neither:
    # no title beats a fragment title
    assert talk_craft.title_choice(("Since we're a trillionth", 2.6), True,
                                   "Since we *grew*") is None
    assert talk_craft.title_choice(("I think if you're like", 2.6), False, None) is None
    # the long form falls back through its chapter titles
    assert talk_craft.title_choice(("I think if you're like", 2.6), False, None,
                                   more=[None, "Learning by *doing*"]) == \
        ("Learning by *doing*", 3.0)
    assert not talk_craft.self_contained("I think if you're like a founder, you learn.")
    assert talk_craft.self_contained("I think deep learning was working.")
    assert talk_craft.title_choice(("Since we're a trillionth", 2.6), True,
                                   "Intelligence will *solve* everything")[0] == \
        "Intelligence will *solve* everything"


# ── the pauses the viewer hears ──────────────────────────────────────────

def test_audible_pauses_are_cut_to_a_breath_on_the_programs_own_timeline():
    fs = talk_craft.FRAME_S
    silent = set()
    for a, b in ((0.4, 0.7), (3.0, 3.8), (6.0, 6.3), (8.8, 9.0), (10.0, 10.3), (14.0, 15.4)):
        silent.update(range(int(round(a / fs)), int(round(b / fs))))

    def quiet(f):
        return f in silent
    # one segment with a lead silence (0.3 s), a 0.8 s pause (cut to a
    # breath), a 0.3 s pause (kept); then a cut whose two sides are silent
    # (8.8-9.0 + 10.0-10.3 = 0.5 s heard as one pause) and a 1.4 s tail
    keep = talk_craft.program_cuts([[0.4, 9.0], [10.0, 15.4]], quiet)
    assert keep[0][0] == pytest.approx(0.67, abs=0.02)   # ~0.03 s before the word
    heard = sum(b - a for a, b in keep)
    assert heard == pytest.approx((15.4 - 10.0) + (9.0 - 0.4) - 0.27 - 0.64 - 0.34 - 0.95, abs=0.05)
    # the tail keeps 0.45 s of live picture after the last word
    assert keep[-1][1] == pytest.approx(14.45, abs=0.02)
    # the 0.3 s pause inside the first segment is untouched
    assert any(a <= 6.0 and 6.3 <= b for a, b in keep)


def test_the_silence_line_follows_the_mastering_gain():
    t = np.arange(talk_craft._RATE * 4) / talk_craft._RATE
    quiet_src = 0.05 * np.sin(2 * np.pi * 220 * t)      # about -29 LUFS speech
    line = talk_craft.silence_line(quiet_src.astype(np.float32))
    loud = talk_craft.silence_line((quiet_src * 4).astype(np.float32))
    # a quieter source is raised more by the master: its silence line is lower
    assert line < loud and abs((loud - line) - 12.04) < 0.2


# ── slams clear of the mouth ─────────────────────────────────────────────

def _slam_ctx(top, bottom, y):
    edl = {"keep": [[0.0, 30.0]], "vectors": [], "motion": [
        {"id": "mg1", "template": "word_slam", "start": 5.0, "end": 6.4,
         "params": {"y": y, "width": 0.82, "text": "*AlexNet* result"},
         "footprint": {"box": [0.06, top, 0.88, bottom]}}]}
    return SimpleNamespace(latest_edl=lambda: {"version": 3, "json": edl}), edl


def test_a_slam_under_the_chin_is_lowered_then_shrunk_then_carded(monkeypatch):
    calls = []

    def run(ctx, steps, name, args, started):
        calls.append((name, args))
        return f"EDL v3 -> v4: {name}"
    # chin at 0.50, slam ink from 0.52: lowered by 0.015 to start at 0.535
    ctx, _edl = _slam_ctx(0.52, 0.66, 0.59)
    monkeypatch.setattr(talk_craft, "chin_line", lambda c, e, a, b: 0.50)
    out = talk_craft.clear_slams(ctx, [], 0.0, run)
    assert calls[0][0] == "set_motion_graphic"
    assert calls[0][1]["params"]["y"] == pytest.approx(0.605, abs=1e-3)
    assert out == [("mg1", "lowered")] or out[0][1] in ("lowered", "carded")
    # a tall slam that cannot fit under the chin is set smaller
    calls.clear()
    ctx, _edl = _slam_ctx(0.52, 0.80, 0.66)
    talk_craft.clear_slams(ctx, [], 0.0, run)
    assert calls[0][0] == "set_motion_graphic" and "width" in calls[0][1]["params"]
    assert calls[0][1]["params"]["width"] < 0.82
    # clear already: nothing written
    calls.clear()
    ctx, _edl = _slam_ctx(0.60, 0.74, 0.67)
    assert talk_craft.clear_slams(ctx, [], 0.0, run) == [] and not calls


# ── B-roll that moves ────────────────────────────────────────────────────

def test_broll_asks_for_video_first_then_any_picture_for_the_misses(monkeypatch):
    import stock
    monkeypatch.setattr(stock, "video_available", lambda: True)
    edl = {"keep": [[0.0, 30.0]], "fx": []}
    ctx = SimpleNamespace(latest_edl=lambda: {"version": 2, "json": edl})
    calls = []

    def run(c, steps, name, args, started):
        calls.append((name, args))
        if name == "source_broll" and len(calls) == 1:
            m = args["moments"][0]           # only the first moment found a clip
            edl["fx"].append({"type": "broll", "start": m["at"],
                              "end": m["at"] + m["duration_s"]})
        return "EDL v2 -> v3"
    moments = [(4.0, 4.4, "sun", "the sun in space"), (12.0, 12.4, "factory", "a factory")]
    fcv.place_broll(ctx, [], 0.0, run, moments, 3.0)
    assert [n for n, _a in calls] == ["source_broll", "source_broll"]
    assert {m["kind"] for m in calls[0][1]["moments"]} == {"video"}
    assert [m["word"] for m in calls[1][1]["moments"]] == ["factory"]
    assert calls[1][1]["moments"][0]["kind"] == "any"


# ── the long talk ────────────────────────────────────────────────────────

def test_a_ten_minute_talk_keeps_about_a_third():
    assert longform.share_for(600.0) == 0.36
    assert longform.share_for(200.0) == 0.55
    assert "CHAPTERS" in longform.SYSTEM_Q5 and "BROLL" in longform.SYSTEM_Q5


def test_the_scouts_chapters_beats_and_broll_land_on_the_kept_stretches(monkeypatch):
    rows = [{"id": f"s{k}", "t0": k * 10.0, "t1": k * 10.0 + 9.5,
             "text": f"Sentence {k} names Stanford and the startup."} for k in range(30)]
    words = []
    for r in rows:
        for j, w in enumerate(r["text"].split()):
            words.append({"w": w, "t0": r["t0"] + j * 0.8, "t1": r["t0"] + j * 0.8 + 0.6})
    ctx = SimpleNamespace(index={"words": words, "sentences": rows})
    text = ('{"keep": [["s2","s5"], ["s12","s15"]], "chapters": ["Leaving *Stanford*", '
            '"The *startup*"], "beats": [{"id": "s3", "words": "Stanford"}, '
            '{"id": "s25", "words": "startup."}], "broll": [{"id": "s13", '
            '"word": "Stanford", "query": "Stanford campus"}]}')
    segs = [[20.0, 59.5], [120.0, 159.5]]
    ex = longform.extras(ctx, text, rows, segs)
    assert ex["titles"] == [(20.0, "Leaving *Stanford*"), (120.0, "The *startup*")]
    assert [b[2] for b in ex["beats"]] == ["Stanford"]          # s25 is cut
    assert [b[3] for b in ex["broll"]] == ["Stanford campus"]


def test_landscape_cut_ins_aim_at_each_shots_own_face(monkeypatch):
    import motion_tools
    words = [{"i": k, "w": "word.", "t0": k * 0.5, "t1": k * 0.5 + 0.4}
             for k in range(80)]
    edl = {"keep": [[0.0, 40.0]]}
    index = {"words": words, "shots": [{"start": 0.0}, {"start": 20.0}]}
    ctx = SimpleNamespace(index=index, latest_edl=lambda: {"version": 2, "json": edl})
    import device_planner
    monkeypatch.setattr(device_planner, "program_words", lambda e, i, tl=None: words)

    def measure(src_t):
        if src_t < 20.0:
            return [[0.55, 0.2, 0.7, 0.45]]          # the speaker on the right
        return []                                      # a wide shot: no face
    monkeypatch.setattr(motion_tools, "_face_measure", lambda c, **k: measure)
    calls = []

    def run(c, steps, name, args, started):
        calls.append(args)
        return "EDL v2 -> v3"
    n = talk_craft.landscape_cut_ins(ctx, [], 0.0, run, [], 3.0)
    assert n == len(calls) >= 2
    assert all(a["end"] <= 20.0 + 1e-6 for a in calls)      # never across the shot change
    assert all(a["cx"] == pytest.approx(0.625) for a in calls)
    assert {a["strength"] for a in calls} == set(talk_craft.LONG_LEVELS)
    gaps = [b["start"] - a["start"] for a, b in zip(calls, calls[1:])]
    assert max(gaps) <= 2 * talk_craft.LONG_CUT_MAX_S + 0.01


def test_chapter_cards_open_each_later_stretch_and_keep_clear_of_cutaways():
    edl = {"keep": [[0.0, 60.0], [100.0, 160.0], [200.0, 260.0]], "vectors": []}
    ctx = SimpleNamespace(latest_edl=lambda: {"version": 2, "json": edl})
    calls = []

    def run(c, steps, name, args, started):
        calls.append((name, args))
        if name == "add_vector_graphic":
            edl["vectors"].append({"id": f"v{len(calls)}"})
        return "EDL v2 -> v3"
    placed = talk_craft.chapter_cards(ctx, [], 0.0, run, [100.0, 200.0],
                                      ["The *startup*", "Scale *wins*"], 3.0,
                                      avoid=[(120.5, 122.0)])
    # program 60 s (the second stretch) gets its card; the third collides
    assert placed == [(60.0, 61.5)]
    names = [n for n, _a in calls]
    assert names.count("add_motion_graphic") == 1


def test_the_long_form_ending_never_lands_on_a_trailing_line():
    rows = [{"id": f"s{k}", "t0": k * 6.0, "t1": k * 6.0 + 5.0,
             "text": "So that is why we kept scaling the networks."} for k in range(20)]
    rows[15]["text"] = "Longevity being, you know, certainly one of them."
    cands = longform.end_candidates(rows, 15, 40.0)
    assert 15 not in cands and cands


def test_the_video_pass_asks_only_the_curated_stock_libraries(monkeypatch):
    import broll_source
    import stock
    monkeypatch.setattr(stock, "video_available", lambda: True)
    monkeypatch.setattr(broll_source, "status", lambda: {
        "pexels": "on", "pixabay": "off: no key", "openverse": "on",
        "wikimedia": "on", "nasa": "on"})
    edl = {"keep": [[0.0, 30.0]], "fx": []}
    ctx = SimpleNamespace(latest_edl=lambda: {"version": 2, "json": edl})
    calls = []

    def run(c, steps, name, args, started):
        calls.append(args)
        return "EDL v2 -> v3"
    fcv.place_broll(ctx, [], 0.0, run, [(4.0, 4.4, "sun", "the sun in space")], 3.0)
    # an archive "video" answered "power plant at night" with a drone strike
    assert calls[0]["providers"] == ["pexels"]
    assert "providers" not in calls[1]                     # the misses: any library
    assert calls[0]["moments"][0]["purpose"] == "show the sun in space as 'sun' is said"


def test_no_cutaway_covers_the_closing_sentence():
    from timeline import Timeline
    tl = Timeline([[0.0, 30.0]], [], None)
    moments = [(5.0, 5.4, "sun", "q"), (12.0, 12.4, "factory", "q"),
               (24.0, 24.4, "rocket", "q")]
    free = fcv.broll_windows(moments, tl, 30.0, 3.0)
    assert any(w[2] == "rocket" for w in free)
    kept = fcv.broll_windows(moments, tl, 30.0, 3.0, clear_from=23.0)
    assert all(b <= 23.0 + 1e-6 for _a, b, _w, _q in kept)
    assert not any(w[2] == "rocket" for w in kept)


def test_the_closing_sentence_starts_after_the_last_full_stop(monkeypatch):
    import device_planner
    words = [{"w": w, "t0": k * 0.5, "t1": k * 0.5 + 0.4} for k, w in enumerate(
        "What is it? It will solve everything you can think of.".split())]
    monkeypatch.setattr(device_planner, "program_words", lambda e, i, tl=None: words)
    ctx = SimpleNamespace(index={}, latest_edl=lambda: {"version": 1, "json": {}})
    assert talk_craft.last_sentence_start(ctx) == 1.5     # "It"


def test_a_source_with_burned_captions_gets_none_on_top():
    centred = {"text": [[0.25, 0.46, 0.75, 0.53]], "faces": [[0.3, 0.2, 0.7, 0.5]]}
    stray = {"text": [[0.0, 0.32, 0.31, 0.44]], "faces": [[0.3, 0.2, 0.7, 0.5]]}
    assert talk_craft.burned_captions({"spatial": {"samples": [centred] * 8 + [stray] * 2}})
    assert not talk_craft.burned_captions({"spatial": {"samples": [stray] * 8 + [centred] * 2}})
    # a static narrow band (the phone fixture's event bar) is not captions
    band = {"text": [[0.39, 0.40, 0.62, 0.47]]}
    assert not talk_craft.burned_captions({"spatial": {"samples": [band] * 10}})


def test_a_phrase_said_twice_is_cut_once():
    words = [{"w": w, "t0": k * 0.3, "t1": k * 0.3 + 0.25} for k, w in enumerate(
        "So I think I think the field was wrong and it was it was clear".split())]
    spans = talk_craft.phrase_repeats(words, [[0.0, 10.0]])
    assert spans == [(0.3, 0.89), (3.0, 3.59)]
    # "very very" is emphasis, one word: not a phrase repeat
    assert talk_craft.phrase_repeats(
        [{"w": w, "t0": k * 0.3, "t1": k * 0.3 + 0.25}
         for k, w in enumerate("it is very very good".split())], [[0.0, 5.0]]) == []
