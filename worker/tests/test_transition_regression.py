"""Renderer regressions found by production frame screening."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from renderer import build_filtergraph  # noqa: E402
from schemas import validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


def test_zoom_punch_never_blends_across_a_concat_discontinuity():
    edl = validate_edl({
        "keep": [[0.0, 5.0], [10.0, 15.0]],
        "effects": {"transition": {
            "style": "zoom_punch", "duration_s": 0.3,
            "scope": "every_cut",
        }},
    }, 20.0).model_dump()
    index = {"video": {"duration": 20.0}, "words": [], "sentences": []}

    graph = build_filtergraph(
        edl, 20.0, True, Timeline(edl["keep"], []), None, [], index,
        preview=False, W=1280, H=720, fps=30.0, frame_mode=None)

    assert "zoompan=z='1+" in graph
    assert "tmix=frames=5" not in graph
    assert "vpunchb" not in graph
