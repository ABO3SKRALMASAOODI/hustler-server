"""Harness v2 routing reaches the requests the worker queues itself (16 Nov).

EDIT-CORE golden replay ecv-v2-1116, session p5857, switch mode "on": the
customer's main request ("help me reduce the footage to just under 60
minutes") was typed while the upload was analysed. The index's auto-resume
queued it (agent_turn job 2) through db.enqueue_job, which never applied the
switch the backend's jobs.enqueue applies, so it ran on v1 while the later
turns (jobs 8-10) ran on v2. db.enqueue_job now stamps with the same
harness_route.stamp as the backend (one file, byte for byte in both images).
"""
import json
from pathlib import Path

import pytest

import db
import gate_lift
import harness_route

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parent

# p5857's shape: the held brief is the session's first message.
PROJECT, SESSION, USER, MESSAGE = 5857, 9301, 4410, 170500


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.conn.sql.append(s)
        self._row = self.conn.answer(s, params)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _Conn:
    """Answers the statements of the auto-resume and gate_lift paths and the
    app_kv read; records every statement and every queued job."""

    def __init__(self, kv=None, kv_fails=False):
        self.kv, self.kv_fails = kv, kv_fails
        self.sql, self.jobs = [], []

    def cursor(self):
        return _Cur(self)

    def answer(self, s, params):
        if "FROM app_kv" in s:
            if self.kv_fails:
                raise RuntimeError("canceling statement due to timeout")
            return None if self.kv is None else {"value": json.dumps(self.kv)}
        if s.startswith("INSERT INTO video_jobs"):
            self.jobs.append({"project_id": params[0], "user_id": params[1],
                              "type": params[2],
                              "payload": params[3].adapted})
            return {"id": len(self.jobs) + 1}
        if s.startswith("SELECT m.id FROM chat_messages"):
            return {"id": params[0]}
        if "FOR UPDATE" in s and "gate_lifted_at" in s:
            return {"id": params[0]}
        return None


@pytest.fixture
def admitted(monkeypatch):
    """A subscriber with credits: the saved request may run now."""
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: False)
    monkeypatch.setattr(db, "saved_request_admission",
                        lambda conn, p, u, gated=None, lane_here=True:
                        ("enqueue", {}))


def _resume(conn):
    return db.resolve_pending_auto_resume(conn, PROJECT, SESSION, USER,
                                          MESSAGE)


def test_mode_on_stamps_the_auto_resumed_saved_request(admitted):
    conn = _Conn(kv={"mode": "on"})
    out = _resume(conn)
    assert out["state"] == "enqueued"
    (job,) = conn.jobs
    assert job["type"] == "agent_turn" and job["project_id"] == PROJECT
    assert job["payload"]["message_id"] == MESSAGE
    assert job["payload"]["auto_resumed"] is True
    assert job["payload"]["harness"] == "v2"


def test_no_row_leaves_the_auto_resumed_request_on_v1(admitted):
    conn = _Conn(kv=None)
    assert _resume(conn)["state"] == "enqueued"
    (job,) = conn.jobs
    assert "harness" not in job["payload"]
    assert job["payload"]["auto_resumed"] is True


def test_mode_off_and_unlisted_list_leave_it_on_v1(admitted):
    for kv in ({"mode": "off", "project_ids": [PROJECT]},
               {"mode": "list", "user_ids": [1], "project_ids": [2]},
               {"mode": "on", "surfaces": ["mcp"]}):
        conn = _Conn(kv=kv)
        _resume(conn)
        assert "harness" not in conn.jobs[-1]["payload"], kv


def test_a_listed_project_or_user_is_stamped(admitted):
    for kv in ({"mode": "list", "project_ids": [PROJECT]},
               {"mode": "list", "user_ids": [USER]}):
        conn = _Conn(kv=kv)
        _resume(conn)
        assert conn.jobs[-1]["payload"]["harness"] == "v2", kv


def test_a_lifted_gate_runs_its_request_on_the_switched_harness(admitted):
    cand = {"reply_id": 170501, "session_id": SESSION, "message_id": MESSAGE,
            "project_id": PROJECT, "user_id": USER}
    conn = _Conn(kv={"mode": "on"})
    out = gate_lift.resume_one(conn, cand)
    assert out["state"] == "enqueued"
    (job,) = conn.jobs
    assert job["payload"]["gate_lifted"] is True
    assert job["payload"]["harness"] == "v2"
    conn = _Conn(kv=None)
    gate_lift.resume_one(conn, cand)
    assert "harness" not in conn.jobs[-1]["payload"]


def test_a_stamp_already_on_the_payload_is_never_changed():
    conn = _Conn(kv={"mode": "off"})
    db.enqueue_job(conn, PROJECT, USER, "agent_turn",
                   {"message_id": MESSAGE, "harness": "v2"})
    assert conn.jobs[-1]["payload"]["harness"] == "v2"
    assert not any("app_kv" in s for s in conn.sql)


def test_a_failed_switch_read_still_queues_the_request(admitted):
    """The read sits under a savepoint inside the resume's transaction: a
    failed read rolls back to it and the job is queued, on v1."""
    conn = _Conn(kv={"mode": "on"}, kv_fails=True)
    assert _resume(conn)["state"] == "enqueued"
    assert "harness" not in conn.jobs[-1]["payload"]
    kinds = [k for s in conn.sql
             for k in ("SAVEPOINT", "ROLLBACK TO SAVEPOINT",
                       "RELEASE SAVEPOINT", "INSERT INTO video_jobs")
             if s.startswith(k)]
    assert kinds == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT",
                     "INSERT INTO video_jobs"]


def test_other_job_types_never_read_the_switch():
    conn = _Conn(kv={"mode": "on"})
    for job_type in ("index", "preview", "final", "filmstrip"):
        db.enqueue_job(conn, PROJECT, USER, job_type, {})
        assert "harness" not in conn.jobs[-1]["payload"]
    assert not any("app_kv" in s for s in conn.sql)


def test_the_twins_are_identical():
    assert (WORKER / "harness_route.py").read_bytes() == \
        (ROOT / "backend" / "video_services" / "harness_route.py").read_bytes(), \
        "edit backend/video_services/harness_route.py, copy it to worker/"
    assert db.harness_route is harness_route
