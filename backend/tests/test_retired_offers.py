"""Retired discounts cannot be revived by stale rows, links or settings."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

import offers
from routes.newsletter import _eligible
from routes.newsletter_content import DEFAULT_TEMPLATES, LIFECYCLE_ORDER


class NoDatabase:
    def cursor(self):
        raise AssertionError("retired offers must not open a database cursor")


def test_all_new_offer_entry_points_are_inactive(monkeypatch):
    monkeypatch.setenv("PADDLE_DISCOUNT_ID", "old-live-id")
    monkeypatch.setitem(offers._discount, "id", "cached-live-id")
    def no_request(*args, **kwargs):
        raise AssertionError("retired discounts must not call Paddle")
    monkeypatch.setattr(offers.requests, "get", no_request)
    monkeypatch.setattr(offers.requests, "post", no_request)
    monkeypatch.setattr(offers.requests, "patch", no_request)
    conn = NoDatabase()
    for kind in offers.KINDS:
        assert offers.mint(conn, 7, kind) is None
        assert offers.send_offer_email(conn, 7, "test@example.com", kind) is False
    assert offers.public(conn, 7) == {"active": False}
    assert offers.discount_id() is None
    assert offers._find_discount({}) is None
    assert offers._create_discount({}) is None
    assert offers.apply_to_subscription("existing-subscription")[0] is False


def test_campaign_cannot_send_on_scheduled_or_forced_runs():
    assert "offer_50" not in LIFECYCLE_ORDER
    assert DEFAULT_TEMPLATES["offer_50"]["enabled"] is False
    assert _eligible(NoDatabase(), "offer_50") == []
