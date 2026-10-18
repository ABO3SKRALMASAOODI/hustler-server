import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, Mock

import jwt
import pytest
from flask import Flask
from routes import marketing_preferences as r


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="unit-test-secret-not-a-production-secret")
    app.register_blueprint(r.marketing_bp, url_prefix="/newsletter")
    conn = MagicMock()
    monkeypatch.setattr(r, "get_db", lambda: conn)
    return app, conn


def headers(app):
    return {"Authorization": "Bearer " + jwt.encode({"sub": "7"}, app.config["SECRET_KEY"], algorithm="HS256")}


def row(**changes):
    return {"email": "reader@example.com", "consented_at": None, "revoked_at": None,
            "unsubscribed_at": None, "kit_state": None, **changes}


def test_account_creation_alone_does_not_enable_marketing(app, monkeypatch):
    app, conn = app
    monkeypatch.setattr(r.marketing, "query", lambda *_: [row()])
    response = app.test_client().get("/newsletter/preferences", headers=headers(app))
    assert response.json["enabled"] is False
    conn.commit.assert_not_called()
    assert app.test_client().get("/newsletter/preferences").status_code == 401


@pytest.mark.parametrize("body", [[], {"enabled": "yes"}, {"enabled": True}, {"enabled": True, "disclosure": "old copy"}])
def test_optin_requires_current_disclosure_and_explicit_boolean(app, monkeypatch, body):
    app, conn = app
    monkeypatch.setattr(r.marketing, "query", lambda *_: [row()])
    response = app.test_client().put("/newsletter/preferences", json=body, headers=headers(app))
    assert response.status_code == 400
    conn.commit.assert_not_called()


def test_provider_suppression_cannot_be_cleared_by_preferences(app, monkeypatch):
    app, conn = app
    monkeypatch.setattr(r.marketing, "query", lambda *_: [row(kit_state="complained")])
    response = app.test_client().put("/newsletter/preferences", headers=headers(app),
        json={"enabled": True, "disclosure": r.marketing.DISCLOSURE})
    assert response.status_code == 409
    conn.commit.assert_not_called()


def test_optout_is_durable_before_calling_provider(app, monkeypatch):
    app, _ = app
    monkeypatch.setattr(r.marketing, "query", lambda *_: [row(consented_at="2026-10-18")])
    actions = []
    monkeypatch.setattr(r.marketing, "suppress_local", lambda *args: actions.append("local"))
    monkeypatch.setattr(r.marketing, "unsubscribe_remote", lambda *args: actions.append("remote"))
    response = app.test_client().put("/newsletter/preferences", headers=headers(app), json={"enabled": False})
    assert response.status_code == 200
    assert response.json["enabled"] is False
    assert actions == ["local", "remote"]


def test_unsigned_webhook_is_rejected_before_database_access(app, monkeypatch):
    app, _ = app
    get_db = Mock(); monkeypatch.setattr(r, "get_db", get_db)
    assert app.test_client().post("/newsletter/kit-webhook", json={"events": []}).status_code == 401
    get_db.assert_not_called()


@pytest.mark.parametrize("payload", [[1], {"events": [1]}, {"events": [{"type": "subscriber.unsubscribed", "data": []}]}])
def test_malformed_signed_events_fail_cleanly(app, monkeypatch, payload):
    app, _ = app
    secret = "test-endpoint-secret"
    monkeypatch.setenv("KIT_WEBHOOK_SECRET", secret)
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    response = app.test_client().post("/newsletter/kit-webhook", data=body, content_type="application/json",
        headers={"X-Kit-Signature": f"t={stamp},v1={sig}"})
    assert response.status_code == 400
