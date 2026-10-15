"""Checkout-interest telemetry accepts every currently purchasable tier."""

import os
import sys

from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan_catalog import PURCHASABLE_PLANS  # noqa: E402
from routes import onboarding  # noqa: E402


class _Cursor:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.statements.append((" ".join(sql.split()), params))


class _Connection:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.closed = False

    def cursor(self):
        return _Cursor(self.statements)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_plan_intent_accepts_every_paddle_checkout_tier(monkeypatch):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-only-secret"
    app.register_blueprint(onboarding.onboarding_bp)

    for plan in sorted(PURCHASABLE_PLANS):
        connection = _Connection()
        monkeypatch.setattr(onboarding, "get_db", lambda: connection)

        response = app.test_client().post(
            "/billing/intent",
            json={"plan": plan, "billing": "monthly", "price_usd": 1},
        )

        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        assert connection.committed and connection.closed
        assert connection.statements[0][1][2] == plan


def test_plan_intent_still_rejects_unknown_tiers(monkeypatch):
    monkeypatch.setattr(
        onboarding, "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened")),
    )
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-only-secret"
    app.register_blueprint(onboarding.onboarding_bp)

    response = app.test_client().post(
        "/billing/intent", json={"plan": "retired"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "unknown plan"}


def test_name_is_authenticated_bounded_and_updates_only_token_account(monkeypatch):
    import jwt
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-name-secret-that-is-long-enough"
    app.register_blueprint(onboarding.onboarding_bp)
    connection = _Connection()
    monkeypatch.setattr(onboarding, "get_db", lambda: connection)
    client = app.test_client()
    assert client.put("/onboarding/name", json={"name": "Test"}).status_code == 401
    headers = {"Authorization": "Bearer " + jwt.encode({"sub": "17", "email": "test@example.com"}, app.config["SECRET_KEY"], algorithm="HS256")}
    for bad in [None, 123, "", "x" * 121]:
        assert client.put("/onboarding/name", headers=headers, json={"name": bad}).status_code == 400
    r = client.put("/onboarding/name", headers=headers, json={"name": "  Test   Person  ", "user_id": 999})
    assert r.status_code == 200
    assert connection.statements[0][1] == ("Test Person", "17")
    assert connection.committed and connection.closed
