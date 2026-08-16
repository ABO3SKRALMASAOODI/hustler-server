"""Terminal billing and durable free-edit qualification regressions."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as dbx  # noqa: E402
import job_completion  # noqa: E402


class _WorkerDb:
    def __init__(self, committed=True):
        self.committed = committed
        self.charges = []
        self.qualifications = []

    def run(self, fn, *args):
        if fn is dbx.finish_accounted_job:
            (_job_id, _result, _lease, user_id, billable, extra,
             qualifies) = args
            if not self.committed:
                return {"committed": False, "charged": None}
            charged = None
            if billable:
                self.charges.append((user_id, _job_id, extra))
                charged = 2.5
            if qualifies:
                self.qualifications.append((user_id, _job_id))
            return {"committed": True, "charged": charged,
                    "billing_error": None, "qualification_error": None}
        if fn is dbx.finish_job:
            return self.committed
        raise AssertionError(fn)


def _job(**overrides):
    job = {"id": 44, "type": "agent_turn", "user_id": 8,
           "project_id": 12}
    job.update(overrides)
    return job


def test_legacy_awaiting_user_result_defaults_to_free():
    worker_db = _WorkerDb()
    result = {"status": "awaiting_user", "outcome": "blocked"}
    assert job_completion.finalize_success(
        worker_db, _job(), result, "lease") is True
    assert worker_db.charges == []
    assert result["credits_charged"] == 0.0


def test_legacy_no_change_reply_defaults_to_free():
    worker_db = _WorkerDb()
    result = {"status": "replied", "outcome": "blocked", "edl_version": 9}
    job_completion.finalize_success(worker_db, _job(), result, "lease")
    assert worker_db.charges == []


def test_ambiguous_legacy_agent_result_defaults_to_free():
    worker_db = _WorkerDb()
    result = {"status": "replied", "edl_version": 9}
    job_completion.finalize_success(worker_db, _job(), result, "lease")
    assert worker_db.charges == []


def test_malformed_false_string_cannot_become_a_charge():
    worker_db = _WorkerDb()
    result = {"status": "replied", "outcome": "fulfilled",
              "billable": "false"}
    job_completion.finalize_success(worker_db, _job(), result, "lease")
    assert worker_db.charges == []


def test_legacy_fulfilled_read_only_answer_remains_billable():
    worker_db = _WorkerDb()
    result = {"status": "replied", "outcome": "fulfilled",
              "edl_version": 1}
    job_completion.finalize_success(worker_db, _job(), result, "lease")
    assert len(worker_db.charges) == 1
    assert result["credits_charged"] == 2.5


def test_committed_real_edit_marks_account_qualification():
    worker_db = _WorkerDb()
    result = {"status": "replied", "outcome": "partial", "edl_version": 2,
              "edl_changed": True, "billable": True}
    job_completion.finalize_success(worker_db, _job(), result, "lease")
    assert worker_db.qualifications == [(8, 44)]


def test_superseded_execution_cannot_mark_account_qualification():
    worker_db = _WorkerDb(committed=False)
    result = {"status": "replied", "outcome": "fulfilled",
              "edl_version": 2, "edl_changed": True, "billable": True}
    assert job_completion.finalize_success(
        worker_db, _job(), result, "stale-lease") is False
    assert worker_db.qualifications == []
    assert worker_db.charges == []


def test_read_only_reply_on_edited_project_does_not_qualify():
    worker_db = _WorkerDb()
    result = {"status": "replied", "outcome": "fulfilled",
              "edl_version": 9, "edl_changed": False, "billable": True}
    assert job_completion.finalize_success(
        worker_db, _job(), result, "lease") is True
    assert len(worker_db.charges) == 1
    assert worker_db.qualifications == []


def test_rendered_shorts_mark_account_qualification():
    worker_db = _WorkerDb()
    result = {"rendered_clips": 2, "billable": False}
    job_completion.finalize_success(
        worker_db, _job(type="shorts_plan"), result, "lease")
    assert worker_db.qualifications == [(8, 44)]


def test_qualification_marker_is_account_scoped_and_idempotent_sql():
    class Cursor:
        rowcount = 1

        def __init__(self):
            self.commands = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            self.commands.append((sql, params))

        def fetchone(self):
            return {"id": 8}

    class Conn:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

    conn = Conn()
    assert dbx.mark_subscribe_gate_qualified(conn, 8, 44) is True
    assert "SELECT %s, NULL, 'subscribe_gate_qualified'" in conn.cur.sql
    assert "WHERE NOT EXISTS" in conn.cur.sql
    assert "ON CONFLICT DO NOTHING" in conn.cur.sql
    assert "FOR UPDATE" in conn.cur.commands[0][0]
    assert conn.cur.params[0] == conn.cur.params[2] == 8
    assert conn.cur.params[1].adapted["source_job_id"] == 44
