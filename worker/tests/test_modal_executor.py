"""Provider migration invariants: Modal is cheaper without duplicate work."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
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


def _enable(monkeypatch, percent=100):
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_PERCENT", percent)
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


def test_success_uses_durable_modal_function_and_marks_completion(monkeypatch):
    _enable(monkeypatch)
    function = _Function(_Call({
        "result": {"render_asset_id": 99}, "job_completed": True}))
    monkeypatch.setattr(remote, "_modal_function", lambda name: function)

    result = remote._run_remote(JOB)

    assert result["render_asset_id"] == 99
    assert result.pop("_remote_job_completed") is True
    assert function.jobs[0]["total_claims"] == 4


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
