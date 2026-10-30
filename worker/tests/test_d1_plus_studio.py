"""Studio in Plus on Claude Haiku 5.5 (owner decision D1, 30 Oct 2026): the
worker half.

* The price cards the charge and the credit line use: Claude Haiku 5.5 and
  Sonnet 5.5 at Anthropic's published rates (checked 30 Oct 2026: Haiku 5.5
  $0.10 in / $0.50 out per 1M up to a 100K-token prompt, $0.50 / $2.50 above;
  5-minute cache writes 1.25x and cache reads 0.1x of input; Sonnet 5.5 $2 /
  $10, cache reads $0.20).
* A new Plus subscriber's Studio turn, with the row exactly as the owner's
  switch writes it, runs on claude-haiku-5-5 (the credited key, lane
  ``claude_paid``) and is charged credits at Plus's contracted card
  (gpt-6-luna), which is Haiku 5.5's own card: what the customer is charged
  per turn is the Haiku cost / USD_PER_CREDIT.
* A turn that STARTS while the switch is off or the credit line is AUTO-OFF
  never takes the Claude lane (a Plus request accepted before then finishes
  on the account's OpenAI lane; new requests meet the MCP path at the
  backend gate).
* A grandfathered subscriber never takes it, on any plan.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import agent_loop
import claude_lane
import claude_plans
import claude_trial
import config
import llm
import model_prices

CUTOFF = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
# The row after the owner's first ON (backend/studio_plus_toggle.switch).
SWITCHED_ON = json.dumps({"on": True, "cutoff_at": CUTOFF.isoformat(),
                          "studio_plans": ["plus_2026"],
                          "plan_models": {"plus_2026": "claude-haiku-5-5",
                                          "pro_2026": "claude-haiku-5-5",
                                          "max_2026": "claude-haiku-5-5"},
                          "updated_by": "admin"})
SWITCHED_OFF = json.dumps(dict(json.loads(SWITCHED_ON), on=False))
JOB = {"id": 41, "user_id": 8}


def test_haiku_and_sonnet_5_5_cards_are_anthropics_published_rates():
    haiku = model_prices.MODEL_PRICES["claude-haiku-5-5"]
    assert (haiku["in"], haiku["cached_in"], haiku["cache_write_in"],
            haiku["out"]) == (0.10, 0.01, 0.125, 0.50)
    assert haiku["reasoning_separate"] is False      # thinking is in output
    long_card = model_prices.CLAUDE_LONG_PROMPT["claude-haiku-5-5"]
    assert long_card["threshold"] == 100000
    assert (long_card["in"], long_card["cached_in"], long_card["cache_write_in"],
            long_card["out"]) == (0.50, 0.05, 0.625, 2.50)
    sonnet = model_prices.MODEL_PRICES["claude-sonnet-5-5"]
    assert (sonnet["in"], sonnet["cached_in"], sonnet["cache_write_in"],
            sonnet["out"]) == (2.00, 0.20, 2.50, 10.00)
    # a credit is $0.005 of metered usage (billed at 2x provider cost)
    assert model_prices.USD_PER_CREDIT == pytest.approx(0.005)


def test_one_haiku_call_costs_its_own_card_and_the_long_card_above_100k():
    # 20 uncached, 30K cache read, 2K cache write, 800 out
    short = model_prices.claude_usage_cost("claude-haiku-5-5", 20, 800,
                                           cache_read=30_000, cache_write=2_000)
    assert short == pytest.approx((20 * 0.10 + 30_000 * 0.01 + 2_000 * 0.125
                                   + 800 * 0.50) / 1e6)
    long = model_prices.claude_usage_cost("claude-haiku-5-5", 20, 800,
                                          cache_read=100_000, cache_write=2_000)
    assert long == pytest.approx((20 * 0.50 + 100_000 * 0.05 + 2_000 * 0.625
                                  + 800 * 2.50) / 1e6)


def test_plus_parity_card_is_haikus_own_card():
    """Credit parity charges a new Plus subscriber's Claude calls at Plus's
    contracted OpenAI card (gpt-6-luna). That card is Haiku 5.5's, so under
    a 100K-token prompt the credits are exactly Haiku's cost / $0.005."""
    assert claude_plans.contracted_model("plus_2026") == "gpt-6-luna"
    luna = model_prices.MODEL_PRICES["gpt-6-luna"]
    haiku = model_prices.MODEL_PRICES["claude-haiku-5-5"]
    for key in ("in", "cached_in", "cache_write_in", "out"):
        assert luna[key] == haiku[key], key
    # a request-sized call: prompt 31,940 tokens (30,100 cached, 1,200
    # written), 1,050 out
    usage = SimpleNamespace(prompt_tokens=31_940, completion_tokens=1_050,
                            prompt_tokens_details={"cached_tokens": 30_100,
                                                   "cache_write_tokens": 1_200})
    parity = llm.credit_parity_cost_usd(usage, "gpt-6-luna")
    true = model_prices.claude_usage_cost("claude-haiku-5-5", 640, 1_050,
                                          cache_read=30_100, cache_write=1_200)
    assert parity == pytest.approx(true)
    assert model_prices.usd_to_credits(parity) == pytest.approx(
        round(true / 0.005, 2))


class _Db:
    def __init__(self, flag, grandfathered=False):
        self.flag = flag
        self.grandfathered = grandfathered

    def run(self, fn, *args):
        if fn is claude_plans.read_flag:
            return claude_plans.parse_flag(self.flag)
        if fn is claude_plans.applies:
            user_id, subscribed, flag = args
            return bool(subscribed and flag["on"] and not self.grandfathered)
        raise AssertionError(fn)


@pytest.fixture
def lane_up(monkeypatch):
    claude_trial.reset_flag_cache()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-anthropic-key-XYZ")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    credit = {"on": True}
    monkeypatch.setattr(claude_trial, "credit_status",
                        lambda worker_db, max_age_s=20.0: credit)
    yield credit
    claude_lane.reopen()
    claude_trial.reset_flag_cache()


def test_a_new_plus_subscriber_runs_on_haiku_with_the_switch_on(lane_up):
    got = agent_loop._claude_paid_lane(_Db(SWITCHED_ON), JOB, True,
                                       "plus_2026", {})
    assert got["lane"]["name"] == "claude_paid"
    assert got["lane"]["model"] == "claude-haiku-5-5"
    assert got["credit_model"] == "gpt-6-luna"


def test_new_pro_and_max_subscribers_run_on_haiku_too_owner_decision_1030(
        lane_up):
    """Owner decision (30 Oct 2026): every NEW subscriber's Studio model is
    claude-haiku-5-5, Plus, Pro and Max alike, both with the row the switch
    writes and with a row that names no plan_models (the defaults)."""
    bare = json.dumps({"on": True, "cutoff_at": CUTOFF.isoformat()})
    for row in (SWITCHED_ON, bare):
        for plan, card in (("plus_2026", "gpt-6-luna"),
                           ("pro_2026", "gpt-6.1-sol"),
                           ("max_2026", "gpt-6.1-sol")):
            got = agent_loop._claude_paid_lane(_Db(row), JOB, True, plan, {})
            assert got["lane"]["model"] == "claude-haiku-5-5", (plan, row)
            assert got["credit_model"] == card       # prices unchanged


def test_switch_off_or_credit_line_off_never_starts_a_claude_turn(lane_up):
    assert agent_loop._claude_paid_lane(_Db(SWITCHED_OFF), JOB, True,
                                        "plus_2026", {}) is None
    lane_up["on"] = False                      # AUTO-OFF
    assert agent_loop._claude_paid_lane(_Db(SWITCHED_ON), JOB, True,
                                        "plus_2026", {}) is None
    lane_up["on"] = True                       # back with the next cycle
    assert agent_loop._claude_paid_lane(_Db(SWITCHED_ON), JOB, True,
                                        "plus_2026", {}) is not None


@pytest.mark.parametrize("plan", ["plus_2026", "pro_2026", "max_2026",
                                  "advanced", "ai", "ai_pro", "ai_max",
                                  "mcp_connect", "plus", "pro", "ultra"])
def test_a_grandfathered_subscriber_never_takes_the_claude_lane(lane_up, plan):
    assert agent_loop._claude_paid_lane(_Db(SWITCHED_ON, grandfathered=True),
                                        JOB, True, plan, {}) is None


def test_the_worker_copy_has_the_credit_line_predicates():
    """The worker's claude_plans is the backend's byte for byte (the backend
    test asserts it); the predicates the backend gates on exist here too."""
    for name in ("lane_open", "studio_included", "plus_studio_live",
                 "paused_for"):
        assert callable(getattr(claude_plans, name))
