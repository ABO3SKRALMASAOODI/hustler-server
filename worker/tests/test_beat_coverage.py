"""Beat coverage and showable moments (round 5 judging, Oct 2026).

Round 6 removed the graphics that only re-typeset the captions and left dead
stretches exactly where the argument lives: Thiel 14.5-25.0 s over "a narrow
cone of progress around the world of bits... computers, internet, mobile",
Jobs 22.5-37.2 s over "injecting some liberal arts into these computers" and
"Let's get proportionally spaced fonts... multiple fonts... graphics". Pinned
here, one block per rule:

  1. spoken_beats finds what a short's words offer to SHOW: lists (noun
     phrases, not lone modifiers), triads, numbers and spoken ranges, names
     and claims.
  2. BEAT COVERAGE (edit_review): a body stretch past ~8 s (~6 s when it
     holds a list, triad, name or number) with no designed beat is named
     with its line and a beat that adds information — never a zoom or a
     sound; the budget's other half.
  3. SHOWABLE MOMENTS: a concrete list or a name set as type is pointed at
     real imagery first; a list as a run of 4+ replacing slams, an item
     without its noun and a range shown as one of its ends are named.
  4. HOOK TIER: a display hook no bigger than the captions, or one with a
     live caption under it, is named; word_slam tier='hook' is a headline
     that owns its zone (the captions wait until it exits).
  5. PAYOFF: 0.8-1.5 s of air, a payoff number ~2 s on screen.
  6. The COUNTER really counts (0 -> value, ~0.4 s, entering with its roll)
     and lands a spoken range figure by figure; the write sets it.
  7. The ACCUMULATING LIST (list_build): items land whole on their onsets,
     persist, the newest accented; the engine mirrors its timing.
  8. The beat planner ranks lists, triads, ranges and names and reports
     dead stretches.

Browser checks read the laid-out DOM (relative comparisons, no pixel or
font-metric margins), so they hold on macOS and on the Linux CI fonts alike.
"""

import asyncio
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import captions as caplib  # noqa: E402
import edit_review  # noqa: E402
import keepout  # noqa: E402
import motion_engine  # noqa: E402
import motion_planner  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import number_reveal  # noqa: E402
import spoken_beats  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


def _words(text, start=0.0, step=0.3):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "t0": round(t, 3), "t1": round(t + step - 0.02, 3)})
        t += step
    return out


def _at(words, token, nth=0):
    hits = [w for w in words if w["w"].strip(",.").lower() == token.strip(",.").lower()]
    return hits[nth]["t0"]


THIEL = ("1960s technology meant computers but also rockets and supersonic aviation and "
         "the Green Revolution agriculture and underwater cities and new medicines.")
JOBS = ("But we're solving the problems of injecting some liberal arts into these computers. "
        "Let's get proportionally spaced fonts in there. Let's get multiple fonts in there. "
        "Let's get graphics in there.")


# ── 1. spoken_beats ──────────────────────────────────────────────────────

def test_a_spoken_list_is_its_noun_phrases_not_its_first_words():
    lists = spoken_beats.lists(_words(THIEL))
    assert len(lists) == 1
    items = [i["text"] for i in lists[0]["items"]]
    # 'computers' is the lead of a longer clause ('meant computers but also')
    assert items[-4:] == ["supersonic aviation", "Green Revolution agriculture",
                          "underwater cities", "new medicines"]
    assert "rockets" in items
    # a repeated item is one item; a figure belongs to a stat run, not a list
    rep = spoken_beats.lists(_words("You know computers, internet, mobile, internet it's "
                                    "generated great companies."))
    assert [i["text"] for i in rep[0]["items"]] == ["computers", "internet", "mobile"]
    assert spoken_beats.lists(_words("We had 30 fonts, 40 fonts, 50 fonts.")) == []


def test_an_anaphora_is_a_triad_with_its_opener():
    tri = spoken_beats.triads(_words(JOBS))
    assert len(tri) == 1 and tri[0]["opener"] == "Let's get"
    assert [i["text"] for i in tri[0]["items"]] == [
        "proportionally spaced fonts", "multiple fonts", "graphics"]


def test_a_spoken_range_is_one_number_and_small_spelled_numbers_are_not_stats():
    nums = spoken_beats.numbers(_words("We have 30, 40 fonts on the screen."))
    assert [(n["kind"], n["values"]) for n in nums] == [("range", (30.0, 40.0))]
    assert spoken_beats.numbers(_words("no student three or four years from now")) == []
    one = spoken_beats.numbers(_words("all we got was 140 characters."))
    assert [(n["kind"], n["values"]) for n in one] == [("number", (140.0,))]


def test_names_and_claims():
    names = [n["text"] for n in spoken_beats.names(_words(
        "And the LISA is the first one. We built it in Cupertino."))]
    assert names == ["LISA", "Cupertino"]           # 'And' / 'We' open sentences
    claims = spoken_beats.claims(_words(
        "I think that's kind of a tell that we have a narrow cone of progress. Okay."))
    assert len(claims) == 1 and claims[0]["text"].startswith("I think")


def test_talk_is_not_a_list_and_generic_capitals_are_not_names():
    """Review, Oct 2026: on three hour-long transcripts half the 'lists' were
    talk — the planner pre-fills a list_build's rows with them."""
    for junk in ("Have a great day, and back to you, Sam.",
                 "We got no gravity. You know, Thor, Ragnarok? Do you guys cover that?",
                 "I can also tag in Dottie itself and say, hey, can we turn this around?",
                 "Just turn the camera to the room, to the audience, and I'll say, put it up.",
                 "It was costing them money, a customer comes in, peruses them and picks one.",
                 "Then at the end of the day, Thibault, Tejal, and I will be back.",
                 "The first principles don't change, you know, in the final analysis, Mead and "
                 "Conway is still a good book.",
                 "A governor... The former governor of California, governor Brown, started this."):
        assert spoken_beats.lists(_words(junk)) == [], junk
    keep = {
        "We're launching ultrafast across the API, ChatGPT, and Codex. And we think so.":
            ["API", "ChatGPT", "Codex"],
        "Models are great for math, coding, and computer use, and all of these.":
            ["math", "coding", "computer use"],
        "So much information in data banks, in congressional budgets, in testimony, in "
        "books, journal articles.": ["in congressional budgets", "in testimony", "in books"],
    }
    for text, items in keep.items():
        got = spoken_beats.lists(_words(text))
        assert got and [i["text"] for i in got[0]["items"]][:3] == items, (text, got)
    # a member turning on a preposition is not a triad item; 'thank you' is no anaphora
    tri = spoken_beats.triads(_words(JOBS + " Let's get to the point."))
    assert [i["text"] for i in tri[0]["items"]] == [
        "proportionally spaced fonts", "multiple fonts", "graphics"]
    assert spoken_beats.triads(_words("Thank you all very much. Thank you for coming here. "
                                      "Thank you for watching online.")) == []
    # a run of three figures is no range ('6, 7, 800 dots per inch' = 600-800)
    assert all(n["kind"] == "number" for n in spoken_beats.numbers(
        _words("We want to go to 6, 7, 800 dots per inch on a printer.")))
    names = [n["text"] for n in spoken_beats.names(_words(
        "We use AI on NVIDIA GPUs every Monday. The Chinese market and Apple, right? "
        "Okay, the CPU ships in January."))]
    assert names == ["NVIDIA GPUs", "Apple"], names


# ── 2. beat coverage ─────────────────────────────────────────────────────

def _edl(motion=(), dur=30.0, **extra):
    edl = {"keep": [[0.0, dur]], "inserts": [], "speed": [], "texts": [],
           "motion": list(motion), "sfx": [], "music": [], "overlays": [],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5},
           "effects": {"grade": "warm", "zooms": []},
           "captions": {"mode": "from_transcript",
                        "style": {"motion_look": "editorial", "highlight_color": "#FFC940"}}}
    edl.update(extra)
    return edl


def _mg(id_, template, start, end, **params):
    params.setdefault("accent", "#FFC940")
    return {"id": id_, "template": template, "start": start, "end": end, "params": params}


def _notes(edl, words, **kw):
    return edit_review.review(edl, {"words": words}, **kw)


def _note(edl, words, code):
    return next((n for n in _notes(edl, words) if n["code"] == code), None)


# A 30 s talk: a hook, filler, then the thesis and a spoken list between
# 10 and 20 s, filler to the payoff.
BODY = ("We waited. " * 15 + "I think the world of bits is a narrow cone of progress. "
        "You know computers, internet, mobile, it's generated some great companies. "
        + "and then " * 14 + "all we got was 140 characters.")


def _body_words():
    return _words(BODY, start=0.2)


def _framing(*extra):
    hook = _mg("hook", "word_slam", 0.0, 2.0, text="They promised us / *flying cars*",
               tier="hook")
    early = _mg("early", "word_slam", 5.0, 6.0, text="*bits*")
    payoff = _mg("payoff", "word_slam", 27.5, 30.0, text="*140* / characters", tier="payoff")
    return [hook, early, *extra, payoff]


def test_a_dead_stretch_over_the_argument_is_named_with_a_beat_that_adds_information():
    words = _body_words()
    note = _note(_edl(_framing()), words, "dead_stretch")
    assert note is not None and note["rank"] == 1
    a, b = note["evidence"]["gaps"][0]
    assert a == pytest.approx(6.0) and b == pytest.approx(27.5)
    msg, fix = note["message"], note["fix"]
    assert "narrow cone of progress" in msg and "computers, internet, mobile" in msg
    assert "list_build" in fix and "COMPUTERS / INTERNET / MOBILE" in fix
    assert "research_broll" in fix and "6-8 s" in fix
    # never a zoom or a sound as the fix
    assert "a zoom or a sound is not a beat" in fix
    for verb in ("add_zoom", "add_sfx", "punch", "whoosh"):
        assert verb not in fix


def test_a_zoom_or_a_sound_does_not_fill_a_dead_stretch_but_a_beat_or_an_image_does():
    words = _body_words()
    zoomed = _edl(_framing(), effects={"grade": "warm", "zooms": [
        {"start": 10.0, "end": 20.0, "strength": 0.15, "mode": "push_in"}]},
        sfx=[{"id": "s", "at": 15.0, "storage_key": "sound:impact_1"}])
    assert _note(zoomed, words, "dead_stretch") is not None
    lst = _mg("list", "list_build", _at(words, "computers,"), 18.0,
              rows=[{"text": "computers"}, {"text": "internet"}, {"text": "mobile"}])
    thesis = _mg("vs", "versus_split", 10.0, 13.0, left="BITS", right="ATOMS")
    covered = _edl(_framing(thesis, lst, _mg("x", "word_slam", 21.0, 22.0, text="*generated*")))
    assert _note(covered, words, "dead_stretch") is None
    # a cover image counts as a beat (real imagery)
    imaged = _edl(_framing(thesis, _mg("x", "word_slam", 21.0, 22.0, text="*generated*")),
                  overlays=[{"id": "o1", "start": 13.5, "duration_s": 5.0, "asset_key": "a"}])
    assert _note(imaged, words, "dead_stretch") is None
    # ...and so does a renderer-native vector graphic (an arrow on the evidence)
    vectored = _edl(_framing(thesis, _mg("x", "word_slam", 21.0, 22.0, text="*generated*")),
                    vectors=[{"id": "v1", "kind": "arrow", "start": 13.5, "end": 18.5}])
    assert _note(vectored, words, "dead_stretch") is None


def test_a_short_quiet_stretch_without_argument_is_a_choice_not_a_gap():
    words = _words("and then " * 60, start=0.2)
    beats = [_mg(f"b{i}", "word_slam", t, t + 1.0, text="*then*") for i, t in
             enumerate((7.5, 14.5, 21.5))]
    edl = _edl(_framing(*beats))
    # every gap is under 8 s and holds no list, triad, name or number
    assert _note(edl, words, "dead_stretch") is None
    # the same 7 s gap holding a spoken list is too long
    listy = _words("and then " * 30 + "rockets, cities, medicines and more. " + "and then " * 25,
                   start=0.2)
    gap = _edl(_framing(_mg("b", "word_slam", 10.5, 11.0, text="*then*"),
                        _mg("c", "word_slam", 18.2, 19.0, text="*more*")))
    note = _note(gap, listy, "dead_stretch")
    assert note is not None and note["evidence"]["gaps"][0][1] - note["evidence"]["gaps"][0][0] > 6.0


def test_a_plain_clip_the_user_only_captioned_is_not_held_to_beat_coverage():
    assert _note(_edl([]), _body_words(), "dead_stretch") is None
    # and the user's own 'minimal' ask suppresses it
    notes = edit_review.review(_edl(_framing()), {"words": _body_words()},
                               request_text="keep it minimal, captions only")
    assert "dead_stretch" not in [n["code"] for n in notes]


# ── 3. showable moments ──────────────────────────────────────────────────

def _thiel_slams(words, texts=("rockets", "supersonic", "agriculture", "underwater", "medicines")):
    keys = ("rockets", "supersonic", "agriculture", "underwater", "medicines.")
    out = []
    for i, (txt, key) in enumerate(zip(texts, keys)):
        t = _at(words, key)
        nxt = _at(words, keys[i + 1]) if i + 1 < len(keys) else t + 0.8
        out.append(_mg(f"item{i}", "word_slam", t - 0.02, nxt - 0.02, text=txt, role="condensed"))
    return out


def test_a_list_set_as_replacing_slams_points_at_images_and_one_accumulating_list():
    words = _words(THIEL + " " + "and then " * 40, start=0.3)
    edl = _edl([_mg("hook", "word_slam", 0.0, 0.5, text="*progress*", tier="hook")]
               + _thiel_slams(words))
    codes = {n["code"]: n for n in _notes(edl, words)}
    show = codes["showable_moment"]
    assert show["rank"] == 2 and "research_broll" in show["fix"] and "0.3-0.6 s" in show["fix"]
    assert "list_build" in show["fix"]
    run = codes["list_as_slams"]
    assert run["evidence"]["ids"] == ["item0", "item1", "item2", "item3", "item4"]
    assert "list_build" in run["fix"]
    lone = codes["list_item_without_noun"]
    assert "UNDERWATER CITIES" in lone["fix"] and "SUPERSONIC AVIATION" in lone["fix"]
    # three slams with their nouns are within the guidance
    three = _edl(_thiel_slams(words, ("rockets", "supersonic aviation", "agriculture"))[:3])
    codes3 = [n["code"] for n in _notes(three, words)]
    assert "list_as_slams" not in codes3


def test_a_list_shown_as_real_images_or_one_list_build_of_noun_phrases_is_fine():
    words = _words(THIEL + " " + "and then " * 40, start=0.3)
    t0 = _at(words, "rockets")
    imaged = _edl([], overlays=[{"id": f"o{i}", "start": t0 + i * 0.6, "duration_s": 0.5,
                                 "asset_key": "a"} for i in range(8)])
    imaged["motion"] = [_mg("x", "word_slam", 20.0, 21.0, text="*then*")]
    assert "showable_moment" not in [n["code"] for n in _notes(imaged, words)]
    lb = _mg("list", "list_build", t0, _at(words, "medicines.") + 1.0, rows=[
        {"text": "rockets"}, {"text": "supersonic aviation"},
        {"text": "Green Revolution agriculture"}, {"text": "underwater cities"},
        {"text": "new medicines"}])
    notes = {n["code"]: n for n in _notes(_edl([dict(lb, params=dict(lb["params"],
                                                                     lead="They promised"))]),
                                          words)}
    for code in ("list_as_slams", "list_item_without_noun", "transcript_list",
                 "restates_captions"):
        assert code not in notes, code
    # the accumulating list is the logged fallback: images first is a suggestion
    assert notes.get("showable_moment", {"rank": 3})["rank"] == 3


def test_a_range_shown_as_one_of_its_ends_is_named():
    words = _words("and then " * 10 + "We have 30, 40 fonts on the screen. " + "and then " * 30,
                   start=0.2)
    t40 = _at(words, "40")
    wrong = _edl([_mg("fonts", "counter", t40 - 0.4, t40 + 1.6, value="40",
                      label="fonts on the screen")])
    note = _note(wrong, words, "number_not_as_said")
    assert note is not None and "30–40" in note["fix"]
    right = _edl([_mg("fonts", "counter", t40 - 0.8, t40 + 1.6, value="30–40",
                      label="fonts on the screen")])
    assert _note(right, words, "number_not_as_said") is None


# ── 4. the hook tier ─────────────────────────────────────────────────────

def test_a_small_hook_with_a_live_caption_under_it_is_named_and_the_hook_tier_is_not():
    words = _words("1960s technology meant computers but also rockets " + "and then " * 40)
    small = _edl([_mg("hook", "word_slam", 0.0, 2.0, text="They promised us / *flying cars*…",
                      kicker="Peter Thiel", width=0.807)])
    note = _note(small, words, "hook_small")
    assert note is not None and note["evidence"]["main_fs"] < 0.05
    assert "1960s technology" in note["message"] and "tier='hook'" in note["fix"]
    big = _edl([_mg("hook", "word_slam", 0.0, 2.0, text="*progress*", kicker="Peter Thiel")])
    shared = _note(big, words, "hook_shares_zone")
    assert shared is not None and "tier='hook'" in shared["fix"]
    tiered = _edl([_mg("hook", "word_slam", 0.0, 2.0, text="They promised us / *flying cars*…",
                       kicker="Peter Thiel", tier="hook")])
    codes = [n["code"] for n in _notes(tiered, words)]
    assert "hook_small" not in codes and "hook_shares_zone" not in codes
    # a hook tier opted out of its zone has the live caption under it again
    opted = _edl([dict(_mg("hook", "word_slam", 0.0, 2.0, text="They promised us / *flying cars*…",
                           tier="hook"), mute_captions=False)])
    shared = _note(opted, words, "hook_shares_zone")
    assert shared is not None and "1960s technology" in shared["message"]


def test_the_hook_tier_owns_its_zone_the_captions_wait_until_it_exits():
    hook = _mg("hook", "word_slam", 0.0, 2.04, text="They promised us / *flying cars*", tier="hook")
    edl = _edl([hook], caption_mutes=[[10.0, 11.0]])
    assert caplib.effective_caption_mutes(edl) == [[0.0, 2.04], [10.0, 11.0]]
    # the editor can keep the captions running beside it
    beside = _edl([dict(hook, mute_captions=False)])
    assert caplib.effective_caption_mutes(beside) == []
    # a display slam never mutes a whole window, and a 'hook' later than the
    # opening owns nothing
    assert caplib.effective_caption_mutes(_edl([dict(hook, params=dict(
        hook["params"], tier="display"))])) == []
    assert caplib.effective_caption_mutes(_edl([dict(hook, start=6.0, end=8.0)])) == []


def test_a_word_mostly_said_after_the_hook_appears_as_it_exits():
    words = [{"w": "1960s", "t0": 0.2, "t1": 1.2}, {"w": "technology", "t0": 1.22, "t1": 1.7},
             {"w": "meant", "t0": 1.7, "t1": 1.92}, {"w": "computers", "t0": 1.92, "t1": 2.36},
             {"w": "but", "t0": 2.36, "t1": 2.64}, {"w": "also", "t0": 2.64, "t1": 2.9}]
    words += _words("and then " * 20, start=3.0)
    hook = _mg("hook", "word_slam", 0.0, 2.04, text="They promised us / *flying cars*", tier="hook")
    edl = _edl([hook])
    tl = Timeline(edl["keep"])
    shown = caplib.caption_words(edl, {"words": words}, tl)
    # 'meant' (mid 1.81) belongs to the hook's window; 'computers' (mid
    # 2.14) is captioned from the frame the hook leaves, never under it
    assert [w["w"] for w in shown[:3]] == ["computers", "but", "also"]
    assert shown[0]["t0"] == pytest.approx(2.04)


def test_words_under_the_hook_tier_are_not_a_sound_off_gap():
    words = _words("1960s technology meant computers but also rockets " + "and then " * 40)
    hook = _mg("hook", "word_slam", 0.0, 2.04, text="They promised us / *flying cars*", tier="hook")
    edl = _edl([hook])
    tl = Timeline(edl["keep"])
    gaps = caption_carry.sound_off_gaps(edl, {"words": words}, tl, min_gap=0.0)
    assert not [g for g in gaps if g["start"] < 2.0], gaps
    # the same words under a manual mute window are reported
    muted = _edl([], caption_mutes=[[0.0, 2.04]])
    gaps = caption_carry.sound_off_gaps(muted, {"words": words}, tl, min_gap=0.0)
    assert [g for g in gaps if g["start"] < 2.0]


# ── 5. payoff ─────────────────────────────────────────────────────────────

def test_a_payoff_number_needs_about_two_seconds_and_the_line_point_eight():
    words = _words("and then " * 45 + "all we got was 140 characters.", start=0.2)
    end_word = words[-1]["t1"]
    dur = end_word + 0.5
    pay = _mg("payoff", "word_slam", dur - 1.4, dur, text="*140* / characters", tier="payoff")
    edl = _edl([_mg("x", "word_slam", 6.0, 7.0, text="*then*"), pay], dur=dur)
    edl["keep"] = [[0.0, dur]]
    codes = {n["code"]: n for n in _notes(edl, words)}
    assert codes["payoff_number_hold"]["evidence"]["held_s"] == pytest.approx(1.4, abs=0.01)
    assert "0.8-1.5 s" in codes["payoff_hold"]["fix"] or "0.8" in codes["payoff_hold"]["fix"]
    held = _edl([_mg("x", "word_slam", 6.0, 7.0, text="*then*"),
                 dict(pay, start=dur - 2.1)], dur=dur)
    held["keep"] = [[0.0, dur]]
    assert "payoff_number_hold" not in [n["code"] for n in _notes(held, words)]
    assert edit_review.PAYOFF_HOLD_MIN_S == 0.8 and edit_review.PAYOFF_NUMBER_HOLD_S == 2.0


# ── 6. the counter's landing, written from the transcript ────────────────

class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, text, t0=10.0, duration=40.0):
        self.duration = duration
        edl = default_edl(duration)
        edl["keep"] = [[0.0, duration]]
        self.index = {"video": {"width": 1080, "height": 1920, "duration": duration},
                      "words": _words(text, t0, 0.25)}
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"

    def item(self, mid):
        return next(m for m in self._edl["motion"] if m["id"] == mid)


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.2, .1, .8, .25]], "ink": [[.2, .1, .8, .25]] * 4})


def test_a_spoken_range_lands_each_figure_on_its_own_word(probe):
    ctx = _Ctx("We have 30, 40 fonts on the screen. And then nothing.")
    w = ctx.index["words"]
    t30, t40 = _at(w, "30,"), _at(w, "40")
    out = motion_tools.add_motion_graphic(ctx, "counter", t30 - 1.5, t40 + 1.6, id="fonts",
                                          params={"value": "30–40", "label": "fonts on the screen"})
    assert "NUMBER LANDED" in out and "range" in out, out
    it = ctx.item("fonts")
    s = it["start"]
    assert s == pytest.approx(t30 - number_reveal.LEAD_S - number_reveal.ROLL_S, abs=0.002)
    assert s + it["params"]["land_first"] == pytest.approx(t30 - number_reveal.LEAD_S, abs=0.002)
    assert s + it["params"]["land"] == pytest.approx(t40 - number_reveal.LEAD_S, abs=0.002)
    # re-saving it changes nothing
    again = motion_tools.set_motion_graphic(ctx, "fonts", params={"label": "fonts on the screen"})
    assert "NUMBER LANDED" not in again
    assert number_reveal.range_values("30 to 40") == ({30.0}, {40.0})
    assert number_reveal.range_values("140") is None and number_reveal.range_values("GPT-4") is None


def test_a_count_whose_window_opens_long_before_its_word_starts_on_its_roll(probe):
    ctx = _Ctx("and all we got was 140 characters. It's not an argument")
    t = _at(ctx.index["words"], "140")
    # on time (auto landing 1.0 s in), but the count would climb for a second
    out = motion_tools.add_motion_graphic(ctx, "counter", t - 1.0, t + 1.4, id="n",
                                          params={"value": "140"})
    it = ctx.item("n")
    assert it["start"] == pytest.approx(t - number_reveal.LEAD_S - number_reveal.ROLL_S, abs=0.002)
    assert it["params"]["land"] == pytest.approx(number_reveal.ROLL_S, abs=0.002)
    assert "never given away" in out, out


# ── 7. the accumulating list ─────────────────────────────────────────────

def _list_item(rows, start=10.0, end=14.0, reading=None, **params):
    item = {"id": "lb", "template": "list_build", "start": start, "end": end,
            "params": motion_templates.check_params("list_build", dict(params, rows=rows))}
    if reading is not None:
        item["reading"] = reading
    return item


def test_the_engine_times_each_item_whole_on_its_first_spoken_word():
    rows = [{"text": "rockets"}, {"text": "supersonic aviation"}, {"text": "underwater cities"}]
    item = _list_item(rows, reading={"v": 1, "rows": [[0.0], [0.62, 0.95], [2.1, 2.4]],
                                     "bridges": []})
    assert caption_carry.list_reveals(item) == [0.0, 0.62, 2.1]
    assert caption_carry.lockup_reveals(item) == [0.0, 0.62, 2.1]
    assert caption_carry.first_reveal(item) == 0.0
    # unspoken items: their 'at', else an even cadence after the item above,
    # never before it; a rise lands 0.06 s early (readable on its word)
    cad = _list_item([{"text": "a b"}, {"text": "c", "at": "0.2"}, {"text": "d"}], start=0, end=3.0)
    assert caption_carry.list_reveals(cad) == [0.0, 0.2, pytest.approx(0.2 + 0.6)]
    rise = _list_item(rows, reading={"v": 1, "rows": [[0.5], [1.0, 1.2], [None, 2.0]],
                                     "bridges": []}, entrance="rise")
    assert caption_carry.list_reveals(rise) == [0.44, 0.94, 1.94]


def test_a_list_build_window_starts_on_its_first_item_and_reads_the_transcript(probe):
    text = ("1960s technology meant computers but also rockets and supersonic aviation and "
            "underwater cities and new medicines. It was like a lot of things.")
    ctx = _Ctx(text)
    w = ctx.index["words"]
    first = _at(w, "rockets")
    out = motion_tools.add_motion_graphic(ctx, "list_build", first - 0.8, first + 4.0, id="lb",
                                          params={"rows": [{"text": "rockets"},
                                                           {"text": "supersonic aviation"},
                                                           {"text": "underwater cities"},
                                                           {"text": "new medicines"}],
                                                  "lead": "They promised"})
    assert out.startswith("EDL v"), out
    it = ctx.item("lb")
    assert it["start"] == pytest.approx(first, abs=0.01), out
    rd = it["reading"]["rows"]
    s = it["start"]
    assert s + rd[1][0] == pytest.approx(_at(w, "supersonic"), abs=0.01)
    assert s + rd[3][0] == pytest.approx(_at(w, "new"), abs=0.01)
    # the captions do not repeat the items it shows
    assert "list_build" in motion_templates.names()


def test_a_triads_lead_is_read_before_its_rows_so_the_caption_never_repeats_it(probe):
    """Review, Oct 2026: the Jobs triad's caption 'Let's get' was still up as
    the list's lead 'Let's get' appeared — the lead was matched after the
    rows, so the opener said just before the window stayed captioned."""
    ctx = _Ctx("We're injecting some liberal arts into these computers. "
               + JOBS.split("computers. ", 1)[1])
    ctx._edl["captions"] = {"mode": "from_transcript"}
    w = ctx.index["words"]
    first = _at(w, "proportionally")
    motion_tools.add_motion_graphic(ctx, "list_build", first, _at(w, "graphics") + 1.2, id="lb",
                                    params={"rows": [{"text": "proportionally spaced fonts"},
                                                     {"text": "multiple fonts"},
                                                     {"text": "graphics"}],
                                            "lead": "Let's get", "y": 0.2, "height": 0.16})
    edl = ctx.latest_edl()["json"]
    tl = Timeline(edl["keep"], [], [])
    shown = [x["w"] for x in caplib.caption_words(edl, ctx.index, tl)]
    assert "Let's" not in shown and "proportionally" not in shown, shown
    assert "computers." in shown


def test_list_and_hook_estimates_follow_their_copy():
    sp = motion_templates.spec("list_build")
    three = keepout.nominal_ink("list_build", sp, {"rows": [{"text": "a"}, {"text": "bb"},
                                                            {"text": "cc"}], "y": 0.5})
    five = keepout.nominal_ink("list_build", sp, {"rows": [{"text": "rockets"},
                                                           {"text": "supersonic aviation"},
                                                           {"text": "green revolution"},
                                                           {"text": "underwater cities"},
                                                           {"text": "new medicines"}],
                                                  "y": 0.5, "lead": "They promised"})
    assert five[3] - five[1] > three[3] - three[1]
    band = keepout.nominal_ink("list_build", sp, {"rows": [{"text": "rockets"},
                                                           {"text": "supersonic aviation"},
                                                           {"text": "underwater cities"}],
                                                  "y": 0.2, "height": 0.16})
    assert band[3] - band[1] <= 0.16 + 0.02
    ws = motion_templates.spec("word_slam")
    disp = keepout.nominal_ink("word_slam", ws, {"text": "They promised us / *flying cars*",
                                                 "y": 0.6})
    hook = keepout.nominal_ink("word_slam", ws, {"text": "They promised us / *flying cars*",
                                                 "y": 0.6, "tier": "hook"})
    assert hook[3] - hook[1] > disp[3] - disp[1]
    fs, lines, lead_fs, n_lead, _w = keepout.hook_layout(
        {"text": "gamers make / *better surgeons*", "width": 0.85})
    assert fs * keepout.CAP_RATIO >= 0.06 and lines == 2 and n_lead == 1 and lead_fs < fs / 2


def test_the_hook_tier_writes_a_hook_line_and_a_late_one_is_named(probe):
    ctx = _Ctx("and then " * 30)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 0.0, 2.0, id="hook",
                                          params={"text": "They promised us / *flying cars*",
                                                  "tier": "hook", "kicker": "Peter Thiel"})
    assert "HOOK: the captions wait until it exits" in out, out
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 6.0, 7.0, id="late",
                                          params={"text": "*again*", "tier": "hook"})
    assert "already this short's hook title" in out and "start it by 1.5s" in out, out
    # a hook that holds the captions back past the hook line is named
    out = motion_tools.set_motion_graphic(ctx, "hook", end=5.0)
    assert "holds the captions back for 5.0s" in out, out
    out = motion_tools.set_motion_graphic(ctx, "hook", end=2.5)
    assert "holds the captions back" not in out, out


# ── 8. the beat planner ──────────────────────────────────────────────────

def test_the_planner_ranks_lists_triads_ranges_and_names_and_reports_dead_stretches():
    text = (THIEL + " " + "and then " * 6 + "We have 30, 40 fonts on the screen. "
            + JOBS + " " + "and then " * 30 + "I met the LISA team there.")
    words = _words(text, start=0.2)
    dur = words[-1]["t1"] + 1.0
    res = motion_planner.plan({"keep": [[0.0, dur]]}, {"words": words})
    by_kind = {}
    for b in res["beats"]:
        by_kind.setdefault(b["kind"], []).append(b)
    lst = by_kind["list"][0]
    assert lst["template"] == "list_build"
    assert [r["text"] for r in lst["params"]["rows"]][-2:] == ["underwater cities", "new medicines"]
    assert len(lst["imagery"]) == len(lst["item_times"]) and lst["imagery"][0]["duration_s"] <= 0.6
    tri = by_kind["triad"][0]
    assert tri["template"] == "list_build" and tri["params"]["lead"] == "Let's get"
    rng = next(b for b in by_kind["number"] if "–" in str(b["params"].get("value")))
    assert rng["params"]["value"] == "30–40"
    # every suggested counter is placeable as written (the counter's own params)
    for b in by_kind["number"]:
        motion_templates.check_params("counter", {k: v for k, v in b["params"].items()
                                                  if v is not None})
    unit = motion_planner.plan({"keep": [[0.0, 30.0]]}, {"words": _words(
        "Hello there friends. It costs $15 or $20 million to build one of these.")})
    assert [b["params"]["value"] for b in unit["beats"] if b["kind"] == "number"] == ["$15–20M"]
    assert by_kind["name"][0]["imagery"][0]["query"] == "LISA"
    assert res["beat_gaps"] and all(b - a > motion_planner.BEAT_GAP_S for a, b in res["beat_gaps"])


# ── rendered compositions ─────────────────────────────────────────────────

def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not (_chromium_ok() and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")


async def _run(cases, size=(1080, 1920)):
    """[(template, params, duration, reading, [(t, js_expr), ...])] -> per
    case the expression values at each time (after MG.ready, seeking in
    order)."""
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for name, params, dur, reading, probes in cases:
                clean = motion_templates.check_params(name, params)
                item = {"id": name, "template": name, "start": 0.0, "end": dur, "params": clean}
                if reading:
                    item["reading"] = reading
                job = motion_templates.build_job(item, size[0], size[1], 30)
                dw, dh = motion_engine.design_size(job.out_w, job.out_h)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh})
                try:
                    await ctx.route("**/*", await motion_engine._route_factory(job))
                    page = await ctx.new_page()
                    await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                    await page.evaluate("async () => { await document.fonts.ready;"
                                        " if (window.MG && MG.ready) await MG.ready; }")
                    got = []
                    for t, expr in probes:
                        await page.evaluate("t => window.__mgSeek(t)", float(t))
                        got.append(await page.evaluate(expr))
                    assert not await page.evaluate("window.__mgErrors"), name
                    out.append(got)
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


# per item: [opacity, colour of its first line], and whether the frame is active
ITEMS = """(() => [window.__mgSeek(MG.t), Array.from(document.querySelectorAll('.item')).map(el =>
  [Number(getComputedStyle(el).opacity), getComputedStyle(el.querySelector('.line')).color])])()"""
GEOM = """(() => Array.from(document.querySelectorAll('.item')).map(el => {
  const r = el.getBoundingClientRect(); return [r.top, r.bottom, parseFloat(el.querySelector('.line').style.fontSize)]; }))()"""


@needs_browser
def test_list_build_items_land_on_their_onsets_persist_and_the_newest_is_accented():
    rows = [{"text": "Rockets"}, {"text": "Supersonic aviation"}, {"text": "Underwater cities"}]
    reading = {"v": 1, "rows": [[0.2], [0.8, 1.1], [1.6, 1.9]], "bridges": []}
    frames = [i / 30 for i in range(0, 90)]
    states, (geo,) = asyncio.run(_run([
        ("list_build", {"rows": rows, "accent": "#FF0000", "color": "#FFFFFF"}, 3.0, reading,
         [(t, ITEMS) for t in frames]),
        ("list_build", {"rows": rows}, 3.0, reading, [(2.5, GEOM)]),
    ]))
    lands = [0.2, 0.8, 1.6]
    red, white = "rgb(255, 0, 0)", "rgb(255, 255, 255)"
    for t, (_active, items) in zip(frames, states):
        for k, (opacity, colour) in enumerate(items):
            on = t >= lands[k] - 1e-6
            assert (opacity > 0.99) == on, (t, k, opacity)
            if on:
                newest = k == max(i for i, l in enumerate(lands) if t >= l - 1e-6)
                assert colour == (red if newest else white), (t, k, colour)
    # a frame that changes is a frame the template declares active
    for i in range(1, len(states)):
        if states[i][1] != states[i - 1][1]:
            assert states[i][0], frames[i]
    # one shared size, stacked top to bottom without overlapping
    assert len({round(g[2], 2) for g in geo}) == 1
    assert all(geo[i][0] >= geo[i - 1][1] - 1 for i in range(1, len(geo)))


NUMS = """(() => { const cs = Array.from(document.querySelectorAll('.num:not(.glow) .core'));
  const st = document.querySelector('#stage');
  return [Number(getComputedStyle(st).opacity), cs.map(c => c.textContent)]; })()"""


# a range's figures, and whether its second figure and its unit are shown
RANGE_PARTS = """(() => { const n = document.querySelector('.num:not(.glow)');
  const vis = el => el ? Number(getComputedStyle(el).opacity) : 1;
  const suf = Array.from(n.children).filter(e => e.classList.contains('aff')).pop();
  return [Array.from(n.querySelectorAll('.core')).map(c => c.textContent),
          vis(n.querySelectorAll('.lead')[1]), vis(suf)]; })()"""


@needs_browser
def test_the_counter_really_counts_into_its_word_and_shows_a_range_as_said():
    frames = [i / 30 for i in range(0, 60)]
    single, rng, money = asyncio.run(_run([
        ("counter", {"value": "140", "land": 1.2, "glow": 0}, 2.0, None,
         [(t, NUMS) for t in frames]),
        ("counter", {"value": "30–40", "land_first": 0.6, "land": 1.3, "glow": 0}, 2.0, None,
         [(t, NUMS) for t in frames]),
        ("counter", {"value": "$15–20M", "land_first": 0.6, "land": 1.3, "glow": 0}, 2.0, None,
         [(t, RANGE_PARTS) for t in frames]),
    ]))
    roll = 0.4
    for t, (opacity, cores) in zip(frames, single):
        if t < 1.2 - roll - 0.07:
            assert opacity == 0, (t, opacity)        # nothing across the setup
        figure = int(cores[0].replace(",", ""))
        assert (figure == 140) == (t >= 1.2 - 1e-6), (t, figure)
    mid = [int(c[0]) for t, (_o, c) in zip(frames, single) if 0.9 <= t < 1.15]
    assert mid and all(0 < v < 140 for v in mid) and mid == sorted(mid)   # it climbs
    for t, (opacity, cores) in zip(frames, rng):
        lo, hi = cores
        assert (lo == "30") == (t >= 0.6 - 1e-6), (t, lo)
        assert (hi == "40") == (t >= 1.3 - 1e-6), (t, hi)
        if t < 0.6 - roll - 0.07:
            assert opacity == 0, (t, opacity)
    # the unit belongs to the second figure: '$15' alone never reads '$15   M',
    # and the second figure never joins reading the first ('15–15M')
    for t, (cores, hi_on, suf_on) in zip(frames, money):
        assert suf_on == hi_on, (t, cores, hi_on, suf_on)
        if hi_on:
            assert cores[1] != cores[0], (t, cores)
    assert money[-1] == [["15", "20"], 1, 1], money[-1]


HOOK = """(() => Array.from(document.querySelectorAll('.line')).map(el => {
  const fs = parseFloat(el.style.fontSize); return [fs * MG.capRatio(el) / MG.H, !!el.dataset.small]; }))()"""


@needs_browser
def test_the_hook_tier_sets_its_main_line_as_a_headline_with_a_small_lead_in():
    (thiel,), (elon,), (display,) = asyncio.run(_run([
        ("word_slam", {"text": "They promised us / *flying cars*…", "tier": "hook",
                       "kicker": "Peter Thiel"}, 2.0, None, [(1.0, HOOK)]),
        ("word_slam", {"text": "gamers make / *better surgeons*", "tier": "hook"}, 2.0, None,
         [(1.0, HOOK)]),
        ("word_slam", {"text": "They promised us / *flying cars*…"}, 2.0, None, [(1.0, HOOK)]),
    ]))
    for lines in (thiel, elon):
        main = [cap for cap, small in lines if not small]
        lead = [cap for cap, small in lines if small]
        assert main and lead
        assert min(main) >= 0.065, lines              # caps at ~7% of the frame height
        assert max(lead) < min(main) / 2, lines
    assert max(cap for cap, _s in display) < min(c for c, s in thiel if not s)


@needs_browser
def test_the_hook_and_list_estimates_match_a_probe():
    cases = [
        ("word_slam", {"text": "They promised us / *flying cars*…", "tier": "hook",
                       "kicker": "Peter Thiel", "y": 0.62}),
        ("word_slam", {"text": "gamers make / *better surgeons*", "tier": "hook", "y": 0.66}),
        ("list_build", {"rows": [{"text": "Rockets"}, {"text": "Supersonic aviation"},
                                 {"text": "Green Revolution agriculture"},
                                 {"text": "Underwater cities"}, {"text": "New medicines"}],
                        "lead": "They promised", "y": 0.62}),
    ]
    items = []
    for name, params in cases:
        clean = motion_templates.check_params(name, params)
        items.append({"id": name, "template": name, "start": 0.0, "end": 2.4, "params": clean})
    reps = motion_tools._probe_items(items, 1080, 1920)
    for item, rep in zip(items, reps):
        got = keepout.settled_ink(rep, motion_tools._probe_times(item))
        want = keepout.nominal_ink(item["template"], motion_templates.spec(item["template"]),
                                   item["params"])
        assert got and max(abs(a - b) for a, b in zip(got, want)) < 0.05, (item["params"], got, want)
