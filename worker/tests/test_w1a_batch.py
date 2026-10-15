"""Track 1A: apply_edit_batch's program-matte rule never refuses a batch
for an item the batch did not touch (review).

A program-matte SubjectMatte is re-filled from its item's CURRENT window on
every batch (edit_batch._behind_through_program_matte). When a later batch
moves the timeline under an untouched item so that no main footage plays in
its window (an insert spliced over it), that item keeps its stored mask —
the render draws it as a plain title there — instead of the whole batch
(a Studio insert, an agent's unrelated edit) being refused for it. A batch
that WRITES such an item is still refused, naming it.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

KEY = "media/p1/card.png"


def _base():
    import edit_batch
    from schemas import validate_edl
    base = validate_edl({"keep": [[0.0, 10.0], [12.0, 30.0]]},
                        40.0).model_dump()
    return edit_batch.apply_batch(base, [{
        "action": "upsert", "layer": "texts", "id": "t1",
        "value": {"id": "t1", "text": "ACROSS", "start": 10.5, "end": 13.0,
                  "template": "title",
                  "behind": {"asset_key": edit_batch.PROGRAM_MATTE_KEY}}}],
        40.0, {KEY: {"duration_s": None}})


def _insert(at=10.0, dur=6.0):
    return {"action": "upsert", "layer": "inserts", "id": "i1",
            "value": {"id": "i1", "kind": "image", "asset_key": KEY,
                      "at_output_s": at, "duration_s": dur}}


def test_an_untouched_program_item_never_blocks_a_later_batch():
    import edit_batch
    g = _base()
    stored = dict(g["texts"][0]["behind"])
    assert stored["asset_key"] == edit_batch.PROGRAM_MATTE_KEY
    # an image spliced over the words' whole window: no footage under them
    out = edit_batch.apply_batch(g, [_insert()], 40.0,
                                 {KEY: {"duration_s": None}})
    assert out["inserts"][0]["id"] == "i1"
    assert out["texts"][0]["behind"] == stored          # kept as it was


def test_a_batch_that_writes_the_item_there_is_still_refused():
    import edit_batch
    g = edit_batch.apply_batch(_base(), [_insert()], 40.0,
                               {KEY: {"duration_s": None}})
    with pytest.raises(ValueError, match="no main footage"):
        edit_batch.apply_batch(g, [{
            "action": "upsert", "layer": "texts", "id": "t1",
            "value": {"id": "t1", "end": 12.5}}], 40.0,
            {KEY: {"duration_s": None}})
    with pytest.raises(ValueError, match="no main footage"):
        edit_batch.apply_batch(g, [{
            "action": "upsert", "layer": "texts", "id": "t2",
            "value": {"id": "t2", "text": "NEW", "start": 11.0, "end": 15.0,
                      "template": "title",
                      "behind": {"asset_key":
                                 edit_batch.PROGRAM_MATTE_KEY}}}], 40.0,
            {KEY: {"duration_s": None}})


def test_an_untouched_item_is_refilled_where_footage_plays():
    """The rule still re-solves an untouched item's source span from its
    current window when footage plays there (the batch contract: authored
    times describe the resulting timeline)."""
    import edit_batch
    g = _base()
    assert (g["texts"][0]["behind"]["src_start"],
            g["texts"][0]["behind"]["src_end"]) == (12.5, 15.0)
    out = edit_batch.apply_batch(g, [{"action": "set", "layer": "keep",
                                      "value": [[0.0, 9.0], [12.0, 30.0]]}],
                                 40.0, {})
    b = out["texts"][0]["behind"]
    assert (b["src_start"], b["src_end"]) == (13.5, 16.0)
