"""Word-level caption muting and sound-off coverage (showcase judging, Oct 2026).

What is pinned here:
  1. A graphic with mute_captions unset hides only the spoken words it shows
     (case, punctuation, plurals, spelled numbers and *stars* folded); the
     other words stay captioned. A paraphrase that shares one word with the
     sentence does not punch a hole in it.
  2. Those captions move to a band clear of the graphic's stored footprint
     box and the face; with no clear band a speech-replacing template mutes them (and
     the write reply names the words), any other template leaves them be.
  3. mute_captions=true no longer mutes a whole window (round 6: every
     heard word reaches the screen once): it hides the words the graphic
     shows like unset, and mutes the rest only where no band is clear of it;
     false keeps captions running but never repeats a number/hero word.
  4. Captions never hold into, or start just before the exit of, a graphic
     that occupies their band.
  5. A spoken span over 0.6 s with no caption and no graphic showing it is a
     sound-off advisory naming the cause and the fix.
  6. Old EDLs without graphics are untouched; renders made before the plan
     are stale only where the plan changes them.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import captions  # noqa: E402
import config  # noqa: E402
import keepout  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import motion_tools  # noqa: E402
import quality_verifier  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


# "they promised us flying cars and all we got was 140 characters."
PUNCH = [("they", 0.2, 0.4), ("promised", 0.4, 0.8), ("us", 0.8, 0.95),
         ("flying", 1.0, 1.4), ("cars", 1.4, 1.8), ("and", 1.8, 1.9),
         ("all", 1.9, 2.05), ("we", 2.05, 2.2), ("got", 2.2, 2.45),
         ("was", 2.45, 2.7), ("140", 2.8, 3.3), ("characters.", 3.3, 3.9),
         ("So", 4.6, 4.7), ("that", 4.7, 4.8), ("we", 4.8, 4.9), ("can", 4.9, 5.0),
         ("deal", 5.0, 5.3), ("in", 5.3, 5.4), ("pictures.", 5.4, 6.0)]
# A face high in a 9:16 frame (output fractions == source on a 9:16 source).
FACE_HIGH = [{"t": t, "faces": [[0.3, 0.12, 0.7, 0.3]]} for t in (0.5, 2.0, 3.5, 5.0)]


def _edl(motion=(), look="editorial", dur=8.0, words=PUNCH, **caps):
    edl = default_edl(dur)
    edl["captions"] = dict({"mode": "from_transcript", "design_version": 2,
                            "style": {"motion_look": look} if look else {"preset": "stacked"}},
                           **caps)
    edl["motion"] = [dict(m) for m in motion]
    return validate_edl(edl, dur).model_dump()


def _index(words=PUNCH, faces=FACE_HIGH):
    ix = {"video": {"width": 1080, "height": 1920}, "words": _w(words)}
    if faces is not None:
        ix["spatial"] = {"samples": faces}
    return ix


def _plan(edl, index):
    return captions.caption_plan(edl, index, Timeline(edl["keep"]))


def _shown(edl, index):
    return [w["w"] for w in captions.caption_words(edl, index, Timeline(edl["keep"]))]


# The aspect (W/H) a box measured on these 9:16 frames is stamped with.
AR_9X16 = round(1080 / 1920, 4)


def _fp(box, ar=AR_9X16):
    """A stored footprint (MotionItem.footprint) for a measured box."""
    return {"box": list(box), "ar": ar, "faces": []}


def _slam(start, end, text, kicker="", box=(0.1, 0.30, 0.9, 0.42), ar=AR_9X16, **kw):
    m = {"id": kw.pop("id", "slam"), "template": "word_slam", "start": start, "end": end,
         "params": {"text": text, "kicker": kicker}}
    if box:
        m["footprint"] = _fp(box, ar)
    m.update(kw)
    return m


def _counter(start, end, value, label="", box=(0.3, 0.42, 0.7, 0.56), ar=AR_9X16, **kw):
    m = {"id": kw.pop("id", "num"), "template": "counter", "start": start, "end": end,
         "params": {"value": value, "label": label}}
    if box:
        m["footprint"] = _fp(box, ar)
    m.update(kw)
    return m


# ── 1. what a graphic shows ──────────────────────────────────────────────

def test_tokens_fold_spelled_numbers_case_plurals_and_stars():
    assert caption_carry.tokens("one hundred and forty *Characters*") == ["140", "character"]
    assert caption_carry.tokens("Forty-two") == ["42"]
    assert caption_carry.tokens("a hundred") == ["100"]
    assert caption_carry.tokens("1,000 fonts") == ["1000", "font"]
    assert caption_carry.tokens("the 1960s") == ["the", "1960"]
    # two numbers said in a row stay two numbers
    assert caption_carry.tokens("thirty, forty fonts") == ["30", "40", "font"]


def test_spoken_numbers_split_over_words_match_the_numeral_on_screen():
    words = [("all", 0.0, 0.2), ("we", 0.2, 0.3), ("got", 0.3, 0.5), ("was", 0.5, 0.7),
             ("one", 0.8, 0.95), ("hundred", 0.95, 1.2), ("forty", 1.2, 1.5),
             ("characters.", 1.5, 2.0)]
    edl = _edl([_counter(0.7, 2.2, "140")], words=words)
    # the counter shows "one hundred forty"; "characters", said while it is
    # up but not on it, stays captioned (every heard word reaches the screen)
    assert _shown(edl, _index(words)) == ["all", "we", "got", "was", "characters."]
    assert [[w["w"] for w in r] for r in _plan(edl, _index(words)).report["num"]["captioned"]] \
        == [["characters."]]
    edl = _edl([_counter(0.7, 2.2, "140", label="characters")], words=words)
    assert _shown(edl, _index(words)) == ["all", "we", "got", "was"]
    assert not _plan(edl, _index(words)).report["num"]["captioned"]


def test_a_paraphrase_sharing_one_word_does_not_punch_a_hole():
    rows = [{"text": "computer fonts were"}, {"text": "*GARBAGE*"}]
    words = [("Every", 0.1, 0.4), ("computer", 0.45, 0.9), ("to", 0.95, 1.05),
             ("date", 1.1, 1.5), ("has", 1.6, 1.8), ("used", 1.8, 2.0),
             ("garbage", 2.1, 2.6)]
    m = {"id": "hook", "template": "phrase_build", "start": 0.0, "end": 3.0,
         "params": {"rows": rows}, "footprint": _fp([0.1, 0.06, 0.9, 0.24])}
    edl = _edl([m], words=words)
    # "computer" is part of a different line, said inside another sentence;
    # "garbage" is the starred hero word, said while it is on screen
    assert _shown(edl, _index(words)) == ["Every", "computer", "to", "date", "has", "used"]


def test_list_rows_take_their_own_words_and_the_connectors_between_them():
    words = [("rockets", 0.2, 0.6), ("and", 0.6, 0.7), ("supersonic", 0.7, 1.2),
             ("aviation", 1.2, 1.7), ("and", 1.7, 1.8), ("the", 1.8, 1.9),
             ("green", 1.9, 2.2), ("revolution", 2.2, 2.7), ("and", 2.8, 2.9),
             ("underwater", 2.9, 3.4), ("cities", 3.4, 3.8), ("and", 3.8, 3.9),
             ("new", 3.9, 4.1), ("medicines.", 4.1, 4.7)]
    rows = [{"text": "ROCKETS"}, {"text": "supersonic jets"},
            {"text": "underwater cities"}, {"text": "NEW *MEDICINES*"}]
    m = {"id": "list", "template": "phrase_build", "start": 0.0, "end": 5.0,
         "params": {"rows": rows}, "footprint": _fp([0.1, 0.06, 0.9, 0.3])}
    edl = _edl([m], words=words)
    # the list takes its rows and the joints between them; the item its rows
    # leave out stays CAPTIONED (the lockup no longer sets it in micro type)
    assert _shown(edl, _index(words)) == ["aviation", "and", "the", "green", "revolution", "and"]
    rep = _plan(edl, _index(words)).report["list"]
    assert [caption_carry._said(r) for r in rep["captioned"]] == \
        ["aviation and the green revolution and"]
    assert rep["joined"] == [] and rep["yielded"] == []
    rd = caption_carry.readings(edl, _index(words), Timeline(edl["keep"]))["list"]
    assert rd["bridges"] == []
    # rows land on their spoken onsets; "jets" is never said
    assert rd["rows"] == [[0.2], [0.7, None], [2.9, 3.4], [3.9, 4.1]]


# ── 2. placement clear of the graphic ────────────────────────────────────

def test_words_it_does_not_show_stay_captioned_in_place_when_their_band_is_clear():
    edl = _edl([_slam(0.9, 2.0, "flying *cars*")])
    ix = _index()
    p = _plan(edl, ix)
    shown = [w["w"] for w in p.caption_words()]
    assert "flying" not in shown and "cars" not in shown
    assert {"they", "promised", "we", "got", "and", "all"} <= set(shown)
    # "and all", said while it is up in the phrase it shows, stay captioned
    assert [caption_carry._said(r) for r in p.report["slam"]["captioned"]] == ["and all"]
    assert not p.report["slam"]["yielded"]
    assert not p.placed and not p.clamp_spans          # nothing collides with y≈0.74
    cues = motion_captions.cues(edl, ix, Timeline(edl["keep"]))
    assert all("z" not in c for c in cues)


def test_words_under_a_graphic_on_the_caption_band_move_clear_of_it_and_the_face():
    # the slam sits on the caption band (0.62-0.82); the face is high, so the
    # band between the face and the graphic is clear
    edl = _edl([_slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))])
    ix = _index()
    p = _plan(edl, ix)
    moved = {p.words[i]["w"]: place for i, place in p.placed.items()}
    assert set(moved) == {"all", "we", "got", "was"}       # "140" is on the slam
    place = moved["we"]
    assert len({str(v) for v in moved.values()}) == 1
    # between the face (0.12-0.30) and the slam (0.62-0.82), nearest its usual band
    assert 0.30 + caption_carry.FACE_PAD <= place["z"][0] < place["z"][1] \
        <= 0.62 - caption_carry.GRAPHIC_PAD + 1e-9
    assert place["z"][1] - place["z"][0] >= caption_carry.MIN_BAND_H - 1e-6
    assert place["y"] == pytest.approx(0.62 - caption_carry.GRAPHIC_PAD - caption_carry.CAP_HALF_H)
    assert p.clamp_spans == [[1.9, 3.0]]
    tl = Timeline(edl["keep"])
    cues = motion_captions.cues(edl, ix, tl)
    inside = [c for c in cues if 1.9 <= c["s"] < 3.0]
    assert inside and all(c.get("z") == place["z"] and c["y"] == place["y"] for c in inside)
    assert all("z" not in c for c in cues if c["s"] >= 3.0 or c["s"] < 1.9)
    # a line in its usual place never holds into the graphic's window
    before = [c for c in cues if c["s"] < 1.9]
    assert before and all(c["e"] <= 1.9 + 1e-6 for c in before)
    # the libass path anchors the same words inside the same clear band
    ass = _edl([_slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))], look=None)
    events, _ = captions.compiled_events(ass, ix, tl, (1080, 1920))
    import re
    for ev in events:
        y = int(re.search(r"\\pos\(\d+,(\d+)\)", ev["text"]).group(1)) / 1920
        if 1.9 <= ev["start"] < 3.0:
            assert place["z"][0] <= y <= place["z"][1], (ev["start"], y)
        else:
            assert y > 0.7, (ev["start"], y)


def test_no_clear_band_mutes_under_a_speech_template_and_keeps_captions_under_others():
    # graphic and assumed face fill the whole safe area
    big = (0.1, 0.45, 0.9, 0.85)
    edl = _edl([_slam(1.9, 3.0, "*140*", box=big)])
    ix = _index(faces=None)                      # unmeasured -> talking-head prior
    p = _plan(edl, ix)
    room = sorted(p.words[i]["w"] for i, (_o, why) in p.hidden.items() if why == "room")
    assert room == ["all", "got", "was", "we"]
    assert p.report["slam"]["muted"]
    # a counter is not a speech-replacing template: its captions stay put
    edl = _edl([_counter(1.9, 3.0, "140", box=big)])
    p = _plan(edl, ix)
    assert not any(why == "room" for _o, why in p.hidden.values())
    assert p.report["num"]["kept"] and not p.clamp_spans


def test_an_unmeasured_box_keeps_the_old_behaviour():
    edl = _edl([_slam(1.9, 3.0, "*140*", box=None)])
    p = _plan(edl, _index())
    assert sorted(p.words[i]["w"] for i, (_o, why) in p.hidden.items()
                  if why == "unmeasured") == ["all", "got", "was", "we"]
    edl = _edl([_counter(1.9, 3.5, "140", box=None)])
    assert _shown(edl, _index()).count("140") == 0        # still no duplicate
    assert "got" in _shown(edl, _index())


# ── 3. explicit intent ───────────────────────────────────────────────────

def test_true_hides_what_it_shows_and_false_keeps_captions_but_never_repeats_a_number():
    # round 6: no graphic mutes a whole window; true hides the words it
    # shows, and the rest stay captioned (clear of it)
    whole = _edl([_counter(1.9, 3.95, "140", label="characters", mute_captions=True)])
    assert captions.effective_caption_mutes(whole) == []
    shown = _shown(whole, _index())
    assert not {"140", "characters."} & set(shown)
    assert {"got", "was"} <= set(shown)
    keep = _edl([_counter(1.9, 3.95, "140", label="characters", mute_captions=False)])
    assert captions.effective_caption_mutes(keep) == []
    shown = _shown(keep, _index())
    assert "140" not in shown                               # the hero number
    assert {"we", "got", "was", "characters."} <= set(shown)   # the rest keep running
    auto = _edl([_counter(1.9, 3.95, "140", label="characters")])
    assert captions.effective_caption_mutes(auto) == []
    assert "characters." not in _shown(auto, _index())      # unset: every word it shows


def test_a_kicker_said_just_before_its_slam_is_handed_to_it():
    words = [("but", 0.0, 0.2), ("it's", 0.2, 0.35), ("not", 0.62, 0.75),
             ("quite", 0.75, 0.9), ("been", 0.9, 1.05), ("enough", 1.3, 1.8),
             ("to", 1.9, 2.0), ("take", 2.0, 2.3)]
    edl = _edl([_slam(1.1, 2.4, "*enough*", kicker="it's not quite been")], words=words)
    # the words within CARRY_LEAD_S go with the slam; earlier ones stay read
    # (and "to take", said while the slam is up, stay captioned beside it)
    assert _shown(edl, _index(words)) == ["but", "it's", "to", "take"]
    # ...only when the slam's own word carries straight on from them
    edl = _edl([_slam(1.1, 2.4, "*plenty*", kicker="it's not quite been")], words=words)
    assert "not" in _shown(edl, _index(words))


# ── 4. timing against the graphic ────────────────────────────────────────

def test_a_word_is_never_revealed_more_than_two_frames_late_for_a_graphic():
    # judged: a page flip snapped to a change made 'has' 0.2 s late. A line
    # in its usual place starting just before a graphic on its band leaves
    # waits at most two frames; one further off is placed clear of it and
    # shows on its own onset
    words = [("the", 0.1, 0.3), ("1960s", 0.3, 0.9), ("technology", 0.9, 1.5),
             ("meant", 1.5, 1.9), ("computers", 2.17, 2.7), ("but", 2.8, 3.0)]
    hook = _slam(0.0, 2.3, "where did / *progress* go?", box=(0.1, 0.62, 0.9, 0.82),
                 id="hook", mute_captions=True)
    edl = _edl([hook], words=words)
    cues = motion_captions.cues(edl, _index(words), Timeline(edl["keep"]))
    onsets = {w["t"].lower(): w["s"] for c in cues for w in c["w"]}
    for t, a, _b in words:
        assert onsets[t.lower()] - a <= 2 / 30 + 0.01, t
    assert caption_carry.START_WAIT_S <= 2 / 30 + 0.01


# ── 5. sound-off coverage ────────────────────────────────────────────────

def test_sound_off_gaps_name_the_words_the_cause_and_the_fix():
    tl = Timeline([[0.0, 8.0]])
    # true no longer hides what the graphic does not show: no gap
    whole = _edl([_counter(0.9, 3.95, "140", label="characters", mute_captions=True)])
    assert caption_carry.sound_off_gaps(whole, _index(), tl) == []
    # ...unless no band is clear of it and the face: then those words go,
    # and the gap names them, the graphic and the fix
    big = _counter(0.9, 3.95, "140", label="characters", mute_captions=True,
                   box=(0.1, 0.45, 0.9, 0.85))
    gaps = caption_carry.sound_off_gaps(_edl([big]), _index(faces=None), tl)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["said"] == "flying cars and all we got"        # content words bound it
    assert g["owner"] == "num" and "no caption band" in g["cause"]
    assert "caption band" in g["fix"] and g["duration_s"] > caption_carry.SOUND_OFF_GAP_S
    # word level: every word is either captioned or on the graphic
    auto = _edl([_counter(0.9, 3.95, "140", label="characters")])
    assert caption_carry.sound_off_gaps(auto, _index(), tl) == []
    assert caption_carry.heard_unshown(auto, _index(), tl) == []


def test_sound_off_gap_from_a_graphic_with_no_clear_band_and_a_manual_mute():
    tl = Timeline([[0.0, 8.0]])
    edl = _edl([_slam(0.9, 2.75, "*140*", box=(0.1, 0.45, 0.9, 0.85))])
    gaps = caption_carry.sound_off_gaps(edl, _index(faces=None), tl)
    assert gaps and gaps[0]["owner"] == "slam" and "caption band" in gaps[0]["fix"]
    manual = _edl()
    manual["caption_mutes"] = [[4.5, 6.2]]
    gaps = caption_carry.sound_off_gaps(manual, _index(), tl)
    assert gaps and "set_caption_mutes" in gaps[0]["cause"]


def test_sound_off_gaps_are_an_advisory_finding_never_a_repair():
    whole = _edl([_counter(0.9, 3.95, "140", label="characters", mute_captions=True,
                           box=(0.1, 0.45, 0.9, 0.85))])
    rows = [r for r in quality_verifier.deterministic_findings(whole, _index(faces=None))
            if r["code"] == "sound_off_gap"]
    assert len(rows) == 1 and not quality_verifier.is_blocking(rows[0])
    assert "flying cars" in rows[0]["message"] and rows[0]["evidence"]["gaps"]
    # no captions -> no sound-off finding here (taste owns "add captions")
    bare = validate_edl(default_edl(8.0), 8.0).model_dump()
    assert not [r for r in quality_verifier.deterministic_findings(bare, _index())
                if r["code"] == "sound_off_gap"]


# ── 6. compatibility, stamps, schema, measurement ────────────────────────

def test_an_edl_without_graphics_is_untouched():
    edl = _edl(look=None)
    ix = _index()
    tl = Timeline(edl["keep"])
    words = captions.transcript_words(edl, ix, tl, captions.effective_caption_mutes(edl))
    p = captions.caption_plan(edl, ix, tl)
    assert p.caption_words() is p.words and [w["w"] for w in words] == [w["w"] for w in p.words]
    assert not p.clamp_spans and not p.wait_spans


def test_renders_before_the_plan_are_stale_only_with_graphics_and_transcript_captions():
    import renderer
    graphic = _edl([_counter(1.0, 2.0, "140")])
    assert not renderer.carry_current({}, graphic)
    assert not renderer.carry_current({"carry_v": config.CAPTION_CARRY_VERSION - 1}, graphic)
    assert renderer.carry_current({"carry_v": config.CAPTION_CARRY_VERSION}, graphic)
    assert renderer.carry_current({}, _edl())                       # no graphics
    bare = validate_edl(dict(default_edl(8.0), motion=[_counter(1.0, 2.0, "140")]),
                        8.0).model_dump()
    assert renderer.carry_current({}, bare)                         # no captions


def test_the_footprint_is_a_measurement_sanitized_or_dropped_never_rejected():
    ok = _edl([_counter(1.0, 2.0, "140", box=(-0.2, 0.4, 1.3, 0.6))])
    assert ok["motion"][0]["footprint"]["box"] == [0.0, 0.4, 1.0, 0.6]
    for bad in ([0.5, 0.5, 0.4, 0.6], [1, 2, 3], ["x", 0, 1, 1]):
        e = _edl([_counter(1.0, 2.0, "140", box=None)])
        e["motion"][0]["footprint"] = _fp(bad)
        assert validate_edl(e, 8.0).model_dump()["motion"][0].get("footprint") is None
    legacy = _edl([_counter(1.0, 2.0, "140", box=None)])
    assert "footprint" not in validate_edl(legacy, 8.0).model_dump(exclude_none=True)["motion"][0]
    # the pre-release name of the same measurement (drawn + drawn_ar = H/W)
    # still reads as the footprint
    e = _edl([_counter(1.0, 2.0, "140", box=None)])
    e["motion"][0].update(drawn=[0.1, 0.62, 0.9, 0.82], drawn_ar=round(1920 / 1080, 4))
    m = validate_edl(e, 8.0).model_dump(exclude_none=True)["motion"][0]
    assert m["footprint"]["box"] == [0.1, 0.62, 0.9, 0.82] and "drawn" not in m
    assert m["footprint"]["ar"] == pytest.approx(AR_9X16, abs=1e-3)


def test_fill_footprints_measures_only_what_the_plan_needs_once(monkeypatch):
    calls = []

    def probe(jobs, times):
        calls.append(len(jobs))
        return [{"errors": [], "visible_frames": 4, "samples": 4,
                 "bboxes": [[0.0, 0.3, 1.0, 0.7]], "ink": [[0.2, 0.4, 0.8, 0.6]]}
                for _ in jobs]
    monkeypatch.setattr(motion_engine, "probe", probe)
    monkeypatch.setattr(motion_layer, "_FOOTPRINT_CACHE", {})
    edl = _edl([_counter(1.0, 2.0, "140", box=None),
                _counter(3.0, 4.0, "7", box=None, id="whole", mute_captions=True),
                _counter(5.0, 6.0, "9", box=(0.1, 0.1, 0.2, 0.2), id="known")])
    motion_layer.fill_footprints(edl, 1080, 1920)
    by = {m["id"]: (m.get("footprint") or {}).get("box") for m in edl["motion"]}
    # mute_captions=true no longer hides a whole window (every heard word
    # reaches the screen once): the words it does not show are placed
    # against its real box like any other graphic's, so it is measured too
    assert by == {"num": [0.2, 0.4, 0.8, 0.6], "whole": [0.2, 0.4, 0.8, 0.6],
                  "known": [0.1, 0.1, 0.2, 0.2]}
    again = _edl([_counter(1.0, 2.0, "140", box=None)])
    motion_layer.fill_footprints(again, 540, 960)            # same aspect: cached
    assert again["motion"][0]["footprint"]["box"] == [0.2, 0.4, 0.8, 0.6] and calls == [2]
    # no transcript captions: nothing to place, nothing measured
    bare = validate_edl(dict(default_edl(8.0), motion=[_counter(1.0, 2.0, "5", box=None)]),
                        8.0).model_dump()
    motion_layer.fill_footprints(bare, 1080, 1920)
    assert calls == [2]


class _Ctx:
    project_id = 1

    def __init__(self, faces=FACE_HIGH):
        self.duration = 8.0
        self._edl = _edl()
        self.index = _index(faces=faces)
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def _probe_with(box):
    return lambda item, W, H, fps=30.0: {
        "errors": [], "visible_frames": 4, "samples": 4,
        "bboxes": [[0.0, box[1] - 0.05, 1.0, box[3] + 0.05]], "ink": [list(box)]}


def test_the_write_stores_the_ink_box_and_says_where_the_captions_go(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_with((0.1, 0.62, 0.9, 0.82)))
    ctx = _Ctx()
    # (placed so the slam already lands on "140": number landing leaves it be)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 2.78, 3.3,
                                          params={"text": "*140*"}, id="slam")
    assert out.startswith("EDL v1") and "NUMBER LANDED" not in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    fp = item["footprint"]
    assert fp["box"] == [0.1, 0.62, 0.9, 0.82]          # ink, not the scrim's reach
    assert fp["ar"] == pytest.approx(1080 / 1920, abs=0.01)   # the shape it fits
    assert "Captions for the words it does not show move to y≈" in out, out
    assert "NOTE (captions)" not in out, out
    # a probe that cannot run drops a stale box rather than keeping it
    monkeypatch.setattr(motion_tools, "_probe_item", lambda *a, **k: None)
    motion_tools.set_motion_graphic(ctx, "slam", params={"text": "*one forty*"})
    item = ctx.latest_edl()["json"]["motion"][0]
    # (a browserless lane estimates the box instead: never the stale one)
    assert not item.get("footprint") or item["footprint"].get("estimated"), item


def test_the_write_names_words_a_graphic_on_the_caption_band_must_mute(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_with((0.1, 0.45, 0.9, 0.85)))
    ctx = _Ctx(faces=None)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.0, 2.75,
                                          params={"text": "*rockets*"})
    assert "NOTE (captions)" in out and '"flying cars and all we got was"' in out, out
    assert "no caption band is clear of this graphic (it draws y 0.45-0.85)" in out, out


# ── review round: what the first cut missed ──────────────────────────────

def test_a_run_of_connectors_alone_is_not_the_graphics_words():
    # "THE END OF THE WORLD" shares only "of the" with what is said: the
    # caption keeps the whole sentence
    words = [("it", 0.1, 0.3), ("was", 0.3, 0.5), ("one", 0.5, 0.8), ("of", 0.8, 0.9),
             ("the", 0.9, 1.0), ("best", 1.0, 1.3), ("years", 1.3, 1.7)]
    edl = _edl([_slam(0.0, 2.0, "the end of the world")], words=words)
    assert _shown(edl, _index(words)) == ["it", "was", "one", "of", "the", "best", "years"]
    # ...while a run that holds a content word is still the graphic's
    edl = _edl([_slam(0.0, 2.0, "*best* years")], words=words)
    assert _shown(edl, _index(words)) == ["it", "was", "one", "of", "the"]


def test_a_box_measured_at_another_frame_shape_is_not_trusted():
    on_band = (0.1, 0.62, 0.9, 0.82)
    fresh = _edl([_slam(1.9, 3.0, "*140*", box=on_band, ar=AR_9X16)])
    p = _plan(fresh, _index())
    assert p.placed and not any(why != "carried" for _o, why in p.hidden.values())
    # the same box stamped at 16:9 means nothing in this 9:16 frame: the
    # speech graphic is assumed to sit on the captions until it is measured
    stale = _edl([_slam(1.9, 3.0, "*140*", box=on_band, ar=round(1920 / 1080, 4))])
    p = _plan(stale, _index())
    assert not p.placed and p.report["slam"]["box"] is None
    assert sorted(p.words[i]["w"] for i, (_o, why) in p.hidden.items()
                  if why == "unmeasured") == ["all", "got", "was", "we"]
    longer = _edl([_slam(0.9, 2.75, "*140*", box=on_band, ar=round(1920 / 1080, 4))])
    gaps = caption_carry.sound_off_gaps(longer, _index(), Timeline(longer["keep"]))
    assert gaps and gaps[0]["owner"] == "slam", gaps
    assert "no box measured" in gaps[0]["cause"] and "re-save" in gaps[0]["fix"], gaps


def test_the_footprint_aspect_is_sanitized_like_the_box():
    ok = _edl([_counter(1.0, 2.0, "140", ar=0.562512)])
    assert ok["motion"][0]["footprint"]["ar"] == 0.5625
    for bad in (0.0, -1.0, 1e9, float("nan")):
        e = _edl([_counter(1.0, 2.0, "140")])
        e["motion"][0]["footprint"]["ar"] = bad
        assert validate_edl(e, 8.0).model_dump()["motion"][0].get("footprint") is None
    # a stamp without a box is no measurement
    e = _edl([_counter(1.0, 2.0, "140", box=None)])
    e["motion"][0]["footprint"] = {"ar": AR_9X16}
    assert validate_edl(e, 8.0).model_dump()["motion"][0].get("footprint") is None


def test_fill_footprints_remeasures_stale_boxes_and_inkless_compositions_get_none(monkeypatch):
    seen = []

    def probe(jobs, times):
        seen.append(len(jobs))
        return [{"errors": [], "visible_frames": 4, "samples": 4,
                 "bboxes": [[0.0, 0.0, 1.0, 1.0]],
                 "ink": [] if "light_leak" in j.label else [[0.2, 0.4, 0.8, 0.6]]}
                for j in jobs]
    monkeypatch.setattr(motion_engine, "probe", probe)
    monkeypatch.setattr(motion_layer, "_FOOTPRINT_CACHE", {})
    leak = {"id": "leak", "template": "light_leak", "start": 3.0, "end": 4.0, "params": {}}
    edl = _edl([_counter(1.0, 2.0, "140", box=(0.1, 0.1, 0.2, 0.2), ar=round(1920 / 1080, 4)),
                leak,
                _counter(5.0, 6.0, "9", box=(0.1, 0.1, 0.2, 0.2), id="ok", ar=AR_9X16)])
    motion_layer.fill_footprints(edl, 1080, 1920)
    by = {m["id"]: ((m.get("footprint") or {}).get("box"), (m.get("footprint") or {}).get("ar"))
          for m in edl["motion"]}
    assert by["num"] == ([0.2, 0.4, 0.8, 0.6], pytest.approx(1080 / 1920, abs=1e-3))
    assert by["leak"] == (None, None)            # soft light: nothing to keep clear of
    assert by["ok"] == ([0.1, 0.1, 0.2, 0.2], AR_9X16)
    assert seen == [2]
    # an inkless report never stands in for a box at write time either
    assert motion_layer.caption_box({"bboxes": [[0, 0, 1, 1]], "ink": []}, [0.1]) is None
    assert motion_layer.caption_box({"bboxes": [[0, 0.5, 1, 1]]}, [0.1]) == [0, 0.5, 1, 1]


def test_a_line_too_short_to_wait_for_the_exit_starts_on_time():
    # "computers" begins in the hook's last 0.1 s and the next line follows
    # straight on: it is shown, never dropped
    words = [("the", 0.1, 0.3), ("1960s", 0.3, 0.9), ("technology", 0.9, 1.5),
             ("meant", 1.5, 1.9), ("computers.", 2.2, 2.42), ("And", 2.45, 2.7),
             ("that", 2.7, 2.9), ("was", 2.9, 3.1), ("it", 3.1, 3.4)]
    hook = _slam(0.0, 2.3, "where did / *progress* go?", box=(0.1, 0.62, 0.9, 0.82),
                 id="hook", mute_captions=True)
    edl = _edl([hook], words=words)
    cues = motion_captions.cues(edl, _index(words), Timeline(edl["keep"]))
    said = [w["t"].lower().strip(".") for c in cues for w in c["w"]]
    assert "computers" in said


def test_a_stitched_preview_re_encodes_captions_a_graphic_change_moved(tmp_path):
    import renderer
    words = [("but", 0.0, 0.2), ("it's", 0.2, 0.35), ("not", 0.62, 0.75),
             ("quite", 0.75, 0.9), ("been", 0.9, 1.05), ("enough", 1.3, 1.8),
             ("to", 1.9, 2.0), ("take", 2.0, 2.3), ("our", 3.5, 3.7),
             ("civilization", 3.7, 4.4), ("further.", 4.4, 5.0)]
    ix = _index(words)
    prev = _edl([_slam(1.1, 2.4, "*plenty*", kicker="it's not quite been")],
                look=None, words=words)
    new = _edl([_slam(1.1, 2.4, "*enough*", kicker="it's not quite been")],
               look=None, words=words)
    tl = Timeline(new["keep"])
    full = captions.build_ass(new, ix, tl, str(tmp_path / "new.ass"), play_res=(1080, 1920))
    spans = renderer._caption_change_windows(prev, Timeline(prev["keep"]), full, ix,
                                             str(tmp_path), 1080, 1920, 30.0,
                                             tl.out_duration)
    # the kicker said BEFORE the slam left the captions: the card it sat in
    # (from 0.0 s) is re-encoded, not copied from the previous preview
    assert spans and min(a for a, _b in spans) < 1.1 - 0.5
    # the captions after the graphic did not change, and nothing else moves
    assert all(b <= 3.5 for _a, b in spans)
    same = renderer._caption_change_windows(new, tl, full, ix, str(tmp_path),
                                            1080, 1920, 30.0, tl.out_duration)
    assert same == []


def test_a_chart_series_is_drawn_not_printed_so_its_numbers_stay_captioned():
    words = [("we", 0.1, 0.3), ("went", 0.3, 0.5), ("from", 0.5, 0.7), ("12", 0.8, 1.1),
             ("to", 1.1, 1.2), ("61", 1.3, 1.7), ("percent", 1.7, 2.2)]
    chart = {"id": "chart", "template": "line_chart", "start": 0.0, "end": 3.0,
             "params": {"values": ["12", "18", "15", "30", "61"], "value_label": "61%"},
             "footprint": _fp([0.1, 0.1, 0.9, 0.4])}
    edl = _edl([chart], words=words)
    # the end tag prints "61%": that one is not read twice; "12" is only a
    # point on the line
    assert _shown(edl, _index(words)) == ["we", "went", "from", "12", "to"]
    assert ("values", "12") not in caption_carry.graphic_lines(edl["motion"][0])


# ── one measurement, one placement pass (with the face keep-out) ─────────

def test_the_plan_steps_around_the_face_zones_the_keep_out_measured():
    # the keep-out measured the face LOW in this window (a push-in), while
    # the index only saw it high: the captions clear the measured zone
    low_face = [0.25, 0.35, 0.75, 0.6]
    slam = _slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))
    slam["footprint"]["faces"] = [low_face]
    edl = _edl([slam])
    ix = _index()                                   # FACE_HIGH: 0.12-0.30
    p = _plan(edl, ix)
    place = next(iter(p.placed.values()))
    assert place["z"][1] <= low_face[1] - caption_carry.FACE_PAD + 1e-9, place
    # ...and, with room above the head, clear of the hair as well
    hair = low_face[1] - caption_carry.HAIR_UP * (low_face[3] - low_face[1])
    assert place["z"][1] <= hair + 1e-9, place
    # the burned (libass) captions take the same band as the motion track
    tl = Timeline(edl["keep"])
    cues = motion_captions.cues(edl, ix, tl)
    assert any(c.get("z") == place["z"] for c in cues)
    ass = _edl([slam], look=None)
    events, _ = captions.compiled_events(ass, ix, tl, (1080, 1920))
    import re
    inside = [int(re.search(r"\\pos\(\d+,(\d+)\)", ev["text"]).group(1)) / 1920
              for ev in events if 1.9 <= ev["start"] < 3.0]
    assert inside and all(place["z"][0] <= y <= place["z"][1] for y in inside), inside


def test_stored_face_zones_go_stale_when_the_picture_changes():
    # the keep-out stored the face zone (stamped with the picture geometry
    # it measured) at the graphic's write; a zoom added afterwards moved the
    # face, and the stored zone was never re-measured. The stale zone is no
    # evidence: the plan clears the face where the index puts it NOW.
    stored = [0.3, 0.1, 0.7, 0.28]                  # where the face was
    slam = _slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))
    slam["footprint"]["faces"] = [stored]
    before = _edl([slam])
    slam["footprint"]["geo"] = caption_carry.face_geometry(before)
    now = [{"t": t, "faces": [[0.3, 0.33, 0.7, 0.5]]} for t in (0.5, 2.0, 3.5, 5.0)]
    ix = _index(faces=now)
    # the same picture: the stored zone is trusted
    same = _edl([slam])
    assert same["motion"][0]["footprint"]["geo"] == caption_carry.face_geometry(same)
    place = next(iter(_plan(same, ix).placed.values()))
    assert place["z"][0] >= stored[3]
    face_now = keepout.face_zone((0.3, 0.33, 0.7, 0.5))
    assert place["z"][0] < face_now[3]             # it would sit on the face now
    # a zoom later: the stored zone is stale and the face is cleared where it is
    zoomed = _edl([slam], dur=8.0)
    zoomed["effects"] = dict(zoomed.get("effects") or {}, zooms=[
        {"id": "z", "start": 1.0, "end": 3.5, "strength": 0.05, "mode": "punch",
         "cy": 0.4}])
    zoomed = validate_edl(zoomed, 8.0).model_dump()
    assert caption_carry.face_geometry(zoomed) != slam["footprint"]["geo"]
    p = _plan(zoomed, ix)
    assert p.placed
    for place in p.placed.values():
        live = keepout.zones_of(keepout.face_track(zoomed, ix, 1080, 1920, 1.9, 3.0))
        for z in live:
            assert place["z"][1] <= z[1] or place["z"][0] >= z[3], (place, z)
    # an unstamped footprint (written before the stamp) is trusted as before
    old = dict(slam, footprint={k: v for k, v in slam["footprint"].items() if k != "geo"})
    assert caption_carry.footprint_faces(_edl([old])["motion"][0], AR_9X16,
                                         caption_carry.face_geometry(zoomed))


def test_an_estimated_footprint_places_captions_until_the_render_measures_it(monkeypatch):
    est = _slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))
    est["footprint"].update(estimated=True, faces=[[0.25, 0.1, 0.75, 0.3]])
    edl = _edl([est])
    p = _plan(edl, _index())
    assert p.placed and p.report["slam"]["estimated"]
    # the render lane has a browser: it measures the estimate, keeping the
    # face zones the keep-out stored at this frame shape
    monkeypatch.setattr(motion_layer, "_FOOTPRINT_CACHE", {})
    monkeypatch.setattr(motion_engine, "probe", lambda jobs, times: [
        {"errors": [], "visible_frames": 4, "samples": 4, "bboxes": [[0, 0.1, 1, 0.3]],
         "ink": [[0.2, 0.14, 0.8, 0.26]] * 4, "cover": [[0.18, 0.12, 0.82, 0.28]] * 4}
        for _ in jobs])
    motion_layer.fill_footprints(edl, 1080, 1920)
    fp = edl["motion"][0]["footprint"]
    assert fp["box"] == [0.18, 0.12, 0.82, 0.28] and not fp.get("estimated")
    assert fp["faces"] == [[0.25, 0.1, 0.75, 0.3]] and fp["ar"] == AR_9X16
    # the measured box is off the caption band: nothing moves any more
    assert not _plan(edl, _index()).placed
    # a probe that cannot run keeps the estimate rather than nothing
    again = _edl([est])
    monkeypatch.setattr(motion_layer, "_FOOTPRINT_CACHE", {})

    def down(jobs, times):
        raise motion_engine.MotionRenderError("no browser")
    monkeypatch.setattr(motion_engine, "probe", down)
    motion_layer.fill_footprints(again, 1080, 1920)
    assert again["motion"][0]["footprint"]["estimated"] is True


def test_a_browserless_write_stores_an_estimate_and_says_so(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda *a, **k: None)
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.9, 3.0,
                                          params={"text": "*140*", "y": 0.7}, id="slam")
    item = ctx.latest_edl()["json"]["motion"][0]
    fp = item["footprint"]
    assert fp["estimated"] is True and fp["ar"] == pytest.approx(AR_9X16, abs=1e-3)
    # the face zones carry the stamp of the picture they were measured on
    assert fp["faces"] and fp["geo"] == caption_carry.face_geometry(ctx.latest_edl()["json"])
    assert "KEEP-OUT (estimated)" in out, out          # moved out of the button rail
    # the caption note over an estimated box says it is one
    on_band = _slam(1.9, 3.0, "*140*", box=(0.1, 0.62, 0.9, 0.82))
    on_band["footprint"]["estimated"] = True
    edl = _edl([on_band])
    notes = motion_tools._word_level_notes(edl, _index(), Timeline(edl["keep"]),
                                           edl["motion"][0], canvas=(1080, 1920))
    assert notes and "by its estimated box" in notes[0], notes


def test_the_keep_out_prices_the_caption_band_of_word_level_graphics():
    edl = _edl()
    ctx = _Ctx()
    ctx._edl = edl
    slam = {"id": "s", "template": "word_slam", "start": 1.9, "end": 3.0,
            "params": {"text": "*140*"}}
    # a speech template with mute_captions unset still has captions beside it,
    # and so has one asked to mute them (every heard word reaches the screen)
    assert motion_tools._caption_anchors(ctx, edl, slam)
    assert motion_tools._caption_anchors(ctx, edl, dict(slam, mute_captions=True)) == \
        motion_tools._caption_anchors(ctx, edl, slam)


def test_the_write_says_when_captions_must_stay_on_a_graphic_they_touch():
    # a counter (not a speech template) on the caption band with no clear
    # band left: the captions stay put, and the reply says they touch it
    big = (0.1, 0.45, 0.9, 0.85)
    edl = _edl([_counter(1.9, 3.0, "140", box=big)])
    notes = motion_tools._word_level_notes(edl, _index(faces=None), Timeline(edl["keep"]),
                                           edl["motion"][0], canvas=(1080, 1920))
    assert notes and "stay on their band and touch it" in notes[0], notes
    assert '"all we got was"' in notes[0], notes


def test_a_graphic_up_long_before_its_words_is_told_to_start_on_them():
    # the Thiel counter: up from "flying cars" (1.0 s) while "140" is only
    # said at 2.8 s; nothing clear of it is left, so the setup's captions
    # touch it — the reply offers starting it on its own words
    big = (0.1, 0.45, 0.9, 0.85)
    edl = _edl([_counter(1.0, 3.9, "140", box=big)])
    notes = motion_tools._word_level_notes(edl, _index(faces=None), Timeline(edl["keep"]),
                                           edl["motion"][0], canvas=(1080, 1920))
    assert notes and any("start it at 2.80s where its own words begin" in n
                         for n in notes), notes
    # a graphic that starts on its own words gets no such advice
    edl = _edl([_counter(2.75, 3.9, "140", box=big)])
    notes = motion_tools._word_level_notes(edl, _index(faces=None), Timeline(edl["keep"]),
                                           edl["motion"][0], canvas=(1080, 1920))
    assert not any("where its own words begin" in n for n in notes), notes
