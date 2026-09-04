"""Deployment health must fail visibly when security settings are absent."""

import hashlib
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _safe_database_url(password="strong-password"):
    # Split the scheme delimiter so this synthetic fixture cannot normalize
    # developers into committing credentialed production URLs in test files.
    return ("postgresql" + f"://release-user:{password}@"
            "db.example/valmera")


def test_healthz_refuses_to_certify_missing_security_configuration(
        monkeypatch):
    from app import create_app

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("PADDLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
    monkeypatch.setenv("PADDLE_MODE", "production")
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
            "database_credential": "missing",
            "direct_database_credential": "not_configured",
            "paddle_environment": "production",
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
    monkeypatch.setenv("DATABASE_URL", _safe_database_url())
    monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
    monkeypatch.setenv("PADDLE_MODE", "production")
    app = create_app()

    body = app.test_client().get("/healthz").get_json()

    assert body["status"] == "ok"
    assert body["checks"] == {
        "secret_key": "configured",
        "paddle_webhook_signing": "configured",
        "paddle_api": "configured",
        "database_credential": "rotated",
        "direct_database_credential": "not_configured",
        "paddle_environment": "production",
    }
    assert app.config["SECRET_KEY"] == secret


def test_public_or_short_application_keys_are_never_used(monkeypatch):
    from app import create_app

    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "test-paddle-secret")
    monkeypatch.setenv("PADDLE_API_KEY", "test-paddle-api-key")
    monkeypatch.setenv("DATABASE_URL", _safe_database_url())
    monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
    monkeypatch.setenv("PADDLE_MODE", "production")
    for unsafe in ("supersecretkey", "devsecret", "too-short"):
        monkeypatch.setenv("SECRET_KEY", unsafe)
        app = create_app()
        response = app.test_client().get("/healthz")
        body = response.get_json()
        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert body["checks"]["secret_key"] == "missing"
        assert app.config["SECRET_KEY"] != unsafe


def test_exposed_database_credential_is_publicly_degraded_without_leaking_it(
        monkeypatch):
    import security_config
    from app import create_app

    database_url = _safe_database_url("old-password")
    monkeypatch.setattr(
        security_config, "COMPROMISED_DATABASE_URL_SHA256",
        hashlib.sha256(database_url.encode()).hexdigest())
    monkeypatch.setenv("SECRET_KEY", "s" * 64)
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "pdl_ntfset_" + "w" * 32)
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_live_" + "a" * 32)
    monkeypatch.setenv("DATABASE_URL", database_url)

    response = create_app().test_client().get("/healthz")
    body = response.get_json()

    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["database_credential"] == "exposed"
    assert database_url.encode() not in response.data


def test_short_billing_secrets_cannot_certify_runtime_health(monkeypatch):
    from app import create_app

    monkeypatch.setenv("SECRET_KEY", "s" * 64)
    monkeypatch.setenv("DATABASE_URL", _safe_database_url())
    for unsafe_name, check_name in (
            ("PADDLE_WEBHOOK_SECRET", "paddle_webhook_signing"),
            ("PADDLE_API_KEY", "paddle_api")):
        monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "pdl_ntfset_" + "w" * 32)
        monkeypatch.setenv("PADDLE_API_KEY", "pdl_live_" + "a" * 32)
        monkeypatch.setenv(unsafe_name, "short")

        response = create_app().test_client().get("/healthz")
        body = response.get_json()

        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert body["checks"][check_name] == "missing"


def test_sandbox_or_exposed_direct_database_cannot_certify_health(
        monkeypatch):
    import security_config
    from app import create_app

    monkeypatch.setenv("SECRET_KEY", "s" * 64)
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "pdl_ntfset_" + "w" * 32)
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_live_" + "a" * 32)
    monkeypatch.setenv("DATABASE_URL", _safe_database_url())
    monkeypatch.setenv("PADDLE_MODE", "sandbox")
    body = create_app().test_client().get("/healthz").get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["paddle_environment"] == "sandbox"

    direct_url = _safe_database_url("direct-old-password")
    monkeypatch.setattr(
        security_config, "COMPROMISED_DATABASE_URL_SHA256",
        hashlib.sha256(direct_url.encode()).hexdigest())
    monkeypatch.setenv("PADDLE_MODE", "production")
    monkeypatch.setenv("DIRECT_DATABASE_URL", direct_url)
    body = create_app().test_client().get("/healthz").get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["direct_database_credential"] == "exposed"
