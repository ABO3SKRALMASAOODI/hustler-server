"""Engines release: the render check knows the effects-graph devices.

Live check on project 3419 (W1 deployed): a 1-frame negative one frame off
a cut read as two JUMP OFF A CUT findings, a VERSION VERIFICATION repair
that holds the export. Device windows (EDL ``fx``) are deliberate changes
like a stylize flash: short ones over their whole window, sustained ones at
their edges only (a pop inside a long section is still caught)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import render_qc                                       # noqa: E402
from schemas import default_edl, validate_edl          # noqa: E402


def _changes(n=300, spikes=()):
    ch = [(1.0, 0.01)] * n
    for k, level, share in spikes:
        ch[k] = (level, share)
    return ch


def _plan(fx):
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 10.0]]
    edl["fx"] = fx
    edl = validate_edl(edl, 20.0).model_dump()
    return render_qc.plan(edl, {}, W=1080, H=1920, fps=30.0)


def test_a_one_frame_negative_off_a_cut_is_deliberate():
    p = _plan([{"id": "n1", "type": "accent", "stage": "over_type",
                "start": 5.0, "end": 5.034, "kind": "negative"}])
    # the negative frame 150 jumps in and out (changes into 150 and 151)
    ch = _changes(spikes=[(149, 90.0, .95), (150, 90.0, .95)])
    assert render_qc.jumps(ch, p) == []


def test_a_white_hit_with_its_decay_is_deliberate():
    p = _plan([{"id": "w1", "type": "accent", "stage": "pre_captions",
                "start": 4.0, "end": 4.034, "kind": "white_hit",
                "decay_f": 5}])
    ch = _changes(spikes=[(119, 80.0, .95), (125, 60.0, .9)])
    assert render_qc.jumps(ch, p) == []


def test_a_long_grade_section_exempts_its_edges_not_its_middle():
    p = _plan([{"id": "g1", "type": "grade_window", "stage": "world",
                "start": 2.0, "end": 8.0, "saturation": 0.0}])
    # colour leaves at frame 60 and returns at frame 240: deliberate
    edges = _changes(spikes=[(59, 40.0, .95), (239, 40.0, .95)])
    assert render_qc.jumps(edges, p) == []
    # a framing pop at 5 s inside the section is still a finding
    mid = _changes(spikes=[(149, 90.0, .95)])
    assert render_qc.jumps(mid, p) != []


def test_without_fx_nothing_changes():
    p = _plan([])
    ch = _changes(spikes=[(149, 90.0, .95)])
    assert render_qc.jumps(ch, p) != []
