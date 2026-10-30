"""New subscribers' Studio agent on the Claude lane (owner, 25 Oct 2026):
app_kv.studio_for_all_plans (default OFF). Existing subscribers keep EXACTLY
their contracted lanes; a paying user never breaks when the promotional
credit runs out (the OpenAI lanes stay behind the Claude lane and take the
same turn on a billing refusal); credits are charged at the contracted rate."""

import json
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import claude_lane
import claude_plans
import claude_trial
import config
import db as dbx
import llm


class _Db:
    def __init__(self, flag, applies=True):
        self.flag = flag
        self._applies = applies

    def run(self, fn, *args):
        if fn is claude_plans.read_flag:
            return claude_plans.parse_flag(self.flag)
        if fn is claude_plans.applies:
            return self._applies
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


ON = json.dumps({"on": True, "cutoff_at": "2026-11-02T00:00:00Z"})
JOB = {"id": 1, "user_id": 8}


def test_flag_off_means_todays_lanes_exactly(lane_up):
    assert agent_loop._claude_paid_lane(_Db(None), JOB, True, "pro_2026",
                                        {}) is None
    assert agent_loop._claude_paid_lane(
        _Db(json.dumps({"on": True})), JOB, True, "pro_2026", {}) is None


def test_grandfathered_subscribers_keep_their_lanes(lane_up):
    assert agent_loop._claude_paid_lane(_Db(ON, applies=False), JOB, True,
                                        "pro_2026", {}) is None


def test_a_new_subscriber_runs_on_the_plans_claude_model(lane_up):
    got = agent_loop._claude_paid_lane(_Db(ON), JOB, True, "pro_2026", {})
    assert got["lane"]["name"] == "claude_paid"
    # Owner decision (30 Oct 2026): Haiku for every new subscriber; credits
    # stay at the plan's contracted card (Pro = Sol).
    assert got["lane"]["model"] == "claude-haiku-5-5"
    assert got["credit_model"] == "gpt-6.1-sol"
    assert "test-anthropic-key-XYZ" not in json.dumps(
        {k: v for k, v in got["lane"].items() if k != "client"})
    plus = agent_loop._claude_paid_lane(_Db(ON), JOB, True, "plus_2026", {})
    assert plus["lane"]["model"] == "claude-haiku-5-5"
    assert plus["credit_model"] == "gpt-6-luna"


def test_no_claude_lane_without_credit_key_plan_or_for_operator_repairs(
        lane_up, monkeypatch):
    assert agent_loop._claude_paid_lane(_Db(ON), JOB, False, "free", {}) is None
    assert agent_loop._claude_paid_lane(
        _Db(ON), JOB, True, "pro_2026", {"operator_repair": True}) is None
    lane_up["on"] = False             # the credit switch is off
    assert agent_loop._claude_paid_lane(_Db(ON), JOB, True, "pro_2026",
                                        {}) is None
    lane_up["on"] = True
    claude_lane._close("billing: credit balance is too low")
    assert agent_loop._claude_paid_lane(_Db(ON), JOB, True, "pro_2026",
                                        {}) is None
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert agent_loop._claude_paid_lane(_Db(ON), JOB, True, "pro_2026",
                                        {}) is None


def test_a_lookup_error_keeps_the_contracted_lanes(lane_up):
    class _Broken:
        def run(self, fn, *a):
            raise RuntimeError("db down")

    assert agent_loop._claude_paid_lane(_Broken(), JOB, True, "pro_2026",
                                        {}) is None


class _StatusError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


class _Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.result


class _Sdk:
    def __init__(self, result):
        self.result = result
        self.messages = self

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        return _Stream(self.result)


def test_a_billing_refusal_for_a_paying_user_fails_over_and_alerts(
        lane_up, monkeypatch):
    """The loop's _switch_quota_lane moves a turn to the next lane exactly
    when llm.note_quota_error says the wallet is empty. A paying user's
    Claude refusal must be one, must flip the credit switch and must queue
    the owner alarm, and the next lane must be the contracted OpenAI one."""
    refused, alarms = [], []
    monkeypatch.setattr(claude_trial, "provider_refused",
                        lambda kind, exc: refused.append(kind))
    monkeypatch.setattr(claude_lane, "_sdk", lambda: _Sdk(_StatusError(
        400, "Your credit balance is too low to access the Anthropic API.")))
    monkeypatch.setattr(llm, "_quota_last", {})

    class _AlarmDb:
        def run(self, fn, *args, **kw):
            alarms.append(args)

        def reset(self):
            pass

    monkeypatch.setattr(dbx, "Db", _AlarmDb)
    paid = agent_loop._claude_paid_lane(_Db(ON), JOB, True, "plus_2026", {})
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-openai-test")
    lanes = [paid["lane"]] + llm.paid_editor_lanes("plus_2026")
    assert [l["name"] for l in lanes][:2] == ["claude_paid", "standard"]
    assert lanes[1]["model"] == config.EDITOR_MODEL
    client = llm.without_sdk_retries(lanes[0]["client"])
    with pytest.raises(claude_lane.ClaudeLaneUnavailable) as err:
        client.chat.completions.create(
            model=lanes[0]["model"],
            messages=[{"role": "user", "content": "trim the pauses"}])
    assert refused == ["billing"]
    assert llm.note_quota_error(err.value, lanes[0]["model"],
                                lanes[0]["base_url"]) is True
    assert alarms and alarms[0][0] == "llm_balance"   # the owner is alerted
    # the lane is latched closed: the next turn starts on OpenAI directly
    assert agent_loop._claude_paid_lane(_Db(ON), JOB, True, "plus_2026",
                                        {}) is None


def test_credit_parity_charges_the_contracted_rate():
    usage = SimpleNamespace(prompt_tokens=100_000, completion_tokens=2_000,
                            prompt_tokens_details={"cached_tokens": 80_000,
                                                   "cache_write_tokens": 10_000})
    luna = llm.credit_parity_cost_usd(usage, "gpt-6-luna")
    # 10K miss @0.10 + 80K hit @0.01 + 10K write @0.125 + 2K out @0.50
    assert luna == pytest.approx((10_000 * 0.10 + 80_000 * 0.01
                                  + 10_000 * 0.125 + 2_000 * 0.50) / 1e6)
    recorded = []

    def recorder(purpose, request, response, u):
        recorded.append(response)

    recorder.credit_price_aliases = {"claude-sonnet-5-5": "gpt-6.1-sol"}
    llm.set_recorder(recorder)
    try:
        u = SimpleNamespace(prompt_tokens=1000, completion_tokens=100,
                            prompt_tokens_details={"cached_tokens": 0,
                                                   "cache_write_tokens": 0},
                            provider_cost_usd=0.0031)
        llm.record("agent", {"model": "claude-sonnet-5-5"}, {"x": 1}, u)
    finally:
        llm.set_recorder(None)
    row = recorded[0]
    assert row["provider_cost_usd"] == 0.0031          # reports: true cost
    assert row["credit_price_model"] == "gpt-6.1-sol"
    assert row["credit_cost_usd"] == pytest.approx(
        (1000 * 2.0 + 100 * 10.0) / 1e6)


def test_the_charge_prefers_the_parity_cost_and_reports_keep_the_true_one():
    assert "credit_cost_usd" in dbx._CHARGE_COST_SQL
    assert "credit_cost_usd" not in dbx._ROW_COST_SQL
    import inspect
    source = inspect.getsource(dbx.charge_turn_credits)
    assert "_CHARGE_COST_SQL" in source


def test_the_in_turn_cap_uses_the_same_parity_rate():
    ctx = SimpleNamespace(model_usage={"claude-sonnet-5-5": {
        "in": 1_000_000, "cached": 0, "out": 0, "reasoning": 0}},
        images_generated=[], credit_price_aliases={}, gen_extra_cost_usd=0.0)
    true_cost = agent_tools.ToolContext.running_credits(ctx)
    ctx.credit_price_aliases = {"claude-sonnet-5-5": "gpt-6-luna"}
    parity = agent_tools.ToolContext.running_credits(ctx)
    assert parity < true_cost
