"""The 280 routine / 20 critical Brevo reservation is account-wide."""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brevo_delivery  # noqa: E402


class _Response:
    def __init__(self, status=201, text=""):
        self.status_code = status
        self.text = text

    def json(self):
        return {"plan": [{"type": "free", "creditsType": "sendLimit", "credits": 300}]}


@pytest.fixture(autouse=True)
def provider_has_capacity(monkeypatch):
    monkeypatch.setattr(brevo_delivery.requests, "get", lambda *_a, **_k: _Response(200))


def _payload():
    return {"subject": "hello", "to": [{"email": "x@example.com"}]}


def test_default_budget_reserves_twenty_from_three_hundred():
    assert brevo_delivery.DAILY_LIMIT == 300
    assert brevo_delivery.BULK_LIMIT == 280
    assert brevo_delivery.CRITICAL_RESERVE == 20


def test_bulk_ceiling_blocks_before_calling_brevo(monkeypatch):
    called = []
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(brevo_delivery, "_reserve", lambda _category: False)
    monkeypatch.setattr(
        brevo_delivery.requests, "post",
        lambda *_a, **_k: called.append(True) or _Response())

    assert brevo_delivery.send_email(_payload(), category="bulk") is False
    assert called == []


def test_critical_email_fails_open_when_budget_database_is_unavailable(
        monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(
        brevo_delivery, "_reserve",
        lambda _category: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(
        brevo_delivery.requests, "post",
        lambda *_a, **_k: _Response(201))

    assert brevo_delivery.send_email(_payload(), category="critical") is True


def test_failed_delivery_returns_its_reserved_slot(monkeypatch):
    released = []
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(brevo_delivery, "_reserve", lambda _category: True)
    monkeypatch.setattr(
        brevo_delivery, "_release",
        lambda category: released.append(category))
    monkeypatch.setattr(
        brevo_delivery.requests, "post",
        lambda *_a, **_k: _Response(429, "daily quota"))

    assert brevo_delivery.send_email(_payload(), category="bulk") is False
    assert released == ["bulk"]


@pytest.mark.parametrize("category,remaining", [("bulk", 20), ("bulk", 0), ("critical", 0)])
def test_real_provider_exhaustion_blocks_even_when_local_budget_has_room(monkeypatch, category, remaining):
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(brevo_delivery, "provider_capacity", lambda: {"remaining": remaining, "error": None})
    monkeypatch.setattr(brevo_delivery, "_reserve", lambda _: True)
    monkeypatch.setattr(brevo_delivery.requests, "post", lambda *_a, **_k: pytest.fail("must not add to Brevo's queue"))
    assert not brevo_delivery.send_email(_payload(), category=category)


def test_reserved_provider_credits_remain_usable_for_subscriber_alerts(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(brevo_delivery, "provider_capacity", lambda: {"remaining": 1, "error": None})
    monkeypatch.setattr(brevo_delivery, "_reserve", lambda _: True)
    monkeypatch.setattr(brevo_delivery.requests, "post", lambda *_a, **_k: _Response(201))
    assert brevo_delivery.send_email(_payload(), category="critical")


def test_unknown_provider_capacity_pauses_marketing(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "test")
    monkeypatch.setattr(brevo_delivery, "provider_capacity", lambda: {"remaining": None, "error": "timeout"})
    monkeypatch.setattr(brevo_delivery.requests, "post", lambda *_a, **_k: pytest.fail("unknown quota must not be guessed"))
    assert not brevo_delivery.send_email(_payload(), category="bulk")


def test_capacity_read_returns_no_private_account_fields(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "test")
    assert brevo_delivery.provider_capacity() == {"remaining": 300, "error": None}


@pytest.mark.parametrize("remaining,bulk,total", [(0, 0, 0), (20, 0, 20), (100, 80, 100), (None, 0, 297)])
def test_dashboard_capacity_uses_provider_allowance_not_just_local_counter(monkeypatch, remaining, bulk, total):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, *_): pass
        def fetchone(self): return (0, 3)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close(self): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(brevo_delivery, "_connect", Connection)
    monkeypatch.setattr(brevo_delivery, "_ensure_schema", lambda _: None)
    monkeypatch.setattr(brevo_delivery, "provider_capacity", lambda: {"remaining": remaining, "error": None})
    status = brevo_delivery.budget_status()
    assert status["bulk_remaining"] == bulk
    assert status["total_remaining"] == total
    assert status["local_bulk_remaining"] == 280
