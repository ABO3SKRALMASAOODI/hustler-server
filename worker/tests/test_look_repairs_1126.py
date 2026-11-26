"""The Director's last look repairs what it names instead of losing the film (26 Nov: #1151, #1817, #1818, #1820).

p6311 and p6304: the look named dark or unreadable type and covered subjects; the devices were cut off the film.
p6275: four of six frames for the upload, nothing named, and the film went back to the upload-level chain. Synthetic
Scores and index stubs; no customer media."""
import json

import score.compiler as S
from score import lower, taste
from tests.test_score_taste import beats_over, comp, film, selfie, voice


def _titled(ink="#FFFFFF", seat=None):
    idx = selfie(20.0)

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"LINE {k}", "for": f"w{a}", "at": f"w{a}", "d": 40}]}
    dna = {"ink": ink}
    if seat:
        dna["seat"] = seat
    return idx, film(beats_over(idx, 3, make), dna=dna)


def _texts(c, tmp_path, text):
    edl, _loc, _rep = lower.lower(c.l1, str(tmp_path))
    return [t for t in edl["texts"] if text in t["text"]], _rep


def test_an_edged_dark_title_goes_out_light_inside_the_dark_outline(tmp_path):
    """The renderer's title outline is dark only, so dark ink marked `edge` went out with no edge (p6304 '03 LOAD' on
    dark straw, p6311 black type on a red dress). Before: colour #111111, outline 0. After: light ink, outline drawn."""
    idx, text = _titled(ink="#111111")
    c = comp(text, idx, voice(20.0))
    words = [i for i in c.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    assert words
    for w in words:
        w["ink"], w["edge"] = "#111111", True
    got, rep = _texts(c, tmp_path, "LINE 0")
    assert got and all(t["color"] == "#FFFFFF" and t["outline_width"] > 0 and t["shadow"] == 1.0 for t in got)
    assert rep.get("texts_edged_light")


def test_mend_lines_keep_the_title_edge_it_and_box_it_when_unreadable(tmp_path):
    idx, text = _titled()
    c = comp(text, idx, voice(20.0))
    m = comp(taste.mend_text(text, {"b01.big": "unreadable"}), idx, voice(20.0))
    before = [i for i in c.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    after = [i for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    assert [i["t"] for i in after] == [i["t"] for i in before] and [i["f"] for i in after] == [i["f"] for i in before]
    assert all(i.get("edge") and i.get("boxed") for i in after)
    assert m.meter["mended_type"]["boxed"]
    got, rep = _texts(m, tmp_path, "LINE 0")
    assert got and all(t.get("box") and t["color"] == "#FFFFFF" for t in got) and rep.get("texts_boxed")
    others = [i for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b02.big")]
    assert others and not any(i.get("boxed") for i in others)                     # only what was named


def test_a_title_that_covers_the_subject_moves_clear_of_where_it_stood(tmp_path):
    idx, text = _titled()
    c = comp(text, idx, voice(20.0))
    m = comp(taste.mend_text(text, {"b01.big": "covers"}), idx, voice(20.0))
    ys0 = [i["y"] for i in c.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    ys1 = [i["y"] for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    rep = m.meter["mended_type"]
    assert rep["moved"] or rep["unresolved"]
    if rep["moved"]:
        dy = rep["moved"][0]["dy"]
        assert abs(dy) > 0.02 and all(abs((b[-1] if isinstance(b, list) else b) - (a[-1] if isinstance(a, list) else a) - dy) < 1e-6 for a, b in zip(ys0, ys1))
    assert [i["t"] for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]        # still on the film


def test_the_look_splits_type_to_mend_from_what_nothing_explains():
    idx, text = _titled()
    c = comp(text, idx, voice(20.0))
    v = {"pairs": 6, "upload": 3, "remove": ["b01.big", "b02.big", "cap", "b03.big"], "why": [],
         "faults": {"b01.big": "unreadable", "b02.big": "covers", "b03.big": "unearned"}}
    mends, cuts = taste.split_named(c, v)
    assert mends == {"b01.big": "unreadable", "b02.big": "covers", "cap": "unreadable"} and cuts == ["b03.big"]


def test_the_look_is_told_the_film_frame_is_nine_sixteen_and_asked_for_faults():
    assert "9:16" in taste.LOOK_SYSTEM and '"fault"' in taste.LOOK_SYSTEM
    v = taste.read_look(json.dumps({"pairs": [{"n": 1, "post": "upload", "why": "x", "remove": [], "fault": {"cap": "unreadable"}}]}), 6, set())
    assert v["remove"] == ["cap"] and v["faults"] == {"cap": "unreadable"}
