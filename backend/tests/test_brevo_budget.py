"""The 280 routine / 20 critical Brevo reservation is account-wide."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brevo_delivery  # noqa: E402


class _Response:
    def __init__(self, status=201, text=""):
        self.status_code = status
        self.text = text


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
