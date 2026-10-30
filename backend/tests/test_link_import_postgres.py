"""Ticket #12 on real PostgreSQL: a pasted link import and its forensics
rows never wait on each other.

30 Oct 2026, user 2651 (free) pasted a Drive link into a new project twice.
Both times POST /messages hung 60 s and failed: _register_link_original
locked the project row FOR UPDATE, then the route wrote two client events on
a SECOND connection before committing. A client_events insert checks its
project_id foreign key with FOR KEY SHARE, which FOR UPDATE blocks; the
event waited on the request's transaction while the request waited on the
event, until Postgres' idle_in_transaction_session_timeout (60 s for the app
role) killed the request's session. The message, the asset and the wall
were rolled back; only the two events survived. The mocked tests in
test_link_import.py could not see it (one connection, no locks).

Real tables live in a throwaway schema (a second connection cannot see a
temp table). CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database:

    QUEUE_TEST_DATABASE_URL=postgresql://u:p@localhost:5432/db \
        python -m pytest tests/test_link_import_postgres.py -q
"""
import os
import secrets
import threading
import time
from urllib.parse import quote, urlparse

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

from routes import video as v                                # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")

SCHEMA = """
CREATE TABLE users(id int PRIMARY KEY, email text, is_subscribed int DEFAULT 0,
  plan text DEFAULT 'free');
CREATE TABLE projects(id int PRIMARY KEY, user_id int REFERENCES users(id),
  chat_session_id int, title text);
CREATE TABLE assets(id serial PRIMARY KEY, project_id int REFERENCES projects(id),
  kind text, storage_key text, bytes bigint, meta jsonb,
  created_at timestamptz DEFAULT NOW());
CREATE TABLE chat_messages(id serial PRIMARY KEY, session_id int, role text,
  content text, meta jsonb, created_at timestamptz DEFAULT NOW());
CREATE TABLE client_events(id serial PRIMARY KEY,
  user_id int REFERENCES users(id) ON DELETE CASCADE,
  project_id int REFERENCES projects(id) ON DELETE CASCADE,
  kind text, asset_id int, detail jsonb, created_at timestamptz DEFAULT NOW());
INSERT INTO users (id, email) VALUES (2651, 'free@example.com');
INSERT INTO projects VALUES (5254, 2651, 5359, 'Untitled project');
"""


@pytest.fixture
def db_url():
    assert urlparse(URL).hostname in {"localhost", "127.0.0.1", "postgres"}
    schema = "t_link_" + secrets.token_hex(4)
    admin = psycopg2.connect(URL)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path = {schema}")
        c.execute(SCHEMA)
    sep = "&" if "?" in URL else "?"
    try:
        yield f"{URL}{sep}options={quote(f'-csearch_path={schema}')}"
    finally:
        with admin.cursor() as c:
            c.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()


def _in_thread(fn, timeout):
    """(finished, result, seconds): fn on another thread, never hanging."""
    out = {}

    def run():
        t0 = time.monotonic()
        out["result"] = fn()
        out["seconds"] = time.monotonic() - t0

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), out.get("result"), out.get("seconds")


def test_a_pasted_link_and_its_events_land_together(db_url, monkeypatch):
    """The route's order exactly: register the link inside the request's
    transaction, then record both events on their own connection, then
    commit. Old code: the first event never returns."""
    monkeypatch.setattr(v, "_subscribe_gate_applies", lambda c, u: True)
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO chat_messages (session_id, role, content) "
                    "VALUES (5359, 'user', 'https://drive.google.com/file/d/x/view')")
        out = v._register_link_original(
            cur, {"chat_session_id": 5359}, 5254, 2651,
            "https://drive.google.com/file/d/x/view",
            "https://drive.google.com/file/d/x/view")
        assert out["subscribe_gated"] is True and out["asset_id"]
        for kind, detail in (
                ("upload_started", {"kind": "original", "source": "link"}),
                ("subscription_upload_locked",
                 {"asset_id": out["asset_id"], "surface": "link"})):
            done, ok, _ = _in_thread(lambda: v.record_client_event(
                2651, 5254, kind, detail=detail, origin="server",
                db_url=db_url), timeout=30)
            assert done, f"{kind}: the event waited on the request's own lock"
            # False would mean it was dropped after its lock timeout: the
            # request's lock still conflicts with the foreign-key check.
            assert ok is True, kind
        conn.commit()
    finally:
        conn.close()
    check = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        c = check.cursor()
        c.execute("SELECT meta->>'upload_state' AS st, meta->>'link_src' AS src "
                  "FROM assets WHERE project_id = 5254 AND kind = 'original'")
        assert c.fetchall() == [{"st": "pending",
                                 "src": "https://drive.google.com/file/d/x/view"}]
        c.execute("SELECT role, meta->>'kind' AS k FROM chat_messages "
                  "WHERE session_id = 5359 ORDER BY id")
        assert [r["role"] for r in c.fetchall()] == ["user", "assistant"]
        c.execute("SELECT kind FROM client_events ORDER BY id")
        assert [r["kind"] for r in c.fetchall()] == [
            "upload_started", "subscription_upload_locked"]
    finally:
        check.close()


def test_a_forensics_row_never_blocks_its_caller(db_url):
    """The class guard: whatever lock the CALLER's open transaction holds on
    the project row, record_client_event gives up within its lock timeout
    and drops the row (telemetry), instead of waiting on its own caller."""
    def record():
        return v.record_client_event(2651, 5254, "upload_started",
                                     detail={"source": "link"},
                                     origin="server", db_url=db_url)

    # This machine's own cost of one unblocked call (connect + a few
    # statements), so the bound below holds on a slow link to the database.
    done, ok, base = _in_thread(record, timeout=30)
    assert done and ok is True
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM projects WHERE id = 5254 FOR UPDATE")
        done, ok, seconds = _in_thread(
            record, timeout=base + v.CLIENT_EVENT_LOCK_TIMEOUT_MS / 1000 + 20)
        assert done, "record_client_event blocked on its caller's lock"
        assert ok is False
        assert seconds < base + v.CLIENT_EVENT_LOCK_TIMEOUT_MS / 1000 + 2
    finally:
        conn.rollback()
        conn.close()
