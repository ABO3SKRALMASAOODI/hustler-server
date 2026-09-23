"""Round 94 — OpenAI priority processing, env-gated and OFF by default.

There is no fast MODEL variant of the gpt-5.6 family (luna/terra/sol differ
by intelligence-per-dollar); the "fast version" OpenAI sells is the
priority SERVICE TIER: same model, faster pool, exactly 2x price. Measured
on our key (responses endpoint, 1400-token generations): median 113 -> 138
tok/s. These pin the wiring:

  * default env -> no service_tier anywhere (today's behaviour, exactly);
  * OPENAI_SERVICE_TIER=priority -> chat kwargs carry it ONLY when the
    base URL is actually OpenAI (any other provider 400s on the unknown
    parameter), and the responses body carries it (its caller is already
    host-gated via responses_available);
  * flipping the tier without flipping LLM_PRICE_* halves the LLM margin —
    the config comment carries the pairing, and the 2x relationship is
    asserted against the standard prices here so the numbers travel
    together.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                   # noqa: E402
import llm                                                      # noqa: E402


def test_default_is_off_and_sends_nothing():
    assert config.OPENAI_SERVICE_TIER == ""
    kw = llm.completion_kwargs("gpt-5.6-luna", max_tokens=100)
    assert "service_tier" not in kw


def test_tier_reaches_chat_kwargs_only_on_openai(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_SERVICE_TIER", "priority")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert llm.completion_kwargs("gpt-5.6-luna")["service_tier"] == "priority"
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.deepseek.com")
    assert "service_tier" not in llm.completion_kwargs("deepseek-v4-pro")


def test_tier_reaches_the_responses_body(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_SERVICE_TIER", "priority")
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(json or {})
        raise RuntimeError("stop here — body captured")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    try:
        llm.responses_create("https://api.openai.com/v1", "k", "gpt-5.6-luna",
                             [{"role": "user", "content": "hi"}], [])
    except RuntimeError:
        pass
    assert captured.get("service_tier") == "priority"

    captured.clear()
    monkeypatch.setattr(config, "OPENAI_SERVICE_TIER", "")
    try:
        llm.responses_create("https://api.openai.com/v1", "k", "gpt-5.6-luna",
                             [{"role": "user", "content": "hi"}], [])
    except RuntimeError:
        pass
    assert "service_tier" not in captured


def test_priority_prices_are_exactly_double_standard():
    """Luna 6 prices the reported tier without an environment price override."""
    import model_prices
    standard = model_prices.luna6_usage_cost(10000, 2000, 4000, 1000)
    priority = model_prices.luna6_usage_cost(
        10000, 2000, 4000, 1000, service_tier='priority')
    assert priority == 2 * standard
