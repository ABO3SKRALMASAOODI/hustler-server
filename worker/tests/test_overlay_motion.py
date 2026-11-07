"""General keyframed motion for visual overlays, through real ffmpeg pixels."""

import os
import subprocess
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import renderer
from schemas import default_edl, validate_edl
from timeline import Timeline, remap_program_items


def _motion_edl(duration=2.0):
    edl = default_edl(duration)
    edl["overlays"] = [{
        "id": "ov1", "asset_key": "images/logo.png", "kind": "image",
        "start": 0.0, "duration_s": duration,
        "x": [{"t": 0.0, "v": -0.25},
              {"t": 0.5, "v": 0.5, "ease": "out"}],
        "y": [{"t": 0.0, "v": 0.7}, {"t": 0.5, "v": 0.5}],
        "scale": [{"t": 0.0, "v": 0.15},
                  {"t": 0.45, "v": 0.5, "ease": "out"}],
        "rotation": [{"t": 0.0, "v": -12.0},
                     {"t": 0.5, "v": 4.0, "ease": "out"},
                     {"t": 0.8, "v": 0.0, "ease": "in_out"}],
        "opacity": [{"t": 0.0, "v": 0.0},
                    {"t": 0.25, "v": 1.0, "ease": "out"}],
    }]
    return validate_edl(edl, duration).model_dump()


def test_overlay_motion_schema_keeps_local_curves_and_direction():
    ov = _motion_edl()["overlays"][0]
    assert ov["scale"][1]["ease"] == "out"
    assert ov["opacity"][0]["v"] == 0.0
    assert ov["rotation"][0]["v"] == -12.0
    assert ov["rotation"][1]["v"] == 4.0

    bad = default_edl(2.0)
    bad["overlays"] = [{
        "id": "ov1", "asset_key": "x", "kind": "image", "start": 0,
        "duration_s": 1.0,
        "scale": [{"t": 0.0, "v": 0.2}, {"t": 1.5, "v": 0.5}],
    }]
    try:
        validate_edl(bad, 2.0)
        raise AssertionError("overlay keyframes past the local window must fail")
    except ValueError as exc:
        assert "element's own length" in str(exc)


def test_overlay_motion_compiles_and_changes_real_pixels(tmp_path):
    edl = _motion_edl()
    tl = Timeline(edl["keep"])
    graph = renderer.build_filtergraph(
        edl, 2.0, False, tl, None, [],
        {"words": [], "sentences": [], "silences": [], "shots": []},
        False, W=320, H=240, fps=30.0, frame_mode=None,
        src_w=320, src_h=240, silence_idx=1,
        overlay_inputs=[(2, edl["overlays"][0])])
    assert "eval=frame" in graph
    assert "geq=r='r(X,Y)'" in graph
    assert "rotate=angle='" in graph
    assert "pad=w='ceil(hypot(" in graph

    out = tmp_path / "motion.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=30",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2",
        "-f", "lavfi", "-i", "color=c=red:s=100x100:d=2:r=30",
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(out),
    ]
    subprocess.run(cmd, check=True, timeout=30)

    frames = []
    for i, at in enumerate((0.03, 1.0)):
        path = tmp_path / f"frame{i}.png"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(at), "-i", str(out), "-frames:v", "1", str(path),
        ], check=True, timeout=15)
        frames.append(Image.open(path).convert("RGB"))

    def red_pixels(image):
        return sum(1 for r, g, b in image.get_flattened_data()
                   if r > g + 70 and r > b + 70)

    # The overlay begins off-frame and transparent, then lands visibly at a
    # larger size. This checks the graph as pixels, not just as a string.
    assert red_pixels(frames[1]) > red_pixels(frames[0]) + 5000


class _Ctx:
    def __init__(self, edl):
        self.duration = 5.0
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def test_set_overlay_motion_is_a_general_atomic_write_and_clips_on_trim():
    edl = default_edl(5.0)
    edl["overlays"] = [{
        "id": "ov1", "asset_key": "image.png", "kind": "image",
        "start": 0.0, "duration_s": 4.0,
    }]
    ctx = _Ctx(edl)
    result = agent_tools.set_overlay_motion(ctx, "ov1", {
        "x": [{"t": 0.0, "v": -0.2}, {"t": 0.4, "v": 0.5}],
        "scale": [{"t": 0.0, "v": 0.2}, {"t": 0.4, "v": 0.55}],
        "rotation": [{"t": 0.0, "v": -8}, {"t": 0.5, "v": 0}],
        "opacity": [{"t": 0.0, "v": 0}, {"t": 0.2, "v": 1}],
    })
    assert result.startswith("EDL v"), result
    ov = ctx.latest_edl()["json"]["overlays"][0]
    assert isinstance(ov["scale"], list) and isinstance(ov["opacity"], list)

    # A structural trim shortens the overlay and clips every curve, not only
    # its x/y path, so a harmless cut can never invalidate the whole EDL.
    remapped = ctx.latest_edl()["json"]
    old = Timeline([[0.0, 5.0]])
    new = Timeline([[0.0, 1.0]])
    remapped["keep"] = [[0.0, 1.0]]
    remap_program_items(remapped, old, new)
    normalized = validate_edl(remapped, 5.0).model_dump()
    ov = normalized["overlays"][0]
    for prop in ("x", "scale", "rotation", "opacity"):
        if isinstance(ov.get(prop), list):
            assert max(k["t"] for k in ov[prop]) <= ov["duration_s"]


def test_moving_overlay_near_program_end_clips_every_motion_curve():
    edl = default_edl(5.0)
    edl["overlays"] = [{
        "id": "ov1", "asset_key": "image.png", "kind": "image",
        "start": 0.0, "duration_s": 4.0,
        "x": [{"t": 0.0, "v": 0.2}, {"t": 3.5, "v": 0.7}],
        "y": [{"t": 0.0, "v": 0.5}, {"t": 3.5, "v": 0.4}],
        "scale": [{"t": 0.0, "v": 0.2}, {"t": 3.5, "v": 0.5}],
        "rotation": [{"t": 0.0, "v": -8}, {"t": 3.5, "v": 0}],
        "opacity": [{"t": 0.0, "v": 0}, {"t": 3.5, "v": 1}],
    }]
    ctx = _Ctx(edl)
    result = agent_tools.move_overlay(ctx, "ov1", start=4.7)
    assert result.startswith("EDL v"), result
    ov = ctx.latest_edl()["json"]["overlays"][0]
    assert ov["duration_s"] == 0.3
    for prop in ("x", "y", "scale", "rotation", "opacity"):
        assert max(k["t"] for k in ov[prop]) <= 0.3


def test_cover_and_screen_takeover_reject_ignored_or_conflicting_curves():
    cover = default_edl(5.0)
    cover["overlays"] = [{
        "id": "ov1", "asset_key": "image.png", "kind": "image",
        "start": 0.0, "duration_s": 2.0, "fit": "cover",
    }]
    ctx = _Ctx(cover)
    assert "no longer fills the whole frame" in agent_tools.set_overlay_motion(
        ctx, "ov1", {"x": [{"t": 0, "v": 0}, {"t": 1, "v": 1}]})
    assert ctx.latest_edl()["json"]["overlays"][0]["fit"] is None
    ctx.latest_edl()["json"]["overlays"][0]["fit"] = "cover"
    assert agent_tools.set_overlay_motion(
        ctx, "ov1", {"opacity": [{"t": 0, "v": 0},
                                   {"t": 0.2, "v": 1}]}).startswith("EDL v")


def test_a_tilted_card_that_settles_stays_one_card(tmp_path):
    """p5374 (Director slice, today's renderer): a card with a fixed tilt and a settling scale showed a
    wrapped sliver of its picture at one side and a coloured line under it. rotate sized its canvas
    from the first, largest frame and then read past every smaller one."""
    edl = default_edl(2.0)
    edl["overlays"] = [{
        "id": "card", "asset_key": "images/card.png", "kind": "image", "start": 0.0, "duration_s": 2.0,
        "x": 0.5, "y": 0.5, "rotation": -3.0,
        "scale": [{"t": 0.0, "v": 0.6}, {"t": 0.4, "v": 0.32}, {"t": 2.0, "v": 0.3}]}]
    edl = validate_edl(edl, 2.0).model_dump()
    graph = renderer.build_filtergraph(
        edl, 2.0, False, Timeline(edl["keep"]), None, [],
        {"words": [], "sentences": [], "silences": [], "shots": []},
        False, W=320, H=320, fps=30.0, frame_mode=None,
        src_w=320, src_h=320, silence_idx=1, overlay_inputs=[(2, edl["overlays"][0])])
    assert "pad=w='ceil(hypot(" in graph and "c=black@0.0:ow=iw:oh=ih" in graph
    out = tmp_path / "card.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x320:d=2:r=30",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2",
        "-f", "lavfi", "-i", "color=c=red:s=200x200:d=2:r=30",
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True, timeout=30)
    path = tmp_path / "settled.png"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1.5", "-i", str(out),
                    "-frames:v", "1", str(path)], check=True, timeout=15)
    im = Image.open(path).convert("RGB")
    red = [(x, y) for y in range(im.height) for x in range(im.width)
           if (lambda p: p[0] > p[1] + 70 and p[0] > p[2] + 70)(im.getpixel((x, y)))]
    assert len(red) > 6000                                  # the card is there, about 96 px square
    half = 0.3 * 320 / 2 * (0.9986 + 0.0523) + 4            # its tilted half-extent, and the encoder's edge
    assert all(abs(x - 160) <= half and abs(y - 160) <= half for x, y in red)
