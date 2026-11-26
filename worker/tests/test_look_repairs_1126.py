"""The Director's last look repairs what it names instead of losing the film (26 Nov: #1151, #1817, #1818, #1820).

p6311 and p6304: the look named dark or unreadable type and covered subjects; the devices were cut off the film.
p6275: four of six frames for the upload, nothing named, and the film went back to the upload-level chain. Synthetic
Scores and index stubs; no customer media."""
import json

import pytest

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


@pytest.mark.parametrize("ink,edge", [("#111111", "#F2F2F2"), ("#D62828", "#F2F2F2"), ("#1D3557", "#F2F2F2"), ("#FFD166", "#111111")])
def test_an_edged_title_keeps_its_ink_inside_the_contrasting_edge(tmp_path, ink, edge):
    """The renderer's title outline was dark only, so dark ink marked `edge` went out with no edge (p6304 '03 LOAD' on
    dark straw, p6311 black type on a red dress). The edge now contrasts the ink and the ink stays: a red or navy the
    customer or the Director chose is not turned white."""
    idx, text = _titled(ink=ink)
    c = comp(text, idx, voice(20.0))
    words = [i for i in c.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    assert words
    for w in words:
        w["ink"], w["edge"] = ink, True
    got, rep = _texts(c, tmp_path, "LINE 0")
    assert got and all(t["color"] == ink and t["outline_color"] == edge and t["outline_width"] > 0 and t["shadow"] == 1.0 for t in got)
    assert rep.get("texts_edged")


def test_the_renderer_draws_a_title_edge_and_panel_in_the_item_outline_colour():
    """graphics hard-coded the title outline as near-black (\\3c&H101010&): a light edge round navy ink could not be
    drawn. The item's outline_color now reaches the edge, and the panel when boxed; without it nothing changes."""
    import graphics
    from schemas import TextItem
    base = {"id": "t", "text": "LOAD", "start": 0.0, "end": 2.0, "template": "title", "color": "#1D3557", "outline_width": 2.2}
    assert TextItem(**base).outline_color is None and TextItem(**base, outline_color="#F2F2F2").outline_color == "#F2F2F2"
    plain = graphics._compile_item(dict(base), 4, (1080, 1920))["text"]
    edged = graphics._compile_item(dict(base, outline_color="#F2F2F2"), 4, (1080, 1920))["text"]
    boxed = graphics._compile_item(dict(base, outline_color="#F2F2F2", box=True), 4, (1080, 1920))["text"]
    assert "\\3c&H101010&" in plain and "\\1c&H57351D&" in edged
    assert "\\3c&HF2F2F2&" in edged and "\\3c&H101010&" not in edged
    assert "\\3c&HF2F2F2&\\3a" in boxed


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
    assert got and all(t.get("box") and t["color"] == "#FFFFFF" and t["outline_color"] == "#111111" for t in got) and rep.get("texts_boxed")
    others = [i for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b02.big")]
    assert others and not any(i.get("boxed") for i in others)                     # only what was named


def test_a_title_that_covers_the_subject_moves_clear_of_where_it_stood(tmp_path):
    """#1151 / #1820: a title the look says covers the subject moves, as one unit, to a clear height: its words,
    size and timing stay. The frame has room (a face high in the frame, the title seated over it): it must move,
    not stay 'unresolved' where it stood (before, the test passed either way)."""
    idx = selfie(20.0, face=(0.38, 0.12, 0.62, 0.3))

    def make(k, a, z):
        return {"pic": "main", "do": [{"g": "big", "t": f"LINE {k}", "for": f"w{a}", "at": f"w{a}", "d": 40}]}
    text = film(beats_over(idx, 3, make), dna={"seat": [0.5, 0.3]})
    c = comp(text, idx, voice(20.0))
    m = comp(taste.mend_text(text, {"b01.big": "covers"}), idx, voice(20.0))
    before = [i for i in c.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    after = [i for i in m.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big")]
    rep = m.meter["mended_type"]
    assert rep["moved"] and not rep["unresolved"], rep
    dy = rep["moved"][0]["dy"]
    last = lambda v: v[-1] if isinstance(v, list) else v       # noqa: E731
    assert abs(dy) > 0.02 and all(abs(last(b["y"]) - last(a["y"]) - dy) < 1e-3 for a, b in zip(before, after))   # dy is reported to 4 places
    assert [i["t"] for i in after] == [i["t"] for i in before] and [i["f"] for i in after] == [i["f"] for i in before]


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
    assert v["remove"] == [] and v["mend"] == ["cap"] and v["faults"] == {"cap": "unreadable"}


def test_a_fault_the_look_did_not_ask_to_remove_mends_type_and_cuts_nothing():
    """read_look added ids that appeared only in `fault` to the removal list, so 'unearned' on an id the look did not
    remove still cut the Director's device. A fault outside `remove` is never a cut: a repair it names is made to type."""
    idx, text = _titled()
    c = comp(text, idx, voice(20.0))
    known = {"b01.big", "b02.big", "b03.big"}
    v = taste.read_look(json.dumps({"pairs": [
        {"n": 1, "post": "upload", "why": "x", "remove": [], "fault": {"b01.big": "unearned", "b02.big": "unreadable"}},
        {"n": 2, "post": "upload", "why": "y", "remove": ["b03.big"], "fault": {"b03.big": "covers"}}]}), 6, known)
    assert v["remove"] == ["b03.big"] and v["mend"] == ["b02.big"] and "b01.big" not in v["faults"]
    mends, cuts = taste.split_named(c, v)
    assert mends == {"b02.big": "unreadable", "b03.big": "covers"} and cuts == []


def test_type_named_without_a_fault_is_measured_on_the_frame_not_put_on_a_panel():
    """split_named mended type named with no fault as 'unreadable': a title the look meant as 'on his face' went onto
    a dark panel there. Now the frame decides: over a head it covers it (edged, moved where there is room, never
    panelled); clear of heads it is unreadable and stands on its panel."""
    v = {"pairs": 6, "upload": 3, "remove": ["b01.big"], "why": ["type on the face"]}
    idx, text = _titled()
    c = comp(text, idx, voice(20.0))
    mends, cuts = taste.split_named(c, v)
    assert mends == {"b01.big": "measure"} and cuts == []
    clear = comp(taste.mend_text(text, mends), idx, voice(20.0)).meter["mended_type"]
    assert clear["measured"] == {"b01.big.0": "unreadable"} and clear["boxed"] == ["b01.big.0"]
    on_face = selfie(20.0, face=(0.3, 0.45, 0.7, 0.75))
    _, text2 = _titled()
    rep = comp(taste.mend_text(text2, mends), on_face, voice(20.0))
    got = rep.meter["mended_type"]
    assert got["measured"] == {"b01.big.0": "covers"} and not got["boxed"]
    assert not any(i.get("boxed") for i in rep.l1["items"] if i["k"] == "word" and i["id"].startswith("b01.big"))


def test_the_look_hears_a_fit_to_nine_sixteen_as_a_fit_not_a_zoom():
    """p6275 (#1817): a square upload fitted to 9:16 is a crop 0.5625 wide and full height. The look's list said
    'the upload framed at 1.78x' for it, and the look called four frames 'zoom crops the player names'. The push-in
    is now read past the fit; a real push-in still reads as one."""
    assert "no push-in" in taste.framing([0.0308, 0.0, 0.5933, 1.0])                   # p6275's crop, measured
    assert "1.78x" not in taste.framing([0.0308, 0.0, 0.5933, 1.0])
    assert "2.00x" in taste.framing([0.25, 0.25, 0.75, 0.75])                          # a 9:16 upload pushed in 2x
    assert "1.33x" in taste.framing([0.1, 0.1, 0.1 + 0.5625 * 0.75, 0.85])            # a square upload pushed in past its fit
