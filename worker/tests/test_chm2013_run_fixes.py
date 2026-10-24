"""CHM Revolutionaries 2013 run (w1022-chm2013-AHHwXUm3iIg, 22-23 Oct 2026):
the editing and review defects it hit, pinned.

1. A hidden negation. s09 (project 4505, EDL v31): the hook-tier slam
   'FAILURE IS *BAD*' owned the captions 0-2.18 s, so 'I don't think' was
   muted and the first caption after it read "it's good." for "I don't
   think it's good". audit_captions, the CAPTION CHECK and critique_short
   all passed it. caption_carry.hidden_negations names it; the CAPTION
   CHECK, audit_captions, the verification (negation_hidden, blocking) and
   the critic (with the op that ends the hook before the sentence) carry
   it. A scan of 353 production EDLs from 21-24 Oct found 15 more ('No one
   should be able to make a new kind of bioterror attack' shown as 'one
   should be able to make ...').
2. The critic told shorts they open mid-sentence where the cut opens on
   the speaker's restart ('I think | I think failure is bad' 4505, 'Three |
   three quite interesting' 4500).
3. A picture card that opened the programme moved off frame 0 when the
   head was extended (4508 v27 0.05 s, v29 0.34 s; 4505): full-frame
   orphan frames, re-set by hand each time.
4. cut_output_range on 4498 (v38 -> v39) cut the footage the cover headline
   opened on and the cover was removed; the band took frame 0.
5. A typeset '?' set after a *starred* word drew the star literally (job
   101975), and the open star starred every word after it.
6. set_motion_graphic refused replace_params inside params ('phrase_build
   has no parameter(s) ['replace_params']', job 104970).
7. audit_motion's unbound_device advisory fired on every hand-written
   grade window of a music-off short (grey_old, grey_95, mono_roulette...).
8. A headline band under the cover read EMPTY HEADLINE BAND while it
   yielded to the cover by design (jobs 101986, 102023, 102095).
9. Argument spellings refused before any work on 23 Oct:
   find_silences(min_duration), get_kept_transcript(start, end),
   add_text(id).

    cd worker && PYTHONPATH=.. python -m pytest tests/test_chm2013_run_fixes.py -q
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import caption_carry  # noqa: E402
import motion_critic  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import quality_verifier  # noqa: E402
import render_qc  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline, remap_program_items  # noqa: E402
from typeset import roles  # noqa: E402

from test_beat_coverage import _Ctx, probe  # noqa: E402,F401


# ── 1. a hidden negation ─────────────────────────────────────────────────

S09 = [("I", 0.0, 0.24), ("think", 0.24, 0.4), ("failure", 0.4, 0.72),
       ("is", 0.72, 0.96), ("bad.", 0.96, 1.44),
       ("I", 1.67, 1.83), ("don't", 1.83, 1.99), ("think", 1.99, 2.15),
       ("it's", 2.15, 2.39), ("good.", 2.39, 2.88),
       ("Only", 3.4, 3.6), ("start", 3.6, 3.9), ("a", 3.9, 3.98),
       ("company", 3.98, 4.4), ("if", 4.4, 4.5), ("you", 4.5, 4.6),
       ("feel", 4.6, 4.8), ("compelled.", 4.8, 5.5)]


def _index(words):
    return {"video": {"width": 1080, "height": 1920, "duration": 10.0},
            "words": [{"w": w, "t0": a, "t1": b} for w, a, b in words],
            "spatial": {"samples": [{"t": t, "faces": [[0.3, 0.12, 0.7, 0.3]]}
                                    for t in (0.5, 2.0, 4.0)]}}


def _hook(end=2.18, text="FAILURE IS *BAD*", **extra):
    params = motion_templates.check_params("word_slam", {
        "text": text, "tier": "hook", "kicker": "ELON MUSK ON FAILURE",
        "y": 0.58, "width": 0.78})
    return dict({"id": "hook", "template": "word_slam", "start": 0.0,
                 "end": end, "params": params}, **extra)


def _edl(motion=(), dur=7.0, mutes=()):
    edl = default_edl(dur)
    edl["keep"] = [[0.0, dur]]
    edl["captions"] = {"mode": "from_transcript", "design_version": 2,
                       "style": {"motion_look": "editorial"}}
    edl["motion"] = [dict(m) for m in motion]
    edl["caption_mutes"] = [list(m) for m in mutes]
    return validate_edl(edl, dur).model_dump()


def _negs(edl, words=S09):
    return caption_carry.hidden_negations(edl, _index(words),
                                          Timeline(edl["keep"]))


def test_the_s09_hook_hides_dont_and_the_caption_reads_its_good():
    edl = _edl([_hook()])
    got = _negs(edl)
    assert len(got) == 1, got
    g = got[0]
    assert g["negation"] == "don't" and g["owner"] == "hook"
    assert g["reads"] == "it's good"
    assert g["said"] == "I don't think it's good"
    assert g["fix"].startswith("end 'hook' at 1.65s, before 'I'")
    # it leads heard_unshown (the CAPTION CHECK), which the hook zone
    # otherwise keeps empty: what is said under a hook is its moment
    unshown = caption_carry.heard_unshown(edl, _index(S09), Timeline(edl["keep"]))
    assert unshown and unshown[0].get("negation_hidden")
    line = render_qc.unshown_line({"unshown": unshown})
    assert line.startswith("NEGATION HIDDEN (blocking: faithful claims)"), line
    assert "it's good" in line and "'don't'" in line


def test_the_verification_blocks_it_and_the_critic_ends_the_hook():
    edl = _edl([_hook()])
    rows = [f for f in quality_verifier.deterministic_findings(edl, _index(S09))
            if f["code"] == "negation_hidden"]
    assert len(rows) == 1 and quality_verifier.is_blocking(rows[0])
    crit = [f for f in motion_critic.lens_words(edl, _index(S09), {"words": []}, 7.0)
            if f["code"] == "negation_hidden"]
    assert crit and crit[0]["severity"] == "blocking"
    assert crit[0]["op"] == {"action": "upsert", "layer": "motion", "id": "hook",
                             "value": {"id": "hook", "end": 1.65}}


def test_the_fixed_hook_and_a_captioned_negation_are_clean():
    # the editor's fix (hook end 1.5): the sentence captions whole
    assert _negs(_edl([_hook(end=1.5)])) == []
    # no hook: 'don't' is captioned with its sentence
    assert _negs(_edl([])) == []
    # the hook keeps the captions running beside it
    assert _negs(_edl([_hook(mute_captions=False)])) == []


def test_a_caption_mute_over_the_negation_is_named_with_its_window():
    got = _negs(_edl([], mutes=[(1.6, 2.1)]))
    assert len(got) == 1 and "set_caption_mutes window 1.6-2.1s" in got[0]["cause"]
    assert "before 1.67s" in got[0]["fix"]


def test_fragments_and_answers_are_not_flipped_claims():
    # 'No,' answering a question: the claim after it stands
    words = [("No,", 0.2, 0.5), ("I", 0.6, 0.7), ("think", 0.7, 0.9),
             ("it's", 0.9, 1.1), ("bad.", 1.1, 1.5), ("And", 2.6, 2.8),
             ("that", 2.8, 3.0), ("matters.", 3.0, 3.6)]
    assert _negs(_edl([_hook(end=0.55, text="*BAD*")]), words) == []
    # the negation's reach: a shown run far down a long sentence is a
    # fragment ('... on the Internet' after 'nobody was making any money')
    words = [("nobody", 0.1, 0.4), ("was", 0.4, 0.5), ("making", 0.5, 0.8),
             ("any", 0.8, 0.9), ("money", 0.9, 1.2), ("on", 2.3, 2.4),
             ("the", 2.4, 2.5), ("Internet.", 2.5, 3.0)]
    assert _negs(_edl([_hook(end=2.2, text="*1995*")]), words) == []
    # a lone word at the hook's edge is not a claim
    words = [("I", 0.1, 0.2), ("don't", 0.2, 0.5), ("get", 0.5, 0.7),
             ("sleep.", 2.3, 2.8)]
    assert _negs(_edl([_hook(end=2.2, text="*TIRED*")]), words) == []


def test_no_one_shown_as_one_should_be_able_is_caught():
    # production scan, project 4587 v31: 'No one should be able to make a
    # new kind of bioterror attack' read 'one should be able to make ...'
    words = [("No", 1.9, 2.05), ("one", 2.05, 2.3), ("should", 2.3, 2.5),
             ("be", 2.5, 2.6), ("able", 2.6, 2.8), ("to", 2.8, 2.9),
             ("make", 2.9, 3.1), ("attacks.", 3.1, 3.7)]
    got = _negs(_edl([_hook(end=2.0, text="*BIOTERROR*")]), words)
    assert len(got) == 1 and got[0]["negation"] == "No"
    assert got[0]["reads"].startswith("one should be able")


# ── 2. a restart is where the sentence starts ────────────────────────────

def _w(*rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


def test_a_restart_is_no_mid_sentence_opening():
    before = _w(("I", 2271.66, 2271.74), ("think", 2271.98, 2272.22))
    after = _w(("I", 2272.275, 2272.515), ("think", 2272.515, 2272.675),
               ("failure", 2272.675, 2272.995))
    assert motion_critic._restarts(before, after)
    assert motion_critic._restarts(_w(("Three", 2011.9, 2012.22)),
                                   _w(("three", 2012.22, 2012.62),
                                      ("quite", 2012.62, 2012.86)))
    # 'it's kind of | fun' is mid-sentence
    assert not motion_critic._restarts(_w(("kind", 0, 1), ("of", 1, 2)),
                                       _w(("fun,", 2, 3)))


def test_the_critic_skips_the_restart_but_not_a_real_fragment():
    words = [("think", 9.7, 9.95), ("I", 10.0, 10.1), ("think", 10.1, 10.3),
             ("failure", 10.3, 10.6), ("is", 10.6, 10.8), ("bad.", 10.8, 11.2)]
    ix = _index(words)
    ix["words"].insert(0, {"w": "I", "t0": 9.6, "t1": 9.7})
    edl = _edl([], dur=20.0)
    edl["keep"] = [[10.0, 11.5]]
    got = motion_critic.lens_words(edl, ix, {"words": []}, 1.5)
    assert not [f for f in got if f["code"] == "opens_mid_sentence"], got
    edl["keep"] = [[10.3, 11.5]]           # opens on 'failure'
    got = motion_critic.lens_words(edl, ix, {"words": []}, 1.2)
    assert [f for f in got if f["code"] == "opens_mid_sentence"]


# ── 3-4. what opened the programme still opens it ────────────────────────

def _remap(edl, new_keep):
    old = Timeline(edl["keep"])
    e = copy.deepcopy(edl)
    e["keep"] = [list(k) for k in new_keep]
    notes = remap_program_items(e, old, Timeline(e["keep"]))
    return e, notes


def test_an_opening_picture_card_keeps_frame_0_when_the_head_grows():
    edl = {"keep": [[3652.29, 3655.26], [3657.48, 3680.0]], "motion": [],
           "effects": {"picture_cards": [
               {"id": "card1", "start": 0.0, "end": 25.49,
                "source": {"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.6},
                "box": [0.0, 0.24, 1.0, 0.794]}]}}
    for head in (3652.24, 3651.9):          # 4508 v27 (+0.05 s), v29 (+0.39 s)
        e, _ = _remap(edl, [[head, 3655.26], [3657.48, 3680.0]])
        card = e["effects"]["picture_cards"][0]
        assert card["start"] == 0.0, (head, card)
        assert card["end"] == round(25.49 + 3652.29 - head, 2)
    # a card that did not open the programme still follows its footage
    edl["effects"]["picture_cards"][0]["start"] = 2.0
    e, _ = _remap(edl, [[3651.9, 3655.26], [3657.48, 3680.0]])
    assert e["effects"]["picture_cards"][0]["start"] == 2.39


def test_a_cut_head_keeps_the_cover_and_the_band_starts_where_it_ends():
    cover = {"id": "cover", "template": "cover_headline", "start": 0.0,
             "end": 2.6, "params": motion_templates.check_params(
                 "cover_headline", {"text": "Why Elon smokes *nothing*"})}
    band = {"id": "band", "template": "headline", "start": 2.6, "end": 37.82,
            "params": motion_templates.check_params(
                "headline", {"text": "Musk on *risk*", "y": 0.18})}
    slam = {"id": "age75", "template": "word_slam", "start": 22.23, "end": 23.9,
            "params": motion_templates.check_params(
                "word_slam", {"text": "*75*"})}
    keep = [[4521.44, 4530.88], [4539.9, 4545.66], [4559.01, 4590.0]]
    edl = {"keep": keep, "motion": [cover, band, slam], "effects": {}}
    e, notes = _remap(edl, keep[1:])         # 4498 v38 -> v39
    got = {m["id"]: (m["start"], m["end"]) for m in e["motion"]}
    assert got["cover"] == (0.0, 2.6), notes
    assert got["band"][0] == 2.6 and got["band"][1] == round(37.82 - 9.44, 2)
    assert got["age75"] == (round(22.23 - 9.44, 2), round(23.9 - 9.44, 2))
    assert any("keeps the opening" in n for n in notes), notes
    # a hook slam whose words were cut is still removed
    hook = {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.6,
            "params": motion_templates.check_params(
                "word_slam", {"text": "*RISK*", "tier": "hook"})}
    e, _ = _remap(dict(edl, motion=[hook]), keep[1:])
    assert e["motion"] == []


# ── 5. typeset punctuation outside a star pair ───────────────────────────

def test_punctuation_after_a_starred_word_is_printed_without_the_star():
    assert roles.tokens("is it *good*?") == [("is", False), ("it", False),
                                             ("good?", True)]
    assert roles.tokens("say *no*? then go") == [
        ("say", False), ("no?", True), ("then", False), ("go", False)]
    assert roles.tokens('"*no*" he said')[0] == ('"no"', True)
    assert roles.tokens("*two words* end") == [("two", True), ("words", True),
                                               ("end", False)]


# ── 6. replace_params inside set_motion_graphic's params ─────────────────

def test_set_motion_graphic_reads_replace_params(probe):
    ctx = _Ctx("we have a list of things to say here and more " * 4, t0=0.5)
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 2.0, 3.5, id="w1",
        params={"text": "*FIRST*", "kicker": "THE", "y": 0.6})
    assert out.startswith("EDL v"), out
    out = motion_tools.set_motion_graphic(
        ctx, "w1", params={"text": "*SECOND*", "replace_params": True})
    assert out.startswith("EDL v"), out
    p = ctx.item("w1")["params"]
    assert p["text"] == "*SECOND*" and not p.get("kicker")
    assert "replace_params" not in p
    out = motion_tools.set_motion_graphic(
        ctx, "w1", params={"kicker": "A", "replace_params": False})
    assert out.startswith("EDL v"), out
    assert ctx.item("w1")["params"]["text"] == "*SECOND*"


# ── 7. unbound_device only where a song swap could re-time it ────────────

def test_a_hand_written_grade_window_is_no_unbound_device_without_music():
    import motion_qa
    grey = {"id": "grey_old", "type": "grade_window", "stage": "screen",
            "start": 1.0, "end": 4.0, "look": "mono"}
    edl = {"keep": [[0.0, 9.0]], "fx": [grey]}
    ix = {"words": [], "video": {"duration": 9.0}}
    codes = [f["code"] for f in motion_qa.audit(edl, ix)["findings"]]
    assert "unbound_device" not in codes
    edl["music"] = [{"id": "m1", "asset_key": "music/1/a.mp3", "start": 0.0,
                     "end": 9.0}]
    codes = [f["code"] for f in motion_qa.audit(edl, ix)["findings"]]
    assert "unbound_device" in codes


# ── 8. a band yielding to the cover is not an empty band ─────────────────

def test_the_band_yielding_to_the_cover_is_no_empty_headline_band():
    fps = 30.0
    # band 0-6 s under a cover 0-2.6 s: blank 0.23-1.17 and 2.20-2.47 s
    # (job 101986's shape), ink otherwise, and a real hole at 4.0-4.4 s
    def share(t):
        if 0.23 <= t < 1.17 or 2.20 <= t < 2.47 or 4.0 <= t < 4.4:
            return 0.0
        return 0.2
    ink = [(round(i / fps, 3), share(i / fps)) for i in range(int(6 * fps))]
    assert len(render_qc.band_gaps(ink, fps)) == 3
    got = render_qc.band_gaps(ink, fps, yields=[[0.0, 2.6]])
    assert got == [(4.0, 4.4)], got
    # the plan hands each band its yield windows (motion_layer.yield_windows)
    import motion_layer
    band = {"id": "band", "template": "headline", "start": 0.0, "end": 6.0,
            "params": motion_templates.check_params(
                "headline", {"text": "Musk on *risk*", "y": 0.18})}
    cover = {"id": "cover", "template": "cover_headline", "start": 0.0,
             "end": 2.6, "params": motion_templates.check_params(
                 "cover_headline", {"text": "Why Elon smokes *nothing*",
                                    "y": 0.55})}
    assert motion_layer.yield_windows(band, [band, cover], 1080, 1920) == \
        [[0.0, 2.6]]


# ── 9. argument spellings refused on 23 Oct ──────────────────────────────

def test_argument_spellings_refused_on_23_oct_are_read():
    import agent_tools
    norm = agent_tools._normalize_tool_call
    # find_silences(min_duration=0.3) (jobs 99193, 99442)
    assert norm("find_silences", {"start": 974.25, "end": 996.59,
                                  "min_duration": 0.3})[1] == {
        "start": 974.25, "end": 996.59, "min_seconds": 0.3}
    # get_kept_transcript(start, end) (jobs 100640, 101653): the whole one
    _n, args, notes = norm("get_kept_transcript", {"start": "2.8", "end": "8.2"})
    assert args == {} and "whole kept transcript" in notes[0]
    # add_text(id=...) (job 99946)
    _n, args, notes = norm("add_text", {"id": "tx_name", "text": "ELON MUSK",
                                        "start": 2.4, "end": 9})
    assert "id" not in args and args["text"] == "ELON MUSK"
