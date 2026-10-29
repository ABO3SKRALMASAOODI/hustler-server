"""Who gets the Claude-funded trial, what it costs, and when it stops
(worker/claude_trial.py, owner decision D2)."""

import re
from types import SimpleNamespace

import pytest

import claude_lane
import claude_trial
import config
import llm


class _Db:
    """worker_db stand-in: run(fn, *a) calls fn(conn, *a)."""

    def __init__(self, conn=None):
        self.conn = conn
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append(getattr(fn, "__name__", str(fn)))
        return fn(self.conn, *args, **kwargs)


@pytest.fixture
def world(monkeypatch):
    """Lane configured, tables present, pool empty, nothing granted."""
    claude_trial.reset_flag_cache()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    state = SimpleNamespace(
        flag={"on": True}, admin=False, budget=None, admitted=True,
        credit={"on": True, "reason": None},
        totals={"pool": 0.0, "month": 0.0, "today": 0.0, "grants_today": 0},
        grants=[])
    monkeypatch.setattr(claude_trial, "project_admitted",
                        lambda conn, project_id: state.admitted)
    monkeypatch.setattr(claude_trial, "credit_status",
                        lambda worker_db, max_age_s=20.0: state.credit)
    monkeypatch.setattr(claude_trial, "read_flag",
                        lambda conn: claude_trial.parse_flag(state.flag))
    monkeypatch.setattr(claude_trial, "_is_admin", lambda conn, uid: state.admin)
    monkeypatch.setattr(claude_trial, "tables_ready", lambda conn: True)
    monkeypatch.setattr(claude_trial, "pool_totals", lambda conn: state.totals)
    monkeypatch.setattr(claude_trial, "get_budget",
                        lambda conn, uid: state.budget)

    def fake_grant(conn, uid, project_id, via, flag, pending=0.0, test=False,
                   admitted=False):
        assert admitted is True       # resolve only grants admitted projects
        state.grants.append((uid, via, test))
        state.budget = {"cap_usd": flag["per_account_usd_cap"],
                        "spent_usd": pending}
        return {"granted": True, "row": state.budget, "reason": "granted"}

    monkeypatch.setattr(claude_trial, "grant", fake_grant)
    yield state
    claude_trial.reset_flag_cache()
    claude_lane.reopen()


def test_flag_defaults_are_the_owner_numbers():
    f = claude_trial.parse_flag('{"on": true}')
    assert f["on"] is True
    # "Just enough", calibrated (C2, 29 Oct): $0.12, as in the backend
    assert f["per_account_usd_cap"] == 0.12
    assert f["pool_usd_cap"] == 200
    assert f["daily_count_cap"] == 40
    assert f["model"] == config.CLAUDE_TRIAL_MODEL
    off = claude_trial.parse_flag("not json")
    assert off["on"] is False
    custom = claude_trial.parse_flag({"on": "true", "per_account_usd_cap": 0.5,
                                      "model": "gpt-6-luna",
                                      "test_user_ids": [7, "8", "x"]})
    assert custom["per_account_usd_cap"] == 0.5
    assert custom["model"] == config.CLAUDE_TRIAL_MODEL   # never another wallet
    assert custom["test_user_ids"] == {7, 8}


def test_flag_off_paid_admin_and_operator_turns_are_untouched(world):
    db = _Db()
    world.flag = {"on": False}
    assert claude_trial.resolve(db, 5, False).kind == "not_trial"
    claude_trial.reset_flag_cache()
    world.flag = {"on": True}
    assert claude_trial.resolve(db, 5, True).kind == "not_trial"
    assert claude_trial.resolve(db, 5, False, operator_repair=True).kind \
        == "not_trial"
    world.admin = True
    assert claude_trial.resolve(db, 5, False).kind == "not_trial"
    assert world.grants == []


def test_a_new_account_is_granted_once_and_runs_on_claude(world):
    d = claude_trial.resolve(_Db(), 5, False, project_id=9)
    assert d.kind == "active"
    assert d.scope.cap_usd == 0.12 and d.scope.remaining_usd == 0.12
    assert d.scope.model == config.CLAUDE_TRIAL_MODEL
    assert world.grants == [(5, "agent_turn", False)]
    again = claude_trial.resolve(_Db(), 5, False, project_id=9)
    assert again.kind == "active" and len(world.grants) == 1


def test_a_spent_dollar_ends_on_the_plan_cards(world):
    world.budget = {"cap_usd": 1.0, "spent_usd": 0.998}
    assert claude_trial.resolve(_Db(), 5, False).kind == "exhausted"


def test_fail_closed_without_key_balance_tables_or_pool(world, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert claude_trial.resolve(_Db(), 5, False).reason == "no_anthropic_key"
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    claude_lane._close("billing: credit balance is too low")
    d = claude_trial.resolve(_Db(), 5, False)
    assert d.kind == "unavailable" and d.reason.startswith("provider_closed")
    claude_lane.reopen()
    world.totals = dict(world.totals, pool=200.0)
    assert claude_trial.resolve(_Db(), 5, False).reason == "pool_spent"
    world.totals = dict(world.totals, pool=10.0)
    monkeypatch.setattr(claude_trial, "tables_ready", lambda conn: False)
    assert claude_trial.resolve(_Db(), 5, False).reason == "migration_040_missing"


def test_index_scope_is_pending_until_the_upload_indexes(world):
    d = claude_trial.resolve(_Db(), 5, False, via="index", allow_grant=False)
    assert d.kind == "active" and d.scope.granted is False
    assert world.grants == []


def test_internal_test_accounts_bypass_the_switch_not_the_pool(world):
    world.flag = {"on": False, "test_user_ids": [29]}
    d = claude_trial.resolve(_Db(), 29, False)
    assert d.kind == "active" and d.scope.is_test
    assert world.grants == [(29, "agent_turn", True)]
    claude_trial.reset_flag_cache()
    # a subscribed test account keeps its paid lanes (claude_plans decides)
    assert claude_trial.resolve(_Db(), 29, True).kind == "not_trial"
    world.totals = dict(world.totals, pool=500.0)
    assert claude_trial.resolve(_Db(), 29, False).reason == "pool_spent"
    claude_trial.reset_flag_cache()
    # anyone else is untouched while the flag is off
    assert claude_trial.resolve(_Db(), 30, False).reason == "flag_off"


def test_only_the_admitted_trial_project_runs_on_claude(world):
    world.admitted = False
    d = claude_trial.resolve(_Db(), 5, False, project_id=9)
    assert d.kind == "not_trial" and d.reason == "not_admitted"
    assert world.grants == []


def test_the_credit_switch_closes_new_turns_and_lets_a_running_one_finish(world):
    world.credit = {"on": False, "reason": "budget"}
    d = claude_trial.resolve(_Db(), 5, False, project_id=9)
    assert d.kind == "unavailable" and d.reason.startswith("credit_switch_off")
    # a later slice of a request that already started finishes (budget only)
    d = claude_trial.resolve(_Db(), 5, False, project_id=9, continuing=True)
    assert d.kind == "active"
    # a billing refusal cannot be finished on: plan cards
    world.credit = {"on": False, "reason": "billing_error"}
    d = claude_trial.resolve(_Db(), 5, False, project_id=9, continuing=True)
    assert d.kind == "unavailable"


def test_an_unreadable_credit_switch_reads_as_off(monkeypatch):
    claude_trial.reset_flag_cache()

    class _Boom:
        def run(self, fn, *a):
            raise RuntimeError("db down")

    assert claude_trial.credit_status(_Boom())["on"] is False
    claude_trial.reset_flag_cache()


def test_disposable_domains_are_refused():
    assert claude_trial.email_is_disposable("a@mailinator.com")
    assert claude_trial.email_is_disposable("a@x.yopmail.fr")
    assert claude_trial.email_is_disposable("nobody")
    assert not claude_trial.email_is_disposable("founder@acme.io")


class _Meter:
    def __init__(self, pool=0.0):
        self.rows = []
        self.alerts = []
        self.pool = pool

    def run(self, fn, *args):
        if fn is claude_trial._meter_tx:
            uid, usd, granted = args[:3]
            self.rows.append((uid, round(usd, 6), granted))
            self.pool += usd
            return (sum(r[1] for r in self.rows if r[2]) if granted else None,
                    self.pool)
        if fn is claude_trial.queue_alert:
            self.alerts.append(args[0])
            return True
        import claude_credit
        if fn is claude_credit.read_config:
            return claude_credit.parse_config(None)
        raise AssertionError(fn)


def test_meter_writes_the_one_running_total_and_alerts_once(monkeypatch):
    meter = _Meter(pool=99.0)
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: meter)
    claude_trial._alerted.clear()
    scope = claude_trial.Scope(5, 1.0, 0.0, "claude-haiku-5-5", pool_cap=200)
    claude_trial.activate(scope)
    try:
        claude_trial.meter("claude-haiku-5-5", 0.25)
        claude_trial.meter("claude-haiku-5-5", 1.5)   # crosses 50 %
        claude_trial.meter("claude-haiku-5-5", 0.25)
    finally:
        claude_trial.deactivate()
    assert meter.rows == [(5, 0.25, True), (5, 1.5, True), (5, 0.25, True)]
    assert scope.spent_usd == pytest.approx(2.0)
    # No pool-percentage mails (owner, 29 Oct: alerts only for a blocked
    # customer or a provider out of credit); the credit switch alerts once.
    assert meter.alerts == []
    monkeypatch.setattr(claude_trial, "POOL_ALERTS_ENABLED", True)
    claude_trial.activate(scope)
    try:
        claude_trial.meter("claude-haiku-5-5", 0.25)
    finally:
        claude_trial.deactivate()
    # one alert per threshold per credit cycle (no rollover) when enabled
    assert meter.alerts == ["pool_50_of_200_2026-11-08"]


def test_pending_scope_accrues_to_the_pool_and_carries_into_the_grant(monkeypatch):
    meter = _Meter()
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: meter)
    scope = claude_trial.Scope(5, 1.0, 0.0, "claude-haiku-5-5", granted=False)
    claude_trial.activate(scope)
    try:
        claude_trial.meter("claude-haiku-5-5", 0.02)
    finally:
        claude_trial.deactivate()
    assert meter.rows == [(5, 0.02, False)]
    assert scope.pending_usd == pytest.approx(0.02)


def test_scope_travels_with_the_recorder_into_pool_threads():
    scope = claude_trial.Scope(5, 1.0, 0.0, "claude-haiku-5-5")

    def recorder(*a):
        return None

    claude_trial.activate(scope, recorder)
    claude_trial._tls.scope = None          # a pool thread: no thread-local
    llm.set_recorder(recorder)
    try:
        assert claude_trial.current() is scope
        client, model = llm.vision_client_for("")
        assert getattr(client, "is_claude_lane", False)
        assert model == "claude-haiku-5-5"
    finally:
        llm.set_recorder(None)
        claude_trial.deactivate(recorder)
    assert claude_trial.current() is None
    assert not getattr(llm.vision_client_for("")[0], "is_claude_lane", False)


def test_hard_ceiling_backstops_critics_and_vision():
    scope = claude_trial.Scope(5, 1.0, 1.2, "claude-haiku-5-5")
    claude_trial.activate(scope)
    try:
        with pytest.raises(claude_lane.ClaudeLaneUnavailable):
            claude_trial.guard_call()
    finally:
        claude_trial.deactivate()


def test_stop_meta_renders_as_plan_cards_with_keep_going():
    meta = claude_trial.stop_meta("exhausted", 42)
    assert meta["subscribe_required"] and meta["credits_exhausted"]
    assert meta["trial_budget_exhausted"] and meta["keep_going"]
    assert meta["request_message_id"] == 42
    paused = claude_trial.stop_meta("unavailable")
    assert paused["trial_unavailable"] and not paused["trial_budget_exhausted"]


def test_provider_refusal_alert_never_contains_the_key(monkeypatch):
    queued = []
    monkeypatch.setattr(claude_trial, "_alert",
                        lambda key, kind, subject, html: queued.append(html))
    switched = []

    class _SwitchDb:
        def run(self, fn, *args):
            switched.append((fn.__name__, args))
            return True

    monkeypatch.setattr(claude_trial, "_metering_db", lambda: _SwitchDb())
    claude_trial.provider_refused(
        "auth", Exception("bad key sk-ant-api03-SECRETSECRET-xyz"))
    assert queued and "sk-ant-" not in queued[0] and "[REDACTED]" in queued[0]
    # the refusal flips the automatic credit switch, without the key either
    assert switched and switched[0][0] == "switch_off"
    assert switched[0][1][0] == "auth_error"
    assert "sk-ant-" not in switched[0][1][1]


class _Cur:
    """Scripted cursor for grant(): answers by SQL shape."""

    def __init__(self, script):
        self.script = script
        self.rowcount = 0
        self._row = None
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        flat = re.sub(r"\s+", " ", sql)
        for pattern, value in self.script:
            if pattern in flat:
                self._row = value(args) if callable(value) else value
                self.rowcount = 1 if "INSERT" in flat else 0
                return
        self._row = None

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, script):
        self.cur = _Cur(script)

    def cursor(self):
        return self.cur


def _grant_script(email="new@acme.io", subscribed=0, grants_today=0,
                  existing=None):
    return [
        ("FROM trial_budgets WHERE user_id", existing),
        ("FROM users WHERE id", {"id": 5, "email": email,
                                 "is_subscribed": subscribed}),
        ("FROM payments", None),
        ("FROM website_signups", None),
        ("FROM trial_budget_days", {"pool": 0, "month": 0, "today": 0,
                                    "grants_today": grants_today}),
        ("INSERT INTO trial_budgets", None),
    ]


def test_grant_enforces_the_d2_limits():
    flag = claude_trial.parse_flag({"on": True})
    assert claude_trial.grant(_Conn(_grant_script(email="thevalmera@gmail.com")),
                              5, 1, "index", flag)["reason"] == "admin"
    assert claude_trial.grant(_Conn(_grant_script(email="x@mailinator.com")),
                              5, 1, "index", flag)["reason"] == "disposable_email"
    assert claude_trial.grant(_Conn(_grant_script(subscribed=1)),
                              5, 1, "index", flag)["reason"] == "paid_account"
    assert claude_trial.grant(_Conn(_grant_script(grants_today=40)),
                              5, 1, "index", flag)["reason"] == "daily_count_cap"
    existing = {"cap_usd": 1, "spent_usd": 0.3}
    got = claude_trial.grant(_Conn(_grant_script(existing=existing)),
                             5, 1, "index", flag)
    assert got["reason"] == "existing" and got["row"] is existing
    conn = _Conn(_grant_script())
    got = claude_trial.grant(conn, 5, 1, "index", flag, pending_usd=0.01)
    assert got["granted"]
    assert any("pg_advisory_xact_lock" in s for s in conn.cur.sql)
    assert any("INSERT INTO trial_budgets" in s for s in conn.cur.sql)


def test_a_turn_that_cannot_start_posts_the_plan_cards(monkeypatch):
    import agent_loop
    posted = []

    class _WDb:
        def run(self, fn, *args):
            posted.append(args)
            return 1

    ctx = SimpleNamespace(latest_edl=lambda: {"version": 3})
    out = agent_loop._claude_trial_stop(
        _WDb(), ctx, {"id": 11}, 7, {"id": 99},
        claude_trial.Decision("exhausted", reason="budget_spent"))
    assert out["billable"] is False and out["outcome"] == "blocked"
    session, role, text, meta = posted[0]
    assert (session, role) == (7, "assistant")
    assert text == claude_trial.EXHAUSTED_BEFORE_TEXT
    assert meta["subscribe_required"] and meta["request_message_id"] == 99
    assert meta["edl_version"] == 3


def test_a_trial_slice_never_charges_credits_and_meters_generations(monkeypatch):
    import agent_loop
    metered = []
    monkeypatch.setattr(claude_trial, "meter",
                        lambda model, usd, kind="llm": metered.append(
                            (kind, round(usd, 4))))
    scope = claude_trial.Scope(5, 1.0, 0.1, "claude-haiku-5-5")
    scope.extras_metered_usd = 0.05
    ctx = SimpleNamespace(claude_trial=scope, gen_extra_cost_usd=0.05,
                          images_generated=["a.png"])
    result = {"billable": True, "status": "replied"}
    agent_loop._claude_trial_settle(ctx, result)
    assert result["billable"] is False
    assert result["claude_trial"]["user_id"] == 5
    assert metered == [("extras", round(config.IMAGE_PRICE_USD, 4))]


def test_only_trial_turns_treat_wallet_errors_as_a_trial_stop():
    import agent_loop
    err = claude_lane.ClaudeLaneUnavailable("credit balance is too low")
    assert not agent_loop._trial_lane_refused(SimpleNamespace(), err)
    trial_ctx = SimpleNamespace(claude_trial=claude_trial.Scope(
        5, 1.0, 0, "claude-haiku-5-5"))
    assert agent_loop._trial_lane_refused(trial_ctx, err)
    assert not agent_loop._trial_lane_refused(trial_ctx, ValueError("bug"))


def test_trial_jobs_are_never_billed_in_credits():
    import job_completion
    scope = claude_trial.Scope(5, 1.0, 0, "claude-haiku-5-5")
    assert job_completion._result_is_billable(
        {"type": "shorts_plan", "_claude_trial": scope}, {"billable": True}) \
        is False
    assert job_completion._result_is_billable(
        {"type": "agent_turn"}, {"billable": True,
                                 "claude_trial": scope.as_dict()}) is False
    assert job_completion._result_is_billable(
        {"type": "shorts_plan"}, {"billable": True}) is True


def test_scout_and_text_calls_follow_the_scope():
    scope = claude_trial.Scope(5, 1.0, 0, "claude-haiku-5-5")
    claude_trial.activate(scope)
    try:
        client, model = llm.agent_client_for(False, "free")
        assert getattr(client, "is_claude_lane", False)
        assert model == "claude-haiku-5-5"
    finally:
        claude_trial.deactivate()
    client, model = llm.agent_client_for(False, "free")
    assert not getattr(client, "is_claude_lane", False)
