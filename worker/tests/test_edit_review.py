"""The advisory "earn its place" review (edit_review, judge round Oct 2026).

Pinned here, one block per rule:
  1. HOOK: a generic question, a hook that spends a later hero word, an
     opening on a word fragment or a disfluency, a jump cut in the first
     1.5 s (or two in the first 3 s), no face on the opening frames.
  2. GRAPHIC BUDGET: more than about one graphic per 6 s, more than ~50% of
     the runtime under graphics, more than 3 type roles, a second accent,
     a graphic that only re-typesets the words being heard, a spoken list set
     as a text stack.
  3. PAYOFF: under 0.6 s of air after the last word (or a dead tail), a
     closing reaction under 1 s, a payoff number without its noun.
  4. IDENTIFY: 'Lisa:' alone on a line; a broadcast lower third beside a
     hook or headline.
  5. TRIM: sentences colliding across a cut, an article dropped inside a
     clause.
  6. RESTRAINT: a barely visible push and a push stacked on a follow pan are
     offered for REMOVAL only; no note ever asks for a zoom or a sound.
  7. FRAME: full-bleed with no committed grade (a taste note).
  8. Advisory plumbing: one non-blocking verification finding, suppressed
     by the user's own request, never raising, gated to short-form.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import edit_review  # noqa: E402
import quality_verifier  # noqa: E402


def _words(text, start=0.2, step=0.3, gap_after=None):
    """Index words for ``text``: each word ``step`` long, back to back,
    with an extra pause after words listed in ``gap_after`` {index: s}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append({"w": w, "t0": round(t, 3), "t1": round(t + step - 0.02, 3)})
        t += step + (gap_after or {}).get(i, 0.0)
    return out


def _edl(keep=None, motion=(), dur=30.0, grade="warm", **extra):
    edl = {"keep": keep or [[0.0, dur]], "inserts": [], "speed": [],
           "texts": [], "motion": list(motion), "sfx": [], "music": [],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5},
           "effects": {"grade": grade, "zooms": []},
           "captions": {"mode": "from_transcript",
                        "style": {"motion_look": "editorial",
                                  "highlight_color": "#FFC940"}}}
    edl.update(extra)
    return edl


def _mg(id_, template, start, end, **params):
    params.setdefault("accent", "#FFC940")
    return {"id": id_, "template": template, "start": start, "end": end,
            "params": params}


def _codes(edl, index=None, **kw):
    return [n["code"] for n in edit_review.review(edl, index or {}, **kw)]


def _note(edl, code, index=None, **kw):
    return next(n for n in edit_review.review(edl, index or {}, **kw)
                if n["code"] == code)


# A 30 s talk, 100 words at 0.3 s each starting 0.2 s: "w0 w1 ... w99".
FILLER = " ".join(f"w{i}" for i in range(100))


# ── 1. hook ──────────────────────────────────────────────────────────────

def test_a_generic_question_hook_is_flagged_with_a_payoff_led_fix():
    edl = _edl(motion=[_mg("hook", "word_slam", 0.0, 2.3,
                           text="where did / *progress* go?",
                           kicker="Peter Thiel")])
    note = _note(edl, "hook_generic_question")
    assert note["rank"] == 1 and note["at"] == 0.0
    assert "generic question" in note["message"]
    assert "strongest line or statistic" in note["fix"]


def test_a_specific_claim_or_a_numbered_question_is_a_fine_hook():
    claim = _edl(motion=[_mg("hook", "hook_title", 0.0, 2.3,
                             text="They promised us / *flying cars*…")])
    numbered = _edl(motion=[_mg("hook", "hook_title", 0.0, 2.3,
                                text="Why do *9 in 10* startups fail?")])
    named = _edl(motion=[_mg("hook", "hook_title", 0.0, 2.3,
                             text="Why did Apple kill the *Lisa*?")])
    shouting = _edl(motion=[_mg("hook", "hook_title", 0.0, 2.3,
                                text="WHERE DID PROGRESS GO?")])
    assert "hook_generic_question" not in _codes(claim)
    assert "hook_generic_question" not in _codes(numbered)
    assert "hook_generic_question" not in _codes(named)
    assert "hook_generic_question" in _codes(shouting)


def test_a_claim_question_without_a_number_ranks_below_a_wh_question():
    edl = _edl(motion=[_mg("hook", "hook_title", 0.0, 2.2,
                           text="Gamers make / *better surgeons?*")])
    note = _note(edl, "hook_generic_question")
    assert note["rank"] == 2 and "no number or claim" in note["message"]


def test_the_hook_must_not_spend_a_later_hero_word():
    hook = _mg("hook", "phrase_build", 0.0, 4.2, rows=[
        {"text": "Steve Jobs, 1983", "role": "sans"},
        {"text": "computer fonts were", "role": "serif"},
        {"text": "*GARBAGE*", "role": "condensed"}])
    slam = _mg("garbage", "word_slam", 13.24, 14.09, text="*garbage*",
               kicker="the fonts have been just")
    note = _note(_edl(motion=[hook, slam]), "hook_spends_hero_word")
    assert note["at"] == 13.24 and "GARBAGE" in note["message"]
    assert "transform the later instance" in note["fix"]
    # a kicker word is not a hero word: 'fonts' in the hook is fine
    other = _mg("pics", "word_slam", 20.0, 21.0, text="*pictures*",
                kicker="the fonts let us deal in")
    assert "hook_spends_hero_word" not in _codes(_edl(motion=[hook, other]))


def test_the_same_hero_word_twice_after_the_hook_is_a_repeat():
    a = _mg("a", "word_slam", 6.0, 7.0, text="*enough*")
    b = _mg("b", "word_slam", 20.0, 21.0, text="never *enough*")
    note = _note(_edl(motion=[a, b]), "repeated_hero_word")
    assert note["at"] == 20.0 and "ENOUGH" in note["message"]


def test_opening_inside_a_word_is_a_fragment():
    words = [{"w": "say", "t0": 9.7, "t1": 9.84},
             {"w": "the", "t0": 9.84, "t1": 10.2}] + _words(FILLER, start=10.3)
    edl = _edl(keep=[[9.78, 39.78]])
    note = _note(edl, "hook_opens_on_fragment", {"words": words})
    assert "tail of 'say'" in note["message"] and "9.84" in note["fix"]
    clean = _edl(keep=[[9.84, 39.84]])
    assert "hook_opens_on_fragment" not in _codes(clean, {"words": words})


def test_a_disfluent_opening_is_flagged():
    words = _words("if somebody was like, ever good at video games " + FILLER)
    note = _note(_edl(), "hook_opens_on_fragment", {"words": words})
    assert "disfluency" in note["message"] and "like," in note["message"]
    # 'like' as a verb is not a filler
    plain = _words("people like video games a lot " + FILLER)
    assert "hook_opens_on_fragment" not in _codes(_edl(), {"words": plain})


def test_a_jump_cut_inside_the_hook_is_flagged_and_a_camera_change_is_not():
    edl = _edl(keep=[[0.0, 0.8], [1.5, 30.0]])
    note = _note(edl, "hook_jump_cut", {"words": _words(FILLER)})
    assert note["at"] == 0.8 and "0.80s" in note["message"]
    assert "restore_range" in note["fix"] and "zoom" not in note["fix"].lower()
    later = _edl(keep=[[0.0, 5.0], [5.5, 30.0]])
    assert "hook_jump_cut" not in _codes(later, {"words": _words(FILLER)})
    shots = {"words": _words(FILLER),
             "shots": [{"id": 0, "start": 0.0, "end": 1.0},
                       {"id": 1, "start": 1.0, "end": 40.0}]}
    assert "hook_jump_cut" not in _codes(edl, shots)


def test_two_cuts_in_the_first_three_seconds_are_flagged():
    edl = _edl(keep=[[0.0, 1.8], [2.0, 2.6], [3.0, 30.0]])
    note = _note(edl, "hook_jump_cut", {"words": _words(FILLER)})
    assert note["evidence"]["cuts"] == [1.8, 2.4]


def test_no_face_on_the_opening_frames_is_flagged_when_faces_exist_later():
    face = [[0.3, 0.2, 0.6, 0.5]]
    samples = ([{"t": 0.3, "faces": []}, {"t": 0.8, "faces": []}]
               + [{"t": 2.6 + k, "faces": face} for k in range(5)])
    note = _note(_edl(), "hook_no_face", {"spatial": {"samples": samples}})
    assert note["evidence"]["first_face_s"] == 2.6
    facing = [{"t": 0.3, "faces": face}] + samples[2:]
    assert "hook_no_face" not in _codes(_edl(), {"spatial": {"samples": facing}})
    nobody = [{"t": t, "faces": []} for t in (0.3, 3.0, 9.0)]
    assert "hook_no_face" not in _codes(_edl(), {"spatial": {"samples": nobody}})


# ── 2. graphic budget ────────────────────────────────────────────────────

def _slams(n, every, length=1.0, start=3.0):
    return [_mg(f"g{k}", "word_slam", start + k * every,
                start + k * every + length, text=f"*hero{k}*")
            for k in range(n)]


def test_more_than_one_graphic_per_six_seconds_is_over_budget():
    note = _note(_edl(motion=_slams(7, 3.9)), "graphic_budget")
    assert note["evidence"]["graphics"] == 7 and note["evidence"]["allowed"] == 5
    assert "one per 6-8 s" in note["message"]
    assert "graphic_budget" not in _codes(_edl(motion=_slams(4, 6.0)))


def test_a_stat_run_counts_as_one_designed_moment():
    run = [_mg(f"st{k}", "word_slam", 8.0 + k * 1.8, 9.8 + k * 1.8,
               text=f"*{n}%* / fewer errors", fit="justify")
           for k, n in enumerate((32, 24, 26))]
    edl = _edl(motion=run + _slams(3, 6.0, start=16.0), dur=24.0)
    assert "graphic_budget" not in _codes(edl)


def test_one_big_word_per_list_item_is_one_designed_moment():
    run = [_mg(f"item{k}", "word_slam", 3.0 + k * 1.3, 3.6 + k * 1.3,
               text=f"*{w}*", role="condensed")
           for k, w in enumerate(("ROCKETS", "SUPERSONIC", "UNDERWATER",
                                  "MEDICINES"))]
    edl = _edl(motion=run + _slams(3, 7.0, start=12.0))
    assert "graphic_budget" not in _codes(edl)


def test_graphics_over_half_the_runtime_are_over_budget():
    long_ones = [_mg("a", "word_slam", 1.0, 9.0, text="*one*"),
                 _mg("b", "word_slam", 12.0, 21.0, text="*two*")]
    note = _note(_edl(motion=long_ones), "graphic_budget")
    assert note["evidence"]["coverage"] > 0.5 and "57%" in note["message"]


def test_the_persistent_headline_and_transitions_are_not_budgeted():
    edl = _edl(motion=[_mg("hl", "headline", 0.0, None,
                           text="The case for *beautiful* type"),
                       _mg("fl", "flash_transition", 10.0, 10.3)]
               + _slams(3, 8.0))
    assert "graphic_budget" not in _codes(edl)


def test_more_than_three_type_roles_is_flagged():
    edl = _edl(motion=[
        _mg("a", "word_slam", 3.0, 4.0, text="*one*", role="grotesk"),
        _mg("b", "word_slam", 10.0, 11.0, text="*two*", role="serif"),
        _mg("c", "typewriter", 16.0, 17.0, text="three", font="mono"),
        _mg("d", "word_slam", 22.0, 23.0, text="*four*", role="condensed")])
    note = _note(edl, "type_roles")
    assert note["evidence"]["roles"] == ["condensed", "mono", "sans", "serif"]
    two = _edl(motion=[
        _mg("a", "word_slam", 3.0, 4.0, text="*one*", role="grotesk"),
        _mg("b", "word_slam", 10.0, 11.0, text="*two*", role="serif")])
    assert "type_roles" not in _codes(two)


def test_a_second_accent_colour_is_flagged_only_where_it_is_drawn():
    edl = _edl(motion=[
        _mg("a", "word_slam", 3.0, 4.0, text="*one*", accent="#FF3B30"),
        _mg("b", "marker_text", 12.0, 14.0, text="the *line*",
            accent="#FFD84D")])
    note = _note(edl, "accent_colours")
    assert "#FF3B30" in note["message"] and "#FFD84D" in note["message"]
    unstarred = _edl(motion=[
        _mg("a", "word_slam", 3.0, 4.0, text="*one*"),
        _mg("b", "word_slam", 12.0, 13.0, text="two", accent="#00FF00")])
    assert "accent_colours" not in _codes(unstarred)


SPOKEN = ("we have a narrow cone of progress around the world of bits and "
          "that is the whole story of the last forty years in technology")


def test_a_graphic_that_only_retypesets_the_heard_words_restates_captions():
    words = _words(SPOKEN + " " + FILLER)       # "narrow" at 1.1 s
    thesis = _mg("thesis", "phrase_build", 1.6, 4.0, rows=[
        {"text": "a *narrow cone*", "role": "condensed"},
        {"text": "of progress around", "role": "serif"},
        {"text": "the world of *bits*", "role": "sans"}])
    edl = _edl(motion=[thesis])
    note = _note(edl, "restates_captions", {"words": words})
    assert note["evidence"]["ids"] == ["thesis"]
    assert "number, a contrast, an identification, evidence or an image" in note["fix"]


def test_a_number_a_paraphrase_or_an_information_template_is_not_restating():
    words = _words(SPOKEN + " " + FILLER)
    idx = {"words": words}
    number = _mg("n", "phrase_build", 6.0, 8.0, rows=[
        {"text": "the last *40* years"}, {"text": "in technology"},
        {"text": "whole story"}])
    paraphrase = _mg("p", "phrase_build", 1.6, 4.0, rows=[
        {"text": "innovation stalled"}, {"text": "outside software"},
        {"text": "since the seventies"}])
    split = _mg("vs", "versus_split", 1.6, 4.0, left="ATOMS", right="BITS",
                left_sub="cone narrow", right_sub="world progress")
    for item in (number, paraphrase, split):
        assert "restates_captions" not in _codes(_edl(motion=[item]), idx)


def test_the_payoff_lockup_and_the_hook_may_quote_the_line():
    words = _words(FILLER[:300] + " " + SPOKEN, start=0.2)
    t = next(w["t0"] for w in words if w["w"] == "narrow")
    payoff = _mg("pay", "phrase_build", t, 30.0, rows=[
        {"text": "a *narrow cone*"}, {"text": "of progress around"},
        {"text": "the world of *bits*"}])
    assert "restates_captions" not in _codes(_edl(motion=[payoff]),
                                             {"words": words})


def test_a_spoken_list_as_a_text_stack_is_a_transcript_list():
    words = _words("technology meant computers but also rockets and supersonic "
                   "aviation and underwater cities and new medicines " + FILLER)
    stack = _mg("list", "phrase_build", 1.6, 4.5, rows=[
        {"text": "ROCKETS", "role": "condensed"},
        {"text": "supersonic aviation", "role": "serif"},
        {"text": "underwater cities", "role": "sans"},
        {"text": "NEW *MEDICINES*", "role": "condensed"}])
    note = _note(_edl(motion=[stack]), "transcript_list", {"words": words})
    assert note["rank"] == 1
    assert "semantic visual insert" in note["fix"] and "search_stock" in note["fix"]
    typed = _words("you know computers, internet, mobile, it generated " + FILLER)
    tw = _mg("bits", "typewriter", 1.6, 3.0, text="computers. internet. mobile.",
             font="mono")
    assert "transcript_list" in _codes(_edl(motion=[tw]), {"words": typed})


def test_the_budget_names_the_restating_graphics_as_first_cuts():
    words = _words(SPOKEN + " " + FILLER)
    thesis = _mg("thesis", "phrase_build", 1.6, 4.0, rows=[
        {"text": "a *narrow cone*"}, {"text": "of progress around"},
        {"text": "the world of *bits*"}])
    edl = _edl(motion=[thesis] + _slams(6, 4.0, start=6.0))
    note = _note(edl, "graphic_budget", {"words": words})
    assert "'thesis'" in note["fix"]


# ── 3. payoff ────────────────────────────────────────────────────────────

def test_a_punchline_without_air_before_the_end_card_is_flagged():
    words = _words(FILLER)                      # last word ends at 30.18
    note = _note(_edl(dur=30.5), "payoff_hold", {"words": words})
    assert note["evidence"]["hold_s"] == 0.32 and "0.6-1.5 s" in note["fix"]
    assert "payoff_hold" not in _codes(_edl(dur=31.0), {"words": words})
    tail = _note(_edl(dur=34.0), "payoff_hold", {"words": words})
    assert "dead tail" in tail["message"]


def test_the_payoff_hold_fix_reads_how_much_tail_the_source_has():
    words = _words(FILLER)                      # 'w99' ends at 30.18
    roomy = words + [{"w": "Next", "t0": 31.4, "t1": 31.7}]
    note = _note(_edl(dur=30.5), "payoff_hold", {"words": roomy})
    assert "extend the last keep to about" in note["fix"] and "'Next'" in note["fix"]
    tight = words + [{"w": "Not", "t0": 30.6, "t1": 30.8}]
    note = _note(_edl(dur=30.5), "payoff_hold", {"words": tight})
    assert "runs on into 'Not'" in note["fix"]
    assert "payoff graphic hold to the end card" in note["fix"]


def test_a_closing_reaction_under_a_second_is_flagged():
    words = _words(FILLER)
    # a separate take after a real cut
    short = _edl(keep=[[0.0, 30.5], [33.0, 33.6]])
    note = _note(short, "reaction_button_short", {"words": words})
    assert note["evidence"]["seconds"] == 0.6
    held = _edl(keep=[[0.0, 30.5], [33.0, 34.3]])
    assert "reaction_button_short" not in _codes(held, {"words": words})
    # Elon: the same source time, but the frame re-aims at the listener
    # (a new focus span) or the camera changes: still a reaction shot
    reaimed = _edl(keep=[[0.0, 30.5], [30.5, 31.1]])
    reaimed["frame"]["focus_track"] = [{"t0": 0.0, "t1": 30.5, "x": 0.4},
                                       {"t0": 30.5, "t1": 40.0, "x": 0.57}]
    assert "reaction_button_short" in _codes(reaimed, {"words": words})
    shots = {"words": words, "shots": [{"id": 0, "start": 0.0, "end": 30.5},
                                       {"id": 1, "start": 30.5, "end": 60.0}]}
    assert "reaction_button_short" in _codes(
        _edl(keep=[[0.0, 30.5], [30.5, 31.1]]), shots)


def test_a_keep_split_at_the_same_source_time_is_no_reaction():
    words = _words(FILLER)
    split = _edl(keep=[[0.0, 30.5], [30.5, 31.1]])     # the line's own tail
    assert "reaction_button_short" not in _codes(split, {"words": words})


def test_a_payoff_number_needs_its_noun():
    bare = _mg("num", "counter", 28.5, 30.0, value="140", label="",
               style="reveal")
    note = _note(_edl(motion=[bare]), "payoff_number_without_noun")
    assert "140 / CHARACTERS" in note["fix"]
    noun = _mg("num", "counter", 28.5, 30.0, value="140", label="characters",
               style="reveal")
    assert "payoff_number_without_noun" not in _codes(_edl(motion=[noun]))
    # a requested CTA after the payoff is not the payoff
    cta = _mg("cta", "save_cta", 29.0, 30.0)
    bare_early = _mg("num", "counter", 26.0, 28.8, value="140", label="",
                     style="reveal")
    assert "payoff_number_without_noun" in _codes(_edl(motion=[bare_early, cta]))


# ── 4. identify ──────────────────────────────────────────────────────────

def test_a_name_with_a_colon_on_its_own_line_reads_as_a_dialogue_label():
    lisa = _mg("lisa", "phrase_build", 14.6, 17.8, rows=[
        {"text": "Lisa:", "role": "script"},
        {"text": "*totally* proportionally"}, {"text": "SPACED TEXT"}])
    note = _note(_edl(motion=[lisa]), "label_reads_as_dialogue")
    assert note["evidence"]["name"] == "Lisa" and "Identify it" in note["fix"]
    headline = _mg("hl", "headline", 0.0, None,
                   text="Steve Jobs: computer fonts were *weird*")
    assert "label_reads_as_dialogue" not in _codes(_edl(motion=[headline]))
    speaker = _mg("q", "phrase_build", 9.0, 12.0, rows=[
        {"text": "Steve Jobs:"}, {"text": "*weird* type"}])
    assert "label_reads_as_dialogue" not in _codes(_edl(motion=[speaker]))


def test_a_lower_third_beside_a_hook_is_redundant_unless_asked_for():
    hook = _mg("hook", "hook_title", 0.0, 2.2, text="Gamers make *32%* fewer errors")
    name = _mg("name", "lower_third", 2.4, 5.6, name="Elon Musk",
               role="on The Joe Rogan Experience", style="clean_bar")
    note = _note(_edl(motion=[hook, name]), "lower_third_redundant")
    assert "kicker" in note["fix"] and "headline band" in note["fix"]
    assert "lower_third_redundant" not in _codes(_edl(motion=[name]))
    assert "lower_third_redundant" not in _codes(
        _edl(motion=[hook, name]), request_text="Add a lower third with his name")


# ── 5. trim rhythm ───────────────────────────────────────────────────────

def test_sentences_colliding_across_a_cut_are_flagged():
    words = (_words("that is incredible.", start=0.2)
             + _words("um you should be required " + FILLER, start=1.4))
    collide = _edl(keep=[[0.0, 1.1], [1.65, 31.0]])   # 'incredible.' | 'you'
    note = _note(collide, "sentence_collision", {"words": words})
    assert "incredible." in note["message"] and "150-250 ms" in note["fix"]
    breath = _edl(keep=[[0.0, 1.3], [1.65, 31.0]])
    assert "sentence_collision" not in _codes(breath, {"words": words})


def test_an_article_dropped_inside_a_clause_is_flagged():
    words = [{"w": "has", "t0": 0.2, "t1": 0.4}, {"w": "used", "t0": 0.4, "t1": 0.7},
             {"w": "a", "t0": 0.75, "t1": 0.85}, {"w": "weird", "t0": 0.9, "t1": 1.2},
             {"w": "type.", "t0": 1.2, "t1": 1.5}] + _words(FILLER, start=1.6)
    edl = _edl(keep=[[0.0, 0.72], [0.88, 31.0]])
    note = _note(edl, "clause_word_dropped", {"words": words})
    assert "[a]" in note["message"] and "restore_range" in note["fix"]


# ── 6. restraint: zoom notes only ever offer removal ─────────────────────

def test_a_barely_visible_push_is_offered_for_removal():
    edl = _edl()
    edl["effects"]["zooms"] = [{"id": "z5", "start": 18.9, "end": 21.9,
                                "strength": 0.08, "mode": "ease"}]
    note = _note(edl, "zoom_barely_visible")
    assert note["fix"].startswith("Remove it")
    edl["effects"]["zooms"] = [{"id": "z1", "start": 18.9, "end": 21.9,
                                "strength": 0.15, "mode": "punch"}]
    assert "zoom_barely_visible" not in _codes(edl)


def test_a_push_stacked_on_a_follow_pan_is_flagged():
    edl = _edl()
    edl["effects"]["zooms"] = [{"id": "z7", "start": 26.0, "end": 30.0,
                                "strength": 0.1, "mode": "ease"}]
    edl["frame"]["follow"] = [{"t0": 0.0, "t1": 30.0,
                               "k": [[5.0, 0.4, 0.5], [27.0, 0.4, 0.5],
                                     [28.0, 0.44, 0.5]]}]
    note = _note(edl, "zoom_with_follow_pan")
    assert note["at"] == 27.0 and note["fix"].startswith("Remove the zoom")


ASKS_FOR_A_DEVICE = ("add_zoom", "add a zoom", "punch in to", "add_sfx",
                     "add a sound", "needs a sound", "with no camera move",
                     "silent mix", "zoom ladder", "zoom on every", "add_sfx(")


def test_no_note_ever_asks_for_a_zoom_or_a_sound():
    words = _words("if somebody was like, " + FILLER)
    edl = _edl(keep=[[0.0, 0.8], [1.5, 30.0]], grade=None,
               motion=[_mg("hook", "word_slam", 0.0, 2.0, text="why *this*?")]
               + _slams(8, 3.0))
    edl["effects"]["zooms"] = [{"id": "z", "start": 10.0, "end": 13.0,
                                "strength": 0.06, "mode": "push_in"}]
    notes = edit_review.review(edl, {"words": words})
    assert len(notes) >= 5
    for note in notes:
        text = (note["message"] + " " + note["fix"]).lower()
        assert not any(a in text for a in ASKS_FOR_A_DEVICE), text


# ── 7. frame ─────────────────────────────────────────────────────────────

def test_full_bleed_without_a_grade_is_a_taste_note():
    designed = [_mg("a", "word_slam", 6.0, 7.0, text="*one*")]
    note = _note(_edl(grade=None, motion=designed), "frame_uncommitted")
    assert "taste call, not a default" in note["fix"]
    assert "frame_uncommitted" not in _codes(_edl(grade="warm", motion=designed))
    # a plain clip the user only asked to caption or trim is theirs
    assert "frame_uncommitted" not in _codes(_edl(grade=None))
    carded = _edl(grade=None, motion=designed)
    carded["effects"]["picture_cards"] = [
        {"id": "card", "start": 0.0, "end": 30.0, "box": [0.06, 0.3, 0.94, 0.68]}]
    assert "frame_uncommitted" not in _codes(carded)


# ── 7b. the guidance's own devices draw no note ──────────────────────────

def test_a_kicker_naming_speaker_and_year_spends_no_hero_word():
    # the playbook's example: kicker 'Steve Jobs, 1983' and later an image
    # card identifying 'Apple Lisa, 1983' — both identify, neither slams
    hook = _mg("hook", "hook_title", 0.0, 2.6,
               text="Every computer has used *weird* type",
               kicker="Steve Jobs, 1983")
    lisa = _mg("lisa", "image_card", 14.8, 17.0, caption="Apple Lisa, 1983")
    assert _codes(_edl(motion=[hook, lisa])) == []
    # a number in the hook's CLAIM still spends a later number slam
    spend = _mg("hook", "hook_title", 0.0, 2.6, text="*140* characters?")
    num = _mg("num", "counter", 26.0, 28.0, value="140", label="characters")
    assert "hook_spends_hero_word" in _codes(_edl(motion=[spend, num]))


def test_a_list_run_after_a_hook_slam_is_one_moment_at_speech_spacing():
    hook = _mg("hook", "hook_title", 0.0, 2.24,
               text="They promised us *flying cars*")
    # list items said ~1.75 s apart: gaps of 1.1 s between the slams
    items = [_mg(f"i{k}", "word_slam", 3.07 + k * 1.75, 3.70 + k * 1.75,
                 text=f"*{w}*")
             for k, w in enumerate(("ROCKETS", "SUPERSONIC", "UNDERWATER",
                                    "MEDICINES"))]
    rest = [_mg("vs", "versus_split", 11.3, 14.7, left="1960s", right="TODAY"),
            _mg("e", "word_slam", 20.0, 21.6, text="*enough*"),
            _mg("n", "counter", 28.6, 30.0, value="140", label="characters")]
    assert "graphic_budget" not in _codes(_edl(motion=[hook] + items + rest))
    runs = edit_review._series(edit_review.moments(_edl(motion=[hook] + items)),
                               solo={"hook"})
    assert [r["ids"] for r in runs] == [["hook"], ["i0", "i1", "i2", "i3"]]
    # a word_slam hook never joins the run that follows it
    slam_hook = dict(hook, template="word_slam")
    runs = edit_review._series(edit_review.moments(_edl(motion=[slam_hook] + items)),
                               solo={"hook"})
    assert runs[0]["ids"] == ["hook"]


def _editorial_headline(text, dur=30.0, speaker="Peter Thiel", per_row=5):
    """The text layers set_editorial_graphic(kind='headline') writes: one
    'title' layer PER WORD, ids 'eg_<id>__<row>_<word>', the speaker label
    first, all over the whole window (built by hand so the test does not
    depend on font metrics)."""
    words = [speaker + ":"] + text.split()
    return [{"id": f"eg_hl__{k // per_row}_{k % per_row}", "template": "title",
             "text": w, "start": 0.0, "end": dur}
            for k, w in enumerate(words)]


def test_an_editorial_headline_band_is_layout_but_still_the_hook():
    claim = _editorial_headline("They promised us flying cars")
    edl = _edl(texts=claim,
               motion=[_mg(f"s{k}", "word_slam", 5 + 8 * k, 6 + 8 * k,
                           text=f"*w{k}*") for k in range(3)])
    # the whole-program headline is not a graphic over 100% of the runtime
    assert "graphic_budget" not in _codes(edl)
    assert [m["id"] for m in edit_review.moments(edl)] == ["s0", "s1", "s2"]
    band = edit_review.bands(edl)
    # each row of per-word layers reads as one line
    assert [t for _k, t in band[0]["lines"]] == [
        "Peter Thiel: They promised us flying", "cars"]
    # 'Peter Thiel: Where did progress go?' is still a generic question
    generic = _editorial_headline("Where did progress go?")
    note = _note(_edl(texts=generic), "hook_generic_question")
    assert note["rank"] == 1 and "Where did progress go?" in note["message"]
    # and a headline that shows the word a later slam spends is a spent hook
    spent = _edl(texts=_editorial_headline("Computer fonts were garbage"),
                 motion=[_mg("g", "word_slam", 13.24, 14.1, text="*garbage*")])
    assert "hook_spends_hero_word" in _codes(spent)


def test_the_editorial_headline_layers_match_what_the_tool_writes():
    # the hand-built layers above follow editorial_graphics' id scheme
    import editorial_graphics
    real = editorial_graphics.compose(
        id="hl", kind="headline", text="They promised us flying cars",
        start=0, end=30, secondary=None, eyebrow=None, palette="ink", box=None,
        motion="settle", W=1080, H=1920, motion_motif=None, treatment="panel",
        mute_captions=False, speaker="Peter Thiel", font_size=None, fit="auto")
    texts = real["texts"]
    assert texts[0]["text"] == "Peter Thiel:"
    assert all(edit_review._EDITORIAL_ID.match(t["id"]) for t in texts)
    band = edit_review.bands(_edl(texts=texts))
    assert " ".join(t for _k, t in band[0]["lines"]) == \
        "Peter Thiel: They promised us flying cars"


def test_a_stutter_cut_drops_no_clause_word():
    words = ([{"w": "of", "t0": 0.2, "t1": 0.4}, {"w": "the", "t0": 0.45, "t1": 0.6},
              {"w": "the", "t0": 0.7, "t1": 0.85}, {"w": "world.", "t0": 0.9, "t1": 1.3}]
             + _words(FILLER, start=1.5))
    edl = _edl(keep=[[0.0, 0.65], [0.86, 31.0]])
    assert "clause_word_dropped" not in _codes(edl, {"words": words})


def test_an_editorial_label_with_a_colon_is_not_a_dialogue_label():
    lesson = _mg("l", "phrase_build", 10.0, 12.0,
                 rows=[{"text": "Lesson:"}, {"text": "*ship* faster"}])
    assert "label_reads_as_dialogue" not in _codes(_edl(motion=[lesson]))


def test_a_requested_cta_in_the_tail_is_not_a_dead_tail():
    words = _words(FILLER)                      # last word ends at 30.18
    cta = _mg("cta", "save_cta", 30.5, 34.0)
    assert "payoff_hold" not in _codes(_edl(motion=[cta], dur=34.0),
                                       {"words": words})
    assert "payoff_hold" in _codes(_edl(dur=34.0), {"words": words})


def test_the_payoff_hold_fix_is_honest_when_the_source_ends():
    words = _words(FILLER)
    idx = {"words": words, "video": {"duration": 30.5}}
    note = _note(_edl(dur=30.5), "payoff_hold", idx)
    assert "source itself ends" in note["fix"] and "restore_range" not in note["fix"]


def test_a_jump_cut_under_a_spliced_insert_is_not_a_hook_jump_cut():
    edl = _edl(keep=[[0.0, 0.8], [1.5, 30.0]])
    edl["inserts"] = [{"id": "b1", "at_output_s": 0.8, "duration_s": 1.2,
                       "storage_key": "x.mp4"}]
    assert "hook_jump_cut" not in _codes(edl, {"words": _words(FILLER)})


# ── 8. advisory plumbing ─────────────────────────────────────────────────

def _thiel_like():
    words = _words("the 1960s technology meant computers but also rockets and "
                   "supersonic aviation and underwater cities and new medicines "
                   + " ".join(f"w{i}" for i in range(70))
                   + " they promised us flying cars and all we got was 140 "
                   "characters.", start=0.2)
    end = words[-1]["t1"] + 0.3
    motion = [_mg("hook", "word_slam", 0.0, 2.3, text="where did / *progress* go?",
                  kicker="Peter Thiel", role="grotesk"),
              _mg("list", "phrase_build", 2.0, 5.0, rows=[
                  {"text": "ROCKETS", "role": "condensed"},
                  {"text": "supersonic aviation", "role": "serif"},
                  {"text": "underwater cities", "role": "sans"},
                  {"text": "NEW *MEDICINES*", "role": "condensed"}]),
              _mg("bits", "typewriter", 12.0, 14.0, text="a. b. c.", font="mono"),
              _mg("num", "counter", end - 1.4, end, value="140", label="",
                  style="reveal")]
    return _edl(keep=[[0.0, end]], grade=None, motion=motion), {"words": words}


def test_one_non_blocking_verification_finding_carries_every_note():
    edl, index = _thiel_like()
    rows = [r for r in quality_verifier.deterministic_findings(edl, index)
            if r["code"] == "earn_its_place"]
    assert len(rows) == 1
    row = rows[0]
    assert row["severity"] == "advisory" and not quality_verifier.is_blocking(row)
    codes = [n["code"] for n in row["evidence"]["notes"]]
    for code in ("hook_generic_question", "transcript_list",
                 "payoff_number_without_noun", "payoff_hold",
                 "frame_uncommitted"):
        assert code in codes, code
    assert row["message"].startswith("EARN ITS PLACE (")
    assert "1) The hook 'where did / progress go?'" in row["message"]
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, index, preview={"edl_version": 2, "duration_s": 30,
                                       "caption_pages": ["p1"]})
    assert record["status"] == "passed", record["unresolved_findings"]
    assert any(r["code"] == "earn_its_place" for r in record["advisories"])


def test_the_summary_leads_with_the_top_notes_and_names_the_rest():
    edl, index = _thiel_like()
    notes = edit_review.review(edl, index)
    text = edit_review.summary(notes, limit=2)
    assert text.count(" Fix: ") == 2
    assert f"(+{len(notes) - 2} more: " in text
    assert edit_review.summary([]) == ""


def test_long_form_and_landscape_programs_are_not_reviewed():
    edl, index = _thiel_like()
    edl["frame"]["ratio"] = "16:9"
    assert edit_review.review(edl, index) == []
    assert edit_review.review(edl, index, force=True)
    long_vertical = _edl(dur=300.0, grade=None)
    assert edit_review.review(long_vertical, {}) == []


def test_a_request_for_the_thing_suppresses_its_note():
    edl, index = _thiel_like()
    asked = edit_review.review(edl, index, request_text="Open on a question hook")
    assert "hook_generic_question" not in [n["code"] for n in asked]
    # 'list' as a word, not inside 'listen'
    listen = edit_review.review(edl, index, request_text="listen to the clip")
    assert "transcript_list" in [n["code"] for n in listen]


def test_odd_data_never_raises():
    weird = _edl(motion=[{"id": "x", "template": "no_such_template",
                          "start": "soon", "end": None, "params": None},
                         "not a dict",
                         {"template": "word_slam", "start": 3, "end": 2,
                          "params": {"text": None}}],
                 texts=[{"text": "Lisa:", "start": 4.0, "end": 5.0}, {"text": ""}])
    weird["effects"]["zooms"] = [{"start": None}, "z"]
    weird["frame"]["follow"] = [{"k": [[1.0], "bad"]}]
    notes = edit_review.review(weird, {"words": [{"w": None}],
                                       "spatial": {"samples": [{"t": "x"}]}})
    assert isinstance(notes, list)
    assert edit_review.review({}, {}) == [] and edit_review.review(None) == []


def test_an_edl_without_graphics_or_words_draws_no_graphic_notes():
    notes = edit_review.review(_edl(), {})
    assert notes == []


# ── 9. the guidance says the same thing on every surface ─────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGIN = os.path.join(_ROOT, "plugins", "valmera", "skills",
                       "valmera-podcast-shorts")


def _flat(text):
    return " ".join(text.replace("–", "-").split()).lower()


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return _flat(fh.read())


def test_worker_skills_plugin_and_core_prompt_carry_the_same_rules():
    import agent_prompt
    worker = {name: _flat(agent_prompt.read_skill_text(name))
              for name in ("short-form-direction", "hooks-retention",
                           "motion-design", "review", "cutting")}
    plugin = {rel: _read(_PLUGIN, *rel.split("/"))
              for rel in ("SKILL.md", "references/looks.md",
                          "references/editing.md", "references/review.md",
                          "references/selection.md")}
    core = _flat(agent_prompt.CORE_PROMPT)
    sfd, looks = worker["short-form-direction"], plugin["references/looks.md"]
    for text in (sfd, looks, core, plugin["SKILL.md"]):
        assert "strongest line or statistic" in text
        assert "generic question" in text
        assert "earn" in text and "its place" in text
        assert "0.6-1.5 s" in text
    for text in (sfd, looks):
        assert "never restyle the transcript as a list" in text or \
            "a spoken list gets semantic visual inserts" in text
        assert "one big word per item" in text
        assert "at most 3 type roles" in text and "one accent" in text
        assert "50% of the runtime" in text
        assert "apple lisa, 1983" in text
        assert "taste call" in text
        assert "lower third" in text or "lower_third" in text
    assert "150-250 ms" in worker["cutting"]
    # the MCP workflow (what plugin and MCP editors read first) agrees
    mcp = _read(_ROOT, "backend", "routes", "mcp.py")
    assert "strongest line or statistic" in mcp and "generic question" in mcp
    assert "a word a later graphic slams" in mcp
    assert "holds 0.6-1.5 s after the last word" in mcp
    assert "hold the payoff 1.0-1.5 s" not in mcp
    assert "earn its place" in worker["review"]
    assert "earn its place" in plugin["references/review.md"]
    # the playbook examples no longer spend the hero word in the hook
    editing = plugin["references/editing.md"]
    assert 'hook_title "computers look like *garbage*"' not in editing
    assert '"why fonts were garbage"' not in editing
    assert "what is money actually worth" not in plugin["references/selection.md"]
    # the old "pose the question" hook rule is gone everywhere
    for text in list(worker.values()) + list(plugin.values()):
        assert "hook text poses the question" not in text
        assert "posed as the question or tension" not in text
