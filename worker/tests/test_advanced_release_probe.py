"""Provider funding must not block an unrelated routing repair release."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import llm


spec = importlib.util.spec_from_file_location(
    "advanced_release_probe", Path(__file__).resolve().parents[2]
    / "backend/scripts/probe_advanced_model.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_responses_error_preserves_structured_quota_metadata(monkeypatch):
    payload = {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: NS(
        status_code=429, text="Provider refused the request", json=lambda: payload))
    with pytest.raises(llm.ProviderHTTPError) as caught:
        llm.responses_create("https://example.invalid/v1", None, "test-model", [], [])
    assert caught.value.status_code == 429
    assert caught.value.error_type == caught.value.error_code == "insufficient_quota"
    assert isinstance(caught.value, RuntimeError)


@pytest.mark.parametrize("status,code,may_release", [
    (429, "insufficient_quota", True),
    (429, "rate_limit_exceeded", False),
    (400, "invalid_request_error", False),
    (401, "invalid_api_key", False),
])
def test_release_probe_distinguishes_funding_from_protocol_failures(monkeypatch, capsys,
                                                                  status, code, may_release):
    monkeypatch.setattr(llm, "paid_editor_lanes", lambda _: [
        {"model": "test-model", "base_url": "https://example.invalid/v1", "api_key": None}])
    def refuse(*args, **kwargs):
        raise llm.ProviderHTTPError("responses", status, "refused", {"error": {"code": code}})
    monkeypatch.setattr(llm, "responses_create", refuse)
    if may_release:
        probe.main()
        assert '"probe_status": "blocked_provider_funding"' in capsys.readouterr().out
    else:
        with pytest.raises(llm.ProviderHTTPError):
            probe.main()


def test_cache_regression_still_blocks_release(monkeypatch):
    monkeypatch.setattr(llm, "paid_editor_lanes", lambda _: [
        {"model": "test-model", "base_url": "https://example.invalid/v1", "api_key": None}])
    response = NS(usage=NS(prompt_tokens=100, completion_tokens=5, prompt_tokens_details={}),
                  choices=[NS(message=NS(tool_calls=[NS(function=NS(
                      name="check_connection", arguments='{"ok": true}'))]))])
    monkeypatch.setattr(llm, "responses_create", lambda *a, **kw: response)
    monkeypatch.setattr(llm, "cached_input_tokens", lambda _: 0)
    monkeypatch.setattr(llm, "provider_cost_usd", lambda *a: 0)
    with pytest.raises(AssertionError, match="No cache reuse"):
        probe.main()
