"""Cloudflare canary routing must be fast, fenced, and safely reversible."""

import json
import os
from pathlib import Path
import sys

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


def test_provider_choice_is_stamped_once_under_the_queue_lease(monkeypatch):
    _enable(monkeypatch)
    calls = []

    class WorkerDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is dbx.project_execution_shape:
                return {"total_bytes": 10, "max_duration_s": 20}
            if fn is dbx.stamp_execution_provider:
                return args[2]
            raise AssertionError(fn)

    job = {key: value for key, value in JOB.items()
           if key != "_execution_shape"}
    provider = remote.stamp_execution_provider(WorkerDb(), job)
    assert provider == "cloudflare"
    assert job["payload"]["execution_provider"] == "cloudflare"
    assert calls[-1][1] == (
        42, 4, "cloudflare", {"total_bytes": 10, "max_duration_s": 20})
    assert job["payload"]["execution_shape"]["total_bytes"] == 10


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
    assert posted[0][1]["json"]["job"]["dispatch_submitted_at"] > 0


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


def test_cloudflare_config_preserves_modal_heavy_fallback():
    root = Path(__file__).resolve().parents[1]
    wrangler = (root / "cloudflare" / "wrangler.jsonc").read_text()
    adapter = (root / "cloudflare" / "src" / "index.ts").read_text()
    dockerfile = (root / "Dockerfile.cloudflare").read_text()
    assert '"instance_type": "standard-4"' in wrangler
    assert '"max_instances": 3' in wrangler
    assert '"WNAM"' in wrangler
    assert '"image_vars": {"WHISPER_MODEL": ""}' in wrangler
    assert '"image_vars": {"WHISPER_MODEL": "medium"}' in wrangler
    assert "const SHARD_COUNTS = { interactive: 5, batch: 3 }" in adapter
    assert "storage.transaction" in adapter
    assert "provider_call_id: callId" in adapter
    assert "provider_adapter_version: this.env.CODE_VERSION" in adapter
    assert "const TERMINAL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000" \
        in adapter
    assert "pruneTerminalCalls" in adapter
    assert 'sleepAfter = "60s"' in adapter
    assert "getByName(shardName" not in adapter  # computed once as `shard`
    assert "getByName(shard)" in adapter
    assert "Cloudflare Container shard is busy" in adapter
    assert "CLOUDFLARE_CONTAINER_PROFILE" in adapter
    assert "http_server.py" in dockerfile
    assert 'if [ -n "$WHISPER_MODEL" ]' in dockerfile
    assert "playwright install" not in dockerfile
    assert "pip install --no-cache-dir demucs" not in dockerfile.lower()
