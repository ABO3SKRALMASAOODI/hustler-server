"""The ledger's SQL on real PostgreSQL: the migration's table, the upsert, and
what the fence reads. The rest of the fence runs on stand-ins that cannot tell
one WHERE clause from another."""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402
import pytest  # noqa: E402

import spend_ledger  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    dsn = os.environ.get("ADMIN_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("local/CI PostgreSQL required")
    params = psycopg2.extensions.parse_dsn(dsn)
    if (params.get("host") not in ("localhost", "127.0.0.1") or
            params.get("dbname") not in ("valmera_test", "valmera_migration_test")):
        pytest.fail("Ledger tests require an explicitly configured local test database")
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    schema = "ledger_" + uuid.uuid4().hex
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"; SET search_path TO "{schema}"')
        cur.execute("CREATE TABLE app_kv(key text PRIMARY KEY, value text)")
        cur.execute((ROOT / "backend/migrations/043_spend_ledger.sql").read_text())
        cur.execute("""INSERT INTO app_kv VALUES ('claude_credit', %s)""", (
            '{"cycle_budget_usd": 148, "safety_margin_usd": 10,'
            ' "cycle_start": "2026-11-05T00:00:00+00:00",'
            ' "cycle_end": "2026-12-08T00:00:00+00:00"}',))
    conn.commit()
    monkeypatch.setattr(spend_ledger, "_migrated", True)   # public has no table here
    try:
        yield conn
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.commit()
        conn.close()


def _row(conn, day, provider, lane, usd, purpose="agent"):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO spend_ledger (day, provider, lane, purpose, usd, calls)
                       VALUES (%s, %s, %s, %s, %s, 1)""",
                    (day, provider, lane, purpose, usd))


def test_a_charge_adds_up_on_one_row(db):
    assert spend_ledger.charge(db, "openai", "customer", "vision", 0.25) is True
    assert spend_ledger.charge(db, "openai", "customer", "vision", 0.5) is True
    with db.cursor() as cur:
        cur.execute("SELECT usd, calls FROM spend_ledger")
        rows = cur.fetchall()
    assert [(float(r["usd"]), r["calls"]) for r in rows] == [(0.75, 2)]


def test_an_unknown_lane_never_lands(db):
    with pytest.raises(psycopg2.errors.CheckViolation):
        spend_ledger.charge(db, "openai", "nobody", "vision", 0.25)


def test_a_lane_reads_its_own_provider_and_its_own_rows(db):
    today, day = NOW.date(), date(2026, 11, 10)
    _row(db, today, "anthropic", "internal", 1.0)
    _row(db, today, "openai", "internal", 4.0)
    _row(db, today, "anthropic", "customer", 50.0)
    _row(db, today, "anthropic", "replay", 7.0)
    _row(db, day, "anthropic", "internal", 2.0)
    room = spend_ledger.room(db, "internal", "anthropic", now=NOW)
    assert room["open"] is True
    assert room["day_spent_usd"] == 1.0 and room["cycle_spent_usd"] == 3.0
    assert room["left_usd"] == 4.0        # $5 a day, $1 spent today


def test_the_day_and_the_cycle_each_close_a_lane(db):
    today = NOW.date()
    _row(db, today, "openai", "internal", 5.0)
    assert spend_ledger.room(db, "internal", "openai", now=NOW)["open"] is False
    assert spend_ledger.room(db, "internal", "anthropic", now=NOW)["open"] is True
    _row(db, today - timedelta(days=3), "anthropic", "internal", 20.0)
    room = spend_ledger.room(db, "internal", "anthropic", now=NOW)
    assert room["open"] is False and room["day_spent_usd"] == 0


def test_a_new_cycle_starts_clean(db):
    _row(db, date(2026, 11, 4), "anthropic", "internal", 500.0)
    assert spend_ledger.room(db, "internal", "anthropic", now=NOW)["open"] is True


def test_a_share_set_in_app_kv_wins(db):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO app_kv VALUES ('spend_fence', %s)""", (
            '{"replay": {"anthropic": {"day_usd": 1, "cycle_usd": 2}}}',))
    _row(db, NOW.date(), "anthropic", "replay", 1.0)
    assert spend_ledger.room(db, "replay", "anthropic", now=NOW)["open"] is False
    assert spend_ledger.room(db, "replay", "openai", now=NOW)["open"] is True


def test_the_cycle_report_groups_by_provider_lane_and_purpose(db):
    today = NOW.date()
    _row(db, today, "anthropic", "customer", 1.0)
    _row(db, today - timedelta(days=1), "anthropic", "customer", 2.0)
    _row(db, date(2026, 11, 1), "anthropic", "customer", 9.0)
    report = spend_ledger.cycle_rows(db, now=NOW)
    assert report["rows"] == [{"provider": "anthropic", "lane": "customer",
                               "purpose": "agent", "usd": 3.0, "calls": 2}]
