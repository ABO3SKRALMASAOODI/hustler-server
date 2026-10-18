from datetime import datetime, timezone
import hashlib
import hmac
from unittest.mock import Mock

import pytest

import kit_marketing as m
from kit_client import KitError


@pytest.fixture
def cfg(monkeypatch):
    values = {"KIT_ACCOUNT_ID": "1", "KIT_AUDIENCE_TAG_ID": "2",
              "KIT_EMAIL_TEMPLATE_ID": "3", "KIT_FROM_EMAIL": "support@valmera.io",
              "KIT_DAILY_ENABLED": "1", "MARKETING_EMAIL_PROVIDER": "kit"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def remote(uid, email, state="active"):
    return {"id": uid, "email_address": email, "state": state}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "check_free_account", lambda *args, **kwargs: 9999)
    monkeypatch.setattr(m, "query", lambda *args: [])
    monkeypatch.setattr(m, "save_links", Mock())
    return Mock()


def test_optout_and_complaint_are_not_reactivated(monkeypatch, cfg, client):
    client.subscribers.return_value = [remote(1, "out@example.com", "cancelled"), remote(2, "spam@example.com", "complained")]
    client.tag_members.return_value = []
    monkeypatch.setattr(m, "audience", lambda _: [{"email": "out@example.com"}, {"email": "spam@example.com"}])
    suppress = Mock(); monkeypatch.setattr(m, "suppress_local", suppress)
    assert m.sync_audience(None, client, cfg)["eligible"] == 0
    assert suppress.call_count == 2
    client.subscribe.assert_not_called(); client.tag.assert_not_called()


def test_dry_run_does_not_import_tag_unsubscribe_or_write(monkeypatch, cfg, client):
    client.subscribers.return_value = [remote(1, "out@example.com", "cancelled")]
    client.tag_members.return_value = [remote(1, "out@example.com", "cancelled")]
    monkeypatch.setattr(m, "audience", lambda _: [{"email": "new@example.com"}, {"email": "out@example.com"}])
    write = Mock(); monkeypatch.setattr(m, "write", write)
    suppress = Mock(); monkeypatch.setattr(m, "suppress_local", suppress)
    assert m.sync_audience(None, client, cfg, dry_run=True)["eligible"] == 1
    for operation in (write, suppress, client.subscribe, client.tag, client.untag, client.unsubscribe):
        operation.assert_not_called()


def test_wrong_tag_membership_blocks_broadcast(monkeypatch, cfg, client):
    client.subscribers.return_value = [remote(1, "one@example.com")]
    client.tag_members.side_effect = [[], []]
    monkeypatch.setattr(m, "audience", lambda _: [{"email": "one@example.com"}])
    monkeypatch.setattr(m, "write", Mock())
    with pytest.raises(KitError, match="kit_audience_sync_pending"):
        m.sync_audience(None, client, cfg)
    client.tag.assert_called_once_with("2", 1)


def test_new_audience_cannot_overflow_free_plan(monkeypatch, cfg, client):
    monkeypatch.setattr(m, "check_free_account", lambda *args, **kwargs: 1)
    client.subscribers.return_value = [remote(1, "existing@example.com")]
    monkeypatch.setattr(m, "audience", lambda _: [{"email": "new@example.com"}])
    with pytest.raises(KitError, match="kit_free_subscriber_limit"):
        m.sync_audience(None, client, cfg)
    client.subscribe.assert_not_called()


def test_failed_optout_is_retried_before_any_new_import(monkeypatch, cfg, client):
    client.subscribers.return_value = []
    monkeypatch.setattr(m, "query", lambda *_: [{"email": "out@example.com", "kit_subscriber_id": 9}])
    client.unsubscribe.side_effect = KitError("kit_http_503", uncertain=True)
    with pytest.raises(KitError, match="kit_http_503"):
        m.sync_audience(None, client, cfg)
    client.unsubscribe.assert_called_once_with(9)
    client.subscribe.assert_not_called()
    client.tag.assert_not_called()


def test_uncertain_create_is_reconciled_never_retried(monkeypatch, cfg, client):
    monkeypatch.setattr(m, "query", lambda *_: [{"state": "uncertain", "description": "valmera-daily-2026-10-18"}])
    client.broadcasts.return_value = []
    result = m.run(None, {"master_enabled": True}, force=True, client=client)
    assert result["skipped"] == "reconciling_prior_attempt"
    client.create_broadcast.assert_not_called()


def test_uncertain_success_is_recovered_without_duplicate(monkeypatch, cfg, client):
    monkeypatch.setattr(m, "query", lambda *_: [{"state": "creating", "description": "valmera-daily-2026-10-18"}])
    client.broadcasts.return_value = [{"id": 7, "description": "valmera-daily-2026-10-18"}]
    write = Mock(); monkeypatch.setattr(m, "write", write)
    m.run(None, {"master_enabled": True}, force=True, client=client)
    assert write.call_args.args[2][0] == 7
    client.create_broadcast.assert_not_called()


def test_force_cannot_bypass_daily_deduplication(monkeypatch, cfg, client):
    monkeypatch.setattr(m, "query", lambda *_: [{"state": "completed", "broadcast_id": 7}])
    monkeypatch.setattr(m, "write", Mock())
    client.broadcast.return_value = {"status": "completed"}
    assert m.run(None, {"master_enabled": True}, force=True, client=client)["skipped"] == "already_scheduled_today"
    client.create_broadcast.assert_not_called()


def test_dubai_midnight_does_not_trigger_the_next_evening_broadcast(cfg, client):
    # 20:01 UTC is already the following calendar day in Dubai.
    now = datetime(2026, 10, 18, 20, 1, tzinfo=timezone.utc)
    result = m.run(None, {"master_enabled": True, "send_hour_utc": 15}, client=client, now=now)
    assert result["skipped"] == "before_send_hour"
    client.create_broadcast.assert_not_called()


def test_daily_content_tracks_return_and_contains_no_personal_tokens():
    from routes.newsletter_content import DEFAULT_TEMPLATES
    day = datetime(2026, 10, 18).date()
    key, content = m.daily_content(day, DEFAULT_TEMPLATES)
    assert f"daily-{day}" in content["content"]
    assert "utm_term=kit" in content["content"]
    assert "AI editing requires a subscription" in content["content"]
    assert "{{CREDITS}}" not in content["content"]


def test_signature_rejects_tampering_replay_and_missing_secret():
    body = b'{"events":[]}'
    signature = hmac.new(b"endpoint-secret", b"1000." + body, hashlib.sha256).hexdigest()
    header = f"t=1000,v1={signature}"
    assert m.valid_signature(body, header, "endpoint-secret", now=1001)
    assert not m.valid_signature(body + b" ", header, "endpoint-secret", now=1001)
    assert not m.valid_signature(body, header, "endpoint-secret", now=1401)
    assert not m.valid_signature(body, header, "", now=1001)


def test_kit_marketing_can_never_fall_back_to_brevo(monkeypatch, cfg):
    from routes import newsletter
    send = Mock(); monkeypatch.setattr(newsletter.brevo_delivery, "send_email", send)
    assert newsletter._send_one("customer@example.com", "Subject", "Content", "https://example.com/unsub") is False
    send.assert_not_called()
