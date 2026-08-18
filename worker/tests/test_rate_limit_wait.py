"""A rate-limited agent step WAITS instead of dying.

One agent turn can exceed the provider's whole TPM tier by itself, so a
long turn hitting 429 mid-flight is expected. Waiting is better than
killing minutes of finished edits.

Aug 14: OpenAI uses HTTP 429 for BOTH an empty wallet and TPM. Waiting on
insufficient_quota just delayed the same death. Responses-lane 429s arrived
as RuntimeError('responses HTTP 429: ...') with no status_code, so they
were not waited at all.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm                                                     # noqa: E402


class _RL(Exception):
    status_code = 429
    response = None

    def __str__(self):
        return "Error 429: rate limit reached"


class _Text(Exception):
    def __str__(self):
        return "Too Many Requests, slow down"


class _Other(Exception):
    def __str__(self):
        return "Error 400: invalid request"


class _WithRA(Exception):
    status_code = 429

    class response:                                    # noqa: N801
        headers = {"retry-after": "30"}

    def __str__(self):
        return "too many requests"


class _ResponsesTPM(Exception):
    """The live shape: responses_create raises RuntimeError, no status_code."""

    def __str__(self):
        return (
            "responses HTTP 429: {\"error\": {\"message\": "
            "\"Rate limit reached for gpt-5.6-luna in organization org-x "
            "on tokens per min (TPM): Limit 200000, Used 187948, "
            "Requested 53803. Please try again in 12.525s.\", "
            "\"type\": \"tokens\", \"code\": \"rate_limit_exceeded\"}}"
        )


class _Quota(Exception):
    status_code = 429

    def __str__(self):
        return (
            "Error code: 429 - {'error': {'message': "
            "'You have no credits remaining. Add credits to continue "
            "using the API at https://platform.openai.com/settings/"
            "organization/billing/.', 'type': 'insufficient_quota'}}"
        )


class _ResponsesQuota(Exception):
    def __str__(self):
        return (
            "responses HTTP 429: {\n"
            "  \"error\": {\n"
            "    \"message\": \"You have no credits remaining. "
            "Add credits to continue using the API at "
            "https://platform.openai.com/settings/organization/billing/\",\n"
            "    \"type\": \"insufficient_quota\"\n"
            "  }\n}"
        )


def test_only_rate_limits_wait():
    assert llm.rate_limit_wait(_Other(), 1, 600) is None
    assert llm.rate_limit_wait(_RL(), 1, 600) == 8.0
    assert llm.rate_limit_wait(_Text(), 1, 600) == 8.0


def test_quota_429_does_not_wait():
    """Empty wallet is not a TPM window. Sleeping it just delayed the death."""
    assert llm.is_quota_error(_Quota())
    assert not llm.is_rate_limit_error(_Quota())
    assert llm.rate_limit_wait(_Quota(), 1, 600) is None
    assert llm.rate_limit_wait(_ResponsesQuota(), 1, 600) is None


def test_responses_http_429_tpm_waits_and_honours_body():
    assert llm.is_rate_limit_error(_ResponsesTPM())
    wait = llm.rate_limit_wait(_ResponsesTPM(), 1, 600)
    # Named wait 12.525s + 1s buffer, not less than the 8s backoff floor.
    assert wait == 13.525


def test_waits_grow_with_attempts():
    assert llm.rate_limit_wait(_RL(), 2, 600) == 16.0
    assert llm.rate_limit_wait(_RL(), 3, 600) == 24.0
    assert llm.rate_limit_wait(_RL(), 8, 600) == 60.0, "growth caps at 60s"
    assert llm.rate_limit_wait(_RL(), 20, 600) == 60.0


def test_retry_after_is_honoured_and_capped():
    assert llm.rate_limit_wait(_WithRA(), 1, 600) == 31.0  # 30 + 1s buffer

    class _Huge(_WithRA):
        class response:                                # noqa: N801
            headers = {"retry-after": "600"}
    assert llm.rate_limit_wait(_Huge(), 1, 600) == 90.0

    class _Tiny(_WithRA):
        class response:                                # noqa: N801
            headers = {"retry-after": "1"}
    # A shorter server hint never undercuts the backoff schedule: attempt 3
    # would wait 24s on its own, and a 1s Retry-After mid-burst is exactly
    # how the old schedule kept walking back into the same wall.
    assert llm.rate_limit_wait(_Tiny(), 3, 600) == 24.0


def test_agent_lanes_offer_only_independently_funded_wallets(monkeypatch):
    base_client, paid_client, frontier_client = object(), object(), object()
    monkeypatch.setattr(llm, "client", lambda: base_client)
    monkeypatch.setattr(llm, "paid_client", lambda: paid_client)
    monkeypatch.setattr(llm, "frontier_client", lambda: frontier_client)
    monkeypatch.setattr(llm.config, "OPENAI_API_KEY", "openai-wallet")
    monkeypatch.setattr(llm.config, "OPENAI_BASE_URL", "https://openai.test/v1")
    monkeypatch.setattr(llm.config, "PAID_API_KEY", "xai-wallet")
    monkeypatch.setattr(llm.config, "PAID_BASE_URL", "https://xai.test/v1")
    monkeypatch.setattr(llm.config, "PAID_AGENT_MODEL", "paid-model")
    monkeypatch.setattr(llm.config, "FRONTIER_API_KEY", "xai-wallet")
    monkeypatch.setattr(llm.config, "FRONTIER_BASE_URL", "https://xai.test/v1")
    monkeypatch.setattr(llm.config, "FRONTIER_AGENT_MODEL", "frontier-model")

    lanes = llm.agent_lanes_for(False, "free")
    assert [row["name"] for row in lanes] == ["standard", "paid_fallback"]
    assert lanes[0]["client"] is base_client
    assert lanes[1]["client"] is paid_client


def test_bounds():
    assert llm.rate_limit_wait(_RL(), 21, 600) is None, "wait-count cap"
    assert llm.rate_limit_wait(_RL(), 1, 14) is None, "turn nearly over"
    assert llm.rate_limit_wait(_RL(), 1, 600, shutting_down=True) is None
    # With 20s left the wait shrinks to leave 8s of working budget.
    assert llm.rate_limit_wait(_RL(), 1, 20) == 8.0


def test_quota_does_not_latch_responses_lane_dead():
    assert llm.looks_like_responses_unsupported(_ResponsesQuota()) is False
    assert llm.looks_like_responses_unsupported(_ResponsesTPM()) is False


def test_agent_client_disables_hidden_sdk_retries():
    class _Client:
        def __init__(self):
            self.options = None

        def with_options(self, **options):
            self.options = options
            return "bounded-client"

    client = _Client()
    assert llm.without_sdk_retries(client) == "bounded-client"
    assert client.options == {"max_retries": 0}


def test_agent_client_accepts_provider_test_doubles_without_options():
    client = object()
    assert llm.without_sdk_retries(client) is client
