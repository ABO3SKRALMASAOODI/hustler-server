"""Per-model pricing: the table, the SQL it generates, and the mirror.

The reason this file exists at all is that credits are now priced from the
`model` column of each llm_calls row rather than from a process-global env
constant. That is what makes it safe for a free user's turn to run on DeepSeek
while a subscriber's runs on Grok — and it is only safe while the worker (which
charges) and the backend (which reports) agree on the numbers.
"""

import importlib.util
import os

import pytest

import agent_tools
import config
import llm
import model_prices


REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _load_backend_copy():
    path = os.path.join(REPO, "backend", "model_prices.py")
    spec = importlib.util.spec_from_file_location("backend_model_prices", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_backend_mirror_has_not_drifted():
    """worker/ charges the credits, backend/ renders the admin cost view, and
    they are separate services that cannot import each other. If these two
    tables ever disagree the admin reports a spend nobody was charged — which
    is exactly how the cached-input bug stayed invisible for a day."""
    backend = _load_backend_copy()
    assert backend.MODEL_PRICES == model_prices.MODEL_PRICES
    fb = config.PRICE_FALLBACK
    assert backend.row_cost_sql(fb) == model_prices.row_cost_sql(fb)


def test_the_default_agent_model_is_priced():
    """An unlisted AGENT_MODEL silently falls back to the env constants. That
    is the right degradation, but it must not be the DEFAULT state."""
    assert model_prices.normalize(config.AGENT_MODEL) in \
        model_prices.MODEL_PRICES


def test_cached_input_is_cheaper_than_a_miss_for_every_model():
    for name, p in model_prices.MODEL_PRICES.items():
        assert p["cached_in"] < p["in"], name
        assert p["out"] > 0 and p["in"] > 0, name


def test_grok_cached_input_is_not_the_miss_price():
    """The comment this replaced told the operator to set Grok's cached rate
    equal to its miss rate ("Grok has no caching"). It does have caching, most
    of an agent turn's input hits it, and pricing those at the miss rate
    over-charges by ~6.7x — the identical bug that had just been fixed for
    DeepSeek."""
    grok = model_prices.MODEL_PRICES["grok-4.5"]
    assert grok["cached_in"] < grok["in"] / 5


def test_unknown_models_fall_back_instead_of_pricing_at_zero():
    p = model_prices.price_for("some-model-nobody-listed",
                               config.PRICE_FALLBACK)
    assert p["in"] == config.LLM_PRICE_IN_PER_M
    assert p["out"] == config.LLM_PRICE_OUT_PER_M
    # ...and it must NOT assume reasoning is billed separately, because that
    # would double-charge every provider that folds it into completion_tokens.
    assert p["reasoning_separate"] is False


def test_the_sql_carries_no_percent_sign():
    """psycopg2 scans the whole statement for its own placeholders before
    Postgres sees it, so one stray percent — even in a comment — raises
    IndexError. That took the admin Users page down once already."""
    assert "%" not in model_prices.row_cost_sql(config.PRICE_FALLBACK)


def test_the_sql_prices_each_model_separately():
    sql = model_prices.row_cost_sql(config.PRICE_FALLBACK)
    for name, p in model_prices.MODEL_PRICES.items():
        assert "'" + name + "'" in sql
        assert str(p["in"]) in sql
    assert "cached_in" in sql
    assert "reasoning_out" in sql


# ── reasoning tokens ────────────────────────────────────────────────────

class _Usage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Details:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


def test_reads_the_object_spelling():
    assert llm.reasoning_tokens(
        _Usage(completion_tokens_details=_Details(4200))) == 4200


def test_reads_the_dict_spelling():
    assert llm.reasoning_tokens(_Usage(
        completion_tokens_details={"reasoning_tokens": 99})) == 99


def test_reads_a_top_level_field():
    assert llm.reasoning_tokens(_Usage(reasoning_tokens=7)) == 7


def test_a_provider_reporting_none_yields_zero():
    assert llm.reasoning_tokens(_Usage(completion_tokens=100)) == 0
    assert llm.reasoning_tokens(None) == 0
    assert llm.reasoning_tokens(_Usage(reasoning_tokens="lots")) == 0


# ── the in-turn cap ─────────────────────────────────────────────────────

def _ctx():
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.images_generated = []
    ctx.gen_extra_cost_usd = 0.0
    ctx.tokens_in = ctx.tokens_out = ctx.tokens_cached_in = 0
    ctx.model_usage = {}
    ctx.credit_budget = None
    return ctx


def test_the_cap_prices_a_mixed_model_turn_per_model():
    """One turn, two providers — the agent on one and vision on another. A
    single blended rate is wrong for at least one of them by construction."""
    ctx = _ctx()
    ctx.add_usage("deepseek-v4-pro", 100_000, 5_000, cached_in=80_000)
    ctx.add_usage("grok-4.5", 20_000, 2_000, cached_in=0)

    ds = model_prices.MODEL_PRICES["deepseek-v4-pro"]
    gk = model_prices.MODEL_PRICES["grok-4.5"]
    expect = ((20_000 * ds["in"] + 80_000 * ds["cached_in"]
               + 5_000 * ds["out"]) +
              (20_000 * gk["in"] + 2_000 * gk["out"])) / 1e6
    assert ctx.running_credits() == round(expect / 0.01, 2)


def test_reasoning_is_charged_only_where_the_provider_bills_it_separately():
    folded = _ctx()
    folded.add_usage("deepseek-v4-pro", 1000, 1000, reasoning=50_000)
    plain = _ctx()
    plain.add_usage("deepseek-v4-pro", 1000, 1000)
    assert folded.running_credits() == plain.running_credits(), \
        "DeepSeek folds reasoning into completion_tokens — charging it again " \
        "would double-bill every reasoning turn"

    separate = _ctx()
    separate.add_usage("grok-4.5", 1000, 1000, reasoning=50_000)
    baseline = _ctx()
    baseline.add_usage("grok-4.5", 1000, 1000)
    assert separate.running_credits() > baseline.running_credits()


def test_the_cap_still_clamps_a_bogus_cache_count():
    ctx = _ctx()
    ctx.add_usage("grok-4.5", 1000, 0, cached_in=999_999)
    assert ctx.running_credits() >= 0


def test_totals_are_kept_alongside_the_breakdown():
    ctx = _ctx()
    ctx.add_usage("grok-4.5", 10, 20, cached_in=5, reasoning=7)
    ctx.add_usage("deepseek-v4-pro", 1, 2, cached_in=1)
    assert (ctx.tokens_in, ctx.tokens_out, ctx.tokens_cached_in) == (11, 22, 6)


# ── plan-based routing ──────────────────────────────────────────────────

def test_routing_is_a_no_op_until_the_paid_provider_is_configured():
    """Shipping this must change nothing. All three PAID_* vars default empty,
    so a subscriber and a free user get the identical client and model."""
    assert not llm.paid_available()
    free = llm.agent_client_for(False)
    paid = llm.agent_client_for(True)
    assert free[1] == paid[1] == config.AGENT_MODEL
    assert free[0] is paid[0]


def test_a_half_configured_paid_provider_is_treated_as_off(monkeypatch):
    """A base URL with no key would 401 every turn — for paying customers
    specifically. Off is the only safe reading of a partial config."""
    monkeypatch.setattr(config, "PAID_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "PAID_AGENT_MODEL", "grok-4.5")
    monkeypatch.setattr(config, "PAID_API_KEY", "")
    assert not llm.paid_available()
    assert llm.agent_client_for(True)[1] == config.AGENT_MODEL


def test_subscribers_get_the_paid_model_once_it_is_configured(monkeypatch):
    monkeypatch.setattr(config, "PAID_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "PAID_AGENT_MODEL", "grok-4.5")
    monkeypatch.setattr(config, "PAID_API_KEY", "xai-test-key")
    monkeypatch.setattr(llm, "_paid_client", None)
    try:
        assert llm.paid_available()
        assert llm.agent_client_for(True)[1] == "grok-4.5"
        assert llm.agent_client_for(False)[1] == config.AGENT_MODEL
    finally:
        llm._paid_client = None


def test_the_paid_model_is_priced():
    """Routing a customer onto a model with no price entry would charge them
    the fallback rate silently. Whatever PAID_AGENT_MODEL is set to must be in
    the table — this asserts it for the model we intend to use."""
    assert "grok-4.5" in model_prices.MODEL_PRICES


def test_reasoning_effort_is_off_by_default():
    """DeepSeek 400s on an unknown parameter, so the field must not be sent
    until someone opts in."""
    assert config.AGENT_REASONING_EFFORT == ""


def test_reasoning_effort_never_applies_to_the_first_iteration():
    """Iteration 0 is where the model reads the state and plans the edit. That
    is the thinking being paid for; only tool dispatch gets downgraded."""
    import inspect
    import agent_loop
    src = inspect.getsource(agent_loop._run_loop)
    assert "AGENT_REASONING_EFFORT and iteration > 0" in src
