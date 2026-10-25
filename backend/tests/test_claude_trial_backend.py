"""Backend reads of the Claude trial budget (migration 040): the one
per-account running total track B's gate reads, and the admin alert drain."""

import claude_trial


class _Cur:
    def __init__(self, tables=True, row=None, pool=None):
        self.tables, self.row, self.pool = tables, row, pool
        self._next = None
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if "to_regclass" in sql:
            self._next = {"a": self.tables, "b": self.tables, "t": self.tables}
        elif "FROM trial_budgets WHERE user_id" in sql:
            self._next = self.row
        elif "FROM trial_budget_days" in sql:
            self._next = self.pool
        else:
            self._next = None

    def fetchone(self):
        return self._next


def test_no_migration_or_no_grant_reads_as_nothing_left():
    assert claude_trial.account_status(_Cur(tables=False), 5)["remaining_usd"] == 0
    status = claude_trial.account_status(_Cur(row=None), 5)
    assert status == {"granted": False, "cap_usd": 0.0, "spent_usd": 0.0,
                      "remaining_usd": 0.0, "exhausted": False}


def test_remaining_is_cap_minus_spent():
    status = claude_trial.account_status(
        _Cur(row={"cap_usd": 1, "spent_usd": 0.31, "exhausted_at": None}), 5)
    assert status["granted"] and not status["exhausted"]
    assert abs(status["remaining_usd"] - 0.69) < 1e-9
    spent = claude_trial.account_status(
        _Cur(row={"cap_usd": 1, "spent_usd": 1.02, "exhausted_at": None}), 5)
    assert spent["exhausted"] and spent["remaining_usd"] == 0


def test_pool_status_reports_percent_of_the_200():
    cur = _Cur(pool={"spent": 100, "month": 40, "today": 2, "grants": 90,
                     "grants_today": 3})
    status = claude_trial.pool_status(cur, 200)
    assert status["pct"] == 50.0 and status["grants_today"] == 3


def test_send_due_never_runs_without_the_tables(monkeypatch):
    class _Conn:
        def cursor(self):
            return _Cur(tables=False)

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(claude_trial, "_connect", lambda: _Conn())
    sent = []
    out = claude_trial.send_due(sender=lambda s, h: sent.append(s) or True)
    assert out["sent"] == 0 and not sent
