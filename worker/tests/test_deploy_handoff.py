"""Deploy handoffs never fail, or wrongly serve, a user's job (Oct 2026).

Release 6d6e5b83 (16 Oct, 18:52-20:18Z): 956 jobs, 4 failed, all MCP calls:
  * 73272 (get_kept_transcript) and 73277 (restore_range): "dispatcher
    shutdown began before Cloudflare submission" while Render replaced the
    dispatcher. Each claim was between launches (a capacity refusal) when
    SIGTERM arrived; its next launch was refused by the drain, and that
    refusal was recorded as the job's one failure.
  * 73449 (set_picture_card) and 74108 (add_motion_graphic): Cloudflare
    "Error: The container is not running, consider calling start()" while the
    executors rolled out. The Worker answered before any /run with
    safe_to_fallback; the dispatcher had no class for it and failed the job.

These tests replay those shapes against the real dispatcher code and pin
every other handoff path found in the review: late refusals during a drain,
orphaned refusals the new dispatcher's guardian finds, a container lost as
/run is sent, a read-only call whose container Cloudflare lost, and a job
type the dispatcher's release introduced reaching an older Worker.
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
import main as worker_main  # noqa: E402
import remote  # noqa: E402

# The real ownership functions: the autouse fixture stubs them for the
# launch-path tests, and the shutdown tests put them back.
_REAL_OWNERSHIP = {name: getattr(dbx, name) for name in (
    "mark_remote_owned", "unmark_remote_owned", "remote_launch_recorded")}

NOT_RUNNING = "Error: The container is not running, consider calling start()"
NO_INSTANCE = ("Error: there is no container instance that can be provided "
               "to this durable object")
# What the Worker answers for a startup that lost its container
# (cloudflare/src/index.ts startupRefusal); remote_executions for 73449.
STARTUP_REFUSAL = {
    "error": NOT_RUNNING, "safe_to_fallback": True, "retryable": True,
    "failure": {"kind": "provider_start_abandoned", "retryable": True,
                "max_attempts": 1, "agent_repairable": False}}


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class _Ledger:
    events = []

    def run(self, fn, *args, **kwargs):
        _Ledger.events.append((fn, args, kwargs))
        if fn is dbx.get_job:
            return None
        return True

    def reset(self):
        pass


def _job(job_id, tool, mutation, project):
    return {"id": job_id, "type": "mcp_tool", "project_id": project,
            "user_id": 60, "attempts": 1, "total_claims": 1,
            "payload": {"tool": tool, "args": {}, "mutation": mutation,
                        "execution_provider": "cloudflare"}}


@pytest.fixture(autouse=True)
def _cloudflare(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "WORKER_ROLE", "worker")
    _Ledger.events = []
    monkeypatch.setattr(remote.dbx, "Db", _Ledger)
    monkeypatch.setattr(remote.dbx, "remote_launch_recorded", lambda _id: None)
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _id: True)
    remote._health_cache.clear()
    monkeypatch.setattr(remote.requests, "get", lambda *_a, **_k: _Response({
        "status": "ok", "provider": "cloudflare",
        "source_version": "same", "features": ["skew_guard", "fence"]}))
    monkeypatch.setattr(remote.version, "code_version", lambda: "same")
    yield
    remote._health_cache.clear()


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(remote.time, "sleep", lambda s: slept.append(s))
    return slept


# ───────────────────── dispatcher swap: Render replaces the dispatcher ─────

@pytest.fixture
def ownership(monkeypatch):
    """The real in-process ownership sets, restored afterwards."""
    for name, fn in _REAL_OWNERSHIP.items():
        monkeypatch.setattr(dbx, name, fn)
    saved = (set(dbx.ACTIVE_JOBS), set(dbx.REMOTE_OWNED_JOBS),
             set(dbx.REMOTE_LAUNCHING_JOBS), dict(dbx.REMOTE_DISPATCH_CLAIMS),
             set(dbx.SETTLING_JOBS), dbx._REMOTE_LAUNCHES_STOPPED)
    for container in (dbx.ACTIVE_JOBS, dbx.REMOTE_OWNED_JOBS,
                      dbx.REMOTE_LAUNCHING_JOBS, dbx.REMOTE_DISPATCH_CLAIMS,
                      dbx.SETTLING_JOBS):
        container.clear()
    dbx._REMOTE_LAUNCHES_STOPPED = False
    yield
    (active, owned, launching, dispatch, settling, stopped) = saved
    for container, value in ((dbx.ACTIVE_JOBS, active),
                             (dbx.REMOTE_OWNED_JOBS, owned),
                             (dbx.REMOTE_LAUNCHING_JOBS, launching),
                             (dbx.SETTLING_JOBS, settling)):
        container.clear()
        container.update(value)
    dbx.REMOTE_DISPATCH_CLAIMS.clear()
    dbx.REMOTE_DISPATCH_CLAIMS.update(dispatch)
    dbx._REMOTE_LAUNCHES_STOPPED = stopped


def test_a_dispatched_claim_is_unlaunched_only_while_no_call_is_in_flight(
        ownership):
    dbx.track_job(73272)
    dbx.mark_remote_dispatch(73272, 1)
    assert dbx.unlaunched_dispatch_claims() == {73272: 1}
    assert dbx.mark_remote_owned(73272) is True
    assert dbx.unlaunched_dispatch_claims() == {}
    # A provider proved its launch was refused before /run.
    dbx.unmark_remote_owned(73272)
    assert dbx.unlaunched_dispatch_claims() == {73272: 1}
    ids, remote_ids = dbx.begin_remote_shutdown()
    assert ids == [73272] and remote_ids == []
    # Launches are stopped: this claim can never start one in this process.
    assert dbx.mark_remote_owned(73272) is False
    assert dbx.unlaunched_dispatch_claims() == {73272: 1}
    dbx.untrack_job(73272)
    assert dbx.unlaunched_dispatch_claims() == {}


def test_replay_73272_capacity_refusals_then_shutdown_returns_the_claim(
        monkeypatch, ownership, sleeps):
    """get_kept_transcript, 18:55:12Z: refused twice for capacity, then the
    drain refused its relaunch. It must go back to the queue unspent."""
    job = _job(73272, "get_kept_transcript", False, 3774)
    launches = []

    def post(*_a, **kwargs):
        launches.append(kwargs["json"]["job"]["id"])
        if len(launches) == 2:
            # Render's SIGTERM lands while the second refusal comes back.
            dbx.begin_remote_shutdown()
        return _Response({"error": NO_INSTANCE, "safe_to_fallback": True,
                          "capacity_unavailable": True}, 503)

    monkeypatch.setattr(remote.requests, "post", post)
    calls = []

    class WorkerDb:
        def run(self, fn, *args, **kwargs):
            calls.append((fn, args))
            if fn is dbx.release_unlaunched_jobs:
                return list(args[0])
            return None

    monkeypatch.setattr(remote, "stamp_execution_provider",
                        lambda _db, _job: "cloudflare")
    monkeypatch.setattr(worker_main, "_runner_for_job",
                        lambda _job: (remote.run_mcp_remote, "redesign"))
    monkeypatch.setattr(worker_main, "_notify_failure",
                        lambda *_a: pytest.fail("a drained claim is no failure"))
    worker_main.process_one(WorkerDb(), dict(job))
    assert len(launches) == 2
    fns = [fn for fn, _args in calls]
    assert dbx.release_unlaunched_jobs in fns
    assert dbx.finish_job not in fns and dbx.requeue_job not in fns
    released = [args for fn, args in calls if fn is dbx.release_unlaunched_jobs]
    assert released == [({73272: 1},)]


def test_shutdown_hands_back_unlaunched_claims_including_studio_turns(
        monkeypatch, ownership):
    """agent_turn is kept out of release_jobs because a turn that RAN may
    have written EDL versions; one that never launched is safe to give back."""
    for job_id in (101, 102, 103, 104):
        dbx.track_job(job_id)
    dbx.mark_remote_dispatch(101, 4)        # agent_turn, between launches
    dbx.mark_remote_dispatch(102, 1)        # mcp_tool, in flight
    dbx.mark_remote_owned(102)
    dbx.remote_launch_recorded(102)
    dbx.mark_remote_dispatch(103, 2)        # refused AFTER the snapshot
    dbx.mark_remote_owned(103)
    # 104: a local runner (no provider call).
    released = []

    class ShutdownDb:
        def run(self, fn, *args):
            if fn is dbx.release_jobs:
                released.append(("release_jobs", sorted(args[0])))
                return len(args[0])
            if fn is dbx.release_unlaunched_jobs:
                released.append(("unlaunched", dict(args[0])))
                return list(args[0])
            raise AssertionError(fn)

    monkeypatch.setattr(worker_main.dbx, "Db", ShutdownDb)
    monkeypatch.setattr(config, "SHUTDOWN_GRACE_S", 5.0)
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] == 0.5:
            # The provider refuses 103's in-flight launch (nothing ran).
            dbx.unmark_remote_owned(103)
        if clock[0] == 1.0:
            dbx.untrack_job(104)              # the local job finishes

    monkeypatch.setattr(worker_main.time, "sleep", sleep)
    monkeypatch.setattr(worker_main.time, "time", lambda: clock[0])

    class Exited(Exception):
        pass

    monkeypatch.setattr(worker_main.os, "_exit",
                        lambda code: (_ for _ in ()).throw(Exited(code)))
    worker_main.DRAINING.clear()
    try:
        with pytest.raises(Exited):
            worker_main._on_shutdown(15, None)
    finally:
        worker_main.DRAINING.clear()
        worker_main.agent_loop.SHUTDOWN.clear()
    assert released[0] == ("release_jobs", [104])
    assert released[1] == ("unlaunched", {101: 4})
    assert ("unlaunched", {103: 2}) in released
    # 102 is remote-owned: its durable ledger row carries it to the
    # replacement dispatcher's guardian.
    assert all(102 not in entry[1] for entry in released)
    # Exit as soon as the local job is done; 101 and 103 are not waited for.
    assert clock[0] == 1.0


def test_shutdown_waits_for_the_queue_write_of_an_answered_call(
        monkeypatch, ownership):
    dbx.track_job(201)
    dbx.mark_remote_dispatch(201, 1)
    dbx.mark_remote_owned(201)
    dbx.remote_launch_recorded(201)
    dbx.mark_settling(201)                  # its envelope came back

    class ShutdownDb:
        def run(self, fn, *args):
            return [] if fn is dbx.release_unlaunched_jobs else 0

    monkeypatch.setattr(worker_main.dbx, "Db", ShutdownDb)
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] == 1.5:
            dbx.untrack_job(201)           # process_one wrote the outcome

    monkeypatch.setattr(worker_main.time, "sleep", sleep)
    monkeypatch.setattr(worker_main.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker_main.os, "_exit",
                        lambda code: (_ for _ in ()).throw(SystemExit(code)))
    try:
        with pytest.raises(SystemExit):
            worker_main._on_shutdown(15, None)
    finally:
        worker_main.DRAINING.clear()
        worker_main.agent_loop.SHUTDOWN.clear()
    assert clock[0] == 1.5


def test_release_of_unlaunched_claims_is_fenced_and_refunds_the_attempt():
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return {"id": executed[-1][1][0]} if executed[-1][1][0] == 7 \
                else None

    class Conn:
        def cursor(self):
            return Cursor()

    released = dbx.release_unlaunched_jobs(Conn(), {7: 3, 8: 1, 9: None})
    assert released == [7]
    assert len(executed) == 2                 # no claim, no release
    sql, params = executed[0]
    assert "attempts = GREATEST(0, attempts - 1)" in sql
    assert "state = 'running'" in sql and "total_claims = %s" in sql
    assert "agent_turn" not in sql
    assert params == (7, 3)


# ───────────────────── executor rollout: "container is not running" ─────

def test_replay_73449_startup_that_lost_its_container_is_retried(
        monkeypatch, sleeps):
    """set_picture_card, 19:09:35Z: the Worker refused before /run because
    the container stopped while it started. Same identity again: it runs."""
    job = _job(73449, "set_picture_card", True, 3771)
    posted = []

    def post(url, **kwargs):
        posted.append(url)
        if len(posted) == 1:
            return _Response(dict(STARTUP_REFUSAL), 503)
        return _Response({"result": {"text": "EDL v12"}, "job_completed": True})

    monkeypatch.setattr(remote.requests, "post", post)
    result = remote._run_cloudflare_with_capacity_wait(dict(job))
    assert result["text"] == "EDL v12"
    assert len(posted) == 2 and posted[0] == posted[1]   # same identity
    assert posted[0].endswith(remote._cloudflare_call_id(job))
    assert len(sleeps) == 1 and sleeps[0] <= 1.5
    # The refused launch's ledger row was closed as cancelled (nothing ran).
    assert any(fn is dbx.finish_remote_execution and args[2] == "cancelled"
               for fn, args, _kw in _Ledger.events)


def test_repeated_startup_losses_spread_then_end_as_a_deferral(
        monkeypatch, sleeps):
    job = _job(74108, "add_motion_graphic", True, 3821)
    monkeypatch.setattr(config, "CLOUDFLARE_CAPACITY_RETRIES", 3)
    seen = []
    monkeypatch.setattr(remote.requests, "post", lambda url, **_k: (
        seen.append(url.rsplit("/", 1)[1])
        or _Response(dict(STARTUP_REFUSAL), 503)))
    with pytest.raises(remote.CloudflareCapacityUnavailable) as caught:
        remote._run_cloudflare_with_capacity_wait(dict(job))
    # Two retries on the pinned shard, then fresh identities.
    assert seen[0] == seen[1] == seen[2]
    assert seen[3].startswith("cf-alt1-") and seen[4].startswith("cf-alt2-")
    # process_one turns this into a deferral that spends no attempt.
    assert failure_policy.decision_for(caught.value, "mcp_tool").retryable


def test_every_unclassified_5xx_refusal_before_run_is_retryable(monkeypatch):
    for error in (NOT_RUNNING,
                  "Error: Container crashed while checking for ports",
                  "expired Cloudflare shard could not be reset: boom"):
        monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: _Response(
            {"error": error, "safe_to_fallback": True}, 503))
        with pytest.raises(remote.CloudflareStartInterrupted):
            remote._run_cloudflare(_job(1, "get_words", False, 5))


def test_a_new_job_type_on_an_older_worker_waits_for_the_rollout(
        monkeypatch):
    refusal = {"error": "job type future_kind is not allowed on batch",
               "safe_to_fallback": True}
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: _Response(refusal, 400))
    job = dict(_job(2, "x", False, 5), type="index")
    monkeypatch.setattr(remote.version, "code_version", lambda: "newer")
    with pytest.raises(remote.CloudflareRolloutPending):
        remote._run_cloudflare(dict(job))
    # Same release on both sides: a real misconfiguration stays loud.
    monkeypatch.setattr(remote.version, "code_version", lambda: "same")
    with pytest.raises(remote.CloudflareLaunchUnavailable) as caught:
        remote._run_cloudflare(dict(job))
    assert not isinstance(caught.value, (remote.CloudflareRolloutPending,
                                         remote.CloudflareCapacityUnavailable))


def test_a_draining_executor_refusal_is_relaunched_in_seconds(monkeypatch,
                                                             sleeps):
    job = _job(3, "get_words", False, 5)
    calls = []
    draining = {"error": "Deploy in progress: this executor is shutting "
                "down for a new release, so this mcp_tool was not started "
                "and nothing ran.", "retryable": True, "skew_refused": True,
                "failure": {"kind": "provider_rollout_pending",
                            "retryable": True, "max_attempts": 1}}

    def launch(j):
        calls.append(remote._cloudflare_call_id(j))
        if len(calls) == 1:
            return remote._interpret_cloudflare_terminal(dict(draining), j)
        return {"text": "words"}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    assert remote._run_cloudflare_with_capacity_wait(dict(job)) == {
        "text": "words"}
    assert sleeps == [remote._DRAINING_RELAUNCH_S]
    assert calls[1].startswith("cf-alt1-")


# ───────────────────── a container lost as /run is sent ─────

def test_a_call_whose_container_was_gone_at_run_is_fenced_and_relaunched(
        monkeypatch, sleeps):
    """The Worker records `unknown` when the container vanished as it sent
    /run. PostgreSQL proves no executor adopted it; fence it so none can,
    and relaunch: a mutation must not end as "outcome unknown"."""
    job = _job(4, "set_picture_card", True, 5)
    fenced = []

    class FenceDb(_Ledger):
        def run(self, fn, *args, **kwargs):
            if fn is dbx.fence_unadopted_remote_execution:
                fenced.append(args)
                return True
            return super().run(fn, *args, **kwargs)

    monkeypatch.setattr(remote.dbx, "Db", FenceDb)
    attempts = []

    def post(url, **_kwargs):
        attempts.append(url.rsplit("/", 1)[1])
        if len(attempts) == 1:
            return _Response({"error": NOT_RUNNING, "call_status": "unknown"},
                             502)
        return _Response({"result": {"text": "EDL v3"}, "job_completed": True})

    monkeypatch.setattr(remote.requests, "post", post)
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "unknown", "error": NOT_RUNNING})
    result = remote._run_cloudflare_with_capacity_wait(dict(job))
    assert result["text"] == "EDL v3"
    assert fenced and fenced[0][3] == attempts[0]
    assert attempts[1].startswith("cf-alt1-")     # the old shard is held


def test_an_adopted_call_is_never_fenced_or_relaunched(monkeypatch):
    job = _job(5, "set_picture_card", True, 5)

    class AdoptedDb(_Ledger):
        def run(self, fn, *args, **kwargs):
            if fn is dbx.fence_unadopted_remote_execution:
                return False                    # started_at is set
            return super().run(fn, *args, **kwargs)

    monkeypatch.setattr(remote.dbx, "Db", AdoptedDb)
    statuses = iter([{"status": "unknown", "error": NOT_RUNNING},
                     {"status": "done", "envelope": {
                         "result": {"text": "EDL v4"}, "job_completed": True}}])
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: next(statuses))
    monkeypatch.setattr(remote.time, "sleep", lambda _s: None)
    monkeypatch.setattr(remote, "_abandon_if_dead", lambda *_a, **_k: None)
    data = remote._recover_cloudflare_result(
        "cf-mcp-p5-x", "mcp", dict(job), remote.time.monotonic() + 60)
    assert data["result"]["text"] == "EDL v4"


def test_fence_sql_only_cancels_a_submitted_call_no_executor_started():
    executed = []

    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return {"name": "remote_executions"}

    class Conn:
        def cursor(self):
            return Cursor()

    original = dbx.remote_executions_table_ready
    dbx.remote_executions_table_ready = lambda _conn: True
    try:
        assert dbx.fence_unadopted_remote_execution(
            Conn(), 9, 2, "cloudflare", "cf-mcp-p1-abc", "gone") is True
    finally:
        dbx.remote_executions_table_ready = original
    sql, params = executed[-1]
    assert "state = 'submitted' AND started_at IS NULL" in sql
    assert "SET state = 'cancelled'" in sql
    assert params[1:] == (9, 2, "cloudflare", "cf-mcp-p1-abc")


def test_executor_ownership_confirmation_refuses_a_fenced_call():
    """The fence is safe because adoption is a compare-and-set too."""
    import inspect
    src = inspect.getsource(dbx.confirm_remote_execution_ownership)
    assert "AND state IN ('submitted', 'running')" in src
    assert "started_at = COALESCE(started_at, NOW())" in src


# ───────────────────── a read-only call whose container was lost ─────

LOST = {"error": "Cloudflare lost the executor during this mcp_tool "
        "(Cloudflare container exited during mcp_tool). No result was "
        "recorded; it is safe to retry.", "retryable": True,
        "failure": {"kind": "transient_infrastructure", "retryable": True,
                    "max_attempts": 1, "agent_repairable": False}}


def test_replay_68884_a_lost_read_is_relaunched_not_failed(monkeypatch,
                                                           sleeps):
    job = _job(68884, "look_at", False, 3615)
    calls = []

    def launch(j):
        calls.append(remote._cloudflare_call_id(j))
        if len(calls) == 1:
            return remote._interpret_cloudflare_terminal(dict(LOST), j)
        return {"text": "frames"}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    assert remote._run_cloudflare_with_capacity_wait(dict(job)) == {
        "text": "frames"}
    assert calls[1].startswith("cf-alt1-mcp_tool-p3615-")


def test_a_lost_mutation_is_never_replayed(monkeypatch, sleeps):
    job = _job(6, "set_picture_card", True, 5)
    lost = dict(LOST, retryable=False, failure={
        "kind": "outcome_unknown", "retryable": False, "max_attempts": 0})
    calls = []

    def launch(j):
        calls.append(1)
        return remote._interpret_cloudflare_terminal(dict(lost), j)

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_cloudflare_with_capacity_wait(dict(job))
    assert calls == [1]


def test_lost_read_relaunches_are_bounded(monkeypatch, sleeps):
    job = _job(7, "get_words", False, 5)
    calls = []

    def launch(j):
        calls.append(1)
        return remote._interpret_cloudflare_terminal(dict(LOST), j)

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_cloudflare_with_capacity_wait(dict(job))
    assert len(calls) == 1 + remote._LOST_READ_RELAUNCHES


# ───────────────────── the replacement dispatcher's guardian ─────

class _GuardianDb:
    def __init__(self, fence=False):
        self.calls = []
        self.fence = fence

    def run(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        if fn is dbx.fence_unadopted_remote_execution:
            return self.fence
        if fn in (dbx.finish_remote_execution, dbx.finish_job,
                  dbx.defer_unlaunched_cloudflare_busy):
            return True
        return None

    def fns(self):
        return [fn for fn, _a, _k in self.calls]

    def reset(self):
        pass


def _orphan(job_id=73277, tool="restore_range", mutation=True):
    from datetime import datetime, timedelta, timezone
    return {"provider": "cloudflare", "call_id": f"cf-mcp-p3772-{job_id}x",
            "function_name": "mcp", "job_id": job_id, "total_claims": 1,
            "type": "mcp_tool", "project_id": 3772, "user_id": 60,
            "attempts": 1, "payload": {"tool": tool, "mutation": mutation},
            "submitted_at": datetime.now(timezone.utc) - timedelta(minutes=5)}


def test_guardian_defers_an_orphaned_skew_or_drain_refusal(monkeypatch):
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "done", "envelope": {
            "error": "Deploy in progress: this executor is shutting down for "
                     "a new release", "retryable": True, "skew_refused": True,
            "failure": {"kind": "provider_rollout_pending",
                        "retryable": True, "max_attempts": 1}}})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _orphan())
    assert event["status"] == "requeued"
    defer = [kw for fn, _a, kw in db.calls
             if fn is dbx.defer_unlaunched_cloudflare_busy]
    assert defer == [{"counter": "cloudflare_rollout_deferrals"}]
    assert dbx.finish_job not in db.fns()


def test_guardian_fences_and_defers_a_call_lost_before_any_executor(
        monkeypatch):
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "unknown", "error": NOT_RUNNING})
    monkeypatch.setattr(remote, "_abandon_if_dead",
                        lambda *_a, **_k: pytest.fail("fenced first"))
    db = _GuardianDb(fence=True)
    monkeypatch.setattr(remote.dbx, "Db", lambda: db)
    event = remote.reconcile_remote_execution(db, _orphan())
    assert event["status"] == "requeued"
    assert dbx.defer_unlaunched_cloudflare_busy in db.fns()
    assert dbx.finish_job not in db.fns()


def test_guardian_still_fails_a_mutation_whose_outcome_is_unknown(
        monkeypatch):
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "failed", "envelope": dict(LOST, retryable=False, failure={
            "kind": "outcome_unknown", "retryable": False,
            "max_attempts": 0})})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _orphan())
    assert event["status"] == "failed"
    assert dbx.defer_unlaunched_cloudflare_busy not in db.fns()


# ───────────────────── the Worker publishing its own new version ─────

def test_a_worker_publish_blip_at_preflight_is_retried_not_failed(
        monkeypatch, sleeps):
    """workers.dev briefly serves 404/5xx while a Worker version publishes;
    no call exists yet, so the claim retries instead of failing."""
    answers = iter([_Response({"error": "not found"}, 404),
                    _Response("<html>", 503)])

    def get(*_a, **_k):
        try:
            return next(answers)
        except StopIteration:
            return _Response({"status": "ok", "provider": "cloudflare",
                              "source_version": "same",
                              "features": ["skew_guard"]})

    monkeypatch.setattr(remote.requests, "get", get)
    posts = []
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: (
        posts.append(1) or _Response({"result": {"text": "ok"},
                                      "job_completed": True})))
    job = _job(8, "get_words", False, 5)
    assert remote._run_cloudflare_with_capacity_wait(dict(job))["text"] == "ok"
    assert len(posts) == 1 and len(sleeps) == 2


def test_an_unreachable_worker_ends_as_a_deferral_never_a_failure(
        monkeypatch, sleeps):
    monkeypatch.setattr(remote.requests, "get", lambda *_a, **_k: (
        _ for _ in ()).throw(requests.ConnectionError("publishing")))
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: pytest.fail(
        "nothing may launch without a preflight"))
    with pytest.raises(remote.CloudflareCapacityUnavailable) as caught:
        remote._run_cloudflare_with_capacity_wait(_job(9, "get_words",
                                                       False, 5))
    # main.process_one defers exactly this class back to the queue unspent.
    assert isinstance(caught.value, remote.CloudflareCapacityUnavailable)


def test_guardian_never_fails_a_claim_another_call_now_serves(monkeypatch):
    """A guardian pass that read the ledger just before the dispatcher
    fenced and relaunched the claim observes the OLD call ending: the
    identity-scoped ledger close finds nothing, so it stands down."""
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    monkeypatch.setattr(remote, "_cloudflare_status", lambda *_a, **_k: {
        "status": "failed", "envelope": dict(LOST, retryable=False, failure={
            "kind": "outcome_unknown", "retryable": False,
            "max_attempts": 0})})

    class Relaunched(_GuardianDb):
        def run(self, fn, *args, **kwargs):
            if fn is dbx.finish_remote_execution:
                self.calls.append((fn, args, kwargs))
                return False
            return super().run(fn, *args, **kwargs)

    db = Relaunched()
    event = remote.reconcile_remote_execution(db, _orphan())
    assert event["status"] == "superseded"
    assert dbx.finish_job not in db.fns()
