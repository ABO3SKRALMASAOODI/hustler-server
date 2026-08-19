"""Cost controls must preserve the edit while bounding repeated compute."""

from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parent
sys.path.insert(0, str(WORKER))

import agent_tools  # noqa: E402
import agent_loop  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import http_server  # noqa: E402
import main  # noqa: E402
import media  # noqa: E402
import remote  # noqa: E402
import renderer  # noqa: E402
import schemas  # noqa: E402
import stitch  # noqa: E402
from timeline import Timeline  # noqa: E402


def _edl(**updates):
    value = {"keep": [[0.0, 20.0]], "texts": [], "inserts": [],
             "speed": [], "effects": {}}
    value.update(updates)
    return value


class _BaselineDb:
    def __init__(self, previous):
        self.previous = previous

    def run(self, fn, *_args):
        if fn is dbx.get_edl_version:
            return {"version": 1, "json": self.previous}
        if fn is dbx.previous_edl_version:
            return {"version": 1, "json": self.previous}
        if fn is dbx.latest_render_version:
            return None
        raise AssertionError(fn)


class _Ctx:
    project_id = 9

    def __init__(self, previous):
        self.last_preview = {"edl_version": 1}
        self.db = _BaselineDb(previous)


def test_changed_section_ranges_cover_local_edit_without_full_program():
    previous = _edl()
    current = _edl(texts=[{"id": "tx1", "text": "hello",
                           "start": 5.0, "end": 7.0}])
    ranges, baseline = agent_tools._change_check_ranges(
        _Ctx(previous), {"version": 2, "json": current},
        [(6.0, "title is visible")])
    assert baseline["version"] == 1
    assert ranges
    assert ranges[0][0] <= 5.0 and ranges[0][1] >= 7.0
    assert sum(b - a for a, b in ranges) < 20.0


def test_global_changed_section_proof_is_sampled_and_bounded():
    previous = _edl()
    current = _edl(effects={"grade": "warm"})
    ranges, _baseline = agent_tools._change_check_ranges(
        _Ctx(previous), {"version": 2, "json": current}, [])
    assert 1 <= len(ranges) <= 6
    assert sum(b - a for a, b in ranges) <= 24.0


def test_first_changed_section_proof_uses_previous_edl_without_full_preview():
    previous = _edl()
    current = _edl(texts=[{"id": "tx1", "text": "first",
                           "start": 1.0, "end": 2.0}])
    ctx = _Ctx(previous)
    ctx.last_preview = None
    ranges, baseline = agent_tools._change_check_ranges(
        ctx, {"version": 2, "json": current}, [])
    assert baseline["version"] == 1
    assert ranges and ranges[0][0] <= 1.0 <= ranges[0][1]


def test_render_preview_schema_reserves_complete_for_readiness():
    _fn, description, properties = agent_tools.TOOLS["render_preview"]
    assert properties["complete"]["type"] == "boolean"
    assert "complete=true" in description
    schema = next(t for t in agent_tools.openai_tools()
                  if t["function"]["name"] == "render_preview")
    assert "complete" not in schema["function"]["parameters"]["required"]


def test_agent_has_no_fixed_edl_revision_ceiling():
    """Correct repairs are governed by semantic/cost evidence, not count."""
    source = (WORKER / "agent_tools.py").read_text()
    assert not hasattr(config, "AGENT_MAX_EDL_WRITES")
    assert "revision_budget_stops" not in source
    assert "revision ceiling" not in source


def test_agent_technical_backstop_leaves_room_for_complex_edits():
    assert 600 < config.AGENT_TURN_TOTAL_TIMEOUT_S <= 3000


def test_preview_check_routes_to_right_sized_service(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "https://heavy")
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_PREVIEW_URL",
                        "https://preview")
    assert remote._executor_url("preview_check") == "https://preview"
    assert http_server.COMPUTE_RUNNERS["preview_check"] is \
        renderer.run_render_job
    assert "preview_check" in main.MEDIA_TYPES


def test_generic_health_is_cached_on_preview_not_heavy(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "https://heavy")
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_PREVIEW_URL",
                        "https://preview")
    remote._health_cache.clear()
    seen = []

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"status": "ok", "features": ["preview"]}

    def fake_get(url, timeout):
        seen.append((url, timeout))
        return _Response()

    monkeypatch.setattr(remote.requests, "get", fake_get)
    assert remote.executor_health()["status"] == "ok"
    assert remote.executor_health()["status"] == "ok"
    assert seen == [("https://preview/health", 20)]


def test_proof_range_guard_clamps_full_length_abuse():
    ranges = renderer._validated_check_ranges([[0.0, 30.0]], 30.0)
    assert ranges == [[0.0, 25.0]]
    many = [[float(i), float(i) + 5.0] for i in range(0, 40, 5)]
    clamped = renderer._validated_check_ranges(many, 40.0)
    assert len(clamped) <= 6
    assert sum(b - a for a, b in clamped) <= 25.0 + 1e-6


def test_proof_piece_clips_overlay_at_its_budget_edge():
    edl = _edl(overlays=[{
        "id": "ov3", "asset_key": "clips/9/rocket.mp4", "kind": "video",
        "start": 8.61, "duration_s": 4.79, "fit": "cover",
        "source_start_s": 28.5,
    }])
    window = stitch.window_edl(edl, Timeline(edl["keep"]), 0.0, 11.21,
                               keep_audio=True)

    assert window["overlays"][0]["start"] == 8.61
    assert window["overlays"][0]["duration_s"] == 2.6
    # Regression: preview_check job 10981 failed here before rendering.
    schemas.validate_edl(window, duration=20.0)


def test_multi_window_budget_clips_text_ending_past_its_1272s_proof():
    """The 25s proof cap can cut the last window after containment.

    Production left a text ending at local 13.32s inside a 12.72s standalone
    piece.  Validation rejected the proof before ffmpeg could render it.
    """
    edl = _edl(
        keep=[[0.0, 40.0]],
        texts=[{
            "id": "tx-proof", "text": "THE RESULT", "start": 30.0,
            "end": 33.32, "template": "title", "entrance": "pop",
            "exit": "fade",
        }],
    )
    tl = Timeline(edl["keep"])
    ranges = renderer._validated_check_ranges(
        [[0.0, 12.28], [20.0, 32.0]], 40.0)
    ranges = renderer._contain_check_items(edl, tl, ranges, 40.0, {})
    ranges = renderer._validated_check_ranges(ranges, 40.0)

    assert ranges == [[0.0, 12.28], [20.0, 32.72]]
    window = stitch.window_edl(
        edl, tl, ranges[1][0], ranges[1][1], keep_audio=True)
    text = window["texts"][0]
    assert text["start"] == 10.0
    assert text["end"] == 12.72       # was 13.32 in the 12.72s piece
    assert text["entrance"] == "pop"
    assert text["exit"] == "none"
    schemas.validate_edl(window, duration=40.0)


def test_proof_budget_clips_long_zoom_at_the_piece_edge():
    """A contained zoom can be cut back by the final 25-second proof cap.

    Production preview-check jobs 12935/12952/12968 all reached this shape:
    the saved full EDL was valid, but the standalone proof retained the full
    zoom end and failed validation before rendering.
    """
    edl = _edl(
        keep=[[0.0, 40.0]],
        effects={"zooms": [{
            "id": "zm-proof", "start": 9.71, "end": 37.05,
            "strength": 0.06,
        }]},
    )
    window = stitch.window_edl(
        edl, Timeline(edl["keep"]), 0.0, 25.0, keep_audio=True)
    zoom = window["effects"]["zooms"][0]
    assert zoom["start"] == 9.71
    assert zoom["end"] == 25.0       # was 37.05 in a 25-second EDL
    schemas.validate_edl(window, duration=40.0)


def test_clipped_ease_zoom_preserves_its_boundary_strength():
    """Clipping must not invent a new ease-out at the proof boundary."""
    edl = _edl(
        keep=[[0.0, 30.0]],
        effects={"zooms": [{
            "id": "zm-ease", "start": 2.0, "end": 20.0,
            "strength": 0.4, "mode": "ease", "cx": 0.25, "cy": 0.7,
        }]},
    )
    window = stitch.window_edl(
        edl, Timeline(edl["keep"]), 5.0, 12.0, keep_audio=True)
    zoom = window["effects"]["zooms"][0]
    assert (zoom["start"], zoom["end"]) == (0.0, 7.0)
    assert zoom["mode"] == "path"
    assert zoom["path"][0]["s"] == pytest.approx(0.4)
    assert zoom["path"][-1]["s"] == pytest.approx(0.4)
    assert all(point["cx"] == pytest.approx(0.25)
               for point in zoom["path"])
    schemas.validate_edl(window, duration=30.0)


@pytest.mark.parametrize("zoom,w0,w1,samples,tolerance", [
    ({"id": "ease", "start": 2.0, "end": 4.0, "strength": 0.8,
      "mode": "ease", "cx": 0.25, "cy": 0.7},
     2.1, 3.0, [2.1, 2.25, 2.4, 2.7, 2.95], 0.002),
    ({"id": "push", "start": 2.0, "end": 10.0, "strength": 0.8,
      "mode": "push_in", "cx": 0.75, "cy": 0.3},
     4.0, 8.0, [4.0, 4.5, 6.0, 7.25, 8.0], 0.002),
    ({"id": "path", "start": 2.0, "end": 10.0, "strength": 0.25,
      "mode": "path", "ease": "cubic_in_out",
      "path": [
          {"f": 0.0, "cx": 0.2, "cy": 0.4, "s": 0.0},
          {"f": 0.5, "cx": 0.8, "cy": 0.7, "s": 0.8},
          {"f": 1.0, "cx": 0.3, "cy": 0.2, "s": 0.1},
      ]},
     3.3, 8.4, [3.3, 3.55, 4.2, 5.85, 6.0, 6.8, 7.45, 8.1, 8.39],
     0.006),
])
def test_clipped_zoom_matches_full_program_frames(
        zoom, w0, w1, samples, tolerance):
    """A proof window must show the same zoom state as the complete edit."""
    edl = _edl(keep=[[0.0, 30.0]], effects={"zooms": [zoom]})
    window = stitch.window_edl(
        edl, Timeline(edl["keep"]), w0, w1, keep_audio=True)
    clipped = window["effects"]["zooms"][0]
    assert len(clipped.get("path") or []) <= 24
    for absolute_t in samples:
        expected = renderer.zoom_state_at([zoom], absolute_t, 30.0)
        actual = renderer.zoom_state_at(
            [clipped], absolute_t - w0, w1 - w0)
        assert actual == pytest.approx(expected, abs=tolerance)
    schemas.validate_edl(window, duration=30.0)


def test_proof_window_rebases_carried_inserts_to_its_local_keep_boundary():
    """Insert coordinates are pre-insert time, proof windows are final time."""
    inserts = [
        {"id": "ins1", "asset_key": "clips/9/a.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 1.25},
        {"id": "ins2", "asset_key": "clips/9/b.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.72},
        {"id": "ins3", "asset_key": "clips/9/c.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.8},
        {"id": "ins4", "asset_key": "clips/9/d.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.8},
    ]
    edl = _edl(keep=[[0.0, 2.73]], inserts=inserts)
    window = stitch.window_edl(
        edl, Timeline(edl["keep"], inserts), 2.68, 6.3,
        keep_audio=True)
    assert window["keep"] == [[2.68, 2.73]]
    assert [item["at_output_s"] for item in window["inserts"]] \
        == [0.05, 0.05, 0.05, 0.05]
    schemas.validate_edl(window, duration=20.0)


def test_insert_proof_clears_baked_caption_mutes_and_keeps_030s_text():
    """The second production insert proof reached two later clock traps."""
    inserts = [
        {"id": "ins1", "asset_key": "clips/9/a.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 1.25},
        {"id": "ins2", "asset_key": "clips/9/b.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.72},
        {"id": "ins3", "asset_key": "clips/9/c.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.8},
        {"id": "ins4", "asset_key": "clips/9/d.mp4", "kind": "video",
         "at_output_s": 2.73, "duration_s": 0.8},
    ]
    edl = _edl(
        keep=[[0.0, 2.73]], inserts=inserts,
        texts=[{"id": "tx-water", "text": "WE NEED WATER.",
                "start": 6.0, "end": 6.3, "template": "title"}],
        caption_mutes=[[2.73, 6.3]],
    )
    window = stitch.window_edl(
        edl, Timeline(edl["keep"], inserts), 2.625, 6.3,
        keep_audio=True)
    assert window["caption_mutes"] == []
    assert window["texts"][0]["start"] == 3.37
    assert window["texts"][0]["end"] == 3.675
    schemas.validate_edl(window, duration=20.0)


@pytest.mark.parametrize("duration,zooms,raw_ranges,expected_ranges", [
    (457.56, [
        {"id": "zm1", "start": 43.44, "end": 54.44,
         "strength": 0.07, "mode": "ease"},
        {"id": "zm2", "start": 118.12, "end": 133.82,
         "strength": 0.06, "mode": "ease"},
        {"id": "zm3", "start": 218.9, "end": 228.4,
         "strength": 0.1, "mode": "push_in"},
        {"id": "zm4", "start": 272.6, "end": 281.0,
         "strength": 0.07, "mode": "ease"},
    ], [[42.69, 45.69], [47.44, 50.44], [52.19, 55.19],
        [117.37, 120.37], [124.47, 127.47], [131.57, 134.57]],
     [[42.69, 55.19], [117.37, 129.87]]),
    (91.02, [
        {"id": "zm1", "start": 0.0, "end": 9.71, "strength": 0.05},
        {"id": "zm2", "start": 9.71, "end": 67.05, "strength": 0.06},
        {"id": "zm3", "start": 67.05, "end": 73.02, "strength": 0.07},
        {"id": "zm4", "start": 88.01, "end": 91.02, "strength": 0.05},
    ], [[0.0, 2.75], [3.61, 6.11], [35.01, 39.63],
        [68.78, 73.77], [87.26, 91.02]], [[0.0, 25.0]]),
    (43.54, [
        {"id": "zm1", "start": 0.0, "end": 3.12,
         "strength": 0.05, "mode": "ease"},
        {"id": "zm4", "start": 3.84, "end": 8.56,
         "strength": 0.05, "mode": "ease"},
        {"id": "zm2", "start": 21.87, "end": 27.71,
         "strength": 0.06, "mode": "ease"},
        {"id": "zm5", "start": 27.73, "end": 29.23,
         "strength": 0.05, "mode": "ease"},
        {"id": "zm3", "start": 29.23, "end": 34.13,
         "strength": 0.05, "mode": "ease"},
        {"id": "zm7", "start": 36.26, "end": 42.73,
         "strength": 0.05, "mode": "ease"},
    ], [[0.31, 10.13], [26.98, 35.06], [36.445, 42.545]],
     [[0.0, 10.13], [21.82, 35.06], [36.21, 37.84]]),
])
def test_exact_production_zoom_proofs_validate_after_budget_clipping(
        duration, zooms, raw_ranges, expected_ranges):
    """Re-run the three post-deploy failed EDL/range geometries exactly."""
    edl = _edl(keep=[[0.0, duration]], effects={"zooms": zooms})
    timeline = Timeline(edl["keep"])
    ranges = renderer._validated_check_ranges(raw_ranges, duration)
    ranges = renderer._contain_check_items(
        edl, timeline, ranges, duration, {})
    ranges = renderer._validated_check_ranges(ranges, duration)
    assert len(ranges) == len(expected_ranges)
    for actual, expected in zip(ranges, expected_ranges):
        assert actual == pytest.approx(expected)
    for start, end in ranges:
        window = stitch.window_edl(
            edl, timeline, start, end, keep_audio=True)
        schemas.validate_edl(window, duration=duration)


def test_proof_window_clips_graphic_edges_and_rebases_local_motion():
    motion = {
        "x": [
            {"t": 0.0, "v": 0.1},
            {"t": 4.0, "v": 0.9, "ease": "in"},
            {"t": 8.0, "v": 0.3, "ease": "out"},
        ],
        "opacity": [
            {"t": 0.0, "v": 0.2},
            {"t": 8.0, "v": 1.0},
        ],
    }
    edl = _edl(
        texts=[
            {"id": "left", "text": "LEFT", "start": 1.0, "end": 5.0,
             "entrance": "slide_up", "exit": "fade"},
            {"id": "right", "text": "RIGHT", "start": 5.0, "end": 9.0,
             "entrance": "pop", "exit": "drop"},
            {"id": "text-motion", "text": "MOVE", "start": 1.0,
             "end": 9.0, "entrance": "none", "exit": "none",
             "motion": motion},
        ],
        vectors=[{
            "id": "vec-motion", "kind": "arrow", "start": 1.0,
            "end": 9.0, "x": 0.5, "y": 0.5, "width": 0.25,
            "height": 0.08, "color": "#FFFFFF", "motion": motion,
        }],
    )
    window = stitch.window_edl(
        edl, Timeline(edl["keep"]), 3.0, 7.0, keep_audio=True)
    texts = {item["id"]: item for item in window["texts"]}
    assert (texts["left"]["start"], texts["left"]["end"]) == (0.0, 2.0)
    assert texts["left"]["entrance"] == "none"
    assert texts["left"]["exit"] == "fade"
    assert (texts["right"]["start"], texts["right"]["end"]) == (2.0, 4.0)
    assert texts["right"]["entrance"] == "pop"
    assert texts["right"]["exit"] == "none"

    vector = window["vectors"][0]
    for graphic in (texts["text-motion"], vector):
        assert (graphic["start"], graphic["end"]) == (0.0, 4.0)
        for prop in ("x", "opacity"):
            curve = graphic["motion"][prop]
            assert curve[0]["t"] == 0.0
            assert curve[-1]["t"] == 4.0
            assert curve[0]["v"] == pytest.approx(
                schemas.anim_value(motion[prop], 2.0), abs=1e-4)
            assert curve[-1]["v"] == pytest.approx(
                schemas.anim_value(motion[prop], 6.0), abs=1e-4)
            # The proof must show the same moving graphic between its sampled
            # endpoints, not merely land at the same boundary values. Reusing
            # a nonlinear ease name on a shortened segment changes the curve
            # (the old ease-in path was 0.4375 here instead of 0.5625).
            for local_t in (0.25, 0.75, 1.0, 1.25, 2.5, 3.0, 3.75):
                assert schemas.anim_value(curve, local_t) == pytest.approx(
                    schemas.anim_value(motion[prop], local_t + 2.0),
                    abs=0.01)
            assert len(curve) <= 24
    schemas.validate_edl(window, duration=20.0)


def test_proof_window_drops_subminimum_boundary_sliver():
    # A valid full EDL can intersect a preview-check window for only 0.03s at
    # one keep boundary. The temporary window must not turn that into an
    # invalid keep and fail before ffmpeg starts.
    edl = _edl(keep=[[0.0, 477.39], [477.39, 500.0]])
    window = stitch.window_edl(
        edl, Timeline(edl["keep"]), 477.36, 480.0, keep_audio=True)
    assert window["keep"] == [[477.39, 480.0]]
    schemas.validate_edl(window, duration=500.0)


def test_canvas_proof_geometry_respects_output_ratio():
    width, height = renderer.frame_dims(1920, 1080, "9:16")
    width, height, _fps = renderer.preview_geometry(width, height, 30.0)
    assert height > width


def test_changed_section_renderer_outputs_only_requested_seconds(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "proof.mp4"
    media.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=24:duration=4", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=4", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(source),
    ], timeout=60)
    edl = _edl(
        keep=[[0.0, 4.0]],
        effects={"stylize": [{"id": "st1", "kind": "grain",
                               "start": 0.0, "end": 4.0,
                               "intensity": 0.25}]})
    duration, ranges, mapped = renderer._render_changed_sections(
        7, {"version": 2, "json": edl},
        {"video": {"duration": 4.0, "width": 320, "height": 180,
                   "fps": 24.0}, "words": []},
        str(source), str(tmp_path), {}, str(output), [[0.5, 2.5]], [1.5])
    assert output.exists()
    assert duration == pytest.approx(2.0, abs=0.15)
    assert ranges == [[0.5, 2.5]]
    assert mapped == [1.0]


def test_windowed_proof_keeps_and_shifts_program_audio():
    edl = _edl(music=[{"id": "m1", "storage_key": "song.mp3",
                       "start": 2.0, "end": 12.0, "offset_s": 1.0}],
               sfx=[{"id": "s1", "storage_key": "hit.mp3", "at": 6.0}],
               voiceover=[{"id": "v1", "asset_key": "vo.mp3",
                           "start_output_s": 3.0,
                           "source_offset_s": 0.0}])
    window = stitch.window_edl(edl, Timeline(edl["keep"]), 5.0, 8.0,
                               keep_audio=True)
    assert window["music"][0]["start"] == 0.0
    assert window["music"][0]["end"] == 3.0
    assert window["music"][0]["offset_s"] == 4.0
    assert window["sfx"][0]["at"] == 1.0
    assert window["voiceover"][0]["start_output_s"] == 0.0
    assert window["voiceover"][0]["source_offset_s"] == 2.0


def test_proof_window_clips_stylize_and_preserves_global_effects():
    edl = _edl(effects={
        "stylize": [
            {"id": "st1", "kind": "grain", "start": 0.0,
             "end": 10.0, "intensity": 0.4},
            {"id": "st2", "kind": "vignette", "start": None,
             "end": None, "intensity": 0.3},
        ],
        "regions": [{"id": "rg1", "kind": "blur", "x": 0.1,
                     "y": 0.1, "w": 0.2, "h": 0.2,
                     "start": None, "end": None}],
        "custom": [{"id": "cf1", "chain": "hflip",
                    "start": None, "end": None}],
    })
    window = stitch.window_edl(edl, Timeline(edl["keep"]), 3.0, 5.0,
                               keep_audio=True)
    stylize = window["effects"]["stylize"]
    assert stylize[0]["start"] == 0.0
    assert stylize[0]["end"] == 2.0
    assert stylize[1]["start"] is None
    assert window["effects"]["regions"][0]["start"] is None
    assert window["effects"]["custom"][0]["start"] is None


def test_deploy_workflow_right_sizes_and_coalesces():
    workflow = (ROOT / ".github/workflows/deploy-executor.yml").read_text()
    assert "--cpu 4 --memory 8Gi --concurrency 1" in workflow
    assert "--cpu 1 --memory 2Gi --concurrency 4" in workflow
    assert "--min-instances 0 --max-instances 5" in workflow
    assert "coalesce rapid pushes" in workflow
    assert 'git diff --quiet "$GITHUB_SHA" "$latest" -- worker/' in workflow
    assert "src-${launcher_hash}" in workflow
    assert '"$current_launcher" != "$LAUNCHER_IMAGE"' in workflow
    assert "desired_launcher=" not in workflow
    heavy_health = "valmera-executor-950454325677.us-central1.run.app/health"
    assert heavy_health not in workflow
    assert config.REMOTE_AGENT_DISPATCH_SLOTS >= 5


def test_old_tool_results_are_compacted_without_breaking_protocol():
    messages = []
    for i in range(10):
        messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "get_edl",
                                         "arguments": "{}"}}],
        })
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "x" * 1800})
    changed = agent_loop._compact_old_tool_results(messages, keep_latest=3)
    tools = [m for m in messages if m["role"] == "tool"]
    assert changed == 7
    assert "compacted" in tools[0]["content"]
    assert len(tools[-1]["content"]) == 1800
    assert [m["tool_call_id"] for m in tools] == [f"c{i}" for i in range(10)]


def test_tpm_estimate_does_not_count_embedded_image_bytes():
    small = [{"role": "user", "content": [{"type": "image_url",
              "image_url": {"url": "data:image/jpeg;base64," + "a" * 10}}]}]
    huge = [{"role": "user", "content": [{"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + "a" * 500000}}]}]
    tools = [{"function": {"name": "get_edl"}}]
    assert agent_loop._agent_request_token_estimate(small, tools, 4000) \
        == agent_loop._agent_request_token_estimate(huge, tools, 4000)


def test_tpm_estimate_reserves_more_for_non_ascii_prompts():
    ascii_message = [{"role": "user", "content": "a" * 4000}]
    arabic_message = [{"role": "user", "content": "م" * 4000}]
    tools = [{"function": {"name": "get_edl"}}]
    assert agent_loop._agent_request_token_estimate(
        arabic_message, tools, 4000) > agent_loop._agent_request_token_estimate(
            ascii_message, tools, 4000) + 3000


def test_windowed_proof_clips_bounded_voiceover():
    edl = _edl(voiceover=[{
        "id": "v1", "asset_key": "main.mov", "start_output_s": 3.0,
        "source_offset_s": 4.0, "duration_s": 4.0,
    }])
    window = stitch.window_edl(edl, Timeline(edl["keep"]), 5.0, 8.0,
                               keep_audio=True)
    assert window["voiceover"][0]["start_output_s"] == 0.0
    assert window["voiceover"][0]["source_offset_s"] == 6.0
    assert window["voiceover"][0]["duration_s"] == 2.0
    after = stitch.window_edl(edl, Timeline(edl["keep"]), 7.1, 8.0,
                              keep_audio=True)
    assert after["voiceover"] == []


def test_render_signature_is_exact_and_pipeline_scoped():
    row = {"version": 2, "json": _edl()}
    first = agent_tools._render_signature(row, "preview")
    assert first == agent_tools._render_signature(
        {"version": 99, "json": _edl()}, "preview")
    assert first != agent_tools._render_signature(row, "preview_check", [[0, 2]])
    assert first != agent_tools._render_signature(
        row, "preview", audio_model_review=False)
    assert len(first) == 64


def test_modal_lifecycle_is_short_and_diagnostics_use_probe():
    source = (WORKER / "modal_app.py").read_text()
    workflow = (ROOT / ".github/workflows/deploy-modal-executor.yml").read_text()
    cloud = (ROOT / ".github/workflows/deploy-executor.yml").read_text()
    assert '"scaledown_window": 10' in source
    assert 'name="preview", cpu=PREVIEW_CPU, memory=PREVIEW_MEMORY' in source
    assert 'name="index", cpu=BATCH_CPU, memory=INDEX_MEMORY' in source
    assert 'name="index_eu", cpu=BATCH_CPU, memory=INDEX_MEMORY' in source
    assert 'cpu=(0.125, 1.0), memory=AGENT_MEMORY' in source
    assert '@modal.concurrent(max_inputs=2, target_inputs=1)' in source
    assert 'name="probe"' in source
    assert remote._modal_function_name("ytprobe") == "probe"
    assert '"valmera-executor", "probe"' in workflow
    assert "coalesce rapid worker pushes" in workflow
    assert "branches: [main]" not in cloud


def test_renderer_caps_mux_to_expected_duration():
    source = (WORKER / "renderer.py").read_text()
    assert source.count('"-t", f"{expected_out_s:.3f}"') >= 3


def test_tpm_reservation_waits_for_live_fleet_capacity(monkeypatch):
    class Cursor:
        def __init__(self, raw):
            self.raw = raw
            self.sql = ""
            self.writes = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            self.sql = sql
            if "INSERT INTO app_kv" in sql:
                self.writes.append(params)

        def fetchone(self):
            if "to_regclass" in self.sql:
                return {"t": "app_kv"}
            if "SELECT value" in self.sql:
                return {"value": self.raw}
            return None

    class Conn:
        def __init__(self, raw):
            self.cur = Cursor(raw)

        def cursor(self):
            return self.cur

    monkeypatch.setattr(dbx.time, "time", lambda: 1000.0)
    open_conn = Conn("[]")
    assert dbx.reserve_llm_tokens(
        open_conn, 80, 150, 60, "job:step:one") == 0
    assert open_conn.cur.writes
    stored = json.loads(open_conn.cur.writes[-1][1])
    assert stored[-1][2] == "job:step:one"

    full_conn = Conn("[[990,100]]")
    assert dbx.reserve_llm_tokens(full_conn, 60, 150, 60) \
        == pytest.approx(50.0)
    assert not full_conn.cur.writes

    reconcile_conn = Conn('[[990,80,"job:step:one"],[995,20]]')
    assert dbx.reconcile_llm_tokens(
        reconcile_conn, "job:step:one", 31, 60) is True
    reconciled = json.loads(reconcile_conn.cur.writes[-1][1])
    assert reconciled == [[990.0, 31, "job:step:one"], [995.0, 20]]


def test_live_turn_adopts_mid_edit_messages(monkeypatch):
    rows = [{"id": 12, "content": "also make it vertical", "meta": None}]

    class WorkerDb:
        @staticmethod
        def run(fn, *args):
            assert fn is dbx.adopt_queued_agent_steers
            assert args[:2] == (3, 4)
            return {"messages": rows, "job_ids": [22]}

    class Ctx:
        user_message = "trim the intro"
        editing_metrics = {}

    monkeypatch.setattr(agent_loop, "_attachment_context",
                        lambda *_args: "")
    messages = []
    ctx = Ctx()
    newest = agent_loop._adopt_steering_messages(
        ctx, WorkerDb(), {"id": 4, "project_id": 3}, 7, messages, 10)
    assert newest == 12
    assert messages[-1]["content"] == "also make it vertical"
    assert "newest user message wins" in messages[0]["content"]
    assert Ctx.editing_metrics["steering_messages_adopted"] == 1
    assert ctx.adopted_steer_job_ids == {22}


def test_continuation_adopts_and_retires_steers_under_stable_root(monkeypatch):
    calls = []

    class WorkerDb:
        @staticmethod
        def run(fn, *args):
            calls.append((fn, args))
            if fn is dbx.adopt_queued_agent_steers:
                return {"messages": [{"id": 14, "content": "add music",
                                      "meta": None}],
                        "job_ids": [31]}
            if fn is dbx.complete_adopted_agent_steers:
                return 1
            raise AssertionError(fn)

    ctx = type("Ctx", (), {"user_message": "make a montage",
                            "editing_metrics": {}})()
    monkeypatch.setattr(agent_loop, "_attachment_context",
                        lambda *_args: "")
    continuation = {
        "id": 40, "project_id": 3,
        "payload": {"root_agent_job_id": 4,
                    "logical_turn_continuation": True},
    }
    messages = []
    agent_loop._adopt_steering_messages(
        ctx, WorkerDb(), continuation, 7, messages, 10)
    agent_loop._complete_adopted_steers(ctx, WorkerDb(), continuation)

    assert calls[0][1][:2] == (3, 4)
    assert calls[1][1] == ([31], 4)
    assert ctx.adopted_steer_job_ids == set()
