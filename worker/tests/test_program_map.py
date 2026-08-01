"""The assembled-program scene map (round 71): program_blocks /
describe_program in timeline.py, and the output-time frame resolution that
look_at(output_times=...) builds on.

Pure logic — no ffmpeg, no DB. Run from worker/:
    python3 -m pytest tests/test_program_map.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from timeline import (Timeline, describe_program, insert_windows,  # noqa: E402
                      program_blocks)


def _edl(keep, inserts=None, speed=None):
    return {"keep": keep, "inserts": inserts or [], "speed": speed or []}


def test_single_segment_no_inserts():
    blocks = program_blocks(_edl([[113.7, 117.25]]))
    assert len(blocks) == 1
    b = blocks[0]
    assert b["n"] == 1 and b["kind"] == "footage"
    assert b["out_start"] == 0.0 and b["out_end"] == 3.55
    assert b["src_start"] == 113.7 and b["src_end"] == 117.25


def test_session_shape_inserts_at_end():
    """The real incident shape: one kept segment, two clips spliced at its
    end. The user's 'second scene' is ins1, the 'third scene' is ins2."""
    ins = [{"id": "ins1", "asset_key": "clips/300/b3729228033d.mov",
            "kind": "video", "at_output_s": 3.55, "duration_s": 4.47,
            "source_start_s": 12.41},
           {"id": "ins2", "asset_key": "clips/300/85b46dc146a7.mov",
            "kind": "video", "at_output_s": 3.55, "duration_s": 14.62,
            "source_start_s": 0.0}]
    edl = _edl([[113.7, 117.25]], ins)
    blocks = program_blocks(edl)
    assert [b["kind"] for b in blocks] == ["footage", "insert", "insert"]
    assert blocks[1]["id"] == "ins1" and blocks[2]["id"] == "ins2"
    assert blocks[1]["out_start"] == 3.55 and blocks[1]["out_end"] == 8.02
    assert blocks[2]["out_start"] == 8.02 and blocks[2]["out_end"] == 22.64
    # windows agree with insert_windows (the pre-existing primitive)
    tl = Timeline(edl["keep"], ins)
    wins = insert_windows(ins, tl)
    assert wins["ins1"] == (3.55, 8.02)
    assert wins["ins2"] == (8.02, 22.64)


def test_insert_before_first_segment():
    ins = [{"id": "a", "asset_key": "k", "kind": "image",
            "at_output_s": 0.0, "duration_s": 2.0}]
    blocks = program_blocks(_edl([[10.0, 15.0]], ins))
    assert [b["kind"] for b in blocks] == ["insert", "footage"]
    assert blocks[0]["out_end"] == 2.0
    assert blocks[1]["out_start"] == 2.0 and blocks[1]["out_end"] == 7.0


def test_mid_edit_insert_between_segments():
    ins = [{"id": "b", "asset_key": "k", "kind": "video",
            "at_output_s": 5.0, "duration_s": 3.0, "source_start_s": 1.0}]
    blocks = program_blocks(_edl([[0.0, 5.0], [20.0, 25.0]], ins))
    assert [(b["kind"], b["out_start"], b["out_end"]) for b in blocks] == [
        ("footage", 0.0, 5.0), ("insert", 5.0, 8.0), ("footage", 8.0, 13.0)]
    assert blocks[0]["n"] == 1 and blocks[2]["n"] == 3


def test_canvas_program_is_inserts_only():
    ins = [{"id": "c1", "asset_key": "x", "kind": "video",
            "at_output_s": 0.0, "duration_s": 4.0},
           {"id": "c2", "asset_key": "y", "kind": "image",
            "at_output_s": 0.0, "duration_s": 3.0}]
    blocks = program_blocks(_edl([], ins))
    assert [b["id"] for b in blocks] == ["c1", "c2"]
    assert blocks[1]["out_end"] == 7.0


def test_speed_remap_shifts_windows():
    """A 2x span over the first segment halves its output length, so the
    insert at its boundary starts earlier in the OUTPUT."""
    ins = [{"id": "s", "asset_key": "k", "kind": "video",
            "at_output_s": 2.0, "duration_s": 1.0, "source_start_s": 0.0}]
    blocks = program_blocks(_edl([[0.0, 4.0]], ins,
                                 [{"start": 0.0, "end": 4.0, "factor": 2.0}]))
    assert blocks[0]["out_end"] == 2.0          # 4s of source at 2x
    assert blocks[1]["out_start"] == 2.0 and blocks[1]["out_end"] == 3.0


def test_describe_program_names_and_numbers():
    ins = [{"id": "ins1", "asset_key": "clips/300/b3729228033d.mov",
            "kind": "video", "at_output_s": 3.55, "duration_s": 4.47,
            "source_start_s": 12.41}]
    txt = describe_program(_edl([[113.7, 117.25]], ins),
                           name_of=lambda k: "IMG_9124 2.MOV")
    assert "scene 1" in txt and "scene 2" in txt
    assert "main footage 113.7-117.25s" in txt
    assert "IMG_9124 2.MOV" in txt and "[ins1]" in txt
    assert "from 12.41s" in txt
    assert "VIEWER" in txt


def test_describe_program_empty():
    assert describe_program(_edl([])) == ""


def test_output_time_resolution_matches_blocks():
    """The math _look_at_output leans on: out_to_src inside footage, block
    offset arithmetic inside an insert."""
    ins = [{"id": "b", "asset_key": "k", "kind": "video",
            "at_output_s": 5.0, "duration_s": 3.0, "source_start_s": 10.0}]
    edl = _edl([[0.0, 5.0], [20.0, 25.0]], ins)
    tl = Timeline(edl["keep"], ins)
    blocks = program_blocks(edl)
    # inside kept footage
    assert tl.out_to_src(1.0) == 1.0
    assert abs(tl.out_to_src(9.0) - 21.0) < 1e-6
    # inside the insert: out_to_src is None, the block gives the local time
    assert tl.out_to_src(6.0) is None
    b = next(x for x in blocks if x["kind"] == "insert")
    local = b["clip_start_s"] + (6.0 - b["out_start"])
    assert local == 11.0
