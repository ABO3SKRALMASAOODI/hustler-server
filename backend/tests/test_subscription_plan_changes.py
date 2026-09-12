"""Upgrade checkouts value unused credits and retire the old contract."""

import os
import sys
from decimal import Decimal

import jwt

os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("PADDLE_API_KEY", "pdl_live_test_key")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402
from routes import paddle  # noqa: E402
from routes import paddle_webhook as webhook  # noqa: E402


class Response:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return {"data": self._data}


def app():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(paddle.paddle_bp)
    return flask_app


def creator_subscription(status="active", price="1500", interval="month"):
    return {
        "id": "sub_creator",
        "status": status,
        "currency_code": "USD",
        "billing_cycle": {"frequency": 1, "interval": interval},
        "items": [{"price": {
            "id": paddle.PLANS["ai"]["price_id"],
            "unit_price": {"amount": price, "currency_code": "USD"},
            "billing_cycle": {"frequency": 1, "interval": interval},
        }}],
    }


def snapshot(remaining=1000, subscribed=1):
    return {
        "plan": "ai", "is_subscribed": subscribed,
        "subscription_id": "sub_creator",
        "credits_monthly": remaining, "credits_monthly_limit": 1000,
        "billing_period": "monthly",
    }


def auth(monkeypatch, row=None, subscription=None):
    monkeypatch.setattr(
        paddle, "decode_token", lambda _header: (7, "buyer@example.com"))
    monkeypatch.setattr(
        paddle, "_subscription_snapshot", lambda _uid: row or snapshot())
    monkeypatch.setattr(
        paddle.requests, "get",
        lambda *_args, **_kwargs: Response(
            data=subscription or creator_subscription()))


def test_unused_creator_pool_halves_a_pro_upgrade():
    credit, percent = paddle._upgrade_credit(1500, 3000, 1000, 1000)
    assert credit == 1500
    assert percent == Decimal("50.000000")


def test_spent_plan_credits_reduce_only_the_unused_value():
    credit, percent = paddle._upgrade_credit(1500, 3000, 250, 1000)
    assert credit == 375
    assert percent == Decimal("12.500000")


def test_yearly_plan_values_one_months_remaining_credit_pool():
    quote = paddle._upgrade_checkout_quote(
        snapshot(), creator_subscription(price="15000", interval="year"),
        "ai_pro", "yearly")
    assert quote["credit_value_minor"] == 1250
    assert quote["charge_minor"] == 28750


def test_upgrade_checkout_gets_one_time_unused_credit_discount(monkeypatch):
    auth(monkeypatch)
    writes = []

    def post(url, **kwargs):
        writes.append((url, kwargs["json"]))
        return Response(status_code=201, data={"id": "dsc_upgrade"})

    monkeypatch.setattr(paddle.requests, "post", post)
    response = app().test_client().post(
        "/paddle/checkout-config",
        json={"plan": "ai_pro", "billing": "monthly"},
        headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_upgrade"] is True
    assert body["remaining_plan_credits"] == 1000
    assert body["credit_value_minor"] == 1500
    assert body["charge_minor"] == 1500
    assert body["discount_id"] == "dsc_upgrade"
    discount = writes[0][1]
    assert discount["amount"] == "50"
    assert discount["enabled_for_checkout"] is True
    assert discount["recur"] is False
    assert discount["usage_limit"] == 1
    intent = jwt.decode(
        body["upgrade_token"], os.environ["SECRET_KEY"], algorithms=["HS256"])
    assert intent["from_subscription_id"] == "sub_creator"
    assert intent["to_plan"] == "ai_pro"


def test_hosted_upgrade_checkout_carries_discount_and_signed_intent(monkeypatch):
    auth(monkeypatch)
    writes = []

    def post(url, **kwargs):
        writes.append((url, kwargs["json"]))
        if url.endswith("/discounts"):
            return Response(status_code=201, data={"id": "dsc_upgrade"})
        return Response(status_code=201, data={
            "id": "txn_upgrade",
            "checkout": {"url": "https://pay.example/upgrade"},
        })

    monkeypatch.setattr(paddle.requests, "post", post)
    response = app().test_client().post(
        "/paddle/create-checkout-session",
        json={"plan": "ai_pro", "billing": "monthly"},
        headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    transaction = next(body for url, body in writes
                       if url.endswith("/transactions"))
    assert transaction["discount_id"] == "dsc_upgrade"
    assert transaction["custom_data"]["upgrade_token"]


def test_checkout_blocks_a_recoverable_past_due_subscription(monkeypatch):
    auth(monkeypatch, row=snapshot(subscribed=0))
    response = app().test_client().post(
        "/paddle/checkout-config", json={"plan": "ai_pro"},
        headers={"Authorization": "Bearer test"})
    assert response.status_code == 409
    assert response.get_json()["code"] == "existing_subscription"


def test_checkout_is_not_used_for_same_tier_or_downgrade(monkeypatch):
    auth(monkeypatch)
    response = app().test_client().post(
        "/paddle/checkout-config", json={"plan": "ai"},
        headers={"Authorization": "Bearer test"})
    assert response.status_code == 409
    assert response.get_json()["code"] == "plan_change_required"


def test_old_subscription_is_retired_only_from_signed_paid_upgrade(monkeypatch):
    quote = paddle._upgrade_checkout_quote(
        snapshot(), creator_subscription(), "ai_pro", "monthly")
    token = paddle._upgrade_token(7, quote, "dsc_upgrade")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return Response(status_code=200)

    monkeypatch.setattr(webhook.requests, "post", post)
    assert webhook._retire_upgrade_source(
        7, "sub_pro", "ai_pro",
        {"custom_data": {"upgrade_token": token}}) is True
    assert calls == [(
        f"{webhook._PADDLE_BASE}/subscriptions/sub_creator/cancel",
        {"effective_from": "next_billing_period"},
    )]


def test_tampered_upgrade_token_never_cancels_a_subscription(monkeypatch):
    monkeypatch.setattr(
        webhook.requests, "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an invalid browser token cannot cancel anything")))
    assert webhook._retire_upgrade_source(
        7, "sub_pro", "ai_pro",
        {"custom_data": {"upgrade_token": "not-a-token"}}) is True


def test_paid_upgrade_webhook_retries_until_old_plan_is_retired(monkeypatch):
    class Db:
        def commit(self):
            pass

    retired = []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_args: 7)
    monkeypatch.setattr(webhook, "get_db", lambda: Db())
    monkeypatch.setattr(webhook, "_plan_from_data", lambda _data: "ai_pro")
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **_kwargs: {
            "recorded": True, "newly_paid": True, "amount_cents": 1500})
    monkeypatch.setattr(
        webhook, "_trial_aware_grant",
        lambda *_args, **_kwargs: (2000, 20, False, "paid"))
    monkeypatch.setattr(
        webhook, "update_user_subscription_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webhook.billing, "record_recovery", lambda *_args: None)
    monkeypatch.setattr(webhook.billing, "set_status", lambda *_args: None)
    monkeypatch.setattr(
        webhook.trial_state, "record_paid_conversion", lambda *_args: None)
    monkeypatch.setattr(webhook, "_record_discount_use", lambda *_args: None)
    monkeypatch.setattr(
        webhook.paid_subscription_alert, "enqueue_and_kick",
        lambda *_args: None)
    monkeypatch.setattr(
        webhook, "_retire_upgrade_source",
        lambda *args: retired.append(args) or False)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(webhook.paddle_webhook)
    response = flask_app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_upgrade", "subscription_id": "sub_pro",
            "status": "completed", "custom_data": {
                "upgrade_token": "signed-by-checkout"},
            "details": {"totals": {
                "grand_total": "1500", "currency_code": "USD"}},
            "items": [{"price": {
                "id": paddle.PLANS["ai_pro"]["price_id"],
                "billing_cycle": {"interval": "month", "frequency": 1},
            }}],
        },
    })

    assert response.status_code == 503
    assert retired and retired[0][:3] == (7, "sub_pro", "ai_pro")
