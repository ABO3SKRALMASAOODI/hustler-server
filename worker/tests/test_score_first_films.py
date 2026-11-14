"""A customer's first film is a film, not the upload with captions (week-1112 lane K, 13 Nov).

What these hold: a wide still is shown whole and its label stands beside it; the hook title has a place or
the film opens on a page; display type is fitted to its seat before it is left out; labels that share a seat
are a list, and a label of a later beat takes the seat; the second caption voice reaches today's renderer;
a cold open keeps its captions; a cut from the person to a found picture lands on a breath; the last look is
told what each picture shows. Fixtures are synthetic Scores and index stubs. No customer media is here."""
import json

import score.compiler as S
from score import brief, lower, taste
from tests.test_score_compiler import BIN, wordless_index
from tests.test_score_taste import beats_over, comp, film, selfie, voice


def close_selfie(seconds=30.0):
    """A head that fills the frame: no place on the take holds display type."""
    return selfie(seconds, face=(0.2, 0.12, 0.8, 0.74))


def shots(c):
    return sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])


def bigs(c):
    return [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"]


# ------------------------------------------------------------------ a wide still is shown whole

def test_a_wide_still_cut_into_a_vertical_film_is_framed_on_the_plate_and_counts_as_a_picture(monkeypatch):
    monkeypatch.setenv("VALMERA_PACE", "0")       # round six fills the frame with it and travels it (test_score_round_six); this is the pace switched off
    idx = selfie(20.0)
    text = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", f"bin:{k}", "main"]} if k == 1 else {"pic": "main"}))
    c = comp(text, idx)
    s = next(i for i in shots(c) if i["id"] == "b02.s1")
    card = next(i for i in c.l1["items"] if i["k"] == "card" and i["id"] == "b02.s1.pic")
    assert s["pic"] == "plate" and s["framed"] == "bin:1" and card["pic"] == "bin:1" and card["f"] == s["f"]
    k = card["s"][-1]
    w, h = card["w"] * k, card["h"] * k
    assert 0.8 <= w <= 0.9 and card["w"] * card["s"][0] <= 0.9 + 1e-6                        # nearly the frame's width, inside the margins as it lands
    assert abs(h / w - (1080 / 1920) * (1067 / 1600)) < 0.01                                 # in its own shape: nothing of it is cropped
    assert c.meter["sources"]["found"] > 1.0 and c.meter["sources"]["pages"] == 0.0             # it holds the frame as a found picture
    assert c.meter["new_pictures10"] > 0 and c.meter["taste"]["supply"]["np"] == 2            # in, and back to the person


def test_a_written_crop_or_a_portrait_still_fills_the_frame_as_before():
    idx = selfie(20.0)
    tall = dict(BIN, **{"0": {"kind": "image", "path": "bin/0.jpg", "w": 1080, "h": 1920}})
    make = lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", {"src": f"bin:{k}", **({"crop": [0.3, 0.0, 0.7, 1.0]} if k == 1 else {})}, "main"]} if k in (0, 1) else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make)), idx, bin_items=tall)
    pics = {i["id"]: i["pic"] for i in shots(c)}
    assert pics["b01.s1"] == "bin:0" and pics["b02.s1"] == "bin:1"
    assert not [i for i in c.l1["items"] if i["k"] == "card"]


def test_a_label_for_a_framed_still_stands_on_the_plate_beside_it_in_its_own_ink_and_leaves_with_it(monkeypatch):
    monkeypatch.setenv("VALMERA_PACE", "0")
    idx = selfie(20.0)

    def make(k, a, z):
        if k != 1:
            return {"pic": "main"}
        return {"cut": [40, 45, 999], "pic": ["main", "bin:1", "main"],
                "do": [{"g": "chip", "t": "A NAME", "for": "b02.s1", "at": "b02.s1.in", "d": 90}]}
    c = comp(film(beats_over(idx, 4, make), system={"devices": ["chip"], "sounds": [], "seats": [[0.5, 0.3]]}), idx)
    s = next(i for i in shots(c) if i["id"] == "b02.s1")
    card = next(i for i in c.l1["items"] if i["id"] == "b02.s1.pic")
    chip = next(i for i in c.l1["items"] if i["id"] == "b02.chip")
    word = next(i for i in c.l1["items"] if i["id"] == "b02.chip.t")
    top = card["y"] - card["h"] * card["s"][-1] / 2
    assert chip["y"] + chip["h"] / 2 < top                              # above the picture, on the plate
    assert chip["f"][1] <= s["f"][1] and word["f"][1] <= s["f"][1]      # written for 3 s, it leaves with the picture
    assert S.luma(word["ink"]) < 0.3 and S.luma(chip["plate"]["c0"]) > 0.7     # dark word on its white label, over a dark page


# ------------------------------------------------------------------ the hook title has a place

def test_a_title_with_no_place_on_the_take_opens_the_film_on_a_page_for_a_second():
    idx = close_selfie(24.0)
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": [{"t": "THE CLAIM", "size": 0.06}, {"t": "said plainly.", "size": 0.03, "font": "serif-italic"}],
                                                   "for": f"w{a}", "at": "b01.in", "d": 70, "seat": [0.5, 0.14], "in": "settle"}]} if k == 0 else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make), system={"devices": ["big"], "sounds": [], "seats": [[0.5, 0.14]]}), idx)
    first = shots(c)[0]
    assert first["pic"] == "plate" and first["f"] == [0, S.HOOK_PAGE_F] and shots(c)[1]["pic"] == "main"
    words = bigs(c)
    assert {w["t"] for w in words} == {"THE CLAIM", "said plainly."} and all(w["f"][1] <= S.HOOK_PAGE_F for w in words)
    px = max(c.l1["voices"][w["v"]]["px"] for w in words)
    assert px >= 0.07 * 1920                                           # on its own page it stands at title size
    assert any(r["id"] == "b01.big" and r["did"] == "repaired" for r in c.meter["taste"]["rows"])
    assert not any(str(f).startswith("HOOK") for f in c.meter["findings"])


def test_a_later_line_with_no_place_on_the_take_is_left_out_and_the_meter_says_where_it_could_stand():
    idx = close_selfie(24.0)
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "LATER", "for": f"w{a}", "at": f"w{a}", "d": 40}]} if k == 2 else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make), system={"devices": ["big"], "sounds": [], "seats": [[0.5, 0.14]]}), idx)
    assert not bigs(c) and all(s["pic"] == "main" for s in shots(c))
    assert any(r["id"] == "b03.big" and "found picture or a page" in r["why"] for r in c.meter["taste"]["rows"])


def test_the_meter_says_when_the_first_second_is_the_upload_as_it_was():
    idx = selfie(20.0)
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "LATE", "for": f"w{a}", "at": f"w{a}", "d": 40}]} if k == 2 else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make)), idx)
    assert any(str(f).startswith("HOOK") for f in c.meter["findings"])
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "NOW", "for": f"w{a}", "at": "b01.in", "d": 40}]} if k == 0 else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make)), idx)
    assert not any(str(f).startswith("HOOK") for f in c.meter["findings"])


def test_display_over_a_found_picture_keeps_the_seat_the_score_wrote():
    idx = close_selfie(24.0)

    def make(k, a, z):
        if k != 1:
            return {"pic": "main"}
        return {"cut": [30, 50, 999], "pic": ["main", {"src": "bin:4"}, "main"],
                "do": [{"g": "big", "t": "ON IT", "for": "b02.s1", "at": "b02.s1.in", "d": 120, "seat": [0.5, 0.5], "size": 0.07}]}
    c = comp(film(beats_over(idx, 4, make), system={"devices": ["big"], "sounds": [], "seats": [[0.5, 0.5]]}), idx)
    s = next(i for i in shots(c) if i["id"] == "b02.s1")
    end = max(i["f"][1] for i in shots(c) if i["id"].startswith("b02.s1") and i["pic"] == "bin:4")      # the picture, through every framing it is cut into
    w = bigs(c)
    assert s["pic"] == "bin:4" and len(w) == 1 and w[0]["f"][1] <= end and 0.4 <= w[0]["y"] <= 0.6


# ------------------------------------------------------------------ fitted before it is left out

def test_a_ladder_written_under_the_floor_or_taller_than_its_seat_is_fitted_not_dropped():
    idx = selfie(20.0)
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": [{"t": "NAME", "size": 0.06}, {"t": "est fini ?", "size": 0.024, "font": "serif-italic"}],
                                                   "for": f"w{a}", "at": "b01.in", "d": 60}]} if k == 0 else {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make)), idx)
    words = bigs(c)
    assert words and min(c.l1["voices"][w["v"]]["px"] for w in words) >= S.READ_FLOOR * 1920 - 1
    ctx = type("C", (), {"W": 1080, "H": 1920, "taste_rows": []})()
    rows = [{"t": "BIG WORD", "font": "Anton-Regular.ttf", "px": 160, "track": -3, "ink": None, "sized": True},
            {"t": "a small line", "font": "InstrumentSerif-Italic.ttf", "px": 70, "track": 0, "ink": None, "sized": True}]
    out = S.lines_that_read(ctx, "b01.big", [dict(r) for r in rows], 0.8, 0.09)
    assert out and sum(l["px"] for l in out) * 1.02 / 1920 <= 0.09 + 1e-6 and min(l["px"] for l in out) >= S.READ_FLOOR * 1920 - 1
    one = S.lines_that_read(ctx, "b01.big", [dict(r) for r in rows], 0.8, 0.045)
    assert [l["t"] for l in one] == ["BIG WORD"]                       # a seat for one line keeps the line that matters
    assert S.lines_that_read(ctx, "b01.big", [dict(r) for r in rows], 0.8, 0.02) is None


# ------------------------------------------------------------------ labels

def test_labels_named_on_one_seat_in_one_beat_stand_together_as_a_list():
    idx = selfie(20.0)
    words = ["ONE", "TWO", "THREE", "FOUR"]

    def make(k, a, z):
        if k != 0:
            return {"pic": "main"}
        return {"pic": "main", "do": [{"g": "chip", "t": t, "for": f"w{a + j}", "at": f"w{a + j}..w{z}.e", "size": 0.026} for j, t in enumerate(words)]}
    c = comp(film(beats_over(idx, 3, make), system={"devices": ["chip"], "sounds": [], "seats": []}), idx)
    cards = [i for i in c.l1["items"] if i["k"] == "card" and i["pic"] == "plate"]
    assert len(cards) == 4
    rects = [[i["x"] - i["w"] / 2, i["y"] - i["h"] / 2, i["x"] + i["w"] / 2, i["y"] + i["h"] / 2] for i in cards]
    for n, r in enumerate(rects):
        assert not any(S.rects_touch(r, q) for q in rects[n + 1:])     # none on another
    assert max(r[3] for r in rects) - min(r[1] for r in rects) < 0.2   # a list, not a column down the frame
    assert not any(r["did"] == "thinned" for r in c.meter["taste"]["rows"])


def test_on_wordless_footage_a_label_on_each_shot_is_one_track_at_one_seat_and_stands_whole():
    idx = wordless_index(10.0)
    idx["shots"] = [{"id": 1, "start": 0.0, "end": 10.0}]
    beats = [{"k": "b", "id": f"b{n + 1:02d}", "at": f"@{t0}..@{t1}", "job": "x", "pic": f"main@{t0}-{t1}",
              "do": [{"g": "chip", "t": f"0{n + 1} step", "for": f"b{n + 1:02d}.s0", "at": f"b{n + 1:02d}.in", "d": 70, "seat": [0.5, 0.7]}]}
             for n, (t0, t1) in enumerate([(6.0, 7.5), (0.2, 1.9), (2.2, 3.9), (4.2, 5.8), (8.0, 9.8)])]
    c = comp(film(beats, system={"devices": ["chip"], "sounds": [], "seats": [[0.5, 0.7]]}), idx)
    cards = sorted((i for i in c.l1["items"] if i["k"] == "card"), key=lambda i: i["f"][0])
    assert len(cards) == 5                                             # five labels, none thinned for the count, none unearned
    assert max(i["y"] for i in cards) - min(i["y"] for i in cards) < 0.02
    for p, q in zip(cards, cards[1:]):
        assert p["f"][1] <= q["f"][0]                                  # the next label takes the seat: one at a time


# ------------------------------------------------------------------ the second caption voice

def test_a_chunk_whose_words_are_all_starred_is_drawn_in_the_second_voice():
    idx = selfie(12.0)
    n = len(idx["words"])
    text = film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main", "type": "0-1|2*|3-4|5* 6|7* 8*"}],
                dna={"pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.044"}, "cap": {"layout": "chunk", "in": "settle"}})
    c = comp(text, idx)
    rep = {}
    caps = lower.lower_captions(c.l1, rep)
    by = {x["text"]: x["style"] for x in caps}
    second = [by["palavra2"], by["palavra7"]]              # round six: a chunk is written word by word, and its entrance plays on its first word
    first = [by["palavra0 palavra1"], by["palavra3 palavra4"], by["palavra5 palavra6"]]       # a star inside a mixed chunk changes nothing
    assert all(s["font"] == "DM Serif Display" and s["animation"] == "pop" for s in second)
    assert all(s["font"] == "Inter Display Bold" and s["animation"] == "none" for s in first)
    assert min(s["size_scale"] for s in second) > max(s["size_scale"] for s in first)
    assert rep["captions_second_voice"] == 2


def test_a_cold_open_keeps_its_captions_and_the_body_keeps_its_own():
    idx = selfie(24.0)
    sents = idx["sentences"]
    late, rest, after = sents[3], sents[:3], sents[4:]
    beats = [{"k": "b", "id": "b01", "w": f"{late['wi0']}-{late['wi1']}", "job": "x", "pic": "main", "type": f"{late['wi0']}-{late['wi0'] + 2}|{late['wi0'] + 3}-{late['wi1']}"},
             {"k": "b", "id": "b02", "w": f"{rest[0]['wi0']}-{rest[-1]['wi1']}", "job": "x", "pic": "main", "type": "cap:3"},
             {"k": "b", "id": "b03", "w": f"{after[0]['wi0']}-{after[-1]['wi1']}", "job": "x", "pic": "main", "type": "cap:3"}]
    c = comp(film(beats), idx)
    edl, _loc, rep = lower.lower(c.l1, "/tmp")
    body = sum(k[1] - k[0] for k in edl["keep"])
    assert rep["out_of_order_spans"] == 1 and body > 0.6 * c.l1["dur_f"] / 30         # the long run in source order is the base
    assert edl["inserts"][0]["at_output_s"] == 0.0 and edl["inserts"][0]["source_start_s"] > 5
    as_text = [t for t in edl["texts"] if t["id"].startswith("tx.cap.")]
    assert rep["captions_as_text"] == len(as_text) == 2 and max(t["start"] for t in as_text) < edl["inserts"][0]["duration_s"]
    assert all(t["font"] == "Inter Display Bold" and t["x"] == 0.5 for t in as_text)
    k0, k1 = edl["keep"][0][0], edl["keep"][-1][1]
    assert edl["captions"] and all(k0 - 0.2 <= x["start"] <= k1 + 0.2 for x in edl["captions"])     # every caption is on the base's own seconds


# ------------------------------------------------------------------ on the breath

def test_a_cut_from_the_person_to_a_found_picture_lands_on_a_breath_near_it():
    idx = selfie(20.0)
    c = comp(film(beats_over(idx, 2, lambda k, a, z: {"cut": [70, 45, 999], "pic": ["main", "bin:4", "main"]} if k == 0 else {"pic": "main"})), idx, voice(20.0))
    cut = next(i for i in shots(c) if i["id"] == "b01.s1")["f"][0]
    fps = 30
    wf = sorted((round(w["t0"] * fps), round(w["t1"] * fps)) for w in idx["words"])
    gaps = [(p[1], q[0]) for p, q in zip(wf, wf[1:]) if q[0] - p[1] >= S.BREATH_MIN_F]
    near = [g for g in gaps if abs((g[0] + g[1]) / 2 - 70 - c.l1["spine"][0]["f"][0]) <= S.BREATH_REACH_F + 14]
    if near:                                                           # this fixture's voice has a breath within reach
        assert any(g[0] - 14 <= cut <= g[1] + 14 for g in near)
    assert abs(cut - 70) <= S.BREATH_REACH_F                           # and never further than 0.4 s from where the Score put it


# ------------------------------------------------------------------ the last look

def test_the_look_is_told_what_a_found_picture_shows_and_what_a_title_page_is(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_PACE", "0")
    idx = close_selfie(24.0)

    def make(k, a, z):
        if k == 0:
            return {"pic": "main", "do": [{"g": "big", "t": "THE CLAIM", "for": f"w{a}", "at": "b01.in", "d": 60}]}
        if k == 1:
            return {"cut": [30, 60, 999], "pic": ["main", "bin:1", "main"]}
        return {"pic": "main"}
    c = comp(film(beats_over(idx, 4, make), system={"devices": ["big"], "sounds": [], "seats": []}), idx)
    (tmp_path / "bin.json").write_text(json.dumps({"items": [{"id": 1, "what": "Mark Webber: his Red Bull on track"}]}))
    whats = taste.bin_whats(str(tmp_path))
    assert whats == {"bin:1": "Mark Webber: his Red Bull on track"}
    s = next(i for i in shots(c) if i["id"] == "b02.s1")
    rows = taste.on_screen(c.l1, (s["f"][0] + 8) / 30, sorted(taste.look_ids(c), key=len, reverse=True), whats)
    assert any("shown whole on a dark page, showing Mark Webber: his Red Bull on track" in r for r in rows)
    assert "b02.s1" in taste.look_ids(c)
    rows = taste.on_screen(c.l1, 0.3, sorted(taste.look_ids(c), key=len, reverse=True), whats)
    assert any("opening title page" in r for r in rows) and any("THE CLAIM" in r for r in rows)
    # round five: a metaphor that lands stands (the ban is gone); what never stands is the generic library scene
    assert "cutaway" in taste.LOOK_SYSTEM and "generic library scene" in taste.LOOK_SYSTEM and "association" in taste.LOOK_SYSTEM


def test_a_printed_text_box_that_lies_on_a_head_is_not_print():
    idx = selfie(12.0, face=(0.3, 0.3, 0.7, 0.6))
    for r in idx["spatial"]["samples"]:
        r["text"] = [[0.4, 0.4, 0.6, 0.46], [0.02, 0.9, 0.3, 0.95]]    # one on the face, one a real sign
    n = len(idx["words"])
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main"}]), idx)
    ctx = S.compile_once(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main"}]), idx, None, BIN, None, None, "t", False, None, {}, True, {}, ({}, {}, None))
    boxes, _ = S.seen_rows(ctx, "main", 3.0)
    assert [0.02, 0.9, 0.3, 0.95] in boxes and [0.4, 0.4, 0.6, 0.46] not in boxes and c is not None


# ------------------------------------------------------------------ doubtful words, empty pages

def test_words_the_transcript_doubts_are_not_captioned():
    idx = selfie(8.0)
    idx["words"] = idx["words"][:8]
    idx["sentences"] = [s for s in idx["sentences"] if s["wi1"] < 8] or [{"id": "s1", "t0": idx["words"][0]["t0"], "t1": idx["words"][-1]["t1"], "wi0": 0, "wi1": 7, "text": "", "speaker": 0}]
    idx["sentences"][-1]["wi1"] = 7
    for w in idx["words"]:
        w["confidence"] = 0.64
    text = film([{"k": "b", "id": "b01", "w": "0-7", "job": "x", "pic": "main", "type": "0-2|3-5|6-7"}])
    c = comp(text, idx)
    assert not [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "cap"]
    assert any(r["id"] == "cap" and "0.64" in r["why"] for r in c.meter["taste"]["rows"])
    for w in idx["words"]:
        w["confidence"] = 0.95
    c = comp(text, idx)
    assert [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "cap"]


def test_a_page_left_with_nothing_on_it_gives_the_frame_back_to_the_take():
    idx = selfie(20.0)

    def make(k, a, z):
        if k != 3:
            return {"pic": "main"}
        return {"cut": [999, 30], "pic": ["main", {"plate": "#101014"}],
                "do": [{"g": "big", "t": "THE END", "for": "b04.s1", "at": "b04.s1.in", "d": 30}]}
    text = film(beats_over(idx, 4, make), system={"devices": ["big"], "sounds": [], "seats": []})
    c = comp(text, idx)
    page = next(i for i in shots(c) if i["id"] == "b04.s1")
    assert page["pic"] == "plate" and page["f"][1] - page["f"][0] == 30 and any(w["t"] == "THE END" for w in bigs(c))   # 999 is "what is left", then the page
    cut = comp(taste.cut_text(text, ["b04.big"]), idx)
    assert not bigs(cut) and all(s["pic"] == "main" for s in shots(cut))        # no second of black where the line was
    assert any(r["did"] == "repaired" and "nothing on it" in r["why"] for r in cut.meter["taste"]["rows"])


def test_a_fill_length_takes_what_the_written_lengths_leave():
    assert S.explicit_lengths(81, [999, 30]) == [51, 30]
    assert S.explicit_lengths(300, [60, 999, 45, 999]) == [60, 97, 45, 98]
    assert S.explicit_lengths(100, [46, 999]) == [46, 54]
    assert S.explicit_lengths(100, [40, 40]) == [40, 60]                       # no fill: the last picture takes what is left, as before
    assert S.explicit_lengths(50, [30, 999, 30]) == [30, 20]                   # no room for a fill: the written lengths stand


def test_a_word_on_the_films_last_frame_does_not_stop_the_compile():
    """p5554: a counter step landed on the frame the film ends on, and the ink walk never advanced."""
    idx = wordless_index(10.0)
    beats = [{"k": "b", "id": "b01", "at": "@0.0..@4.0", "job": "x", "pic": "main"},
             {"k": "b", "id": "b02", "at": "@4.0..@9.9", "job": "x", "pic": "main",
              "do": [{"g": "counter", "from": 1, "to": 3, "for": "turn", "at": "@8.0..@9.9", "step": 1, "fmt": "{n}/4", "size": 0.07}]}]
    c = comp(film(beats, system={"devices": ["counter"], "sounds": [], "seats": []}), idx)
    assert all(i["f"][1] <= c.l1["dur_f"] for i in c.l1["items"])


def test_the_opening_title_of_wordless_footage_is_earned_by_the_film_starting():
    idx = wordless_index(10.0)
    beats = [{"k": "b", "id": "b01", "at": "@6.0..@8.0", "job": "x", "pic": "main",
              "do": [{"g": "big", "t": "THE TITLE", "for": "@7.3", "at": "b01.in", "d": 40}]},
             {"k": "b", "id": "b02", "at": "@0.5..@5.0", "job": "x", "pic": "main",
              "do": [{"g": "big", "t": "MIDDLE", "for": "@3.3", "at": "@3.3", "d": 40}]}]
    c = comp(film(beats + [{"k": "b", "id": "b03", "at": "@8.0..@9.9", "job": "x", "pic": "main"}], system={"devices": ["big"], "sounds": [], "seats": []}), idx)
    said = " ".join(w["t"] for w in bigs(c))
    assert "TITLE" in said and "MIDDLE" not in said                    # the opening stands; a line in the middle still needs its event


def test_a_silent_beat_the_look_emptied_goes_with_what_stood_on_it():
    idx = selfie(12.0)
    n = len(idx["words"])
    beats = [{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main"},
             {"k": "b", "id": "b02", "f": 36, "job": "x", "pic": {"plate": "#101014"},
              "do": [{"g": "big", "t": "THE END", "for": "turn", "at": "b02.in", "d": 36}]}]
    text = film(beats, system={"devices": ["big"], "sounds": [], "seats": []})
    c = comp(text, idx)
    assert shots(c)[-1]["pic"] == "plate" and bigs(c)
    assert taste.silent_beats_emptied(text, ["b02.big"]) == ["b02"] and taste.silent_beats_emptied(text, ["b01.big"]) == []
    cut = comp(taste.cut_text(text, ["b02.big"]), idx)
    assert cut.l1["dur_f"] == c.l1["dur_f"] - 36 and all(s["pic"] == "main" for s in shots(cut))     # no second of black at the end


def test_a_film_that_sets_no_captions_may_seat_its_title_in_the_caption_band():
    from tests.test_score_compiler import talk_index

    def rooms(idx):
        n = len(idx["words"])
        ctx = S.compile_once(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main"}]), idx, None, BIN, None, None, "t", False, None, {}, True, {}, ({}, {}, None))
        S.film_seats(ctx)
        return taste.captions_off(ctx), max(r[1] for r in ctx.seat_rooms)
    spoken = talk_index(12.0, faces=False)
    off, tall = rooms(spoken)
    assert not off
    noise = talk_index(12.0, faces=False)
    for w in noise["words"]:
        w["confidence"] = 0.5
    off2, tall2 = rooms(noise)
    assert off2 and tall2 > tall + 0.05                                   # the band the caption would hold is ground for a title


def test_type_for_a_doubtful_transcript_is_briefed_in_english():
    idx = selfie(8.0)
    idx["words"] = idx["words"][:3]
    assert brief.doubtful_words(idx)
    out = brief.scout_part({"scope": "film", "lang": "fi", "asks": [], "about": "x"}, idx)
    assert "`en`" in out[1] and "`fi`" not in out[1]
    idx = selfie(8.0)
    assert not brief.doubtful_words(idx) and "`fi`" in brief.scout_part({"scope": "film", "lang": "fi", "asks": []}, idx)[1]


# ------------------------------------------------------------------ round three: type that stands on a clip

def clip_perc(seconds=8.0, head_from=4.5, db=-25.0):
    """A clip of two shots: a firework with nobody in it, then a man whose head is large in the frame."""
    samples = []
    for k in range(int(seconds * 2)):
        t = 0.25 + 0.5 * k
        samples.append({"t": t, "face": [0.25, 0.2, 0.75, 0.6] if t >= head_from else None, "persons": int(t >= head_from)})
    return {"samples": samples, "shots": [{"id": 1, "t": [0.0, head_from]}, {"id": 2, "t": [head_from, seconds]}],
            "audio": {"step": 0.5, "rms_db": [db] * int(seconds * 2 + 2), "mean_db": db, "onsets": []}}


def clip_film(title_seat=(0.5, 0.3), snd=True):
    b1 = {"k": "b", "id": "b01", "at": "@0.2..@3.0", "job": "x", "pic": "main@0.2-3.0",
          "do": [{"g": "big", "t": [{"t": "TONIGHT", "size": 0.09}, {"t": "the fire goes up", "size": 0.036, "font": "serif-italic"}],
                  "for": "b01.s0", "at": "b01.in", "d": 54, "seat": list(title_seat)}]}
    if snd:
        b1["snd"] = [{"s": "impact_1", "for": "b01.big", "db": -12}]
    return film([b1, {"k": "b", "id": "b02", "at": "@4.6..@7.8", "job": "x", "pic": "main@4.6-7.8"}],
                system={"devices": ["big"], "sounds": ["impact"], "seats": [list(title_seat)]})


def test_a_title_on_a_clip_of_several_shots_is_seated_for_the_seconds_it_stands():
    idx = wordless_index(8.0)
    c = comp(clip_film(), idx, clip_perc())
    title = next(w for w in bigs(c) if w["t"] == "TONIGHT")
    px = c.l1["voices"][title["v"]]["px"]
    assert px >= 0.085 * 1280                                           # at the size it was written, not at the reading floor
    assert 0.2 <= title["y"] <= 0.45 and title["f"][1] <= 60            # in the open ground of its own shot, near the seat written
    assert any(r["id"] == "b01.big" and r["did"] == "repaired" and "seated for the" in r["why"] for r in c.meter["taste"]["rows"])
    # the same title while the head is on screen has no such ground: it is not set across the face
    late = film([{"k": "b", "id": "b01", "at": "@4.6..@7.8", "job": "x", "pic": "main@4.6-7.8",
                  "do": [{"g": "big", "t": [{"t": "TONIGHT", "size": 0.09}], "for": "b01.s0", "at": "b01.in", "d": 54, "seat": [0.5, 0.4]}]}],
                system={"devices": ["big"], "sounds": [], "seats": [[0.5, 0.4]]})
    c2 = comp(late, idx, clip_perc())
    for w in bigs(c2):
        v = c2.l1["voices"][w["v"]]
        top, bottom = w["y"] - 0.8 * v["px"] / 1280, w["y"]
        assert bottom < 0.2 - 0.6 * 0.4 + 0.02 or top > 0.6 + 0.15 * 0.4 - 0.02 or w.get("on") is None      # above the hair or under the chin


def test_a_text_box_the_next_sample_does_not_find_again_is_not_print():
    idx = wordless_index(6.0)
    idx["spatial"] = {"samples": [{"t": 0.5 * k, "text": [[0.1 + 0.07 * k, 0.2 + 0.05 * (k % 3), 0.3 + 0.07 * k, 0.26 + 0.05 * (k % 3)]], "faces": []} for k in range(12)]}
    ctx = S.compile_once(film([{"k": "b", "id": "b01", "at": "@0.0..@5.9", "job": "x", "pic": "main"}]), idx, None, BIN, None, None, "t", False, None, {}, True, {}, ({}, {}, None))
    assert S.seen_rows(ctx, "main", 2.0)[0] == []                       # sparks: a box per sample, never twice in one place
    for r in idx["spatial"]["samples"]:
        r["text"].append([0.1, 0.7, 0.9, 0.78])                          # a caption the upload prints: there in every sample
    ctx = S.compile_once(film([{"k": "b", "id": "b01", "at": "@0.0..@5.9", "job": "x", "pic": "main"}]), idx, None, BIN, None, None, "t", False, None, {}, True, {}, ({}, {}, None))
    assert [0.1, 0.7, 0.9, 0.78] in S.seen_rows(ctx, "main", 2.0)[0]


def test_display_type_over_a_picture_is_light_with_the_edge_unless_the_ground_is_bright_throughout(monkeypatch):
    import numpy as np
    idx = wordless_index(8.0)

    def ground(left, right):
        arr = np.zeros((24, S.THUMB_H, S.THUMB_W), dtype="float32")
        arr[:, :, : S.THUMB_W // 2], arr[:, :, S.THUMB_W // 2:] = left, right
        monkeypatch.setattr(S, "thumbs", lambda ctx, pic: arr)
        return [w for w in bigs(comp(clip_film(snd=False), idx, clip_perc())) if w["f"][0] < 60]
    mixed = ground(0.92, 0.08)                                           # a pale step and black trousers under one title
    assert mixed and all(S.luma(w["ink"]) > 0.5 and w.get("edge") for w in mixed)
    assert len({w["ink"] for w in mixed}) == 1                           # both lines of the ladder in one ink
    bright = ground(0.95, 0.93)
    assert bright and all(S.luma(w["ink"]) < 0.3 and not w.get("edge") for w in bright)
    dark = ground(0.06, 0.1)
    assert dark and all(S.luma(w["ink"]) > 0.5 and not w.get("edge") for w in dark)


def test_a_placed_sound_on_a_film_with_no_voice_is_heard_over_its_track():
    idx = wordless_index(8.0)
    c = comp(clip_film(), idx, clip_perc(db=-25.0))
    table, _ = S.sound_table()
    hit = float(table["impact_1"].get("hit_lufs", -15))
    snd = c.l1["sound"]
    assert len(snd) == 1 and abs(snd[0]["db"] - max(-36, min(-2, -25.0 + 3.0 - hit))) < 0.11       # 3 dB over what the track plays there
    # a talking take keeps its sound under the voice, as before
    talk = selfie(20.0)
    make = lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "ONE WORD", "for": f"w{a}", "at": f"w{a}", "d": 30}], "snd": [{"s": "impact_1", "for": "b01.big", "db": -6}]} if k == 0 else {"pic": "main"}
    c2 = comp(film(beats_over(talk, 3, make), system={"devices": ["big"], "sounds": ["impact"], "seats": [[0.5, 0.75]]}), talk, voice(20.0, db=-18.0))
    for s in c2.l1["sound"]:
        assert s["db"] <= -18.0 - 7.0 - hit + 0.11          # class T: 7 dB under the voice since round five


def test_the_brief_has_the_open_ground_shot_by_shot_for_a_clip():
    idx = wordless_index(8.0)
    ctx = S.compile_once(film([{"k": "b", "id": "b01", "at": "@0.0..@7.9", "job": "x", "pic": "main"}]), idx, None, BIN, None, S.faces_from(clip_perc()), "t", False, None, clip_perc(), True, {}, ({}, {}, None))
    rows = S.shot_grounds(ctx)
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][2] and max(h for _, h in rows[0][2]) >= 0.3           # the firework: the frame is open
    assert all(not (0.2 - 0.25 < seat[1] < 0.66) for seat, _ in rows[1][2])      # the man: only above his hair or under his chin
    one = S.compile_once(film([{"k": "b", "id": "b01", "w": "0-5", "job": "x", "pic": "main"}]), selfie(12.0), None, BIN, None, None, "t", False, None, {}, True, {}, ({}, {}, None))
    assert S.shot_grounds(one) == []                                     # one take: the film's seat is the answer


def test_a_counter_keeps_the_seat_its_first_step_was_given():
    idx = wordless_index(8.0)
    text = film([{"k": "b", "id": "b01", "at": "@0.2..@4.0", "job": "x", "pic": "main@0.2-4.0",
                  "do": [{"g": "counter", "from": 0, "to": 4, "for": "b01.s0", "at": "b01.in..b01.out", "fmt": "{n}/4 DOWN", "size": 0.06, "seat": [0.5, 0.3]}]},
                 {"k": "b", "id": "b02", "at": "@4.6..@7.8", "job": "x", "pic": "main@4.6-7.8"}],
                system={"devices": ["counter"], "sounds": [], "seats": [[0.5, 0.3]]})
    c = comp(text, idx, clip_perc())
    steps = [w for w in bigs(c) if "DOWN" in w["t"]]
    assert len(steps) > 4
    assert len({round(w["y"], 3) for w in steps}) == 1                   # one place for the whole count, not a new one each step
    assert sum(1 for r in c.meter["taste"]["rows"] if "seated for the" in r["why"]) <= 1


def test_type_for_a_noise_transcript_follows_what_the_frames_print_not_english_by_default():
    idx = selfie(8.0)
    idx["words"] = idx["words"][:3]
    line = brief.scout_part({"scope": "film", "lang": "fi", "asks": [], "about": "x"}, idx)[1]
    assert "language the frames themselves print" in line and "not known" in line and "not their words" in line
    silent = brief.scout_part({"scope": "film", "lang": "en", "asks": []}, wordless_index(5.0))[1]
    assert "language the frames themselves print" in silent              # no word spoken and nothing typed: not English by default
    asked = brief.scout_part({"scope": "film", "lang": "es", "asks": [{"id": "a1", "said": "x", "probe": "caption"}]}, wordless_index(5.0))[1]
    assert "`es`" in asked                                               # a typed request has a language


def test_the_last_line_of_a_film_with_no_voice_is_earned_by_the_film_ending():
    idx = wordless_index(8.0)

    def text(d):
        return film([{"k": "b", "id": "b01", "at": "@0.2..@3.0", "job": "x", "pic": "main@0.2-3.0"},
                     {"k": "b", "id": "b02", "at": "@4.0..@7.0", "job": "x", "pic": "main@4.0-7.0",
                      "do": [{"g": "big", "t": "AND HIM", "for": "b02.s0", "at": "@3.7", "d": d, "seat": [0.5, 0.3]}]}],
                    system={"devices": ["big"], "sounds": [], "seats": [[0.5, 0.3]]})
    c = comp(text(80), idx, clip_perc(head_from=99.0))
    assert "AND HIM" in " ".join(w["t"] for w in bigs(c))                # lands 0.8 s into the last shot and holds to the end: the close
    c = comp(text(20), idx, clip_perc(head_from=99.0))
    assert "AND HIM" not in " ".join(w["t"] for w in bigs(c))            # gone before the end: a line in the middle of a shot, answering nothing


def test_the_brief_says_where_the_upload_prints_words_of_its_own(tmp_path, monkeypatch):
    idx = wordless_index(11.0)
    per = dict(clip_perc(11.0, head_from=99.0), W=720, H=1280, dur=11.0,
               text=[{"read": "", "box": [0.0, 0.19, 0.91, 0.33], "t": [4.5, 9.5], "seen": 9}, {"read": "", "box": [0.4, 0.5, 0.5, 0.52], "t": [1.0, 2.0], "seen": 1}])
    monkeypatch.setattr(S, "take_seats", lambda work, index=None: (0.56, [[0.5, 0.6]], [[0.72, 0.2]], []))
    out = " ".join(brief.seats_part(str(tmp_path), idx, per))
    assert "The upload prints words of its own (y 0.19-0.33 from 4.5 to 9.5 s)" in out and "never says them again" in out
    per["text"] = []
    assert "prints words of its own" not in " ".join(brief.seats_part(str(tmp_path), idx, per))
