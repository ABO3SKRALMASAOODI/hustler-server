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
