"""Provider migration invariants: Modal is cheaper without duplicate work."""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import modal_app  # noqa: E402
import remote  # noqa: E402


JOB = {"id": 42, "type": "preview", "project_id": 7, "user_id": 3,
       "attempts": 0, "total_claims": 4,
       "payload": {"edl_version": 5}}


class _Call:
    object_id = "fc-durable-1"

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error

    def get(self, timeout=None):
        if self.error:
            raise self.error
        return self.answer


class _Function:
    def __init__(self, call):
        self.call = call
        self.jobs = []

    def spawn(self, job=None):
        self.jobs.append(job)
        return self.call


@pytest.fixture(autouse=True)
def _no_real_remote_ledger(monkeypatch):
    """Unit Modal calls never contact the configured production database."""
    class Ledger:
        def run(self, fn, *args, **kwargs):
            if fn is dbx.record_remote_execution:
                return False
            if fn is dbx.get_remote_execution:
                return None
            return True

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Ledger)


def _enable(monkeypatch, percent=100):
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_PERCENT", percent)
    monkeypatch.setattr(config, "MODAL_EU_PERCENT", 0)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES", frozenset({
        "preview", "final", "index", "agent_turn"}))


def test_rollout_selection_is_stable_per_job(monkeypatch):
    _enable(monkeypatch, percent=37)
    first = remote._modal_selected(JOB)
    assert all(remote._modal_selected(dict(JOB)) == first for _ in range(20))
    monkeypatch.setattr(config, "MODAL_EXECUTOR_PERCENT", 0)
    assert remote._modal_selected(JOB) is False
    monkeypatch.setattr(config, "MODAL_EXECUTOR_PERCENT", 100)
    assert remote._modal_selected(JOB) is True


def test_redesign_policy_bypasses_legacy_percentage_rollout(monkeypatch):
    _enable(monkeypatch, percent=0)
    job = dict(JOB, payload={"execution_policy": "redesign"})
    assert remote._modal_selected(job) is True


def test_unstamped_and_invalid_policy_are_safely_legacy():
    assert config.execution_policy_for(JOB) == "legacy"
    assert config.execution_policy_for(
        dict(JOB, payload={"execution_policy": "redesign"})) == "redesign"
    assert config.execution_policy_for(
        dict(JOB, payload={"execution_policy": "future"})) == "legacy"


def test_orchestration_job_families_have_isolated_functions():
    assert remote._modal_function_name("agent_turn") == "agent"
    assert remote._modal_function_name("mcp_tool") == "mcp"
    assert remote._modal_function_name("shorts_plan") == "shorts"


def test_mcp_orchestration_never_falls_back_to_dispatcher(monkeypatch):
    _enable(monkeypatch)
    function = _Function(_Call({"result": {"text": "ok"},
                                "job_completed": True}))
    selected = []
    monkeypatch.setattr(
        remote, "_modal_function",
        lambda name: selected.append(name) or function)
    mcp_job = dict(JOB, type="mcp_tool", payload={"tool": "get_edl"})

    result = remote.run_mcp_remote(None, mcp_job)

    assert result["text"] == "ok"
    assert selected == ["mcp"]


def test_eu_rollout_is_stable_and_limited_to_configured_media(monkeypatch):
    monkeypatch.setattr(config, "MODAL_EU_PERCENT", 37)
    monkeypatch.setattr(config, "MODAL_EU_TYPES", frozenset({
        "final", "mcp_tool"}))
    final = dict(JOB, type="final")
    first = remote._modal_eu_selected(final)
    assert all(remote._modal_eu_selected(dict(final)) == first
               for _ in range(20))
    assert remote._modal_eu_selected(dict(JOB, type="preview")) is False

    # Synchronous calls have no database id. Canonical JSON keeps equivalent
    # retries together even when dict insertion order differs.
    left = dict(JOB, id=None, type="mcp_tool",
                payload={"tool": "__media__", "args": {"b": 2, "a": 1}})
    right = dict(left, payload={
        "args": {"a": 1, "b": 2}, "tool": "__media__"})
    assert remote._modal_eu_selected(left) \
        == remote._modal_eu_selected(right)


def test_success_uses_durable_modal_function_and_marks_completion(monkeypatch):
    _enable(monkeypatch)
    function = _Function(_Call({
        "result": {"render_asset_id": 99}, "job_completed": True}))
    monkeypatch.setattr(remote, "_modal_function", lambda name: function)
    owned = []
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", owned.append)

    result = remote._run_remote(JOB)

    assert result["render_asset_id"] == 99
    assert result.pop("_remote_job_completed") is True
    assert function.jobs[0]["total_claims"] == 4
    assert owned == [42]


def test_modal_call_id_is_persisted_before_waiting(monkeypatch):
    _enable(monkeypatch)
    events = []

    class Ledger:
        def run(self, fn, *args, **kwargs):
            events.append((fn.__name__, args))
            return True

        def reset(self):
            pass

    function = _Function(_Call({
        "result": {"render_asset_id": 99}, "job_completed": True}))
    monkeypatch.setattr(remote, "_modal_function", lambda _name: function)
    monkeypatch.setattr(remote.dbx, "Db", Ledger)

    remote._run_modal(JOB)

    record = next(row for row in events
                  if row[0] == "record_remote_execution")
    assert record[1][0:5] == (42, 4, "modal", "fc-durable-1", "preview")
    assert any(row[0] == "finish_remote_execution" for row in events)


def test_duplicate_modal_spawn_reconnects_recorded_owner(monkeypatch):
    _enable(monkeypatch)
    duplicate = _Call({"error": "must not own", "job_completed": False})
    function = _Function(duplicate)
    owner = _Call({"result": {"render_asset_id": 77},
                   "job_completed": True})

    class Ledger:
        def run(self, fn, *args, **kwargs):
            if fn is dbx.record_remote_execution:
                return False
            if fn is dbx.get_remote_execution:
                return {"job_id": 42, "total_claims": 4,
                        "provider": "modal", "call_id": "fc-owner",
                        "state": "running"}
            return True

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Ledger)
    monkeypatch.setattr(remote, "_modal_function", lambda _name: function)
    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(
            from_id=lambda call_id: owner if call_id == "fc-owner" else None)))

    result = remote._run_modal(JOB)

    assert result["render_asset_id"] == 77
    assert result.pop("_remote_job_completed") is True
    assert function.jobs  # the duplicate was accepted but never trusted


def test_guardian_heartbeats_only_after_provider_proves_call_is_running(
        monkeypatch):
    class RunningCall:
        def get(self, timeout=None):
            raise TimeoutError("still running")

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=lambda _call_id: RunningCall())))
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            return True

    row = {"provider": "modal", "call_id": "fc-1", "job_id": 42,
           "total_claims": 4, "type": "preview", "project_id": 7,
           "user_id": 3, "attempts": 1, "payload": {}}
    event = remote.reconcile_remote_execution(WorkerDb(), row)
    assert event["status"] == "running"
    assert calls == [(dbx.heartbeat_remote_execution, (42, 4))]


def test_guardian_attachment_grace_exceeds_poll_interval():
    assert config.REMOTE_GUARDIAN_ATTACH_GRACE_S \
        > config.REMOTE_GUARDIAN_INTERVAL_S


def test_guardian_does_not_kill_a_just_spawned_invisible_modal_call(
        monkeypatch):
    import modal

    class NotVisibleYet:
        def get(self, timeout=None):
            raise modal.exception.OutputExpiredError()

    monkeypatch.setattr(
        modal.FunctionCall, "from_id",
        staticmethod(lambda _call_id: NotVisibleYet()))
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            return True

    row = {"provider": "modal", "call_id": "fc-new", "job_id": 42,
           "total_claims": 4, "type": "preview", "project_id": 7,
           "user_id": 3, "attempts": 1, "payload": {},
           "submitted_at": datetime.now(timezone.utc)}

    event = remote.reconcile_remote_execution(WorkerDb(), row)

    assert event["status"] == "visibility_pending"
    assert calls == []


def test_modal_attached_dispatcher_reconnects_visibility_race(monkeypatch):
    import modal

    _enable(monkeypatch)

    class EventuallyVisible(_Call):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def get(self, timeout=None):
            self.polls += 1
            if self.polls == 1:
                raise modal.exception.OutputExpiredError()
            return {"result": {"ok": True}, "job_completed": True}

    call = EventuallyVisible()
    function = _Function(call)
    monkeypatch.setattr(remote, "_modal_function", lambda _name: function)
    monkeypatch.setattr(
        modal.FunctionCall, "from_id", staticmethod(lambda _call_id: call))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)

    result = remote._run_modal(JOB)

    assert result["ok"] is True
    assert result.pop("_remote_job_completed") is True
    assert call.polls == 2


def test_modal_visibility_grace_is_bounded():
    now = datetime.now(timezone.utc)
    assert remote._modal_visibility_grace_active({
        "submitted_at": now - timedelta(seconds=10)}, now)
    assert not remote._modal_visibility_grace_active({
        "submitted_at": now - timedelta(seconds=61)}, now)


def test_guardian_terminal_budget_failure_is_not_requeued(monkeypatch):
    class FailedCall:
        def get(self, timeout=None):
            raise RuntimeError("Modal workspace spending limit exceeded")

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=lambda _call_id: FailedCall())))
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            return True

    row = {"provider": "modal", "call_id": "fc-1", "job_id": 42,
           "total_claims": 4, "type": "preview", "project_id": 7,
           "user_id": 3, "attempts": 1, "payload": {}}
    event = remote.reconcile_remote_execution(WorkerDb(), row)
    assert event["status"] == "failed"
    assert any(fn is dbx.finish_remote_execution for fn, _ in calls)
    assert any(fn is dbx.finish_job for fn, _ in calls)
    assert not any(fn is dbx.requeue_job for fn, _ in calls)


def test_prelaunch_failure_can_fall_back_to_cloud_run(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "MODAL_CLOUD_RUN_FALLBACK", True)
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda job, function_override=None: (_ for _ in ()).throw(
            remote.ModalLaunchUnavailable("no call id")))
    seen = []
    monkeypatch.setattr(
        remote, "_run_cloud",
        lambda job, url_override=None: seen.append(job["id"]) or {"ok": True})

    assert remote._run_remote(JOB) == {"ok": True}
    assert seen == [42]


def test_redesign_prelaunch_failure_never_falls_back(monkeypatch):
    _enable(monkeypatch, percent=0)
    monkeypatch.setattr(config, "MODAL_CLOUD_RUN_FALLBACK", True)
    job = dict(JOB, payload={"execution_policy": "redesign"})
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda job, function_override=None: (_ for _ in ()).throw(
            remote.ModalLaunchUnavailable("temporary control-plane failure")))
    cloud = []
    monkeypatch.setattr(
        remote, "_run_cloud",
        lambda *args, **kwargs: cloud.append(True) or {"wrong": True})

    with pytest.raises(remote.ModalLaunchUnavailable):
        remote._run_remote(job)
    assert cloud == []


def test_rejected_modal_launch_restores_local_shutdown_ownership(monkeypatch):
    _enable(monkeypatch)

    class Rejected:
        @staticmethod
        def spawn(_job):
            raise RuntimeError("control plane rejected launch")

    events = []
    monkeypatch.setattr(remote, "_modal_function", lambda _name: Rejected())
    monkeypatch.setattr(
        remote.dbx, "mark_remote_owned",
        lambda job_id: events.append(("mark", job_id)))
    monkeypatch.setattr(
        remote.dbx, "unmark_remote_owned",
        lambda job_id: events.append(("unmark", job_id)))

    with pytest.raises(remote.ModalLaunchUnavailable):
        remote._run_modal(JOB)
    assert events == [("mark", 42), ("unmark", 42)]


def test_shutdown_winner_refuses_modal_before_spawn(monkeypatch):
    _enable(monkeypatch)

    class MustNotSpawn:
        @staticmethod
        def spawn(_job):
            raise AssertionError("shutdown-lost job must not reach Modal")

    monkeypatch.setattr(remote, "_modal_function", lambda _name: MustNotSpawn())
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _job_id: False)

    with pytest.raises(remote.ModalLaunchUnavailable, match="shutdown"):
        remote._run_modal(JOB)


def test_postlaunch_transport_failure_reconnects_without_cloud_fallback(
        monkeypatch):
    _enable(monkeypatch)
    function = _Function(_Call(error=ConnectionError("SDK stream reset")))
    monkeypatch.setattr(remote, "_modal_function", lambda name: function)
    monkeypatch.setattr(
        remote, "_recover_modal_result",
        lambda call_id, job, deadline: {
            "result": {"ok": True}, "job_completed": True})
    cloud = []
    monkeypatch.setattr(
        remote, "_run_cloud",
        lambda *args, **kwargs: cloud.append(True) or {"wrong": True})

    result = remote._run_remote(JOB)

    assert result["ok"] is True
    assert result.pop("_remote_job_completed") is True
    assert cloud == []


def test_postlaunch_terminal_failure_surfaces_without_hour_long_recovery(
        monkeypatch):
    _enable(monkeypatch)
    function = _Function(_Call(error=ValueError("container failed to boot")))
    monkeypatch.setattr(remote, "_modal_function", lambda name: function)
    recovered = []
    monkeypatch.setattr(
        remote, "_recover_modal_result",
        lambda *args: recovered.append(True) or {"result": {"wrong": True}})

    try:
        remote._run_remote(JOB)
        assert False, "terminal Modal failure should be raised"
    except remote.RemoteExecutorError as exc:
        assert "container failed to boot" in str(exc)
    assert recovered == []


def test_postlaunch_wait_outlasts_short_dispatch_deadline(monkeypatch):
    """A durable render is never requeued while the same paid call runs."""
    _enable(monkeypatch)
    function = _Function(_Call({"result": {"ok": True},
                                "job_completed": True}))
    monkeypatch.setattr(remote, "_modal_function", lambda name: function)
    monkeypatch.setattr(config, "EXECUTOR_REQUEST_TIMEOUT_S", 3600)
    monkeypatch.setattr(config, "executor_timeout_for", lambda kind: 100)
    seen = []
    original_get = function.call.get

    def capture_timeout(timeout=None):
        seen.append(timeout)
        return original_get(timeout)

    function.call.get = capture_timeout
    remote._run_modal(JOB)
    assert seen[0] > 3600


def test_final_and_index_skip_slow_cloud_run_job_launcher_when_modal_selected(
        monkeypatch):
    _enable(monkeypatch)
    launched = []
    monkeypatch.setattr(
        remote, "_launch_batch_and_wait",
        lambda db, job: launched.append(job["id"]) or {"wrong": True})
    monkeypatch.setattr(remote, "_run_request_with_capacity_fallback",
                        lambda job: {"provider": "modal"})

    assert remote.run_render_remote(None, dict(JOB, type="final")) \
        == {"provider": "modal"}
    assert remote.run_index_remote(None, dict(JOB, type="index")) \
        == {"provider": "modal"}
    assert launched == []


def test_unconfigured_job_type_keeps_cloud_run(monkeypatch):
    _enable(monkeypatch)
    assert remote._modal_selected(dict(JOB, type="capture")) is False


def test_modal_warm_path_boots_real_runner_stack(monkeypatch):
    monkeypatch.setattr(
        modal_app, "_boot",
        lambda profile, role="executor", pricing_multiplier=1.0: None)
    monkeypatch.setattr(
        config, "require_core",
        lambda: (_ for _ in ()).throw(AssertionError(
            "warm-up must not apply a role-specific launch guard")))
    result = modal_app._run({"type": "__warm"}, "preview")
    report = result["result"]
    assert report["warmed"] is True
    assert report["profile"] == "preview"
    assert report["adapter_version"] == modal_app.adapter_version()
    assert "preview" in report["runners"]
    assert report["ffmpeg"].startswith("ffmpeg version")


def test_modal_agent_boot_routes_nested_compute_back_to_modal(monkeypatch):
    keys = ("WORKER_ROLE", "EXECUTOR_PROVIDER", "MODAL_EXECUTOR_PROFILE",
            "MODAL_EXECUTOR_ENABLED", "MODAL_EXECUTOR_PERCENT")
    before = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.pop("MODAL_EXECUTOR_ENABLED", None)
        os.environ.pop("MODAL_EXECUTOR_PERCENT", None)
        modal_app._boot("agent", role="agent_executor")
        assert os.environ["MODAL_EXECUTOR_ENABLED"] == "1"
        assert os.environ["MODAL_EXECUTOR_PERCENT"] == "100"
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_modal_dependency_context_excludes_ordinary_source():
    assert modal_app.DEPENDENCY_CONTEXT_IGNORE == ["**", "!requirements.txt"]


def test_modal_render_cpu_is_hard_capped_at_costed_profiles():
    assert modal_app.PREVIEW_CPU == (2.0, 2.0)
    assert modal_app.BATCH_CPU == (4.0, 4.0)
    assert modal_app.HEAVY_CPU == (4.0, 4.0)


def test_modal_memory_right_sizing_preserves_production_hard_limits():
    assert modal_app.PREVIEW_MEMORY == (2048, 4096)
    assert modal_app.BATCH_MEMORY == (4096, 16384)
    assert modal_app.INDEX_MEMORY == (4096, 16384)
    assert modal_app.LIGHT_MEMORY == (1024, 4096)
    assert modal_app.HEAVY_MEMORY == (16384, 32768)
    assert modal_app.AGENT_MEMORY == (1024, 2048)
    assert modal_app.PROBE_MEMORY == (1024, 4096)
    assert modal_app.HEALTH_MEMORY == (512, 1024)


def test_compute_fleet_has_explicit_us_and_bounded_eu_envelopes():
    assert modal_app.US_COMMON["region"] == "us"
    assert modal_app.EU_COMMON["region"] == "eu"
    assert modal_app.US_COMMON["routing_region"] == "us-east"
    assert modal_app.EU_COMMON["routing_region"] == "us-east"
    assert remote._modal_function_name("preview") == "preview"
    assert remote._modal_function_name("final") == "final"
    assert remote._modal_function_name("index") == "index"
    assert remote._modal_function_name("frames") == "light"
    assert remote._modal_function_name("capture") == "heavy"
    assert remote._modal_function_name("filmstrip") == "preview"
    assert remote._modal_function_name("fetch") == "egress"
    assert remote._modal_function_name("search") == "egress"
    assert remote._modal_function_name("clean") == "heavy"
    assert config.MODAL_FINAL_TIMEOUT_S > config.EXECUTOR_REQUEST_TIMEOUT_S
    assert config.MODAL_AGENT_TIMEOUT_S > config.EXECUTOR_REQUEST_TIMEOUT_S


def test_eu_index_falls_back_to_us_then_legacy_batch_before_launch(
        monkeypatch):
    monkeypatch.setattr(config, "MODAL_EU_PERCENT", 100)
    monkeypatch.setattr(config, "MODAL_EU_TYPES", frozenset({"index"}))
    seen = []
    function = _Function(_Call({"result": {"ok": True},
                                "job_completed": True}))

    def lookup(name):
        seen.append(name)
        if name in {"index_eu", "index"}:
            raise remote.ModalLaunchUnavailable(f"missing {name}")
        return function

    monkeypatch.setattr(remote, "_modal_function", lookup)
    result = remote._run_modal(dict(JOB, type="index"))
    assert result["ok"] is True
    assert result.pop("_remote_job_completed") is True
    assert seen == ["index_eu", "index", "batch"]


def test_filmstrip_is_forced_to_modal_without_cloud_run_fallback(monkeypatch):
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    seen = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda job, function_override=None: seen.append(
            (job["type"], function_override)) or {"available": True})
    monkeypatch.setattr(
        remote, "_run_cloud",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("filmstrip must not use the retired executor")))

    result = remote.run_filmstrip_remote(
        None, dict(JOB, type="filmstrip"))

    assert result == {"available": True}
    assert seen == [("filmstrip", "preview")]


def test_modal_compute_image_exposes_the_filmstrip_runner():
    import http_server
    assert "filmstrip" in http_server.COMPUTE_RUNNERS


def test_mcp_media_is_forced_to_modal_but_other_tools_are_rejected(monkeypatch):
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    seen = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda job, function_override=None: seen.append(
            (job["payload"]["tool"], function_override)) or {"ok": True})
    payload = {"tool": "__media__", "args": {}}

    assert remote.run_mcp_media_remote(7, payload, user_id=3) == {"ok": True}
    assert seen == [("__media__", "preview")]
    try:
        remote.run_mcp_media_remote(7, {"tool": "get_edl"}, user_id=3)
        assert False, "non-media tools must stay on the dispatcher"
    except ValueError as exc:
        assert "__media__ only" in str(exc)


def test_modal_compute_image_exposes_only_the_mcp_media_wrapper():
    import http_server
    assert http_server.COMPUTE_RUNNERS["mcp_tool"] is \
        http_server._run_mcp_media_job
    try:
        http_server._run_mcp_media_job(
            None, {"payload": {"tool": "apply_edit_recipe"}})
        assert False, "general editing tools must not run on compute executor"
    except ValueError as exc:
        assert "__media__ only" in str(exc)
