"""Round 46 — the move to DeepSeek V4 Pro.

Three things here would fail silently and expensively rather than loudly:

  1. **Cache-hit billing.** DeepSeek serves a repeated prompt prefix at
     $0.003625/1M against $1.74/1M for a miss — 480x. An agent turn re-sends
     the same system prompt + ~60 tool schemas every iteration, so most of its
     input after step one is a cache hit. Bill it at the miss rate and a
     10-step turn costs the user several times what it cost us; nothing errors,
     the number is just wrong. The in-turn cap and the final DB charge have to
     agree on that, or a turn stops on spend the user is never billed for.
  2. **The image provider must stay off the chat provider.** DeepSeek has no
     /images/generations endpoint. Inheriting its base URL would 404 every
     generation — which is invisible in the UI (the agent just says it couldn't
     make an image) and has already cost two multi-week silent outages on this
     one capability. Absent key => capability OFF, not broken.
  3. **The failure message must not paste the provider's body into the chat.**
     On Jul 26 2026 every user in the product read our xAI team UUID and a
     sentence truncated mid-word, four times in a row, alongside an invitation
     to retry something that could not succeed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop                                              # noqa: E402
import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import llm                                                     # noqa: E402


# ── the model actually configured ──────────────────────────────────────

def test_agent_and_vision_are_both_v4_pro():
    """v4-flash is text-only: it ignores/rejects image parts, which would
    blind look_at and every contact-sheet read. If AGENT_MODEL is ever moved
    to flash, VISION_MODEL must NOT follow it."""
    assert config.AGENT_MODEL == "deepseek-v4-pro"
    assert config.VISION_MODEL == "deepseek-v4-pro"
    assert config.OPENAI_BASE_URL == "https://api.deepseek.com"


def test_prices_match_the_configured_model():
    """CLAUDE.md's standing rule: change AGENT_MODEL, change these, or credits
    drift from real cost."""
    assert config.LLM_PRICE_IN_PER_M == 1.74
    assert config.LLM_PRICE_OUT_PER_M == 3.48
    assert config.LLM_PRICE_CACHED_IN_PER_M < config.LLM_PRICE_IN_PER_M


def test_worker_and_db_price_constants_agree():
    """Two modules read the same env for the same number; a default that drifts
    between them means the in-turn cap and the final charge disagree."""
    import db
    assert db.LLM_PRICE_IN_PER_M == config.LLM_PRICE_IN_PER_M
    assert db.LLM_PRICE_OUT_PER_M == config.LLM_PRICE_OUT_PER_M
    assert db.LLM_PRICE_CACHED_IN_PER_M == config.LLM_PRICE_CACHED_IN_PER_M


# ── cached-token accounting ────────────────────────────────────────────

class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, **kw):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        for k, v in kw.items():
            setattr(self, k, v)


class _Details:
    def __init__(self, cached_tokens):
        self.cached_tokens = cached_tokens


def test_reads_deepseeks_spelling():
    u = _Usage(prompt_tokens=1000, prompt_cache_hit_tokens=900)
    assert llm.cached_input_tokens(u) == 900


def test_reads_openais_spelling():
    u = _Usage(prompt_tokens=1000, prompt_tokens_details=_Details(750))
    assert llm.cached_input_tokens(u) == 750


def test_reads_a_dict_details_payload():
    u = _Usage(prompt_tokens=1000, prompt_tokens_details={"cached_tokens": 400})
    assert llm.cached_input_tokens(u) == 400


def test_a_provider_with_no_caching_reports_zero():
    """Zero makes the cached price inert — a provider without prompt caching
    must not accidentally receive a discount."""
    assert llm.cached_input_tokens(_Usage(prompt_tokens=1000)) == 0
    assert llm.cached_input_tokens(None) == 0


def test_cached_can_never_exceed_the_prompt():
    """The charge subtracts this from prompt_tokens. A bogus larger value would
    otherwise credit the user for tokens they really did spend."""
    u = _Usage(prompt_tokens=100, prompt_cache_hit_tokens=100000)
    assert llm.cached_input_tokens(u) == 100


def test_garbage_is_not_a_discount():
    assert llm.cached_input_tokens(
        _Usage(prompt_tokens=500, prompt_cache_hit_tokens="lots")) == 0


# ── the in-turn cap uses the same formula as the DB charge ──────────────

def _ctx():
    """A bare ToolContext: its __init__ wants a live DB/job/project, and none
    of that is involved in pricing. This still calls the REAL running_credits,
    which is the thing under test."""
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.images_generated = []
    ctx.gen_extra_cost_usd = 0.0
    ctx.tokens_in = 0
    ctx.tokens_out = 0
    ctx.tokens_cached_in = 0
    ctx.credit_budget = None
    return ctx


def _expected_credits(tin, cached, tout):
    cost = ((tin - cached) * config.LLM_PRICE_IN_PER_M +
            cached * config.LLM_PRICE_CACHED_IN_PER_M +
            tout * config.LLM_PRICE_OUT_PER_M) / 1e6
    return round(cost / 0.01, 2)


def test_running_credits_discounts_cache_hits():
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out, ctx.tokens_cached_in = 500_000, 20_000, 0
    full = ctx.running_credits()
    ctx.tokens_cached_in = 450_000
    cached = ctx.running_credits()
    assert cached < full
    assert cached == _expected_credits(500_000, 450_000, 20_000)
    # A realistic multi-step turn: billing it as all-miss would overcharge by
    # well over 2x. This is the whole reason the split exists.
    assert full > cached * 2


def test_running_credits_matches_all_miss_when_nothing_is_cached():
    """Legacy parity: with no cache reporting, the number must be exactly what
    it was before this change."""
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out, ctx.tokens_cached_in = 300_000, 10_000, 0
    legacy = round(((300_000 * config.LLM_PRICE_IN_PER_M +
                     10_000 * config.LLM_PRICE_OUT_PER_M) / 1e6) / 0.01, 2)
    assert ctx.running_credits() == legacy


def test_running_credits_clamps_a_bad_cached_count():
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out = 1000, 0
    ctx.tokens_cached_in = 999_999
    assert ctx.running_credits() >= 0


def test_the_db_charge_uses_the_same_three_part_formula():
    """The SQL and the Python cap must not drift. Asserting the shape of the
    query is weaker than running it, but it does catch someone 'simplifying'
    the charge back to prompt_tokens * price_in."""
    import inspect
    import db
    src = inspect.getsource(db.charge_turn_credits)
    assert "cached_in" in src
    assert "LLM_PRICE_CACHED_IN_PER_M" in src
    assert "LLM_PRICE_IN_PER_M" in src


# ── the image provider is independent of the chat provider ─────────────

def test_image_defaults_do_not_follow_the_chat_provider():
    """DeepSeek has no image endpoint; the default must not point there."""
    assert "deepseek" not in config.IMAGE_BASE_URL


def test_image_key_is_not_inherited_across_providers(monkeypatch):
    """A DeepSeek chat key is not an xAI image key. Inheriting it would send a
    valid-looking request with the wrong credential."""
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(config, "IMAGE_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "IMAGE_API_KEY", "")
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "grok-imagine-image-quality")
    assert not llm.image_available()
    # ...and the capability is HIDDEN, not advertised-then-failing.
    assert "generate_image" not in agent_tools.capabilities_digest()
    assert all(t["function"]["name"] != "generate_image"
               for t in agent_tools.openai_tools())


def test_image_key_is_inherited_when_the_provider_is_the_same(monkeypatch):
    """Single-provider deployments (everything on xAI) must keep working with
    only OPENAI_API_KEY set — this is the backward-compatible path."""
    import importlib
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "xai-key")
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    fresh = importlib.reload(config)
    try:
        assert fresh.IMAGE_API_KEY == "xai-key"
    finally:
        importlib.reload(config)      # restore the real process config


# ── the message a customer reads when the provider is out of credit ────

# The verbatim exception text from the Jul 26 2026 outage.
_REAL_403 = ("Error code: 403 - {'code': 'permission-denied', 'error': 'Your "
             "team 166666fc-e639-48d4-b7be-54d9c8cd277c has either used all "
             "available credits or reached its monthly spending limit'}")


class _Quota(Exception):
    status_code = 403


def test_the_provider_body_never_reaches_the_user():
    msg = agent_loop._user_facing_failure(_Quota(_REAL_403))
    for leak in ("166666fc", "permission-denied", "403", "team",
                 "Error code", "{"):
        assert leak not in msg, f"leaked {leak!r}: {msg}"


def test_a_quota_failure_does_not_invite_a_retry_that_cannot_work():
    """"try sending that again" was the worst part: a provider with no credit
    fails identically every time, so people burned their session retrying."""
    msg = agent_loop._user_facing_failure(_Quota(_REAL_403))
    assert "try sending that again" not in msg.lower()
    assert "my side" in msg.lower() or "on it" in msg.lower()
    # It must still say their work survived and they weren't billed.
    assert "safe" in msg.lower()
    assert "credits" in msg.lower()


def test_quota_is_detected_from_the_body_alone():
    """Not every SDK surfaces status_code — the text must be enough."""
    msg = agent_loop._user_facing_failure(RuntimeError(_REAL_403))
    assert "a little later" in msg


def test_insufficient_balance_phrasings_are_caught():
    for body in ("Insufficient Balance",
                 "You exceeded your current quota",
                 "billing hard limit reached",
                 "Your credit balance is too low"):
        msg = agent_loop._user_facing_failure(RuntimeError(body))
        assert "later" in msg, body


def test_rate_limit_is_its_own_message():
    class _RL(Exception):
        status_code = 429
    msg = agent_loop._user_facing_failure(_RL("Too Many Requests"))
    assert "rate-limit" in msg.lower()
    assert "minute" in msg.lower()


def test_a_timeout_says_so_and_suggests_smaller_steps():
    msg = agent_loop._user_facing_failure(
        TimeoutError("Request timed out after 90s"))
    assert "too long" in msg.lower() and "smaller" in msg.lower()


def test_an_ordinary_bug_keeps_the_old_generic_wording():
    """Unknown failures ARE usually worth one retry, and the old sentence was
    fine for them — it just must not carry the exception text."""
    msg = agent_loop._user_facing_failure(
        KeyError("shots"))
    assert msg == ("Something went wrong on my end while editing. Your video "
                   "and edit history are safe — try sending that again.")


def test_no_failure_message_ever_contains_a_stack_or_repr():
    for e in (_Quota(_REAL_403), RuntimeError("boom at 0x7f9"),
              KeyError("k"), TimeoutError("t"), ValueError("v")):
        msg = agent_loop._user_facing_failure(e)
        for junk in ("Traceback", "0x7f9", "Error code", "{'"):
            assert junk not in msg
