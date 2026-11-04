"""Real PostgreSQL coverage of the server-side wallet warning and recharge."""
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

import anthropic_wallet as wallet


@pytest.fixture
def db():
    dsn = os.environ.get("ADMIN_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("local/CI PostgreSQL required")
    params = psycopg2.extensions.parse_dsn(dsn)
    if (params.get("host") not in ("localhost", "127.0.0.1") or
            params.get("dbname") not in ("wallet_test", "valmera_test", "valmera_migration_test")):
        pytest.fail("Wallet tests require an explicitly configured local test database")
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    schema = "wallet_" + uuid.uuid4().hex
    with conn.cursor() as cur:
        cur.execute('CREATE SCHEMA "' + schema + '"')
        cur.execute('SET search_path TO "' + schema + '"')
        cur.execute("""CREATE TABLE app_kv(key text PRIMARY KEY,value text,updated_at timestamptz);
          CREATE TABLE trial_budget_days(day date PRIMARY KEY,spent_usd numeric NOT NULL DEFAULT 0,
              extras_usd numeric NOT NULL DEFAULT 0,paid_usd numeric NOT NULL DEFAULT 0);
          CREATE TABLE trial_alerts(id bigserial PRIMARY KEY,dedupe_key text UNIQUE,kind text,
              subject text,html_content text,status text DEFAULT 'pending',
              attempt_count int DEFAULT 0,last_error text,sent_at timestamptz);
          INSERT INTO trial_budget_days(day,spent_usd,extras_usd,paid_usd)
              VALUES ('2026-11-04',10,2,100)""")
    conn.commit()
    try:
        yield conn
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SET search_path TO public')
            cur.execute('DROP SCHEMA "' + schema + '" CASCADE')
        conn.commit()
        conn.close()


def spend(db, amount):
    with db.cursor() as cur:
        cur.execute("UPDATE trial_budget_days SET paid_usd=paid_usd+%s", (amount,))


def alerts(db):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM trial_alerts ORDER BY id")
        return cur.fetchall()


def test_threshold_is_durable_deduplicated_and_rearmed_only_by_recharge(db):
    now = datetime.now(timezone.utc)
    assert wallet.status(db)["configured"] is False
    assert wallet.scan(db, now) == 0
    wallet.confirm_balance(db, 2.21)
    assert wallet.scan(db, now) == 0
    spend(db, 1.21)
    assert wallet.scan(db, now + timedelta(minutes=2)) == 1
    db.commit()  # A process restart cannot forget the warning.
    assert wallet.scan(db, now + timedelta(minutes=4)) == 0
    assert wallet.status(db)["estimated_remaining_usd"] == 1
    assert alerts(db)[0]["status"] == "pending"
    assert "estimated" in alerts(db)[0]["subject"]
    wallet.confirm_balance(db, .75)
    assert wallet.scan(db, now + timedelta(minutes=6)) == 0
    wallet.confirm_balance(db, 5)
    assert alerts(db)[0]["status"] == "failed"
    assert alerts(db)[0]["attempt_count"] == 8
    assert alerts(db)[0]["sent_at"] is None
    spend(db, 4.1)
    assert wallet.scan(db, now + timedelta(minutes=8)) == 1
    assert len(alerts(db)) == 2


def test_month_rollover_and_non_anthropic_extras_do_not_refill_or_drain_wallet(db):
    wallet.confirm_balance(db, 2)
    with db.cursor() as cur:
        cur.execute("UPDATE trial_budget_days SET spent_usd=spent_usd+30,extras_usd=extras_usd+30")
        cur.execute("INSERT INTO trial_budget_days(day,paid_usd) VALUES ('2026-12-01',.5)")
    assert wallet.status(db)["estimated_remaining_usd"] == 1.5


def test_missing_meter_history_is_unknown_and_never_sends_a_false_warning(db):
    wallet.confirm_balance(db, 2)
    with db.cursor() as cur:
        cur.execute("DELETE FROM trial_budget_days")
    status = wallet.status(db)
    assert status["needs_resync"] and status["estimated_remaining_usd"] is None
    assert wallet.scan(db) == 0
    assert alerts(db) == []


@pytest.mark.parametrize("balance", [None, True, -1, "1", float("inf"), float("nan"), 1000001])
def test_invalid_balance_cannot_arm_monitor(db, balance):
    with pytest.raises(ValueError):
        wallet.confirm_balance(db, balance)
    assert wallet.status(db)["configured"] is False


def test_only_owner_can_read_or_calibrate_wallet(db, monkeypatch):
    from flask import Flask
    from routes import admin_video
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "wallet-test-key-not-a-production-credential"
    app.register_blueprint(admin_video.admin_video_bp)

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(admin_video, "adb", connection)
    client = app.test_client()
    path = "/admin/video/anthropic-wallet"
    assert client.post(path, json={"balance_usd": 2}).status_code == 401
    def auth(email):
        return {"Authorization": "Bearer " + jwt.encode({"email": email}, app.config["SECRET_KEY"], algorithm="HS256")}
    assert client.get(path, headers=auth("customer@example.test")).status_code == 403
    assert client.post(path, headers=auth("customer@example.test"), json={"balance_usd": 2}).status_code == 403
    owner = auth("thevalmera@gmail.com")
    assert client.post(path, headers=owner, json={"balance_usd": True}).status_code == 400
    result = client.post(path, headers=owner, json={"balance_usd": 2.21})
    assert result.status_code == 200
    assert result.json["estimated_remaining_usd"] == 2.21
    assert result.json["recipient"] == "thevalmera@gmail.com"
    assert client.get(path, headers=owner).json["configured"] is True
