"""Word-level caption muting and sound-off coverage (showcase judging, Oct 2026).

What is pinned here:
  1. A graphic with mute_captions unset hides only the spoken words it shows
     (case, punctuation, plurals, spelled numbers and *stars* folded); the
     other words stay captioned. A paraphrase that shares one word with the
     sentence does not punch a hole in it.
  2. Those captions move to a band clear of the graphic's drawn box and the
     face; with no clear band a speech-replacing template mutes them (and
     the write reply names the words), any other template leaves them be.
  3. mute_captions=true is the old whole-window mute; false keeps captions
     running but never repeats a number/hero word the graphic shows.
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


def _slam(start, end, text, kicker="", drawn=(0.1, 0.30, 0.9, 0.42), **kw):
    m = {"id": kw.pop("id", "slam"), "template": "word_slam", "start": start, "end": end,
         "params": {"text": text, "kicker": kicker}}
    if drawn:
        m["drawn"] = list(drawn)
    m.update(kw)
    return m


def _counter(start, end, value, label="", drawn=(0.3, 0.42, 0.7, 0.56), **kw):
    m = {"id": kw.pop("id", "num"), "template": "counter", "start": start, "end": end,
         "params": {"value": value, "label": label}}
    if drawn:
        m["drawn"] = list(drawn)
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
    assert _shown(edl, _index(words)) == ["all", "we", "got", "was", "characters."]


def test_a_paraphrase_sharing_one_word_does_not_punch_a_hole():
    rows = [{"text": "computer fonts were"}, {"text": "*GARBAGE*"}]
    words = [("Every", 0.1, 0.4), ("computer", 0.45, 0.9), ("to", 0.95, 1.05),
             ("date", 1.1, 1.5), ("has", 1.6, 1.8), ("used", 1.8, 2.0),
             ("garbage", 2.1, 2.6)]
    m = {"id": "hook", "template": "phrase_build", "start": 0.0, "end": 3.0,
         "params": {"rows": rows}, "drawn": [0.1, 0.06, 0.9, 0.24]}
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
         "params": {"rows": rows}, "drawn": [0.1, 0.06, 0.9, 0.3]}
    edl = _edl([m], words=words)
    assert _shown(edl, _index(words)) == ["aviation", "and", "the", "green",
                                          "revolution", "and"]


# ── 2. placement clear of the graphic ────────────────────────────────────

def test_words_it_does_not_show_stay_captioned_in_place_when_their_band_is_clear():
    edl = _edl([_slam(0.9, 2.0, "flying *cars*")])
    ix = _index()
    p = _plan(edl, ix)
    shown = [w["w"] for w in p.caption_words()]
    assert "flying" not in shown and "cars" not in shown
    assert {"they", "promised", "and", "all", "we", "got"} <= set(shown)
    assert not p.placed and not p.clamp_spans          # nothing collides with y≈0.74
    cues = motion_captions.cues(edl, ix, Timeline(edl["keep"]))
    assert all("z" not in c for c in cues)


def test_words_under_a_graphic_on_the_caption_band_move_clear_of_it_and_the_face():
    # the slam sits on the caption band (0.62-0.82); the face is high, so the
    # band between the face and the graphic is clear
    edl = _edl([_slam(1.9, 3.0, "*140*", drawn=(0.1, 0.62, 0.9, 0.82))])
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
    ass = _edl([_slam(1.9, 3.0, "*140*", drawn=(0.1, 0.62, 0.9, 0.82))], look=None)
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
    edl = _edl([_slam(1.9, 3.0, "*140*", drawn=big)])
    ix = _index(faces=None)                      # unmeasured -> talking-head prior
    p = _plan(edl, ix)
    room = sorted(p.words[i]["w"] for i, (_o, why) in p.hidden.items() if why == "room")
    assert room == ["all", "got", "was", "we"]
    assert p.report["slam"]["muted"]
    # a counter is not a speech-replacing template: its captions stay put
    edl = _edl([_counter(1.9, 3.0, "140", drawn=big)])
    p = _plan(edl, ix)
    assert not any(why == "room" for _o, why in p.hidden.values())
    assert p.report["num"]["kept"] and not p.clamp_spans


def test_an_unmeasured_box_keeps_the_old_behaviour():
    edl = _edl([_slam(1.9, 3.0, "*140*", drawn=None)])
    p = _plan(edl, _index())
    assert sorted(p.words[i]["w"] for i, (_o, why) in p.hidden.items() if why == "room") \
        == ["all", "got", "was", "we"]
    edl = _edl([_counter(1.9, 3.5, "140", drawn=None)])
    assert _shown(edl, _index()).count("140") == 0        # still no duplicate
    assert "got" in _shown(edl, _index())


# ── 3. explicit intent ───────────────────────────────────────────────────

def test_true_is_the_whole_window_and_false_keeps_captions_but_never_repeats_a_number():
    whole = _edl([_counter(1.9, 3.95, "140", label="characters", mute_captions=True)])
    assert captions.effective_caption_mutes(whole) == [[1.9, 3.95]]
    assert not {"got", "was", "140", "characters."} & set(_shown(whole, _index()))
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
    assert _shown(edl, _index(words)) == ["but", "it's", "to", "take"]
    # ...only when the slam's own word carries straight on from them
    edl = _edl([_slam(1.1, 2.4, "*plenty*", kicker="it's not quite been")], words=words)
    assert "not" in _shown(edl, _index(words))


# ── 4. timing against the graphic ────────────────────────────────────────

def test_a_caption_waits_for_an_occupying_graphic_to_clear_instead_of_touching_it():
    words = [("the", 0.1, 0.3), ("1960s", 0.3, 0.9), ("technology", 0.9, 1.5),
             ("meant", 1.5, 1.9), ("computers", 2.17, 2.7), ("but", 2.8, 3.0)]
    hook = {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.3,
            "params": {"text": "where did / *progress* go?"}, "mute_captions": True}
    edl = _edl([hook], words=words)
    cues = motion_captions.cues(edl, _index(words), Timeline(edl["keep"]))
    first = cues[0]
    assert first["w"][0]["t"].lower().startswith("computers")
    assert first["s"] >= 2.3 + motion_captions.CAPTION_LEAD_S - 1e-6
    items = motion_captions.items(validate_edl(edl, 8.0).model_dump(), _index(words),
                                  Timeline(edl["keep"]))
    assert items[0]["start"] >= 2.3 - 1e-6           # rendered ON the exit, not before


# ── 5. sound-off coverage ────────────────────────────────────────────────

def test_sound_off_gaps_name_the_words_the_cause_and_the_fix():
    tl = Timeline([[0.0, 8.0]])
    whole = _edl([_counter(0.9, 3.95, "140", label="characters", mute_captions=True)])
    gaps = caption_carry.sound_off_gaps(whole, _index(), tl)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["said"] == "flying cars and all we got"        # content words bound it
    assert g["owner"] == "num" and "mute_captions=true" in g["cause"]
    assert "unset" in g["fix"] and g["duration_s"] > caption_carry.SOUND_OFF_GAP_S
    # word level: every word is either captioned or on the graphic
    auto = _edl([_counter(0.9, 3.95, "140", label="characters")])
    assert caption_carry.sound_off_gaps(auto, _index(), tl) == []
    # a short whole-window mute over a single word is under the threshold
    short = _edl([_counter(2.8, 3.3, "99", mute_captions=True)])
    assert caption_carry.sound_off_gaps(short, _index(), tl) == []


def test_sound_off_gap_from_a_graphic_with_no_clear_band_and_a_manual_mute():
    tl = Timeline([[0.0, 8.0]])
    edl = _edl([_slam(0.9, 2.75, "*140*", drawn=(0.1, 0.45, 0.9, 0.85))])
    gaps = caption_carry.sound_off_gaps(edl, _index(faces=None), tl)
    assert gaps and gaps[0]["owner"] == "slam" and "caption band" in gaps[0]["fix"]
    manual = _edl()
    manual["caption_mutes"] = [[4.5, 6.2]]
    gaps = caption_carry.sound_off_gaps(manual, _index(), tl)
    assert gaps and "set_caption_mutes" in gaps[0]["cause"]


def test_sound_off_gaps_are_an_advisory_finding_never_a_repair():
    whole = _edl([_counter(0.9, 3.95, "140", label="characters", mute_captions=True)])
    rows = [r for r in quality_verifier.deterministic_findings(whole, _index())
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


def test_drawn_is_a_measurement_sanitized_or_dropped_never_rejected():
    ok = _edl([_counter(1.0, 2.0, "140", drawn=(-0.2, 0.4, 1.3, 0.6))])
    assert ok["motion"][0]["drawn"] == [0.0, 0.4, 1.0, 0.6]
    for bad in ([0.5, 0.5, 0.4, 0.6], [1, 2, 3], ["x", 0, 1, 1]):
        e = _edl([_counter(1.0, 2.0, "140", drawn=None)])
        e["motion"][0]["drawn"] = bad
        assert validate_edl(e, 8.0).model_dump()["motion"][0].get("drawn") is None
    legacy = _edl([_counter(1.0, 2.0, "140", drawn=None)])
    assert "drawn" not in validate_edl(legacy, 8.0).model_dump(exclude_none=True)["motion"][0]


def test_fill_drawn_measures_only_what_the_plan_needs_once(monkeypatch):
    calls = []

    def probe(jobs, times):
        calls.append(len(jobs))
        return [{"errors": [], "visible_frames": 4, "samples": 4,
                 "bboxes": [[0.0, 0.3, 1.0, 0.7]], "ink": [[0.2, 0.4, 0.8, 0.6]]}
                for _ in jobs]
    monkeypatch.setattr(motion_engine, "probe", probe)
    monkeypatch.setattr(motion_layer, "_DRAWN_CACHE", {})
    edl = _edl([_counter(1.0, 2.0, "140", drawn=None),
                _counter(3.0, 4.0, "7", drawn=None, id="whole", mute_captions=True),
                _counter(5.0, 6.0, "9", drawn=(0.1, 0.1, 0.2, 0.2), id="known")])
    motion_layer.fill_drawn(edl, 1080, 1920)
    by = {m["id"]: m.get("drawn") for m in edl["motion"]}
    assert by == {"num": [0.2, 0.4, 0.8, 0.6], "whole": None, "known": [0.1, 0.1, 0.2, 0.2]}
    again = _edl([_counter(1.0, 2.0, "140", drawn=None)])
    motion_layer.fill_drawn(again, 540, 960)            # same aspect: cached
    assert again["motion"][0]["drawn"] == [0.2, 0.4, 0.8, 0.6] and calls == [1]
    # no transcript captions: nothing to place, nothing measured
    bare = validate_edl(dict(default_edl(8.0), motion=[_counter(1.0, 2.0, "5", drawn=None)]),
                        8.0).model_dump()
    motion_layer.fill_drawn(bare, 1080, 1920)
    assert calls == [1]


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
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.9, 3.0,
                                          params={"text": "*140*"}, id="slam")
    assert out.startswith("EDL v1"), out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["drawn"] == [0.1, 0.62, 0.9, 0.82]          # ink, not the scrim's reach
    assert "Captions for the words it does not show move to y≈" in out, out
    assert "NOTE (captions)" not in out, out
    # a probe that cannot run drops a stale box rather than keeping it
    monkeypatch.setattr(motion_tools, "_probe_item", lambda *a, **k: None)
    motion_tools.set_motion_graphic(ctx, "slam", params={"text": "*one forty*"})
    assert "drawn" not in ctx.latest_edl()["json"]["motion"][0] or \
        ctx.latest_edl()["json"]["motion"][0]["drawn"] is None


def test_the_write_names_words_a_graphic_on_the_caption_band_must_mute(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_with((0.1, 0.45, 0.9, 0.85)))
    ctx = _Ctx(faces=None)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.0, 2.75,
                                          params={"text": "*rockets*"})
    assert "NOTE (captions)" in out and '"flying cars and all we got was"' in out, out
    assert "no caption band is clear of this graphic (it draws y 0.45-0.85)" in out, out
