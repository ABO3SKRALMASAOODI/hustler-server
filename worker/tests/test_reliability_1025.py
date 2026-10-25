"""Strategy 1025 reliability: R1 quiet infrastructure retries and no raw
error text, R2 never 'local', R5 the LLM balance watch, R7 fallbacks only to
an executor on this release."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import llm  # noqa: E402
import main as worker_main  # noqa: E402
import remote  # noqa: E402

NOW = datetime.now(timezone.utc)
RAW = ("no remote executor is configured for index",
       "Cloudflare Container shard is busy",
       "Cloudflare source version does not match the dispatcher (a != b)",
       "container readiness mismatch role=executor source=f3f2c1e9",
       "Deploy in progress: Valmera's executor is switching to a new release",
       "Cloudflare lost the executor during this index (container exited)")


# ── R1: classification, window, words ────────────────────────────────────

@pytest.mark.parametrize("text", RAW)
def test_fleet_failures_are_infrastructure(text):
    assert failure_policy.is_infrastructure(RuntimeError(text))


@pytest.mark.parametrize("text", (
    "inserts[2].at_output_s 4.1 is not on a keep-segment boundary",
    "ffmpeg failed (exit 1): Invalid argument",
    "video is 3.3 h and the limit is 3 h"))
def test_the_customers_edit_or_file_is_not_infrastructure(text):
    assert not failure_policy.is_infrastructure(RuntimeError(text))


@pytest.mark.parametrize("text", RAW + (
    "ffmpeg failed (exit -9): encoder : Lavc61 aac | [tls @ 0x9] IO error",
    "inserts[2].at_output_s 4.1 is not on a keep-segment boundary"))
def test_user_reason_never_pastes_the_raw_error(text):
    reason = failure_policy.user_reason(RuntimeError(text))
    for fragment in ("executor", "ffmpeg", "Cloudflare", "inserts[", "0x9",
                     "readiness", "dispatcher"):
        assert fragment not in reason


def test_quiet_retry_window_is_ten_minutes_for_idempotent_media_only():
    job = {"type": "index", "created_at": NOW - timedelta(minutes=1)}
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 0) == 10
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 2) == 40
    assert failure_policy.infra_retry_delay(
        job, NOW.timestamp(), failure_policy.INFRA_RETRY_MAX) is None
    old = dict(job, created_at=NOW - timedelta(minutes=11))
    assert failure_policy.infra_retry_delay(old, NOW.timestamp(), 0) is None
    near = dict(job, created_at=NOW - timedelta(minutes=9, seconds=53))
    assert failure_policy.infra_retry_delay(near, NOW.timestamp(), 1) == \
        pytest.approx(7, abs=0.5)
    for kind in ("agent_turn", "mcp_tool", "shorts_plan"):
        assert failure_policy.infra_retry_delay(
            dict(job, type=kind), NOW.timestamp(), 0) is None


class _QueueDb:
    def __init__(self, deferred=True):
        self.calls = []
        self.deferred = deferred

    def run(self, fn, *args, **kw):
        self.calls.append((fn, args, kw))
        if fn is dbx.defer_unlaunched_cloudflare_busy:
            return self.deferred
        return None


def test_an_infrastructure_failure_goes_back_to_the_queue_quietly(monkeypatch):
    waits = []
    monkeypatch.setattr(worker_main.DRAINING, "wait", waits.append)
    db = _QueueDb()
    # The real clock: _quiet_infra_retry measures age with time.time().
    job = {"id": 5, "type": "preview",
           "created_at": datetime.now(timezone.utc) - timedelta(minutes=2),
           "payload": {"infra_retry_deferrals": 1}}
    err = RuntimeError("Cloudflare Container shard is busy")
    decision = failure_policy.decision_for(err, "preview")
    assert worker_main._quiet_infra_retry(db, job, err, decision, 3)
    assert waits == [20]
    fn, args, kw = db.calls[0]
    assert fn is dbx.defer_unlaunched_cloudflare_busy
    assert args[:2] == (5, 3) and kw["counter"] == "infra_retry_deferrals"
    assert "infra_retry_deferrals" in dbx._DEFERRAL_COUNTERS


def test_a_real_edit_failure_or_an_old_job_is_not_retried_quietly(monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _QueueDb()
    bad = RuntimeError("ffmpeg failed (exit 1): Invalid argument")
    job = {"id": 5, "type": "final", "created_at": datetime.now(timezone.utc),
           "payload": {}}
    assert not worker_main._quiet_infra_retry(
        db, job, bad, failure_policy.decision_for(bad, "final"), 1)
    infra = RuntimeError("Cloudflare Container shard is busy")
    old = dict(job, created_at=datetime.now(timezone.utc)
               - timedelta(minutes=12))
    assert not worker_main._quiet_infra_retry(
        db, old, infra, failure_policy.decision_for(infra, "final"), 1)
    assert db.calls == []


class _ChatDb:
    def __init__(self):
        self.notes = []

    def run(self, fn, *args):
        if fn is worker_main.dbx.get_project:
            return {"chat_session_id": 7}
        if fn is worker_main.dbx.add_job_failure_message:
            self.notes.append(args[1])
            return None
        if fn is worker_main.dbx.has_newer_agent_turn:
            return False
        raise AssertionError(fn)


@pytest.mark.parametrize("job_type,payload", (
    ("index", {}), ("final", {}), ("preview", {"source": "user_edit"})))
@pytest.mark.parametrize("text", RAW)
def test_customers_never_see_raw_infrastructure_text(job_type, payload, text):
    db = _ChatDb()
    worker_main._notify_failure(
        db, {"id": 1, "type": job_type, "project_id": 2, "payload": payload},
        RuntimeError(text))
    assert len(db.notes) == 1
    note = db.notes[0]
    assert "on our side" in note
    for fragment in ("executor is", "Cloudflare", "shard", "readiness",
                     "dispatcher", "Deploy in progress", "f3f2c1e9"):
        assert fragment not in note


def test_ffmpeg_detail_stays_out_of_the_export_note():
    db = _ChatDb()
    worker_main._notify_failure(
        db, {"id": 1, "type": "final", "project_id": 2, "payload": {}},
        RuntimeError("ffmpeg failed (exit 1): Invalid argument | "
                     "title : ZGJkNNZh"))
    assert "ZGJkNNZh" not in db.notes[0] and "ffmpeg" not in db.notes[0]


def test_the_shorts_planners_own_reasons_are_still_quoted():
    db = _ChatDb()
    worker_main._notify_failure(
        db, {"id": 1, "type": "shorts_plan", "project_id": 2, "payload": {}},
        RuntimeError("shorts need a longer source (40 s)"))
    assert "shorts need a longer source (40 s)" in db.notes[0]


# ── R2: never 'local' while a fleet is configured ─────────────────────────

def _cloudflare(monkeypatch, types=("index", "preview", "final")):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES", frozenset(types))
    monkeypatch.setattr(config, "CLOUDFLARE_MAX_INDEX_INPUT_BYTES",
                        8 * 1024 ** 3)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "")


class _StampDb:
    def __init__(self, shape):
        self.shape = shape
        self.calls = []

    def run(self, fn, *args):
        self.calls.append(fn)
        if fn is dbx.project_execution_shape:
            return self.shape
        if fn is dbx.lease_is_current:
            return True
        if fn is dbx.stamp_execution_provider:
            return args[2]
        raise AssertionError(fn)


def test_a_job_no_selector_accepts_is_re_resolved_then_sent_to_the_fleet(
        monkeypatch):
    _cloudflare(monkeypatch)
    alarms, sleeps = [], []
    monkeypatch.setattr(remote, "owner_alarm",
                        lambda key, text, **kw: alarms.append((key, text)))
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    # 9 GB, no asset kind: the shape 2392's first upload had on 19 Oct.
    huge = {"assets": 1, "total_bytes": 9_015_887_417,
            "max_bytes": 9_015_887_417, "max_duration_s": 0.0}
    job = {"id": 84902, "type": "index", "project_id": 1, "user_id": 2392,
           "total_claims": 1, "payload": {}}
    provider = remote.stamp_execution_provider(_StampDb(huge), job)
    assert provider == "cloudflare"
    assert sleeps == [remote.REROUTE_WAIT_S]
    assert alarms and alarms[0][0] == "executor_routing_alarm"
    assert "84902" in alarms[0][1]
    assert job["payload"]["execution_provider"] == "cloudflare"


def test_a_failed_shape_probe_is_simply_retried(monkeypatch):
    _cloudflare(monkeypatch)
    monkeypatch.setattr(remote.time, "sleep", lambda s: None)
    alarms = []
    monkeypatch.setattr(remote, "owner_alarm",
                        lambda key, text, **kw: alarms.append(key))
    db = _StampDb({"assets": 1, "total_bytes": 10_000_000,
                   "max_bytes": 10_000_000, "max_duration_s": 30.0})
    calls = {"n": 0}
    real = db.run

    def flaky(fn, *args):
        if fn is dbx.project_execution_shape and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("connection reset")
        return real(fn, *args)
    db.run = flaky
    job = {"id": 9, "type": "index", "project_id": 1, "total_claims": 1,
           "payload": {}}
    assert remote.stamp_execution_provider(db, job) == "cloudflare"
    assert alarms == []                 # re-resolved, nothing to alarm


def test_development_without_a_fleet_still_runs_local(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "")
    assert not remote.remote_configured()
    job = {"id": 1, "type": "preview", "project_id": 1, "payload": {}}
    assert remote.desired_execution_provider(job) == "local"
    assert remote.fallback_provider(job) is None


def test_a_stale_local_stamp_moves_to_the_fleet(monkeypatch):
    _cloudflare(monkeypatch)
    moved = []
    monkeypatch.setattr(remote, "owner_alarm", lambda *a, **k: None)

    class Probe:
        def run(self, fn, *args):
            moved.append((fn, args))
            return args[2]

        def reset(self):
            pass
    monkeypatch.setattr(remote.dbx, "Db", Probe)
    job = {"id": 3, "type": "preview", "total_claims": 2,
           "payload": {"execution_provider": "local"}}
    assert remote._move_stale_local(job) == "cloudflare"
    assert moved[0][0] is dbx.replace_local_execution_provider
    assert job["payload"]["execution_provider"] == "cloudflare"


def test_boot_self_check_names_types_without_an_executor(monkeypatch):
    _cloudflare(monkeypatch, types=("index",))
    alarms = []
    monkeypatch.setattr(remote, "owner_alarm",
                        lambda key, text, **kw: alarms.append(text))
    problems = remote.routing_self_check()
    assert "preview has no executor" in problems
    assert "index has no executor" not in problems
    assert alarms and "Routing self-check" in alarms[0]
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", False)
    monkeypatch.setenv("RENDER", "true")
    assert remote.routing_self_check() == [
        "no remote executor is configured at all"]


class _Cur:
    def __init__(self):
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"provider": "cloudflare"}


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self):
        return self.cur


def test_only_a_local_stamp_on_the_current_lease_can_move():
    conn = _Conn()
    assert dbx.replace_local_execution_provider(conn, 3, 2, "cloudflare") \
        == "cloudflare"
    sql, params = conn.cur.sql[0]
    assert "IN ('', 'local')" in sql and "state = 'running'" in sql
    with pytest.raises(ValueError):
        dbx.replace_local_execution_provider(conn, 3, 2, "local")
    with pytest.raises(ValueError):
        dbx.note_owner_alarm(conn, "anything", "x")
    dbx.note_owner_alarm(conn, "llm_balance", "empty wallet", provider="h")
    assert "INSERT INTO app_kv" in conn.cur.sql[-1][0]


# ── R7: fallbacks only to this release ───────────────────────────────────

def test_version_skew_needs_two_known_versions():
    assert remote.version_skew({"source_version": "a"}, mine="b")
    assert not remote.version_skew({"source_version": "a"}, mine="a")
    assert not remote.version_skew({}, mine="a")
    assert not remote.version_skew({"code_version": "x"}, mine="unknown")


def test_a_fallback_on_other_code_is_refused(monkeypatch):
    monkeypatch.setattr(remote.version, "code_version", lambda: "mine")
    monkeypatch.setattr(remote, "_modal_health_cached",
                        lambda timeout=15: {"source_version": "older"})
    assert not remote.fallback_version_ok("modal")
    monkeypatch.setattr(remote, "_modal_health_cached",
                        lambda timeout=15: {"source_version": "mine"})
    assert remote.fallback_version_ok("modal")

    def down(timeout=15):
        raise RuntimeError("timeout")
    monkeypatch.setattr(remote, "_modal_health_cached", down)
    assert remote.fallback_version_ok("modal")      # unknown is not skew


# ── R5: the LLM balance watch ────────────────────────────────────────────

def test_an_empty_wallet_alarms_the_owner_once_per_window(monkeypatch):
    noted = []

    class Probe:
        def run(self, fn, *args, **kw):
            noted.append((fn, args, kw))

        def reset(self):
            pass
    monkeypatch.setattr(dbx, "Db", Probe)
    monkeypatch.setattr(llm, "_quota_last", {})
    exc = RuntimeError('responses HTTP 429: {"error": {"message": "You have '
                       'no credits remaining", "code": "insufficient_quota"}}')
    assert llm.note_quota_error(exc, "gpt-6-luna", "https://api.openai.com/v1")
    assert llm.note_quota_error(exc, "gpt-6-luna", "https://api.openai.com/v1")
    assert len(noted) == 1
    fn, args, kw = noted[0]
    assert fn is dbx.note_owner_alarm and args[0] == "llm_balance"
    assert "api.openai.com" in args[1] and kw["provider"] == "api.openai.com"
    assert not llm.note_quota_error(RuntimeError("HTTP 429 rate limit"))


def test_paying_users_fail_over_only_to_an_explicit_second_wallet(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-primary")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config, "PAID_FAILOVER_BASE_URL", "")
    monkeypatch.setattr(config, "PAID_FAILOVER_API_KEY", "")
    monkeypatch.setattr(config, "PAID_FAILOVER_MODEL", "")
    assert [l["name"] for l in llm.paid_editor_lanes("plus_2026")] == \
        ["standard"]
    monkeypatch.setattr(config, "PAID_FAILOVER_BASE_URL",
                        "https://api.anthropic.com/v1/")
    monkeypatch.setattr(config, "PAID_FAILOVER_API_KEY", "sk-second")
    monkeypatch.setattr(config, "PAID_FAILOVER_MODEL", "claude-sonnet-x")
    monkeypatch.setattr(llm, "_paid_failover_client", None)
    lanes = llm.paid_editor_lanes("plus_2026")
    assert [l["name"] for l in lanes] == ["standard", "paid_failover"]
    assert lanes[1]["model"] == "claude-sonnet-x"
    # The same wallet under another name is not a failover.
    monkeypatch.setattr(config, "PAID_FAILOVER_BASE_URL",
                        "https://api.openai.com/v1")
    monkeypatch.setattr(config, "PAID_FAILOVER_API_KEY", "sk-primary")
    assert [l["name"] for l in llm.paid_editor_lanes("plus_2026")] == \
        ["standard"]
