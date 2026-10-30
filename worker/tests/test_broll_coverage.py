"""Ticket #51 (30 Oct, user 2666): b-roll asked for "throughout" an 18 s talk
covered only 1-7 s, and nothing the agent read named the bare 7-15.4 s."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402
import timeline  # noqa: E402

TALK = {"keep": [[0.0, 15.44]]}


def test_two_clips_at_the_start_leave_the_rest_bare():
    edl = dict(TALK, overlays=[
        {"asset_key": "a", "kind": "video", "start": 1.0, "duration_s": 3.0, "scale": 1.0},
        {"asset_key": "b", "kind": "video", "start": 4.0, "duration_s": 3.0,
         "scale": [{"t": 0, "v": 1.0}, {"t": 3, "v": 1.1}]}])
    total, covered, bare = timeline.broll_coverage(edl)
    assert (total, covered, bare) == (15.44, 6.0, (7.0, 15.44))
    line = agent_tools._broll_coverage_line(edl)
    assert "B-ROLL COVERAGE" in line and "7-15.44s has none" in line


def test_a_logo_is_not_broll_and_no_broll_says_nothing():
    logo = dict(TALK, overlays=[{"asset_key": "l", "kind": "image", "start": 0,
                                 "duration_s": 15, "scale": 0.15}])
    assert timeline.broll_coverage(logo) is None
    assert agent_tools._broll_coverage_line(TALK) == ""


def test_well_covered_program_says_nothing():
    edl = dict(TALK, overlays=[
        {"asset_key": str(i), "kind": "video", "start": s, "duration_s": 3.0, "scale": 1.0}
        for i, s in enumerate((1.0, 5.0, 9.0, 12.0))])
    assert agent_tools._broll_coverage_line(edl) == ""
