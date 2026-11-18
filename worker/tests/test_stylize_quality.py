"""High-end finishing effects compile instead of silently disappearing."""

import os
import shutil
import subprocess
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
    assert "deshake=rx=32:ry=32:edge=mirror:blocksize=8:search=less" in graph


@pytest.mark.parametrize("intensity", [0, .5, 1])
def test_stabilization_filter_accepts_low_middle_and_high_strength(intensity):
    ffmpeg = shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg"))
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    graph = _graph([{"id": "st1", "kind": "stabilize", "intensity": intensity}])
    effect = next(part.split("]", 1)[1].split("[", 1)[0]
                  for part in graph.split(";") if "]deshake=" in part)
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x180:rate=30:duration=0.1", "-filter_threads", "1",
         "-vf", effect, "-frames:v", "3", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(result.stdout) == 3 * 320 * 180 * 3


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


@pytest.mark.parametrize("kind", ["halation", "glow"])
def test_a_bloom_never_casts_the_picture_magenta(kind):
    """EDIT-CORE gate, 17 Nov 2026 (p5873): halation's screen blend ran on YUV
    planes, where screening lifts both chroma planes above neutral. A neutral
    grey came out magenta-blue over the whole frame (whites a* -0.9 -> +17 at
    intensity 0.08), and "warm, creamy neutrals" shipped pink-mauve. A bloom
    over a picture with no highlights must leave its colour where it was."""
    import numpy as np
    ffmpeg = shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg"))
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    graph = _graph([{"id": "st1", "kind": kind, "intensity": 0.3}])
    parts = graph.split(";")
    start = next(i for i, p in enumerate(parts) if p.startswith("[vc]"))
    end = next(i for i, p in enumerate(parts) if p.endswith("[vsty0]"))
    chain = ";".join(["[0:v]format=yuv420p[vc]"] + parts[start:end + 1])
    out = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         "color=c=0x808080:size=320x180:rate=30:duration=0.1",
         "-filter_complex", chain, "-map", "[vsty0]", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    rgb = np.frombuffer(out.stdout, np.uint8).reshape(-1, 3).astype(float).mean(0)
    # neutral grey stays grey: no channel pulled away from the others
    assert rgb.max() - rgb.min() < 6, rgb
