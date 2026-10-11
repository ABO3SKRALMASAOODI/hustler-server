"""Daily billing snapshots (migration 032): true MRR history, never rebuilt."""
from datetime import datetime, timezone

import billing_sync


class Cur:
    def __init__(self, table=True, users=()):
        self.table, self.users = table, list(users)
        self.sql, self.params = [], []
        self._next = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self.params.append(params)
        if "to_regclass" in flat:
            self._next = [{"t": "billing_daily_status" if self.table else None}]
        elif "FROM users u" in flat:
            self._next = self.users
        else:
            self._next = []

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return self._next

    def close(self):
        pass


class Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_snapshot_is_a_no_op_until_the_table_exists():
    cur = Cur(table=False)
    out = billing_sync.snapshot_daily(Conn(cur))
    assert out == {"skipped": "migration 032 not applied"}
    assert not any("INSERT" in s for s in cur.sql)


def test_snapshot_upserts_one_row_per_customer_per_dubai_day():
    users = [{"id": 1, "billing_status": "active", "plan": "ai",
              "period": "monthly", "paying": True},
             {"id": 2, "billing_status": "active", "plan": "ai",
              "period": "yearly", "paying": True},
             {"id": 3, "billing_status": "canceled", "plan": "ai_pro",
              "period": "monthly", "paying": False}]
    cur = Cur(users=users)
    conn = Conn(cur)
    # 21:30 UTC on 10 Oct is already 11 Oct in Dubai.
    out = billing_sync.snapshot_daily(
        conn, now=datetime(2026, 10, 10, 21, 30, tzinfo=timezone.utc))
    assert out == {"day": "2026-10-11", "rows": 3}
    inserts = [p for s, p in zip(cur.sql, cur.params) if "INSERT" in s]
    assert [p[6] for p in inserts] == [1500, 1250, 0]       # cents per month
    assert all(str(p[0]) == "2026-10-11" for p in inserts)
    upsert = next(s for s in cur.sql if "INSERT" in s)
    assert "ON CONFLICT (day, user_id) DO UPDATE" in upsert
    selected = next(s for s in cur.sql if "FROM users u" in s)
    assert "is_verified = 1" in selected and "thevalmera" in selected
    assert conn.commits == 1


def test_billing_tick_takes_the_snapshot_after_reconciling(monkeypatch):
    calls = []
    monkeypatch.setattr(billing_sync, "reconcile_all",
                        lambda conn: calls.append("reconcile") or {})
    monkeypatch.setattr(billing_sync, "run_dunning",
                        lambda conn: calls.append("dunning") or {})
    monkeypatch.setattr(billing_sync, "snapshot_daily",
                        lambda conn: calls.append("snapshot") or {})
    monkeypatch.setattr(billing_sync.billing, "columns_ready", lambda conn: True)

    class LockCur(Cur):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "pg_try_advisory_lock" in sql:
                self._next = [{"got": True}]
    out = billing_sync.run_billing_tick(conn=Conn(LockCur()))
    assert calls == ["reconcile", "dunning", "snapshot"]
    assert "snapshot" in out
