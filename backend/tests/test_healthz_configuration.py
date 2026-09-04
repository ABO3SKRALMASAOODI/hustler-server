"""Deployment health must fail visibly when security settings are absent."""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_healthz_refuses_to_certify_missing_security_configuration(
        monkeypatch):
    from app import create_app

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("PADDLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef")
    app = create_app()

    response = app.test_client().get("/healthz")
    body = response.get_json()

    assert response.status_code == 503
    assert body == {
        "status": "degraded",
        "role": "backend",
        "commit": "0123456789ab",
        "checks": {
            "secret_key": "missing",
            "paddle_webhook_signing": "missing",
            "paddle_api": "missing",
        },
    }
    assert app.config["SECRET_KEY"]
    assert app.config["SECRET_KEY"] != "supersecretkey"


def test_healthz_certifies_configured_security(monkeypatch):
    from app import create_app

    secret = "test-secret-that-is-not-public-and-is-long-enough"
    monkeypatch.setenv("SECRET_KEY", secret)
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "test-paddle-secret")
    monkeypatch.setenv("PADDLE_API_KEY", "test-paddle-api-key")
    app = create_app()

    body = app.test_client().get("/healthz").get_json()

    assert body["status"] == "ok"
    assert body["checks"] == {
        "secret_key": "configured",
        "paddle_webhook_signing": "configured",
        "paddle_api": "configured",
    }
    assert app.config["SECRET_KEY"] == secret


def test_public_or_short_application_keys_are_never_used(monkeypatch):
    from app import create_app

    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "test-paddle-secret")
    monkeypatch.setenv("PADDLE_API_KEY", "test-paddle-api-key")
    for unsafe in ("supersecretkey", "devsecret", "too-short"):
        monkeypatch.setenv("SECRET_KEY", unsafe)
        app = create_app()
        response = app.test_client().get("/healthz")
        body = response.get_json()
        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert body["checks"]["secret_key"] == "missing"
        assert app.config["SECRET_KEY"] != unsafe
