"""High-end finishing effects compile instead of silently disappearing."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import renderer                                                # noqa: E402
from schemas import EDLValidationError, STYLIZE_KINDS, validate_edl  # noqa: E402
from timeline import Timeline                                  # noqa: E402


def _graph(stylize):
    edl = validate_edl({
        "keep": [[0, 4]],
        "effects": {"stylize": stylize},
    }, 4).model_dump()
    return renderer.build_filtergraph(
        edl, 4.0, True, Timeline(edl["keep"]), None, [],
        {"words": [], "silences": []}, True,
        W=320, H=180, fps=30.0)


def test_stabilize_is_a_real_renderer_pass():
    assert "stabilize" in STYLIZE_KINDS
    graph = _graph([{"id": "st1", "kind": "stabilize",
                     "intensity": 0.5}])
    assert "deshake=rx=28:ry=28:edge=mirror:blocksize=8:search=less" in graph


def test_stabilize_rejects_a_fake_window_instead_of_ignoring_it():
    with pytest.raises(EDLValidationError, match="whole video"):
        validate_edl({
            "keep": [[0, 4]],
            "effects": {"stylize": [{"id": "st1", "kind": "stabilize",
                                        "start": 1, "end": 3}]},
        }, 4)


def test_halation_is_highlight_only_warm_bloom_and_can_be_windowed():
    assert "halation" in STYLIZE_KINDS
    graph = _graph([{"id": "st1", "kind": "halation",
                     "start": 0.5, "end": 2.5, "intensity": 0.75}])
    assert "lutyuv=y='if(gte(val," in graph
    assert "colorbalance=rs=" in graph and ":bs=-" in graph
    assert "blend=all_mode=screen" in graph
    assert "enable='between(t,0.500,2.500)'" in graph
