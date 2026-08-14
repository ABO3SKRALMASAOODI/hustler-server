"""Vector EDL primitives: schema, tools, timeline and ASS compilation."""

import os
import glob
import re
import shutil
import subprocess
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import edl_diff  # noqa: E402
import graphics  # noqa: E402
import screening  # noqa: E402
from schemas import (VECTOR_KINDS, default_edl, describe_edl,  # noqa: E402
                     edl_signature, validate_edl)
from timeline import Timeline, remap_program_items  # noqa: E402


def _libass_ffmpeg():
    candidates = [shutil.which("ffmpeg")]
    candidates.extend(sorted(glob.glob(
        "/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg"), reverse=True))
    for binary in filter(None, candidates):
        probe = subprocess.run([binary, "-hide_banner", "-filters"],
                               capture_output=True, text=True)
        if re.search(r"\bsubtitles\b", probe.stdout + probe.stderr):
            return binary
    return None


LIBASS_FFMPEG = _libass_ffmpeg()


def _vector(kind, ident="vec1", **overrides):
    item = {"id": ident, "kind": kind, "start": 0.5, "end": 3.5,
            "x": 0.5, "y": 0.5, "width": 0.3, "height": 0.12,
            "color": "#ff3366"}
    item.update(overrides)
    return item


def test_schema_supports_every_kind_and_preserves_old_signatures():
    edl = default_edl(8.0)
    edl["vectors"] = [
        _vector(kind, f"vec{i}", start=0.5+i, end=1.0+i,
                value=0.64 if kind == "progress" else None)
        for i, kind in enumerate(VECTOR_KINDS)
    ]
    normalized = validate_edl(edl, 8.0).model_dump()
    assert [v["kind"] for v in normalized["vectors"]] == list(VECTOR_KINDS)
    assert all(v["color"] == "#FF3366" for v in normalized["vectors"])
    assert "vectors x6" in describe_edl(normalized, 8.0)
    assert edl_signature(default_edl(8.0)) == edl_signature(
        dict(default_edl(8.0), vectors=[]))


def test_every_primitive_compiles_to_deterministic_ass_paths(tmp_path):
    vectors = [
        _vector("rectangle", "rect", rounding=0.3,
                stroke_color="#ffffff", stroke_width=0.003),
        _vector("ellipse", "ellipse", start=1, end=4),
        _vector("line", "line", start=2, end=5, height=0.01),
        _vector("arrow", "arrow", start=3, end=6),
        _vector("ring", "ring", start=4, end=7, stroke_width=0.01),
        _vector("progress", "progress", start=5, end=8, value=0.64,
                background_color="#20242a", rounding=0.5),
    ]
    edl = validate_edl(dict(default_edl(9.0), vectors=vectors), 9.0).model_dump()
    a, b = tmp_path / "a.ass", tmp_path / "b.ass"
    assert graphics.build_gfx_ass(edl, 9.0, str(a), (720, 1280))
    assert graphics.build_gfx_ass(edl, 9.0, str(b), (720, 1280))
    assert a.read_bytes() == b.read_bytes()
    content = a.read_text()
    # progress is two overlapping path events (track + fill); every other
    # primitive is one.
    assert content.count("Dialogue:") == len(vectors) + 1
    assert content.count(r"\p1") >= len(vectors)
    assert r"\1c&H6633FF&" in content       # #FF3366 -> ASS BGR
    assert r"\1c&H2A2420&" in content       # progress track
    # A ring is a compound pair of opposite-direction cubic ellipses, not a
    # fake centre painted the assumed background colour.
    ring_line = next(line for line in content.splitlines()
                     if line.startswith("Dialogue:") and line.count(" b ") >= 8)
    assert ring_line.count(" b ") >= 8


def test_vector_motion_compiles_position_scale_rotation_and_opacity(tmp_path):
    motion = {"x": [{"t": 0, "v": -0.1},
                    {"t": 2, "v": 0.8, "ease": "in_out"}],
              "scale": [{"t": 0, "v": 0.3},
                        {"t": 0.4, "v": 1.1, "ease": "out"}],
              "rotation": [{"t": 0, "v": -12}, {"t": 2, "v": 8}],
              "opacity": [{"t": 0, "v": 0}, {"t": 0.2, "v": 1}]}
    edl = validate_edl(dict(default_edl(4.0), vectors=[
        _vector("arrow", motion=motion, start=1, end=3)]), 4.0).model_dump()
    path = tmp_path / "motion.ass"
    graphics.build_gfx_ass(edl, 4.0, str(path), (640, 360))
    content = path.read_text()
    assert content.count("Dialogue:") > 4
    assert r"\move(" in content and r"\fscx" in content
    assert r"\frz" in content and r"\alpha&H" in content
    assert r"\t(0," in content


@pytest.mark.skipif(not LIBASS_FFMPEG,
                    reason="needs ffmpeg with the libass subtitles filter")
def test_vector_paths_burn_as_pixels_and_ring_centre_is_transparent(tmp_path):
    """Production-stack pixel proof, including the compound ring winding."""
    vectors = [
        _vector("ring", "ring", start=0, end=1, x=0.5, y=0.4,
                width=0.5, height=0.3, color="#FFE14D",
                stroke_width=0.018),
        _vector("progress", "progress", start=0, end=1, x=0.5, y=0.8,
                width=0.7, height=0.06, color="#55E27A",
                background_color="#25272E", value=0.65, rounding=0.5),
    ]
    edl = validate_edl(dict(default_edl(1.0), vectors=vectors), 1.0).model_dump()
    ass, png = tmp_path / "pixel.ass", tmp_path / "pixel.png"
    graphics.build_gfx_ass(edl, 1.0, str(ass), (360, 640))
    filt = f"subtitles=filename='{ass}':fontsdir='{graphics.FONTS_DIR}'"
    subprocess.run([
        LIBASS_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=0x17365D:s=360x640:r=30:d=1", "-vf", filt,
        "-frames:v", "1", str(png),
    ], check=True)
    image = Image.open(png).convert("RGB")
    centre = image.getpixel((180, 256))
    ring_top = image.getpixel((180, 160))
    progress_fill = image.getpixel((100, 512))
    # Ring centre retains the source blue; its top stroke is bright yellow.
    assert centre[2] > centre[0] * 2 and centre[2] > centre[1]
    assert ring_top[0] > 160 and ring_top[1] > 130
    assert progress_fill[1] > progress_fill[0] * 1.4


@pytest.mark.skipif(not LIBASS_FFMPEG,
                    reason="needs ffmpeg with the libass subtitles filter")
def test_vector_motion_changes_real_rendered_position_and_scale(tmp_path):
    motion = {
        "x": [{"t": 0, "v": 0.18}, {"t": 2, "v": 0.82,
                                           "ease": "in_out"}],
        "scale": [{"t": 0, "v": 0.45}, {"t": 2, "v": 1.25,
                                              "ease": "out"}],
        "rotation": [{"t": 0, "v": -10}, {"t": 2, "v": 12}],
        "opacity": [{"t": 0, "v": 0}, {"t": 0.18, "v": 1,
                                             "ease": "out"}],
    }
    vector = _vector("arrow", start=0, end=2, y=0.5, width=0.3,
                     height=0.12, color="#22CCFF", motion=motion)
    edl = validate_edl(dict(default_edl(2.0), vectors=[vector]),
                       2.0).model_dump()
    ass, video = tmp_path / "move.ass", tmp_path / "move.mp4"
    graphics.build_gfx_ass(edl, 2.0, str(ass), (640, 360))
    filt = f"subtitles=filename='{ass}':fontsdir='{graphics.FONTS_DIR}'"
    subprocess.run([
        LIBASS_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=black:s=640x360:r=30:d=2", "-vf", filt,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True)

    def colored_box(at, name):
        png = tmp_path / name
        subprocess.run([LIBASS_FFMPEG, "-y", "-v", "error", "-ss", str(at),
                        "-i", str(video), "-frames:v", "1", str(png)],
                       check=True)
        image = Image.open(png).convert("RGB")
        pts = [(x, y) for y in range(image.height) for x in range(image.width)
               if (lambda p: p[1] > 100 and p[2] > 130)(image.getpixel((x, y)))]
        assert pts
        return (min(x for x, _ in pts), min(y for _, y in pts),
                max(x for x, _ in pts), max(y for _, y in pts), len(pts))

    early = colored_box(0.25, "early.png")
    late = colored_box(1.70, "late.png")
    assert (late[0] + late[2]) / 2 - (early[0] + early[2]) / 2 > 220
    assert late[4] > early[4] * 1.8


def test_text_only_output_is_unchanged_by_empty_vector_collection(tmp_path):
    text = {"id": "tx1", "text": "UNCHANGED", "start": 0, "end": 2,
            "template": "title", "entrance": "none", "exit": "none"}
    a, b = tmp_path / "old.ass", tmp_path / "new.ass"
    graphics.build_gfx_ass({"texts": [text]}, 3.0, str(a))
    graphics.build_gfx_ass({"texts": [text], "vectors": []}, 3.0, str(b))
    assert a.read_bytes() == b.read_bytes()


class _Ctx:
    def __init__(self):
        self.duration = 5.0
        self._edl = validate_edl(default_edl(5.0), 5.0).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes)+1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes)+1}: {description}"


def test_agent_can_add_update_clear_motion_and_remove_vector():
    ctx = _Ctx()
    result = agent_tools.add_vector_graphic(
        ctx, "ring", 1, 4, x=0.25, y=0.4, color="#22ccff",
        motion={"scale": [{"t": 0, "v": 0.2}, {"t": 0.4, "v": 1}]})
    assert result.startswith("EDL v"), result
    item = ctx.latest_edl()["json"]["vectors"][0]
    assert item["id"] == "vec1" and item["color"] == "#22CCFF"
    assert isinstance(item["motion"]["scale"], list)
    assert agent_tools.set_vector_graphic(
        ctx, "vec1", x=0.7, motion={}).startswith("EDL v")
    item = ctx.latest_edl()["json"]["vectors"][0]
    assert item["x"] == 0.7 and item["motion"] is None
    assert agent_tools.remove_vector_graphic(ctx, "vec1").startswith("EDL v")
    assert ctx.latest_edl()["json"]["vectors"] == []


def test_shortening_program_clips_vector_window_and_local_keyframes():
    edl = validate_edl(dict(default_edl(5.0), vectors=[
        _vector("arrow", start=0.2, end=4.5,
                motion={"x": [{"t": 0, "v": 0.2},
                              {"t": 4.0, "v": 0.8}]})]), 5.0).model_dump()
    edl["keep"] = [[0.0, 1.5]]
    remap_program_items(edl, Timeline([[0.0, 5.0]]), Timeline([[0.0, 1.5]]))
    normalized = validate_edl(edl, 5.0).model_dump()
    item = normalized["vectors"][0]
    assert item["end"] == 1.5
    assert max(k["t"] for k in item["motion"]["x"]) <= 1.3


def test_vector_changes_are_highlighted_and_motion_is_screened():
    prev = default_edl(5.0)
    vector = _vector("arrow", start=1.0, end=3.0,
                     motion={"rotation": [{"t": 0, "v": -8},
                                          {"t": 2, "v": 0}]})
    new = dict(default_edl(5.0), vectors=[vector])
    change = edl_diff.change_ranges(prev, new)
    assert [1.0, 3.0] in change["out_ranges"]
    claims = edl_diff.verify_plan(prev, new)
    assert any("arrow graphic" in claim for _t, claim in claims)
    frames = screening.plan(new, 5.0, max_frames=24, base_frames=6)
    reasons = " ".join(row["reason"] for row in frames)
    assert "arrow motion" in reasons
