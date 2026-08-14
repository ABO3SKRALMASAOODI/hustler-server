"""Cost controls must preserve the edit while bounding repeated compute."""

from pathlib import Path
import sys

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


def test_proof_range_guard_clamps_full_length_abuse():
    ranges = renderer._validated_check_ranges([[0.0, 30.0]], 30.0)
    assert ranges == [[0.0, 25.0]]
    many = [[float(i), float(i) + 5.0] for i in range(0, 40, 5)]
    clamped = renderer._validated_check_ranges(many, 40.0)
    assert len(clamped) <= 6
    assert sum(b - a for a, b in clamped) <= 25.0 + 1e-6


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
    assert config.REMOTE_AGENT_DISPATCH_SLOTS >= 10


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


def test_render_signature_is_exact_and_pipeline_scoped():
    row = {"version": 2, "json": _edl()}
    first = agent_tools._render_signature(row, "preview")
    assert first == agent_tools._render_signature(
        {"version": 99, "json": _edl()}, "preview")
    assert first != agent_tools._render_signature(row, "preview_check", [[0, 2]])
    assert len(first) == 64


def test_modal_lifecycle_is_short_and_diagnostics_use_probe():
    source = (WORKER / "modal_app.py").read_text()
    workflow = (ROOT / ".github/workflows/deploy-modal-executor.yml").read_text()
    cloud = (ROOT / ".github/workflows/deploy-executor.yml").read_text()
    assert '"scaledown_window": 10' in source
    assert 'name="preview", cpu=PREVIEW_CPU, memory=4096' in source
    assert 'cpu=(0.125, 1.0), memory=1024' in source
    assert '@modal.concurrent(max_inputs=6, target_inputs=4)' in source
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
    assert dbx.reserve_llm_tokens(open_conn, 80, 150, 60) == 0
    assert open_conn.cur.writes

    full_conn = Conn("[[990,100]]")
    assert dbx.reserve_llm_tokens(full_conn, 60, 150, 60) \
        == pytest.approx(50.0)
    assert not full_conn.cur.writes


def test_live_turn_adopts_mid_edit_messages(monkeypatch):
    rows = [{"id": 12, "content": "also make it vertical", "meta": None}]

    class WorkerDb:
        @staticmethod
        def run(fn, *_args):
            assert fn is dbx.adopt_queued_agent_steers
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
