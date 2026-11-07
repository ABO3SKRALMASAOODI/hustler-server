"""Ticket #77 (project 5533, 7 Nov 2026): "the whole background to look like
the dark backdrop curtain, except for the floor". The skill said a white wall
needs about ev -7 to reach a black curtain, but the subject schema capped
ev at -4, and the editor guessed -2.2, -3, -4: the wall stayed grey and the
request ended partial. The range now reaches a curtain's dark, and
``style.match`` measures the reference area on real frames and solves ev."""

import numpy as np
import pytest

from fx_schemas.subject import SubjectFx  # noqa: F401  (import check)
from tools_ext import subject_tools as st
from tests.test_w1b_tools import Ctx, _fx, _no_side_effects  # noqa: F401


def _room():
    """A 9:16 frame: white wall left, black curtain centre, floor below."""
    f = np.full((192, 108, 3), 225, np.uint8)       # pale wall
    f[:140, 35:75] = 22                              # dark curtain
    f[140:] = (150, 110, 70)                         # wooden floor
    return f


def test_a_curtain_dark_exposure_is_a_valid_edit():
    import schemas
    ctx = Ctx()
    out = st.add_subject_effect(
        ctx, "exposure", start=0, end=10, scope="background",
        region={"shape": "rect", "box": [0, 0, 1, 0.72]},
        style={"ev": -7, "saturation": 0})
    assert out.startswith("EDL"), out
    item = [f for f in _fx(ctx) if f.get("kind") == "exposure"][0]
    assert item["ev"] == -7
    schemas.validate_edl(ctx.edl, ctx.duration)


def test_match_solves_ev_from_the_measured_picture():
    frames = [_room()] * 3
    ev, area, ref = st.solve_match_ev(frames, [0, 0, 1, 0.72],
                                      [0.4, 0.1, 0.6, 0.6])
    assert area > 200 and ref < 30
    # the wall's bright part lands on the curtain's: 225 * 2**(ev/2.2) ~ 22
    assert 225 * 2 ** (ev / 2.2) == pytest.approx(22, abs=3)
    assert ev < -6.5


def test_the_verb_writes_the_measured_ev(monkeypatch):
    import tools_ext.finish_tools as ft
    monkeypatch.setattr(ft, "_frames", lambda ctx, edl, times:
                        [_room() for _ in times])
    ctx = Ctx()
    out = st.add_subject_effect(
        ctx, "exposure", start=0, end=10, scope="background",
        region={"shape": "rect", "box": [0, 0, 1, 0.72]},
        style={"match": [0.4, 0.1, 0.6, 0.6], "saturation": 0})
    assert out.startswith("EDL"), out
    assert "measured" in out and "ev = -" in out
    item = [f for f in _fx(ctx) if f.get("kind") == "exposure"][0]
    assert item["ev"] < -6.5
    assert "_match" not in item


def test_match_without_frames_is_refused_not_guessed(monkeypatch):
    import tools_ext.finish_tools as ft
    monkeypatch.setattr(ft, "_frames", lambda ctx, edl, times: [])
    ctx = Ctx()
    out = st.add_subject_effect(
        ctx, "exposure", start=0, end=10, scope="background",
        style={"match": [0.4, 0.1, 0.6, 0.6]})
    assert out.startswith("REJECTED") and "style.ev" in out
    assert ctx.writes == []


def test_match_belongs_to_exposure():
    ctx = Ctx()
    out = st.add_subject_effect(ctx, "negate", start=0, end=1,
                                scope="background",
                                style={"match": [0, 0, 1, 1]})
    assert out.startswith("REJECTED") and "match" in out
