"""P0 #861: a request a gate refused runs once the gate's reason is gone.

Account 2869's request (chat message 170081) was refused by the trial gate
at index time because analysis had used up its allowance. 2c288ef9 made the
allowance whole, but nothing re-ran the request: the customer stayed on the
plan cards. gate_lift runs it from the server, exactly once."""
import json

import pytest

import db
import gate_lift
import indexer


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._row = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))
        self._row, self._rows = self.conn.answer(sql, params)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    """Answers the statements gate_lift makes; records every one."""

    def __init__(self, *, lifted=False, busy=False, lock=True, cands=(),
                 lifted_before=False):
        self.sql = []
        self.lifted, self.busy, self.lock = lifted, busy, lock
        self.lifted_before = lifted_before
        self.cands = list(cands)

    def cursor(self):
        return _Cur(self)

    def answer(self, sql, params):
        s = " ".join(sql.split())
        if "pg_try_advisory_xact_lock" in s:
            return {"ok": self.lock}, []
        if s.startswith("WITH refusals"):
            return None, self.cands
        if "FOR UPDATE" in s and "gate_lifted_at" in s:
            return (None if self.lifted else {"id": 170085}), []
        if s.startswith("SELECT 1 FROM video_jobs") and "gate_lifted" in s:
            return ({"x": 1} if self.lifted_before else None), []
        if s.startswith("SELECT 1 FROM video_jobs"):
            return ({"x": 1} if self.busy else None), []
        return None, []

    def updates(self):
        return [(s, p) for s, p in self.sql if s.startswith("UPDATE")]


CAND = {"reply_id": 170085, "session_id": 5648, "message_id": 170081,
        "project_id": 5543, "user_id": 2869}


@pytest.fixture
def lane(monkeypatch):
    """A never-paid account on its trial project; ``state`` is what the
    shared admission answers now."""
    seen = {"state": ("enqueue", {"trial_lane": True}), "jobs": [],
            "gated_marks": [], "events": []}
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: True)
    def admission(conn, p, u, gated=None, lane_here=True):
        seen["lane_here"] = lane_here
        return seen["state"]
    monkeypatch.setattr(db, "saved_request_admission", admission)
    monkeypatch.setattr(db, "enqueue_job",
                        lambda conn, p, u, t, payload:
                        seen["jobs"].append((p, u, t, payload)) or 137999)
    monkeypatch.setattr(db, "mark_message_subscribe_gated",
                        lambda conn, mid: seen["gated_marks"].append(mid))
    monkeypatch.setattr(db, "record_client_event",
                        lambda conn, u, p, kind, detail=None:
                        seen["events"].append((kind, detail)))
    return seen


def test_a_refused_request_runs_once_the_trial_budget_is_back(lane):
    conn = _Conn()
    out = gate_lift.resume_one(conn, CAND)
    assert out == {"state": "enqueued", "job_id": 137999, "trial": True}
    assert lane["jobs"] == [(5543, 2869, "agent_turn", {
        "message_id": 170081, "auto_resumed": True, "gate_lifted": True,
        "gate_reply_id": 170085, "trial_lane": True})]
    # The refusal's plan cards are retired in the same transaction: the
    # flags the Studio draws them from are off, the originals kept.
    (sql, params), = conn.updates()
    assert "superseded_card" in sql and params[1] == 170085
    stamp = params[0].adapted
    assert stamp["gate_lifted_job_id"] == 137999 and stamp["gate_lifted_at"]
    assert stamp["credits_exhausted"] is False
    assert stamp["subscribe_required"] is False
    assert stamp["subscribe_gate"] is False


def test_a_gate_that_still_holds_leaves_the_refusal_untouched(lane):
    for state in (("gated", {}), ("no_credits", {})):
        lane["state"] = state
        conn = _Conn()
        assert gate_lift.resume_one(conn, CAND) == {"state": state[0]}
        assert lane["jobs"] == [] and conn.updates() == []
        assert lane["gated_marks"] == []


def test_it_runs_exactly_once(lane):
    # Another worker lifted it between the scan and the lock.
    conn = _Conn(lifted=True)
    assert gate_lift.resume_one(conn, CAND) == {"state": "already_lifted"}
    # A resend / the Studio's replay / a running turn got there first.
    conn2 = _Conn(busy=True)
    assert gate_lift.resume_one(conn2, CAND) == {"state": "busy_or_ran"}
    assert lane["jobs"] == [] and conn.updates() == conn2.updates() == []


def test_the_account_row_is_locked_before_the_reply(lane, monkeypatch):
    order = []
    monkeypatch.setattr(db, "subscribe_gate_applies",
                        lambda conn, uid: order.append("account") or True)
    conn = _Conn()
    real = conn.answer

    def answer(sql, params):
        if "FOR UPDATE" in sql:
            order.append("reply")
        return real(sql, params)
    conn.answer = answer
    gate_lift.resume_one(conn, CAND)
    assert order[:2] == ["account", "reply"]


def test_one_sweeper_at_a_time_and_each_lift_is_recorded(lane):
    assert gate_lift.sweep_once(_Conn(lock=False, cands=[CAND])) == []
    assert lane["jobs"] == []
    out = gate_lift.sweep_once(_Conn(cands=[CAND]))
    assert [r["state"] for _, r in out] == ["enqueued"]
    kind, detail = lane["events"][0]
    assert kind == "blocked_prompt_replayed"
    assert detail["via"] == "gate_lift" and detail["job_id"] == 137999


def test_one_failing_candidate_never_blocks_the_next(lane, monkeypatch):
    calls = []

    def flaky(conn, p, u, t, payload):
        calls.append(payload["message_id"])
        if len(calls) == 1:
            raise RuntimeError("boom")
        return 138000
    monkeypatch.setattr(db, "enqueue_job", flaky)
    other = dict(CAND, reply_id=2, message_id=1, project_id=7)
    conn = _Conn(cands=[CAND, other])
    out = gate_lift.sweep_once(conn)
    assert [r["state"] for _, r in out] == ["error", "enqueued"]
    stmts = [s for s, _ in conn.sql]
    assert "ROLLBACK TO SAVEPOINT gate_lift" in stmts


def test_the_sweep_is_throttled_and_never_raises(monkeypatch):
    class Db:
        n = 0

        def run(self, fn):
            Db.n += 1
            raise RuntimeError("db down")
    gate_lift._next_at[0] = 0.0
    assert gate_lift.sweep(Db(), now=1000.0) is None
    assert gate_lift.sweep(Db(), now=1001.0) is None
    assert Db.n == 1


def test_the_scan_reads_no_customer_words():
    # Machine state only: meta flags, ids and jobs, never what was typed.
    sql = gate_lift.CANDIDATES_SQL
    assert "content" not in sql and "blocked_prompt" not in sql
    assert "gate_lifted_at" in sql and "request_message_id" in sql


def test_the_index_gate_names_the_request_it_refused():
    meta = indexer._subscribe_gate_meta(request_message_id=170081)
    assert meta["request_message_id"] == 170081
    assert meta["subscribe_gate"] is True and meta["credits_exhausted"]
    assert "request_message_id" not in indexer._subscribe_gate_meta()


def test_admission_is_one_answer_for_the_index_and_the_lift(monkeypatch):
    import claude_lane
    import first_cut
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: True)
    monkeypatch.setattr(first_cut, "project_admission",
                        lambda conn, p: {"kind": "trial"})
    monkeypatch.setattr(first_cut, "trial_requests_on",
                        lambda conn, uid=None: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(first_cut, "trial_remaining_usd", lambda conn, u: 0.32)
    assert db.saved_request_admission(None, 5543, 2869) == (
        "enqueue", {"trial_lane": True})
    monkeypatch.setattr(first_cut, "trial_remaining_usd", lambda conn, u: 0.0)
    assert db.saved_request_admission(None, 5543, 2869) == ("gated", {})
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: False)
    monkeypatch.setattr(db, "user_credits_balance", lambda conn, u: 0.5)
    assert db.saved_request_admission(None, 5543, 2869) == ("no_credits", {})
    monkeypatch.setattr(db, "user_credits_balance", lambda conn, u: 40)
    assert db.saved_request_admission(None, 5543, 2869) == ("enqueue", {})


def test_a_request_is_lifted_once_per_lane(lane):
    # The lifted turn met a closed lane and refused again with the same
    # request: the sweep must not start it every minute.
    conn = _Conn(lifted_before=True)
    assert gate_lift.resume_one(conn, CAND) == {"state": "lifted_before"}
    assert lane["jobs"] == [] and conn.updates() == []
    (sql, params), = [(s, p) for s, p in conn.sql if "gate_lifted' =" in s]
    assert params == (5543, "170081", "true")


def test_the_dispatcher_leaves_the_claude_lane_to_the_turn(lane):
    # 2869 at 14:19 UTC: the sweep runs in the Render dispatcher, which holds
    # no Claude key, so a lane check there read every trial request gated.
    gate_lift.resume_one(_Conn(), CAND)
    assert lane["lane_here"] is False


def test_admission_without_the_key_here(monkeypatch):
    import claude_lane
    import first_cut
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: True)
    monkeypatch.setattr(first_cut, "project_admission",
                        lambda conn, p: {"kind": "trial"})
    monkeypatch.setattr(first_cut, "trial_requests_on",
                        lambda conn, uid=None: True)
    monkeypatch.setattr(first_cut, "trial_remaining_usd", lambda conn, u: 0.32)
    monkeypatch.setattr(claude_lane, "configured", lambda: False)
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    assert db.saved_request_admission(None, 5543, 2869, lane_here=False) == (
        "enqueue", {"trial_lane": True})
    # Where the turn runs (the index's auto-resume), the lane is judged.
    assert db.saved_request_admission(None, 5543, 2869) == ("gated", {})
    # A process holding the key judges its own latch either way.
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    assert db.saved_request_admission(None, 5543, 2869, lane_here=False) == (
        "gated", {})
