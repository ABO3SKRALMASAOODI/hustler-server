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


@pytest.fixture(autouse=True)
def _default_lane_has_a_key(monkeypatch):
    """These tests assert ROUTING — which model id each plan resolves to — and
    never make a call. But agent_client_for builds the default lane's client on
    the way, and the OpenAI SDK refuses to construct one with no key, so they
    were passing only because some test that happened to run earlier had
    already primed llm._client. That made them order-dependent: adding an
    unrelated test FILE ahead of this one in collection order was enough to
    turn five of them red on a machine with no OPENAI_API_KEY set. A test about
    which model id we pick must not depend on the ambient environment.
    """
    if not config.OPENAI_API_KEY:
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key-not-used")
        monkeypatch.setattr(llm, "_client", None)
    yield
    llm._client = None


def test_the_default_agent_model_is_priced():
    """An unlisted AGENT_MODEL silently falls back to the env constants. That
    is the right degradation, but it must not be the DEFAULT state."""
    assert model_prices.normalize(config.AGENT_MODEL) in \
        model_prices.MODEL_PRICES


def test_cached_input_never_costs_more_than_a_miss_for_every_model():
    for name, p in model_prices.MODEL_PRICES.items():
        assert p["cached_in"] <= p["in"], name
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
    assert "audio_in" in sql and "audio_out" in sql


def test_audio_model_uses_official_modality_prices():
    audio = model_prices.price_for("gpt-audio-1.5", config.PRICE_FALLBACK)
    assert audio["in"] == 2.50 and audio["out"] == 10.00
    assert audio["audio_in"] == 32.00 and audio["audio_out"] == 64.00


# ── reasoning tokens ────────────────────────────────────────────────────

class _Usage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Details:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


class _AudioDetails:
    def __init__(self, audio_tokens):
        self.audio_tokens = audio_tokens


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


def test_reads_audio_token_details_without_double_pricing_them_as_text():
    usage = _Usage(
        prompt_tokens_details=_AudioDetails(800),
        completion_tokens_details={"audio_tokens": 120})
    assert llm.audio_token_counts(usage) == (800, 120)


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
    assert ctx.running_credits() == model_prices.usd_to_credits(expect)


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


def test_audio_tokens_use_the_audio_rate_in_the_live_cap():
    ctx = _ctx()
    ctx.add_usage("gpt-audio-1.5", 1000, 200,
                  audio_in=800, audio_out=100)
    p = model_prices.price_for("gpt-audio-1.5", config.PRICE_FALLBACK)
    expect = ((200 * p["in"] + 800 * p["audio_in"]
               + 100 * p["out"] + 100 * p["audio_out"]) / 1e6)
    assert ctx.running_credits() == model_prices.usd_to_credits(expect)


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

def _paid_on(monkeypatch, key="xai-test-key"):
    monkeypatch.setattr(config, "PAID_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "PAID_AGENT_MODEL", "grok-4.5")
    monkeypatch.setattr(config, "PAID_API_KEY", key)
    monkeypatch.setattr(llm, "_paid_client", None)


def _frontier_on(monkeypatch, key="xai-test-key"):
    monkeypatch.setattr(config, "FRONTIER_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "FRONTIER_AGENT_MODEL", "grok-4.5")
    monkeypatch.setattr(config, "FRONTIER_VISION_MODEL", "grok-4.5")
    monkeypatch.setattr(config, "FRONTIER_API_KEY", key)
    monkeypatch.setattr(llm, "_frontier_client", None)


def test_a_half_configured_paid_provider_is_treated_as_off(monkeypatch):
    """A base URL with no key would 401 every turn — for paying customers
    specifically. Off is the only safe reading of a partial config."""
    _paid_on(monkeypatch, key="")
    assert not llm.paid_available()
    assert llm.agent_client_for(True, "ai_pro")[1] == config.AGENT_MODEL


def test_only_frontier_changes_the_model_as_shipped(monkeypatch):
    """The shipped lineup is TWO volume steps and ONE model step.

    Pro shipped for an afternoon badged "MORE INTELLIGENCE" while resolving to
    the same model id as Frontier — grok-4.5 is the strongest model anything in
    this stack points at, so "stronger than Creator" and "the strongest we
    have" were the same string, and the $100 card's argument was false. Pro
    went back to selling room. This asserts the routing matches the cards.
    """
    _frontier_on(monkeypatch)
    try:
        assert config.PAID_PLANS == set(), \
            "PAID_PLANS must ship empty — see the config comment"
        for plan in ("free", "ai", "ai_pro"):
            assert llm.agent_client_for(plan != "free", plan)[1] == \
                config.AGENT_MODEL, plan
        assert llm.agent_client_for(True, "ai_max")[1] == "grok-4.5"
    finally:
        llm._frontier_client = None


def test_promoting_pro_to_a_model_tier_is_one_env_var(monkeypatch):
    """The lane stays wired so Pro can become a model tier the day there IS
    something between the standard and frontier models."""
    _paid_on(monkeypatch)
    monkeypatch.setattr(config, "PAID_PLANS", {"ai_pro"})
    try:
        assert llm.agent_client_for(True, "ai_pro")[1] == "grok-4.5"
        # ...and Creator must NOT come with it.
        assert llm.agent_client_for(True, "ai")[1] == config.AGENT_MODEL
    finally:
        llm._paid_client = None


def test_a_trial_previews_its_own_plans_model(monkeypatch):
    """A Creator trial must NOT be served a better plan's model. Previewing a
    model the customer stops getting the moment they pay is the bait-and-switch
    that per-plan routing exists to prevent."""
    _paid_on(monkeypatch)
    monkeypatch.setattr(config, "PAID_PLANS", {"ai_pro"})
    _frontier_on(monkeypatch)
    try:
        assert llm.agent_client_for(True, "ai")[1] == config.AGENT_MODEL
        assert llm.frontier_client() is not llm.paid_client()
    finally:
        llm._paid_client = None
        llm._frontier_client = None


def test_an_unconfigured_tier_degrades_instead_of_401ing(monkeypatch):
    """No key anywhere: every plan falls back to the base model. A worse edit
    is recoverable; a 401 on every turn of a $100 plan is not."""
    _paid_on(monkeypatch, key="")
    monkeypatch.setattr(config, "FRONTIER_API_KEY", "")
    for plan in ("ai", "ai_pro", "ai_max"):
        assert llm.agent_client_for(True, plan)[1] == config.AGENT_MODEL


def test_the_paid_model_is_priced():
    """Routing a customer onto a model with no price entry would charge them
    the fallback rate silently. Whatever PAID_AGENT_MODEL is set to must be in
    the table — this asserts it for the model we intend to use."""
    assert "grok-4.5" in model_prices.MODEL_PRICES


def test_reasoning_effort_is_high_and_rejection_is_survivable():
    """Round 63 flipped this to "low" — an agent turn is round-trips x 13s and
    most output tokens were reasoning spent on tool dispatch.

    ROUND 91b flips it to "high", because that saving was measured on the
    wrong side of the bill. On gpt-5.6-luna (0.20 in / 1.20 out per 1M) job
    2603's entire four-call turn spent 775 output tokens, 573 of them
    reasoning — $0.0009 — while re-sending ~28,000 INPUT tokens per call, about
    $0.022. Input outweighs all the thinking by ~25x. And the cost of thinking
    too little is job 2599: the same request, 923 seconds, five inpaint passes,
    timed out, nothing changed — answered correctly in 37s once reasoning came
    back.

    Either way the flip is only safe because a provider that rejects the field
    costs ONE call per process (retry without, latch per model), so this pins
    the value AND the machinery that makes it survivable, together.

    Round 97 raises it again, high -> max: gpt-5.6 documents
    none|low|medium|high|xhigh|max, and the Aug 6-8 cohort showed the spirals
    (a 10x repeated no-op cut_range) living exactly on the steps that fell
    back to effort='none'. The rails that make it survivable at max live in
    test_round91_responses_lane.py: a thinking-sized lane timeout, the
    incomplete-payload 'length' retry staying IN the lane, and effort-value
    400s never latching the lane dead.
    """
    assert config.AGENT_REASONING_EFFORT == "max"
    import llm

    class FakeErr(Exception):
        pass

    e = FakeErr("Unknown parameter: 'reasoning_effort'")
    assert llm.looks_like_bad_parameter(e, "reasoning_effort")
    # an unrelated 400 must NOT latch the field off
    e2 = FakeErr("invalid value for max_tokens")
    assert not llm.looks_like_bad_parameter(e2, "reasoning_effort")
    assert not llm.reasoning_effort_rejected("some-model-xyz")
    llm.mark_reasoning_effort_rejected("some-model-xyz")
    try:
        assert llm.reasoning_effort_rejected("some-model-xyz")
    finally:
        llm._no_reasoning_effort.discard("some-model-xyz")


def test_reasoning_effort_never_applies_to_the_first_iteration():
    """Iteration 0 is where the model reads the state and plans the edit. That
    is the thinking being paid for; only tool dispatch gets downgraded."""
    import inspect
    import agent_loop
    src = inspect.getsource(agent_loop._run_loop)
    # Round 100 tiering: the chat path still sends effort only from the
    # second iteration on, and the value it sends is the per-step tier
    # (configured effort on the planning step, dispatch effort after).
    assert "step_effort and iteration > 0" in src
    assert "config.AGENT_REASONING_EFFORT if iteration == 0" in src
    assert "config.AGENT_REASONING_EFFORT_DISPATCH" in src


# ── the burn rate and what the plans are priced against ─────────────────────

def test_a_credit_burns_at_twice_the_models_cost():
    """This one constant IS the margin. At 0.01 (one credit, one cent of real
    spend) the annual tiers sat at 28-40% and an intro discount on an annual
    plan was a below-cost sale; at 0.005 every plan clears 40%."""
    assert model_prices.USD_PER_CREDIT == 0.005
    assert model_prices.usd_to_credits(1.00) == 200.0
    assert model_prices.usd_to_credits(0.0) == 0.0
    # Junk in must not raise inside a charge.
    assert model_prices.usd_to_credits(None) == 0.0


def test_the_charge_and_the_in_turn_cap_use_the_SAME_divisor():
    """Two divisors here would stop a turn at a number the invoice contradicts.
    Both must go through usd_to_credits, not a literal."""
    import inspect
    import db as worker_db_mod
    for src in (inspect.getsource(worker_db_mod.charge_turn_credits),
                inspect.getsource(agent_tools.ToolContext.running_credits)):
        assert "usd_to_credits" in src
        assert "/ 0.01" not in src


def test_every_live_plan_clears_a_forty_percent_margin():
    """The plan table and the burn rate are set in different files and only
    make sense together — this is the assertion that ties them."""
    backend = _load_backend_copy()
    assert backend.USD_PER_CREDIT == model_prices.USD_PER_CREDIT
    for plan, price, granted in (("ai", 15, 1000), ("ai_pro", 30, 2000),
                                 ("ai_max", 50, 5000)):
        cost = granted * model_prices.USD_PER_CREDIT
        assert cost <= price * 0.60, (plan, cost, price)
        # ...and the annual price (ten months of the monthly one) too, which is
        # the one that used to go negative.
        assert cost <= (price * 10 / 12.0) * 0.75, (plan, cost)
