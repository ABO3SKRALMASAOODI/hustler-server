from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

import anthropic_key_health
from anthropic_key_health import ExpiryUnknown, check_key

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


def test_expiry_is_recorded_and_alerted_three_days_ahead():
    expires = "2026-11-29T12:00:00+00:00"
    metadata = Mock(return_value=expires)
    probe = Mock(return_value=True)
    state, alert = check_key("test-key", {}, NOW, metadata, probe)
    assert state["expires_at"] == expires and "provider_api_keys" in alert[1]
    assert alert[0] == "Anthropic key expiry warning"
    probe.assert_called_once_with("test-key")        # metadata never stands in for the live call
    assert check_key("test-key", state, NOW, metadata, probe)[1] is None


def test_inference_key_only_probes_live_credential_and_alerts_on_first_failure():
    probe = Mock(return_value=False)
    state, alert = check_key("test-key", {}, NOW, lambda key: None, probe)
    assert "probe failed" in alert[0]
    assert "test-key" not in str(state) + str(alert)
    assert check_key("test-key", state, NOW, lambda key: None, probe)[1] is None
    probe.assert_called_once_with("test-key")


def test_a_transient_probe_answer_never_alerts_and_never_hides_a_real_failure():
    """Finding 5: one 529 or network blip at the daily check mailed 'rotate the key' for a working key
    and stored ok=False for 24 h, so a real failure the next day sent nothing."""
    probe = Mock(return_value=None)
    state, alert = check_key("test-key", {"fingerprint": "x"}, NOW, lambda key: None, probe)
    assert alert is None and "ok" not in state and "checked_at" not in state
    # asked again within minutes, not a day later
    assert check_key("test-key", state, NOW + timedelta(minutes=2), lambda key: None, probe)[1] is None
    assert probe.call_count == 1
    probe.return_value = True
    state, alert = check_key("test-key", state, NOW + timedelta(minutes=11), lambda key: None, probe)
    assert alert is None and state["ok"] is True and "retry_at" not in state
    probe.return_value = False
    state, alert = check_key("test-key", state, NOW + timedelta(days=1, minutes=11), lambda key: None, probe)
    assert alert and "probe failed" in alert[0]


def test_a_key_past_its_expiry_fails_even_with_metadata():
    """Finding 5: with expiry metadata configured the live probe never ran, and an expired key was ok."""
    probe = Mock(return_value=None)
    state, alert = check_key("test-key", {}, NOW, lambda key: "2026-11-25T00:00:00Z", probe)
    assert state["ok"] is False and alert[0] == "Anthropic live key probe failed"
    probe.assert_called_once_with("test-key")


def test_unreadable_expiry_is_recorded_and_logged_once(caplog):
    """Finding 6: without ANTHROPIC_ADMIN_API_KEY / ANTHROPIC_API_KEY_ID the warning silently never fired."""
    anthropic_key_health._unread_logged.clear()

    def metadata(key):
        raise ExpiryUnknown("ANTHROPIC_ADMIN_API_KEY and ANTHROPIC_API_KEY_ID are not both set")
    with caplog.at_level("WARNING", logger="anthropic_key_health"):
        state, alert = check_key("test-key", {}, NOW, metadata, lambda key: True)
        check_key("other-key", {}, NOW, metadata, lambda key: True)
    assert alert is None and state["ok"] is True
    assert "not both set" in state["expiry_unknown"]
    assert sum("not monitored" in r.getMessage() for r in caplog.records) == 1
    state, _ = check_key("test-key", dict(state, checked_at=(NOW - timedelta(days=2)).isoformat()), NOW,
                         lambda key: None, lambda key: True)
    assert "expiry_unknown" not in state


def test_expiry_lookup_names_why_it_cannot_read(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_ID", raising=False)
    with pytest.raises(ExpiryUnknown, match="not both set"):
        anthropic_key_health._expiry("sk-ant-test-abcd")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "admin")
    monkeypatch.setenv("ANTHROPIC_API_KEY_ID", "key_1")
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True, json=lambda: {"partial_key_hint": "sk-ant-other...wxyz", "expires_at": None}))
    with pytest.raises(ExpiryUnknown, match="different key"):
        anthropic_key_health._expiry("sk-ant-test-abcd")
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True, json=lambda: {"partial_key_hint": "sk-ant-test...abcd", "expires_at": "2027-01-01T00:00:00Z"}))
    assert anthropic_key_health._expiry("sk-ant-test-abcd") == "2027-01-01T00:00:00Z"


@pytest.mark.parametrize(("status", "text", "verdict"), [
    (200, "", True), (401, "invalid x-api-key", False), (403, "permission", False), (402, "", False),
    (400, "Your credit balance is too low", False), (400, "max_tokens: bad value", None),
    (429, "rate_limit_error", None), (529, "overloaded_error", None), (500, "api_error", None)])
def test_probe_verdicts(monkeypatch, status, text, verdict):
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(status_code=status, text=text))
    assert anthropic_key_health._probe("k") is verdict


def test_probe_network_failure_has_no_verdict(monkeypatch):
    def fail(*a, **k):
        raise requests.ConnectionError("reset")
    monkeypatch.setattr(requests, "post", fail)
    assert anthropic_key_health._probe("k") is None
