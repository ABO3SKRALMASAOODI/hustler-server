"""Two inserts at ONE boundary play in LIST order (round 61).

Sharing a boundary is not an edge case — it is the only way the EDL can say
"this clip, then that clip, both spliced at the same cut", and it is exactly
what splitting one inserted clip in two produces. 23 EDLs in production hold
co-located inserts.

Timeline used to sort the (at_output_s, duration_s) TUPLE, so the SHORTER block
played first. Three things depended on the order and only one of them agreed:

  * schemas.validate_edl sorts on at_output_s ALONE (stable), so the list keeps
    creation order;
  * renderer.build_filtergraph iterates `inserts` in LIST order and pairs input
    N with window N, on a comment asserting "sorted by validate_edl = tl.ins
    order";
  * Timeline re-sorted by duration, quietly making that comment false.

So a clip split into a long head and a short tail rendered tail-first, and the
studio (whose JS mirror had always sorted on at_output_s alone) drew it
head-first. These tests pin the agreement rather than the fix.

    cd worker && python -m pytest tests/test_insert_order.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import schemas                                                   # noqa: E402
import timeline as tlmod                                          # noqa: E402

SRC = 400.0
CLIP = "clips/246/3a38d20ff110.mov"


def split_edl(head, tail, at=17.67, keep=None):
    """One clip spliced at `at`, split into a head and a tail — head FIRST in
    the list, which is what _split_insert produces."""
    e = schemas.default_edl(SRC)
    e["keep"] = keep or [[111.85, 129.52]]
    e["inserts"] = [
        {"id": "ins1", "asset_key": CLIP, "kind": "video",
         "at_output_s": at, "duration_s": head, "source_start_s": 5.97},
        {"id": "ins2", "asset_key": CLIP, "kind": "video",
         "at_output_s": at, "duration_s": tail, "source_start_s": 5.97 + head},
    ]
    return schemas.validate_edl(e, SRC).model_dump()


# ── the inversion ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("head, tail", [
    (9.33, 4.12),      # split late: head is LONGER — the broken case
    (4.12, 9.33),      # split early: head is shorter — passed even when broken
    (6.0, 6.0),        # equal: only list order can decide
])
def test_the_head_always_plays_before_the_tail(head, tail):
    edl = split_edl(head, tail)
    tl = tlmod.Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    wins = tlmod.insert_windows(edl["inserts"], tl)
    assert wins["ins1"][0] < wins["ins2"][0], (
        f"head {head}s must precede tail {tail}s, got {wins}")
    # ...and they are adjacent, with no gap and no overlap.
    assert wins["ins1"][1] == pytest.approx(wins["ins2"][0], abs=0.01)


def test_validate_edl_does_not_reorder_a_shared_boundary():
    """It sorts on at_output_s alone. If it ever sorted the tuple instead, the
    renderer's list-order iteration would silently invert again."""
    edl = split_edl(9.33, 4.12)
    assert [i["id"] for i in edl["inserts"]] == ["ins1", "ins2"]
    assert [i["duration_s"] for i in edl["inserts"]] == [9.33, 4.12]


def test_timeline_ins_order_matches_the_validated_list_order():
    """THE invariant renderer.build_filtergraph rests on: it walks `inserts` in
    list order and pairs input N with the Nth window Timeline reports."""
    edl = split_edl(9.33, 4.12)
    tl = tlmod.Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    listed = [(float(i["at_output_s"]), float(i["duration_s"]))
              for i in edl["inserts"]]
    assert tl.ins == listed
    starts = [s for s, _d in tl.insert_positions()]
    assert starts == sorted(starts)


def test_splitting_never_changes_the_program_length():
    whole = split_edl(9.33, 4.12)
    one = dict(whole)
    one["inserts"] = [dict(whole["inserts"][0], duration_s=9.33 + 4.12)]
    assert schemas.program_duration(whole) == schemas.program_duration(one)


def test_transition_junctions_pairs_each_junction_with_its_own_length():
    """transition_junctions builds its own block list and indexes ins_durs by
    the same counter as at_list. Those two came from DIFFERENT sorts before, so
    a co-located pair mismatched every junction after it."""
    edl = split_edl(9.33, 4.12, keep=[[0.0, 10.0], [20.0, 30.0]], at=10.0)
    edl["effects"] = dict(edl.get("effects") or {},
                          transition={"kind": "dip_to_black", "duration_s": 0.4,
                                      "scope": "every_cut"})
    js = tlmod.transition_junctions(edl, index=None)
    # blocks: seg0 | ins1 | ins2 | seg1  -> 3 junctions, and it must not raise
    # or fall back to a different count.
    assert js == {0, 1, 2}, js


def test_a_single_insert_is_bit_identical_to_before():
    """The no-tie case is the overwhelming majority of production EDLs; the
    sort change must not move a single number in it."""
    e = schemas.default_edl(SRC)
    e["keep"] = [[111.85, 129.52]]
    e["inserts"] = [{"id": "ins1", "asset_key": CLIP, "kind": "video",
                     "at_output_s": 17.67, "duration_s": 13.45,
                     "source_start_s": 5.97}]
    edl = schemas.validate_edl(e, SRC).model_dump()
    tl = tlmod.Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    assert tl.ins == [(17.67, 13.45)]
    assert tl.out_duration == pytest.approx(31.12, abs=0.01)
    assert tlmod.insert_windows(edl["inserts"], tl) == {"ins1": (17.67, 31.12)}


def test_many_inserts_at_many_boundaries_stay_grouped_and_ordered():
    """Ties break by list order; different boundaries still sort by position,
    including when the list is given out of order."""
    e = schemas.default_edl(SRC)
    e["keep"] = [[0.0, 10.0], [20.0, 30.0], [40.0, 50.0]]
    e["inserts"] = [
        {"id": "b1", "asset_key": CLIP, "kind": "video",
         "at_output_s": 20.0, "duration_s": 5.0},
        {"id": "a1", "asset_key": CLIP, "kind": "video",
         "at_output_s": 10.0, "duration_s": 9.0},
        {"id": "a2", "asset_key": CLIP, "kind": "video",
         "at_output_s": 10.0, "duration_s": 1.0},
    ]
    edl = schemas.validate_edl(e, SRC).model_dump()
    assert [i["id"] for i in edl["inserts"]] == ["a1", "a2", "b1"]
    tl = tlmod.Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    wins = tlmod.insert_windows(edl["inserts"], tl)
    assert wins["a1"][0] < wins["a2"][0] < wins["b1"][0]
    assert wins["a1"] == (10.0, 19.0) and wins["a2"] == (19.0, 20.0)
