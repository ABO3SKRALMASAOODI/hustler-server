"""Paddle web routes stay bounded and keep their fallback checkout usable."""

import ast
import inspect
import os
import sys

import requests


os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("PADDLE_API_KEY", "pdl_live_test_key")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402
from routes import newsletter, paddle  # noqa: E402


class _OfferDb:
    def close(self):
        pass


class _CheckoutResponse:
    status_code = 201
    text = "private-provider-response-marker"

    @staticmethod
    def json():
        return {"data": {
            "id": "txn_123",
            "checkout": {"url": "https://checkout.paddle.test/txn_123"},
        }}


def _app():
    app = Flask(__name__)
    app.register_blueprint(paddle.paddle_bp)
    return app


def test_hosted_checkout_defaults_to_live_creator_and_has_a_deadline(
        monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(
        paddle, "decode_token", lambda _header: (7, "buyer@example.com"))
    monkeypatch.setattr(paddle, "_subscription_snapshot", lambda _uid: {})
    monkeypatch.setattr(paddle, "get_offers_db", lambda: _OfferDb())
    monkeypatch.setattr(paddle.offers, "live_offer", lambda *_args: None)

    def post(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return _CheckoutResponse()

    monkeypatch.setattr(paddle.requests, "post", post)
    response = _app().test_client().post(
        "/paddle/create-checkout-session", json={},
        headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    assert response.get_json()["plan"] == "ai"
    assert seen["json"]["items"] == [{
        "price_id": paddle.PLANS["ai"]["price_id"], "quantity": 1}]
    assert seen["json"]["custom_data"]["billing"] == "monthly"
    assert seen["timeout"] == paddle.PADDLE_API_TIMEOUT
    assert "private-provider-response-marker" not in capsys.readouterr().out


def test_checkout_provider_timeout_is_explicitly_retryable(monkeypatch):
    monkeypatch.setattr(
        paddle, "decode_token", lambda _header: (7, "buyer@example.com"))
    monkeypatch.setattr(paddle, "_subscription_snapshot", lambda _uid: {})
    monkeypatch.setattr(
        paddle.requests, "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.Timeout("provider stalled")))

    response = _app().test_client().post(
        "/paddle/create-checkout-session",
        json={"plan": "ai_max", "billing": "monthly"},
        headers={"Authorization": "Bearer test"})

    assert response.status_code == 503
    assert response.get_json()["retryable"] is True


def test_every_direct_live_web_provider_call_has_a_timeout():
    missing = []
    for module in (paddle, newsletter):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "requests"):
                continue
            if func.attr not in {"get", "post", "put", "patch", "delete",
                                 "request", "head"}:
                continue
            if not any(keyword.arg == "timeout" for keyword in node.keywords):
                missing.append((module.__name__, func.attr, node.lineno))
    assert missing == []
