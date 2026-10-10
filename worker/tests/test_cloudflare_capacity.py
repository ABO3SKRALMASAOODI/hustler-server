"""Cloudflare "no container instance" refusals: root cause and recovery.

Production, Aug 31 - Oct 10 2026: every windowed watch_video (37 of 37) failed
with "there is no container instance that can be provided to this durable
object", "shard is busy" or "throttling the container service". The media
child on the interactive lane ignored the resolved asset, re-resolved, and
launched another media child, recursively, until Cloudflare refused one.
These tests pin the fix (the child encodes locally) and the recovery policy
for a genuine capacity refusal: nothing ran, so spread, fail over, fall back,
and only then report an honest, retryable failure.
"""

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import mcp_media  # noqa: E402
import remote  # noqa: E402

MB = 1048576
NO_INSTANCE = ("Error: there is no container instance that can be provided "
               "to this durable object")


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class _Reached(Exception):
    pass


class _Ledger:
    events = []

    def run(self, fn, *args, **_kwargs):
        _Ledger.events.append((fn, args))
        if fn is dbx.get_job:
            return None
        return True

    def reset(self):
        pass


@pytest.fixture(autouse=True)
def _cloudflare(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_WAIT_S", 150.0)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_RETRIES", 4)
    monkeypatch.setattr(config, "WORKER_ROLE", "worker")
    _Ledger.events = []
    monkeypatch.setattr(remote.dbx, "Db", _Ledger)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _id: True)
    monkeypatch.setattr(remote.dbx, "remote_launch_recorded", lambda _id: None)
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned", lambda _id: None)
    remote._health_cache.clear()
    monkeypatch.setattr(remote.requests, "get", lambda *_a, **_k: _Response({
        "status": "ok", "provider": "cloudflare"}))


# ─────────────────────────── the root cause: recursion ────────────────────

class _Db:
    def __init__(self, asset):
        self.asset = asset

    def run(self, fn, *a):
        return self.asset


class _Ctx:
    project_id = 3419
    workdir = "/nonexistent"

    def __init__(self, job, **asset):
        self.job = job
        self.pending_images = []
        row = {"id": 41, "storage_key": "proxies/3419/source.mp4",
               "duration_s": 3314.8, "height": 480, "fps": 30.0,
               "bytes": 150 * MB, "kind": "proxy", "meta": {}}
        row.update(asset)
        self.db = _Db(row)

    def latest_edl(self):
        return {"version": 11, "json": {}}


WINDOW = {"kind": "source", "start": 1690, "end": 1770, "frames": False,
          "delivery": "url", "_inline_max_bytes": 0}
RESOLVED = {"storage_key": "proxies/3419/resolved.mp4", "bytes": 150 * MB,
            "duration_s": 3314.8, "height": 480, "fps": 30.0}


def _encode_here(monkeypatch):
    """Reaching the local encode is the success condition: no bucket here."""
    class _S:
        @staticmethod
        def exists(key):
            raise _Reached(key)

    monkeypatch.setattr(mcp_media, "storage", _S)
    monkeypatch.setattr(
        remote, "run_mcp_media_remote",
        lambda *a, **k: pytest.fail("a media executor re-dispatched MCP media"))


def test_cloudflare_media_child_encodes_its_resolved_asset_locally(
        monkeypatch):
    """Job 62750's child: Cloudflare interactive, id-less mcp_media."""
    monkeypatch.setattr(config, "WORKER_ROLE", "executor")
    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    _encode_here(monkeypatch)
    monkeypatch.setattr(
        mcp_media, "_source_object",
        lambda _ctx: pytest.fail("the child must not re-resolve"))
    child = {"id": None, "type": "mcp_media", "project_id": 3419}
    with pytest.raises(_Reached):
        mcp_media.prepare(_Ctx(child), dict(
            WINDOW, _resolved_asset=RESOLVED,
            _resolved_what="the source"), 0)


def test_queued_media_tool_on_an_executor_lane_resolves_and_encodes_here(
        monkeypatch):
    """A watch_video that failed over from the MCP lane runs on the media
    lane itself: it must neither trust caller-supplied _resolved_* fields nor
    send the encode anywhere else."""
    monkeypatch.setattr(config, "WORKER_ROLE", "executor")
    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    _encode_here(monkeypatch)
    resolved_by_itself = []
    monkeypatch.setattr(
        mcp_media, "_source_object",
        lambda ctx: resolved_by_itself.append(1) or (
            {"storage_key": "proxies/3419/own.mp4", "bytes": 150 * MB,
             "duration_s": 3314.8, "height": 480, "fps": 30.0}, "the source"))
    queued = {"id": 62751, "type": "mcp_tool", "project_id": 3419,
              "total_claims": 1}
    forged = dict(RESOLVED, storage_key="clips/another-user/private.mp4")
    with pytest.raises(_Reached):
        mcp_media.prepare(_Ctx(queued), dict(WINDOW, _resolved_asset=forged),
                          0)
    assert resolved_by_itself == [1]


def test_orchestrator_sends_one_child_with_only_its_own_resolution(
        monkeypatch):
    monkeypatch.setattr(config, "WORKER_ROLE", "mcp_executor")
    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    monkeypatch.setattr(
        mcp_media, "_source_object",
        lambda ctx: ({"storage_key": "proxies/3419/own.mp4",
                      "bytes": 150 * MB, "duration_s": 3314.8,
                      "height": 480, "fps": 30.0}, "the source"))
    monkeypatch.setattr(remote, "mcp_media_available", lambda: True)
    sent = []
    monkeypatch.setattr(
        remote, "run_mcp_media_remote",
        lambda project_id, payload, user_id=None: sent.append(payload)
        or {"text": "watched"})
    queued = {"id": 62751, "type": "mcp_tool", "project_id": 3419,
              "user_id": 9}
    out = mcp_media.prepare(_Ctx(queued), dict(
        WINDOW, _resolved_asset={"storage_key": "clips/other/private.mp4"},
        _resolved_preview={"audio_model_review": False, "forged": True},
        _resolved_what="forged"), 0)
    assert out == {"text": "watched"}
    args = sent[0]["args"]
    assert args["_resolved_asset"]["storage_key"] == "proxies/3419/own.mp4"
    assert args["_resolved_what"] == "the source"
    assert "_resolved_preview" not in args          # kind=source has none


@pytest.mark.parametrize("role", ["executor", "batch_executor"])
def test_media_executor_refuses_to_redispatch_mcp_media(monkeypatch, role):
    monkeypatch.setattr(config, "WORKER_ROLE", role)
    monkeypatch.setattr(
        remote, "_run_remote",
        lambda job: pytest.fail("recursion reached the provider"))
    with pytest.raises(remote.RemoteExecutorError, match="re-dispatch"):
        remote.run_mcp_media_remote(3419, {"tool": "__media__", "args": {}})


def test_modal_child_contract_still_needs_an_id_less_job(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    assert mcp_media.trusted_media_child(_Ctx({"id": None}))
    # A queued job on Modal carries the MCP client's raw arguments.
    assert not mcp_media.trusted_media_child(_Ctx({"id": 5}))


# ─────────────────────────── refusal classification ───────────────────────

MEDIA_JOB = {"id": None, "type": "mcp_media", "project_id": 3419,
             "user_id": 9, "attempts": 0,
             "payload": {"tool": "__media__", "args": {}}}
QUEUED_MCP = {"id": 62751, "type": "mcp_tool", "project_id": 3419,
              "user_id": 9, "attempts": 1, "total_claims": 1,
              "payload": {"tool": "get_edl", "mutation": False,
                          "execution_provider": "cloudflare"}}


@pytest.mark.parametrize("body", [
    {"error": NO_INSTANCE, "safe_to_fallback": True,
     "capacity_unavailable": True},
    # The adapter deployed before this change sends only the message.
    {"error": NO_INSTANCE, "safe_to_fallback": True},
    {"error": "Error: Your application is throttling the container service; "
              "reduce concurrent requests and try again later",
     "safe_to_fallback": True},
    {"error": "Error: you are requesting too many containers per second",
     "safe_to_fallback": True},
])
def test_no_instance_refusal_is_proven_unlaunched_capacity(monkeypatch, body):
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: _Response(body, 503))
    with pytest.raises(remote.CloudflareCapacityUnavailable) as caught:
        remote._run_cloudflare(dict(QUEUED_MCP))
    assert isinstance(caught.value, remote.CloudflareLaunchUnavailable)
    assert not isinstance(caught.value, remote.CloudflareCapacityBusy)
    assert any(fn is dbx.finish_remote_execution and args[2] == "cancelled"
               for fn, args in _Ledger.events)


def test_reconnect_reads_the_refusal_instead_of_waiting_out_the_lease(
        monkeypatch):
    """The launch request timed out while the runtime searched for an
    instance; the Durable Object then recorded its refusal."""
    launches = []

    def post(*_a, **kwargs):
        launches.append(kwargs["json"]["launch_id"])
        raise requests.ReadTimeout("observation window ended")

    statuses = []
    monkeypatch.setattr(remote.requests, "post", post)
    monkeypatch.setattr(
        remote, "_cloudflare_status",
        lambda call_id, lane, timeout=10: statuses.append((call_id, lane))
        or {"status": "refused", "launchId": launches[-1], "envelope": {
            "error": NO_INSTANCE, "safe_to_fallback": True,
            "capacity_unavailable": True}})
    monkeypatch.setattr(remote.time, "sleep",
                        lambda _s: pytest.fail("a refusal is not worth a poll"))
    with pytest.raises(remote.CloudflareCapacityUnavailable):
        remote._run_cloudflare(dict(QUEUED_MCP))
    assert len(statuses) == 1
    assert any(fn is dbx.finish_remote_execution and args[2] == "cancelled"
               for fn, args in _Ledger.events)


def test_reconnected_rollout_refusal_keeps_its_rollout_wait(monkeypatch):
    launches = []

    def post(*_a, **kwargs):
        launches.append(kwargs["json"]["launch_id"])
        raise requests.ConnectionError("reset")

    monkeypatch.setattr(remote.requests, "post", post)
    monkeypatch.setattr(
        remote, "_cloudflare_status",
        lambda *_a, **_k: {"status": "refused", "launchId": launches[-1],
                           "envelope": {
            "error": "Error: Container sidecar is shutting down",
            "safe_to_fallback": True}})
    with pytest.raises(remote.CloudflareRolloutPending):
        remote._run_cloudflare(dict(QUEUED_MCP))


def test_an_older_refusal_of_the_same_id_is_not_proof(monkeypatch):
    """A rollout wait relaunches the same call id. If that relaunch's
    response is lost, the previous launch's refusal must not be read as this
    one's: the new request may still start the call."""
    clock = {"now": 0.0}
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])

    def sleep(seconds):
        clock["now"] += max(seconds, 1.0)

    monkeypatch.setattr(remote.time, "sleep", sleep)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TIMEOUTS",
                        {**config.CLOUDFLARE_EXECUTOR_TIMEOUTS,
                         "mcp_tool": 60})
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({"frames"}))
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: (
        _ for _ in ()).throw(requests.ReadTimeout("lost")))
    reads = []
    monkeypatch.setattr(
        remote, "_cloudflare_status",
        lambda *_a, **_k: reads.append(1) or {
            "status": "refused", "launchId": "an-earlier-launch",
            "envelope": {"error": NO_INSTANCE, "safe_to_fallback": True,
                         "capacity_unavailable": True}})
    with pytest.raises(remote.RemoteExecutorError,
                       match="could not be recovered") as caught:
        remote._run_cloudflare(dict(QUEUED_MCP))
    assert not isinstance(caught.value, remote.CloudflareLaunchUnavailable)
    assert len(reads) > 2


# ─────────────────────────── spread, fail over, report ────────────────────

def _route(job):
    return remote._cloudflare_lane_for(job), remote._cloudflare_call_id(job)


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(remote.time, "sleep", lambda s: slept.append(s))
    return slept


def test_media_child_spreads_then_fails_over_to_batch(monkeypatch, sleeps):
    seen = []

    def run(job):
        seen.append(_route(job))
        if len(seen) < 3:
            raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)
        return {"text": "watched"}

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = dict(MEDIA_JOB)
    assert remote._run_cloudflare_with_capacity_wait(job) == {
        "text": "watched"}
    lanes = [lane for lane, _id in seen]
    assert lanes == ["interactive", "interactive", "batch"]
    assert len({call_id for _lane, call_id in seen}) == 3
    assert seen[1][1].startswith("cf-alt1-mcp_media-p3419-")
    assert len(sleeps) == 2 and sleeps[0] <= 1.0 and sleeps[1] <= 3.0


def test_pinned_mcp_shard_spreads_on_its_own_lane_only(monkeypatch, sleeps):
    seen = []

    def run(job):
        seen.append(_route(job))
        if len(seen) < 3:
            raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"]))
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert [lane for lane, _id in seen] == ["mcp", "mcp", "mcp"]
    assert seen[0][1].startswith("cf-mcp-p3419-")
    assert seen[1][1].startswith("cf-alt1-mcp_tool-p3419-")
    assert seen[2][1].startswith("cf-alt2-mcp_tool-p3419-")
    assert any(fn is dbx.lease_is_current for fn, _a in _Ledger.events)


def test_watch_video_job_fails_over_from_mcp_to_the_media_lanes(
        monkeypatch, sleeps):
    seen = []

    def run(job):
        seen.append(_route(job))
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_RETRIES", 5)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_WAIT_S", 600.0)
    job = dict(QUEUED_MCP, payload={"tool": "__media__", "mutation": False})
    with pytest.raises(remote.CloudflareCapacityUnavailable):
        remote._run_cloudflare_with_capacity_wait(job)
    assert [lane for lane, _id in seen] == [
        "mcp", "mcp", "interactive", "mcp", "batch", "mcp"]
    # Distinct Durable Objects first; only then, after backoff, the pinned
    # shard is tried again.
    assert len(set(seen[:5])) == 5
    assert seen[5] == seen[0]


def test_preview_fails_over_to_batch_and_records_that_lane(monkeypatch,
                                                           sleeps):
    seen = []

    def run(job):
        seen.append(_route(job))
        if len(seen) == 1:
            raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)
        if len(seen) == 2:
            raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = {"id": 900, "type": "preview", "project_id": 5, "user_id": 1,
           "attempts": 1, "total_claims": 2, "payload": {}}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert [lane for lane, _id in seen] == [
        "interactive", "interactive", "batch"]


def test_every_route_refused_is_an_honest_retryable_outcome(monkeypatch,
                                                            sleeps):
    calls = []

    def run(job):
        calls.append(_route(job))
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"]))
    with pytest.raises(remote.CloudflareCapacityUnavailable) as caught:
        remote._run_cloudflare_with_capacity_wait(job)
    assert len(calls) == 1 + config.CLOUDFLARE_CAPACITY_RETRIES
    error = caught.value
    text = str(error)
    assert "could not provide a container for this mcp_tool" in text
    assert "Nothing ran and nothing changed" in text
    assert "safe to retry" in text
    assert "there is no container instance" in text   # operator category
    decision = failure_policy.decision_for(error, "mcp_tool")
    assert decision.kind == "provider_capacity_unavailable"
    assert decision.retryable is True
    # MCP's queue never claims a job twice; the caller retries.
    assert decision.max_attempts == 1


def test_capacity_retries_stop_at_the_wait_budget(monkeypatch, sleeps):
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_WAIT_S", 0.0)
    calls = []

    def run(job):
        calls.append(1)
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    with pytest.raises(remote.CloudflareCapacityUnavailable,
                       match="could not provide a container"):
        remote._run_cloudflare_with_capacity_wait(dict(MEDIA_JOB))
    assert calls == [1] and sleeps == []


def test_a_lost_lease_ends_the_capacity_wait(monkeypatch, sleeps):
    class LostLease(_Ledger):
        def run(self, fn, *args, **kwargs):
            if fn is dbx.lease_is_current:
                return False
            return super().run(fn, *args, **kwargs)

    monkeypatch.setattr(remote.dbx, "Db", LostLease)
    calls = []

    def run(job):
        calls.append(1)
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    with pytest.raises(dbx.JobLeaseLost):
        remote._run_cloudflare_with_capacity_wait(
            dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"])))
    assert calls == [1]


def test_start_abandoned_before_run_retries_on_a_new_identity(monkeypatch,
                                                              sleeps):
    unmarked, seen = [], []
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned",
                        lambda job_id: unmarked.append(job_id))

    def run(job):
        seen.append(_route(job))
        if len(seen) == 1:
            error = remote.CloudflareTerminalFailure(
                "Cloudflare container startup was abandoned before /run")
            error.failure_kind = "provider_start_abandoned"
            raise error
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"]))
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert seen[0][1] != seen[1][1]
    assert unmarked == [QUEUED_MCP["id"]]


@pytest.mark.parametrize("kind", ["transient_infrastructure", "unknown",
                                  "outcome_unknown"])
def test_terminal_failures_after_run_are_never_replayed_here(monkeypatch,
                                                             kind):
    calls = []

    def run(_job):
        calls.append(1)
        error = remote.CloudflareTerminalFailure("ffmpeg failed")
        error.failure_kind = kind
        raise error

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_cloudflare_with_capacity_wait(dict(QUEUED_MCP))
    assert calls == [1]


def test_fenced_fallback_runs_only_after_cloudflare_routes_are_exhausted(
        monkeypatch, sleeps):
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"mcp_tool"}))
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES",
                        frozenset({"mcp_tool"}))
    attempts, modal = [], []

    def run(_job):
        attempts.append(1)
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    monkeypatch.setattr(remote, "_run_modal",
                        lambda job, function_override=None: modal.append(
                            job["id"]) or {"ok": "modal"})
    job = dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"]))
    assert remote._run_remote(job) == {"ok": "modal"}
    assert len(attempts) == 1 + config.CLOUDFLARE_CAPACITY_RETRIES
    assert modal == [QUEUED_MCP["id"]]


def test_mcp_media_child_uses_the_fenced_fallback_where_enabled(monkeypatch,
                                                                sleeps):
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({"mcp_media"}))
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES",
                        frozenset({"mcp_media"}))
    monkeypatch.setattr(remote, "_run_cloudflare", lambda _job: (
        _ for _ in ()).throw(remote.CloudflareCapacityUnavailable(
            NO_INSTANCE)))
    modal = []
    monkeypatch.setattr(remote, "_run_modal",
                        lambda job, function_override=None: modal.append(
                            (job["type"], function_override)) or {"ok": 1})
    with pytest.raises(remote.CloudflareCapacityUnavailable):
        remote.run_mcp_media_remote(3419, {"tool": "__media__", "args": {}})
    assert modal == []
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    assert remote.run_mcp_media_remote(
        3419, {"tool": "__media__", "args": {}}) == {"ok": 1}
    assert modal == [("mcp_tool", "preview")]


# ─────────────────────────── policy and queue ─────────────────────────────

@pytest.mark.parametrize("text", [
    NO_INSTANCE,
    "Error: Your application is throttling the container service; reduce "
    "concurrent requests and try again later",
    "Cloudflare could not provide a container for this mcp_media: 5 launch "
    "attempt(s) ... Nothing ran and nothing changed; it is safe to retry",
])
@pytest.mark.parametrize("job_type,attempts", [
    ("mcp_tool", 1), ("preview", 2), ("final", 2)])
def test_capacity_text_is_classified_retryable_with_bounded_attempts(
        text, job_type, attempts):
    decision = failure_policy.classify(RuntimeError(text), job_type)
    assert decision.kind == "provider_capacity_unavailable"
    assert decision.retryable is True
    assert decision.max_attempts == min(attempts,
                                        config.MAX_ATTEMPTS_MEDIA
                                        if job_type != "mcp_tool" else 1)
    assert decision.agent_repairable is False


class _QueueDb:
    def __init__(self, deferred):
        self.deferred, self.calls = deferred, []

    def run(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        if fn is dbx.defer_unlaunched_cloudflare_busy:
            return self.deferred
        if fn in (dbx.requeue_job, dbx.finish_job):
            return True
        return None


def _process(monkeypatch, job_type, deferred):
    import main
    monkeypatch.setattr(main.remote, "stamp_execution_provider",
                        lambda _db, _job: "cloudflare")

    def runner(_db, job):
        raise remote._capacity_exhausted(job, {
            "started": remote.time.monotonic() - 150, "attempts": 5,
            "routes": [("interactive", 0), ("batch", 0)],
            "last": remote.CloudflareCapacityUnavailable(NO_INSTANCE)})

    monkeypatch.setattr(main, "_runner_for_job",
                        lambda _job: (runner, "test"))
    monkeypatch.setattr(main, "_notify_failure", lambda *_a: None)
    db = _QueueDb(deferred)
    main.process_one(db, {"id": 42, "type": job_type, "project_id": 7,
                          "user_id": 60, "attempts": 1, "total_claims": 3,
                          "payload": {}})
    return db.calls


@pytest.mark.parametrize("job_type", ["preview", "final", "index"])
def test_exhausted_capacity_returns_media_to_the_queue_unspent(monkeypatch,
                                                               job_type):
    calls = _process(monkeypatch, job_type, deferred=True)
    fns = [fn for fn, _a, _k in calls]
    assert dbx.requeue_job not in fns and dbx.finish_job not in fns
    _fn, args, kwargs = next(c for c in calls
                             if c[0] is dbx.defer_unlaunched_cloudflare_busy)
    assert args[:2] == (42, 3)
    assert args[3] == config.CLOUDFLARE_CAPACITY_MAX_DEFERRALS
    assert kwargs == {"counter": "cloudflare_capacity_deferrals"}


@pytest.mark.parametrize("job_type", ["mcp_tool", "agent_turn"])
def test_exhausted_capacity_goes_straight_to_live_callers(monkeypatch,
                                                          job_type):
    calls = _process(monkeypatch, job_type, deferred=True)
    fns = [fn for fn, _a, _k in calls]
    assert dbx.defer_unlaunched_cloudflare_busy not in fns
    assert dbx.finish_job in fns and dbx.requeue_job not in fns
    _fn, args, _k = next(c for c in calls if c[0] is dbx.finish_job)
    failure = args[3]["failure"]
    assert failure["kind"] == "provider_capacity_unavailable"
    assert failure["retryable"] is True


def test_capacity_deferral_counter_is_known():
    assert "cloudflare_capacity_deferrals" in dbx._DEFERRAL_COUNTERS


def test_capacity_routes_cover_every_lane_without_repeating():
    media = {"type": "mcp_tool", "payload": {"tool": "__media__"}}
    routes = [remote._capacity_route(media, retry) for retry in range(1, 7)]
    assert routes == [("mcp", 1), ("interactive", 0), ("mcp", 2),
                      ("batch", 0), ("mcp", 0), ("interactive", 1)]
    preview = {"type": "preview"}
    assert [remote._capacity_route(preview, r) for r in range(1, 5)] == [
        ("interactive", 1), ("batch", 0), ("interactive", 2), ("batch", 1)]
    # Batch-only work never moves to the smaller interactive image.
    for job_type in ("index", "final", "fetch", "stems", "matte"):
        assert remote._cloudflare_failover_lanes({"type": job_type}) == ()


class _Clock:
    """Each launch takes 100 s of (fake) wall clock."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.mark.parametrize("queued,expected_attempts", [(False, 2), (True, 5)])
def test_child_capacity_retries_stay_inside_the_parent_lease_margin(
        monkeypatch, sleeps, queued, expected_attempts):
    clock = _Clock()
    monkeypatch.setattr(remote.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_WAIT_S", 600.0)
    attempts = []

    def run(job):
        attempts.append(1)
        clock.now += 100
        raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = dict(QUEUED_MCP, payload=dict(QUEUED_MCP["payload"])) if queued \
        else dict(MEDIA_JOB)
    with pytest.raises(remote.CloudflareCapacityUnavailable):
        remote._run_cloudflare_with_capacity_wait(job)
    # A child never starts a launch past CLOUDFLARE_MCP_CHILD_MARGIN_S - 60
    # (its parent's lease covers child lease + 60 s + that margin); a queued
    # job is bounded by the retry count first.
    assert len(attempts) == expected_attempts


def test_every_lane_can_hold_one_container_per_shard():
    """Shard names are the only Durable Objects customers reach, so a lane's
    max_instances must cover its shard count; the extra deployment-probe
    Durable Object destroys its container right after each probe."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "cloudflare"
    wrangler = json.loads((root / "wrangler.jsonc").read_text())
    adapter = (root / "src" / "index.ts").read_text()
    shards = dict(re.findall(r"(\w+): (\d+)", re.search(
        r"const SHARD_COUNTS = \{([^}]*)\}", adapter).group(1)))
    classes = {"interactive": "ValmeraInteractive", "batch": "ValmeraBatch",
               "agent": "ValmeraAgent", "mcp": "ValmeraMcp",
               "shorts": "ValmeraShorts"}
    limits = {c["class_name"]: c["max_instances"]
              for c in wrangler["containers"]}
    for lane, count in shards.items():
        assert limits[classes[lane]] >= int(count), lane
    probe = adapter[adapter.index('url.pathname === "/probe"'):]
    probe = probe[:probe.index("statusMatch")]
    assert "this.destroy()" in probe


def test_a_busy_alternate_slot_is_not_retried_as_its_own_spread(monkeypatch,
                                                                sleeps):
    seen = []

    def run(job):
        seen.append(_route(job))
        if len(seen) == 1:
            raise remote.CloudflareCapacityBusy("Cloudflare Container shard is busy")
        if len(seen) == 2:
            raise remote.CloudflareCapacityUnavailable(NO_INSTANCE)
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = {"id": 900, "type": "preview", "project_id": 5, "user_id": 1,
           "attempts": 1, "total_claims": 2, "payload": {}}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert seen[1][1].startswith("cf-alt1-preview-p5-")
    assert seen[2] != seen[1]
    assert seen[2][0] == "batch"


# ─────────────────────────── review follow-ups ────────────────────────────

def test_a_nested_childs_capacity_failure_never_replays_its_parent(
        monkeypatch):
    """A parent MCP tool or Studio turn that already ran reports its child's
    exhausted capacity as a terminal envelope. The parent may have changed
    the project before the child call, so it must never run again on the
    fenced fallback provider."""
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES",
                        frozenset({"mcp_tool", "agent_turn"}))
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES",
                        frozenset({"mcp_tool", "agent_turn"}))
    monkeypatch.setattr(remote, "_run_modal", lambda *_a, **_k: pytest.fail(
        "a parent that already ran was replayed on Modal"))
    child_error = remote._capacity_exhausted(dict(MEDIA_JOB), {
        "started": remote.time.monotonic() - 150, "attempts": 5,
        "routes": [("interactive", 0), ("batch", 0)],
        "last": remote.CloudflareCapacityUnavailable(NO_INSTANCE)})
    # The executor classifies the parent's failure from its text.
    decision = failure_policy.classify(child_error, "mcp_tool")
    envelope = {"error": str(child_error), "retryable": decision.retryable,
                "failure": decision.payload(child_error),
                "timings": {"total_s": 140.0}}
    assert envelope["failure"]["kind"] == "provider_capacity_unavailable"
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: _Response(envelope))
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    job = dict(QUEUED_MCP, payload={"tool": "add_stock_media",
                                    "mutation": True,
                                    "execution_provider": "cloudflare"})

    class _Lease(_Ledger):
        """The lease is still ours and the terminal identity is closed:
        everything a provider switch checks before it may replay."""

        def run(self, fn, *args, **kwargs):
            _Ledger.events.append((fn, args))
            if fn is dbx.get_job:
                return {"id": job["id"], "state": "running",
                        "total_claims": job["total_claims"]}
            return True

    monkeypatch.setattr(remote.dbx, "Db", _Lease)
    with pytest.raises(remote.CloudflareTerminalFailure) as caught:
        remote._run_remote(job)
    assert caught.value.failure_kind == "provider_capacity_unavailable"
    # A startup Cloudflare abandoned before /run still reaches the fallback.
    abandoned = {"error": "Cloudflare container startup was abandoned "
                 "before /run", "retryable": True,
                 "failure": {"kind": "provider_start_abandoned",
                             "retryable": True}}
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: _Response(abandoned))
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_RETRIES", 0)
    replayed = []
    monkeypatch.setattr(remote, "_run_modal", lambda j, *_a, **_k:
                        replayed.append(j["id"]) or {"ok": "modal"})
    assert remote._run_remote(dict(job)) == {"ok": "modal"}
    assert replayed == [job["id"]]


class _GuardianDb:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args):
        self.calls.append(fn)
        if fn in (dbx.finish_remote_execution, dbx.finish_job):
            return True
        return None


def _refused_row(submitted_at):
    return {"provider": "cloudflare", "call_id": "cf-mcp-p3419-de27a999983124a9b36f",
            "function_name": "mcp", "job_id": 62751, "total_claims": 1,
            "type": "mcp_tool", "project_id": 3419, "user_id": 9,
            "attempts": 1, "payload": {"tool": "__media__",
                                       "mutation": False},
            "submitted_at": submitted_at}


@pytest.mark.parametrize("submitted_ago,refused_ago,acts", [
    # An attached dispatcher reads its refusal within a 2-s poll and closes
    # the row itself; the guardian waits out the attach grace first.
    (60, 5, False),
    # Still open long after the refusal: the dispatcher is gone.
    (300, 200, True),
    # A refusal older than this row's launch belongs to an earlier launch
    # of the same identity; the current one may still start.
    (100, 200, False),
])
def test_guardian_acts_on_a_refusal_only_once_it_is_an_orphan(
        monkeypatch, submitted_ago, refused_ago, acts):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "refused",
        "updatedAt": (now - timedelta(seconds=refused_ago)).isoformat()
        .replace("+00:00", "Z"),
        "envelope": {"error": NO_INSTANCE, "safe_to_fallback": True,
                     "capacity_unavailable": True,
                     "failure": {"kind": "provider_capacity_unavailable",
                                 "retryable": True, "max_attempts": 1}}})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(
        db, _refused_row(now - timedelta(seconds=submitted_ago)))
    if acts:
        assert event["status"] == "failed"
        assert dbx.finish_remote_execution in db.calls
    else:
        assert event["status"] == "unknown"
        assert db.calls == []


def test_reconnect_reads_a_readiness_refusal_as_a_rollout(monkeypatch):
    launches = []

    def post(*_a, **kwargs):
        launches.append(kwargs["json"]["launch_id"])
        raise requests.ReadTimeout("observation window ended")

    monkeypatch.setattr(remote.requests, "post", post)
    monkeypatch.setattr(
        remote, "_cloudflare_status",
        lambda *_a, **_k: {"status": "refused", "launchId": launches[-1],
                           "envelope": {
            "error": "container readiness mismatch role=executor "
                     "source=old expected_role=executor expected_source=new",
            "safe_to_fallback": True}})
    with pytest.raises(remote.CloudflareRolloutPending):
        remote._run_cloudflare(dict(QUEUED_MCP))
