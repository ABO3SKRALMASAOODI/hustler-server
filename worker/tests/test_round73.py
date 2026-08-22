"""Round 73: the two ways a render burned money for nobody.

Reconstructed from the first Cloud Run invoice — $14 for 7 days, of which
10.93 of 19.81 attributable compute-hours produced NOTHING:

  * final job=836 ran FIVE 50-minute encodes against MAX_ATTEMPTS_MEDIA=3,
    because release_jobs refunds an attempt on every dispatcher SIGTERM and
    there were 50 deploys that week. Four jobs in that shape = 9.37 hours.
  * eleven requests died as Cloud Run 504s. The dispatcher gave up and
    requeued, and the executor kept rendering the dead job to completion
    beside its own retry — two instances, 16 vCPU, one job — because nothing
    ever told it that it had been abandoned.

So: one counter that is never refunded, and one signal that already existed
(set_progress' rowcount) finally being read.
"""
import json
import inspect
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config                                                  # noqa: E402
import db as wdb                                               # noqa: E402
import media                                                   # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


# ------------------------------------------------------------------ #
#  Fakes                                                              #
# ------------------------------------------------------------------ #

class _Cur:
    def __init__(self, sink, rowcount=1, fetchone=None, fetchall=None):
        self.sink, self.rowcount = sink, rowcount
        self._one, self._all = fetchone, fetchall

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.sink.append((sql, params))
    def fetchone(self): return self._one
    def fetchall(self): return self._all or []


class _Conn:
    def __init__(self, rowcount=1, fetchone=None, fetchall=None):
        self.sql = []
        self._rc, self._one, self._all = rowcount, fetchone, fetchall

    def cursor(self):
        return _Cur(self.sql, self._rc, self._one, self._all)

    def rollback(self): pass


def _with_column(ready):
    """Force db.claims_column_ready's cached answer for one test."""
    wdb._CLAIMS_COL["ok"] = bool(ready)
    wdb._CLAIMS_COL["checked_at"] = time.time() if not ready else 0.0
    # These SQL-shape tests exercise the pre-025 fallback unless they opt in
    # explicitly; suppress the migration probe so it is not mistaken for the
    # claim statement under inspection.
    wdb._REMOTE_EXEC_TABLE["ok"] = False
    wdb._REMOTE_EXEC_TABLE["checked_at"] = time.time()


# ------------------------------------------------------------------ #
#  The counter that is never handed back                              #
# ------------------------------------------------------------------ #

def test_the_absolute_ceiling_sits_above_the_refundable_budget():
    """If these ever cross, the backstop becomes the primary limit and a job
    loses retries it was promised — deploys would eat the budget again, just
    from the other end."""
    assert config.MAX_CLAIMS_ABSOLUTE > config.MAX_ATTEMPTS_MEDIA


def test_claim_counts_a_claim_that_release_can_never_refund():
    _with_column(True)
    c = _Conn(fetchone={"id": 7})
    wdb.claim_job(c, ["final"], config.MAX_ATTEMPTS_MEDIA)
    sql, params = c.sql[0]
    assert "total_claims = COALESCE(total_claims, 0) + 1" in sql
    assert "COALESCE(total_claims, 0) < %s" in sql, \
        "claim_job must STOP SELECTING past the ceiling, not merely count"
    assert config.MAX_CLAIMS_ABSOLUTE in params

    # ...and the refund path must not touch it. This is the entire mechanism:
    # job 836 got five lives because the only counter it had was refundable.
    r = _Conn(fetchall=[{"id": 1}])
    wdb.release_jobs(r, [1])
    rel = r.sql[0][0]
    assert "attempts = GREATEST(0, attempts - 1)" in rel, \
        "the deploy refund is deliberate and must survive"
    assert "total_claims" not in rel, \
        "refunding total_claims would restore exactly the bug this fixes"


def test_durable_remote_job_is_not_falsely_heartbeated_or_releaseable():
    """The provider ledger, not an unobserving dispatcher, protects a call."""
    job_id = 730073
    try:
        wdb.track_job(job_id)
        assert wdb.mark_remote_owned(job_id) is True
        assert job_id in wdb.active_job_ids()
        assert job_id in wdb.remote_owned_job_ids()
        assert job_id in wdb.remote_launching_job_ids()
        assert job_id not in wdb.locally_owned_job_ids()
        wdb.remote_launch_recorded(job_id)
        assert job_id not in wdb.remote_launching_job_ids()
    finally:
        wdb.untrack_job(job_id)
    assert job_id not in wdb.remote_owned_job_ids()

    shutdown = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "ids, remote_ids = dbx.begin_remote_shutdown()" in shutdown
    assert "dbx.remote_launching_job_ids()" in shutdown
    heartbeat = inspect.getsource(wdb.heartbeat_forever)
    assert "ACTIVE_JOBS - REMOTE_OWNED_JOBS" in heartbeat


def test_shutdown_and_remote_launch_reservation_are_atomic():
    """A released claim can never be submitted after shutdown wins."""
    first, second = 730074, 730075
    with wdb._ACTIVE_LOCK:
        before_stopped = wdb._REMOTE_LAUNCHES_STOPPED
    try:
        wdb.track_job(first)
        assert wdb.mark_remote_owned(first) is True
        wdb.track_job(second)

        local, remote = wdb.begin_remote_shutdown()

        assert second in local
        assert first in remote
        assert wdb.mark_remote_owned(second) is False
        assert second not in wdb.remote_owned_job_ids()
    finally:
        wdb.untrack_job(first)
        wdb.untrack_job(second)
        with wdb._ACTIVE_LOCK:
            wdb._REMOTE_LAUNCHES_STOPPED = before_stopped


def test_claim_does_not_duplicate_an_unexpired_durable_provider_call():
    _with_column(True)
    wdb._REMOTE_EXEC_TABLE["ok"] = True
    c = _Conn(fetchone={"id": 7})
    wdb.claim_job(c, ["preview"], config.MAX_ATTEMPTS_MEDIA)
    sql, _params = c.sql[0]
    assert "remote_executions remote_live" in sql
    assert "remote_live.total_claims = video_jobs.total_claims" in sql
    assert "remote_live.deadline_at > NOW()" in sql


def test_guardian_never_competes_with_a_fresh_attached_dispatcher():
    wdb._REMOTE_EXEC_TABLE["ok"] = True
    c = _Conn(fetchall=[])

    assert wdb.active_remote_executions(c) == []

    sql, params = c.sql[0]
    assert "r.submitted_at < NOW()" in sql
    assert "make_interval(secs => %s)" in sql
    assert params == (config.REMOTE_GUARDIAN_ATTACH_GRACE_S,)
    assert config.REMOTE_GUARDIAN_ATTACH_GRACE_S >= 30


def test_queue_still_works_before_the_migration_has_run():
    """Prod schema is applied by hand from the Render shell, so there is always
    a window where the deploy is live and the psql is not."""
    _with_column(False)
    c = _Conn(fetchone={"id": 7})
    wdb.claim_job(c, ["final"], config.MAX_ATTEMPTS_MEDIA)
    sql, params = c.sql[0]
    assert "total_claims" not in sql
    assert config.MAX_CLAIMS_ABSOLUTE not in params
    assert "attempts < %s" in sql, "the pre-round-73 budget still applies"


def test_index_claims_fair_share_only_during_cross_project_contention():
    """The SQL both protects another project and preserves solo throughput.

    The outer NOT EXISTS opens every row when no other project is queued;
    otherwise the correlated COUNT admits only this project's oldest N jobs.
    Lower-id queued rows are counted so simultaneous SKIP LOCKED claimers
    cannot all see a zero-running snapshot and take the same project's batch.
    """
    _with_column(True)
    c = _Conn(fetchone={"id": 7})
    wdb.claim_job(c, ["index"], config.MAX_ATTEMPTS_MEDIA)
    sql, params = c.sql[0]
    assert "waiting.project_id <> video_jobs.project_id" in sql
    assert "SELECT COUNT(*) FROM video_jobs ahead" in sql
    assert "ahead.id < video_jobs.id" in sql
    assert config.INDEX_FAIR_SHARE_PER_PROJECT in params


def test_non_index_lanes_do_not_receive_index_fair_share_clause():
    _with_column(True)
    c = _Conn(fetchone={"id": 8})
    wdb.claim_job(c, ["preview", "final"], config.MAX_ATTEMPTS_MEDIA)
    sql, params = c.sql[0]
    assert "video_jobs ahead" not in sql
    assert config.INDEX_FAIR_SHARE_PER_PROJECT not in params


def test_agent_claim_is_serialized_in_postgres_across_processes():
    """Two Render services may poll at once, but one project gets one editor.

    This protection must live inside the claim transaction—not in a Python
    lock—because dedicated agent workers do not share process memory.
    Lower-id queued siblings close the READ COMMITTED/SKIP LOCKED race, and a
    fresh running sibling holds the project until its heartbeat goes stale.
    """
    _with_column(True)
    c = _Conn(fetchone={"id": 9})
    wdb.claim_job(c, ["agent_turn"], config.MAX_ATTEMPTS_AGENT)
    sql, _params = c.sql[0]
    assert "live.project_id = video_jobs.project_id" in sql
    assert "live.type IN ('agent_turn', 'shorts_plan'" in sql
    assert "live.heartbeat_at >= NOW()" in sql
    assert "live.id < video_jobs.id" in sql
    assert "FOR UPDATE OF video_jobs SKIP LOCKED" in sql


def test_agent_continuation_keeps_the_project_lane_ahead_of_followups():
    """A physical slice boundary must not reorder one logical request.

    A follow-up may have the lower id because it arrived while the root was
    running.  The root's continuation is created later, but it still owns the
    editor lane until it replies and retires any instructions it adopted.
    """
    _with_column(True)
    c = _Conn(fetchone={"id": 12})
    wdb.claim_job(c, ["agent_turn"], config.MAX_ATTEMPTS_AGENT)
    sql, _params = c.sql[0]
    assert "logical_turn_continuation" in sql
    assert "continuity priority" in sql
    compact = " ".join(sql.split())
    assert "live.payload->>'logical_turn_continuation'" in compact
    assert "video_jobs.payload->>'logical_turn_continuation'" in compact



def test_a_ceilinged_job_fails_visibly_instead_of_sitting_queued():
    """claim_job stops selecting it, which makes it invisible rather than
    bounded — the studio would spin forever on a job no worker will take."""
    _with_column(True)
    c = _Conn(fetchall=[{"id": 836, "type": "final", "project_id": 170,
                         "error": None, "payload": {}}])
    rows = wdb.fail_ceilinged_jobs(c)
    sql, params = c.sql[0]
    assert "state = 'failed'" in sql
    assert config.MAX_CLAIMS_ABSOLUTE in params
    assert len(rows) == 1, "the reaper needs the row back to post in chat"


def test_the_ceiling_does_not_shoot_the_last_allowed_attempt():
    """The Nth claim SETS total_claims to N, so a bare `>= N` would fail the
    very run it just authorised, mid-render."""
    _with_column(True)
    c = _Conn(fetchall=[])
    wdb.fail_ceilinged_jobs(c)
    sql, _ = c.sql[0]
    assert "heartbeat_at <" in sql, \
        "a live, heartbeating job is doing its last legitimate attempt"
    assert "state = 'queued'" in sql


def test_ceiling_reaper_is_inert_before_the_migration():
    _with_column(False)
    c = _Conn()
    assert wdb.fail_ceilinged_jobs(c) == []
    assert c.sql == [], "must not query a column that does not exist yet"


# ------------------------------------------------------------------ #
#  The abandonment signal that was already being computed              #
# ------------------------------------------------------------------ #

def test_set_progress_reports_whether_the_row_is_still_ours():
    ours = _Conn(rowcount=1)
    assert wdb.set_progress(ours, 42, 89) is True
    gone = _Conn(rowcount=0)
    assert wdb.set_progress(gone, 42, 89) is False, \
        "rowcount 0 IS the abandonment signal; it used to be discarded"


def test_attempts_closes_the_race_the_state_check_cannot():
    """Once the retry has claimed the row it is `running` again, so an orphan
    checking only state would see its own replacement and carry on."""
    c = _Conn(rowcount=1)
    wdb.set_progress(c, 42, 89, 2)
    sql, params = c.sql[0]
    assert "attempts = %s" in sql
    assert "state = 'running'" in sql
    assert 2 in params

    # Unqualified callers (indexer, agent_loop) keep the old shape exactly.
    c2 = _Conn(rowcount=1)
    wdb.set_progress(c2, 42, 89)
    assert "attempts = %s" not in c2.sql[0][0]
    assert "state = 'running'" in c2.sql[0][0], \
        "round-19's clause must survive: it is what makes cancellation stick"


def test_total_claims_is_the_non_refundable_execution_lease():
    """A deploy refunds attempts, so only total_claims uniquely names a run."""
    c = _Conn(rowcount=1)
    wdb.set_progress(c, 42, 89, attempts=2, total_claims=7)
    sql, params = c.sql[0]
    assert "total_claims = %s" in sql
    assert "attempts = %s" not in sql
    assert 7 in params

    done = _Conn(rowcount=1)
    assert wdb.finish_job(done, 42, "done", result={"ok": True},
                          total_claims=7) is True
    assert "total_claims = %s" in done.sql[0][0]

    retried = _Conn(rowcount=1)
    assert wdb.requeue_job(retried, 42, RuntimeError("temporary"),
                           total_claims=7) is True
    assert "total_claims = %s" in retried.sql[0][0]


def test_terminal_result_strips_non_finite_metadata_without_mutating_runner():
    """A finished encode must not retry because ffmpeg measured silence."""
    result = {"audio_qc": {"i": -math.inf, "tp": math.nan},
              "nested": [1.0, math.inf]}
    done = _Conn(rowcount=1)

    assert wdb.finish_job(done, 42, "done", result=result,
                          total_claims=7) is True
    adapted = done.sql[0][1][2].adapted
    assert adapted == {"audio_qc": {"i": None, "tp": None},
                       "nested": [1.0, None]}
    json.dumps(adapted, allow_nan=False)
    assert math.isinf(result["audio_qc"]["i"]), \
        "the persistence guard must not rewrite the runner's live result"


def test_terminal_job_atomically_closes_its_provider_ledger(monkeypatch):
    monkeypatch.setattr(wdb, "remote_executions_table_ready", lambda _conn: True)
    done = _Conn(rowcount=1)

    assert wdb.finish_job(done, 42, "done", result={"ok": True},
                          total_claims=7) is True

    assert len(done.sql) == 2
    ledger_sql, ledger_params = done.sql[1]
    assert "UPDATE remote_executions" in ledger_sql
    assert "state IN ('submitted', 'running')" in ledger_sql
    assert ledger_params == ("done", None, 42, 7)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_cancellation_actually_kills_ffmpeg():
    """The point is the PROCESS dying, not an exception being raised.

    Raising out of progress_cb would unwind the read loop and leave ffmpeg
    running — which is the failure being fixed, not a fix. So cancellation
    hangs off the watchdog thread, the only thing here that calls proc.kill().
    """
    abandoned = [False]

    def _cancelled():
        return abandoned[0]

    # A 600s encode nobody is waiting for. Cancelled ~1s in, it must die in
    # seconds; uncancelled it would run for minutes.
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30",
           "-f", "lavfi", "-i", "anullsrc", "-t", "600",
           "-c:v", "libx264", "-preset", "veryslow",
           "-progress", "pipe:1", "-nostats",
           os.path.join("/tmp", "round73_cancel.mp4")]

    import threading
    threading.Timer(1.0, lambda: abandoned.__setitem__(0, True)).start()

    t0 = time.monotonic()
    with pytest.raises(media.MediaError) as e:
        media.run(cmd, progress_cb=lambda f: None, expected_out_s=600,
                  cancelled_cb=_cancelled)
    elapsed = time.monotonic() - t0

    assert "cancelled" in str(e.value).lower()
    assert elapsed < 30, (
        f"took {elapsed:.1f}s — the watchdog polls every 2s, so an abandoned "
        "encode must stop within seconds, not run to its wall-clock cap")
    try:
        os.remove(os.path.join("/tmp", "round73_cancel.mp4"))
    except OSError:
        pass


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_a_healthy_encode_is_untouched_by_the_new_watchdog():
    """A callback that never fires, and one that throws, must both be
    invisible to a good render — a diagnostic must never fail a job."""
    out = os.path.join("/tmp", "round73_ok.mp4")
    for cb in (lambda: False, lambda: 1 / 0):
        media.run(["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "testsrc=size=320x240:rate=15",
                   "-f", "lavfi", "-i", "anullsrc", "-t", "1",
                   "-c:v", "libx264", "-preset", "ultrafast",
                   "-progress", "pipe:1", "-nostats", out],
                  progress_cb=lambda f: None, expected_out_s=1,
                  cancelled_cb=cb)
        assert os.path.getsize(out) > 0
    os.remove(out)


def test_cancelled_cb_is_optional_everywhere():
    """Every other media.run caller in the tree passes nothing."""
    import inspect
    sig = inspect.signature(media.run)
    assert sig.parameters["cancelled_cb"].default is None
    for fn in (__import__("renderer").render_edl,
               __import__("renderer")._render_canvas_edl):
        assert inspect.signature(fn).parameters["cancelled_cb"].default is None


def test_requeue_cannot_resurrect_a_job_we_no_longer_hold():
    """Round 73 made this reachable: an abandoned executor now RAISES instead
    of rendering a dead job to completion, and process_one's except branch
    requeues on a raise. Without the clause that error walks into the reaper's
    terminal row, sets it `queued` again, and buys the job another 8-vCPU run
    after the user has already been told it died."""
    held = _Conn(rowcount=1)
    assert wdb.requeue_job(held, 7, RuntimeError("boom")) is True
    assert "state = 'running'" in held.sql[0][0]

    gone = _Conn(rowcount=0)
    assert wdb.requeue_job(gone, 7, RuntimeError("boom")) is False


def test_remote_handoff_insert_between_update_and_select_is_pending():
    """READ COMMITTED can reveal the exact insert on the second statement.

    The ownership UPDATE legitimately matched nothing just before the
    dispatcher committed.  Seeing that same active physical call in the
    following SELECT means retry, not supersession.
    """
    class Cursor:
        def __init__(self, row):
            self.row = row
            self.rowcount = 0
            self.fetches = iter([
                {"name": "remote_executions"},
                row,
            ])

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, _sql, _params=None): pass
        def fetchone(self): return next(self.fetches)

    class Conn:
        def __init__(self, row): self.cur = Cursor(row)
        def cursor(self): return self.cur

    exact = {
        "total_claims": 4,
        "provider": "modal",
        "call_id": "fc-owner",
        "state": "submitted",
    }
    assert wdb.confirm_remote_execution_ownership(
        Conn(exact), 42, 4, "modal", "fc-owner") == "pending"

    other = dict(exact, call_id="fc-other")
    status = wdb.confirm_remote_execution_ownership(
        Conn(other), 42, 4, "modal", "fc-owner")
    assert status.startswith("superseded:")
    assert "call_match=False" in status
