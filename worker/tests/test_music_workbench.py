"""Round 79f — music may extend PAST the program's end (the workbench).

The timeline is where the user chooses WHICH part of a song plays; the
unused remainder is material, not an error. The schema admits it, the
renderer clamps the mix (and the fade-out) to the program, and an item
lying entirely beyond the video is skipped before it is even fetched.

Run:  python -m pytest tests/test_music_workbench.py -q     (from worker/)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import EDLValidationError, default_edl, validate_edl  # noqa: E402

SRC = 354.6


def _edl(music):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 28.0]]
    e["music"] = music
    return e


def test_music_may_outlive_the_program():
    e = validate_edl(_edl([{"id": "mus1", "storage_key": "music/1/s.m4a",
                            "start": 0.13, "end": 65.72,
                            "gain_db": -4.0}]), SRC).model_dump()
    assert e["music"][0]["end"] == 65.72


def test_music_fully_beyond_is_legal_workbench_material():
    e = validate_edl(_edl([{"id": "mus1", "storage_key": "music/1/s.m4a",
                            "start": 40.0, "end": 60.0,
                            "gain_db": -4.0}]), SRC).model_dump()
    assert e["music"][0]["start"] == 40.0


def test_music_an_hour_past_the_program_is_a_stray_value():
    with pytest.raises(EDLValidationError):
        validate_edl(_edl([{"id": "mus1", "storage_key": "music/1/s.m4a",
                            "start": 0.0, "end": 28.0 + 3700.0,
                            "gain_db": -4.0}]), SRC)


def test_music_invalid_ranges_still_reject():
    for span in ([5.0, 5.0], [5.0, 4.0], [-1.0, 4.0]):
        with pytest.raises(EDLValidationError):
            validate_edl(_edl([{"id": "mus1",
                                "storage_key": "music/1/s.m4a",
                                "start": span[0], "end": span[1],
                                "gain_db": -4.0}]), SRC)


# ── round 79j: the sequence is as long as its content ──────────────────────

from renderer import build_filtergraph, music_tail_ext, music_tail_current  # noqa: E402
from timeline import Timeline                                # noqa: E402

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}


def _graph(edl, music_inputs):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, SRC, True, tl, None, music_inputs, INDEX,
                             False, W=1280, H=720, fps=30.0, frame_mode=None,
                             src_w=1280, src_h=720)


def test_music_tail_extends_the_render_over_black():
    m = {"id": "mus1", "storage_key": "music/1/s.m4a", "start": 0.0,
         "end": 57.3, "gain_db": -4.0, "offset_s": 4.71, "duck": False}
    e = validate_edl(_edl([m]), SRC).model_dump()
    g = _graph(e, [(1, e["music"][0], 66.0)])
    assert "tpad=stop_mode=add:stop_duration=29.300:color=black" in g
    assert "apad=pad_dur=29.300" in g
    # the music window is no longer clamped at the program's end
    assert "atrim=start=4.710:end=62.010" in g       # 4.71 + 57.3
    assert abs(music_tail_ext(e, 28.0) - 29.3) < 1e-6


def test_music_inside_the_video_renders_byte_identically():
    m = {"id": "mus1", "storage_key": "music/1/s.m4a", "start": 0.0,
         "end": 20.0, "gain_db": -4.0, "duck": False}
    e = validate_edl(_edl([m]), SRC).model_dump()
    g = _graph(e, [(1, e["music"][0], 66.0)])
    assert "tpad" not in g and "apad" not in g
    assert music_tail_ext(e, 28.0) == 0.0


def test_muted_tail_does_not_extend():
    m = {"id": "mus1", "storage_key": "music/1/s.m4a", "start": 0.0,
         "end": 57.3, "gain_db": -4.0, "mute": True}
    e = validate_edl(_edl([m]), SRC).model_dump()
    assert music_tail_ext(e, 28.0) == 0.0


def test_tail_gate_busts_only_outliving_music():
    long_e = validate_edl(_edl([{"id": "mus1",
                                 "storage_key": "music/1/s.m4a",
                                 "start": 0.0, "end": 57.3,
                                 "gain_db": -4.0}]), SRC).model_dump()
    short_e = validate_edl(_edl([{"id": "mus1",
                                  "storage_key": "music/1/s.m4a",
                                  "start": 0.0, "end": 20.0,
                                  "gain_db": -4.0}]), SRC).model_dump()
    assert music_tail_current({}, short_e, 28.0)           # grandfathered
    assert not music_tail_current({}, long_e, 28.0)        # must re-render
    assert music_tail_current({"tail_v": 1}, long_e, 28.0)
