"""EDIT-CORE 26 Nov: tools repair what a model writes instead of refusing it.

p6238 (24 Nov): apply_edit_batch refused a vector colour '#FFFFFF00' and an
item op with no stable id; p6304 (26 Nov): "Unsupported operation upsert on
speed" when the editor retimed sp1-sp3 to reach the asked 55 s.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _base():
    from schemas import validate_edl
    return validate_edl({"keep": [[0.0, 10.0], [12.0, 30.0]]}, 40.0).model_dump()


def test_hex_rgba_reads_every_colour_a_model_writes():
    from schemas import hex_rgba, coerce_hex
    assert hex_rgba("#FFFFFF00") == ("#FFFFFF", 0.0)
    assert hex_rgba("#ff000080")[0] == "#FF0000"
    assert abs(hex_rgba("#ff000080")[1] - 0.502) < 0.01
    assert coerce_hex("fff") == "#FFFFFF"
    assert coerce_hex("#abc") == "#AABBCC"
    assert coerce_hex("0x00ff00") == "#00FF00"
    assert coerce_hex("Red") == "#FF0000"
    assert hex_rgba("rgba(255, 0, 0, 0.5)") == ("#FF0000", 0.5)
    assert hex_rgba("transparent") == ("#000000", 0.0)
    assert coerce_hex("banana") is None
    assert coerce_hex("") is None


def test_batch_vector_with_8_digit_hex_is_an_outline_not_a_refusal():
    import edit_batch
    out = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "vectors", "id": "box1",
        "value": {"kind": "rectangle", "start": 1.0, "end": 4.0,
                  "color": "#FFFFFF00", "stroke_color": "#FFFFFF",
                  "stroke_width": 0.01}}], 40.0, {})
    vec = out["vectors"][0]
    assert vec["color"] == "#FFFFFF"
    assert vec["fill_opacity"] == 0.0
    assert vec["stroke_color"] == "#FFFFFF"


def test_see_through_fill_renders_with_its_own_fill_alpha():
    import graphics
    tag = graphics._fill_alpha_tag({"fill_opacity": 0.0}, 1.0)
    assert tag == r"\1a&HFF&"
    assert graphics._fill_alpha_tag({"fill_opacity": None}, 1.0) == ""
    assert graphics._fill_alpha_tag({}, 1.0) == ""


def test_text_colour_without_hash_and_named_colour_are_repaired():
    import edit_batch
    out = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "texts", "id": "t1",
        "value": {"text": "HELLO", "start": 1.0, "end": 3.0,
                  "color": "ffcc00", "accent_color": "red"}}], 40.0, {})
    tx = out["texts"][0]
    assert tx["color"] == "#FFCC00" and tx["accent_color"] == "#FF0000"


def test_item_upsert_with_its_id_only_in_the_value():
    import edit_batch
    out = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "texts",
        "value": {"id": "t9", "text": "A", "start": 1.0, "end": 3.0}}], 40.0, {})
    assert [t["id"] for t in out["texts"]] == ["t9"]


def test_item_upsert_with_no_id_at_all_adds_a_new_row():
    import edit_batch
    g = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "texts", "id": "tx1",
        "value": {"text": "A", "start": 1.0, "end": 3.0}}], 40.0, {})
    out = edit_batch.apply_batch(g, [{
        "action": "upsert", "layer": "texts",
        "value": {"text": "B", "start": 4.0, "end": 6.0}}], 40.0, {})
    ids = [t["id"] for t in out["texts"]]
    assert len(ids) == 2 and len(set(ids)) == 2 and "tx1" in ids


def test_item_upsert_with_a_list_upserts_each_row():
    import edit_batch
    out = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "texts",
        "value": [{"id": "a", "text": "A", "start": 1.0, "end": 3.0},
                  {"text": "B", "start": 4.0, "end": 6.0}]}], 40.0, {})
    assert len(out["texts"]) == 2 and out["texts"][0]["id"] == "a"


def test_remove_naming_nothing_changes_nothing_and_does_not_refuse():
    import edit_batch
    g = edit_batch.apply_batch(_base(), [{
        "action": "upsert", "layer": "texts", "id": "tx1",
        "value": {"text": "A", "start": 1.0, "end": 3.0}}], 40.0, {})
    out = edit_batch.apply_batch(g, [
        {"action": "remove", "layer": "texts"},
        {"action": "upsert", "layer": "texts", "id": "tx1", "value": {"end": 4.0}}],
        40.0, {})
    assert [t["id"] for t in out["texts"]] == ["tx1"]


def test_speed_span_upsert_and_remove_by_id():
    import edit_batch
    g = edit_batch.apply_batch(_base(), [{
        "action": "set", "layer": "speed",
        "value": [{"id": "sp1", "start": 0.0, "end": 5.0, "factor": 0.7},
                  {"id": "sp2", "start": 12.0, "end": 20.0, "factor": 1.15}]}],
        40.0, {})
    out = edit_batch.apply_batch(g, [
        {"action": "upsert", "layer": "speed", "id": "sp1", "value": {"factor": 0.5}},
        {"action": "remove", "layer": "speed", "id": "sp2"}], 40.0, {})
    assert [(s["id"], s["factor"]) for s in out["speed"]] == [("sp1", 0.5)]
