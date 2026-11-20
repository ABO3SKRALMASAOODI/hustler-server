"""The model budget governor (owner, 20 Nov 2026): paying customers edit on
the best Claude model today's Anthropic allowance can pay for, charged at the
plan's Haiku card; the plan's model is the floor and always allowed.

The database is faked by statement shape (one object is connection and
cursor, like the claude_plans / claude_credit tests)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import claude_lane
import claude_plans
import claude_trial
import config
import llm
import model_budget
import model_prices

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
REFILL = "2026-12-08T00:00:00+00:00"     # 18 days after the start of NOW's day
HAIKU, SONNET, OPUS = model_budget.HAIKU, model_budget.SONNET, model_budget.OPUS
CUSTOMER = 3261
INTERNAL = 60


class World:
    """app_kv, the wallet's meters and llm_calls, by statement shape."""

    def __init__(self, wallet=69.04, baseline=100.0, spent_today=0.0,
                 lifetime=100.0, company=0.0, budget=None, account=None,
                 samples=None, cycle=12.0, line_cfg=None):
        self.kv = {}
        if wallet is not None:
            self.kv["anthropic_wallet"] = json.dumps({
                "confirmed_balance_usd": wallet, "meter_baseline_usd": baseline,
                "confirmed_at": "2026-11-20T10:00:00+00:00"})
        if budget is not None:
            self.kv["model_budget"] = json.dumps(budget)
        # a credit line well above the wallet unless a test sets one
        self.kv["claude_credit"] = json.dumps(line_cfg or {
            "cycle_budget_usd": 1000.0, "safety_margin_usd": 10,
            "cycle_start": "2026-11-05T00:00:00+00:00", "cycle_end": REFILL})
        self.spent_today = spent_today
        # the wallet meter counts today's customer spend as well
        self.lifetime = lifetime + spent_today
        self.company = company
        self.account = account or {}
        self.samples = samples or {}
        self.cycle = cycle
        self.sql = []
        self.stamped = []
        self._rows = []

    @property
    def connection(self):
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        p = params or ()
        self.sql.append(q)
        self._rows = []
        if q.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            return
        if q.startswith("SELECT value FROM app_kv"):
            v = self.kv.get(p[0])
            self._rows = [] if v is None else [{"value": v}]
        elif "FROM trial_budget_days WHERE day = %s" in q:
            self._rows = [{"usd": self.spent_today}]
        elif "WHERE day >= %s AND day < %s" in q:             # cycle spend
            self._rows = [{"usd": self.cycle}]
        elif "AS usd FROM trial_budget_days" in q:
            self._rows = [{"usd": self.lifetime}]
        elif q.startswith("SELECT to_regclass('public.spend_ledger')"):
            self._rows = [{"t": "spend_ledger"}]
        elif "FROM spend_ledger" in q:
            self._rows = [{"usd": self.company}]
        elif "information_schema.columns" in q:
            self._rows = [{"x": 1}]
        elif "ROW_NUMBER() OVER" in q:
            models = p[1]
            self._rows = [{"model": m, "usd": usd}
                          for m in models for usd in self.samples.get(m, [])]
        elif "FROM llm_calls l JOIN video_jobs j" in q:
            self._rows = [{"usd": self.account.get(int(p[2]), 0.0)}]
        elif q.startswith("UPDATE video_jobs"):
            self.stamped.append((p[1], json.loads(p[0])))
        else:
            raise AssertionError(f"unexpected SQL: {q[:160]}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture(autouse=True)
def fresh():
    model_budget.reset_cache()
    yield
    model_budget.reset_cache()


def _decide(world, user=CUSTOMER, paying=True, plan_model=HAIKU):
    return model_budget.decide(world, user, plan_model, paying, now=NOW)


# ------------------------------------------------------------------ the ladder

def test_the_allowance_spreads_the_wallet_over_the_days_left():
    w = World(wallet=69.04)
    state = model_budget.budget_state(w, model_budget.parse_config(None), NOW)
    assert state["days_left"] == 18.0
    assert state["allowance_usd"] == pytest.approx((69.04 - 15.0) / 18.0)
    # today's own spend is counted once: the wallet at the start of the day
    w = World(wallet=69.04, spent_today=2.0)
    state = model_budget.budget_state(w, model_budget.parse_config(None), NOW)
    assert state["wallet_usd"] == pytest.approx(67.04)
    assert state["allowance_usd"] == pytest.approx((69.04 - 15.0) / 18.0)


def test_opus_when_the_allowance_is_large():
    got = _decide(World(wallet=200.0))           # (200-15)/18 = $10.3 a day
    assert got["model"] == OPUS and got["upgraded"] and got["reason"] == "fits"
    assert got["plan_model"] == HAIKU and got["charge_as"] == HAIKU
    assert got["expected_usd"] == pytest.approx(1.2)


def test_sonnet_when_only_sonnet_fits():
    # (69.04-15)/18 = $3.00 a day, $2.0 already spent: Opus 1.2 does not fit,
    # Sonnet 0.6 does (and is inside the account's 40% share)
    got = _decide(World(wallet=67.04, spent_today=2.0))
    assert got["model"] == SONNET and got["reason"] == "fits"


def test_haiku_when_nothing_fits_and_the_request_still_runs():
    got = _decide(World(wallet=67.04 - 1.0, spent_today=2.9))
    assert got["model"] == HAIKU and not got["upgraded"]
    assert got["reason"] == "daily_allowance"
    # an empty wallet: the floor, never a refusal
    got = _decide(World(wallet=0.0))
    assert got["model"] == HAIKU and got["allowance_usd"] == 0.0


def test_one_account_gets_at_most_its_share_of_the_day():
    w = World(wallet=200.0, account={CUSTOMER: 3.5})   # share 0.4 x $10.28
    got = _decide(w)
    # Opus (1.2) would pass $4.11; Sonnet (0.6) still fits
    assert got["model"] == SONNET and got["account_premium_usd"] == 3.5
    w = World(wallet=200.0, account={CUSTOMER: 4.0})
    got = _decide(w)
    assert got["model"] == HAIKU and got["reason"] == "account_share"
    # another account is unaffected
    assert _decide(w, user=CUSTOMER + 1)["model"] == OPUS


def test_the_credit_line_bounds_the_allowance_so_the_switch_never_trips():
    # a lot of wallet, but the Claude credit line has $9 of room for 18 days
    w = World(wallet=500.0, cycle=139.14 - 10 - 9.0, line_cfg={
        "cycle_budget_usd": 139.14, "safety_margin_usd": 10,
        "cycle_start": "2026-11-05T00:00:00+00:00", "cycle_end": REFILL})
    state = model_budget.budget_state(w, model_budget.parse_config(None), NOW)
    assert state["allowance_usd"] == pytest.approx(9.0 / 18.0)
    assert _decide(w)["model"] == HAIKU


def test_the_pick_is_pure_and_the_floor_is_the_plans_model():
    costs = {OPUS: 1.2, SONNET: 0.6, HAIKU: 0.04}
    ladder = [OPUS, SONNET, HAIKU]
    assert model_budget.pick(ladder, HAIKU, 10, 0, 0, costs, 0.4) == (OPUS, "fits")
    assert model_budget.pick(ladder, SONNET, 10, 0, 0, costs, 0.4)[0] == OPUS
    assert model_budget.pick(ladder, OPUS, 10, 0, 0, costs, 0.4)[0] == OPUS
    assert model_budget.pick(ladder, "gpt-6.1-sol", 10, 0, 0, costs, 0.4) == (
        "gpt-6.1-sol", "plan_model_not_on_ladder")


# ------------------------------------------------------------------ who gets it

def test_non_paying_and_internal_accounts_keep_the_plan_model():
    w = World(wallet=200.0)
    got = _decide(w, paying=False)
    assert got["model"] == HAIKU and got["reason"] == "not_paying"
    got = _decide(w, user=INTERNAL)
    assert got["model"] == HAIKU and got["reason"] == "not_customer"
    w = World(wallet=200.0, budget={"test_user_ids": [INTERNAL]})
    assert _decide(w, user=INTERNAL)["model"] == OPUS


def test_off_is_todays_behaviour_and_absent_is_on():
    assert model_budget.parse_config(None)["on"] is True
    w = World(wallet=200.0, budget={"on": False})
    got = _decide(w)
    assert got["model"] == HAIKU and got["reason"] == "off"
    assert not any("llm_calls" in q for q in w.sql)


def test_wallet_unknown_or_any_error_falls_back_silently():
    got = _decide(World(wallet=None))
    assert got["model"] == HAIKU and got["reason"] == "wallet_unknown"
    # the meter went backwards: unknown, never funded
    got = _decide(World(wallet=69.04, baseline=500.0))
    assert got["model"] == HAIKU and got["reason"] == "wallet_unknown"

    class Broken:
        def cursor(self):
            raise RuntimeError("db down")

    got = model_budget.decide(Broken(), CUSTOMER, HAIKU, True, now=NOW)
    assert got["model"] == HAIKU and got["reason"] == "error"


def test_after_the_refill_date_nothing_is_assumed():
    late = datetime(2026, 12, 9, tzinfo=UTC)
    got = model_budget.decide(World(wallet=200.0), CUSTOMER, HAIKU, True,
                              now=late)
    assert got["model"] == HAIKU and got["reason"] == "refill_passed"


# ------------------------------------------------------------------ learning

def test_expected_cost_learns_from_real_requests():
    cfg = model_budget.parse_config(None)
    w = World(samples={OPUS: [2.0] * 20})
    got = model_budget.expected_costs(w, cfg, NOW)
    # the default counts as 2 requests: (1.2*2 + 40) / 22
    assert got[OPUS]["usd"] == pytest.approx((1.2 * 2 + 40.0) / 22, abs=1e-4)
    assert got[OPUS]["samples"] == 20
    assert got[SONNET] == {"usd": 0.6, "samples": 0}
    learn = next(q for q in w.sql if "ROW_NUMBER() OVER" in q)
    # only requests the governor ran on that model teach it
    assert "payload->'model_budget'->>'model' = l.model" in learn
    # an expensive history moves the ladder down
    w = World(wallet=200.0, samples={OPUS: [6.0] * 20})
    assert _decide(w)["model"] == SONNET


def test_blend_starts_at_the_default():
    assert model_budget.blend(1.2, [], 2) == 1.2
    assert model_budget.blend(1.2, [0.2, 0.2], 2) == pytest.approx(0.7)


# ------------------------------------------------------------------ the worker wiring

class _WorkerDb:
    def __init__(self, world, flag):
        self.world = world
        self.flag = flag

    def run(self, fn, *args):
        if fn is claude_plans.read_flag:
            return claude_plans.parse_flag(self.flag)
        return fn(self.world, *args)


@pytest.fixture
def lane_up(monkeypatch):
    claude_trial.reset_flag_cache()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    monkeypatch.setattr(claude_trial, "credit_status",
                        lambda worker_db, max_age_s=20.0: {"on": True})
    yield
    claude_lane.reopen()
    claude_trial.reset_flag_cache()


FLAG = json.dumps({"on": True, "cutoff_at": "2026-10-30T00:00:00Z",
                   "credit_parity": False})


def _route(world, payload, job=None, trialing=False, govern=True):
    job = job or {"id": 501, "user_id": CUSTOMER, "payload": payload}
    return agent_loop._claude_paid_lane(
        _WorkerDb(world, FLAG), job, True, "plus_2026", payload,
        trialing=trialing, govern=govern)


def test_an_upgraded_request_edits_on_opus_and_helpers_keep_haiku(
        lane_up, monkeypatch):
    monkeypatch.setattr(model_budget, "decide",
                        lambda conn, *a: model_budget._decision(
                            OPUS, HAIKU, "fits", None))
    world, payload = World(), {}
    route = _route(world, payload)
    assert route["lane"]["model"] == OPUS
    assert route["helper_lane"]["model"] == HAIKU
    assert route["credit_aliases"] == {OPUS: HAIKU}
    assert payload["model_budget"]["model"] == OPUS
    assert payload["model_budget"]["root"] == 501
    assert world.stamped and world.stamped[0][0] == 501
    # Shorts' helper lane is not an editing request: no governor
    plain = _route(World(), {}, govern=False)
    assert plain["lane"]["model"] == HAIKU and "model_budget" not in plain


def test_a_continuation_keeps_the_model(lane_up, monkeypatch):
    calls = []

    def decide(conn, *a):
        calls.append(a)
        return model_budget._decision(OPUS, HAIKU, "fits", None)

    monkeypatch.setattr(model_budget, "decide", decide)
    first = {}
    _route(World(), first)
    assert len(calls) == 1
    # the continuation's payload is a copy of the first slice's
    cont = dict(first, root_agent_job_id=501, continuation_sequence=1,
                logical_turn_continuation=True)
    route = _route(World(), cont, job={"id": 502, "user_id": CUSTOMER})
    assert len(calls) == 1                      # not decided again
    assert route["lane"]["model"] == OPUS
    assert cont["model_budget"]["reason"] == "continuation"
    # the budget dropping meanwhile does not move a request mid-way
    monkeypatch.setattr(model_budget, "decide", lambda conn, *a: (
        model_budget._decision(HAIKU, HAIKU, "daily_allowance", None)))
    assert _route(World(), dict(cont), job={"id": 503, "user_id": CUSTOMER}
                  )["lane"]["model"] == OPUS
    # a NEW request (no inherited decision) is decided afresh
    assert _route(World(), {}, job={"id": 600, "user_id": CUSTOMER}
                  )["lane"]["model"] == HAIKU


def test_a_slice_that_failed_on_the_upgrade_continues_on_the_plan_model(
        lane_up):
    previous = model_budget._decision(OPUS, HAIKU, "fits", None)
    previous["root"] = 501
    cont = {"model_budget": previous, "root_agent_job_id": 501,
            "continuation_state": {"exception_fingerprint": "boom #"}}
    route = _route(World(), cont, job={"id": 502, "user_id": CUSTOMER})
    assert route["lane"]["model"] == HAIKU and "helper_lane" not in route
    assert cont["model_budget"]["reason"] == "after_error"


def test_the_governor_reaches_the_lane_with_a_real_world(lane_up):
    route = _route(World(wallet=200.0), {})
    assert route["lane"]["model"] == OPUS
    assert route["model_budget"]["allowance_usd"] == pytest.approx(185 / 18,
                                                                   abs=0.2)
    # a Paddle trial is not a paying account
    route = _route(World(wallet=200.0), {}, trialing=True)
    assert route["lane"]["model"] == HAIKU
    assert route["model_budget"]["reason"] == "not_paying"


def test_a_governor_failure_keeps_the_plan_lane(lane_up, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(model_budget, "kept", boom)
    route = _route(World(), {})
    assert route["lane"]["model"] == HAIKU and "helper_lane" not in route


# ------------------------------------------------------------------ billing

def test_an_upgraded_call_is_charged_at_the_haiku_card():
    usage = SimpleNamespace(prompt_tokens=40_000, completion_tokens=3_000,
                            prompt_tokens_details={"cached_tokens": 30_000,
                                                   "cache_write_tokens": 5_000},
                            provider_cost_usd=0.21)
    recorded = []

    def recorder(purpose, request, response, u):
        recorded.append(response)

    recorder.credit_price_aliases = {OPUS: HAIKU}
    llm.set_recorder(recorder)
    try:
        llm.record("agent", {"model": OPUS}, {"x": 1}, usage)
    finally:
        llm.set_recorder(None)
    row = recorded[0]
    assert row["provider_cost_usd"] == 0.21        # the meters: real cost
    assert row["credit_price_model"] == HAIKU
    haiku_card = (5_000 * 0.10 + 30_000 * 0.01 + 5_000 * 0.125
                  + 3_000 * 0.50) / 1e6
    assert row["credit_cost_usd"] == pytest.approx(haiku_card)
    # the in-turn cap counts the same credits as a Haiku turn would
    opus = SimpleNamespace(model_usage={OPUS: {
        "in": 40_000, "cached": 30_000, "out": 3_000, "reasoning": 0}},
        images_generated=[], gen_extra_cost_usd=0.0,
        credit_price_aliases={OPUS: HAIKU})
    haiku = SimpleNamespace(model_usage={HAIKU: {
        "in": 40_000, "cached": 30_000, "out": 3_000, "reasoning": 0}},
        images_generated=[], gen_extra_cost_usd=0.0, credit_price_aliases={})
    assert agent_tools.ToolContext.running_credits(opus) == pytest.approx(
        agent_tools.ToolContext.running_credits(haiku))
    opus.credit_price_aliases = {}
    assert agent_tools.ToolContext.running_credits(opus) > 10 * \
        agent_tools.ToolContext.running_credits(haiku)
    assert model_prices.MODEL_PRICES[HAIKU]["in"] == 0.10


# ------------------------------------------------------------------ twins

def test_the_backend_copy_is_byte_identical():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend" / "model_budget.py").read_bytes() == \
        (root / "worker" / "model_budget.py").read_bytes()


def test_the_admin_summary_reads_the_same_numbers():
    out = model_budget.summary(World(wallet=200.0), now=NOW)
    assert out["on"] is True and out["next_model"] == OPUS
    assert out["allowance_usd"] == pytest.approx(185 / 18)
    assert out["reason_text"]
    assert model_budget.summary(World(budget={"on": False}), now=NOW) == {
        "on": False, "ladder": [OPUS, SONNET, HAIKU], "reserve_usd": 15.0,
        "refill_at": REFILL, "account_share": 0.4, "charge_as": HAIKU}
