"""Deterministic failures must never tell a user to repeat the same action."""

import main as worker_main


class _FakeDb:
    def __init__(self):
        self.messages = []
        self.notified_jobs = set()

    def run(self, fn, *args):
        if fn is worker_main.dbx.get_project:
            return {"chat_session_id": 77}
        if fn is worker_main.dbx.add_job_failure_message:
            session_id, content, job_id, *kind = args
            if job_id not in self.notified_jobs:
                self.notified_jobs.add(job_id)
                error_kind = kind[0] if kind else "job_failed"
                self.messages.append(
                    (session_id, "assistant", content,
                     {"error": error_kind, "job": job_id}))
            return None
        raise AssertionError(f"unexpected DB helper: {fn}")


def _failure_note(error):
    fake = _FakeDb()
    worker_main._notify_failure(
        fake,
        {"id": 9, "type": "shorts_plan", "project_id": 3},
        RuntimeError(error),
    )
    assert len(fake.messages) == 1
    return fake.messages[0][2]


def test_short_source_failure_has_a_non_looping_recovery():
    note = _failure_note("shorts need a longer source")
    assert "Edit it directly here" in note
    assert "Make shorts again" in note


def test_thin_transcript_failure_points_to_a_specific_chat_edit():
    note = _failure_note("no clip-worthy moments")
    assert "build one short" in note
    assert "specific idea" in note


def test_reconstructed_capacity_failure_is_notified_once():
    fake = _FakeDb()
    job = {"id": 91, "type": "agent_turn", "project_id": 3,
           "payload": {}}

    worker_main._notify_failure(
        fake, job, RuntimeError("Cloudflare Container shard is busy"))
    worker_main._notify_failure(
        fake, job, RuntimeError("Cloudflare Container shard is busy"))

    assert len(fake.messages) == 1
    assert "send Continue" in fake.messages[0][2]
    assert fake.messages[0][3] == {"error": "job_failed", "job": 91}


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows=None):
        self.cur = _Cursor(rows)

    def cursor(self):
        return self.cur


def test_failure_message_insert_is_transaction_locked_and_idempotent():
    conn = _Conn([{"id": 42}])

    assert worker_main.dbx.add_job_failure_message(
        conn, 7, "safe note", 91) == 42
    sql, params = conn.cur.sql[0]
    assert "pg_advisory_xact_lock" in sql
    assert "WHERE NOT EXISTS" in sql
    assert "IN ('job_failed', 'job_died')" in sql
    assert "existing.meta->>'job' = %s" in sql
    assert params[0] == params[-1] == "91"


def test_reaper_scan_is_recent_bounded_and_suppresses_stale_agent_notes():
    row = {"id": 91, "type": "agent_turn"}
    conn = _Conn([row])

    assert worker_main.dbx.unnotified_terminal_failures(conn, limit=900) == [row]
    sql, params = conn.cur.sql[0]
    assert "INTERVAL '24 hours'" in sql
    assert "newer.role = 'user'" in sql
    assert "answered.role = 'assistant'" in sql
    assert "notified.meta->>'job' = j.id::text" in sql
    assert "newer_agent.type = 'agent_turn'" in sql
    assert params == (500,)
