"""Ticket #11 on real PostgreSQL: which held request is waiting, when it
counts as started, the once-only stamp, what an MCP-only subscriber's
assistant is shown, and the intake's "paid but never ran" read.

Temp tables carry only the columns these statements read (CI:
QUEUE_TEST_DATABASE_URL; locally any throwaway database).
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
import psycopg2
from psycopg2.extras import Json, RealDictCursor

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

from ops_loop import intake                                 # noqa: E402
from video_services import held_request                      # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")

SCHEMA = """
SET TIME ZONE 'UTC';
CREATE TEMP TABLE users(id int PRIMARY KEY, email text, is_verified int DEFAULT 1,
  created_at timestamp DEFAULT '2026-10-01', is_subscribed int DEFAULT 0,
  plan text DEFAULT 'free');
CREATE TEMP TABLE projects(id int PRIMARY KEY, user_id int, chat_session_id int);
CREATE TEMP TABLE chat_messages(id int PRIMARY KEY, session_id int, role text,
  content text, meta jsonb, created_at timestamp DEFAULT NOW());
CREATE TEMP TABLE video_jobs(id serial PRIMARY KEY, user_id int, project_id int,
  type text, state text DEFAULT 'queued', payload jsonb,
  created_at timestamptz DEFAULT NOW());
CREATE TEMP TABLE payments(id serial PRIMARY KEY, user_id int, plan text,
  status text, amount_cents bigint, occurred_at timestamp);
"""
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


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


def user(cur, uid, plan="free", subscribed=False):
    cur.execute("INSERT INTO users (id, email, plan, is_subscribed) "
                "VALUES (%s, %s, %s, %s)",
                (uid, f"u{uid}@example.com", plan, int(subscribed)))
    cur.execute("INSERT INTO projects VALUES (%s, %s, %s)",
                (uid * 10, uid, uid * 100))
    return uid * 10, uid * 100


def msg(cur, mid, session, role, meta=None, ago_min=0, content="x"):
    cur.execute("INSERT INTO chat_messages VALUES (%s, %s, %s, %s, %s, %s)",
                (mid, session, role, content, Json(meta or {}),
                 NOW - timedelta(minutes=ago_min)))


def wall(cur, mid, session, ago_min=5, surface="blank_canvas", message_id=None,
         text="Record my website as a 20 s 9:16 demo", **extra):
    blocked = {"text": text}
    if message_id:
        blocked["message_id"] = message_id
    msg(cur, mid, session, "assistant",
        dict({"kind": "subscription_plans", "surface": surface,
              "blocked_prompt": blocked}, **extra), ago_min)


def subscribe(cur, uid, plan):
    cur.execute("UPDATE users SET plan = %s, is_subscribed = 1 WHERE id = %s",
                (plan, uid))


# ── what is waiting ────────────────────────────────────────────────────────

def test_the_newest_wall_that_is_still_the_last_message_is_waiting(cur):
    pid, sid = user(cur, 1)
    msg(cur, 1, sid, "user", ago_min=7)
    wall(cur, 2, sid, ago_min=7, message_id=1)
    msg(cur, 3, sid, "user", ago_min=6)
    wall(cur, 4, sid, ago_min=6, message_id=3)
    held = held_request.pending(cur, 1)
    assert held["id"] == 4 and held["project_id"] == pid
    assert held_request.pending(cur, 1, project_id=pid)["id"] == 4
    assert held_request.pending(cur, 1, project_id=999) is None


def test_nothing_waits_once_the_user_moved_on_or_it_ran_or_it_is_old(cur):
    _, sid = user(cur, 1)
    wall(cur, 2, sid, ago_min=5, surface="chat")
    # The analysis greeting of the walled upload the payment unlocked is not
    # the user moving on (users 2544, 2555, 2589 had one after their wall).
    msg(cur, 3, sid, "assistant", {"kind": "index_ready"}, ago_min=2)
    assert held_request.pending(cur, 1)["id"] == 2
    msg(cur, 4, sid, "user", ago_min=1)               # typed again
    assert held_request.pending(cur, 1) is None
    _, sid = user(cur, 2)
    wall(cur, 20, sid, surface="chat", replayed_at="x")
    assert held_request.pending(cur, 2) is None
    _, sid = user(cur, 3)
    wall(cur, 30, sid, ago_min=60 * 25, surface="chat")
    assert held_request.pending(cur, 3) is None
    assert held_request.pending(cur, 3, window_h=26)["id"] == 30
    _, sid = user(cur, 4)
    msg(cur, 40, sid, "assistant", {"kind": "subscription_plans",
                                    "blocked_prompt": {"text": "  "}})
    assert held_request.pending(cur, 4) is None


# ── exactly once ───────────────────────────────────────────────────────────

def test_the_stamp_lands_once(cur):
    _, sid = user(cur, 1)
    wall(cur, 2, sid)
    assert held_request._mark(cur, 2, "transaction.paid", 77, 1) is True
    assert held_request._mark(cur, 2, "studio", 78, 1) is False
    cur.execute("SELECT meta FROM chat_messages WHERE id = 2")
    meta = cur.fetchone()["meta"]
    assert meta["replay_job_id"] == 77 and meta["replay_origin"] == \
        "transaction.paid"
    assert held_request.pending(cur, 1) is None


def test_started_while_the_resumed_turn_is_live(cur):
    pid, sid = user(cur, 1)
    msg(cur, 1, sid, "user")
    wall(cur, 2, sid, message_id=1)
    assert held_request.started(cur, sid) is None
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'agent_turn', %s) RETURNING id",
                (pid, Json({"message_id": 1})))
    job = cur.fetchone()["id"]
    held_request._mark(cur, 2, "transaction.completed", job, 1)
    got = held_request.started(cur, sid)
    assert got["job_id"] == job and got["message_id"] == 1
    assert got["origin"] == "transaction.completed"
    cur.execute("UPDATE video_jobs SET state = 'done'")
    assert held_request.started(cur, sid) is None


def test_started_sees_the_studios_own_send(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, surface="chat")
    msg(cur, 3, sid, "user", {"client_msg_id": "replay-2"})
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, state, "
                "payload) VALUES (1, %s, 'agent_turn', 'running', %s)",
                (pid, Json({"message_id": 3})))
    got = held_request.started(cur, sid)
    assert got["message_id"] == 3 and got["origin"] == "studio"
    assert got["state"] == "running"


def test_a_message_waiting_for_its_upload_analysis_counts_as_started(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, surface="chat")
    msg(cur, 3, sid, "user", {"client_msg_id": "replay-2"})
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'index', '{}')", (pid,))
    got = held_request.started(cur, sid)
    assert got["waiting_for_index"] is True and got["message_id"] == 3


# ── an MCP-only plan: the customer's own assistant is told ─────────────────

def test_an_mcp_only_subscribers_assistant_sees_the_request(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid)
    assert held_request.for_assistant(cur, 1, pid) is None   # still walled
    subscribe(cur, 1, "plus_2026")
    note = held_request.for_assistant(cur, 1, pid)
    assert note["project_id"] == pid
    assert "Record my website as a 20 s 9:16 demo" in note["text"]
    assert note["text"].startswith("WAITING REQUEST FROM THE STUDIO")
    assert held_request.for_assistant(cur, 1)["project_id"] == pid
    # A state read is not the assistant taking over; an edit is.
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'mcp_tool', %s)",
                (pid, Json({"tool": "__state__"})))
    assert held_request.for_assistant(cur, 1, pid) is not None
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, 999, 'mcp_tool', %s)",
                (Json({"tool": "record_website_demo"}),))
    assert held_request.for_assistant(cur, 1, pid) is None


def test_a_studio_plan_runs_it_instead_so_the_assistant_is_not_told(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid)
    subscribe(cur, 1, "pro_2026")
    assert held_request.studio_lane_open(cur, 1) is True
    assert held_request.for_assistant(cur, 1, pid) is None
    subscribe(cur, 1, "mcp_connect")
    assert held_request.studio_lane_open(cur, 1) is False


# ── the intake: a paid customer whose held request never ran ───────────────

def _scan(cur, now):
    return intake._held_not_resumed(
        cur, now - timedelta(minutes=11), now,
        {"internal": [22, 60], "limit": 50})


def test_a_paid_studio_customer_whose_request_never_ran_is_a_p0(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, ago_min=9)
    subscribe(cur, 1, "pro_2026")
    for status in ("paid", "completed"):
        cur.execute("INSERT INTO payments (user_id, plan, status, "
                    "amount_cents, occurred_at) VALUES (1, 'pro_2026', %s, "
                    "3000, %s)", (status, NOW - timedelta(minutes=5)))
    now = NOW.replace(tzinfo=timezone.utc)
    got = _scan(cur, now)
    assert [f["ref"] for f in got] == ["held:2"]        # one per request
    assert got[0]["severity"] == "P0" and got[0]["project_id"] == pid
    assert got[0]["cause_key"] == "auto-held-request-not-resumed"
    # Inside the grace the webhook is still expected to run it.
    assert _scan(cur, now - timedelta(minutes=3)) == []
    held_request._mark(cur, 2, "transaction.completed", 5, 1)
    assert _scan(cur, now) == []


def test_mcp_only_plans_internal_accounts_and_later_walls_are_not_filed(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, ago_min=9)
    subscribe(cur, 1, "plus_2026")
    pid60, sid60 = user(cur, 60)
    wall(cur, 3, sid60, ago_min=9)
    subscribe(cur, 60, "pro_2026")
    pid3, sid3 = user(cur, 4)
    wall(cur, 4, sid3, ago_min=2)                       # after the payment
    subscribe(cur, 4, "pro_2026")
    for uid in (1, 60, 4):
        cur.execute("INSERT INTO payments (user_id, plan, status, "
                    "amount_cents, occurred_at) VALUES (%s, 'x', 'completed',"
                    " 1500, %s)", (uid, NOW - timedelta(minutes=5)))
    assert _scan(cur, NOW.replace(tzinfo=timezone.utc)) == []


# ── D1V: Studio in Plus can open a new Plus subscriber's lane later ────────

def _plus_lane_open(monkeypatch):
    """A NEW Plus subscriber while Studio in Plus is on (D1): the gate lets
    them into the Studio."""
    from routes import video
    monkeypatch.setattr(video, "_new_subscriber_studio", lambda *_: True)


def test_the_assistant_keeps_the_request_when_the_plus_lane_opens(cur,
                                                                  monkeypatch):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid)
    subscribe(cur, 1, "plus_2026")
    assert held_request.for_assistant(cur, 1, pid) is not None   # lane closed
    _plus_lane_open(monkeypatch)
    assert held_request.studio_lane_open(cur, 1) is True
    # The hand-off follows the plan: the note does not vanish with nobody
    # running the request.
    assert held_request.for_assistant(cur, 1, pid) is not None
    assert held_request.assistant_took_over(cur, 1, NOW - timedelta(minutes=5)) is False
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'mcp_tool', %s)",
                (pid, Json({"tool": "record_website_demo"})))
    assert held_request.assistant_took_over(cur, 1, NOW - timedelta(minutes=5)) is True
    assert held_request.for_assistant(cur, 1, pid) is None
    # Once the Studio ran it the note is gone too.
    pid2, sid2 = user(cur, 3)
    wall(cur, 30, sid2)
    subscribe(cur, 3, "plus_2026")
    held_request._mark(cur, 30, "studio", 9, 31)
    assert held_request.for_assistant(cur, 3, pid2) is None


def test_mcp_activity_never_holds_back_a_studio_plan(cur):
    pid, sid = user(cur, 1)
    wall(cur, 2, sid)
    subscribe(cur, 1, "pro_2026")
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'mcp_tool', %s)",
                (pid, Json({"tool": "record_website_demo"})))
    assert held_request.assistant_took_over(cur, 1, NOW - timedelta(minutes=5)) is False
    assert held_request.for_assistant(cur, 1, pid) is None


def test_the_intake_skips_a_plus_request_the_assistant_made(cur, monkeypatch):
    _plus_lane_open(monkeypatch)
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, ago_min=9)
    subscribe(cur, 1, "plus_2026")
    cur.execute("INSERT INTO payments (user_id, plan, status, amount_cents, "
                "occurred_at) VALUES (1, 'plus_2026', 'completed', 1500, %s)",
                (NOW - timedelta(minutes=5),))
    now = NOW.replace(tzinfo=timezone.utc)
    assert [f["ref"] for f in _scan(cur, now)] == ["held:2"]   # lane open
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload, "
                "created_at) VALUES (1, %s, 'mcp_tool', %s, %s)",
                (pid, Json({"tool": "record_website_demo"}),
                 now - timedelta(minutes=4)))
    assert _scan(cur, now) == []


def test_a_state_read_of_the_held_project_hands_it_to_the_assistant(cur):
    """open_project / project_state print the waiting request; once the
    assistant has been shown it, the Studio must not also run it (it may be
    asking the user to confirm). A state read elsewhere, or after the
    request's window, is not that."""
    pid, sid = user(cur, 1)
    wall(cur, 2, sid, ago_min=10)
    subscribe(cur, 1, "plus_2026")
    since = NOW - timedelta(minutes=10)
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, 999, 'mcp_tool', %s)", (Json({"tool": "__state__"}),))
    assert held_request.assistant_took_over(cur, 1, since, pid) is False
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload, "
                "created_at) VALUES (1, %s, 'mcp_tool', %s, %s)",
                (pid, Json({"tool": "__state__"}),
                 since + timedelta(hours=held_request.WINDOW_H, minutes=5)))
    assert held_request.assistant_took_over(cur, 1, since, pid) is False
    cur.execute("INSERT INTO video_jobs (user_id, project_id, type, payload) "
                "VALUES (1, %s, 'mcp_tool', %s)", (pid, Json({"tool": "__state__"})))
    assert held_request.assistant_took_over(cur, 1, since, pid) is True
    # ...and the note stays with the assistant until it acts
    assert held_request.for_assistant(cur, 1, pid) is not None
    subscribe(cur, 1, "pro_2026")
    assert held_request.assistant_took_over(cur, 1, since, pid) is False
