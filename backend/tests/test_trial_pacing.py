"""Adaptive pacing of the trial's Claude credit (trial_pacing.py, 31 Oct 2026).

daily_usd_cap = (credit remaining - safety margin) / days remaining;
per_account_usd_cap = daily / expected new trial accounts a day, clamped to
[$0.12, $0.40]; a busy day lowers it, quiet days raise it; the trial never
runs out before the cycle ends; every change is logged and written only into
app_kv.free_result.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import trial_pacing as tp
import trial_toggle

END = datetime(2026, 11, 8, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
ON = datetime(2026, 10, 30, 19, 50, tzinfo=timezone.utc)


def test_daily_cap_is_the_usable_credit_over_the_days_left():
    p = tp.compute(100.84, 10, 2.0, END, NOW, 0, 0, ON)
    days = (END - NOW).total_seconds() / 86400
    assert p["days_left"] == round(days, 3)
    # rounded DOWN to the cent: pacing never rounds the credit up
    assert p["daily_usd_cap"] == int((100.84 - 2.0 - 10) / days * 100) / 100
    assert p["usable_usd"] == pytest.approx(88.84)


def test_the_same_inputs_give_the_same_numbers():
    a = tp.compute(100.84, 10, 3.21, END, NOW, 9, 9, ON)
    b = tp.compute(100.84, 10, 3.21, END, NOW, 9, 9, ON)
    assert a == b


def test_per_account_cap_follows_the_signup_rate_between_floor_and_ceiling():
    # quiet: 2 accounts a day -> daily/2 is far above the ceiling
    quiet = tp.compute(100, 10, 0, END, NOW - timedelta(days=3), 2, 6)
    assert quiet["per_account_usd_cap"] == 0.40 and quiet["bound"] == "ceiling"
    # 40 accounts a day on ~$11.25/day -> $0.28 each, inside the band
    mid = tp.compute(100, 10, 0, END, NOW - timedelta(days=3) + timedelta(hours=8), 40, 40)
    assert mid["bound"] is None
    assert mid["per_account_usd_cap"] == int(mid["daily_usd_cap"] / 40 * 100) / 100
    # a flood: 300 a day -> the $0.12 floor (the daily cap stops grants first)
    busy = tp.compute(100, 10, 0, END, NOW - timedelta(days=3), 300, 300)
    assert busy["per_account_usd_cap"] == 0.12 and busy["bound"] == "floor"
    assert "floor" in busy["why"] and "daily limit" in busy["why"]


def test_a_busy_day_lowers_the_cap_and_quiet_days_raise_it_again():
    long_ago = NOW - timedelta(days=10)
    base = tp.compute(100, 10, 0, END, NOW, 40, 90, long_ago)
    busier = tp.compute(100, 10, 0, END, NOW, 70, 120, long_ago)
    assert base["bound"] is None and busier["bound"] is None
    assert busier["expected_accounts_per_day"] > base["expected_accounts_per_day"]
    assert busier["per_account_usd_cap"] < base["per_account_usd_cap"]
    # two quiet days later the 72 h window has decayed: the cap rises
    later = NOW + timedelta(days=2)
    quiet = tp.compute(100, 10, 0, END, later, 10, 60, long_ago)
    assert quiet["per_account_usd_cap"] > busier["per_account_usd_cap"]


def test_the_rate_counts_only_the_hours_since_the_trial_went_on():
    # 9 grants in the 12 h since the switch = 18 a day, not 9/24h or 9/72h
    p = tp.compute(100.84, 10, 1.5, END, ON + timedelta(hours=12), 9, 9, ON)
    assert p["expected_accounts_per_day"] == 18.0
    # and never over less than MIN_WINDOW_H hours (one early signup is not 24/day)
    early = tp.compute(100.84, 10, 0, END, ON + timedelta(minutes=30), 1, 1, ON)
    assert early["expected_accounts_per_day"] == 24.0 / tp.MIN_WINDOW_H


def test_the_last_day_may_spend_what_is_left_and_no_more():
    p = tp.compute(100, 10, 80, END, END - timedelta(hours=5), 5, 20)
    assert p["days_left"] == 1.0
    assert p["daily_usd_cap"] == 10.0
    gone = tp.compute(100, 10, 95, END, END - timedelta(hours=5), 5, 20)
    assert gone["daily_usd_cap"] == 0.0


@pytest.mark.parametrize("daily_signups", [
    [5] * 9, [60] * 9, [2, 2, 80, 80, 3, 3, 150, 1, 1], [40, 10, 300, 300, 0, 0, 25, 25, 25]])
def test_the_trial_never_runs_out_before_the_cycle_ends(daily_signups):
    """Hour by hour over a whole cycle, every account spends its full cap the
    hour it is granted (the worst case), grants stop at the daily cap like
    worker/claude_trial.grant, and the caps are re-paced every hour: the
    spend never passes the line, and the trial is still granting on the
    last day."""
    budget, margin, spent = 100.84, 10.0, 0.0
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    grants = []                     # (time, usd)
    granted_last_day = 0
    due = 0.0
    t = start
    while t < END:
        day = int((t - start).total_seconds() // 86400)
        due += daily_signups[min(day, len(daily_signups) - 1)] / 24.0
        n, due = int(due), due - int(due)
        d1 = sum(1 for g, _ in grants if g > t - timedelta(hours=24))
        d3 = sum(1 for g, _ in grants if g > t - timedelta(hours=72))
        p = tp.compute(budget, margin, spent, END, t, d1, d3, start)
        today = sum(u for g, u in grants if g.date() == t.date())
        for _ in range(n):
            if today >= p["daily_usd_cap"]:
                break                           # today's paywall, not tomorrow's credit
            grants.append((t, p["per_account_usd_cap"]))
            spent += p["per_account_usd_cap"]
            today += p["per_account_usd_cap"]
            if END - t <= timedelta(days=1):
                granted_last_day += 1
        assert spent <= budget - margin + tp.CEILING_USD + 1e-9
        t += timedelta(hours=1)
    if sum(daily_signups):
        assert granted_last_day > 0


class Conn:
    def __init__(self, flag, credit=None, spent=0.0, grants=(0, 0)):
        self.kv = {"free_result": json.dumps(flag)}
        if credit is not None:
            self.kv["claude_credit"] = json.dumps(credit)
        self.spent, self.grants, self.writes, self.sql = spent, grants, [], []

    def cursor(self):
        return Cur(self)

    def commit(self):
        pass


class Cur:
    def __init__(self, conn):
        self.c, self.row, self.rowcount = conn, None, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.c.sql.append(s)
        if s.startswith("SELECT value, updated_at FROM app_kv"):
            v = self.c.kv.get(params[0])
            self.row = None if v is None else {"value": v, "updated_at": NOW}
        elif s.startswith("SELECT value FROM app_kv"):
            v = self.c.kv.get(params[0])
            self.row = None if v is None else {"value": v}
        elif "pg_advisory_xact_lock" in s:
            self.row = None
        elif "to_regclass('public.trial_budgets') AS t" in s:
            self.row = {"t": "trial_budgets"}
        elif "to_regclass('public.trial_budgets')" in s:
            self.row = {"a": 1, "b": 1}
        elif "information_schema.columns" in s:
            self.row = {"?": 1}
        elif "FROM trial_budget_days" in s:
            self.row = {"usd": self.c.spent}
        elif "COUNT(*) FILTER (WHERE granted_at >= %s) AS d1" in s:
            self.c.grant_params = params
            self.row = {"d1": self.c.grants[0], "d3": self.c.grants[1]}
        elif "FROM trial_budgets" in s:
            self.row = {"cycle": 9, "all_time": 10, "exhausted": 0, "spent": 0.2}
        elif "FROM projects" in s:
            self.row = {"n": 1}
        elif s.startswith("INSERT INTO app_kv"):
            self.c.kv[params[0]] = params[1]
            self.c.writes.append((params[0], json.loads(params[1])))
        else:
            raise AssertionError(f"unexpected SQL: {s[:120]}")

    def fetchone(self):
        return self.row


PROD_FLAG = {"on": True, "model": "claude-haiku-5-5", "per_account_usd_cap": 0.12,
             "daily_usd_cap": 15, "daily_count_cap": 40, "pool_usd_cap": 109,
             "monthly_usd_cap": 109, "switched_at": ON.isoformat(),
             "test_user_ids": [29, 46], "trial_requests": True,
             "updated_by": "admin (release 1030)"}
CREDIT = {"cycle_budget_usd": 100.84, "safety_margin_usd": 10}


def test_run_writes_only_free_result_merged_and_logs_the_change(capsys):
    conn = Conn(dict(PROD_FLAG), CREDIT, spent=1.6, grants=(9, 9))
    out = tp.run(conn, now=NOW)
    assert out["changed"] is True
    assert [k for k, _ in conn.writes] == ["free_result"]
    value = conn.writes[0][1]
    for key in ("on", "model", "daily_count_cap", "pool_usd_cap", "switched_at",
                "test_user_ids", "trial_requests", "updated_by"):
        assert value[key] == PROD_FLAG[key]
    p = value["pacing"]
    assert value["daily_usd_cap"] == p["daily_usd_cap"]
    assert value["per_account_usd_cap"] == p["per_account_usd_cap"] == 0.40
    assert p["history"][-1]["per_account_usd_cap"] == [0.12, 0.40]
    assert p["history"][-1]["daily_usd_cap"][0] == 15
    assert p["why"].startswith("$0.40 per new account because")
    assert "trial pacing: daily_usd_cap 15 ->" in capsys.readouterr().out
    # test and internal accounts are not signups
    excluded = conn.grant_params[3]
    assert 29 in excluded and 46 in excluded and 60 in excluded


def test_an_unchanged_result_writes_nothing():
    conn = Conn(dict(PROD_FLAG), CREDIT, spent=1.6, grants=(9, 9))
    tp.run(conn, now=NOW)
    again = tp.run(conn, now=NOW + timedelta(minutes=15))
    assert again["changed"] is False and len(conn.writes) == 1


def test_the_owner_can_stop_pacing():
    conn = Conn(dict(PROD_FLAG, pacing_off=True), CREDIT, spent=1.6, grants=(9, 9))
    assert tp.run(conn, now=NOW)["skipped"] == "pacing_off"
    assert conn.writes == []


def test_history_is_bounded():
    conn = Conn(dict(PROD_FLAG), CREDIT, spent=0.0, grants=(9, 9))
    for hour in range(tp.HISTORY_KEEP + 5):
        conn.spent = (hour % 2) * 20.0   # the daily cap swings every hour
        tp.run(conn, now=NOW + timedelta(hours=hour))
    stored = json.loads(conn.kv["free_result"])
    assert len(stored["pacing"]["history"]) == tp.HISTORY_KEEP


def test_the_admin_card_shows_the_effective_cap_with_one_sentence_of_why():
    conn = Conn(dict(PROD_FLAG), CREDIT, spent=1.6, grants=(9, 9))
    tp.run(conn, now=NOW)
    st = trial_toggle.status(conn, now=NOW)
    assert st["per_account_usd_cap"] == 0.40
    assert st["pacing"]["per_account_usd_cap"] == 0.40
    assert st["pacing"]["why"].count(". ") == 0 and st["pacing"]["why"].endswith(".")
    assert st["daily_usd_cap"] == st["pacing"]["daily_usd_cap"]


def test_the_card_before_pacing_ever_ran():
    st = trial_toggle.status(Conn(dict(PROD_FLAG), CREDIT), now=NOW)
    assert st["pacing"] is None and st["per_account_usd_cap"] == 0.12


def test_the_scheduler_never_starts_on_a_stub_database():
    class App:
        config = {"DATABASE_URL": "postgresql://stub/stub"}
    tp._scheduler = None
    tp.start_scheduler(App())
    assert tp._scheduler is None
