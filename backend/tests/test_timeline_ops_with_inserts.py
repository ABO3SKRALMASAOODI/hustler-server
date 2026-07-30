"""Deleting a chunk with a clip spliced in — the round-60 dead end.

Project 246, v19: keep [[111.85, 130.08], [339.27, 354.61]] and one clip at
33.57s, which is the LAST keep boundary. The user pressed delete on a chunk and
got

    inserts[0].at_output_s 33.57 is not on a keep-segment boundary —
    nearest boundary is 18.23. Inserts splice BETWEEN kept segments
    (or at the start/end).

Nothing was deleted. Every other click in the timeline hit the same wall,
because this route applied the op and validated it without ever moving the
inserts — the guard was right and the write was incomplete.

These tests drive the real route functions (_apply_edl_op + _reanchor_after_op)
and then validate with the same schema the worker uses, so they fail if either
half regresses.

    cd backend && python -m pytest tests/test_timeline_ops_with_inserts.py -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402

wschemas = video.wschemas
SRC_DUR = 400.0

PROD_KEEP = [[111.85, 130.08], [339.27, 354.61]]
PROD_INS = [{"id": "ins1", "asset_key": "clips/246/5d5fc51b50b5.mov",
             "kind": "video", "at_output_s": 33.57, "duration_s": 10.0}]


def prod_edl():
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [list(k) for k in PROD_KEEP]
    e["inserts"] = [dict(i) for i in PROD_INS]
    return wschemas.validate_edl(e, SRC_DUR).model_dump()


def apply(edl, op, args, assets=None):
    """One UI click, end to end: the op, the re-anchor, the validation."""
    new, desc = video._apply_edl_op(edl, op, args, assets or {},
                                    src_dur=SRC_DUR)
    new, desc = video._reanchor_after_op(edl, new, desc)
    return wschemas.validate_edl(new, SRC_DUR).model_dump(), desc


# ── the click that failed ───────────────────────────────────────────────────

@pytest.mark.parametrize("index, survives, at", [
    (1, [111.85, 130.08], 18.23),    # delete the second take
    (0, [339.27, 354.61], 15.34),    # delete the first take
])
def test_deleting_a_chunk_now_works_and_keeps_the_clip(index, survives, at):
    out, desc = apply(prod_edl(), "remove_keep_segment", {"index": index})
    assert out["keep"] == [survives]
    assert len(out["inserts"]) == 1
    assert out["inserts"][0]["at_output_s"] == at
    assert out["inserts"][0]["asset_key"] == PROD_INS[0]["asset_key"]
    assert "deleted the clip" in desc


def test_the_user_is_told_the_clip_moved():
    _out, desc = apply(prod_edl(), "remove_keep_segment", {"index": 1})
    assert "ins1" in desc and "18.23" in desc


def test_without_the_reanchor_the_same_click_still_400s():
    """Proof the test is testing something: skip _reanchor_after_op and the
    schema rejects it with the exact message the user saw."""
    edl = prod_edl()
    new, _desc = video._apply_edl_op(edl, "remove_keep_segment", {"index": 1},
                                     {}, src_dur=SRC_DUR)
    with pytest.raises(wschemas.EDLValidationError) as ex:
        wschemas.validate_edl(new, SRC_DUR)
    assert "not on a keep-segment boundary" in str(ex.value)


# ── the neighbours, which had the same hole ─────────────────────────────────

def test_trimming_a_chunk_keeps_the_clip_at_the_end():
    """Dragging the last take's tail in by 5s moves the final boundary; the
    clip pinned there moves with it instead of being orphaned."""
    out, _ = apply(prod_edl(), "trim_keep_segment",
                   {"index": 1, "edge": "end", "delta_s": -5.0})
    assert out["keep"][1][1] == 349.61
    assert out["inserts"][0]["at_output_s"] == 28.57


def test_trimming_an_earlier_chunk_moves_the_clip_with_the_programme():
    out, _ = apply(prod_edl(), "trim_keep_segment",
                   {"index": 0, "edge": "start", "delta_s": 3.0})
    assert out["keep"][0][0] == 114.85
    assert out["inserts"][0]["at_output_s"] == 30.57


def test_a_split_leaves_every_insert_exactly_where_it_was():
    """A split renders the identical programme, so nothing may move — and the
    clip's boundary must survive the extra one the split introduces."""
    out, _ = apply(prod_edl(), "split_keep", {"at_program_s": 9.0})
    assert len(out["keep"]) == 3
    assert out["inserts"][0]["at_output_s"] == 33.57


def test_split_then_delete_the_split_half_is_the_whole_manual_cut():
    """The idiom the studio actually sends, twice over, with a clip present."""
    edl = prod_edl()
    edl, _ = apply(edl, "split_keep", {"at_program_s": 9.0})
    edl, _ = apply(edl, "remove_keep_segment", {"index": 0})
    assert edl["keep"] == [[120.85, 130.08], [339.27, 354.61]]
    # 9.23 + 15.34 of surviving footage
    assert edl["inserts"][0]["at_output_s"] == 24.57
    edl, _ = apply(edl, "remove_keep_segment", {"index": 1})
    assert edl["keep"] == [[120.85, 130.08]]
    assert edl["inserts"][0]["at_output_s"] == 9.23


# ── the other way this op could strand a project ────────────────────────────

def test_deleting_the_last_take_is_refused_in_words_that_mean_something():
    """Popping the only keep segment leaves keep empty with no canvas, which
    validate_edl rejects in language about canvas programs. Say the real thing
    instead."""
    edl = prod_edl()
    edl, _ = apply(edl, "remove_keep_segment", {"index": 1})
    with pytest.raises(ValueError) as ex:
        apply(edl, "remove_keep_segment", {"index": 0})
    msg = str(ex.value)
    assert "spliced-in clips" in msg and "canvas" not in msg.lower()


def test_a_behind_subject_text_cannot_be_dragged_in_the_timeline():
    """It owns a mask measured from particular frames. Dragging the block would
    leave the words on a different second of footage and cut the subject out
    where they are not — the same reason a screen takeover's block is locked."""
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [[0.0, 60.0]]
    e["texts"] = [{"id": "tx1", "text": "BEHIND", "start": 2.0, "end": 5.0,
                   "behind": {"asset_key": "matte/1/a.mp4", "src_start": 2.0,
                              "src_end": 5.0, "fp": "f"}}]
    edl = wschemas.validate_edl(e, SRC_DUR).model_dump()
    with pytest.raises(ValueError) as ex:
        apply(edl, "retime_text", {"id": "tx1", "start": 20.0})
    assert "BEHIND the subject" in str(ex.value)
    # an ordinary text still drags
    e["texts"][0].pop("behind")
    plain = wschemas.validate_edl(e, SRC_DUR).model_dump()
    out, _ = apply(plain, "retime_text", {"id": "tx1", "start": 20.0})
    assert out["texts"][0]["start"] == 20.0


def test_no_inserts_still_behaves_exactly_as_before():
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [[0.0, 10.0], [20.0, 30.0]]
    out, _ = apply(wschemas.validate_edl(e, SRC_DUR).model_dump(),
                   "remove_keep_segment", {"index": 0})
    assert out["keep"] == [[20.0, 30.0]]
    assert out["inserts"] == []
