from unittest.mock import Mock

import pytest
import requests

from kit_client import KitClient, KitError, check_free_account


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("kit_client.time.sleep", lambda _: None)
    session = Mock()
    session.request.return_value = Mock(status_code=200, json=lambda: {})
    return KitClient("private-test-key", session)


def test_timeout_is_not_retried_and_does_not_leak_recipient_or_key(client):
    client.session.request.side_effect = requests.Timeout("private-test-key recipient@example.com")
    with pytest.raises(KitError) as exc:
        client.subscribe("recipient@example.com")
    assert exc.value.uncertain
    assert str(exc.value) == "kit_request_failed"
    client.session.request.assert_called_once()


def test_redirect_never_sends_key_to_another_host(client):
    client.session.request.return_value.status_code = 302
    with pytest.raises(KitError, match="kit_http_302"):
        client.account()
    assert client.session.request.call_args.kwargs["allow_redirects"] is False


def test_pagination_never_accepts_a_partial_audience(client):
    client.session.request.return_value.json = lambda: {"subscribers": [], "pagination": {"has_next_page": True, "end_cursor": "same"}}
    with pytest.raises(KitError, match="kit_invalid_pagination"):
        client.subscribers()


def test_reads_all_states_across_pages(client):
    client.session.request.side_effect = [
        Mock(status_code=200, json=lambda: {"subscribers": [{"id": 1, "state": "cancelled"}], "pagination": {"has_next_page": True, "end_cursor": "next"}}),
        Mock(status_code=200, json=lambda: {"subscribers": [{"id": 2, "state": "active"}], "pagination": {"has_next_page": False}}),
    ]
    assert [r["id"] for r in client.subscribers()] == [1, 2]
    assert client.session.request.call_args.kwargs["params"]["status"] == "all"


def test_broadcast_is_targeted_and_never_published_publicly(client):
    client.session.request.return_value.json = lambda: {"broadcast": {"id": 10}}
    client.create_broadcast(description="valmera-daily-2026-10-18", subject="A useful edit", content="HTML",
                            preview_text="Preview", tag_id=2, template_id=3,
                            sender="support@valmera.io", send_at="2026-10-18T15:00:00Z")
    payload = client.session.request.call_args.kwargs["json"]
    assert payload["public"] is False
    assert payload["subscriber_filter"] == [{"all": [{"type": "tag", "ids": [2]}]}]


def account():
    return {"id": 7, "plan_type": "free", "plan": {"on_trial": False, "subscriber_limit": 10000},
            "sending_addresses": [{"email_address": "support@valmera.io", "is_verified": True, "is_dmarc_configured": True}]}


def test_free_account_and_sender_required():
    assert check_free_account(account(), expected_id=7, sender="support@valmera.io") == 9999


@pytest.mark.parametrize("change,code", [
    ({"id": 8}, "kit_account_mismatch"),
    ({"plan_type": "creator"}, "kit_free_plan_required"),
    ({"plan": {"on_trial": True}}, "kit_free_plan_required"),
    ({"sending_addresses": []}, "kit_sender_not_verified"),
])
def test_paid_trial_wrong_account_and_unverified_sender_are_blocked(change, code):
    with pytest.raises(KitError, match=code):
        check_free_account({**account(), **change}, expected_id=7, sender="support@valmera.io")
