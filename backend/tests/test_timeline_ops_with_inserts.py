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


# ── round 61: the clip dropped at the end ───────────────────────────────────
#
# Same session, later the same night. He dropped a 23.86s clip onto the end of
# the timeline and got a 10.0s block that showed no frames, then spent six
# drags across two positions getting its length back, then asked the agent to
# split it because the scissors refused.

CLIP = "clips/246/3a38d20ff110.mov"
CLIP_LEN = 23.86


def dropped_edl(dur=CLIP_LEN, at=18.23, src0=None):
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [list(k) for k in PROD_KEEP]
    ins = {"id": "ins1", "asset_key": CLIP, "kind": "video",
           "at_output_s": at, "duration_s": dur}
    if src0:
        ins["source_start_s"] = src0
    e["inserts"] = [ins]
    return wschemas.validate_edl(e, SRC_DUR).model_dump()


def test_a_dropped_clip_arrives_at_its_own_length_not_ten_seconds():
    """The 10s cap belonged to the AGENT's insert_media, which picks its own
    b-roll lengths. This op is only reached by a human dragging a file onto
    their own timeline; they chose the file."""
    edl = wschemas.validate_edl(
        {**wschemas.default_edl(SRC_DUR), "keep": [list(k) for k in PROD_KEEP]},
        SRC_DUR).model_dump()
    assets = {7: {"id": 7, "kind": "video_clip", "storage_key": CLIP,
                  "duration_s": CLIP_LEN}}
    out, desc = apply(edl, "insert_media", {"asset_id": 7, "at_output_s": 33.5},
                      assets)
    assert out["inserts"][0]["duration_s"] == CLIP_LEN, desc
    # ...and it landed at the END boundary, which is where he dropped it.
    assert out["inserts"][0]["at_output_s"] == 33.57


def test_a_long_recording_is_still_bounded_by_one_shared_ceiling():
    """Not by a second, different number: the drop and the resize handle have
    to agree, or a clip arrives at a length the chip then refuses to restore."""
    edl = wschemas.validate_edl(
        {**wschemas.default_edl(SRC_DUR), "keep": [list(k) for k in PROD_KEEP]},
        SRC_DUR).model_dump()
    assets = {7: {"id": 7, "kind": "video_clip", "storage_key": CLIP,
                  "duration_s": 5000.0}}
    out, _ = apply(edl, "insert_media", {"asset_id": 7, "at_output_s": 0.0},
                   assets)
    assert out["inserts"][0]["duration_s"] == video._INSERT_MAX_S
    capped, _ = apply(out, "set_insert_duration",
                      {"id": "ins1", "duration_s": 9999.0})
    assert capped["inserts"][0]["duration_s"] == video._INSERT_MAX_S


def test_the_scissors_split_an_inserted_clip():
    """out_to_src maps a program time inside a splice to None, and the answer
    used to be "move the playhead onto the footage" — a refusal to cut a block
    sitting right there on the timeline."""
    edl = dropped_edl()                       # plays 18.23 -> 42.09
    out, desc = apply(edl, "split_keep", {"at_program_s": 30.0})
    ins = out["inserts"]
    assert len(ins) == 2, desc
    head, tail = ins
    assert head["at_output_s"] == tail["at_output_s"] == 18.23
    assert head["duration_s"] == pytest.approx(11.77, abs=0.01)
    assert tail["duration_s"] == pytest.approx(12.09, abs=0.01)
    # The tail SEEKS — without this the second half replays the beginning and
    # "split" is indistinguishable from "duplicate".
    assert head.get("source_start_s") in (None, 0.0)
    assert tail["source_start_s"] == pytest.approx(11.77, abs=0.01)
    # The keep list is untouched and the program is exactly as long as before.
    assert out["keep"] == PROD_KEEP
    assert wschemas.program_duration(out) == wschemas.program_duration(edl)


def test_the_two_halves_play_head_first_even_when_the_head_is_longer():
    """The ordering that made this representable at all. Program order at a
    shared boundary is LIST order (timeline._ins_sort_key); sorting the
    (at, duration) tuple, as it used to, played the SHORTER half first — so
    splitting late in a clip put its tail before its head."""
    edl = dropped_edl()
    out, _ = apply(edl, "split_keep", {"at_program_s": 40.0})
    head, tail = out["inserts"]
    assert head["duration_s"] > tail["duration_s"]      # 21.77 vs 2.09
    tl = video.wtimeline.Timeline(out["keep"], out["inserts"], out.get("speed"))
    wins = video.wtimeline.insert_windows(out["inserts"], tl)
    assert wins[head["id"]][0] < wins[tail["id"]][0]
    assert wins[head["id"]][1] == pytest.approx(wins[tail["id"]][0], abs=0.01)


def test_a_split_half_can_be_deleted_and_the_other_survives():
    edl = dropped_edl()
    out, _ = apply(edl, "split_keep", {"at_program_s": 30.0})
    tail_id = out["inserts"][1]["id"]
    out, _ = apply(out, "remove_insert", {"id": tail_id})
    assert [i["id"] for i in out["inserts"]] == ["ins1"]
    assert out["inserts"][0]["duration_s"] == pytest.approx(11.77, abs=0.01)


def test_a_still_image_is_refused_in_words_rather_than_duplicated():
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [list(k) for k in PROD_KEEP]
    e["inserts"] = [{"id": "ins1", "asset_key": "images/1/a.png",
                     "kind": "image", "at_output_s": 18.23, "duration_s": 6.0}]
    edl = wschemas.validate_edl(e, SRC_DUR).model_dump()
    with pytest.raises(ValueError) as ex:
        apply(edl, "split_keep", {"at_program_s": 21.0})
    assert "still image" in str(ex.value)


def test_splitting_at_a_splice_edge_is_still_nothing_to_split():
    edl = dropped_edl()
    for at in (18.24, 42.08):
        with pytest.raises(ValueError) as ex:
            apply(edl, "split_keep", {"at_program_s": at})
        assert "already a clip edge" in str(ex.value)


def test_splitting_footage_is_completely_unchanged_by_any_of_this():
    edl = dropped_edl()
    out, desc = apply(edl, "split_keep", {"at_program_s": 10.0})
    assert out["keep"] == [[111.85, 121.85], [121.85, 130.08],
                           [339.27, 354.61]]
    assert len(out["inserts"]) == 1
    # ...and the clip is still in front of the same footage (source anchor
    # 339.27 — round 60's rule). Splitting take one adds a boundary BEFORE it,
    # so its output value is unchanged even though its junction index moved.
    assert out["inserts"][0]["at_output_s"] == 18.23


# ── round 75b: dragging a split scene between other splits ──────────────────

def _split_edl():
    """Every scene an insert at ONE boundary — exactly what splitting an
    inserted recording produces, and the launch-video timeline's real shape."""
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [[111.85, 115.4]]                     # 3.55s of footage
    e["inserts"] = [
        {"id": "a", "asset_key": "clips/1/rec.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 2.0, "source_start_s": 0.0},
        {"id": "b", "asset_key": "clips/1/rec.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 3.0, "source_start_s": 2.0},
        {"id": "c", "asset_key": "clips/1/rec.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 4.0, "source_start_s": 5.0}]
    return wschemas.validate_edl(e, SRC_DUR).model_dump()


def test_dragging_a_split_scene_between_two_other_splits_reorders():
    """The drag used to change at_output_s only — which, with every scene at
    the ONE boundary, was already its value: the drag did literally nothing,
    reported success, and the user filed 'im still not being able to put
    splitted scene between another splitted scenes'. The op now reorders
    within the boundary by the requested program time."""
    edl = _split_edl()
    # scenes: a 3.55-5.55, b 5.55-8.55, c 8.55-12.55; drag c between a and b
    out, desc = apply(edl, "move_insert", {"id": "c", "at_output_s": 5.55})
    assert [i["id"] for i in out["inserts"]] == ["a", "c", "b"]
    assert "5.55" in desc
    out2, _d = apply(out, "move_insert", {"id": "c", "at_output_s": 3.55})
    assert [i["id"] for i in out2["inserts"]] == ["c", "a", "b"]
    out3, _d = apply(out2, "move_insert", {"id": "c", "at_output_s": 12.0})
    assert [i["id"] for i in out3["inserts"]] == ["a", "b", "c"]
