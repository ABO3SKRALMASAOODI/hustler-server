"""An upload that falls silent without landing files a ticket, on real
PostgreSQL with temp tables (census 6 Nov 2026: 18 of 23 accounts that lost an
upload left no row saying so).

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
import psycopg2
from psycopg2.extras import Json, RealDictCursor

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

from ops_loop import intake                                  # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")

SCHEMA = """
SET TIME ZONE 'UTC';
CREATE TEMP TABLE users(id int PRIMARY KEY, email text, is_admin boolean,
  created_at timestamp DEFAULT '2026-11-01', is_subscribed int DEFAULT 0,
  plan text DEFAULT 'free');
CREATE TEMP TABLE projects(id int PRIMARY KEY, user_id int);
CREATE TEMP TABLE client_events(id serial PRIMARY KEY, user_id int,
  project_id int, kind text, asset_id int, detail jsonb, created_at timestamptz);
INSERT INTO users (id, email) VALUES (2837, 'a@example.com'), (2846, 'b@example.com');
INSERT INTO projects VALUES (5499, 2837), (5600, 2846);
"""
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(minutes=1)


@pytest.fixture
def cur(monkeypatch):
    monkeypatch.setattr(intake, "_customers", lambda: "TRUE")
    conn = psycopg2.connect(URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    c = conn.cursor()
    c.execute(SCHEMA)
    yield c
    conn.rollback()
    conn.close()


def event(c, event_kind, ago, bytes_=7_845_957_336, user=2837, project=5499,
          **detail):
    c.execute("""INSERT INTO client_events (user_id, project_id, kind, detail,
                 created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
              (user, project, event_kind, Json(dict(detail, bytes=bytes_)),
               NOW - ago))
    return c.fetchone()["id"]


GONE = intake.UPLOAD_SILENT_AFTER
JUST = timedelta(seconds=30)


def stopped(c):
    return intake._stopped_uploads(c, SINCE, NOW, {
        "since": SINCE, "now": NOW, "limit": 100})


def test_the_silent_upload_files_a_ticket(cur):
    """2837 at 01:48: upload_started, then nothing, ever."""
    eid = event(cur, "upload_started", GONE + JUST, kind="original",
                filename="PARTIDO CHACARITA-CHICAGO.mp4")
    out = stopped(cur)
    assert [f["ref"] for f in out] == [f"stopped:{eid}"]
    f = out[0]
    assert f["cause_key"].startswith("auto-upload-stopped")
    assert f["cause_key"].endswith("no-signal")
    assert f["severity"] == "P1"
    assert "PARTIDO CHACARITA-CHICAGO.mp4" in f["detail"]


def test_a_page_that_said_it_was_leaving_is_its_own_cause(cur):
    event(cur, "upload_started", GONE * 3)
    eid = event(cur, "upload_interrupted", GONE + JUST, kind="original",
                sent_bytes=5_469_372_416, filename="a.mp4")
    out = stopped(cur)
    assert [f["ref"] for f in out] == [f"stopped:{eid}"]
    assert out[0]["cause_key"].endswith("page-left")
    assert "5469372416" in out[0]["detail"]


def test_a_transfer_with_a_heartbeat_is_not_stopped(cur):
    event(cur, "upload_started", GONE + JUST)
    event(cur, "upload_progress", timedelta(minutes=4))
    assert stopped(cur) == []


def test_one_that_landed_failed_or_finished_its_original_is_not_stopped(cur):
    event(cur, "upload_started", GONE + JUST)
    event(cur, "upload_landed", GONE)
    event(cur, "upload_started", GONE + JUST, bytes_=500, user=2846, project=5600)
    event(cur, "upload_failed", GONE, bytes_=500, user=2846, project=5600)
    assert stopped(cur) == []


def test_a_beat_recorded_just_after_the_landing_is_not_a_stop(cur):
    """#401, 14 Nov: user 3048's 58 MB original landed at 01:54:56.335; the
    page's 30 s beat, sent as the last part finished, was recorded 0.3 s later.
    The file was in the project, and the watch paged a P0 for it."""
    event(cur, "upload_started", GONE + JUST, bytes_=58_638_069, kind="original")
    landed = GONE + timedelta(milliseconds=300)
    event(cur, "upload_landed", landed, bytes_=58_638_069, kind="original")
    event(cur, "upload_progress", GONE, bytes_=58_638_069, kind="original",
          sent_bytes=58_638_069)
    assert stopped(cur) == []


def test_the_same_file_sent_again_after_landing_is_judged_again(cur):
    event(cur, "upload_started", timedelta(hours=1))
    event(cur, "upload_landed", timedelta(minutes=50))
    eid = event(cur, "upload_started", GONE + JUST)
    assert [f["ref"] for f in stopped(cur)] == [f"stopped:{eid}"]


def test_a_landed_proxy_first_original_is_only_landed_when_its_bytes_are(cur):
    eid = event(cur, "upload_started", GONE + JUST)
    event(cur, "upload_landed", GONE, original_pending=True)
    assert [f["ref"] for f in stopped(cur)] == [f"stopped:{eid}"]
    event(cur, "upload_original_ready", GONE - JUST)
    assert stopped(cur) == []


def test_a_browser_proxy_is_never_judged_alone(cur):
    event(cur, "upload_started", GONE + JUST, bytes_=30_000_000, kind="proxy")
    assert stopped(cur) == []


def test_a_resumed_transfer_is_judged_by_its_last_sign_of_life(cur):
    event(cur, "upload_started", timedelta(hours=2))
    eid = event(cur, "upload_resumed", GONE + JUST, bytes_done=3_825_205_248,
                filename="a.mp4")
    out = stopped(cur)
    assert [f["ref"] for f in out] == [f"stopped:{eid}"]
    assert "3825205248" in out[0]["detail"]


def test_a_link_import_has_no_transfer_to_lose(cur):
    """routes/video.py records upload_started with no bytes for a pasted link.
    It never lands by size: its index job reports it, and it is not a stop."""
    cur.execute("""INSERT INTO client_events (user_id, project_id, kind, detail,
                   created_at) VALUES (2837, 5499, 'upload_started', %s, %s)""",
                (Json({"kind": "original", "source": "link", "host": "x.test"}),
                 NOW - GONE - JUST))
    assert stopped(cur) == []


def test_a_heartbeat_says_how_much_was_sent(cur):
    event(cur, "upload_started", GONE * 2)
    event(cur, "upload_progress", GONE + JUST, sent_bytes=123_456_789)
    assert "123456789" in stopped(cur)[0]["detail"]


def test_a_stop_outside_this_window_is_someone_elses(cur):
    event(cur, "upload_started", GONE + timedelta(minutes=5))
    assert stopped(cur) == []


def test_a_project_the_customer_deleted_is_not_a_lost_upload(cur):
    event(cur, "upload_started", GONE + JUST, project=9999)
    assert stopped(cur) == []
