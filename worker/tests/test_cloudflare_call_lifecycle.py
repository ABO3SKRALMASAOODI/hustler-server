"""Cloudflare call lifecycle: leases, dead calls, busy children, deploy skew."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import remote  # noqa: E402

MUTATION = {"id": 58049, "type": "mcp_tool", "project_id": 3177,
            "user_id": 60, "attempts": 1, "total_claims": 1,
            "payload": {"tool": "set_typography_scene", "mutation": True}}


def _enable(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)


@pytest.fixture(autouse=True)
def _fresh_stale_sightings():
    remote._STALE_SIGHTINGS.clear()
    yield
    remote._STALE_SIGHTINGS.clear()


def _stale_seen_before(call_id, claim=1):
    """An earlier stale reading, old enough for the next one to confirm."""
    now = remote.time.monotonic()
    remote._STALE_SIGHTINGS[(call_id, claim)] = [
        now - config.CLOUDFLARE_DEAD_CALL_CONFIRM_S - 1, now - 10]


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


# --------------------------------------------------------------- leases ---

def test_mcp_lease_is_sized_to_real_tool_calls_not_six_hours():
    lease = config.cloudflare_timeout_for("mcp_tool")
    # p99 101 s, max 907 s in production; the old lease was 21,600 s.
    assert 1200 <= lease <= 1800
    assert lease * 10 < 21600


def test_mcp_lease_outlives_every_synchronous_child_it_can_wait_on():
    lease = config.cloudflare_timeout_for("mcp_tool")
    for child in config.CLOUDFLARE_SYNCHRONOUS_TYPES:
        # remote._run_cloudflare waits the child's lease + 60 s.
        assert lease >= config.cloudflare_timeout_for(child) + 60 \
            + config.CLOUDFLARE_MCP_CHILD_MARGIN_S, child


def test_mcp_lease_falls_to_its_budget_when_children_are_short(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({"frames", "mcp_tool"}))
    assert config.cloudflare_timeout_for("mcp_tool") == 1200
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TIMEOUTS",
                        {**config.CLOUDFLARE_EXECUTOR_TIMEOUTS,
                         "mcp_tool": 3000})
    assert config.cloudflare_timeout_for("mcp_tool") == 3000


def test_source_length_bound_lanes_keep_their_leases():
    for kind in ("index", "final", "agent_turn", "shorts_plan"):
        assert config.cloudflare_timeout_for(kind) >= 21600
    assert config.cloudflare_timeout_for("preview") >= 3600


def test_adapter_comment_no_longer_claims_a_26_minute_lease():
    adapter = open(os.path.join(os.path.dirname(__file__), "..",
                                "cloudflare", "src", "index.ts")).read()
    assert "26-minute" not in adapter
    assert 'override sleepAfter = MODEL_GAP_SLEEP_AFTER' in adapter
    assert 'const MODEL_GAP_SLEEP_AFTER = "240s"' in adapter


# --------------------------------------------- busy id-less child calls ---

def _frames():
    return {"id": None, "type": "frames", "project_id": 7, "user_id": 3,
            "attempts": 0, "total_claims": None,
            "payload": {"storage_key": "clips/7/a.mp4", "times": [1.0]}}


def test_busy_synchronous_child_retries_on_fresh_unaccepted_identities(
        monkeypatch):
    _enable(monkeypatch)
    ids, sleeps = [], []
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)

    def launch(job):
        ids.append(remote._cloudflare_call_id(job))
        if len(ids) < 3:
            raise remote.CloudflareCapacityBusy(
                "Cloudflare Container shard is busy")
        return {"keys": ["scratch/frame.jpg"]}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    job = _frames()
    assert remote._run_remote(job) == {"keys": ["scratch/frame.jpg"]}
    assert len(ids) == len(set(ids)) == 3
    # Jittered exponential backoff: 0.5-1.5 s, then 1-3 s.
    assert 0.5 <= sleeps[0] <= 1.5 and 1.0 <= sleeps[1] <= 3.0
    assert len(sleeps) == 2


def test_busy_synchronous_child_is_bounded(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_SYNC_BUSY_RETRIES", 3)
    sleeps, calls = [], []
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)

    def busy(job):
        calls.append(remote._cloudflare_call_id(job))
        raise remote.CloudflareCapacityBusy("Cloudflare Container shard is busy")

    monkeypatch.setattr(remote, "_run_cloudflare", busy)
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_remote(_frames())
    assert len(calls) == len(set(calls)) == 4
    assert len(sleeps) == 3 and sum(sleeps) <= 10.5


def test_busy_child_with_a_configured_alternate_does_not_wait(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_TYPES", frozenset({"frames"}))
    monkeypatch.setattr(remote.time, "sleep",
                        lambda _s: pytest.fail("must not wait"))
    monkeypatch.setattr(remote, "_run_cloudflare", lambda _job: (
        _ for _ in ()).throw(remote.CloudflareCapacityBusy("shard is busy")))
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare_with_capacity_wait(_frames())


def test_ambiguous_child_launch_is_never_retried_on_a_new_identity(
        monkeypatch):
    _enable(monkeypatch)
    calls = []

    def ambiguous(job):
        calls.append(remote._cloudflare_call_id(job))
        raise remote.RemoteExecutorError("connection lost after acceptance")

    monkeypatch.setattr(remote, "_run_cloudflare", ambiguous)
    with pytest.raises(remote.RemoteExecutorError):
        remote._run_cloudflare_with_capacity_wait(_frames())
    assert len(calls) == 1


@pytest.mark.parametrize("runner,kind", [
    (remote.run_stock_acquire_remote, "stock_acquire"),
    (remote.run_fetch_remote, "fetch"),
])
def test_egress_children_also_retry_a_busy_shard(monkeypatch, runner, kind):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({kind, "search"}))
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES",
                        frozenset({kind, "search"}))
    monkeypatch.setattr(remote.time, "sleep", lambda _s: None)
    ids = []

    def launch(job):
        ids.append(remote._cloudflare_call_id(job))
        if len(ids) == 1:
            raise remote.CloudflareCapacityBusy("Cloudflare Container shard is busy")
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    result = runner(7, {"url": "https://x"}, 3)
    assert result["ok"] is True and result["fetch_provider"] == "cloudflare"
    assert len(set(ids)) == 2


# ------------------------------------------------- deploy in progress ---

@pytest.mark.parametrize("kind,attempts", [
    ("mcp_tool", 1), ("agent_turn", 1), ("index", 2), ("final", 2)])
def test_rollout_wait_ends_with_a_retryable_deploy_in_progress(
        monkeypatch, kind, attempts):
    _enable(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep",
                        lambda delay: now.__setitem__(0, now[0] + delay))

    class Probe:
        def run(self, fn, *_args):
            assert fn is dbx.lease_is_current
            return True

        def reset(self):
            pass

    monkeypatch.setattr(dbx, "Db", Probe)
    monkeypatch.setattr(remote, "_run_cloudflare", lambda _job: (
        _ for _ in ()).throw(remote.CloudflareRolloutPending(
            "container readiness mismatch role=executor source=old")))
    job = {"id": 42, "type": kind, "project_id": 7, "total_claims": 1}
    with pytest.raises(remote.CloudflareRolloutPending) as caught:
        remote._run_cloudflare_with_capacity_wait(job)
    assert now[0] == config.CLOUDFLARE_ROLLOUT_WAIT_S == 300
    decision = failure_policy.decision_for(caught.value, kind)
    assert decision.kind == "provider_rollout_pending"
    assert decision.retryable is True
    assert decision.max_attempts == attempts
    assert str(caught.value).startswith("Deploy in progress")
    assert "not started and nothing changed" in str(caught.value)


def test_child_call_reports_deploy_in_progress_immediately(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.time, "sleep",
                        lambda _s: pytest.fail("children must not wait"))
    monkeypatch.setattr(remote, "_run_cloudflare", lambda _job: (
        _ for _ in ()).throw(remote.CloudflareRolloutPending("source skew")))
    with pytest.raises(remote.CloudflareRolloutPending) as caught:
        remote._run_cloudflare_with_capacity_wait(_frames())
    assert "Deploy in progress" in str(caught.value)
    assert failure_policy.decision_for(caught.value, "mcp_tool").retryable


# ---------------------------------------------------- dead-call proofs ---

CALL = "cf-mcp-p3177-0123456789abcdef0123"
ALIVE = {"job_state": "running", "job_claims": 1, "heartbeat_stale": False,
         "remote_claims": 1, "remote_provider": "cloudflare",
         "remote_call_id": CALL, "remote_state": "running",
         "observed_stale": False}


@pytest.mark.parametrize("changes,dead", [
    ({}, None),
    ({"heartbeat_stale": True}, None),           # ledger still observed
    ({"observed_stale": True}, None),            # job still heartbeating
    ({"heartbeat_stale": True, "observed_stale": True}, "no executor heartbeat"),
    ({"job_state": "done"}, None),               # success is acknowledged
    ({"job_state": "failed"}, "queue job is failed"),
    ({"job_state": "queued"}, "queue job is queued"),
    ({"job_claims": 2}, "moved to a newer claim"),
    ({"remote_state": "failed"}, "executor recorded failed"),
    ({"remote_state": "cancelled"}, "executor recorded cancelled"),
    ({"remote_call_id": "cf-mcp-p3177-other"}, "another provider call"),
    ({"remote_provider": "modal"}, "another provider call"),
    ({"remote_provider": None, "remote_claims": None, "remote_call_id": None,
      "remote_state": None, "observed_stale": None}, None),
    ({"remote_provider": None, "remote_claims": None, "remote_call_id": None,
      "remote_state": None, "observed_stale": None,
      "heartbeat_stale": True}, "no executor heartbeat"),
])
def test_dead_call_reason_requires_positive_database_evidence(changes, dead):
    reason = remote._cloudflare_dead_call_reason(
        {**ALIVE, **changes}, MUTATION, CALL)
    if dead is None:
        assert reason is None
    else:
        assert dead in reason
    assert remote._cloudflare_dead_call_reason(None, MUTATION, CALL) is None
    assert "no longer exists" in remote._cloudflare_dead_call_reason(
        {"job_state": "missing"}, MUTATION, CALL)


def test_liveness_query_compares_heartbeats_in_database_time(monkeypatch):
    executed = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def execute(self, sql, args): executed.append((sql, args))
        def fetchone(self): return None

    class Conn:
        def cursor(self): return Cursor()

    monkeypatch.setattr(dbx, "remote_executions_table_ready", lambda _c: True)
    assert remote._cloudflare_liveness_row(Conn(), 58049, 180.0) == {
        "job_state": "missing"}
    sql, args = executed[0]
    assert args == (180.0, 180.0, 58049)
    assert "j.heartbeat_at" in sql and "r.last_observed_at" in sql
    assert sql.count("NOW() - make_interval(secs => %s)") == 2
    assert "LEFT JOIN remote_executions" in sql
    monkeypatch.setattr(dbx, "remote_executions_table_ready", lambda _c: False)
    assert remote._cloudflare_liveness_row(Conn(), 58049, 180.0) is None


class _LivenessDb:
    def __init__(self, row, extra=None):
        self.row, self.calls, self.extra = row, [], extra or {}

    def run(self, fn, *args):
        self.calls.append((fn, args))
        if fn is remote._cloudflare_liveness_row:
            return self.row
        if fn in self.extra:
            return self.extra[fn]
        raise AssertionError(fn)

    def reset(self):
        pass


def test_abandon_request_carries_exact_claim_and_reason(monkeypatch):
    _enable(monkeypatch)
    posted = []
    envelope = {"error": "lost", "retryable": False,
                "failure": {"kind": "outcome_unknown", "retryable": False}}
    monkeypatch.setattr(remote.requests, "post", lambda url, **kw: (
        posted.append((url, kw)) or _Response(
            {"status": "failed", "envelope": envelope, "abandoned": True})))
    _stale_seen_before(CALL)
    db = _LivenessDb({**ALIVE, "heartbeat_stale": True,
                      "observed_stale": True})
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) == envelope
    url, kwargs = posted[0]
    assert url == f"https://executor.example/calls/mcp/{CALL}/abandon"
    assert kwargs["json"]["job"] == {"id": 58049, "type": "mcp_tool",
                                     "project_id": 3177, "total_claims": 1}
    assert "no executor heartbeat for 180s" in kwargs["json"]["reason"]


@pytest.mark.parametrize("response", [
    _Response({"status": "unknown", "abandoned": False}, 409),
    _Response({"status": "stopping", "abandoned": False}, 200),
    remote.requests.ConnectionError("lost"),
])
def test_refused_or_unconfirmed_abandonment_changes_nothing(
        monkeypatch, response):
    _enable(monkeypatch)

    def post(*_a, **_k):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(remote.requests, "post", post)
    _stale_seen_before(CALL)
    db = _LivenessDb({**ALIVE, "heartbeat_stale": True,
                      "observed_stale": True})
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None


def test_live_or_unproven_calls_are_never_abandoned(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: pytest.fail("must not abandon"))
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION,
                                   _LivenessDb(dict(ALIVE))) is None
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION,
                                   _LivenessDb(None)) is None
    child = dict(MUTATION, id=None, total_claims=None)
    assert remote._abandon_if_dead(CALL, "mcp", child,
                                   _LivenessDb(None)) is None


# ------------------------------------------- attached dispatcher recovery ---

def test_unknown_call_earns_no_heartbeat_and_dead_one_is_released(
        monkeypatch):
    _enable(monkeypatch)
    statuses = iter([{"status": "running"}] + [{"status": "unknown"}] * 40)
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: next(statuses))
    monkeypatch.setattr(remote.time, "sleep", lambda _s: None)
    calls, checks = [], []

    class Probe:
        def run(self, fn, *args):
            calls.append(fn)
            if fn is dbx.get_job:
                return {"state": "running"}
            if fn is dbx.heartbeat_remote_execution:
                return True
            raise AssertionError(fn)

        def reset(self):
            pass

    monkeypatch.setattr(dbx, "Db", Probe)
    envelope = {"error": "lost", "retryable": False}

    def abandon(call_id, lane, job, worker_db=None):
        checks.append(call_id)
        return envelope if len(checks) == 2 else None

    monkeypatch.setattr(remote, "_abandon_if_dead", abandon)
    result = remote._recover_cloudflare_result(CALL, "mcp", MUTATION,
                                               remote.time.monotonic() + 3600)
    assert result == envelope
    # One heartbeat for the running observation; none while unknown.
    assert calls.count(dbx.heartbeat_remote_execution) == 1
    # Liveness is evaluated on the first unknown poll and every 15th after.
    assert len(checks) == 2
    assert calls.count(dbx.get_job) == 1 + remote._LIVENESS_EVERY_POLLS


# ------------------------------------------------------ orphan guardian ---

def _guardian_row(payload):
    return {"provider": "cloudflare", "call_id": CALL, "function_name": "mcp",
            "job_id": 58049, "total_claims": 1, "type": "mcp_tool",
            "project_id": 3177, "user_id": 60, "attempts": 1,
            "payload": payload}


def test_guardian_leaves_a_live_unknown_call_alone_without_heartbeat(
        monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: {"status": "unknown"})
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: pytest.fail("must not abandon"))
    db = _LivenessDb(dict(ALIVE))
    event = remote.reconcile_remote_execution(
        db, _guardian_row(MUTATION["payload"]))
    assert event["status"] == "running"
    assert [fn for fn, _ in db.calls] == [remote._cloudflare_liveness_row]


@pytest.mark.parametrize("mutation", [True, False])
def test_guardian_releases_dead_call_with_mutation_aware_outcome(
        monkeypatch, mutation):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: {"status": "unknown"})
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    if mutation:
        envelope = {"error": "Cloudflare lost the executor during this "
                    "mcp_tool (no executor heartbeat for 180s). Its outcome "
                    "is unknown", "retryable": False,
                    "failure": {"kind": "outcome_unknown", "retryable": False,
                                "max_attempts": 0}}
    else:
        envelope = {"error": "Cloudflare lost the executor; safe to retry",
                    "retryable": True,
                    "failure": {"kind": "transient_infrastructure",
                                "retryable": True, "max_attempts": 1}}
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: _Response(
        {"status": "failed", "envelope": envelope, "abandoned": True}))
    _stale_seen_before(CALL)
    finished = []
    db = _LivenessDb({**ALIVE, "heartbeat_stale": True,
                      "observed_stale": True}, {
        dbx.finish_remote_execution: True,
        dbx.requeue_job: True,
    })
    original = db.run

    def run(fn, *args):
        if fn is dbx.finish_job:
            finished.append(args)
            return True
        return original(fn, *args)

    db.run = run
    event = remote.reconcile_remote_execution(
        db, _guardian_row({"tool": "x", "mutation": mutation}))
    # MCP calls are never requeued (the lane claims one attempt only): the
    # live model receives the outcome and decides whether to call again.
    assert event["status"] == "failed"
    assert not any(fn is dbx.requeue_job for fn, _ in db.calls)
    failure = finished[0][3]["failure"]
    if mutation:
        assert failure["kind"] == "outcome_unknown"
        assert failure["retryable"] is False
        assert "outcome is unknown" in str(finished[0][2])
    else:
        assert failure["kind"] == "transient_infrastructure"
        assert failure["retryable"] is True


def test_media_jobs_get_one_automatic_retry_after_a_dead_call(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: {"status": "unknown"})
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    envelope = {"error": "Cloudflare lost the executor; safe to retry",
                "retryable": True,
                "failure": {"kind": "transient_infrastructure",
                            "retryable": True, "max_attempts": 2}}
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: _Response(
        {"status": "failed", "envelope": envelope, "abandoned": True}))
    _stale_seen_before(CALL)
    db = _LivenessDb({**ALIVE, "heartbeat_stale": True,
                      "observed_stale": True}, {
        dbx.finish_remote_execution: True, dbx.requeue_job: True})
    row = dict(_guardian_row({}), type="preview", function_name="interactive")
    assert remote.reconcile_remote_execution(db, row)["status"] == "requeued"


def test_studio_turns_keep_their_reaper_death_resume(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: pytest.fail("must not abandon"))
    turn = dict(MUTATION, type="agent_turn")
    assert remote._abandon_if_dead(CALL, "agent", turn, _LivenessDb(
        {**ALIVE, "heartbeat_stale": True, "observed_stale": True})) is None


# --------------------------------------------- busy shard, dead owner ---

def _busy_post(owner):
    return _Response({"error": "Cloudflare Container shard is busy",
                      "safe_to_fallback": True, "active_call_id": owner}, 429)


def _launch_fixture(monkeypatch, owner_status, liveness):
    _enable(monkeypatch)
    abandoned = []

    def post(url, **kwargs):
        if url.endswith("/abandon"):
            abandoned.append((url, kwargs["json"]))
            return _Response({"status": "failed", "abandoned": True,
                              "envelope": {"error": "lost"}})
        return _busy_post(CALL)

    class Probe:
        def run(self, fn, *args):
            if fn is remote._cloudflare_liveness_row:
                return liveness
            if fn is dbx.completed_remote_call:
                return None
            return True

        def reset(self):
            pass

    monkeypatch.setattr(dbx, "Db", Probe)
    monkeypatch.setattr(dbx, "mark_remote_owned", lambda _id: True)
    monkeypatch.setattr(dbx, "remote_launch_recorded", lambda _id: None)
    monkeypatch.setattr(dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "_cloudflare_preflight", lambda **_k: {})
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda call_id, lane, timeout=10: owner_status)
    monkeypatch.setattr(remote.requests, "post", post)
    return abandoned


def test_busy_project_shard_releases_its_dead_owner(monkeypatch):
    owner = {"status": "unknown", "job": {"id": 58049, "type": "mcp_tool",
                                          "project_id": 3177, "total_claims": 1}}
    _stale_seen_before(CALL)
    abandoned = _launch_fixture(monkeypatch, owner, {
        **ALIVE, "heartbeat_stale": True, "observed_stale": True})
    waiting = dict(MUTATION, id=58050)
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare(waiting)
    assert len(abandoned) == 1
    assert abandoned[0][0].endswith(f"/calls/mcp/{CALL}/abandon")
    assert abandoned[0][1]["job"] == owner["job"]


@pytest.mark.parametrize("owner", [
    {"status": "running", "job": {"id": 58049, "type": "mcp_tool",
                                  "project_id": 3177, "total_claims": 1}},
    {"status": "unknown"},                       # pre-upgrade call, no identity
    {"status": "unknown", "job": {"id": None, "type": "frames",
                                  "project_id": 7, "total_claims": None}},
])
def test_busy_owner_without_proof_is_left_alone(monkeypatch, owner):
    abandoned = _launch_fixture(monkeypatch, owner, {
        **ALIVE, "heartbeat_stale": True, "observed_stale": True})
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare(dict(MUTATION, id=58050))
    assert abandoned == []


def test_live_busy_owner_is_left_alone(monkeypatch):
    owner = {"status": "unknown", "job": {"id": 58049, "type": "mcp_tool",
                                          "project_id": 3177, "total_claims": 1}}
    abandoned = _launch_fixture(monkeypatch, owner, dict(ALIVE))
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare(dict(MUTATION, id=58050))
    assert abandoned == []


# ------------------------------------- stale heartbeat needs confirmation ---

def _clocked(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    return now


def _abandon_poster(monkeypatch):
    posted = []
    monkeypatch.setattr(remote.requests, "post", lambda url, **kw: (
        posted.append(kw["json"]) or _Response(
            {"status": "failed", "abandoned": True,
             "envelope": {"error": "lost", "retryable": True}})))
    return posted


STALE = {**ALIVE, "heartbeat_stale": True, "observed_stale": True}


def test_one_stale_reading_never_destroys_a_call(monkeypatch):
    _enable(monkeypatch)
    now = _clocked(monkeypatch)
    posted = _abandon_poster(monkeypatch)
    db = _LivenessDb(dict(STALE))
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    now[0] += config.CLOUDFLARE_DEAD_CALL_CONFIRM_S - 1
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    assert posted == []
    now[0] += 1
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is not None
    assert len(posted) == 1
    assert posted[0]["reason"].startswith("no executor heartbeat for 180s")
    # A terminal outcome forgets the call.
    assert remote._STALE_SIGHTINGS == {}


def test_database_outage_cannot_get_a_live_executor_destroyed(monkeypatch):
    """The first query after an outage sees every heartbeat stale; the
    executor's reconnecting heartbeat thread beats before confirmation."""
    _enable(monkeypatch)
    now = _clocked(monkeypatch)
    posted = _abandon_poster(monkeypatch)
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION,
                                   _LivenessDb(dict(STALE))) is None
    now[0] += 30
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION,
                                   _LivenessDb(dict(ALIVE))) is None
    now[0] += config.CLOUDFLARE_DEAD_CALL_CONFIRM_S
    # A later stale reading starts a new clock instead of confirming.
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION,
                                   _LivenessDb(dict(STALE))) is None
    assert posted == []


def test_an_unobserved_gap_restarts_confirmation(monkeypatch):
    _enable(monkeypatch)
    now = _clocked(monkeypatch)
    posted = _abandon_poster(monkeypatch)
    db = _LivenessDb(dict(STALE))
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    now[0] += remote._STALE_SIGHTING_MAX_GAP_S + 1
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    assert posted == []
    # Guardian-cadence readings keep the restarted first sighting alive.
    restarted = now[0]
    while now[0] + 15 - restarted < config.CLOUDFLARE_DEAD_CALL_CONFIRM_S:
        now[0] += 15
        assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    now[0] += 15
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is not None
    assert len(posted) == 1


def test_sightings_are_per_claim_and_bounded(monkeypatch):
    _enable(monkeypatch)
    now = _clocked(monkeypatch)
    _abandon_poster(monkeypatch)
    db = _LivenessDb(dict(STALE))
    remote._abandon_if_dead(CALL, "mcp", MUTATION, db)
    now[0] += config.CLOUDFLARE_DEAD_CALL_CONFIRM_S
    # The same call id under a newer claim is a different execution.
    newer = dict(MUTATION, total_claims=2)
    assert remote._abandon_if_dead(CALL, "mcp", newer, _LivenessDb(
        dict(STALE, job_claims=2, remote_claims=2))) is None
    assert set(remote._STALE_SIGHTINGS) == {(CALL, 1), (CALL, 2)}
    now[0] += remote._STALE_SIGHTING_MAX_GAP_S + 1
    remote._stale_heartbeat_confirmed("cf-other-call-identity", 1)
    assert set(remote._STALE_SIGHTINGS) == {("cf-other-call-identity", 1)}


def test_refused_abandonment_keeps_the_confirmed_sighting(monkeypatch):
    _enable(monkeypatch)
    now = _clocked(monkeypatch)
    responses = iter([_Response({"status": "unknown", "abandoned": False}, 409),
                      _Response({"status": "failed", "abandoned": True,
                                 "envelope": {"error": "lost"}})])
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: next(responses))
    db = _LivenessDb(dict(STALE))
    remote._abandon_if_dead(CALL, "mcp", MUTATION, db)
    now[0] += config.CLOUDFLARE_DEAD_CALL_CONFIRM_S
    # The shard's own 120-s disconnection floor refused; the next reading
    # retries at once rather than starting a new confirmation clock.
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) is None
    now[0] += 15
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, db) == {
        "error": "lost"}


@pytest.mark.parametrize("changes", [
    {"remote_state": "failed"}, {"job_state": "failed"},
    {"job_claims": 2}, {"job_state": "missing"},
])
def test_positive_records_need_no_confirmation(monkeypatch, changes):
    _enable(monkeypatch)
    _clocked(monkeypatch)
    posted = _abandon_poster(monkeypatch)
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, _LivenessDb(
        {**ALIVE, **changes})) is not None
    assert len(posted) == 1


def test_recorded_executor_failure_keeps_its_cause(monkeypatch):
    _enable(monkeypatch)
    posted = _abandon_poster(monkeypatch)
    cause = ("RuntimeError: ffmpeg exited 1\n" + "frame= 10 fps=0.0\r" * 40
             + "Error initializing filter 'drawtext'\x00\x1b[0m")
    assert remote._abandon_if_dead(CALL, "mcp", MUTATION, _LivenessDb(
        {**ALIVE, "remote_state": "failed", "remote_error": cause})) is not None
    reason = posted[0]["reason"]
    assert reason.startswith("its executor recorded failed: RuntimeError")
    assert reason.endswith("Error initializing filter 'drawtext' [0m")
    assert " ... " in reason
    # The Durable Object keeps 160 printable characters of the reason.
    assert len(reason) <= 160
    assert all(" " <= ch <= "~" for ch in reason)
    assert remote._cloudflare_dead_call_reason(
        {**ALIVE, "remote_state": "cancelled", "remote_error": None},
        MUTATION, CALL) == "its executor recorded cancelled"


def test_liveness_query_reads_the_executor_error():
    executed = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def execute(self, sql, args): executed.append(sql)
        def fetchone(self): return {"job_state": "running"}

    class Conn:
        def cursor(self): return Cursor()

    original = dbx.remote_executions_table_ready
    dbx.remote_executions_table_ready = lambda _c: True
    try:
        remote._cloudflare_liveness_row(Conn(), 1, 180.0)
    finally:
        dbx.remote_executions_table_ready = original
    assert "r.error AS remote_error" in executed[0]


# -------------------------------------- deploy skew spends no attempt ---

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


def _process_rollout_pending(monkeypatch, job_type, deferred):
    import main
    monkeypatch.setattr(main.remote, "stamp_execution_provider",
                        lambda _db, _job: "cloudflare")

    def runner(_db, job):
        raise remote._deploy_in_progress(job, "source skew")

    monkeypatch.setattr(main, "_runner_for_job",
                        lambda _job: (runner, "test"))
    monkeypatch.setattr(main, "_notify_failure", lambda *_a: None)
    db = _QueueDb(deferred)
    main.process_one(db, {"id": 42, "type": job_type, "project_id": 7,
                          "user_id": 60, "attempts": 1, "total_claims": 3,
                          "payload": {}})
    return [(fn, args, kwargs) for fn, args, kwargs in db.calls]


@pytest.mark.parametrize("job_type", ["final", "preview", "index"])
def test_deploy_in_progress_returns_media_to_the_queue_unspent(
        monkeypatch, job_type):
    calls = _process_rollout_pending(monkeypatch, job_type, deferred=True)
    fns = [fn for fn, _a, _k in calls]
    assert dbx.defer_unlaunched_cloudflare_busy in fns
    assert dbx.requeue_job not in fns and dbx.finish_job not in fns
    _fn, args, kwargs = next(c for c in calls
                             if c[0] is dbx.defer_unlaunched_cloudflare_busy)
    assert args[:2] == (42, 3)
    assert args[3] == config.CLOUDFLARE_ROLLOUT_MAX_DEFERRALS == 1
    assert kwargs == {"counter": "cloudflare_rollout_deferrals"}
    assert (dbx.bump_metric, ("cloudflare_rollout_deferred",), {}) in calls


def test_exhausted_deploy_deferral_falls_back_to_the_bounded_retry(
        monkeypatch):
    calls = _process_rollout_pending(monkeypatch, "final", deferred=False)
    fns = [fn for fn, _a, _k in calls]
    assert dbx.requeue_job in fns          # attempts 1 < max_attempts 2
    assert dbx.finish_job not in fns


@pytest.mark.parametrize("job_type", ["mcp_tool", "agent_turn"])
def test_deploy_in_progress_goes_straight_to_live_callers(
        monkeypatch, job_type):
    calls = _process_rollout_pending(monkeypatch, job_type, deferred=True)
    fns = [fn for fn, _a, _k in calls]
    assert dbx.defer_unlaunched_cloudflare_busy not in fns
    assert dbx.finish_job in fns and dbx.requeue_job not in fns


def test_rollout_deferrals_have_their_own_counter():
    class Conn:
        rowcount = 1

        def __init__(self):
            self.sql = []

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params):
            self.sql.append((sql, params))

    busy, rollout = Conn(), Conn()
    assert dbx.defer_unlaunched_cloudflare_busy(
        busy, 42, 6, RuntimeError("busy"), 5)
    assert dbx.defer_unlaunched_cloudflare_busy(
        rollout, 42, 6, RuntimeError("deploy"), 1,
        counter="cloudflare_rollout_deferrals")
    busy_sql, busy_params = busy.sql[0]
    rollout_sql, rollout_params = rollout.sql[0]
    assert "cloudflare_rollout_deferrals" not in busy_sql
    assert "cloudflare_busy_deferrals" not in rollout_sql
    assert rollout_sql == busy_sql.replace("cloudflare_busy_deferrals",
                                           "cloudflare_rollout_deferrals")
    # Both refund the attempt, keep the claim ceiling and delay the reclaim.
    assert "attempts = GREATEST(0, attempts - 1)" in rollout_sql
    assert "{cloudflare_busy_deferred}" in rollout_sql
    assert rollout_params[1:] == (42, 6, 1) and busy_params[1:] == (42, 6, 5)
    with pytest.raises(ValueError):
        dbx.defer_unlaunched_cloudflare_busy(
            Conn(), 42, 6, RuntimeError("x"), 1, counter="attempts")
