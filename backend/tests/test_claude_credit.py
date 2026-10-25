"""The automatic credit switch (backend/claude_credit.py, owner 25 Oct 2026):
per-cycle Claude spend against the budget, OFF at the line or on a billing
refusal, a probe that turns it back ON in a new cycle, one admin alert per
switch, and the same code in the backend and the worker."""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import claude_credit as cc  # noqa: E402
import claude_credit_probe as probe  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


class FakeConn:
    """app_kv + trial_budget_days + trial_alerts, by SQL shape."""

    def __init__(self, ledger=True):
        self.kv = {}
        self.days = {}
        self.alerts = {}
        self.ledger = ledger
        self._row = None
        self.rowcount = 0
        self.commits = 0

    # connection
    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    # cursor
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def spend(self, day, trial=0.0, extras=0.0, paid=0.0):
        row = self.days.setdefault(day, {"spent": 0.0, "extras": 0.0,
                                         "paid": 0.0, "paid_calls": 0})
        row["spent"] += trial + extras
        row["extras"] += extras
        row["paid"] += paid

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self._row, self.rowcount = None, 0
        if q.startswith("SELECT value FROM app_kv WHERE key"):
            value = self.kv.get(params[0])
            self._row = {"value": value} if value is not None else None
        elif q.startswith("INSERT INTO app_kv"):
            self.kv[params[0]] = params[1]
            self.rowcount = 1
        elif "information_schema.columns" in q:
            self._row = {"x": 1} if self.ledger else None
        elif "GREATEST(spent_usd - extras_usd, 0)" in q:
            start, end = params
            self._row = {"usd": sum(
                max(r["spent"] - r["extras"], 0) + r["paid"]
                for d, r in self.days.items() if start <= d < end)}
        elif "pg_advisory_xact_lock" in q:
            pass
        elif q.startswith("SELECT to_regclass('public.trial_alerts')"):
            self._row = {"t": "trial_alerts"}
        elif q.startswith("INSERT INTO trial_alerts"):
            key = params[0]
            if key not in self.alerts:
                self.alerts[key] = {"kind": params[1], "subject": params[2],
                                    "html": params[3]}
                self.rowcount = 1
        elif q.startswith("INSERT INTO trial_budget_days (day, paid_usd"):
            today = datetime.now(UTC).date()
            self.spend(today, paid=params[0])
            self.days[today]["paid_calls"] += params[1]
            self.rowcount = 1
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


def _at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC)


def _state(conn):
    return json.loads(conn.kv[cc.STATE_KEY])


def test_backend_and_worker_share_one_implementation():
    backend = (ROOT / "backend" / "claude_credit.py").read_bytes()
    worker = (ROOT / "worker" / "claude_credit.py").read_bytes()
    assert backend == worker
    plans_b = (ROOT / "backend" / "claude_plans.py").read_bytes()
    plans_w = (ROOT / "worker" / "claude_plans.py").read_bytes()
    assert plans_b == plans_w


def test_defaults_are_the_owners_cycle():
    cfg = cc.parse_config(None)
    assert cfg["cycle_budget_usd"] == 200.0
    assert cfg["cycle_end"] == _at(2026, 11, 8, 0)
    start, end = cc.cycle_window(cfg, _at(2026, 10, 25))
    assert (start, end) == (_at(2026, 10, 8, 0), _at(2026, 11, 8, 0))
    # the next cycle starts on 8 Nov, nothing carried over
    start, end = cc.cycle_window(cfg, _at(2026, 11, 8, 0))
    assert (start, end) == (_at(2026, 11, 8, 0), _at(2026, 12, 8, 0))
    start, end = cc.cycle_window(cfg, _at(2027, 1, 31))
    assert (start, end) == (_at(2027, 1, 8, 0), _at(2027, 2, 8, 0))
    assert cc.line_usd(cfg) == 190.0
    bad = cc.parse_config("not json")
    assert bad["cycle_budget_usd"] == 200.0


def test_a_missing_state_row_is_on_and_reading_never_writes():
    conn = FakeConn()
    status = cc.status(conn, now=_at(2026, 10, 25))
    assert status["on"] is True and status["state"] == "on"
    assert conn.kv == {} and conn.alerts == {}


def test_reaching_the_line_turns_it_off_once_with_one_alert():
    conn = FakeConn()
    now = _at(2026, 10, 25)
    conn.spend(date(2026, 10, 20), trial=120.0, extras=30.0)  # extras: not Anthropic
    conn.spend(date(2026, 10, 24), paid=60.0)
    assert cc.check_line(conn, now=now) is False              # 180 < 190
    conn.spend(date(2026, 10, 25), trial=11.0)
    status = cc.status(conn, now=now)
    assert status["on"] is False and status["reason"] == "budget"
    assert cc.check_line(conn, now=now) is True
    assert _state(conn)["state"] == "off" and _state(conn)["reason"] == "budget"
    assert _state(conn)["cycle_end"].startswith("2026-11-08")
    assert cc.check_line(conn, now=now) is False              # idempotent
    assert len(conn.alerts) == 1
    alert = next(iter(conn.alerts.values()))
    assert alert["kind"] == "credit_switch_off"
    assert "pre-edit paywall" in alert["html"]


def test_spend_from_an_earlier_cycle_does_not_count():
    conn = FakeConn()
    conn.spend(date(2026, 11, 7), trial=195.0)
    assert cc.status(conn, now=_at(2026, 11, 7))["on"] is False
    assert cc.status(conn, now=_at(2026, 11, 8, 1))["on"] is True


def test_a_billing_refusal_turns_it_off_and_never_leaks_the_key():
    conn = FakeConn()
    assert cc.switch_off(conn, cc.REASON_BILLING,
                         "400 credit balance too low sk-ant-api03-SECRET",
                         now=_at(2026, 10, 25)) is True
    state = _state(conn)
    assert state["state"] == "off" and state["reason"] == "billing_error"
    assert "sk-ant-" not in conn.kv[cc.STATE_KEY]
    assert all("sk-ant-" not in a["html"] for a in conn.alerts.values())


def test_the_probe_switches_on_only_in_a_new_cycle_after_a_credit_stop():
    conn = FakeConn()
    cc.switch_off(conn, cc.REASON_BUDGET, now=_at(2026, 11, 1))
    calls = []

    def call(key):
        calls.append(key)
        return True, "stop_reason=end_turn"

    # same cycle: it probes (recorded) but stays off
    out = probe.run(conn, "k", call=call, now=_at(2026, 11, 2))
    assert out["result"] == "recorded" and calls == ["k"]
    assert _state(conn)["state"] == "off" and _state(conn)["last_probe_ok"]
    # three hours have not passed: no call at all
    out = probe.run(conn, "k", call=call, now=_at(2026, 11, 2, 13))
    assert out["result"] == "nothing to do" and len(calls) == 1
    # new cycle: one call, back on, one alert
    out = probe.run(conn, "k", call=call, now=_at(2026, 11, 8, 3))
    assert out["result"] == "switched_on" and len(calls) == 2
    assert _state(conn)["state"] == "on"
    kinds = sorted(a["kind"] for a in conn.alerts.values())
    assert kinds == ["credit_switch_off", "credit_switch_on"]


def test_a_failed_probe_in_a_new_cycle_stays_off():
    conn = FakeConn()
    cc.switch_off(conn, cc.REASON_BILLING, now=_at(2026, 11, 1))
    out = probe.run(conn, "k", call=lambda key: (False, "402 billing_error"),
                    now=_at(2026, 11, 9))
    assert out["result"] == "recorded" and out["probe_ok"] is False
    assert _state(conn)["state"] == "off"
    assert _state(conn)["last_probe_error"] == "402 billing_error"


def test_a_key_problem_comes_back_as_soon_as_the_probe_succeeds():
    conn = FakeConn()
    cc.switch_off(conn, cc.REASON_AUTH, "401", now=_at(2026, 10, 25))
    out = probe.run(conn, "k", call=lambda key: (True, ""),
                    now=_at(2026, 10, 25, 16))
    assert out["result"] == "switched_on"


def test_while_on_the_probe_makes_no_call_and_enforces_the_line():
    conn = FakeConn()

    def call(key):
        raise AssertionError("no Anthropic call while the switch is on")

    assert probe.run(conn, "k", call=call,
                     now=_at(2026, 10, 25))["result"] == "nothing to do"
    conn.spend(date(2026, 10, 25), paid=191.0)
    out = probe.run(conn, "k", call=call, now=_at(2026, 10, 25))
    assert out["result"] == "switched off (budget)"
    assert _state(conn)["reason"] == "budget"


def test_dry_run_decides_without_writing():
    conn = FakeConn()
    cc.switch_off(conn, cc.REASON_BUDGET, now=_at(2026, 11, 1))
    before = dict(conn.kv)
    out = probe.run(conn, "k", dry_run=True,
                    call=lambda key: (_ for _ in ()).throw(AssertionError()),
                    now=_at(2026, 11, 8, 3))
    assert out["result"].startswith("would probe; would switch on")
    assert conn.kv == before and conn.commits == 0


def test_a_paid_call_lands_in_the_cycle_ledger():
    conn = FakeConn()
    assert cc.record_paid_spend(conn, 0.25) is True
    start, end = cc.cycle_window(cc.parse_config(None))
    assert abs(cc.cycle_spend(conn, start, end) - 0.25) < 1e-9
    assert cc.record_paid_spend(FakeConn(ledger=False), 0.25) is False


def test_a_configured_budget_and_cycle_are_honoured():
    conn = FakeConn()
    conn.kv[cc.CONFIG_KEY] = json.dumps({
        "cycle_budget_usd": 50, "safety_margin_usd": 5,
        "cycle_end": "2026-12-01T00:00:00Z"})
    conn.spend(date(2026, 11, 20), trial=46.0)
    status = cc.status(conn, now=_at(2026, 11, 20))
    assert status["line_usd"] == 45.0 and status["on"] is False
    assert status["cycle_start"].startswith("2026-11-01")


def test_a_manual_override_is_one_write():
    conn = FakeConn()
    cc.switch_off(conn, cc.REASON_BILLING, now=_at(2026, 10, 25))
    conn.kv[cc.STATE_KEY] = json.dumps({"state": "on", "reason": "manual"})
    assert cc.status(conn, now=_at(2026, 10, 25))["on"] is True


def test_the_workflow_runs_the_probe_every_three_hours_with_the_credited_key():
    wf = (ROOT / ".github" / "workflows" / "claude-credit-probe.yml").read_text()
    assert 'cron: "23 */3 * * *"' in wf
    assert "secrets.ANTHROPIC_API_KEY" in wf
    assert "secrets.PRODUCTION_DATABASE_URL" in wf
    assert "backend/scripts/claude_credit_probe.py" in wf
