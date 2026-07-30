"""Round 60 — an insert follows its JUNCTION when the keep list changes.

An insert's at_output_s has to land exactly on a keep boundary or validate_edl
refuses the whole EDL. So every write that touches the keep list has to move
the inserts with it, and the only stable way to say WHICH junction an insert
was at is the footage it sat in front of: boundary output positions all move
when anything upstream is trimmed or deleted.

The case that made this a bug is pinned first, from production. Project 246,
v19: keep [[111.85, 130.08], [339.27, 354.61]] with one clip at the LAST
boundary, 33.57s. The user tried to delete a take and got

    inserts[0].at_output_s 33.57 is not on a keep-segment boundary —
    nearest boundary is 18.23.

Nothing was deleted, and no click in the timeline could get out of that state,
because the backend's keep ops never re-snapped inserts at all.

Run:  python -m pytest tests/test_insert_resnap.py -q      (from worker/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                                 # noqa: E402

import timeline as tl_mod                                     # noqa: E402
from schemas import (EDLValidationError, default_edl,          # noqa: E402
                     keep_boundaries, validate_edl)

SRC_DUR = 400.0


def _ins(at, iid="ins1", dur=10.0):
    return {"id": iid, "asset_key": "clips/246/x.mov", "kind": "video",
            "at_output_s": at, "duration_s": dur}


def _edl(keep, inserts, speed=None):
    e = default_edl(SRC_DUR)
    e["keep"] = [list(k) for k in keep]
    e["inserts"] = [dict(i) for i in inserts]
    if speed:
        e["speed"] = [dict(s) for s in speed]
    return e


# ------------------------------------------------------------------ #
#  The production case                                                #
# ------------------------------------------------------------------ #

PROD_KEEP = [[111.85, 130.08], [339.27, 354.61]]
PROD_AT = 33.57          # the last boundary: 18.23 + 15.34


def test_prod_state_is_reproduced_exactly():
    """The starting state really did put the clip at the last boundary."""
    assert keep_boundaries(PROD_KEEP, []) == [0.0, 18.23, 33.57]
    validate_edl(_edl(PROD_KEEP, [_ins(PROD_AT)]), SRC_DUR)


@pytest.mark.parametrize("drop, expect_at", [
    # Deleting the SECOND take removes the boundary the clip sat on. The clip
    # was at the end of the programme and stays at the end of the programme.
    (1, 18.23),
    # Deleting the FIRST take does not remove that boundary, but it does move
    # it: everything shifts earlier by 18.23s.
    (0, 15.34),
])
def test_deleting_a_take_moves_the_clip_instead_of_400ing(drop, expect_at):
    keep = [k for i, k in enumerate(PROD_KEEP) if i != drop]
    inserts, notes = tl_mod.resnap_inserts([_ins(PROD_AT)], PROD_KEEP, keep)
    assert inserts[0]["at_output_s"] == expect_at
    assert notes and "ins1" in notes[0]
    # and the EDL the user's click produces now validates
    validate_edl(_edl(keep, inserts), SRC_DUR)


def test_without_the_resnap_that_edl_is_rejected():
    """The guard is real: this is the exact error the user was shown."""
    keep = [PROD_KEEP[0]]
    with pytest.raises(EDLValidationError) as ex:
        validate_edl(_edl(keep, [_ins(PROD_AT)]), SRC_DUR)
    assert "not on a keep-segment boundary" in str(ex.value)
    assert "18.23" in str(ex.value)


# ------------------------------------------------------------------ #
#  Following the same footage, not the same number                    #
# ------------------------------------------------------------------ #

def test_trimming_an_earlier_take_keeps_the_clip_at_its_own_cut():
    """Nearest-VALUE snapping is what this replaces.

    keep [[0,10],[10,20],[20,30]] with a clip at the third take's start
    (20.0). Trim 4s off the FIRST take and every boundary moves 4s earlier —
    16.0 is now the third take's start and 6.0 the second's. Nearest value
    from 20.0 is 16.0 here, so both methods agree; the divergence is in the
    next test.
    """
    old = [[0, 10], [10, 20], [20, 30]]
    new = [[4, 10], [10, 20], [20, 30]]
    ins, _ = tl_mod.resnap_inserts([_ins(20.0)], old, new)
    assert ins[0]["at_output_s"] == 16.0


def test_a_deleted_middle_take_collapses_the_seam_forward():
    """A clip in front of a take that is deleted stays in front of whatever
    now follows — where the viewer last saw it — not at the start."""
    old = [[0, 10], [10, 12], [20, 30]]
    new = [[0, 10], [20, 30]]          # the 2s take is gone
    ins, _ = tl_mod.resnap_inserts([_ins(10.0, dur=3.0)], old, new)
    assert ins[0]["at_output_s"] == 10.0        # still after the first take
    validate_edl(_edl(new, ins), SRC_DUR)


def test_nearest_value_would_hop_to_the_wrong_cut():
    """The case that makes anchors necessary rather than merely tidier.

    Three takes; a clip sits at the THIRD one's start. Take one is trimmed
    hard (18s of 20 removed), so the third take's junction moves from 40 to
    22 — and the SECOND take's junction (20) is now the nearest value to the
    clip's old 40. Value-snapping puts the clip in the middle of take two;
    the anchor keeps it where the user put it.
    """
    old = [[0, 20], [100, 120], [200, 220]]
    new = [[18, 20], [100, 120], [200, 220]]
    assert keep_boundaries(old, []) == [0.0, 20.0, 40.0, 60.0]
    assert keep_boundaries(new, []) == [0.0, 2.0, 22.0, 42.0]
    ins, _ = tl_mod.resnap_inserts([_ins(40.0)], old, new)
    assert ins[0]["at_output_s"] == 22.0
    nearest_value = min(keep_boundaries(new, []), key=lambda b: abs(b - 40.0))
    assert nearest_value == 42.0                # what shipped before: take four
    assert ins[0]["at_output_s"] != nearest_value


def test_a_split_is_a_no_op_for_every_insert():
    """Splitting a take adds a boundary and changes no output position, so
    nothing may move — a split that re-anchored inserts would silently rewrite
    the programme the user is looking at."""
    old = [[0, 30]]
    new = [[0, 18.54], [18.54, 30]]
    for at in (0.0, 30.0):
        ins, notes = tl_mod.resnap_inserts([_ins(at)], old, new)
        assert ins[0]["at_output_s"] == at
        assert notes == []


def test_speed_write_keeps_the_junction_on_the_new_clock():
    """Keep unchanged: every anchor matches itself, so the insert lands on the
    same junction INDEX at its speed-remapped position. This is the behaviour
    _write_speed used to hand-roll."""
    keep = [[0, 10], [10, 20]]
    speed = [{"id": "sp1", "start": 0.0, "end": 10.0, "factor": 4.0}]
    old_b = keep_boundaries(keep, [])
    new_b = keep_boundaries(keep, speed)
    assert old_b == [0.0, 10.0, 20.0] and new_b == [0.0, 2.5, 12.5]
    ins, notes = tl_mod.resnap_inserts([_ins(10.0)], keep, keep, [], speed)
    assert ins[0]["at_output_s"] == 2.5        # same junction, 4x intro
    assert notes and "2.5" in notes[0]
    # nearest VALUE from 10.0 would have been 12.5 — the NEXT take's start
    assert min(new_b, key=lambda b: abs(b - 10.0)) == 12.5


def test_every_insert_moves_and_ids_are_preserved():
    old = [[0, 10], [10, 20], [20, 30]]
    new = [[0, 10], [20, 30]]
    ins, notes = tl_mod.resnap_inserts(
        [_ins(10.0, "insA", 2.0), _ins(30.0, "insB", 2.0)], old, new)
    assert [i["id"] for i in ins] == ["insA", "insB"]
    assert [i["at_output_s"] for i in ins] == [10.0, 20.0]
    assert len(notes) == 1        # insA did not move; insB did
    assert "insB" in notes[0]
    validate_edl(_edl(new, ins), SRC_DUR)


def test_no_inserts_is_cheap_and_silent():
    assert tl_mod.resnap_inserts([], [[0, 10]], [[0, 5]]) == ([], [])


def test_anchors_are_index_aligned_with_boundaries():
    for keep in ([[0, 10]], [[0, 10], [20, 30]], [[1, 2], [3, 4], [5, 6]]):
        assert len(tl_mod.keep_anchor_times(keep)) == \
            len(keep_boundaries(keep, []))
