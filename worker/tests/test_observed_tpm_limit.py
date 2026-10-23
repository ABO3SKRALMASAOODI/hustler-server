"""Fleet TPM admission follows the provider's reported limit.

Studio project 2592 (22 Oct 2026): ~123K-token prompts met the fixed 140K
yield line (config.AGENT_TPM_SOFT_CAP), so the whole fleet made one model
call per minute and the turn slept 4,711 s — with no provider 429 at all in
the ten days around it. The header OpenAI sends on every response says what
the org's tier really is; admission now uses it when it is known.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_loop  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402


class _Resp:
    status_code = 200

    def __init__(self, headers):
        self.headers = headers

    def json(self):
        return {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 120000, "output_tokens": 10}}


def _reset(monkeypatch):
    monkeypatch.setattr(llm, "_observed_tpm", {})


def test_without_a_reported_limit_the_configured_caps_apply(monkeypatch):
    _reset(monkeypatch)
    assert llm.tpm_caps("gpt-6-luna") == (config.AGENT_TPM_SOFT_CAP,
                                          config.AGENT_TPM_HARD_CAP)


def test_the_responses_lane_learns_and_records_the_reported_limit(
        monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **k: _Resp({"x-ratelimit-limit-tokens": "4000000",
                               "x-ratelimit-remaining-tokens": "3870000"}))
    out = llm.responses_create("https://api.openai.com/v1", "k",
                               "gpt-6-luna",
                               [{"role": "user", "content": "hi"}], [])
    assert out.usage.tpm_limit == 4_000_000
    soft, hard = llm.tpm_caps("gpt-6-luna")
    assert hard == 4_000_000
    assert soft == round(4_000_000 * config.AGENT_TPM_OBSERVED_SHARE)
    # a 123K prompt is no longer one call per minute for the whole fleet
    assert soft // 123_000 >= 20
    # another model keeps the configured guess
    assert llm.tpm_caps("deepseek-v4-pro")[0] == config.AGENT_TPM_SOFT_CAP

    recorded = {}
    monkeypatch.setattr(llm, "get_recorder",
                        lambda: lambda p, rq, rs, u: recorded.update(rs))
    llm.record("agent", {"model": "gpt-6-luna"}, {"content": "ok"},
               out.usage)
    assert recorded["tpm_limit"] == 4_000_000


def test_a_lower_reported_tier_lowers_admission_too(monkeypatch):
    _reset(monkeypatch)
    llm.note_rate_limit_headers("gpt-6-luna",
                                {"x-ratelimit-limit-tokens": "90000"})
    assert llm.tpm_caps("gpt-6-luna") == (63000, 90000)


def test_odd_or_missing_headers_change_nothing(monkeypatch):
    _reset(monkeypatch)
    for headers in (None, {}, {"x-ratelimit-limit-tokens": ""},
                    {"x-ratelimit-limit-tokens": "lots"},
                    {"x-ratelimit-limit-tokens": "0"}):
        assert llm.note_rate_limit_headers("gpt-6-luna", headers) is None
    assert llm.observed_tpm_limit("gpt-6-luna") is None


def test_a_stale_observation_expires(monkeypatch):
    _reset(monkeypatch)
    llm.note_rate_limit_headers("gpt-6-luna",
                                {"x-ratelimit-limit-tokens": "4000000"})
    assert llm.observed_tpm_limit("gpt-6-luna", max_age_s=3600)
    assert llm.observed_tpm_limit("gpt-6-luna", max_age_s=-1) is None


def test_agent_admission_reads_the_caps_through_tpm_caps():
    src = inspect.getsource(agent_loop)
    assert "llm.tpm_caps(model)" in src
    assert "config.AGENT_TPM_SOFT_CAP, config.AGENT_TPM_WINDOW_S" not in src
