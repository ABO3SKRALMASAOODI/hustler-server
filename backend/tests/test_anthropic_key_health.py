from datetime import datetime, timezone
from unittest.mock import Mock

from anthropic_key_health import check_key


def test_expiry_is_recorded_and_alerted_three_days_ahead():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    expires = "2026-11-29T12:00:00+00:00"
    metadata = Mock(return_value=expires)
    probe = Mock(return_value=True)
    state, alert = check_key("test-key", {}, now, metadata, probe)
    assert state["expires_at"] == expires and "provider_api_keys" in alert[1]
    assert probe.call_count == 0
    assert check_key("test-key", state, now, metadata, probe)[1] is None


def test_inference_key_only_probes_live_credential_and_alerts_on_first_failure():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    probe = Mock(return_value=False)
    state, alert = check_key("test-key", {}, now, lambda key: None, probe)
    assert "probe failed" in alert[0]
    assert "test-key" not in str(state) + str(alert)
    assert check_key("test-key", state, now, lambda key: None, probe)[1] is None
    probe.assert_called_once_with("test-key")
