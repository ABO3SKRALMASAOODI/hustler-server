"""Cost controls must preserve the edit while bounding repeated compute."""

from pathlib import Path
import sys

import pytest

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parent
sys.path.insert(0, str(WORKER))

import agent_tools  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import http_server  # noqa: E402
import main  # noqa: E402
import media  # noqa: E402
import remote  # noqa: E402
import renderer  # noqa: E402
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


def test_proof_range_guard_rejects_full_length_abuse():
    with pytest.raises(dbx.PermanentJobError):
        renderer._validated_check_ranges([[0.0, 30.0]], 30.0)


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
    edl = _edl(keep=[[0.0, 4.0]])
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


def test_deploy_workflow_right_sizes_and_coalesces():
    workflow = (ROOT / ".github/workflows/deploy-executor.yml").read_text()
    assert "--cpu 4 --memory 8Gi --concurrency 1" in workflow
    assert "--cpu 1 --memory 2Gi --concurrency 4" in workflow
    assert "--min-instances 0 --max-instances 5" in workflow
    assert "coalesce rapid pushes" in workflow
    assert 'git diff --quiet "$GITHUB_SHA" "$latest" -- worker/' in workflow
    assert "src-${launcher_hash}" in workflow
    heavy_health = "valmera-executor-950454325677.us-central1.run.app/health"
    assert heavy_health not in workflow
    assert config.REMOTE_AGENT_DISPATCH_SLOTS >= 10
