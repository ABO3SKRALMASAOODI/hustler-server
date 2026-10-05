"""Production v8 canary: deliberate graphics must survive the review path."""
import pytest

import audit
import taste
from timeline import Timeline


def findings(texts, mutes=None):
    edl = {"keep": [[0, 12]], "frame": {"ratio": "9:16"},
           "captions": {"mode": "from_transcript", "style": {"anchor_y": .66}},
           "texts": texts, "caption_mutes": mutes or []}
    index = {"words": [{"w": "speech", "t0": 0, "t1": 1}],
             "shots": [{"t0": 0, "t1": 12}]}
    return taste.critique(edl, index, Timeline(edl["keep"], [], []), 1920, 1080)


def label(text, x, **kwargs):
    return {"text": text, "start": 0, "end": 4, "x": x, "y": .21,
            "size_scale": .45, "mute_captions": False, **kwargs}


def test_diagram_labels_and_dialogue_are_not_deleted_for_coexisting():
    assert findings([label("RUSSIA", .23), label("ENGINES", .76)]) == []


def test_explicitly_overlapping_labels_still_require_composition_review():
    result = findings([label("RUSSIA", .5), label("ENGINES", .5)])
    assert any("composition check" in text for text in result)


def test_scale_motion_uses_largest_authored_bounds():
    result = findings([label("RUSSIA", .25, motion={"scale": [
        {"t": 0, "v": 1}, {"t": 1, "v": 4}]}), label("ENGINES", .65)])
    assert any("composition check" in text for text in result)


def test_moving_label_is_not_certified_from_its_initial_anchor():
    result = findings([label("RUSSIA", .23, motion={"x": [
        {"t": 0, "v": .23}, {"t": 1, "v": .76}]}), label("ENGINES", .76)])
    assert any("composition check" in text for text in result)


def test_owned_caption_mute_is_recognized_without_duplicate_manual_mute():
    assert findings([label("MADE IN HOUSE", .5, mute_captions=True)]) == []


def test_legacy_unknown_caption_intent_retains_review():
    text = label("Unplaced callout", .5)
    text.pop("mute_captions")
    assert any("two layers of text" in message for message in findings([text]))


def test_adjacent_camera_segments_do_not_cut_speech():
    words = [{"w": "Well", "t0": 9.87, "t1": 10.43}]
    assert audit.midword_boundaries([[0, 10.06], [10.06, 16.58]], words, 16.58) == []


@pytest.mark.parametrize("keep", [
    [[0, 10.06], [10.16, 16.58]],
    [[10.06, 16.58], [0, 10.06]],
    [[0, 10.06]],
])
def test_removed_or_reordered_speech_still_reports_midword_boundary(keep):
    words = [{"w": "Well", "t0": 9.87, "t1": 10.43}]
    assert audit.midword_boundaries(keep, words, 16.58)
