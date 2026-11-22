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
    """app_kv, the wallet's meters, spend_ledger, llm_calls and video_jobs,
    by statement shape."""

    def __init__(self, wallet=69.04, baseline=100.0, spent_today=0.0,
                 lifetime=100.0, company=0.0, budget=None, account=None,
                 samples=None, cycle=12.0, line_cfg=None, fence=None,
                 lane_spent=None, since=0.0, confirmed_at=None, flying=(),
                 project=(), typical=None, source_s=None, progress=None,
                 request_premium=0.0):
        self.kv = {}
        if wallet is not None:
            self.kv["anthropic_wallet"] = json.dumps({
                "confirmed_balance_usd": wallet, "meter_baseline_usd": baseline,
                "confirmed_at": confirmed_at or "2026-11-20T10:00:00+00:00"})
        if budget is not None:
            self.kv["model_budget"] = json.dumps(budget)
        # a credit line well above the wallet unless a test sets one
        self.kv["claude_credit"] = json.dumps(line_cfg or {
            "cycle_budget_usd": 1000.0, "safety_margin_usd": 10,
            "cycle_start": "2026-11-05T00:00:00+00:00", "cycle_end": REFILL})
        # no company room unless a test gives the company lanes a share
        self.kv["spend_fence"] = json.dumps(fence or {
            "internal": {"*": {"day_usd": 0, "cycle_usd": 0}},
            "replay": {"anthropic": {"day_usd": 0, "cycle_usd": 0}}})
        self.lane_spent = lane_spent or {}
        self.spent_today = spent_today
        # the wallet meter counts today's customer spend as well
        self.lifetime = lifetime + spent_today
        self.company = company
        self.since = since
        self.account = account or {}
        self.samples = samples or {}
        self.cycle = cycle
        self.flying = list(flying)
        self.project = list(project)
        self.typical = typical
        self.source_s = source_s
        self.progress = progress
        self.request_premium = request_premium
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

    @staticmethod
    def _mixrow(mix):
        return {"prompt": mix["fresh"] + mix["cached"] + mix["written"],
                "cached": mix["cached"], "written": mix["written"],
                "completion": mix["out"]}

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        p = params or ()
        self.sql.append(q)
        self._rows = []
        if q.startswith(("SELECT pg_advisory_xact_lock", "SAVEPOINT", "RELEASE", "ROLLBACK")):
            return
        if q.startswith("SELECT value FROM app_kv"):
            v = self.kv.get(p[0])
            self._rows = [] if v is None else [{"value": v}]
        elif "FROM trial_budget_days WHERE day = %s" in q:
            self._rows = [{"usd": self.spent_today}]
        elif "WHERE day >= %s AND day < %s" in q:             # cycle spend
            self._rows = [{"usd": self.cycle}]
        elif "FROM trial_budget_days WHERE day >= %s" in q:   # since a refill
            self._rows = [{"usd": self.since}]
        elif "AS usd FROM trial_budget_days" in q:
            self._rows = [{"usd": self.lifetime}]
        elif q.startswith("SELECT to_regclass('public.spend_ledger')"):
            self._rows = [{"t": "spend_ledger"}]
        elif "WHERE provider = 'anthropic' AND lane = ANY(%s)" in q:
            self._rows = [{"lane": lane, "usd": usd}
                          for lane, usd in self.lane_spent.items()
                          if lane in p[0]]
        elif "lane<>'customer' AND day >= %s" in q:
            self._rows = [{"usd": 0.0}]
        elif "FROM spend_ledger" in q:
            self._rows = [{"usd": self.company}]
        elif "information_schema.columns" in q:
            self._rows = [{"x": 1}]
        elif "ROW_NUMBER() OVER" in q:
            models = p[1]
            self._rows = [{"model": m, "usd": usd}
                          for m in models for usd in self.samples.get(m, [])]
        elif "FROM video_jobs j WHERE j.type = 'agent_turn'" in q:  # in flight
            self._rows = [r for r in self.flying
                          if r["id"] != p[1] and r.get("root", r["id"]) != p[2]]
        elif "LEFT JOIN LATERAL" in q:                        # typical request
            if self.typical:
                row = self._mixrow(self.typical["mix"])
                row.update(duration_s=self.typical.get("duration_s"), n=50)
                self._rows = [row]
            else:
                self._rows = [{"prompt": None, "cached": None, "written": None,
                               "completion": None, "duration_s": None, "n": 0}]
        elif "FROM assets WHERE project_id = %s" in q:
            self._rows = [{"dur": self.source_s}]
        elif "COUNT(DISTINCT l.job_id) AS slices" in q:       # this request
            if self.progress:
                row = self._mixrow(self.progress["mix"])
                row["slices"] = self.progress["slices"]
            else:
                row = {"prompt": None, "cached": None, "written": None,
                       "completion": None, "slices": 0}
            self._rows = [row]
        elif "(%s = 0 OR l.project_id = %s)" in q:            # its premium cost
            self._rows = [{"usd": self.request_premium}]
        elif "WHERE l.project_id = %s AND l.purpose = %s" in q:  # its project
            self._rows = [self._mixrow(m) for m in self.project]
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
def fresh(monkeypatch):
    monkeypatch.setattr(model_budget, "_now", lambda now=None: now or NOW)
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
    assert got["plan_model"] == HAIKU and got["markup"] == 1.5
    assert got["expected_usd"] == pytest.approx(1.2)


def test_configured_sonnet_when_only_sonnet_fits():
    # (69.04-15)/18 = $3.00 a day, $2.0 already spent: Opus 1.2 does not fit,
    # Sonnet 0.6 does (and is inside the account's 40% share)
    got = _decide(World(wallet=67.04, spent_today=2.0, budget={
        "plan_ladder": {"grandfathered": [OPUS, SONNET]}}))
    assert got["model"] == SONNET and got["reason"] == "fits"


def test_haiku_when_nothing_fits_and_the_request_still_runs():
    got = _decide(World(wallet=67.04 - 1.0, spent_today=2.9))
    assert got["model"] == HAIKU and not got["upgraded"]
    assert got["reason"] == "daily_allowance"
    # an empty wallet: the floor, never a refusal
    got = _decide(World(wallet=0.0))
    assert got["model"] == HAIKU and got["allowance_usd"] == 0.0


def test_account_spend_does_not_demote_paid_requests():
    assert _decide(World(wallet=200, account={CUSTOMER: 4}))["model"] == OPUS


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
    assert model_budget.pick(ladder, HAIKU, 10, 0, costs) == (OPUS, "fits")
    assert model_budget.pick(ladder, SONNET, 10, 0, costs)[0] == OPUS
    assert model_budget.pick(ladder, OPUS, 10, 0, costs)[0] == OPUS
    assert model_budget.pick(ladder, "gpt-6.1-sol", 10, 0, costs) == (
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


def test_after_the_refill_the_wallet_is_assumed_refilled_and_upgrades_go_on():
    """Owner, 20 Nov: the subscription refills the credit at 8 Dec. With no
    balance confirmed after the refill, the wallet is nonexpiring + refill -
    the spend since, the next refill moves a cycle on, and paying requests
    keep being upgraded (before the fix: "refill_passed", Haiku)."""
    late = datetime(2026, 12, 9, 12, tzinfo=UTC)
    w = World(wallet=20.0, since=10.0)
    cfg = model_budget.parse_config(None)
    state = model_budget.budget_state(w, cfg, late)
    assert state["problem"] is None
    assert state["wallet_basis"] == "assumed_refill"
    assert state["wallet_usd"] == pytest.approx(4.26 + 200.0 - 10.0)
    assert state["last_refill_at"] == "2026-12-08T00:00:00+00:00"
    assert state["next_refill_at"] == "2027-01-08T00:00:00+00:00"
    assert state["days_left"] == 30.0
    assert state["assumed"]["spent_since_refill_usd"] == pytest.approx(10.0)
    assert state["allowance_usd"] == pytest.approx((194.26 - 15.0) / 30.0)
    got = model_budget.decide(w, CUSTOMER, HAIKU, True, now=late)
    assert got["model"] == OPUS and got["wallet_basis"] == "assumed_refill"
    # a balance confirmed after the refill always wins
    w = World(wallet=50.0, confirmed_at="2026-12-08T09:00:00+00:00")
    state = model_budget.budget_state(w, cfg, late)
    assert state["wallet_basis"] == "confirmed"
    assert state["wallet_usd"] == pytest.approx(50.0)
    # and a cycle later it repeats: 8 Jan passed, nothing confirmed since
    later = datetime(2027, 1, 8, 6, tzinfo=UTC)
    state = model_budget.budget_state(World(wallet=50.0, since=1.0,
                                            confirmed_at="2026-12-08T09:00:00+00:00"),
                                      cfg, later)
    assert state["wallet_basis"] == "assumed_refill"
    assert state["last_refill_at"] == "2027-01-08T00:00:00+00:00"
    assert state["wallet_usd"] == pytest.approx(203.26)


@pytest.mark.parametrize("line_end", [REFILL, "2026-11-08T00:00:00+00:00"])
def test_refills_stay_on_the_8th_across_december_january_and_february(line_end):
    """claude_credit's cycle_end equals refill_at in production (both 8 Dec):
    the refills follow its monthly cycle. When its cycle_end is earlier, the
    governor's own refill_at steps by calendar month on the same day. Before
    the fix both paths stepped 30 days: 7 Jan, 6 Feb, 8 Mar..."""
    w = World(line_cfg={"cycle_budget_usd": 1000.0, "safety_margin_usd": 10,
                        "cycle_end": line_end})
    cfg = model_budget.parse_config(None)
    expect = [
        (datetime(2026, 12, 7, 23, tzinfo=UTC), "2026-11-08", "2026-12-08"),
        (datetime(2026, 12, 8, tzinfo=UTC), "2026-12-08", "2027-01-08"),
        (datetime(2026, 12, 31, tzinfo=UTC), "2026-12-08", "2027-01-08"),
        (datetime(2027, 1, 7, 23, tzinfo=UTC), "2026-12-08", "2027-01-08"),
        (datetime(2027, 1, 8, 1, tzinfo=UTC), "2027-01-08", "2027-02-08"),
        (datetime(2027, 2, 7, tzinfo=UTC), "2027-01-08", "2027-02-08"),
        (datetime(2027, 2, 8, tzinfo=UTC), "2027-02-08", "2027-03-08"),
        (datetime(2027, 2, 28, tzinfo=UTC), "2027-02-08", "2027-03-08"),
    ]
    for now, last, nxt in expect:
        got_last, got_next, source = model_budget.refill_window(w, cfg, now)
        assert (got_last.date().isoformat(), got_next.date().isoformat()) == (
            last, nxt), now
        assert source == ("claude_credit" if line_end == REFILL
                          else "model_budget")


def test_own_monthly_refills_clamp_to_the_months_end():
    w = World(line_cfg={"cycle_budget_usd": 1000.0, "safety_margin_usd": 10,
                        "cycle_end": "2026-11-08T00:00:00+00:00"})
    cfg = model_budget.parse_config({"refill_at": "2027-01-31T00:00:00+00:00"})
    last, nxt, _ = model_budget.refill_window(w, cfg, datetime(2027, 3, 1, tzinfo=UTC))
    assert (last.date().isoformat(), nxt.date().isoformat()) == (
        "2027-02-28", "2027-03-31")


def test_claude_credits_own_cycle_end_wins_once_it_moved_past_refill_at():
    late = datetime(2026, 12, 20, tzinfo=UTC)
    w = World(wallet=20.0, since=5.0, line_cfg={
        "cycle_budget_usd": 1000.0, "safety_margin_usd": 10,
        "cycle_end": "2027-01-08T00:00:00+00:00"})
    state = model_budget.budget_state(w, model_budget.parse_config(None), late)
    assert state["refill_source"] == "claude_credit"
    assert state["last_refill_at"] == "2026-12-08T00:00:00+00:00"
    assert state["next_refill_at"] == "2027-01-08T00:00:00+00:00"
    assert state["wallet_basis"] == "assumed_refill"


def test_until_the_refill_only_what_expires_is_spread():
    # with no reserve, the purchased (non-expiring) $4.26 is kept, the
    # promotional rest is spread over the days to the refill
    w = World(wallet=69.04)
    cfg = model_budget.parse_config({"reserve_usd": 0})
    state = model_budget.budget_state(w, cfg, NOW)
    assert state["keep_usd"] == pytest.approx(4.26)
    assert state["allowance_usd"] == pytest.approx((69.04 - 4.26) / 18.0)
    assert state["next_refill_at"] == REFILL and state["wallet_basis"] == "confirmed"


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
    w = World(wallet=200.0, samples={OPUS: [15.0] * 20})
    assert _decide(w)["model"] == HAIKU


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
                        lambda conn, *a, **kw: model_budget._decision(
                            OPUS, HAIKU, "fits", None))
    world, payload = World(), {}
    route = _route(world, payload)
    assert route["lane"]["model"] == OPUS
    assert route["helper_lane"]["model"] == HAIKU
    assert OPUS not in route["credit_aliases"]
    assert payload["model_budget"]["model"] == OPUS
    assert payload["model_budget"]["root"] == 501
    assert world.stamped and world.stamped[0][0] == 501
    # Shorts' helper lane is not an editing request: no governor
    plain = _route(World(), {}, govern=False)
    assert plain["lane"]["model"] == HAIKU and "model_budget" not in plain


def test_a_continuation_keeps_its_model_while_its_next_slice_fits(
        lane_up, monkeypatch):
    calls = []

    def decide(conn, *a, **kwargs):
        calls.append(a)
        return model_budget._decision(OPUS, HAIKU, "fits", None)

    monkeypatch.setattr(model_budget, "decide", decide)
    first = {}
    _route(World(wallet=200.0), first)
    assert len(calls) == 1
    # the continuation's payload is a copy of the first slice's; its first
    # slice cost $0.30 on Opus and the next one fits the day
    cont = dict(first, root_agent_job_id=501, continuation_sequence=1,
                logical_turn_continuation=True)
    slice_mix = {"fresh": 10_000, "cached": 300_000, "written": 30_000,
                 "out": 6_000}
    route = _route(World(wallet=200.0, progress={"mix": slice_mix, "slices": 1}),
                   cont, job={"id": 502, "user_id": CUSTOMER})
    assert len(calls) == 1                      # not a new request
    assert route["lane"]["model"] == OPUS
    assert cont["model_budget"]["reason"] == "continuation"
    assert cont["model_budget"]["expected_usd"] == pytest.approx(
        model_budget.mix_cost(OPUS, slice_mix))
    # a NEW request (no inherited decision) is decided afresh
    monkeypatch.setattr(model_budget, "decide", lambda conn, *a: (
        model_budget._decision(HAIKU, HAIKU, "daily_allowance", None)))
    assert _route(World(), {}, job={"id": 600, "user_id": CUSTOMER}
                  )["lane"]["model"] == HAIKU


def test_a_long_request_steps_down_the_ladder_at_its_slice_boundaries(lane_up):
    """Review 20 Nov: a request that turns out long (the golden p5857 needs
    10-12M tokens) must not run every slice on Opus. Each slice boundary
    decides again from the request's own spend; before the fix the model was
    kept for every slice whatever it cost."""
    previous = model_budget._decision(OPUS, HAIKU, "fits", None,
                                      expected_usd=1.2)
    previous.update(root=501, plan="plus_2026")
    budget = {"plan_ladder": {"plus_2026": [OPUS, SONNET]}}
    # 2 slices so far, 3M tokens: about $1.65 a slice on Opus, $1.02 on
    # Sonnet. Today: (69.04-15)/18 = $3.00, $1.9 spent (most of it this
    # request's): Opus no longer fits, Sonnet does.
    mix = {"fresh": 100_000, "cached": 2_600_000, "written": 260_000,
           "out": 40_000}
    cont = {"model_budget": dict(previous), "root_agent_job_id": 501}
    world = World(budget=budget, wallet=69.04 - 1.9, spent_today=1.9,
                  progress={"mix": mix, "slices": 2}, request_premium=1.9,
                  account={CUSTOMER: 0.0})
    route = _route(world, cont, job={"id": 503, "user_id": CUSTOMER})
    got = cont["model_budget"]
    assert route["lane"]["model"] == SONNET and route["helper_lane"]["model"] == HAIKU
    assert got["reason"] == "stepped_down" and got["stepped_from"] == OPUS
    assert got["slices"] == 2 and got["request_premium_usd"] == pytest.approx(1.9)
    assert got["expected_usd"] == pytest.approx(
        model_budget.mix_cost(SONNET, {k: v / 2 for k, v in mix.items()}))
    # more spent: Sonnet no longer fits either, the request finishes on the
    # plan's model (never stopped)
    cont = {"model_budget": dict(previous), "root_agent_job_id": 501}
    world = World(budget=budget, wallet=69.04 - 2.5, spent_today=2.5,
                  progress={"mix": mix, "slices": 2}, request_premium=2.5)
    route = _route(world, cont, job={"id": 504, "user_id": CUSTOMER})
    assert route["lane"]["model"] == HAIKU and "helper_lane" not in route
    assert cont["model_budget"]["reason"] == "stepped_down"
    assert cont["model_budget"]["upgraded"] is False
    # a stepped-down request never climbs back
    sonnet = model_budget._decision(SONNET, HAIKU, "stepped_down", None)
    sonnet.update(root=501, plan="plus_2026")
    got = model_budget.reconsider(World(budget=budget, wallet=500.0, progress={
        "mix": {k: v / 100 for k, v in mix.items()}, "slices": 1}),
        CUSTOMER, HAIKU, sonnet, 501, now=NOW)
    assert got["model"] == SONNET and got["reason"] == "continuation"


def test_the_first_decision_is_priced_from_the_requests_size():
    """Review 20 Nov: expected_usd was flat ($1.20 Opus) whatever the source.
    A project whose requests use millions of tokens is priced on Opus's card
    from its own history; a short one from the typical request scaled by its
    source length."""
    big = {"fresh": 150_000, "cached": 4_000_000, "written": 400_000,
           "out": 60_000}
    # $10.3 a day: the flat $1.20 would give Opus; this project's own
    # requests cost about $4.0 on Opus, which still fits the day
    w = World(wallet=200.0, project=[big, big])
    got = model_budget.decide(w, CUSTOMER, HAIKU, True, project_id=77,
                              root=900, now=NOW)
    assert got["estimate_basis"] == "project"
    assert got["costs"][OPUS] == pytest.approx(model_budget.mix_cost(OPUS, big),
                                               abs=1e-3)
    assert got["model"] == OPUS
    assert got["expected_usd"] == pytest.approx(model_budget.mix_cost(OPUS, big),
                                                abs=1e-3)
    # no history on the project: the typical request, scaled by source length
    typical = {"mix": {"fresh": 14_000, "cached": 327_000, "written": 67_000,
                       "out": 10_000}, "duration_s": 1000.0}
    short = World(wallet=200.0, typical=typical, source_s=500.0)
    got = model_budget.decide(short, CUSTOMER, HAIKU, True, project_id=78,
                              now=NOW)
    assert got["estimate_basis"] == "source_length" and got["model"] == OPUS
    assert got["expected_usd"] == pytest.approx(
        model_budget.mix_cost(OPUS, typical["mix"]) * 0.5, abs=1e-3)
    long = World(wallet=200.0, typical=typical, source_s=60_000.0)
    got = model_budget.decide(long, CUSTOMER, HAIKU, True, project_id=79,
                              now=NOW)
    # scaled at most 4x
    assert got["costs"][OPUS] == pytest.approx(
        model_budget.mix_cost(OPUS, typical["mix"]) * 4.0, abs=1e-3)


def test_requests_that_start_together_count_each_other():
    """Review 20 Nov: spent_today is cached and counts only metered calls, so
    requests starting together could all get Opus. Upgraded jobs queued or
    running count at what they are still expected to spend."""
    flying = [{"id": 700, "user_id": CUSTOMER + 5, "expected": 6.0, "spent": 0.5},
              {"id": 701, "user_id": CUSTOMER + 6, "expected": 3.0, "spent": 0.0}]
    # $10.28 a day; without the in-flight jobs Opus (1.2) fits
    assert _decide(World(wallet=200.0))["model"] == OPUS
    got = _decide(World(wallet=200.0, flying=flying))
    assert got["inflight_usd"] == pytest.approx(8.5)
    # 8.5 + 1.2 = 9.7 fits; with one more they no longer do
    assert got["model"] == OPUS
    flying.append({"id": 702, "user_id": CUSTOMER + 7, "expected": 1.0, "spent": 0.0})
    got = _decide(World(wallet=200.0, flying=flying))
    assert got["model"] == HAIKU and got["inflight_usd"] == pytest.approx(9.5)
    # reservations count against the common daily wallet
    mine = [{"id": 710, "user_id": CUSTOMER, "expected": 3.5, "spent": 0.0}]
    assert _decide(World(wallet=200.0, flying=mine))["model"] == OPUS
    # a request's own jobs are never in its way
    got = model_budget.decide(World(wallet=200.0, flying=[
        {"id": 900, "user_id": CUSTOMER, "expected": 9.0, "spent": 0.0}]),
        CUSTOMER, HAIKU, True, root=900, now=NOW)
    assert got["model"] == OPUS


def test_company_room_comes_off_the_wallet_before_it_is_spread():
    """Review 20 Nov: the replay and internal lanes may still spend their
    cycle shares from the same wallet; production read $20.96 replay and
    $20 internal. Their remaining room is subtracted before spreading, from
    the ledger spend_ledger.room and the fence read."""
    w = World(wallet=69.04, fence={
        "replay": {"anthropic": {"day_usd": 10.0, "cycle_usd": 72.37}}},
        lane_spent={"replay": 51.41, "internal": 0.0})
    state = model_budget.budget_state(w, model_budget.parse_config(None), NOW)
    assert state["company_room"] == {"internal": pytest.approx(20.0),
                                     "replay": pytest.approx(20.96)}
    assert state["company_room_usd"] == pytest.approx(40.96)
    assert state["allowance_usd"] == pytest.approx((69.04 - 40.96 - 15.0) / 18.0)
    assert _decide(w)["model"] == HAIKU          # $0.73 a day: nothing fits
    # a spent share leaves no room to subtract
    w = World(wallet=69.04, lane_spent={"replay": 100.0, "internal": 25.0},
              fence={"replay": {"anthropic": {"day_usd": 10, "cycle_usd": 72.37}}})
    state = model_budget.budget_state(w, model_budget.parse_config(None), NOW)
    assert state["company_room_usd"] == 0.0
    # the same numbers spend_ledger.room gives the fence
    import spend_ledger
    w = World(wallet=69.04, fence={
        "replay": {"anthropic": {"day_usd": 100.0, "cycle_usd": 72.37}}},
        lane_spent={"replay": 51.41})
    w.execute = _per_lane(w.execute, w)
    room = spend_ledger.room(w, "replay", "anthropic", now=NOW)
    assert model_budget.company_room(w, NOW)["lanes"]["replay"] == \
        pytest.approx(room["cycle_usd"] - room["cycle_spent_usd"])


def _per_lane(execute, world):
    """spend_ledger._spent's own statement, per lane, for the comparison."""
    def run(sql, params=None):
        q = " ".join(sql.split())
        if "FROM spend_ledger WHERE lane = %s AND provider = %s" in q:
            world._rows = [{"usd": world.lane_spent.get(params[0], 0.0)}]
            return
        execute(sql, params)
    return run


def test_a_replay_account_is_governed_when_the_replay_is_listed(monkeypatch):
    """A golden replay runs with SPEND_LANE=replay and a synthetic account
    whose id is not known in advance: "replay" in test_user_ids governs it."""
    monkeypatch.setenv("SPEND_LANE", "replay")
    w = World(wallet=200.0)
    got = _decide(w, user=123456)
    assert got["model"] == HAIKU and got["reason"] == "not_customer"
    w = World(wallet=200.0, budget={"test_user_ids": ["replay"]})
    assert _decide(w, user=123456)["model"] == OPUS
    # a known id works too
    w = World(wallet=200.0, budget={"test_user_ids": [123456]})
    assert _decide(w, user=123456)["model"] == OPUS
    # production (no SPEND_LANE) never reads "replay" as an internal account
    monkeypatch.delenv("SPEND_LANE")
    w = World(wallet=200.0, budget={"test_user_ids": ["replay"]})
    assert _decide(w, user=INTERNAL)["reason"] == "not_customer"


def test_a_retried_slice_stays_on_opus_with_wallet_room(
        lane_up):
    previous = model_budget._decision(OPUS, HAIKU, "fits", None)
    previous["root"] = 501
    cont = {"model_budget": previous, "root_agent_job_id": 501,
            "continuation_state": {"exception_fingerprint": "boom #"}}
    route = _route(World(), cont, job={"id": 502, "user_id": CUSTOMER})
    assert route["lane"]["model"] == OPUS
    assert cont["model_budget"]["reason"] == "continuation"


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


# ------------------------------------------------------------------ helpers

class _Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.result


class _FakeSdk:
    def __init__(self, results):
        self.results = list(results)
        self.bodies = []
        self.messages = self

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        self.bodies.append(body)
        return _Stream(self.results.pop(0))


def test_a_governed_opus_turns_request_contract_call_goes_to_haiku_forced(
        lane_up, monkeypatch):
    """Review 20 Nov: helpers read ctx.agent_model / ctx.llm_client, so on an
    upgraded turn they ran on Opus, whose lane drops a forced tool_choice
    (claude_lane._NO_FORCED_TOOL): the contract could come back as text and
    fall back. They go to the plan's Haiku, with the tool call forced."""
    import request_contract
    name = "record_contract"
    sdk = _FakeSdk([SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name=name,
                                 input={"ok": True})],
        stop_reason="tool_use", stop_details=None, _request_id="r",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))])
    monkeypatch.setattr(claude_lane, "_sdk", lambda: sdk)
    monkeypatch.setattr(claude_trial, "meter_paid", lambda model, usd: None)
    monkeypatch.setattr(claude_trial, "meter",
                        lambda model, usd, kind="llm": None)
    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx.llm_client, ctx.agent_model = claude_lane.ClaudeLaneClient(), OPUS
    ctx.helper_client = claude_lane.ClaudeLaneClient()
    ctx.helper_model = HAIKU
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    try:
        request_contract._call(ctx, name, schema, "system", {"x": 1})
    except Exception:  # noqa: BLE001 - the body sent is what is checked
        pass
    body = sdk.bodies[0]
    assert body["model"] == HAIKU
    assert body["tool_choice"] == {"type": "tool", "name": name}
    # without a helper lane the turn's own model answers, as before
    assert llm.helper_lane(SimpleNamespace(llm_client="c", agent_model=OPUS)) \
        == ("c", OPUS)


# ------------------------------------------------------------------ billing

def test_an_explicit_legacy_credit_parity_alias_is_still_honored():
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
    assert out["next_refill_at"] == REFILL and out["wallet_basis"] == "confirmed"
    assert out["refill_usd"] == 200.0 and out["nonexpiring_usd"] == 4.26
    assert out["company_room_usd"] == 0.0 and "inflight_usd" in out
    # after the refill the card shows the assumption
    late = model_budget.summary(World(wallet=20.0, since=3.0),
                                now=datetime(2026, 12, 9, tzinfo=UTC))
    assert late["wallet_basis"] == "assumed_refill"
    assert late["assumed"]["wallet_usd"] == pytest.approx(201.26)
    assert late["next_refill_at"] == "2027-01-08T00:00:00+00:00"
    assert model_budget.summary(World(budget={"on": False}), now=NOW) == {
        "on": False, "ladder": [OPUS, SONNET, HAIKU], "reserve_usd": 15.0,
        "refill_at": REFILL, "refill_usd": 200.0, "nonexpiring_usd": 4.26,
        "cycle_days": 30.0, "plan_ladder": model_budget.DEFAULTS["plan_ladder"], "markup": 1.5}


def test_question_need_asks_the_plans_helper_model_not_the_upgrade(monkeypatch):
    """_question_needs_customer forces a tool call (question_need). On an
    upgraded request agent_lanes[0] is Opus/Sonnet, which drops a forced
    tool_choice (claude_lane._NO_FORCED_TOOL): the judge goes to the plan's
    helper model (llm.helper_lane) like every other helper."""
    import question_need
    import quality_verifier
    seen = []
    monkeypatch.setattr(question_need, "needed",
                        lambda client, model, request, q: seen.append(
                            (client, model)) or True)
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "edit")
    opus, haiku = object(), object()
    ctx = SimpleNamespace(
        agent_lanes=[{"client": opus, "model": OPUS},
                     {"client": haiku, "model": HAIKU}],
        llm_client=opus, agent_model=OPUS,
        helper_client=haiku, helper_model=HAIKU)
    assert agent_tools._question_needs_customer(ctx, "Which file?") is True
    assert seen[-1] == (haiku, HAIKU)
    # not upgraded: the turn's own client and model, as before
    ctx = SimpleNamespace(agent_lanes=[{"client": haiku, "model": HAIKU}],
                          llm_client=haiku, agent_model=HAIKU)
    agent_tools._question_needs_customer(ctx, "Which file?")
    assert seen[-1] == (haiku, HAIKU)
    # a bare context with only its lanes keeps working
    ctx = SimpleNamespace(agent_lanes=[{"client": haiku, "model": "m"}])
    agent_tools._question_needs_customer(ctx, "Which file?")
    assert seen[-1] == (haiku, "m")


@pytest.mark.parametrize("plan", ["plus_2026", "pro_2026", "max_2026", "ai_pro"])
def test_paid_opus_week_2026_11_21(plan):
    picked = model_budget.decide(World(wallet=200), CUSTOMER, HAIKU, True, now=NOW, plan=plan)
    assert picked["model"] == OPUS
    assert "upgrade your plan" in model_prices.low_balance_notice(picked, 1)
    assert model_prices.low_balance_notice(picked, 5000) is None
    assert model_budget.decide(World(wallet=0), CUSTOMER, HAIKU, True, now=NOW, plan=plan)["model"] == HAIKU


@pytest.mark.parametrize("plan", ["free", "mcp_connect"])
def test_free_and_mcp_do_not_get_editing_upgrade(plan):
    assert model_budget.plan_rungs(model_budget.parse_config(None), plan, HAIKU) == [HAIKU]
