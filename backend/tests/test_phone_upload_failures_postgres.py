"""The phone widget's "upload failures" counts one person's rapid repeats as
ONE failure (ticket #12), on real PostgreSQL with temp tables.

30 Oct 2026: user 2649 picked the same 11 empty downloader files twice in a
minute (plus two single re-picks); the widget read "24 upload failures
today" for one person's one problem. The rows stay in client_events and the
raw number is still reported as upload_failure_events.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
import psycopg2
from psycopg2.extras import Json, RealDictCursor

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

from routes import phone_status                              # noqa: E402
from routes.admin import _scope                              # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")

SCHEMA = """
SET TIME ZONE 'UTC';
CREATE TEMP TABLE users(id int PRIMARY KEY, email text,
  created_at timestamp DEFAULT '2026-10-30');
CREATE TEMP TABLE client_events(id serial PRIMARY KEY, user_id int,
  project_id int, kind text, detail jsonb, created_at timestamptz);
INSERT INTO users (id, email) VALUES (2649, 'a@example.com'),
  (2655, 'b@example.com'), (2700, 'c@example.com');
"""
T0 = datetime(2026, 10, 30, 13, 25, 33, tzinfo=timezone.utc)


@pytest.fixture
def cur():
    assert urlparse(URL).hostname in {"localhost", "127.0.0.1", "postgres"}
    conn = psycopg2.connect(URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as c:
            c.execute(SCHEMA)
            yield c
    finally:
        conn.rollback()
        conn.close()


def event(cur, uid, at, kind="upload_rejected", **detail):
    cur.execute("INSERT INTO client_events (user_id, project_id, kind, "
                "detail, created_at) VALUES (%s, 1, %s, %s, %s)",
                (uid, kind, Json(dict({"reason": "empty or incomplete file",
                                       "bytes": 0}, **detail)), at))


def count(cur):
    cur.execute(phone_status.UPLOAD_FAILURES_SQL.format(scope=_scope("u")),
                (T0 - timedelta(hours=6), T0 + timedelta(hours=6)))
    row = cur.fetchone()
    return row["failures"], row["events"]


def test_one_persons_burst_is_one_failure(cur):
    # 2649's real pattern: 11 + 11 in two picks 33 s apart, then 2 re-picks.
    for i in range(11):
        event(cur, 2649, T0 + timedelta(milliseconds=50 * i))
    for i in range(11):
        event(cur, 2649, T0 + timedelta(seconds=33, milliseconds=50 * i))
    event(cur, 2649, T0 + timedelta(seconds=45))
    event(cur, 2649, T0 + timedelta(seconds=57))
    assert count(cur) == (1, 24)


def test_separate_people_and_separate_times_still_count(cur):
    event(cur, 2649, T0)
    event(cur, 2649, T0 + timedelta(seconds=30))
    # the same person again after a real gap is a new failure
    event(cur, 2649, T0 + timedelta(minutes=40))
    # another person at the very same moment is another failure
    event(cur, 2655, T0, kind="upload_failed", reason="network")
    # a chain of repeats each under the gap stays one failure
    for m in (0, 8, 16, 24):
        event(cur, 2700, T0 + timedelta(minutes=m))
    assert count(cur) == (4, 8)


def test_an_accepted_upload_is_not_a_failure(cur):
    event(cur, 2655, T0, reason="over duration cap", accepted=True)
    event(cur, 2655, T0 + timedelta(hours=1), kind="upload_started")
    assert count(cur) == (0, 0)
