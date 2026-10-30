"""R1 chaos (30 Oct 2026): failure injection on the render box's execution
path found these gaps; each test pins the fix.

* A box job is never failed by our own infrastructure: its quiet retries
  are bounded by count, not by the job's age (a 30-min index whose slot
  died at minute 11 spent its attempt; a second loss failed the upload).
* A YouTube link pasted as the main video, walled on the box, tells the
  customer the truth and the one step that works, never the agent's
  find_song instructions or "try a different format like mp4".
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import main as worker_main  # noqa: E402

NOW = datetime.now(timezone.utc)
LOST = ("Box lost the executor during this index (Box container exited "
        "during index). No result was recorded; it is safe to retry.")


def _lost(kind="transient_infrastructure"):
    err = RuntimeError(LOST)
    return failure_policy.attach(
        err, failure_policy.decision_for(err, "index"),
        {"kind": kind, "retryable": True, "max_attempts": 2})


# ── box jobs: quiet retries by count, not age ────────────────────────────

@pytest.mark.parametrize("job_type", ("index", "final", "preview"))
def test_a_box_job_lost_late_in_a_long_run_is_still_retried_quietly(
        job_type):
    job = {"type": job_type, "created_at": NOW - timedelta(minutes=25),
           "payload": {"execution_provider": "box"}}
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 0) == 10
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 2) == 40
    # Still bounded: the count ends it, then the ordinary policy decides.
    assert failure_policy.infra_retry_delay(
        job, NOW.timestamp(), failure_policy.INFRA_RETRY_MAX) is None


def test_the_box_window_is_hours_not_forever():
    job = {"type": "index", "payload": {"execution_provider": "box"},
           "created_at": NOW - timedelta(
               seconds=failure_policy.BOX_INFRA_RETRY_WINDOW_S + 5)}
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 0) is None


def test_the_dispatch_target_alone_marks_a_box_job():
    job = {"type": "final", "created_at": NOW - timedelta(minutes=40),
           "_executor_target": "box", "payload": {}}
    assert failure_policy.infra_retry_delay(job, NOW.timestamp(), 1) == 20


def test_other_providers_keep_the_ten_minute_window():
    for provider in ("cloudflare", "modal", ""):
        job = {"type": "index", "created_at": NOW - timedelta(minutes=11),
               "payload": {"execution_provider": provider}}
        assert failure_policy.infra_retry_delay(
            job, NOW.timestamp(), 0) is None


class _QueueDb:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kw):
        self.calls.append((fn, args, kw))
        if fn is dbx.defer_unlaunched_cloudflare_busy:
            return True
        return None


def test_a_box_index_whose_slot_died_at_minute_eleven_goes_back_unspent(
        monkeypatch):
    """The live run: job 125402's slot was killed (docker kill) mid-index;
    at 11+ minutes of age it used to fall to the ordinary policy, which
    spends the attempt (and fails the upload on the next loss)."""
    waits = []
    monkeypatch.setattr(worker_main.DRAINING, "wait", waits.append)
    db = _QueueDb()
    job = {"id": 125402, "type": "index",
           "created_at": datetime.now(timezone.utc) - timedelta(minutes=11),
           "payload": {"execution_provider": "box"}}
    err = _lost()
    decision = failure_policy.decision_for(err, "index")
    assert failure_policy.is_infrastructure(err, decision)
    assert worker_main._quiet_infra_retry(db, job, err, decision, 2)
    assert waits == [10]
    fn, args, kw = db.calls[0]
    assert fn is dbx.defer_unlaunched_cloudflare_busy
    assert args[:2] == (125402, 2)
    assert kw["counter"] == "infra_retry_deferrals"


def test_a_mutating_box_call_with_an_unknown_outcome_is_never_replayed(
        monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _QueueDb()
    err = RuntimeError("Box lost the executor during this mcp_tool ... Its "
                       "outcome is unknown")
    err = failure_policy.attach(err, failure_policy.decision_for(err),
                                {"kind": "outcome_unknown",
                                 "retryable": False, "max_attempts": 0})
    job = {"id": 9, "type": "mcp_tool", "created_at": NOW,
           "payload": {"execution_provider": "box", "mutation": True}}
    assert not worker_main._quiet_infra_retry(
        db, job, err, failure_policy.decision_for(err), 1)
    assert db.calls == []


# ── a walled YouTube link pasted as the main video ───────────────────────

class _ChatDb:
    def __init__(self):
        self.notes = []

    def run(self, fn, *args):
        if fn is worker_main.dbx.get_project:
            return {"chat_session_id": 7}
        if fn is worker_main.dbx.add_job_failure_message:
            self.notes.append(args[1])
            return None
        raise AssertionError(fn)


WALL = ("YouTube blocked THIS upload from our server (\"sign in to confirm "
        "you're not a bot\") — a per-video check on our datacenter IP, not a "
        "problem with the link. If this link came from find_song, fetch the "
        "NEXT candidate now (different uploads are challenged at different "
        "rates; official/popular ones usually pass). Only after the "
        "candidates are exhausted, tell the user: the link plays fine in a "
        "browser, so they can download the file and attach it with the "
        "paperclip in chat (mp3/wav/m4a for audio, mp4/mov for video).")


def test_a_walled_youtube_link_import_tells_the_customer_what_works():
    db = _ChatDb()
    err = dbx.PermanentJobError(
        f"Couldn't import the video from that link: {WALL}")
    worker_main._notify_failure(
        db, {"id": 1, "type": "index", "project_id": 2,
             "payload": {"asset_id": 3}}, err)
    assert db.notes == [worker_main.LINK_IMPORT_WALL_NOTE]
    note = db.notes[0]
    assert "YouTube blocked" in note and "upload the file" in note
    for fragment in ("find_song", "candidate", "paperclip", "datacenter",
                     "different format", "rendered as it stands"):
        assert fragment not in note


def test_any_other_link_import_failure_says_link_not_format():
    db = _ChatDb()
    err = dbx.PermanentJobError(
        "Couldn't import the video from that link: ERROR: [vimeo] 123: "
        "This video is private https://vimeo.com/123?token=abc")
    worker_main._notify_failure(
        db, {"id": 1, "type": "index", "project_id": 2,
             "payload": {"asset_id": 3}}, err)
    assert db.notes == [worker_main.LINK_IMPORT_FAIL_NOTE]
    assert "vimeo.com" not in db.notes[0] and "token" not in db.notes[0]


def test_an_ordinary_index_failure_keeps_its_note():
    db = _ChatDb()
    worker_main._notify_failure(
        db, {"id": 1, "type": "index", "project_id": 2,
             "payload": {"asset_id": 3}},
        RuntimeError("Video is 4.5h — the limit is 3h"))
    assert "hours" in db.notes[0]
    assert db.notes[0] not in (worker_main.LINK_IMPORT_WALL_NOTE,
                               worker_main.LINK_IMPORT_FAIL_NOTE)


# ── a box job short of scratch waits for the shared disk ─────────────────

import config  # noqa: E402
import remote  # noqa: E402
import box_routing  # noqa: E402

SCRATCH = ("this job needs about {need} GB of scratch space in /tmp/valmera "
           "and only 21.4 GB is free. On Cloud Run that space is RAM: raise "
           "the executor's --memory (32Gi is the maximum) or mount a volume "
           "for WORKER_TMP_DIR. See worker/DEPLOY_EXECUTOR.md.")


def _scratch_failure(need):
    err = remote.CloudflareTerminalFailure(SCRATCH.format(need=need))
    err.failure_kind, err.retryable, err.max_attempts = (
        "executor_capacity", False, 0)
    return err


@pytest.fixture
def _box_path(monkeypatch):
    box_routing.set_routing_for_tests({
        "enabled": True, "url": "https://box.example",
        "owner_user_ids": [60], "stages": {"owner": True}})
    sleeps = []
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "_require_current_lease",
                        lambda job, what: None)
    yield sleeps
    box_routing.set_routing_for_tests({})


@pytest.mark.parametrize("jtype", ("final", "index", "preview"))
def test_a_box_job_short_of_scratch_waits_in_the_queue(monkeypatch,
                                                       _box_path, jtype):
    calls = []

    def run(job):
        calls.append(job["type"])
        raise _scratch_failure("31.0")
    monkeypatch.setattr(remote, "_run_cloudflare", run)
    job = {"id": 77, "type": jtype, "project_id": 5, "user_id": 60,
           "attempts": 1, "total_claims": 3,
           "payload": {"execution_provider": "box"}}
    with pytest.raises(remote.BoxWaiting) as caught:
        remote._run_box_with_wait(job)
    assert calls == [jtype]               # never re-launched in this claim
    assert "nothing ran" in str(caught.value)
    assert 20.0 <= _box_path[0] <= 40.0   # a short hold before the queue


def test_a_need_no_box_can_meet_is_still_the_jobs_failure(monkeypatch,
                                                          _box_path):
    monkeypatch.setattr(remote, "_run_cloudflare", lambda job: (_ for _ in
                        ()).throw(_scratch_failure(
                            f"{config.BOX_SCRATCH_MAX_GB + 50:.1f}")))
    job = {"id": 78, "type": "final", "project_id": 5, "user_id": 60,
           "attempts": 1, "total_claims": 1,
           "payload": {"execution_provider": "box"}}
    with pytest.raises(remote.CloudflareTerminalFailure):
        remote._run_box_with_wait(job)


def test_scratch_wait_never_replays_a_mutation_or_a_child():
    err = _scratch_failure("12.0")
    assert not remote._box_scratch_wait({"type": "mcp_tool"}, err)
    assert not remote._box_scratch_wait({"type": "agent_turn"}, err)
    assert remote._box_scratch_wait({"type": "final"}, err)
    other = remote.CloudflareTerminalFailure("Invalid argument")
    other.failure_kind = "executor_capacity"
    assert not remote._box_scratch_wait({"type": "final"}, other)


# ── S7: the Render dispatcher restarted mid-job (the guardian takes over) ─
# The SIGTERM path keeps a remote-owned box call ("preserving durable remote
# job(s)", main._on_shutdown) and hands back only unlaunched claims; the new
# dispatcher's guardian then observes each box ledger row it inherited.

from datetime import datetime as _dt  # noqa: E402


class _GuardianDb:
    def __init__(self, job_state="running"):
        self.calls = []
        self.job_state = job_state

    def run(self, fn, *args, **_kwargs):
        self.calls.append(fn)
        if fn is dbx.get_job:
            return {"state": self.job_state}
        if fn in (dbx.finish_remote_execution, dbx.defer_box_wait,
                  dbx.requeue_job, dbx.finish_job,
                  dbx.heartbeat_remote_execution):
            return True
        return None


def _box_row(jtype="final", ago=600):
    now = _dt.now(timezone.utc)
    return {"provider": "box", "call_id": f"bx-{jtype}-0123456789abcdef0123",
            "function_name": "batch", "job_id": 125413, "total_claims": 1,
            "type": jtype, "project_id": 5122, "user_id": 60, "attempts": 1,
            "payload": {"execution_provider": "box"},
            "submitted_at": now - timedelta(seconds=ago)}


def _status(monkeypatch, body):
    monkeypatch.setattr(remote, "check_executor_version",
                        lambda quiet=True: "")
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: body)


def test_s7_a_call_refused_in_the_queue_after_its_dispatcher_died_waits(
        monkeypatch):
    now = _dt.now(timezone.utc)
    _status(monkeypatch, {
        "status": "refused",
        "updatedAt": (now - timedelta(seconds=300)).isoformat()
        .replace("+00:00", "Z"),
        "envelope": {"error": "Box is at capacity: no free batch slot "
                     "within 150s; nothing ran", "safe_to_fallback": True,
                     "box_wait": True, "capacity_unavailable": True,
                     "failure": {"kind": "provider_capacity_unavailable",
                                 "retryable": True, "max_attempts": 2}}})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _box_row())
    assert event["status"] == "requeued"
    assert dbx.defer_box_wait in db.calls          # unspent, box rules
    assert dbx.finish_job not in db.calls and dbx.requeue_job not in db.calls


def test_s7_a_call_still_running_on_the_box_is_left_alone(monkeypatch):
    _status(monkeypatch, {"status": "running"})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _box_row())
    assert event["status"] == "running"
    assert db.calls == [dbx.heartbeat_remote_execution]   # never a 2nd run


def test_s7_a_call_the_executor_finished_is_closed_as_done(monkeypatch):
    _status(monkeypatch, {"status": "done", "envelope": {
        "job_completed": True, "result": {"render_asset_id": 37871}}})
    db = _GuardianDb(job_state="done")
    event = remote.reconcile_remote_execution(db, _box_row())
    assert event["status"] == "done"
    assert dbx.finish_remote_execution in db.calls
    assert dbx.finish_job not in db.calls and dbx.requeue_job not in db.calls


def test_s7_a_slot_lost_while_no_dispatcher_watched_is_retried(monkeypatch):
    _status(monkeypatch, {"status": "failed", "envelope": {
        "error": LOST, "retryable": True,
        "failure": {"kind": "transient_infrastructure", "retryable": True,
                    "max_attempts": 2}}})
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _box_row("index"))
    assert event["status"] == "requeued"
    assert dbx.finish_job not in db.calls


def test_s7_an_unreachable_box_is_observed_again_never_failed(monkeypatch):
    def down(*_a, **_k):
        raise remote.requests.ConnectionError("box unreachable")
    monkeypatch.setattr(remote, "_cloudflare_status", down)
    db = _GuardianDb()
    event = remote.reconcile_remote_execution(db, _box_row())
    assert event["status"] == "unknown"
    assert db.calls == []


# ── S4: the database unreachable from the box for a minute ───────────────

import psycopg2  # noqa: E402
import executor_runtime  # noqa: E402


def test_a_database_link_error_is_infrastructure_not_the_edit():
    for err in (psycopg2.OperationalError("server closed the connection "
                                          "unexpectedly"),
                psycopg2.InterfaceError("connection already closed"),
                psycopg2.OperationalError("connection to server at \"db\" "
                                          "(10.0.0.1), port 5432 failed: "
                                          "timeout expired")):
        decision = failure_policy.classify(err, "final")
        assert decision.kind == "transient_infrastructure"
        assert decision.retryable
        assert failure_policy.is_infrastructure(err, decision)
    # The same words relayed as text by an executor envelope.
    relayed = RuntimeError("SSL SYSCALL error: EOF detected")
    assert failure_policy.classify(relayed, "preview").kind == \
        "transient_infrastructure"


class _FlakyCommit:
    def __init__(self, fails, landed=False):
        self.fails, self.landed, self.calls = fails, landed, 0

    def __call__(self, db, job, result, claim):
        self.calls += 1
        if self.calls <= self.fails:
            raise psycopg2.OperationalError("server closed the connection "
                                            "unexpectedly")
        return False if self.landed else True


class _JobRow:
    def __init__(self, row):
        self.row = row

    def run(self, fn, *args, **kw):
        assert fn is dbx.get_job
        return self.row


def test_a_finished_job_rides_out_a_database_blip(monkeypatch):
    flaky = _FlakyCommit(fails=3)
    monkeypatch.setattr(executor_runtime.job_completion, "finalize_success",
                        flaky)
    waits = []
    assert executor_runtime._commit_result(
        _JobRow({}), {"id": 1}, {"ok": True}, 2, sleep=waits.append) is True
    assert flaky.calls == 4 and waits == [5, 10, 20]


def test_a_commit_whose_answer_was_lost_is_read_back_not_superseded(
        monkeypatch):
    flaky = _FlakyCommit(fails=1, landed=True)
    monkeypatch.setattr(executor_runtime.job_completion, "finalize_success",
                        flaky)
    ok = _JobRow({"state": "done", "total_claims": 2})
    assert executor_runtime._commit_result(
        ok, {"id": 1}, {}, 2, sleep=lambda s: None) is True
    # Another claim's row is a real supersession.
    flaky.calls = 0
    other = _JobRow({"state": "running", "total_claims": 3})
    assert executor_runtime._commit_result(
        other, {"id": 1}, {}, 2, sleep=lambda s: None) is False


def test_a_link_down_for_good_still_raises(monkeypatch):
    monkeypatch.setattr(executor_runtime.job_completion, "finalize_success",
                        _FlakyCommit(fails=99))
    with pytest.raises(psycopg2.OperationalError):
        executor_runtime._commit_result(_JobRow({}), {"id": 1}, {}, 2,
                                        sleep=lambda s: None)
    assert sum(executor_runtime.COMMIT_RETRY_WAITS_S) <= 120
