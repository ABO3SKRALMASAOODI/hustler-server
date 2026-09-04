"""Browser API access is explicit without making every origin trusted."""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _stable_app(monkeypatch):
    import app as app_module

    monkeypatch.setattr(
        app_module, "database_runtime_status", lambda _dsn: "ready")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("FRONTEND_URL", raising=False)


def test_production_frontends_receive_cors_without_a_wildcard():
    from app import create_app

    client = create_app().test_client()
    for origin in ("https://valmera.io", "https://www.valmera.io"):
        response = client.get("/healthz", headers={"Origin": origin})
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert response.headers["Access-Control-Allow-Origin"] != "*"
        assert "Origin" in response.headers.get("Vary", "")


def test_unknown_origins_and_originless_requests_receive_no_cors_grant():
    from app import create_app

    client = create_app().test_client()
    for headers in ({}, {"Origin": "https://attacker.example"}):
        response = client.get("/healthz", headers=headers)
        assert "Access-Control-Allow-Origin" not in response.headers


def test_allowed_preflight_is_bounded_to_configured_headers_and_methods():
    from app import create_app

    response = create_app().test_client().options("/healthz", headers={
        "Origin": "https://valmera.io",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    })

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == \
        "https://valmera.io"
    assert set(response.headers["Access-Control-Allow-Headers"].lower().split(", ")) == \
        {"authorization", "content-type"}
    assert "GET" in response.headers["Access-Control-Allow-Methods"]


def test_additional_preview_origin_requires_explicit_configuration(monkeypatch):
    from app import cors_allowed_origins, create_app

    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://preview.example, *, javascript:bad, https://bad.example/path",
    )
    assert "https://preview.example" in cors_allowed_origins()
    assert "*" not in cors_allowed_origins()
    assert "javascript:bad" not in cors_allowed_origins()
    assert "https://bad.example/path" not in cors_allowed_origins()

    response = create_app().test_client().get(
        "/healthz", headers={"Origin": "https://preview.example"})
    assert response.headers["Access-Control-Allow-Origin"] == \
        "https://preview.example"
