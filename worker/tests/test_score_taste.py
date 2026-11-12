"""The taste governor of the Director's film (worker/score/taste.py, week-1112 TASTE.md).

A device exists only where an event earns it, and its count follows the supply of new pictures. The
fixtures are synthetic Scores and index stubs with the shapes measured on the films the owner called
childish (week-1112/taste/films.json): p5591, a 32.8 s selfie with 22 zoom steps, 19 sounds and 12
titles; p5565, 87 sounds on a video call. No customer media is here. Every assertion is a number."""
import json
import os
import subprocess

import pytest

import score.compiler as S
from score import brief, direct, taste
from tests.test_score_compiler import BIN, talk_index, wordless_index

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")


def selfie(seconds=32.8, face=(0.36, 0.2, 0.64, 0.4)):
    """One take, a close face, a voice with a level."""
    idx = talk_index(seconds, faces=False)
    idx["spatial"]["samples"] = [{"t": float(k), "faces": [list(face)], "mean_luma": 90.0} for k in range(int(seconds) + 1)]
    return idx


def voice(seconds, db=-18.0):
    return {"audio": {"step": 0.5, "rms_db": [db] * int(seconds * 2 + 2), "mean_db": db, "onsets": []}}


def film(beats, system=None, dna=None, film_=None, extra=()):
    head = [json.dumps({"k": "treat", "read": "x", "story": "x", **({"system": system} if system else {})}),
            json.dumps({"k": "film", "v": 1, "fps": 30, "frame": "9:16", "spine": "main", "band": "P1", "seed": 3, **(film_ or {})}),
            json.dumps({"k": "dna", "plate": "#101014", "ink": "#FFFFFF", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"},
                        "big": "condensed", "seat": [0.5, 0.62], **(dna or {})})]
    return "\n".join(head + [json.dumps(x) for x in extra] + [json.dumps(b, ensure_ascii=False) for b in beats]) + "\n"


def beats_over(idx, n, make):
    """n beats that cover every word, each starting where a sentence starts."""
    sents = idx["sentences"]
    per = max(1, len(sents) // n)
    out = []
    for k in range(0, len(sents), per):
        grp = sents[k:k + per]
        a, z = grp[0]["wi0"], grp[-1]["wi1"]
        b = {"k": "b", "id": f"b{len(out) + 1:02d}", "w": f"{a}-{z}", "job": "x", "type": f"{a}-{min(z, a + 2)}"}
        b.update(make(len(out), a, z))
        out.append(b)
    return out


def comp(text, idx, perc=None, **kw):
    kw.setdefault("bin_items", BIN)
    c = S.compile_score(text, index=idx, sid="taste", perception=perc or {}, **kw)
    assert not S.validate_l1(c.l1), S.validate_l1(c.l1)[:4]
    return c


def camera_steps(c):
    """Hard changes of scale between two shots of the take that follow each other."""
    shots = sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    n = 0
    for p, q in zip(shots, shots[1:]):
        if p.get("pic") == q.get("pic") == "main":
            a, b = S._crop_end(p), S._crop_start(q)
            if abs((a[2] - a[0]) / (b[2] - b[0]) - 1) >= 0.04:
                n += 1
    return n


def p5591_shape(idx):
    """Every beat crops the take on a pulse, lands a title, a chip and a flash, and plays two sounds."""
    def make(k, a, z):
        return {"cut": "pulse 0.5", "pic": [{"src": "main", "zoom": 1.3, "for": "turn"}, {"src": "main"}, {"src": "main", "crop": "face", "for": f"w{a + 1}"}],
                "do": [{"g": "big", "t": f"TITLE {k}", "for": f"w{a}", "at": f"w{a}", "d": 12, "seat": [0.5, 0.1 + 0.07 * (k % 9)]},
                       {"g": "chip", "t": f"chip {k}", "for": f"w{a + 2}", "at": f"w{a + 2}", "seat": [0.2 + 0.07 * (k % 9), 0.5]},
                       {"g": "flash", "for": "turn", "at": f"w{a}", "f": 2}],
                "snd": [{"s": ("impact_1", "whoosh_soft_1", "pop_1", "ding_1", "click_1")[k % 5], "at": f"w{a + 3}", "for": f"w{a + 3}", "db": -4},
                        {"s": ("riser_2", "swish_1", "shutter_1", "glitch_1")[k % 4], "for": f"b{k + 1:02d}.big", "db": -4}]}
    return beats_over(idx, 12, make)


# ------------------------------------------------------------------ supply and allowance

def test_the_allowance_is_the_table_of_taste_4_2():
    t = taste.allow({"cls": "T", "np10": 0.6, "dur_s": 32.8, "bed": False})
    assert (t["camera"], t["sounds"], t["display"], t["seats"], t["flashes"], t["under_voice_db"]) == (1, 1, 6, 2, 1, 9.0)
    assert taste.allow({"cls": "T", "np10": 0, "dur_s": 47.9, "bed": False})["sounds"] == 2            # p5565
    assert taste.allow({"cls": "M", "np10": 4.2, "dur_s": 4.8, "bed": False})["sounds"] == 1           # p5582: never under one
    p = taste.allow({"cls": "P", "np10": 14, "dur_s": 20, "bed": False})
    assert (p["sounds"], p["camera"], p["display"]) == (12, 7, 20)
    calm = taste.allow({"cls": "M", "np10": 3, "dur_s": 40, "bed": False}, calm=True)
    assert calm["display"] == taste.allow({"cls": "M", "np10": 3, "dur_s": 40, "bed": False})["display"] // 2
    assert [taste.klass(x, 30) for x in (0.6, 1.0, 4.9, 5.0)] == ["T", "M", "M", "P"] and taste.klass(9, 300) == "L"


def test_a_crop_of_the_take_is_not_a_new_picture_and_a_page_is_not_one():
    idx = selfie(20.0)
    c = comp(film(beats_over(idx, 4, lambda k, a, z: {"cut": "pulse 0.5", "pic": [{"src": "main"}, {"src": "main", "crop": "upper"}],
                                                       "do": [{"g": "page", "t": "A WORD", "for": f"w{a}", "at": f"w{a}..w{a + 1}.e"}]})), idx)
    assert c.meter["taste"]["supply"]["np"] == 0 and c.meter["taste"]["supply"]["cls"] == "T"


def test_the_uploads_own_cuts_and_found_pictures_are_supply():
    idx = wordless_index(20.0)
    idx["shots"] = [{"id": k + 1, "start": 2.0 * k, "end": 2.0 * k + 2.0} for k in range(10)]
    c = comp(film([{"k": "b", "id": "b01", "at": "@0..@20", "job": "x", "pic": "main"}]), idx)
    assert c.meter["taste"]["supply"]["np"] == 9 and c.meter["taste"]["supply"]["cls"] == "M"
    idx = selfie(20.0)
    c = comp(film(beats_over(idx, 5, lambda k, a, z: {"cut": [30, 30, 30, 30], "pic": ["main", f"bin:{k % 4}"]})), idx)
    assert c.meter["taste"]["supply"]["np"] >= 8


# ------------------------------------------------------------------ the shapes the owner called childish

def test_p5591_one_take_holds_one_sound_one_step_one_caption_seat_and_two_display_seats():
    idx = selfie(32.8)
    c = comp(film(p5591_shape(idx), system={"card": "P1", "devices": ["big", "chip", "flash"], "sounds": ["impact", "whoosh", "pop", "riser"]},
                  film_={"drop": ["20-21", "40-41", "60-61"]}), idx, voice(32.8))
    t = c.meter["taste"]
    assert t["supply"]["cls"] == "T" and t["allow"]["sounds"] == 1
    assert t["kept"]["camera"][0] <= 1 and t["kept"]["camera"][1] >= 12
    assert camera_steps(c) <= 1 + t["flips"] <= 1 + t["speech_edits"]
    snd = c.l1["sound"]
    assert len(snd) == 1
    lands = [i["on"] for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"] + [i["f"][0] for i in c.l1["items"] if i["k"] == "card"]
    assert min(abs(snd[0]["event_f"] - f) for f in lands) <= taste.PARTNER_F
    assert t["kept"]["display"][0] <= 6 and t["kept"]["flash"][0] <= 1
    caps = [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "cap"]
    assert caps and max(w["y"] if not isinstance(w["y"], list) else w["y"][-1] for w in caps) - min(w["y"] if not isinstance(w["y"], list) else w["y"][-1] for w in caps) <= 0.045
    assert len(t["seats"]["display"]) <= 2
    for w in (i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"):
        r = S.type_rect(type("C", (), {"voices": c.l1["voices"], "W": 1080, "H": 1920})(), w)
        cy = (r[1] + r[3]) / 2
        assert min(abs(cy - s[1]) for s in t["seats"]["display"]) <= 0.09, (w["id"], cy)


def test_p5565_eighty_seven_sounds_on_a_video_call_become_two():
    idx = selfie(47.9, face=(0.4, 0.3, 0.6, 0.42))

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"WORD {k}", "for": f"w{a}", "at": f"w{a}"}],
                "snd": [{"s": ("tick_1", "pop_1", "click_1", "swish_1", "ding_1", "impact_1", "whoosh_soft_1")[(k + j) % 7], "at": f"w{min(z, a + j)}", "for": f"w{min(z, a + j)}"} for j in range(7)]
                + [{"s": "impact_1", "for": f"b{k + 1:02d}.big"}]}
    c = comp(film(beats_over(idx, 12, make)), idx, voice(47.9))
    assert c.meter["taste"]["sounds"][1] >= 80 and len(c.l1["sound"]) == 2
    assert "EARNED" in c.meter_text and "SUPPLY class T" in c.meter_text


def test_p5582_five_seconds_of_product_hold_one_sound_and_one_display_line():
    idx = selfie(4.8)

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"LABEL {k} {j}", "for": f"w{a}", "at": f"w{a}+{4 * j}f", "d": 6} for j in range(4)],
                "snd": [{"s": "click_1", "for": f"b{k + 1:02d}.big{j or ''}"} for j in range(4)]}
    c = comp(film(beats_over(idx, 2, make)), idx, voice(4.8))
    assert len(c.l1["sound"]) == 1 and c.meter["taste"]["kept"]["display"][0] == 1


def test_a_film_of_pictures_keeps_its_sounds():
    """Many new pictures per 10 s and 12 sounds in 20 s, each on a picture: nothing to remove."""
    idx = selfie(21.0)
    names = ["whoosh_soft_1", "whoosh_soft_2", "riser_1", "riser_2", "riser_3", "riser_4"]
    words = len(idx["words"])
    beats = []
    for k in range(20):
        a, z = k * words // 20, (k + 1) * words // 20 - 1
        snd = [{"s": names[(k - 2 * (k // 5)) % 6], "for": f"b{k + 1:02d}.s1"}] if k % 5 < 3 else []
        beats.append({"k": "b", "id": f"b{k + 1:02d}", "w": f"{a}-{z}", "job": "x", "type": f"{a}-{z}", "cut": [8, 8, 8, 8],
                      "pic": ["main", f"bin:{k % 4}", "bin:4", f"bin:{(k + 1) % 4}"], "snd": snd})
    c = comp(film(beats, film_={"band": "S1"}), idx, voice(21.0))
    t = c.meter["taste"]
    assert t["supply"]["cls"] == "P" and t["supply"]["np10"] >= 10 and t["allow"]["sounds"] >= 12
    assert len(c.l1["sound"]) == t["sounds"][1] == 12 and not [r for r in t["rows"] if r["dept"] == "sound"]


def test_the_same_score_twice_is_the_same_film():
    idx = selfie(32.8)
    text = film(p5591_shape(idx))
    assert comp(text, idx, voice(32.8)).json() == comp(text, idx, voice(32.8)).json()


# ------------------------------------------------------------------ a device answers something

def test_a_device_line_with_no_for_is_dropped_and_the_meter_says_so():
    idx = selfie(12.0)
    c = comp(film(beats_over(idx, 3, lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "NO REASON", "at": f"w{a}"}],
                                                       "snd": [{"s": "impact_1", "at": f"w{a}"}]})), idx)
    assert not [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"] and not c.l1["sound"]
    n = 2 * c.meter["beats"]
    assert n >= 6 and sum(1 for r in c.meter["rejects"] if S.NO_FOR in r[1]) == n
    assert f"LINES dropped, {n} with no `for`" in c.meter_text and "b01.big" in c.meter_text


def test_for_is_checked_against_the_signal():
    idx = selfie(12.0)
    mid = idx["sentences"][1]["wi0"] + 2          # inside a sentence, no pause before it
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{mid - 1}", "job": "x", "pic": "main"},
                   {"k": "b", "id": "b02", "w": f"{mid}-{mid + 3}", "job": "x", "pic": "main",
                    "do": [{"g": "big", "t": "MID", "for": "turn", "at": f"w{mid}"}, {"g": "chip", "t": "gone", "for": "w999"},
                           {"g": "big", "t": "MOMENT", "for": "@3.3", "at": f"w{mid}"}, {"g": "chip", "t": "name", "for": "b09.card"}]},
                   {"k": "b", "id": "b03", "w": f"{mid + 4}-{len(idx['words']) - 1}", "job": "x", "pic": "main"}]), idx)
    said = " ".join(r[1] for r in c.meter["rejects"])
    assert "b02.big dropped: it answers a turn, and neither the voice nor the picture turns there" in said
    assert "b02.chip dropped: it answers w999, which is not in the film" in said
    assert "b02.big1 dropped: it answers @3.3, and nothing was measured" in said
    assert any(r["id"] == "b02.chip1" and "nothing of that name lands" in r["why"] for r in c.meter["taste"]["rows"])
    assert not [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"]


def test_a_sound_lands_on_what_it_answers_or_moves_to_it_or_goes():
    idx = selfie(14.0)
    a2 = idx["sentences"][1]["wi0"]
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{a2 - 1}", "job": "x", "pic": "main",
                    "do": [{"g": "big", "t": "CLAIM", "for": "w0", "at": "w0", "d": 40}],
                    "snd": [{"s": "impact_1", "for": "b01.big"}]},
                   {"k": "b", "id": "b02", "w": f"{a2}-{len(idx['words']) - 1}", "job": "x", "pic": "main",
                    "snd": [{"s": "whoosh_soft_1", "for": f"w{a2 + 3}", "at": f"w{a2 + 3}"}]}]), idx, voice(14.0))
    assert [s["id"] for s in c.l1["sound"]] == ["b01.snd0"]
    land = min(i["on"] for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big")
    assert abs(c.l1["sound"][0]["event_f"] - land) <= taste.PARTNER_F
    assert any(r["id"] == "b02.snd0" and r["why"] == "nothing lands on screen under it" for r in c.meter["taste"]["rows"])
    # five frames off its partner: moved onto it, not dropped
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{len(idx['words']) - 1}", "job": "x", "pic": "main",
                    "do": [{"g": "big", "t": "CLAIM", "for": "w0", "at": "w0", "d": 40}],
                    "snd": [{"s": "impact_1", "for": "b01.big", "at": "b01.big.in+6f"}]}]), idx, voice(14.0))
    land = min(i["on"] for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big")
    assert len(c.l1["sound"]) == 1 and c.l1["sound"][0]["event_f"] == land
    assert any(r["did"] == "repaired" and r["dept"] == "sound" for r in c.meter["taste"]["rows"])
    assert not S.validate_l1(c.l1)


def test_a_sound_sits_under_the_voice():
    idx = selfie(14.0)
    text = film([{"k": "b", "id": "b01", "w": f"0-{len(idx['words']) - 1}", "job": "x", "pic": "main",
                  "do": [{"g": "big", "t": "CLAIM", "for": "w0", "at": "w0", "d": 40}], "snd": [{"s": "impact_1", "for": "b01.big", "db": -2}]}])
    c = comp(text, idx, voice(14.0, db=-18.0))
    table, _ = S.sound_table()
    s = c.l1["sound"][0]
    assert s["db"] + table["impact_1"]["hit_lufs"] <= -18.0 - 9.0 + 0.06          # class T: 9 dB under the voice
    old = S.compile_score(text, index=idx, perception=voice(14.0, db=-18.0), govern=False).l1["sound"]
    assert not old or old[0]["db"] > s["db"]


def test_a_stamp_off_a_board_is_not_a_word_of_the_film():
    idx = selfie(10.0)
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{len(idx['words']) - 1}", "job": "x", "pic": "main",
                    "do": [{"g": "chip", "t": "@0.0 · YÖ", "for": "w0"}, {"g": "big", "t": "bin:3", "for": "w1", "at": "w1"}]}]), idx)
    assert not [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"]
    assert sum(1 for r in c.meter["rejects"] if "is a reference, not a word of this film" in r[1]) == 2


# ------------------------------------------------------------------ the take

def test_one_take_has_no_pulse_and_changes_framing_only_where_the_voice_was_cut():
    idx = selfie(30.0)
    plain = film(beats_over(idx, 8, lambda k, a, z: {"cut": "pulse 0.5", "pic": "main"}))
    c = comp(plain, idx)
    assert camera_steps(c) == 0 and c.meter["new_pictures10"] == 0
    assert c.meter["shots"] == c.meter["beats"]
    old = S.compile_score(plain, index=idx, govern=False)
    assert old.meter["cuts"] >= 20                                    # what the film did before: a crop every half second
    cut = film(beats_over(idx, 8, lambda k, a, z: {"cut": "pulse 0.5", "pic": "main"}), film_={"drop": ["20-23", "50-53"]})
    c = comp(cut, idx)
    steps = camera_steps(c)
    assert steps == c.meter["taste"]["flips"] == 2
    shots = sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    wide = max(s["crop"][2] - s["crop"][0] for s in shots if not isinstance(s["crop"][0], list))
    close = min(s["crop"][2] - s["crop"][0] for s in shots if not isinstance(s["crop"][0], list))
    assert 1.08 <= wide / close <= 1.12


def test_a_step_on_a_take_is_fifteen_percent_at_most_and_keeps_the_eyes():
    idx = selfie(30.0, face=(0.36, 0.2, 0.64, 0.42))
    a = idx["sentences"][2]["wi0"]
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{a - 1}", "job": "x", "pic": "main"},
                   {"k": "b", "id": "b02", "w": f"{a}-{len(idx['words']) - 1}", "job": "x", "pic": {"src": "main", "crop": "face.close", "for": "turn"}}]), idx)
    shots = sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    first = shots[0]["crop"]
    last = shots[-1]["crop"]
    last = last[len(last) // 2] if isinstance(last[0], list) else last
    assert 1.0 < (first[2] - first[0]) / (last[2] - last[0]) <= taste.STEP_ONCE + 1e-6
    eye_y = 0.2 + 0.4 * 0.22
    assert last[1] < eye_y < last[3] and last[0] <= 0.36 and last[2] >= 0.64            # the face stays whole
    assert (0.42 - 0.2) / (last[3] - last[1]) <= taste.FACE_H_MAX


def test_a_low_resolution_take_is_not_zoomed():
    idx = selfie(20.0)
    idx["video"].update(width=304, height=540)                           # p5571: 304 px wide, 3.55x to fill the frame
    a = idx["sentences"][1]["wi0"]
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{a - 1}", "job": "x", "pic": "main"},
                   {"k": "b", "id": "b02", "w": f"{a}-{len(idx['words']) - 1}", "job": "x", "pic": {"src": "main", "zoom": 2.0, "for": "turn"}}],
                  film_={"band": "P1"}), idx)
    for s in (i for i in c.l1["items"] if i["k"] == "shot"):
        r = s["crop"][0] if isinstance(s["crop"][0], list) else s["crop"]
        assert r[2] - r[0] >= 0.999


# ------------------------------------------------------------------ what the customer asked for

def test_what_the_customer_asked_for_is_never_counted():
    idx = selfie(30.0)

    def make(k, a, z):
        return {"pic": {"src": "main", "zoom": 1.12, "for": "a1"}}
    text = film(beats_over(idx, 5, make))
    c = _with_asks(text, idx, [{"id": "a1", "said": "zoom on each point", "probe": "cut_rate", "min": None}])
    n = c.meter["beats"]
    assert n >= 5 and c.meter["taste"]["allow"]["camera"] == 1
    assert c.meter["taste"]["kept"]["camera"] == [n, n] and len(c.meter["taste"]["bound"]) == n
    # an ask id that is not in the Scout's ledger is craft: it names nothing
    c = _with_asks(text, idx, [])
    assert c.meter["taste"]["kept"]["camera"] == [0, n]
    # an ask of another department ties nothing
    c = _with_asks(text, idx, [{"id": "a1", "said": "add captions", "probe": "caption_coverage", "min": 0.9}])
    assert c.meter["taste"]["kept"]["camera"] == [0, n]


def test_an_ask_with_a_number_raises_the_count_to_that_number_and_a_bind_to_a_beat_ties_nothing():
    idx = selfie(12.0)
    n = len(idx["words"])
    beats = [{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main",
              "do": [{"g": "big", "t": f"EFFECT {j}", "for": "a2", "at": f"w{3 * j}", "d": 20} for j in range(6)]}]
    c = _with_asks(film(beats), idx, [{"id": "a2", "said": "make some effects", "probe": "design_items", "min": 3}])
    assert c.meter["taste"]["allow"]["display"] == 2 and c.meter["taste"]["kept"]["display"] == [3, 6]
    beats[0]["do"] = [{"g": "big", "t": f"EFFECT {j}", "for": f"w{3 * j}", "at": f"w{3 * j}", "d": 20} for j in range(6)]
    c = _with_asks(film(beats, extra=[{"k": "bind", "ask": "a2", "to": ["b01"]}]), idx, [{"id": "a2", "said": "make some effects", "probe": "design_items", "min": None}])
    assert c.meter["taste"]["kept"]["display"] == [2, 6] and not c.meter["taste"]["bound"]


def _with_asks(text, idx, asks, perc=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        json.dump({"asks": asks}, open(os.path.join(d, "scout.json"), "w"))
        json.dump(idx, open(os.path.join(d, "index.json"), "w"))
        c = S.compile_score(text, index=idx, work_dir=d, bin_items=BIN, perception=perc or {}, sid="taste")
    assert not S.validate_l1(c.l1)
    return c


# ------------------------------------------------------------------ placement

def test_one_caption_seat_off_the_face_inside_the_band():
    idx = selfie(20.0, face=(0.25, 0.3, 0.75, 0.66))                  # the face covers the usual caption height
    c = comp(film(beats_over(idx, 5, lambda k, a, z: {"pic": "main", "type": [{"cap": f"{a}-{a + 2}|{a + 3}-{a + 4}", "seat": [0.5, 0.2 + 0.1 * k]}]}),
                  dna={"seat": [0.5, 0.5]}), idx)
    y = c.meter["taste"]["seats"]["caption"]
    assert 0.48 <= y <= 0.78 and y - taste.CAP_BLOCK_H / 2 > 0.66
    caps = [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "cap"]
    assert caps and all(abs((w["y"] if not isinstance(w["y"], list) else w["y"][-1]) - y) <= 0.04 for w in caps)
    assert all(w["x"] >= 0.14 - 1e-6 for w in caps if not isinstance(w["x"], list))


def test_added_type_stays_inside_the_safe_area_and_left_of_the_buttons():
    idx = selfie(12.0, face=(0.4, 0.3, 0.6, 0.45))
    n = len(idx["words"])
    c = _with_asks(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main",
                          "do": [{"g": "ticker", "t": "RUNNING LINE", "for": "w0", "y": 0.96, "at": "w0..w9.e"},
                                 {"g": "page", "t": "PAGE WORDS", "for": "w12", "at": "w12..w13.e", "seat": [0.5, 0.93]}]}]), idx, [])
    C = type("C", (), {"voices": c.l1["voices"], "W": 1080, "H": 1920})()
    rects = [S.type_rect(C, w) for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "big"]
    assert rects
    for r in rects:
        assert r[1] >= 0.10 - 1e-3 and r[3] <= 0.80 + 1e-3, r


def test_an_upload_with_its_own_captions_gets_none_unless_asked():
    idx = selfie(20.0)
    for k, s in enumerate(idx["spatial"]["samples"]):
        w = 0.5 + 0.06 * (k % 5)                              # a line that changes with the voice
        s["text"] = [[0.5 - w / 2, 0.7, 0.5 + w / 2, 0.76]]
    text = film(beats_over(idx, 4, lambda k, a, z: {"pic": "main"}))
    c = comp(text, idx)
    assert not [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "cap"]
    assert any(r["id"] == "cap" and "prints its own captions on" in r["why"] for r in c.meter["taste"]["rows"])
    c = _with_asks(text, idx, [{"id": "a1", "said": "add captions", "probe": "caption_coverage", "min": 0.9}])
    assert [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "cap"]


# ------------------------------------------------------------------ the film's system

def test_a_film_speaks_in_its_system():
    idx = selfie(30.0)

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"A {k}", "for": f"w{a}", "at": f"w{a}", "d": 30},
                                      {"g": ("chip", "counter", "type_on", "card")[k % 4], "t": f"b {k}", "for": f"w{a + 2}", "at": f"w{a + 2}", "pic": "bin:0"}]}
    c = comp(film(beats_over(idx, 8, make), system={"card": "P1", "devices": ["big", "chip"], "sounds": ["impact"]}), idx)
    t = c.meter["taste"]
    assert t["speaks"] == ["big", "chip"]
    assert {r["id"].split(".")[1].rstrip("0123456789") for r in t["rows"] if "outside this film's system" in r["why"]} == {"counter", "type_on", "card"}
    # no system named: the Score's own most used devices stand in, three at most on a take
    c = comp(film(beats_over(idx, 8, make)), idx)
    assert len(c.meter["taste"]["speaks"]) <= 3 and "names no system" in c.meter_text


def test_style_cards_are_the_ones_this_footage_can_supply():
    idx = selfie(32.8)
    per = {"dur": 32.8, "shots": [{"t": [0, 32.8]}]}
    assert brief.pick_styles(idx, per, []) == ["P1"]
    many = brief.pick_styles(idx, per, [{"id": i} for i in range(16)])
    assert many and "P1" not in many and set(many) <= {"P6", "M8", "P2"}
    assert taste.reach(per, [], 32.8) == 0 and taste.reach(per, [{}] * 16, 32.8) > 5
    txt = "\n".join(brief.supply_part(idx, per, []))
    assert "class T" in txt and "sounds 1" in txt


def test_band_distance_reads_new_pictures_not_crops():
    far = direct.band_distance({"band": "P2", "cuts10": 9.7, "new_pictures10": 0.6, "type_events10": 0})
    near = direct.band_distance({"band": "P1", "cuts10": 9.7, "new_pictures10": 0.6, "type_events10": 0})
    assert near < 0.25 < 1.5 < far


# ------------------------------------------------------------------ the last look

def test_the_look_names_devices_and_the_compiler_removes_them_or_gives_the_floor():
    idx = selfie(20.0)

    def make(k, a, z):
        return {"pic": "main", "look": {"contrast": 1.1}, "do": [{"g": "big", "t": f"LINE {k}", "for": f"w{a}", "at": f"w{a}", "d": 40}],
                "snd": [{"s": "impact_1", "for": f"b{k + 1:02d}.big"}] if k == 0 else []}
    text = film(beats_over(idx, 3, make))
    c = comp(text, idx, voice(20.0))
    ids = c.meter["taste"]["devices"]
    assert "b01.big" in ids and "b01.snd0" in ids
    cut = comp(taste.cut_text(text, ["b01.big"]), idx, voice(20.0))
    left = {i["id"].rsplit(".", 1)[0] for i in cut.l1["items"] if i["k"] == "word" and i.get("role") == "big"}
    assert "b01.big" not in left and "b02.big" in left
    assert not cut.l1["sound"]                                         # its sound went with it
    assert any(r["id"] == "b01.big" and r["did"] == "cut" for r in cut.meter["taste"]["rows"])
    floor = comp(taste.floor_text(text), idx, voice(20.0))
    assert floor.meter["taste"]["floor"] and not [i for i in floor.l1["items"] if i["k"] in ("card", "flash") and i.get("mode") != "pool"]
    assert not [i for i in floor.l1["items"] if i["k"] == "word" and i.get("role") == "big"] and not floor.l1["sound"]
    assert [i for i in floor.l1["items"] if i["k"] == "word" and i.get("role") == "cap"]            # the captions stay
    assert floor.l1["film"]["grade"] == c.l1["film"]["grade"] and floor.l1["dur_f"] == c.l1["dur_f"]
    assert "FLOOR" in floor.meter_text


def test_the_floor_keeps_what_the_customer_asked_for():
    idx = selfie(10.0)
    n = len(idx["words"])
    text = film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main",
                  "do": [{"g": "chip", "t": "LOGO", "for": "a3", "at": "beat"}, {"g": "big", "t": "CRAFT", "for": "w3", "at": "w3", "d": 40}]}])
    c = _with_asks(taste.floor_text(text), idx, [{"id": "a3", "said": "add the logo", "probe": "logo_shown", "min": None}])
    kept = {i["t"] for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"}
    assert kept == {"LOGO"}


def test_the_verdict_is_counted_from_the_pairs():
    known = {"b01.big", "b02.chip"}
    six = lambda ups, rm=(): json.dumps({"pairs": [{"n": i + 1, "post": "upload" if i < ups else "film", "why": "type on the face", "remove": list(rm) if i < ups else []} for i in range(6)]})   # noqa: E731
    v = taste.read_look("Here you go:\n" + six(2, ["b01.big", "made.up", "cap"]) + "\nthanks", 6, known)
    assert v == {"pairs": 6, "upload": 2, "remove": ["b01.big", "cap"], "why": ["type on the face"] * 2}
    assert taste.verdict_of(v) == "cut"
    assert taste.verdict_of(taste.read_look(six(4, ["b01.big"]), 6, known)) == "floor"
    assert taste.verdict_of(taste.read_look(six(3, []), 6, known)) == "cut"
    assert taste.verdict_of(taste.read_look(six(0), 6, known)) == "film"
    assert taste.read_look("not json", 6, known) is None and taste.verdict_of(None) == "film"
    same = json.dumps({"pairs": [{"n": i + 1, "post": "same", "why": "nothing added", "remove": []} for i in range(6)]})
    assert taste.verdict_of(taste.read_look(same, 6, known)) == "film"           # a frame nothing was added to counts for neither
    assert '"same"' in taste.LOOK_SYSTEM


def test_the_look_sets_the_upload_above_the_film_and_asks_once(tmp_path, monkeypatch):
    from PIL import Image

    from score import call
    idx = selfie(6.0)
    n = len(idx["words"])
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main", "type": "cap:3",
                    "do": [{"g": "big", "t": "CLAIM", "for": "w0", "at": "w0", "d": 60}]}]), idx)
    for name, colour in (("source.mp4", "navy"), ("film.mp4", "maroon")):
        subprocess.run([FF, "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=180x320:r=30:d=7", "-pix_fmt", "yuv420p", str(tmp_path / name)], check=True)
    seen = {}

    def stream(model, system, content, **kw):
        seen.update(model=model, system=system, content=content, kw=kw)
        return call.Result(json.dumps({"pairs": [{"n": i + 1, "post": "upload", "why": "covers him", "remove": ["b01.big"]} for i in range(6)]}), None, "end_turn", 0.5, 2.0, 0.031)
    monkeypatch.setattr(call, "stream", stream)
    v, usd = taste.look(str(tmp_path), c, str(tmp_path / "film.mp4"), 1e12, "claude-opus-5-5")
    assert usd == 0.031 and taste.verdict_of(v) == "floor" and v["remove"] == ["b01.big"]
    assert seen["kw"]["purpose"] == "director_last_look" and seen["content"][0]["type"] == "image"
    assert "b01.big: display type, reading CLAIM" in seen["content"][1]["text"] and "cap: the caption" in seen["content"][1]["text"]
    with Image.open(tmp_path / "look.jpg") as im:
        w, h = im.size
        top, bottom = im.getpixel((w // 12, 22 + 200)), im.getpixel((w // 12, 22 + 404 + 6 + 200))
    assert top[2] > top[0] + 40 and bottom[0] > bottom[2] + 40           # the upload above, the film below


def test_the_times_of_the_look_sit_where_most_was_added():
    idx = selfie(12.0)
    n = len(idx["words"])
    c = comp(film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main",
                    "do": [{"g": "big", "t": "LATE", "for": f"w{n - 6}", "at": f"w{n - 6}", "d": 40}]}]), idx)
    times = taste.look_times(c.l1)
    assert len(times) == 6 and times == sorted(times)
    big = [i for i in c.l1["items"] if i["k"] == "word" and i.get("role") == "big"][0]
    assert any(big["f"][0] / 30 <= t < big["f"][1] / 30 for t in times)
    assert abs(taste.source_second(c.l1, 3.0) - (3.0 + c.l1["spine"][0]["src"][0])) < 0.05


# ------------------------------------------------------------------ the last look in the job

class _LookDb:
    def __init__(self):
        self.events, self.allowance = [], []

    def run(self, fn, *args):
        import db as dbx
        name = getattr(fn, "__name__", "")
        if name == "get_asset":
            return {"storage_key": "media/1/preview.mp4"}
        if name == "_allowance_tx":
            self.allowance.append(round(args[1], 4))
        if fn is dbx.record_client_event:
            self.events.append((args[2], args[3]))
        return None


def _film_state(tmp_path, monkeypatch, verdict, usd=0.04):
    """A film that landed, its preview on screen, and a look that answers `verdict`."""
    import claude_trial
    import storage
    from types import SimpleNamespace as NS

    from score import film as film_mod
    idx = selfie(20.0)

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"LINE {k}", "for": f"w{a}", "at": f"w{a}", "d": 40}],
                "snd": [{"s": "impact_1", "for": f"b{k + 1:02d}.big"}] if k == 0 else []}
    text = film(beats_over(idx, 3, make), dna={"look": {"contrast": 1.1}})
    work = tmp_path / "film"
    work.mkdir()
    json.dump(idx, open(work / "index.json", "w"))
    c = S.compile_score(text, work_dir=str(work), bin_items={}, perception=voice(20.0))
    written = []

    class Ctx(NS):
        def apply_score(self, edl, desc):
            written.append((edl, desc))
            self.versions_written.append(len(written) + 2)
            return f"EDL v{len(written) + 1} -> v{len(written) + 2}: {desc}"
    ctx = Ctx(versions_written=[2], director_version=2, first_cut_phases={"director": {"stages": {}}},
              last_preview={"render_asset_id": 7, "edl_version": 2},
              director_state={"work": str(work), "text": text, "comp": c, "edl": {"keep": [[0, 20]], "first": True}, "bin_doc": None,
                              "original_key": "orig/1.mp4", "cfg": {"models": {"direct": "claude-opus-5-5"}}})
    monkeypatch.setattr(storage, "presign_get", lambda key, expires=3600: "https://signed.test/" + key)
    monkeypatch.setattr(film_mod, "_publish", lambda ctx, db, edl, locals_, bin_doc, key: edl)
    seen = {}

    def look(work_, comp_, url, deadline, model, effort="low"):
        seen.update(url=url, model=model)
        if isinstance(verdict, Exception):
            raise verdict
        scope.spent_usd += usd
        return verdict, usd
    monkeypatch.setattr(taste, "look", look)
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    return film_mod, ctx, _LookDb(), scope, written, seen, str(work)


def _verdict(upload, remove):
    return {"pairs": 6, "upload": upload, "remove": list(remove), "why": ["type over his face"] * upload}


def test_a_film_that_wins_every_pair_stands_as_rendered(tmp_path, monkeypatch):
    import time
    film_, ctx, db, scope, written, seen, work = _film_state(tmp_path, monkeypatch, _verdict(0, []))
    spend = {"usd": 0.2}
    assert film_.last_look(ctx, db, {"id": 2, "user_id": 3, "project_id": 1}, time.monotonic(), spend, scope) is None
    assert not written and not db.events and seen["url"].endswith("media/1/preview.mp4")
    assert ctx.first_cut_phases["director"]["look"]["decision"] == "film"
    assert round(spend["usd"], 2) == 0.24 and db.allowance == [0.1, -0.06]          # reserved on top, the rest taken back
    assert ctx.director_state is None and not os.path.exists(work)                 # the folder goes


def test_what_the_look_names_is_removed_and_the_film_is_written_again(tmp_path, monkeypatch):
    import time
    film_, ctx, db, scope, written, seen, work = _film_state(tmp_path, monkeypatch, _verdict(2, ["b01.big"]))
    applied = film_.last_look(ctx, db, {"id": 2, "user_id": 3, "project_id": 1}, time.monotonic(), {"usd": 0.0}, scope)
    assert applied and "captions" in applied
    edl, desc = written[0]
    assert "after the last look" in desc and not edl["sfx"]                        # its sound went with it
    assert all("LINE 0" not in t["text"] for t in edl["texts"]) and any("LINE 1" in t["text"] for t in edl["texts"])
    kind, detail = db.events[0]
    assert kind == "director_taste" and detail["decision"] == "cut" and detail["removed"] == ["b01.big"] and detail["for_upload"] == 2
    assert ctx.director_version == 3 and ctx.director_first == {"edl": {"keep": [[0, 20]], "first": True}, "version": 2}
    assert film_.restore_first(ctx) and written[-1][0] == {"keep": [[0, 20]], "first": True} and ctx.director_first is None


def test_four_of_six_for_the_upload_delivers_the_plain_floor_film_and_files_it(tmp_path, monkeypatch):
    import time
    film_, ctx, db, scope, written, seen, work = _film_state(tmp_path, monkeypatch, _verdict(4, []))
    applied = film_.last_look(ctx, db, {"id": 2, "user_id": 3, "project_id": 1}, time.monotonic(), {"usd": 0.0}, scope)
    edl, _ = written[0]
    assert applied and not edl["texts"] and not edl["sfx"] and not edl["fx"] and edl["captions"]
    assert edl["effects"]["grade_custom"]["contrast"] == 1.1                       # the trims, the captions and the grade stay
    assert db.events[0][0] == "director_taste" and db.events[0][1]["decision"] == "floor"
    assert ctx.first_cut_phases["director"]["look"]["decision"] == "floor"


@pytest.mark.parametrize("fault", ["late", "raises", "unreadable", "no_preview", "off"])
def test_a_look_that_fails_never_blocks_the_customer(tmp_path, monkeypatch, fault):
    import time

    from score import call
    verdict = {"late": call.Late("past its deadline"), "raises": RuntimeError("board"), "unreadable": None}.get(fault, _verdict(6, []))
    film_, ctx, db, scope, written, seen, work = _film_state(tmp_path, monkeypatch, verdict)
    started = time.monotonic()
    if fault == "no_preview":
        ctx.last_preview = {}
    if fault == "off":
        monkeypatch.setenv("DIRECTOR_LOOK", "0")
    assert film_.last_look(ctx, db, {"id": 2, "user_id": 3, "project_id": 1}, started, {"usd": 0.0}, scope) is None
    assert not written and not db.events and ctx.director_state is None and not os.path.exists(work)
    assert film_.last_look(ctx, db, {"id": 2}, started, {"usd": 0.0}, scope) is None        # nothing left to look at


def test_a_look_past_its_time_leaves_the_film(tmp_path, monkeypatch):
    import time
    film_, ctx, db, scope, written, seen, work = _film_state(tmp_path, monkeypatch, _verdict(6, []))
    assert film_.last_look(ctx, db, {"id": 2}, time.monotonic() - film_.LOOK_BY_S - 1, {"usd": 0.0}, scope) is None
    assert not seen and "late" in ctx.first_cut_phases["director"]["look"]["skipped"]


def test_first_cut_renders_again_after_the_look_and_falls_back_to_the_first_film(monkeypatch, tmp_path):
    import claude_trial

    from score import film as film_
    from tests.test_score_film import _run
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    calls = []

    def attempt(ctx, worker_db, job, started, spend, trial_scope, subscribed):
        ctx.versions_written.append(2)
        ctx.director_version = 2
        return [{"tool": "director", "ok": True}], ["12 shots", "titles"], {"how": "director", "edl_version": 2}

    def look(ctx, worker_db, job, started, spend, trial_scope):
        calls.append("look")
        ctx.director_version = 3
        return ["captions"]
    monkeypatch.setattr(film_, "last_look", look)
    monkeypatch.setattr(film_, "restore_first", lambda ctx: calls.append("restore") or True)
    renders = [True, True]
    result, said, reads, chain_args = _run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope, renders=renders)
    assert calls == ["look"] and renders == [] and not chain_args
    assert result["first_cut"]["picked"] == {"how": "director", "edl_version": 3, "look": True} and result["outcome"] == "fulfilled"
    assert "captions" in said[-1][1] and "titles" not in said[-1][1]               # the reply names what the film now holds
    # the look's version does not render: the first film goes back and renders, the chain never runs
    calls.clear()
    renders = [True, False, True]
    result, said, reads, chain_args = _run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope, renders=renders)
    assert calls == ["look", "restore"] and renders == [] and not chain_args and result["outcome"] == "fulfilled"


# ------------------------------------------------------------------ windows of ten seconds, and found pictures

def test_hard_steps_on_a_take_are_ten_seconds_apart():
    """A row of closed pauses is a row of plain jump cuts: the framing changes at a speech edit at most once in
    10 s, so no 10 s window ever holds two steps (the rater's measured ceiling counts per window)."""
    idx = selfie(40.0)
    drops = [f"{w}-{w + 1}" for w in range(8, len(idx["words"]) - 4, 9)]             # a cut every three seconds or so
    c = comp(film(beats_over(idx, 6, lambda k, a, z: {"pic": "main"}), film_={"drop": drops}), idx)
    t = c.meter["taste"]
    assert t["speech_edits"] >= 8 and 1 <= t["flips"] <= 4
    shots = sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    steps = [q["f"][0] for p, q in zip(shots, shots[1:]) if abs((S._crop_end(p)[2] - S._crop_end(p)[0]) / (S._crop_start(q)[2] - S._crop_start(q)[0]) - 1) >= 0.04]
    assert len(steps) == t["flips"] and all(b - a >= 10 * 30 for a, b in zip(steps, steps[1:]))


def test_no_ten_second_window_holds_two_sounds_on_a_take():
    idx = selfie(47.9)

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"WORD {k}", "for": f"w{a}", "at": f"w{a}", "d": 30}], "snd": [{"s": ("impact_1", "whoosh_soft_1", "pop_1")[k % 3], "for": f"b{k + 1:02d}.big"}]}
    c = comp(film(beats_over(idx, 14, make)), idx, voice(47.9))
    at = sorted(s["event_f"] for s in c.l1["sound"])
    assert len(at) == 2 and at[1] - at[0] >= 10 * 30


def test_the_look_can_send_a_found_picture_back_to_the_take():
    idx = selfie(20.0)
    text = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 40], "pic": ["main", f"bin:{k}"]}))
    c = comp(text, idx)
    n = c.meter["sources"]["found_items"]
    assert "b02.s1" in taste.look_ids(c) and n >= 4
    cut = comp(taste.cut_text(text, ["b02.s1"]), idx)
    pics = {i["id"]: i["pic"] for i in cut.l1["items"] if i["k"] == "shot"}
    assert pics["b02.s1"] == "main" and pics["b01.s1"] == "bin:0" and cut.meter["sources"]["found_items"] == n - 1
    assert any(r["id"] == "b02.s1" and r["did"] == "cut" for r in cut.meter["taste"]["rows"])
    floor = comp(taste.floor_text(text), idx)
    assert {i["pic"] for i in floor.l1["items"] if i["k"] == "shot"} == {"main"}        # the plain film is the upload's own picture


def test_a_two_line_caption_and_a_one_line_caption_share_one_anchor():
    """The renderer centres a caption on its anchor: the lowered anchor is the block's middle, so the seat holds."""
    from score import lower
    idx = selfie(20.0)
    long_ = lambda a, z: f"{a}-{min(z, a + 7)}"          # noqa: E731  eight words: two lines at .72 of the frame
    c = comp(film(beats_over(idx, 4, lambda k, a, z: {"pic": "main", "type": f"{a} {a + 1}|{long_(a + 2, z)}"})), idx)
    rep = {}
    caps = lower.lower_captions(c.l1, rep)
    ys = {round(x["style"]["anchor_y"], 3) for x in caps}
    assert len(caps) >= 8 and any(not x["style"]["single_line"] for x in caps)
    assert max(ys) - min(ys) <= 0.004, ys
    assert abs(sum(ys) / len(ys) - c.meter["taste"]["seats"]["caption"]) <= 0.02


def test_a_named_ink_the_picture_swallows_flips(monkeypatch):
    """P4, p5591: black "McLAREN ?" on a dark photo. The picture under the word is read; an ink it swallows flips."""
    idx = selfie(10.0)
    n = len(idx["words"])
    text = film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main",
                  "do": [{"g": "big", "t": "DARK ON DARK", "for": "w0", "at": "w0", "d": 60, "ink": "#111111"}]}])
    monkeypatch.setattr(S, "under_rect", lambda ctx, s, f, rect: (0.05, 0.12))       # a dark picture under it
    c = S.compile_score(text, index=idx, sid="taste", bin_items={})
    inks = {w["ink"] for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "big"}
    assert inks == {"#FFFFFF"}
    monkeypatch.setattr(S, "under_rect", lambda ctx, s, f, rect: (0.8, 0.95))        # a bright one: the named ink reads, it stays
    c = S.compile_score(text, index=idx, sid="taste", bin_items={})
    assert {w["ink"] for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "big"} == {"#111111"}


def test_the_caption_seat_clears_the_words_the_upload_prints():
    """p5565: a slide's question sat at the usual caption height, and the caption was set on top of it."""
    idx = selfie(20.0, face=(0.4, 0.2, 0.6, 0.32))
    for s in idx["spatial"]["samples"]:
        s["text"] = [[0.1, 0.55, 0.6, 0.66]]                  # a block of printed text, not a caption line: it stays
    c = comp(film(beats_over(idx, 4, lambda k, a, z: {"pic": "main"}), dna={"seat": [0.5, 0.6]}), idx)
    y = c.meter["taste"]["seats"]["caption"]
    h = taste.CAP_BLOCK_H / 2
    assert 0.48 <= y <= 0.78 and (y + h <= 0.55 or y - h >= 0.66)          # above the printed block or under it, never on it
    assert [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "cap"]
    for x, sy in c.meter["taste"]["seats"]["display"]:
        assert not (0.55 - 0.045 < sy < 0.66 + 0.045 and x - 0.28 < 0.6)


def test_a_beat_of_words_after_a_beat_of_seconds_is_one_continuous_take():
    """p5571: the upload played as an insert of itself from the first word on, framed lower than the upload."""
    from score import lower
    idx = selfie(12.0)
    w0 = idx["words"][10]["t0"]
    text = film([{"k": "b", "id": "b01", "at": f"@0.0..@{w0:.2f}", "job": "x", "pic": "main", "type": ""},
                 {"k": "b", "id": "b02", "w": f"10-{len(idx['words']) - 1}", "job": "x", "pic": "main", "type": "cap:3"}])
    c = S.compile_score(text, index=idx, sid="taste", bin_items={})
    sp = c.l1["spine"]
    assert all(abs(q["src"][0] - p["src"][1]) < 1e-6 for p, q in zip(sp, sp[1:]))
    rep = {}
    keep, inserts = lower.lower_spine(c.l1, type("B", (), {"locals": {}, "work": "/tmp", "plate": lambda *a: "p"})(), rep)
    assert not inserts and "out_of_order_spans" not in rep and len(keep) >= 1


def test_a_device_can_answer_the_uploads_own_cut_at_the_start_of_its_shot():
    idx = wordless_index(12.0)
    idx["shots"] = [{"id": 1, "start": 0.0, "end": 4.0}, {"id": 2, "start": 4.0, "end": 12.0}]
    text = film([{"k": "b", "id": "b01", "at": "@0.0..@3.95", "job": "x", "pic": "main", "type": ""},
                 {"k": "b", "id": "b02", "at": "@3.95..@11.0", "job": "x", "pic": "main", "type": "",
                  "do": [{"g": "big", "t": "HERE", "for": "b02.s0", "at": "b02.in", "d": 40}]}])
    c = comp(text, idx)
    assert [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "big"], c.meter["taste"]["rows"]


def test_a_title_can_answer_the_films_first_picture():
    idx = wordless_index(12.0)
    text = film([{"k": "b", "id": "b01", "at": "@2.0..@6.0", "job": "x", "pic": "main", "type": "",
                  "do": [{"g": "big", "t": "OPEN", "for": "b01.s0", "at": "b01.in", "d": 40}], "snd": [{"s": "impact_1", "for": "b01.big"}]},
                 {"k": "b", "id": "b02", "at": "@6.0..@11.0", "job": "x", "pic": "main", "type": ""}])
    c = comp(text, idx)
    assert [w for w in c.l1["items"] if w["k"] == "word" and w.get("role") == "big"] and len(c.l1["sound"]) == 1


def test_a_beat_played_out_of_order_is_a_cut_and_changes_no_framing():
    """p5575: a wordless montage with a cold open. Every jump of the spine was read as a speech edit and the film
    sat a tenth closer, off-centre, from the second shot on."""
    idx = wordless_index(12.0)
    idx["spatial"] = {"samples": [{"t": float(k), "faces": [[0.6, 0.55, 0.8, 0.7]], "mean_luma": 120.0} for k in range(13)]}
    text = film([{"k": "b", "id": "b01", "at": "@6.9..@7.7", "job": "x", "pic": "main", "type": ""},
                 {"k": "b", "id": "b02", "at": "@0.0..@3.9", "job": "x", "pic": "main", "type": ""},
                 {"k": "b", "id": "b03", "at": "@4.0..@6.0", "job": "x", "pic": "main", "type": ""},
                 {"k": "b", "id": "b04", "at": "@8.0..@10.0", "job": "x", "pic": "main", "type": ""}], dna={"take": 1.2})
    c = comp(text, idx)
    assert c.meter["taste"]["speech_edits"] == 0 and c.meter["taste"]["flips"] == 0
    for s in (i for i in c.l1["items"] if i["k"] == "shot"):
        r = s["crop"] if not isinstance(s["crop"][0], list) else s["crop"][0]
        assert [round(v, 3) for v in r] == [0, 0, 1, 1]              # the upload as shot: dna.take never zooms


def test_a_tenth_at_a_speech_edit_keeps_its_centre_and_the_head_whole():
    idx = selfie(30.0, face=(0.55, 0.5, 0.85, 0.72))                  # a face low and to the right
    c = comp(film(beats_over(idx, 6, lambda k, a, z: {"pic": "main"}), film_={"drop": ["30-33"]}), idx)
    assert c.meter["taste"]["flips"] == 1
    close = [s["crop"] for s in c.l1["items"] if s["k"] == "shot" and not isinstance(s["crop"][0], list) and s["crop"][2] - s["crop"][0] < 0.99]
    assert close
    for r in close:
        hd = S.head_of([0.55, 0.5, 0.85, 0.72])
        assert r[0] <= max(hd[0], 0) + 1e-6 and r[2] >= min(hd[2], 1) - 1e-6          # the head stays inside the closer frame
        assert abs((r[0] + r[2]) / 2 - 0.5) <= 0.06                                     # and the frame barely leaves its centre


def test_an_earned_step_outranks_the_tenth_at_a_speech_edit_near_it():
    idx = selfie(30.0)
    sents = idx["sentences"]
    a = sents[3]["wi0"]
    cut = f"{a + 4}-{a + 6}"                                          # words cut out two seconds after the earned step
    beats = [{"k": "b", "id": "b01", "w": f"0-{a - 1}", "job": "x", "pic": "main"},
             {"k": "b", "id": "b02", "w": f"{a}-{len(idx['words']) - 1}", "job": "x", "pic": {"src": "main", "zoom": 1.12, "for": "turn"}}]
    c = comp(film(beats, film_={"drop": [cut]}), idx)
    t = c.meter["taste"]
    assert t["kept"]["camera"] == [1, 1] and t["speech_edits"] == 1 and t["flips"] == 0
    assert camera_steps(c) == 1
    # with no earned step, the same cut takes its tenth
    beats[1]["pic"] = "main"
    c = comp(film(beats, film_={"drop": [cut]}), idx)
    assert c.meter["taste"]["flips"] == 1 and camera_steps(c) == 1


def test_the_look_can_take_back_a_silent_beat_the_film_added():
    """p5584: the film's last beat held a frame of the upload that was the platform's end card."""
    idx = selfie(12.0)
    n = len(idx["words"])
    text = film([{"k": "b", "id": "b01", "w": f"0-{n - 1}", "job": "x", "pic": "main"},
                 {"k": "b", "id": "b02", "f": 45, "job": "x", "pic": "main@10.0", "type": ""}])
    c = comp(text, idx)
    assert "b02" in taste.look_ids(c) and c.meter["taste"]["silent"] == ["b02"]
    t = (c.l1["dur_f"] - 20) / 30
    assert any(r.startswith("b02: a beat the editor added") for r in taste.on_screen(c.l1, t, sorted(taste.look_ids(c), key=len, reverse=True)))
    cut = comp(taste.cut_text(text, ["b02"]), idx)
    assert cut.l1["dur_f"] == c.l1["dur_f"] - 45 and any(r["id"] == "b02" and r["did"] == "cut" for r in cut.meter["taste"]["rows"])
    assert comp(taste.floor_text(text), idx).l1["dur_f"] == c.l1["dur_f"] - 45
    # a beat of the voice is never taken: naming it changes nothing
    assert comp(taste.cut_text(text, ["b01"]), idx).l1["dur_f"] == c.l1["dur_f"]


def test_a_step_is_not_made_blind():
    """p5584: 12% on a man whose face the index had missed cut the top of his head."""
    idx = talk_index(20.0, faces=False)
    a = idx["sentences"][1]["wi0"]
    beats = [{"k": "b", "id": "b01", "w": f"0-{a - 1}", "job": "x", "pic": "main"},
             {"k": "b", "id": "b02", "w": f"{a}-{len(idx['words']) - 1}", "job": "x", "pic": {"src": "main", "zoom": 1.12, "for": "turn"}}]
    c = comp(film(beats, film_={"drop": [f"{a + 5}-{a + 7}"]}), idx)
    assert camera_steps(c) == 0 and c.meter["taste"]["flips"] == 0
    assert any(r["id"] == "b02.p0" and "no face found" in r["why"] for r in c.meter["taste"]["rows"])
    beats[1]["pic"]["for"] = "a1"                      # the customer asked for it: it is made
    c = _with_asks(film(beats), idx, [{"id": "a1", "said": "zoom in when I start the second point", "probe": "cut_rate", "min": None}])
    assert camera_steps(c) == 1
