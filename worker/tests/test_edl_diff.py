"""edl_diff.change_ranges — the structural what-changed diff behind the
studio's transient edit highlight (round 71). Pure logic.

Run from worker/:  python3 -m pytest tests/test_edl_diff.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from edl_diff import change_ranges                           # noqa: E402


def _edl(keep=None, **kw):
    base = {"keep": keep if keep is not None else [[0.0, 10.0]]}
    base.update(kw)
    return base


def test_identical_is_none():
    assert change_ranges(_edl(), _edl()) is None


def test_text_added_is_its_window():
    prev = _edl()
    new = _edl(texts=[{"id": "tx1", "text": "HI", "start": 2.0, "end": 3.5,
                       "template": "title"}])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[2.0, 3.5]] and c["global"] is False


def test_text_removed_is_a_point():
    prev = _edl(texts=[{"id": "tx1", "text": "HI", "start": 2.0, "end": 3.5,
                        "template": "title"}])
    c = change_ranges(prev, _edl())
    assert c["out_ranges"] == [[2.0, 2.0]]


def test_cut_range_is_a_junction_point():
    prev = _edl([[0.0, 10.0]])
    new = _edl([[0.0, 4.0], [6.0, 10.0]])
    c = change_ranges(prev, new)
    # the cut 4-6s closes up at output 4.0
    assert c["out_ranges"] == [[4.0, 4.0]]


def test_restore_is_the_restored_span():
    prev = _edl([[0.0, 4.0], [6.0, 10.0]])
    new = _edl([[0.0, 10.0]])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[4.0, 6.0]]


def test_insert_added_is_its_program_window():
    prev = _edl([[0.0, 5.0]])
    new = _edl([[0.0, 5.0]],
               inserts=[{"id": "ins1", "asset_key": "k", "kind": "video",
                         "at_output_s": 5.0, "duration_s": 3.0}])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[5.0, 8.0]]


def test_insert_removed_is_a_point():
    prev = _edl([[0.0, 5.0]],
                inserts=[{"id": "ins1", "asset_key": "k", "kind": "video",
                          "at_output_s": 5.0, "duration_s": 3.0}])
    c = change_ranges(prev, _edl([[0.0, 5.0]]))
    assert c["out_ranges"] == [[5.0, 5.0]]


def test_grade_change_is_global():
    prev = _edl()
    new = _edl(effects={"grade": "cinematic"})
    c = change_ranges(prev, new)
    assert c["global"] is True and c["out_ranges"] == []


def test_zoom_added_is_local_not_global():
    prev = _edl()
    new = _edl(effects={"zooms": [{"id": "z1", "start": 1.0, "end": 2.5,
                                   "strength": 0.2}]})
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[1.0, 2.5]] and c["global"] is False


def test_close_ranges_merge():
    prev = _edl()
    new = _edl(texts=[{"id": "a", "text": "x", "start": 1.0, "end": 2.0,
                       "template": "title"},
                      {"id": "b", "text": "y", "start": 2.1, "end": 3.0,
                       "template": "title"}])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[1.0, 3.0]]


def test_never_raises_on_garbage():
    assert change_ranges({"keep": "??"}, _edl()) is None


def test_cut_range_reports_the_removed_span_in_old_coordinates():
    """The viewer is still watching the PREVIOUS render while the turn
    runs — cut_ranges point at the doomed material in that clock."""
    prev = _edl([[0.0, 10.0]])
    new = _edl([[0.0, 4.0], [6.0, 10.0]])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[4.0, 4.0]]      # junction in the NEW program
    assert c["cut_ranges"] == [[4.0, 6.0]]      # the span in the OLD program


def test_cut_after_earlier_cut_maps_through_old_timeline():
    """prev already has a cut, so old-program coordinates != source."""
    prev = _edl([[0.0, 2.0], [5.0, 10.0]])      # old program: 7s
    new = _edl([[0.0, 2.0], [8.0, 10.0]])       # cuts source 5-8
    c = change_ranges(prev, new)
    # source 5-8 sits at old-program 2-5
    assert c["cut_ranges"] == [[2.0, 5.0]]


def test_insert_removed_reports_its_old_window_as_cut():
    prev = _edl([[0.0, 5.0]],
                inserts=[{"id": "ins1", "asset_key": "k", "kind": "video",
                          "at_output_s": 5.0, "duration_s": 3.0}])
    c = change_ranges(prev, _edl([[0.0, 5.0]]))
    assert c["out_ranges"] == [[5.0, 5.0]]
    assert c["cut_ranges"] == [[5.0, 8.0]]


def test_text_removed_reports_its_old_window_as_cut():
    prev = _edl(texts=[{"id": "tx1", "text": "HI", "start": 2.0, "end": 3.5,
                        "template": "title"}])
    c = change_ranges(prev, _edl())
    assert c["cut_ranges"] == [[2.0, 3.5]]


def test_additions_have_no_cut_ranges():
    new = _edl(texts=[{"id": "tx1", "text": "HI", "start": 2.0, "end": 3.5,
                       "template": "title"}])
    c = change_ranges(_edl(), new)
    assert c["cut_ranges"] == []


def test_many_cuts_stay_red_never_global_white():
    """A cut_silences-style pass (dozens of removals) must NOT collapse to
    the global full-bar shimmer — the red cut set carries the story."""
    keep_new = []
    t = 0.0
    for _ in range(20):                     # 20 kept islands, 19 gaps cut
        keep_new.append([t, t + 1.0])
        t += 2.0
    c = change_ranges(_edl([[0.0, t]]), _edl(keep_new))
    assert c["global"] is False
    assert c["out_ranges"] == []            # white noise dropped
    assert len(c["cut_ranges"]) == 20       # the gaps, in old coordinates


def _ins(id, at, dur, src=0.0, asset="k"):
    return {"id": id, "asset_key": asset, "kind": "video",
            "at_output_s": at, "duration_s": dur, "source_start_s": src}


def test_trimmed_insert_reports_removed_content_as_red():
    """set_insert_window shrinking a clip is a REMOVAL to the viewer — the
    session that motivated this saw an 14.6s screen recording trimmed to
    3s with no red at all."""
    prev = _edl([[0.0, 5.0]], inserts=[_ins("ins2", 5.0, 14.62)])
    new = _edl([[0.0, 5.0]], inserts=[_ins("ins2", 5.0, 3.0, src=4.0)])
    c = change_ranges(prev, new)
    # old window 5-19.62; surviving content 4-7 sat at old 9-12
    assert c["cut_ranges"] == [[5.0, 9.0], [12.0, 19.62]]
    # a pure trim adds NO white over the survivor
    assert c["out_ranges"] == []


def test_split_insert_reds_only_the_removed_middle():
    """cut_output_range splits ins2 into head (id kept) + tail (new id):
    only the cut middle is red, and neither surviving piece is white."""
    prev = _edl([[0.0, 5.0]], inserts=[_ins("ins2", 5.0, 10.0)])
    new = _edl([[0.0, 5.0]],
               inserts=[_ins("ins2", 5.0, 4.0),
                        _ins("ins3", 5.0, 3.0, src=7.0)])
    c = change_ranges(prev, new)
    # removed clip content 4-7 sat at old-program 9-12
    assert c["cut_ranges"] == [[9.0, 12.0]]
    assert c["out_ranges"] == []


def test_moved_insert_is_still_white():
    prev = _edl([[0.0, 5.0]], inserts=[_ins("ins1", 0.0, 3.0)])
    new = _edl([[0.0, 5.0]], inserts=[_ins("ins1", 5.0, 3.0)])
    c = change_ranges(prev, new)
    assert c["out_ranges"] == [[5.0, 8.0]]
