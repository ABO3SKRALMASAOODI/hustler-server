"""Cloudflare canary routing must be fast, fenced, and safely reversible."""

import json
import os
from pathlib import Path
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import remote  # noqa: E402


JOB = {
    "id": 42, "type": "preview_check", "project_id": 7, "user_id": 3,
    "attempts": 1, "total_claims": 4,
    "payload": {"edl_version": 5, "execution_policy": "redesign"},
    "_execution_shape": {"total_bytes": 500_000_000,
                         "max_duration_s": 900},
}


def _enable(monkeypatch, percent=100):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", percent)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES", frozenset({
        "preview_check", "filmstrip", "index"}))
    monkeypatch.setattr(config, "CLOUDFLARE_MAX_INPUT_BYTES", 4 * 1024 ** 3)
    monkeypatch.setattr(config, "CLOUDFLARE_MAX_SOURCE_DURATION_S", 3600)
    remote._health_cache.clear()


def test_canary_selection_is_stable_and_capacity_gated(monkeypatch):
    _enable(monkeypatch, 37)
    first = remote._cloudflare_selected(JOB)
    assert all(remote._cloudflare_selected(dict(JOB)) == first
               for _ in range(20))
    assert remote._cloudflare_selected(dict(
        JOB, _execution_shape={"total_bytes": 5 * 1024 ** 3,
                               "max_duration_s": 900})) is False
    assert remote._cloudflare_selected(dict(
        JOB, _execution_shape={"total_bytes": 500_000_000,
                               "max_duration_s": 7200})) is False


def test_raw_index_uses_long_lived_modal_lane(monkeypatch):
    _enable(monkeypatch)
    raw = dict(JOB, type="index",
               payload={"asset_id": 9, "execution_policy": "redesign"},
               _execution_shape={"total_bytes": 1_631_253_121,
                                 "max_duration_s": 0})
    assert remote._cloudflare_selected(raw) is False

    prepared = dict(raw, payload={
        **raw["payload"],
        "client_proxy_key": "clientproxies/7/prepared.mp4",
    })
    assert remote._cloudflare_selected(prepared) is True


def test_provider_choice_is_stamped_once_under_the_queue_lease(monkeypatch):
    _enable(monkeypatch)
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is dbx.project_execution_shape:
                # psycopg2 returns PostgreSQL numerics as Decimal.  The first
                # provider request must receive the same strict-JSON shape as
                # a retry reloaded from JSONB, otherwise only attempt one
                # fails before Cloudflare sees it.
                return {"total_bytes": Decimal("10"),
                        "max_duration_s": Decimal("20.5")}
            if fn is dbx.stamp_execution_provider:
                return args[2]
            raise AssertionError(fn)

    job = {key: value for key, value in JOB.items()
           if key != "_execution_shape"}
    provider = remote.stamp_execution_provider(WorkerDb(), job)
    assert provider == "cloudflare"
    assert job["payload"]["execution_provider"] == "cloudflare"
    assert calls[-1][1] == (
        42, 4, "cloudflare", {"total_bytes": 10,
                               "max_duration_s": 20.5})
    assert job["payload"]["execution_shape"]["total_bytes"] == 10
    assert json.loads(json.dumps(job["payload"])) == job["payload"]


def test_provider_shape_database_numerics_are_strict_json_safe():
    shape = {
        "total_bytes": Decimal("500000000"),
        "max_duration_s": Decimal("900.25"),
        "unknown_duration_s": Decimal("NaN"),
    }

    safe = dbx._json_safe(shape)

    assert safe == {
        "total_bytes": 500000000,
        "max_duration_s": 900.25,
        "unknown_duration_s": None,
    }
    assert json.loads(json.dumps(safe)) == safe


def test_stamped_provider_is_immune_to_rollout_percentage_changes(monkeypatch):
    _enable(monkeypatch, 0)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    assert remote.desired_execution_provider(job) == "cloudflare"


class _Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise remote.requests.HTTPError(str(self.status_code))


def test_cloudflare_uses_deterministic_call_and_persists_before_wait(
        monkeypatch):
    _enable(monkeypatch)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    events = []

    class Ledger:
        def run(self, fn, *args, **kwargs):
            events.append((fn, args))
            return True

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Ledger)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned",
                        lambda job_id: events.append(("owned", (job_id,))))
    monkeypatch.setattr(remote.requests, "get", lambda *a, **k: _Response({
        "status": "ok", "provider": "cloudflare"}))
    posted = []
    monkeypatch.setattr(
        remote.requests, "post",
        lambda url, **kwargs: posted.append((url, kwargs)) or _Response({
            "result": {"ok": True}, "job_completed": True}))

    result = remote._run_remote(job)

    assert result["ok"] is True
    assert result.pop("_remote_job_completed") is True
    record = next(row for row in events
                  if isinstance(row[0], type(dbx.record_remote_execution))
                  and row[0] is dbx.record_remote_execution)
    call_id = record[1][3]
    assert call_id == remote._cloudflare_call_id(job)
    assert f"/calls/interactive/{call_id}" in posted[0][0]
    assert posted[0][1]["json"]["timeout_s"] > 0
    assert posted[0][1]["timeout"] == \
        config.CLOUDFLARE_START_OBSERVATION_S
    assert posted[0][1]["json"]["job"]["dispatch_submitted_at"] > 0


def test_shutdown_winner_refuses_cloudflare_before_launch(monkeypatch):
    _enable(monkeypatch)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    monkeypatch.setattr(remote.requests, "get", lambda *a, **k: _Response({
        "status": "ok", "provider": "cloudflare"}))
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _job_id: False)
    monkeypatch.setattr(
        remote.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("shutdown-lost job must not reach Cloudflare")))

    with pytest.raises(remote.CloudflareLaunchUnavailable, match="shutdown"):
        remote._run_cloudflare(job)


def test_cloudflare_authenticated_preflight_is_cached(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_SECRET", "shared-secret")
    gets = []
    monkeypatch.setattr(
        remote.requests, "get",
        lambda url, **kwargs: gets.append((url, kwargs)) or _Response({
            "status": "ok", "provider": "cloudflare"}))

    assert remote._cloudflare_preflight()["provider"] == "cloudflare"
    assert remote._cloudflare_preflight()["provider"] == "cloudflare"
    assert len(gets) == 1
    assert gets[0][1]["headers"]["Authorization"] == \
        "Bearer shared-secret"


def test_cloudflare_preflight_rejects_source_skew_before_launch(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        remote.requests, "get",
        lambda *_a, **_k: _Response({
            "status": "ok", "provider": "cloudflare",
            "source_version": "stale-source"}))

    with pytest.raises(remote.CloudflareLaunchUnavailable,
                       match="does not match"):
        remote._cloudflare_preflight()


def test_orchestration_is_cloudflare_eligible_without_media_shape(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        config, "CLOUDFLARE_EXECUTOR_TYPES",
        frozenset({"agent_turn", "mcp_tool", "shorts_plan"}))

    for job_type, lane in (("agent_turn", "agent"),
                           ("mcp_tool", "mcp"),
                           ("shorts_plan", "shorts")):
        job = dict(JOB, type=job_type, _execution_shape={})
        assert remote._cloudflare_selected(job) is True
        assert remote._cloudflare_lane(job_type) == lane


def test_synchronous_frames_use_unique_named_cloudflare_calls(monkeypatch):
    _enable(monkeypatch)
    # Production may have an explicit queue-backed allowlist from an older
    # deploy. The additive synchronous subset must not remain Modal-primary
    # merely because that unrelated env value has not yet learned `frames`.
    monkeypatch.setattr(
        config, "CLOUDFLARE_EXECUTOR_TYPES", frozenset({"preview_check"}))
    monkeypatch.setattr(
        config, "CLOUDFLARE_SYNCHRONOUS_TYPES", frozenset({"frames"}))
    job = {"id": None, "type": "frames", "project_id": 7,
           "total_claims": None,
           "payload": {"storage_key": "clips/7/a.mp4", "times": [1.0]}}

    assert remote._cloudflare_selected(job) is True
    assert remote._cloudflare_lane("frames") == "interactive"
    first = remote._cloudflare_call_id(job)
    assert remote._cloudflare_call_id(job) == first
    another = {key: value for key, value in job.items()
               if key != "_cloudflare_sync_nonce"}
    assert remote._cloudflare_call_id(another) != first


def test_synchronous_frames_skip_queue_ledger_and_run_on_cloudflare(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        config, "CLOUDFLARE_EXECUTOR_TYPES", frozenset({"frames"}))
    monkeypatch.setattr(
        config, "CLOUDFLARE_SYNCHRONOUS_TYPES", frozenset({"frames"}))
    monkeypatch.setattr(remote.requests, "get", lambda *a, **k: _Response({
        "status": "ok", "provider": "cloudflare"}))
    posted = []
    monkeypatch.setattr(
        remote.requests, "post",
        lambda url, **kwargs: posted.append((url, kwargs)) or _Response({
            "result": {"keys": ["scratch/frame.jpg"]}}))
    monkeypatch.setattr(
        remote.dbx, "Db", lambda: (_ for _ in ()).throw(
            AssertionError("id-less calls must not touch the queue ledger")))
    job = {"id": None, "type": "frames", "project_id": 7,
           "user_id": 3, "attempts": 0, "total_claims": None,
           "payload": {"storage_key": "clips/7/a.mp4", "times": [1.0]}}

    assert remote._run_remote(job)["keys"] == ["scratch/frame.jpg"]
    assert "/calls/interactive/cf-frames-" in posted[0][0]
    assert posted[0][1]["json"]["job"]["id"] is None


def test_terminal_synchronous_capacity_failure_falls_back_without_queue_row(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES", frozenset({"frames"}))
    monkeypatch.setattr(
        config, "CLOUDFLARE_EXECUTOR_TYPES", frozenset({"frames"}))
    monkeypatch.setattr(
        config, "CLOUDFLARE_SYNCHRONOUS_TYPES", frozenset({"frames"}))
    failure = remote.CloudflareTerminalFailure("container out of capacity")
    failure.failure_kind = "executor_capacity"
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        remote.dbx, "Db", lambda: (_ for _ in ()).throw(
            AssertionError("id-less fallback has no queue row to fence")))
    modal = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda job, function_override=None: modal.append(job["type"])
        or {"keys": []})
    job = {"id": None, "type": "frames", "project_id": 7,
           "user_id": 3, "attempts": 0, "total_claims": None,
           "payload": {"storage_key": "clips/7/a.mp4", "times": [1.0]}}

    assert remote._run_remote(job) == {"keys": []}
    assert modal == ["frames"]


def test_guardian_queues_modal_when_rollout_abandoned_before_run(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"mcp_tool"}))
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "failed",
        "envelope": {
            "error": "Cloudflare container startup was abandoned before /run",
            "retryable": True,
            "failure": {"kind": "provider_start_abandoned",
                        "retryable": True},
        },
    })
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is dbx.finish_remote_execution:
                return True
            if fn is dbx.requeue_provider_fallback:
                return True
            raise AssertionError(fn)

    row = {
        "provider": "cloudflare", "call_id": "cf-mcp-abandoned",
        "function_name": "mcp", "job_id": 52, "total_claims": 1,
        "type": "mcp_tool", "project_id": 7, "user_id": 3,
        "attempts": 1, "payload": {"execution_provider": "cloudflare"},
    }

    event = remote.reconcile_remote_execution(WorkerDb(), row)

    assert event["status"] == "provider_fallback_queued"
    assert calls == [
        (dbx.finish_remote_execution,
         (52, 1, "failed", event["error"], "cloudflare",
          "cf-mcp-abandoned")),
        (dbx.requeue_provider_fallback,
         (52, 1, "cloudflare", "cf-mcp-abandoned", "modal",
          event["error"])),
    ]


def test_only_proven_prelaunch_failure_falls_back_to_modal(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(
            remote.CloudflareLaunchUnavailable("preflight unavailable")))
    modal = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda _job, function_override=None: modal.append(_job["id"])
        or {"ok": True})
    assert remote._run_remote(job) == {"ok": True}
    assert modal == [42]


def test_confirmed_cloudflare_capacity_failure_falls_back_once_to_modal(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure("container out of capacity")
    failure.failure_kind = "executor_capacity"
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))

    class Probe:
        calls = []

        def run(self, fn, *args):
            self.calls.append((fn, args))
            if fn is dbx.get_job:
                return {"state": "running", "total_claims": 4}
            if fn is dbx.finish_remote_execution:
                return True
            if fn is dbx.get_remote_execution:
                return {"state": "failed", "total_claims": 4,
                        "provider": "cloudflare",
                        "call_id": remote._cloudflare_call_id(job)}
            raise AssertionError(fn)

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    modal = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda _job, function_override=None: modal.append(_job["id"])
        or {"ok": True})

    assert remote._run_remote(job) == {"ok": True}
    assert modal == [42]


def test_terminal_cloudflare_fallback_repairs_missing_ledger_before_modal(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure("container unavailable")
    failure.failure_kind = "transient_infrastructure"
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))
    events = []

    class Probe:
        def __init__(self):
            self.recorded = False

        def run(self, fn, *args):
            events.append(fn)
            if fn is dbx.get_job:
                return {"state": "running", "total_claims": 4}
            if fn is dbx.finish_remote_execution:
                return self.recorded
            if fn is dbx.get_remote_execution:
                if not self.recorded:
                    return None
                return {"state": "failed", "total_claims": 4,
                        "provider": "cloudflare",
                        "call_id": remote._cloudflare_call_id(job)}
            if fn is dbx.record_remote_execution:
                self.recorded = True
                return True
            raise AssertionError(fn)

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    modal = []
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda _job, function_override=None: modal.append(_job["id"])
        or {"ok": True})

    assert remote._run_remote(job) == {"ok": True}
    assert dbx.record_remote_execution in events
    assert modal == [42]


def test_terminal_cloudflare_fallback_stops_if_ledger_cannot_be_fenced(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure("container unavailable")
    failure.failure_kind = "transient_infrastructure"
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))

    class Probe:
        def run(self, fn, *_args):
            if fn is dbx.get_job:
                return {"state": "running", "total_claims": 4}
            if fn is dbx.finish_remote_execution:
                return False
            if fn is dbx.get_remote_execution:
                return {"state": "running", "total_claims": 4,
                        "provider": "cloudflare", "call_id": "other"}
            raise AssertionError(fn)

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("unfenced claim must not reach Modal")))

    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_remote(job)


def test_confirmed_deterministic_failure_does_not_buy_a_modal_rerun(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure("invalid EDL")
    failure.failure_kind = "invalid_edl"
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("deterministic failure must not rerun")))

    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_remote(job)


def test_nonretryable_unknown_failure_does_not_buy_a_modal_rerun(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"mcp_tool"}))
    job = dict(JOB, type="mcp_tool",
               payload={**JOB["payload"],
                        "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure(
        "project has not finished indexing")
    failure.failure_kind = "unknown"
    failure.retryable = False
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("nonretryable failure must not rerun")))

    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_remote(job)


def test_nonretryable_provider_budget_can_switch_to_modal(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"preview_check"}))
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    failure = remote.CloudflareTerminalFailure("provider budget exceeded")
    failure.failure_kind = "provider_budget_exhausted"
    failure.retryable = False
    monkeypatch.setattr(
        remote, "_run_cloudflare",
        lambda _job: (_ for _ in ()).throw(failure))

    class Probe:
        def run(self, fn, *_args):
            if fn is dbx.get_job:
                return {"state": "running", "total_claims": 4}
            if fn is dbx.finish_remote_execution:
                return True
            if fn is dbx.get_remote_execution:
                return {"state": "failed", "total_claims": 4,
                        "provider": "cloudflare",
                        "call_id": remote._cloudflare_call_id(job)}
            raise AssertionError(fn)

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    monkeypatch.setattr(remote, "_run_modal",
                        lambda *_a, **_k: {"ok": True})

    assert remote._run_remote(job) == {"ok": True}


def test_ambiguous_post_disconnect_recovers_same_call_not_modal(monkeypatch):
    _enable(monkeypatch)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})

    class Ledger:
        def run(self, _fn, *args, **kwargs):
            return True

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Ledger)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _job_id: None)
    monkeypatch.setattr(remote.requests, "get", lambda *a, **k: _Response({
        "status": "ok", "provider": "cloudflare"}))
    monkeypatch.setattr(
        remote.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(
            remote.requests.ConnectionError("response lost")))
    recovered = []
    monkeypatch.setattr(
        remote, "_recover_cloudflare_result",
        lambda call_id, lane, _job, deadline: recovered.append(
            (call_id, lane)) or {
                "result": {"ok": True}, "job_completed": True})
    monkeypatch.setattr(
        remote, "_run_modal",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("ambiguous launch must not switch providers")))

    result = remote._run_remote(job)
    assert result["ok"] is True
    assert recovered == [(remote._cloudflare_call_id(job), "interactive")]


def test_ambiguous_missing_status_never_authorizes_modal_fallback(monkeypatch):
    _enable(monkeypatch)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: {"status": "missing"})
    class Probe:
        def run(self, fn, *_args, **_kwargs):
            assert fn is dbx.get_job
            return {"state": "running"}

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.1, 0.2, 1.1])
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks, 1.1))

    with pytest.raises(remote.RemoteExecutorError) as caught:
        remote._recover_cloudflare_result(
            remote._cloudflare_call_id(job), "interactive", job, 1.0)

    assert not isinstance(caught.value, remote.CloudflareLaunchUnavailable)
    assert "could not be recovered" in str(caught.value)


def test_recovery_preserves_unknown_call_error_at_deadline(monkeypatch):
    _enable(monkeypatch)
    job = dict(JOB, payload={**JOB["payload"],
                             "execution_provider": "cloudflare"})
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "unknown",
        "error": "container connection closed before a response",
    })

    class Probe:
        def run(self, fn, *_args, **_kwargs):
            assert fn is dbx.get_job
            return {"state": "running"}

        def reset(self):
            pass

    monkeypatch.setattr(remote.dbx, "Db", Probe)
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.1, 1.1])
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks, 1.1))

    with pytest.raises(remote.RemoteExecutorError) as caught:
        remote._recover_cloudflare_result(
            remote._cloudflare_call_id(job), "interactive", job, 1.0)

    assert "container connection closed before a response" in str(caught.value)
    assert not str(caught.value).endswith(": None")


def test_cloudflare_config_preserves_modal_heavy_fallback():
    root = Path(__file__).resolve().parents[1]
    wrangler = (root / "cloudflare" / "wrangler.jsonc").read_text()
    adapter = (root / "cloudflare" / "src" / "index.ts").read_text()
    dockerfile = (root / "Dockerfile.cloudflare").read_text()
    assert '"instance_type": "standard-4"' in wrangler
    assert '"max_instances": 3' in wrangler
    assert '"WNAM"' in wrangler
    assert '"WHISPER_MODEL": ""' in wrangler
    assert '"WHISPER_MODEL": "medium"' in wrangler
    wrangler_config = json.loads(wrangler)
    assert all(container["image_vars"]["SOURCE_VERSION"]
               == "set-by-deploy-workflow"
               for container in wrangler_config["containers"])
    assert "interactive: 5, batch: 3, agent: 5, mcp: 12, shorts: 8" \
        in adapter
    assert "storage.transaction" in adapter
    assert "provider_call_id: callId" in adapter
    assert "provider_adapter_version: this.env.CODE_VERSION" in adapter
    assert "containerReadiness" in adapter
    assert "safe_to_fallback: true" in adapter
    assert 'url.pathname === "/probe"' in adapter
    assert 'body.code_version === expectedSource' in adapter
    assert 'body.role === this.workerRole' in adapter
    assert "const TERMINAL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000" \
        in adapter
    assert "pruneTerminalCalls" in adapter
    assert 'sleepAfter = "60s"' in adapter
    assert "override async onActivityExpired" in adapter
    assert 'storage.get<ActiveCall>("active")' in adapter
    assert "Idle timeout expired with no active provider lease" in adapter
    assert "await this.destroy()" in adapter
    assert "const STARTING_STALE_MS = 180 * 1000" in adapter
    assert "expireStaleStart" in adapter
    assert "expireExecutorLease" in adapter
    assert 'status: "stopping"' in adapter
    assert "exceeded its executor lease" in adapter
    assert 'kind: "transient_infrastructure"' in adapter
    assert "markRunning" in adapter
    assert "startup was abandoned before /run" in adapter
    assert "getByName(shardName" not in adapter  # computed once as `shard`
    assert "getByName(shard)" in adapter
    assert "Cloudflare Container shard is busy" in adapter
    assert "CLOUDFLARE_CONTAINER_PROFILE" in adapter
    assert 'protected readonly workerRole = "mcp_executor"' in adapter
    assert '"class_name": "ValmeraMcp"' in wrangler
    assert '"instance_type": "standard-1"' in wrangler
    assert "http_server.py" in dockerfile
    assert "ARG SOURCE_VERSION=unknown" in dockerfile
    assert "/opt/valmera-source-version" in dockerfile
    assert 'if [ -n "$WHISPER_MODEL" ]' in dockerfile
    assert "playwright install" not in dockerfile
    assert "pip install --no-cache-dir demucs" not in dockerfile.lower()
