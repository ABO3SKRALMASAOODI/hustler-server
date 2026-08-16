"""One model turn may debit one user balance exactly once."""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


class _SharedLedger:
    def __init__(self, barrier=None):
        self.lock = threading.Lock()
        self.barrier = barrier
        self.ledger = {}
        self.pools = {"daily": 10.0, "bonus": 5.0, "monthly": 20.0}
        self.updates = 0
        self.statements = []


class _Cursor:
    def __init__(self, shared):
        self.shared = shared
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.shared.statements.append(normalized)
        if "FROM llm_calls WHERE job_id" in normalized:
            self._row = {
                "n": 2, "tin": 20_000, "tout": 1_000,
                "token_cost": 0.01, "n_images": 0, "gen_cost": 0,
            }
            return
        if "FROM users WHERE id = %s FOR UPDATE" in normalized:
            with self.shared.lock:
                self._row = {
                    "credits_daily": self.shared.pools["daily"],
                    "credits_bonus": self.shared.pools["bonus"],
                    "credits_monthly": self.shared.pools["monthly"],
                }
            if self.shared.barrier is not None:
                self.shared.barrier.wait(timeout=3)
            return
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self._row = {"pg_advisory_xact_lock": None}
            return
        if normalized.startswith("SELECT credits_used FROM job_credits"):
            key = (params[0], params[1])
            with self.shared.lock:
                amount = self.shared.ledger.get(key)
            self._row = ({"credits_used": amount}
                         if amount is not None else None)
            return
        if normalized.startswith("INSERT INTO job_credits"):
            key = (params[0], params[2])
            amount = float(params[4])
            with self.shared.lock:
                if key in self.shared.ledger:
                    self._row = None
                else:
                    self.shared.ledger[key] = amount
                    self._row = {"credits_used": amount}
            return
        if normalized.startswith("UPDATE users"):
            with self.shared.lock:
                self.shared.pools["daily"] -= float(params[0])
                self.shared.pools["bonus"] -= float(params[1])
                self.shared.pools["monthly"] -= float(params[2])
                self.shared.updates += 1
            self._row = None
            return
        raise AssertionError(normalized)

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, shared):
        self.shared = shared

    def cursor(self):
        return _Cursor(self.shared)


def test_duplicate_charge_returns_existing_claim_without_second_debit():
    shared = _SharedLedger()
    first = db.charge_turn_credits(_Conn(shared), 5, 777)
    second = db.charge_turn_credits(_Conn(shared), 5, 777)

    assert first == second
    assert first > 0
    assert shared.updates == 1
    assert len(shared.ledger) == 1
    assert shared.pools["daily"] == 10.0 - first


def test_two_concurrent_completions_share_one_ledger_claim_and_debit():
    shared = _SharedLedger(barrier=threading.Barrier(2))
    results = []
    errors = []

    def charge():
        try:
            results.append(db.charge_turn_credits(_Conn(shared), 5, 888))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=charge) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2 and results[0] == results[1]
    assert shared.updates == 1
    assert len(shared.ledger) == 1


def test_ledger_claim_statement_precedes_balance_update():
    shared = _SharedLedger()
    db.charge_turn_credits(_Conn(shared), 5, 999)
    claim_at = next(i for i, sql in enumerate(shared.statements)
                    if sql.startswith("INSERT INTO job_credits"))
    debit_at = next(i for i, sql in enumerate(shared.statements)
                    if sql.startswith("UPDATE users"))
    assert claim_at < debit_at


def test_migration_repairs_duplicates_then_enforces_unique_job_turn():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backend", "migrations", "020_job_credit_idempotency.sql")
    sql = open(path, encoding="utf-8").read()
    assert "job_credit_duplicate_rollup" in sql
    assert "SUM(credits_used)" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    assert "ON job_credits (job_id, turn)" in sql
    assert "WHERE job_id LIKE 'video:%'" in sql


class _AccountingCursor:
    def __init__(self, commands):
        self.commands = commands

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.commands.append((" ".join(sql.split()), params))


class _AccountingConn:
    def __init__(self):
        self.commands = []

    def cursor(self):
        return _AccountingCursor(self.commands)


def test_stale_lease_is_fenced_before_any_charge_or_marker(monkeypatch):
    called = []
    monkeypatch.setattr(db, "finish_job", lambda *_args: False)
    monkeypatch.setattr(
        db, "completed_job_lease_matches", lambda *_args: False)
    monkeypatch.setattr(
        db, "charge_turn_credits",
        lambda *_args: called.append("charge"))
    monkeypatch.setattr(
        db, "mark_subscribe_gate_qualified",
        lambda *_args: called.append("qualify"))

    out = db.finish_accounted_job(
        _AccountingConn(), 9, {"status": "replied"}, 3, 8,
        billable=True, qualify_subscribe=True)

    assert out["committed"] is False
    assert called == []


def test_winning_lease_charges_and_marks_inside_terminal_transaction(
        monkeypatch):
    called = []
    monkeypatch.setattr(db, "finish_job", lambda *_args: True)
    monkeypatch.setattr(
        db, "charge_turn_credits",
        lambda *_args: (called.append("charge") or 2.5))
    monkeypatch.setattr(
        db, "mark_subscribe_gate_qualified",
        lambda *_args: (called.append("qualify") or True))
    monkeypatch.setattr(
        db, "patch_done_job_result",
        lambda *_args, **_kwargs: (called.append("patch") or True))
    conn = _AccountingConn()

    out = db.finish_accounted_job(
        conn, 9, {"status": "replied"}, 3, 8,
        billable=True, qualify_subscribe=True)

    assert out == {"committed": True, "charged": 2.5,
                   "billing_error": None, "qualification_error": None}
    assert called == ["charge", "patch", "qualify", "patch"]
    commands = [sql for sql, _params in conn.commands]
    assert commands.index("SAVEPOINT terminal_billing") < \
        commands.index("RELEASE SAVEPOINT terminal_billing")
    assert "RELEASE SAVEPOINT terminal_qualification" in commands


def test_lost_commit_ack_replays_same_done_lease_as_success(monkeypatch):
    called = []
    monkeypatch.setattr(db, "finish_job", lambda *_args: False)
    monkeypatch.setattr(
        db, "completed_job_lease_matches", lambda *_args: True)
    monkeypatch.setattr(
        db, "charge_turn_credits",
        lambda *_args: (called.append("charge") or 1.5))
    monkeypatch.setattr(
        db, "patch_done_job_result",
        lambda *_args, **_kwargs: (called.append("patch") or True))

    out = db.finish_accounted_job(
        _AccountingConn(), 9, {"status": "replied"}, 3, 8,
        billable=True)

    assert out["committed"] is True
    assert out["charged"] == 1.5
    assert called == ["charge", "patch"]


def test_terminal_billing_failure_persists_repair_flag(monkeypatch):
    patches = []
    monkeypatch.setattr(db, "finish_job", lambda *_args: True)
    monkeypatch.setattr(
        db, "charge_turn_credits",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database busy")))
    monkeypatch.setattr(
        db, "patch_done_job_result",
        lambda _conn, _job, patch=None, remove=():
            patches.append((patch or {}, remove)) or True)

    out = db.finish_accounted_job(
        _AccountingConn(), 9, {"status": "replied"}, 3, 8,
        billable=True, extra_credits=2.0)

    assert out["committed"] is True
    assert out["billing_error"] == "database busy"
    assert patches == [({"billing_pending": True,
                         "billing_error": "database busy",
                         "billing_extra_credits": 2.0}, ())]


def test_terminal_qualification_failure_persists_repair_flag(monkeypatch):
    patches = []
    monkeypatch.setattr(db, "finish_job", lambda *_args: True)
    monkeypatch.setattr(
        db, "mark_subscribe_gate_qualified",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("event busy")))
    monkeypatch.setattr(
        db, "patch_done_job_result",
        lambda _conn, _job, patch=None, remove=():
            patches.append((patch or {}, remove)) or True)

    out = db.finish_accounted_job(
        _AccountingConn(), 9, {"status": "replied"}, 3, 8,
        qualify_subscribe=True)

    assert out["committed"] is True
    assert out["qualification_error"] == "event busy"
    assert patches == [({"qualification_pending": True,
                         "qualification_error": "event busy"}, ())]


def test_reaper_repairs_pending_billing_and_qualification(monkeypatch):
    class PendingCursor(_AccountingCursor):
        def fetchall(self):
            return [{"id": 91, "user_id": 8,
                     "result": {"billing_pending": True,
                                "billing_extra_credits": 2.0,
                                "qualification_pending": True}}]

    class PendingConn(_AccountingConn):
        def cursor(self):
            return PendingCursor(self.commands)

    called = []
    monkeypatch.setattr(
        db, "charge_turn_credits",
        lambda *_args: (called.append("charge") or 4.5))
    monkeypatch.setattr(
        db, "mark_subscribe_gate_qualified",
        lambda *_args: (called.append("qualify") or True))
    monkeypatch.setattr(
        db, "patch_done_job_result",
        lambda *_args, **_kwargs: (called.append("patch") or True))

    repaired = db.reconcile_pending_accounting(PendingConn())

    assert repaired == [{"job_id": 91,
                         "fixed": ["billing", "qualification"]}]
    assert called == ["charge", "patch", "qualify", "patch"]


def test_agent_turn_baseline_is_fenced_and_persisted_in_job_payload():
    conn = _AccountingConn()
    # The lightweight cursor does not model rowcount by default.
    cursor = conn.cursor()
    cursor.rowcount = 1
    conn.cursor = lambda: cursor

    assert db.record_agent_turn_baseline(
        conn, 44, 7, 3, "a" * 64) is True
    sql, params = conn.commands[-1]
    assert "turn_baseline_digest" in sql
    assert "turn_baseline_version" in sql
    assert "state = 'running'" in sql and "total_claims = %s" in sql
    assert "AND NOT (COALESCE(payload" in sql
    assert params == ("a" * 64, 3, 44, 7)


class _GateCursor:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.commands.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


class _GateConn:
    def __init__(self, row=None, rowcount=1):
        self.cur = _GateCursor(row, rowcount)

    def cursor(self):
        return self.cur


def test_worker_subscribe_gate_matches_current_turn_delivery_contract():
    conn = _GateConn({"is_subscribed": False, "email": "user@example.com"})

    assert db.subscribe_gate_applies(conn, 81) is True
    sql, params = next((sql, params) for sql, params in conn.cur.commands
                       if "subscribe_gate_qualified" in sql)
    assert "subscribe_gate_qualified" in sql
    assert "j.result->>'edl_changed' = 'true'" in sql
    assert "rendered_clips" in sql
    assert params == (81, 81)
    first_sql, first_params = conn.cur.commands[0]
    assert "FROM users" in first_sql and "FOR UPDATE" in first_sql
    assert first_params == (81,)


def test_gated_saved_prompt_is_retired_without_claiming_it_ran():
    conn = _GateConn(rowcount=1)

    assert db.mark_message_subscribe_gated(conn, 1234) is True
    sql, params = conn.cur.commands[-1]
    assert "UPDATE chat_messages" in sql
    assert "'subscribe_gated', TRUE" in sql
    assert "role = 'user'" in sql
    assert params == (1234,)


class _AutoResumeCursor:
    def __init__(self, gated):
        self.gated = gated
        self.rowcount = 1
        self.row = None
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.commands.append((normalized, params))
        if "SELECT is_subscribed, email FROM users" in normalized:
            self.row = {"is_subscribed": False, "email": "user@example.com"}
        elif "SELECT id FROM users" in normalized:
            self.row = {"id": params[0]}
        elif "SELECT 1 WHERE EXISTS" in normalized:
            self.row = {"?column?": 1} if self.gated else None
        elif "SELECT m.id FROM chat_messages" in normalized:
            self.row = {"id": params[0]}
        elif "SELECT credits_balance FROM users" in normalized:
            self.row = {"credits_balance": 10.0}
        elif normalized.startswith("INSERT INTO video_jobs"):
            self.row = {"id": 901}

    def fetchone(self):
        return self.row


class _AutoResumeConn:
    def __init__(self, gated):
        self.cur = _AutoResumeCursor(gated)

    def cursor(self):
        return self.cur


def test_atomic_auto_resume_gates_and_retires_under_one_account_lock():
    conn = _AutoResumeConn(gated=True)

    out = db.resolve_pending_auto_resume(conn, 9, 10, 11, 12)

    assert out == {"state": "gated", "job_id": None}
    commands = [sql for sql, _params in conn.cur.commands]
    user_lock = next(i for i, sql in enumerate(commands)
                     if "FROM users" in sql and "FOR UPDATE" in sql)
    message_lock = next(i for i, sql in enumerate(commands)
                        if "SELECT m.id FROM chat_messages" in sql)
    retire = next(i for i, sql in enumerate(commands)
                  if sql.startswith("UPDATE chat_messages"))
    assert user_lock < message_lock < retire
    assert not any(sql.startswith("INSERT INTO video_jobs")
                   for sql in commands)


def test_atomic_auto_resume_enqueues_once_before_releasing_account_lock():
    conn = _AutoResumeConn(gated=False)

    out = db.resolve_pending_auto_resume(
        conn, 9, 10, 11, 12, {"direct_short": True})

    assert out == {"state": "enqueued", "job_id": 901}
    insert = next((sql, params) for sql, params in conn.cur.commands
                  if sql.startswith("INSERT INTO video_jobs"))
    payload = insert[1][3].adapted
    assert payload == {"direct_short": True, "message_id": 12,
                       "auto_resumed": True}


def test_qualification_writer_uses_the_same_account_row_lock():
    conn = _AutoResumeConn(gated=False)

    assert db.mark_subscribe_gate_qualified(conn, 11, 99) is True
    commands = [sql for sql, _params in conn.cur.commands]
    assert "FROM users" in commands[0] and "FOR UPDATE" in commands[0]
    assert commands[1].startswith("INSERT INTO client_events")
