"""A tray submitted after a trim cannot strand that project forever."""

import renderer
from schemas import validate_edl


def _broken():
    return {
        "keep": [[7.05, 13.33]],
        "inserts": [{"id": "ins6", "asset_key": "clip.mp4",
                     "kind": "video", "at_output_s": 13.33,
                     "duration_s": 3.0}],
    }


def test_legacy_source_clock_anchor_repairs_to_edited_end():
    fixed = renderer._repair_legacy_insert_boundaries(_broken())
    assert fixed["inserts"][0]["at_output_s"] == 6.28
    # The repaired result satisfies the actual renderer schema.
    assert validate_edl(fixed, 13.33).inserts[0].at_output_s == 6.28


def test_repair_does_not_mutate_stored_edl():
    broken = _broken()
    renderer._repair_legacy_insert_boundaries(broken)
    assert broken["inserts"][0]["at_output_s"] == 13.33


def test_valid_anchor_is_untouched():
    edl = _broken()
    edl["inserts"][0]["at_output_s"] = 6.28
    assert renderer._repair_legacy_insert_boundaries(edl) is edl


def test_unrelated_invalid_anchor_remains_strict():
    edl = _broken()
    edl["inserts"][0]["at_output_s"] = 3.14
    assert renderer._repair_legacy_insert_boundaries(edl) is edl
