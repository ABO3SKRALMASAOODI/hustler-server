"""Why no ticket appeared (owner, 6 Nov 2026). The whole path on real PostgreSQL
with temp tables, the customers' own cases replayed through intake.run: a
failure lands on a ticket someone acts on, or on a ticket whose fix is proven,
and nothing is counted in silence.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
import psycopg2
from psycopg2.extras import Json, RealDictCursor

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

from ops_loop import intake, store                           # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations",
                         "041_outcome_loop.sql")
SCHEMA = """
SET TIME ZONE 'UTC';
CREATE TEMP TABLE users(id int PRIMARY KEY, email text, is_admin boolean,
  created_at timestamp DEFAULT '2026-11-01', is_subscribed int DEFAULT 0,
  billing_status text, trial_status text, plan text DEFAULT 'free',
  credits_balance numeric DEFAULT 0, credits_monthly_limit numeric);
CREATE TEMP TABLE projects(id int PRIMARY KEY, user_id int, chat_session_id int);
CREATE TEMP TABLE client_events(id serial PRIMARY KEY, user_id int,
  project_id int, kind text, asset_id int, detail jsonb, created_at timestamptz);
CREATE TEMP TABLE video_jobs(id serial PRIMARY KEY, project_id int, user_id int,
  type text, payload jsonb DEFAULT '{}', state text, progress int, error text,
  result jsonb, attempts int DEFAULT 0, heartbeat_at timestamptz,
  created_at timestamptz, updated_at timestamptz, total_claims int DEFAULT 0);
CREATE TEMP TABLE assets(id serial PRIMARY KEY, project_id int, kind text,
  bytes bigint, duration_s float, meta jsonb DEFAULT '{}', created_at timestamptz);
CREATE TEMP TABLE chat_messages(id serial PRIMARY KEY, session_id int, role text,
  content text, meta jsonb DEFAULT '{}', created_at timestamp);
CREATE TEMP TABLE app_kv(key text PRIMARY KEY, value text, updated_at timestamptz);
CREATE TEMP TABLE llm_calls(id serial PRIMARY KEY, project_id int, job_id int,
  purpose text, model text, response jsonb, created_at timestamptz);
CREATE TEMP TABLE payments(id serial PRIMARY KEY, user_id int, plan text,
  status text, amount_cents int, subscription_id text, occurred_at timestamp,
  created_at timestamp);
CREATE TEMP TABLE admin_subscription_history(subscription_id text,
  provider_updated_at timestamptz, status text, scheduled_action text,
  event_type text);
INSERT INTO users (id, email) VALUES (2837, 'free@example.com'),
  (2123, 'paying@example.com'), (2846, 'other@example.com');
UPDATE users SET is_subscribed = 1, billing_status = 'active' WHERE id = 2123;
INSERT INTO projects VALUES (5499, 2837, 1), (5498, 2123, 2), (5600, 2846, 3);
"""
T0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def tickets_schema():
    sql = open(MIGRATION, encoding="utf-8").read()
    sql = sql.replace("CREATE TABLE IF NOT EXISTS",
                      "CREATE TEMP TABLE IF NOT EXISTS")
    return "\n".join(ln for ln in sql.splitlines()
                     if ln.strip() not in ("BEGIN;", "COMMIT;")
                     and not ln.strip().startswith("SET LOCAL"))


class Clock:
    def __init__(self):
        self.now = T0


@pytest.fixture
def world(monkeypatch):
    conn = psycopg2.connect(URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    c = conn.cursor()
    c.execute(SCHEMA)
    c.execute(tickets_schema())
    clock = Clock()
    monkeypatch.setattr(intake, "_customers", lambda: "TRUE")
    monkeypatch.setattr(intake, "_tables_ready", lambda cur: True)
    monkeypatch.setattr(store, "_now", lambda: clock.now)
    # These P0s open after the owner's state emails were switched on.
    import cockpit_alerts
    monkeypatch.setattr(cockpit_alerts, "STATE_MAIL_FROM", T0 - timedelta(days=30))
    # The first pass starts from here, so earlier tests' windows do not apply.
    store.put_state(c, intake.WATERMARK_KEY, {"at": T0.isoformat()})
    yield World(conn, c, clock)
    conn.rollback()
    conn.close()


class World:
    def __init__(self, conn, cur, clock):
        self.conn, self.c, self.clock = conn, cur, clock

    def tick(self, at=None):
        self.clock.now = at or self.clock.now + MIN
        return intake.run(self.c, self.conn.cursor(), self.clock.now)

    def ticks(self, minutes):
        """A tick a minute, as the web process does."""
        out = []
        for _ in range(int(minutes)):
            out.append(self.tick())
        return out

    def event(self, event_kind, user=2837, project=5499, after=0, **detail):
        self.c.execute(
            """INSERT INTO client_events (user_id, project_id, kind, detail,
               created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user, project, event_kind, Json(detail),
             self.clock.now + timedelta(seconds=after)))
        return self.c.fetchone()["id"]

    def job(self, type_="index", state="done", project=5499, user=2837,
            age=0, result=None, payload=None, error=None):
        at = self.clock.now - timedelta(seconds=age)
        self.c.execute(
            """INSERT INTO video_jobs (project_id, user_id, type, state, error,
               result, payload, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (project, user, type_, state, error, Json(result or {}),
             Json(payload or {}), at, self.clock.now))
        return self.c.fetchone()["id"]

    def ticket(self, key):
        self.c.execute("SELECT * FROM tickets WHERE cause_key = %s", (key,))
        return self.c.fetchone()

    def only_ticket(self):
        self.c.execute("SELECT * FROM tickets ORDER BY id")
        rows = self.c.fetchall()
        assert len(rows) == 1, [r["cause_key"] for r in rows]
        return rows[0]

    def closed(self, key, status, reason=None, severity="P1", days=7):
        at = self.clock.now - timedelta(days=days)
        self.c.execute(
            """INSERT INTO tickets (cause_key, title, severity, status,
               wont_fix_reason, root_cause, fix_sha, regression_check,
               first_seen, last_seen, closed_at, occurrences, history)
               VALUES (%s, 'x', %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, '[]')""",
            (key, severity, status, reason,
             *(("disk", "abc1234", "t.py") if status == "fixed"
               else (None, None, None)), at, at, at))


LOST = "Your browser lost access to this file (it was moved, renamed, or " \
       "released by your phone). Select the same file again to continue — " \
       "everything already sent is kept."
LOST_KEY = intake.cause_key("upload-failed", intake.signature(LOST))


def lost_upload(w, user=2837, project=5499, bytes_=7_845_957_336, **kw):
    return w.event("upload_failed", user=user, project=project, reason=LOST,
                   bytes=bytes_, file_gone=True, resumable=True, offline=False,
                   **kw)


def events_of(t, name):
    return [h for h in t["history"] if h.get("event") == name]


# ── A closed ticket cannot hide what comes after it ──────────────────────

def test_the_lost_upload_on_a_closed_ticket_reopens_it(world):
    """#48, 6 Nov 01:44: account 2837's 7.85 GB upload landed on a wont_fix
    ticket closed "device-side" a week before. Only a counter moved."""
    world.closed(LOST_KEY, "wont_fix",
                 "device-side: the phone released IMG_5207.mov (28 MB)")
    lost_upload(world)
    out = world.tick()
    t = world.ticket(LOST_KEY)
    assert out["tickets"][0]["action"] == "reopened"
    assert t["status"] == "open" and t["recurrence_count"] == 1
    assert t["wont_fix_reason"] is None and t["closed_at"] is None
    assert events_of(t, "recurred")[0]["previous_status"] == "wont_fix"
    assert "device-side" in \
        events_of(t, "recurred")[0]["previous_wont_fix_reason"]


def test_a_blocked_request_after_123_closed_cannot_vanish(world):
    """#123 was closed for one customer's footage on a key that holds every
    blocked Studio request; the next customer's would have been counted."""
    key = "auto-request-blocked-replied"
    world.closed(key, "wont_fix", "content policy: one customer's footage",
                 severity="P0")
    world.job("agent_turn", project=5600, user=2846,
              result={"outcome": "blocked", "status": "replied"})
    world.tick()
    t = world.ticket(key)
    assert t["status"] == "open" and t["recurrence_count"] == 1
    # A new episode: it pages only if this customer is left stuck.
    assert t["severity"] == "P1"
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P0"


def test_an_owner_decision_stays_closed_and_is_reported(world):
    from ops_loop import views
    key = "auto-job-final-owner-cap"
    world.closed(key, "wont_fix", "Owner decision: finals over 4K are capped?")
    failure = {"ref": "job:1", "source": "job", "at": world.clock.now,
               "user_id": 2846, "project_id": 5600, "severity": "P0",
               "cause_key": key, "title": "final failed", "detail": "x"}
    t, action = store.record_failure(world.conn.cursor(), failure)
    assert action == "reported" and t["status"] == "wont_fix"
    assert t["occurrences"] == 2 and events_of(t, "reported")
    got = views.failures_between(world.c, T0 - timedelta(hours=1),
                                 T0 + timedelta(hours=1), T0)
    assert [x["id"] for x in got["held_for_owner"]] == [t["id"]]
    assert got["held_for_owner"][0]["failures"] == 1
    assert "finals over 4K" in got["held_for_owner"][0]["reason"]


def test_what_happened_before_the_closing_only_counts(world):
    key = "auto-job-index-oom"
    world.closed(key, "fixed", days=1)
    t, action = store.record_failure(world.conn.cursor(), {
        "ref": "job:9", "source": "job", "cause_key": key, "title": "x",
        "at": world.clock.now - timedelta(days=2), "user_id": 2846,
        "project_id": 5600, "severity": "P0", "detail": "late row"})
    assert action == "attached_to_closed" and t["status"] == "fixed"


def test_a_review_after_the_closing_reopens_a_wont_fix_too(world):
    key = "karaoke-highlight-box-misaligned"
    world.closed(key, "wont_fix", "Expected animation", severity="P2")
    world.c.execute(
        """INSERT INTO session_reviews (session_key, surface, user_id, project_id,
           started_at, settled_at, fingerprint, verdict, score, reasoning)
           VALUES ('p5600-1', 'studio', 2846, 5600, %s, %s, 'f',
                   'delivered_poor', 3, 'r') RETURNING id""",
        (world.clock.now, world.clock.now))
    t, action = store.open_or_attach(world.conn.cursor(), key, "x", "P2",
                                     session_key="p5600-1")
    assert action == "reopened" and t["status"] == "open"
    assert t["wont_fix_reason"] is None


def test_no_closed_ticket_is_left_only_counting(world):
    """The invariant: for every way a ticket can be closed, a later failure
    ends open or reported, never just counted."""
    for i, (status, reason) in enumerate((
            ("fixed", None), ("wont_fix", "device-side: x"),
            ("wont_fix", "noise"), ("wont_fix", "expected"),
            ("wont_fix", "owner decision: y"))):
        key = f"auto-case-{i}"
        world.closed(key, status, reason)
        t, action = store.record_failure(world.conn.cursor(), {
            "ref": f"e:{i}", "source": "job", "cause_key": key, "title": "x",
            "at": world.clock.now, "user_id": 2846, "project_id": 5600,
            "severity": "P1", "detail": "d"})
        assert action in ("reopened", "reported"), (status, reason, action)
        assert (t["status"] == "open") == (action == "reopened")


# ── wont_fix is an owner decision and nothing else ───────────────────────

@pytest.mark.parametrize("reason", [
    "device-side: the phone released the file", "expected animation", "noise",
    "the customer recovered alone", "content policy", "duplicate"])
def test_a_customer_failure_cannot_be_closed_as_wont_fix(world, reason):
    tid = failed_ticket(world, key="auto-x")
    with pytest.raises(store.StoreError, match="owner decision"):
        store.close(world.conn.cursor(), tid, "wont_fix", reason=reason)
    assert world.only_ticket()["status"] == "open"


def test_an_owner_decision_can_close(world):
    tid = failed_ticket(world, key="auto-x")
    t = store.close(world.conn.cursor(), tid, "wont_fix",
                    reason="owner decision: the Plus wall")
    assert t["status"] == "wont_fix" and t["closed_at"]


def test_a_closed_ticket_cannot_be_closed_again(world):
    """#88 went straight from wont_fix to fixed, and the record shows no episode
    for the failure that landed in between."""
    world.closed("auto-x", "wont_fix", "owner decision: y")
    with pytest.raises(store.StoreError, match="reopen it first"):
        store.close(world.conn.cursor(), world.only_ticket()["id"], "fixed",
                    root_cause="r", fix_sha="abc1234", regression_check="t")


# ── A ticket closes with a replay on a copy reaching the bar ─────────────

def proof(**kw):
    return dict({"run": "r1", "session": "p5499", "state": "done",
                 "failures": [], "fixer": "fixer-1"}, **kw)


def failed_ticket(world, key="auto-job-index-oom", project=5499):
    t, _ = store.record_failure(world.conn.cursor(), {
        "ref": "job:1", "source": "job", "at": world.clock.now, "cause_key": key,
        "user_id": 2837, "project_id": project, "severity": "P0", "title": "x",
        "detail": "d"})
    return t["id"]


def close_fixed(world, tid, replay):
    return store.close(world.conn.cursor(), tid, "fixed", root_cause="r",
                       fix_sha="abc1234", regression_check="tests/t.py::x",
                       replay=replay)


def test_fixed_needs_a_replay_of_that_request(world):
    tid = failed_ticket(world)
    with pytest.raises(store.StoreError, match="replay of that request"):
        close_fixed(world, tid, None)
    with pytest.raises(store.StoreError, match="not a session of this ticket"):
        close_fixed(world, tid, proof(session="p777"))
    with pytest.raises(store.StoreError, match="did not finish"):
        close_fixed(world, tid, proof(state="running"))
    with pytest.raises(store.StoreError, match="job_failed"):
        close_fixed(world, tid, proof(failures=["job_failed"]))
    t = close_fixed(world, tid, proof())
    assert t["status"] == "fixed"
    assert events_of(t, "fixed")[0]["replay"]["session"] == "p5499"


def quality_ticket(world):
    world.c.execute(
        """INSERT INTO session_reviews (session_key, surface, user_id, project_id,
           started_at, settled_at, fingerprint, verdict, score, reasoning)
           VALUES ('p5499-1', 'studio', 2837, 5499, %s, %s, 'f',
                   'delivered_poor', 2, 'r')""",
        (world.clock.now, world.clock.now))
    t, _ = store.open_or_attach(world.conn.cursor(), "first-cut-is-generic",
                                "x", "P1", session_key="p5499-1")
    return t["id"]


@pytest.mark.parametrize("key", ["auto-gap-first-cut-is-the-upload",
                                 "auto-gap-request-no-change-fulfilled"])
def test_a_result_that_changed_nothing_closes_on_the_score(key):
    """Neither raised an error, so "the replay finished with no failure" is what
    they already did for the customer."""
    assert store.is_quality({"cause_key": key})
    assert not store.is_quality({"cause_key": "auto-job-index-oom"})


def test_a_quality_ticket_closes_at_10_rated_by_someone_else(world):
    tid = quality_ticket(world)
    with pytest.raises(store.StoreError, match="no rating"):
        close_fixed(world, tid, proof())
    with pytest.raises(store.StoreError, match="below the bar"):
        close_fixed(world, tid, proof(score_exact=9.5, rubric="v3"))
    with pytest.raises(store.StoreError, match="Opus"):
        close_fixed(world, tid, proof(score_exact=10, rubric="v3"))
    with pytest.raises(store.StoreError, match="never rates its own"):
        close_fixed(world, tid, proof(score_exact=10, rubric="v3",
                                      rated_by="claude-opus-a",
                                      fixer="claude-opus-a"))
    t = close_fixed(world, tid, proof(score_exact=10, rubric="v3",
                                      rated_by="claude-opus-a"))
    assert t["status"] == "fixed"


# ── Severity is what happened to the customer ────────────────────────────

def test_a_free_customer_left_stuck_pages_like_a_paying_one(world):
    """2837 is free. It sat on P1 with nobody coming."""
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    t = world.only_ticket()
    assert t["status"] == "open" and t["severity"] == "P1", "filed within a minute"
    world.ticks(8)
    assert world.only_ticket()["severity"] == "P1", "the customer has a moment"
    world.ticks(3)
    t = world.only_ticket()
    assert t["severity"] == "P0"
    assert events_of(t, "escalated")[0]["why"] == "left_blocked"
    assert [r["id"] for r in intake.pending_p0(world.c, world.clock.now)] == \
        [t["id"]]


def test_a_paying_customer_is_judged_the_same(world):
    lost_upload(world, user=2123, project=5498, bytes_=20_000_000)
    world.tick()
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_a_customer_who_recovers_alone_pages_nobody_and_the_ticket_stays(world):
    """2701 landed the same file 2 minutes later, 2709 in 5."""
    lost_upload(world, bytes_=28_000_000)
    world.tick()
    world.ticks(2)
    world.event("upload_landed", kind="original", bytes=28_000_000)
    world.ticks(15)
    t = world.only_ticket()
    assert t["severity"] == "P1" and t["status"] == "open"
    assert events_of(t, "recovered") and not events_of(t, "escalated")
    assert intake.pending_p0(world.c, world.clock.now) == []


def test_a_customer_trying_again_is_not_stuck_yet(world):
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.event("upload_resumed", bytes=20_000_000, bytes_done=10_000_000)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P1"


def test_an_offline_customer_gets_longer(world):
    world.event("upload_failed", reason="connection lost", bytes=1000,
                offline=True)
    world.tick()
    key = world.only_ticket()["cause_key"]
    world.ticks(20)
    assert world.ticket(key)["severity"] == "P1"
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P0"


def test_a_song_landing_does_not_bring_back_a_refused_video(world):
    """Account 2649: 22 refused videos read `recovered` because one song landed
    in the project a minute later. It never got a video in."""
    world.event("upload_rejected", reason="empty or incomplete file", bytes=0)
    world.tick()
    world.ticks(2)
    world.event("upload_landed", kind="music", bytes=215_279)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_another_file_does_not_drop_the_look_at_a_lost_upload(world):
    """The 7.8 GB film is still not in. A small clip in another project starting,
    or landing, says nothing about it, and `trying` ended the look for good."""
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.event("upload_started", project=5600, kind="original", bytes=4_000)
    world.tick()
    world.event("upload_landed", project=5600, kind="original", bytes=4_000)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_the_same_file_in_a_new_project_is_past_it(world):
    """Account 2701 started again in a new project with the same file."""
    lost_upload(world, bytes_=42_971_432)
    world.tick()
    world.ticks(2)
    world.event("upload_landed", project=5600, kind="original", bytes=42_971_432)
    world.ticks(15)
    t = world.only_ticket()
    assert t["severity"] == "P1" and events_of(t, "recovered")


def test_a_smaller_copy_in_the_same_project_is_past_it(world):
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.event("upload_landed", kind="original", bytes=9_000_000)
    world.ticks(15)
    t = world.only_ticket()
    assert t["severity"] == "P1" and events_of(t, "recovered")


def test_a_failure_that_names_no_file_takes_any_video(world):
    """A proxy failure carries no size: 2701 again, past it in a new project."""
    world.event("upload_proxy_failed", reason="error in input stream")
    world.tick()
    world.ticks(2)
    world.event("upload_landed", project=5600, kind="original", bytes=42_971_432)
    world.ticks(11)
    t = world.only_ticket()
    assert t["severity"] == "P1" and events_of(t, "recovered")


def test_a_stop_is_not_hidden_by_another_file_in_the_project(world):
    """A song landing, or another file failing, is no news of this transfer."""
    world.event("upload_started", kind="original", bytes=500_000)
    world.ticks(2)
    world.event("upload_landed", kind="music", bytes=215_279)
    world.event("upload_failed", reason="network", bytes=9_999, kind="clip")
    world.ticks(16)
    assert world.ticket(intake.cause_key("upload-stopped", "no-signal"))


def test_a_stop_ends_with_a_row_about_its_own_file(world):
    world.event("upload_started", kind="original", bytes=500_000)
    world.ticks(2)
    world.event("upload_original_ready", bytes=500_000)
    world.ticks(16)
    assert not world.ticket(intake.cause_key("upload-stopped", "no-signal"))


def test_a_cut_short_file_left_stuck_pages_like_any_customer(world):
    """Severity read the error text: "moov atom not found" was P1 for good, and
    the owner saw account 2123's three failed jobs with no agent on them."""
    for _ in range(3):
        world.job("index", state="failed", project=5498, user=2123,
                  error="ffprobe failed: moov atom not found | a.mp4")
    world.tick()
    assert world.only_ticket()["severity"] == "P1", "on a ticket within a minute"
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_a_cut_short_file_replaced_pages_nobody(world):
    world.job("index", state="failed", project=5498, user=2123,
              error="ffprobe failed: moov atom not found | a.mp4")
    world.tick()
    world.ticks(3)
    world.job("index", state="done", project=5498, user=2123)
    world.ticks(11)
    t = world.only_ticket()
    assert t["severity"] == "P1" and t["status"] == "open"


def test_a_refused_unfinished_file_left_stuck_pages(world):
    world.event("upload_rejected", reason="unfinished file", device_side=True,
                bytes=15_728_640)
    world.tick()
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(40)
    assert world.only_ticket()["severity"] == "P0"


def test_an_unclassified_mcp_error_is_judged_by_what_happened(world):
    world.event("mcp_error_response", project=None, user=2123, tool="export_final",
                category="unclassified")
    world.tick()
    key = "auto-mcp-export-final-unclassified"
    assert world.ticket(key)["severity"] == "P1"
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P0"


def test_an_unclassified_mcp_error_the_caller_corrected_pages_nobody(world):
    world.event("mcp_error_response", project=None, user=2123, tool="export_final",
                category="unclassified")
    world.tick()
    world.ticks(2)
    world.job("mcp_tool", state="done", project=5498, user=2123,
              payload={"tool": "export_final"}, result={})
    world.ticks(11)
    assert world.ticket("auto-mcp-export-final-unclassified")["severity"] == "P1"


def test_a_customer_stuck_when_the_ticket_closed_is_not_lost(world):
    """The Fixer closes inside the grace: the look used to write `left_blocked`
    on a closed ticket, the one thing that knew, and nobody read it."""
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.c.execute("""UPDATE tickets SET status = 'fixed', root_cause = 'x',
                       fix_sha = 'abc1234', regression_check = 't.py',
                       closed_at = %s""", (world.clock.now,))
    world.ticks(11)
    t = world.only_ticket()
    assert t["status"] == "open" and t["severity"] == "P0"
    assert t["recurrence_count"] == 1 and t["fix_sha"] is None
    assert events_of(t, "recurred")[0]["why"] == "left_blocked"
    assert events_of(t, "recurred")[0]["previous_fix_sha"] == "abc1234"


def test_a_customer_who_got_past_it_leaves_the_closing_alone(world):
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.c.execute("""UPDATE tickets SET status = 'fixed', root_cause = 'x',
                       fix_sha = 'abc1234', regression_check = 't.py',
                       closed_at = %s""", (world.clock.now,))
    world.event("upload_landed", kind="original", bytes=20_000_000)
    world.ticks(11)
    assert world.only_ticket()["status"] == "fixed"


def test_a_stop_filed_behind_a_closing_still_gets_its_look(world):
    """A stop is filed 15 minutes after its last sign of life, so a closing in
    between put it `attached_to_closed`: a counter moved, as on #48."""
    key = intake.cause_key("upload-stopped", "no-signal")
    world.event("upload_started", kind="original", bytes=500_000)
    world.tick()
    world.closed(key, "fixed", days=0)
    world.c.execute("UPDATE tickets SET closed_at = %s", (T0 + 5 * MIN,))
    world.ticks(13)
    assert world.ticket(key)["status"] == "fixed", "nothing filed yet"
    world.ticks(3)
    t = world.ticket(key)
    assert t["status"] == "open" and t["severity"] == "P0"
    assert events_of(t, "recurred")[0]["why"] == "left_blocked"


def test_a_stuck_customer_on_an_owner_decision_stays_reported(world):
    lost_upload(world, bytes_=20_000_000)
    world.tick()
    world.ticks(2)
    world.c.execute("""UPDATE tickets SET status = 'wont_fix', closed_at = %s,
                       wont_fix_reason = 'owner decision: the size cap'""",
                    (world.clock.now,))
    world.ticks(11)
    t = world.only_ticket()
    assert t["status"] == "wont_fix" and events_of(t, "left_blocked")


def test_a_second_look_that_cannot_be_made_counts_as_stuck(world, monkeypatch):
    from ops_loop import recovery
    lost_upload(world, bytes_=20_000_000)
    world.tick()

    def broken(cur, w, now):
        raise RuntimeError("no such column")
    monkeypatch.setitem(recovery.CHECKS, "upload", broken)
    world.ticks(10)
    assert world.only_ticket()["severity"] == "P1", "tried again first"
    world.ticks(6)
    t = world.only_ticket()
    assert t["severity"] == "P0" and "second look failed" in \
        events_of(t, "escalated")[0]["note"]


def test_a_big_upload_gets_the_time_its_file_needs(world):
    lost_upload(world, bytes_=1_000_000_000)        # about 24 min at p10 speed
    world.tick()
    world.ticks(20)
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(6)
    assert world.only_ticket()["severity"] == "P0"


def test_a_failed_job_retried_to_done_pages_nobody(world):
    world.job("preview", state="failed", error="box lost it", project=5600,
              user=2846)
    world.tick()
    world.ticks(3)
    world.job("preview", state="done", project=5600, user=2846)
    world.ticks(10)
    t = world.only_ticket()
    assert t["severity"] == "P1" and events_of(t, "recovered")


def test_a_request_that_stays_unfulfilled_pages(world):
    world.job("agent_turn", project=5600, user=2846,
              result={"outcome": "partial", "status": "replied"})
    world.tick()
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_a_player_error_is_judged_by_playback_not_by_the_plan(world):
    world.event("player_error", media_error=4,
                src_path="/valmera-media/originals/5/a.mkv")
    world.tick()
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(2)
    world.event("player_recovered", attempts=1)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P1"


def test_a_held_request_still_pages_at_once(world, monkeypatch):
    failure = {"ref": "held:1", "source": "held", "at": world.clock.now,
               "cause_key": "auto-held-request-not-resumed", "user_id": 2123,
               "project_id": 5498, "severity": "P0", "title": "x", "detail": "d"}
    monkeypatch.setattr(intake, "scan", lambda *a: [failure])
    world.tick()
    assert world.only_ticket()["severity"] == "P0"


# ── No flood ─────────────────────────────────────────────────────────────

def test_a_retry_loop_and_other_customers_are_one_ticket(world):
    for i in range(24):                 # u2649's 24 retries
        world.event("upload_rejected", reason="empty file", bytes=0, after=i)
    world.event("upload_rejected", user=2846, project=5600, reason="empty file",
                bytes=0)
    world.tick(T0 + timedelta(seconds=30))
    t = world.only_ticket()
    assert t["occurrences"] == 25 and t["severity"] == "P1"


def test_a_failure_seen_twice_is_counted_once_and_looked_at_once(world):
    lost_upload(world, bytes_=1000)
    world.ticks(14)
    t = world.only_ticket()
    assert t["occurrences"] == 1
    assert len(events_of(t, "escalated")) == 1
    state = json.loads(world.ticket and _kv(world))
    assert state["watching"] == {}


def _kv(world):
    world.c.execute("SELECT value FROM app_kv WHERE key = %s",
                    (intake.WATERMARK_KEY,))
    return world.c.fetchone()["value"]


def test_a_repeating_cause_of_recovered_customers_stays_open_until_fixed(world):
    for n in range(3):
        lost_upload(world, bytes_=1000 + n)
        world.tick()
        world.event("upload_landed", kind="original", bytes=1000 + n)
        world.ticks(12)
    t = world.only_ticket()
    assert t["status"] == "open" and t["severity"] == "P1"
    assert t["occurrences"] == 3 and len(events_of(t, "recovered")) == 3


def test_the_second_look_survives_a_restart(world):
    lost_upload(world, bytes_=1000)
    world.tick()
    assert "event:" in _kv(world) and "watching" in _kv(world)


# ── Stopped uploads, on top of intake._stopped_uploads ───────────────────

def test_the_silent_upload_is_on_a_ticket_and_then_pages(world):
    """2837 at 01:48: upload_started and nothing else, ever."""
    world.event("upload_started", kind="original", bytes=2_000_000,
                filename="a.mp4")
    world.ticks(intake.UPLOAD_SILENT_AFTER.total_seconds() // 60 + 1)
    # 15 minutes of nothing is already past a small file's deadline.
    t = world.ticket("auto-upload-stopped-no-signal")
    assert t["severity"] == "P0" and t["status"] == "open"
    assert events_of(t, "escalated")[0]["why"] == "left_blocked"


def test_a_big_upload_with_no_heartbeat_is_not_paged_while_it_may_be_sending(world):
    """An older tab sends no heartbeat. 2 GB takes about 50 min at p10 speed; the
    back-test's margin keeps it from paging, and if it lands, nobody was."""
    world.event("upload_started", kind="original", bytes=2_000_000_000)
    world.ticks(40)
    t = world.ticket("auto-upload-stopped-no-signal")
    assert t["severity"] == "P1", "seen, filed, not paged"
    world.event("upload_landed", kind="original", bytes=2_000_000_000)
    world.ticks(60)
    t = world.ticket("auto-upload-stopped-no-signal")
    assert t["severity"] == "P1" and events_of(t, "recovered")


# ── An agent's stall is not a customer's silence ─────────────────────────

def p0(world, status="open", key="auto-x"):
    tid = failed_ticket(world, key=key)
    if status == "fixing":
        store.set_fixing(world.conn.cursor(), tid, "on it")
    return tid


def test_a_new_failure_does_not_make_a_stuck_claim_look_alive(world):
    """#77 sat 'fixing' for 22 h and took 5 failures, each resetting the only
    clock that could have noticed."""
    tid = p0(world, "fixing")
    world.clock.now += timedelta(hours=3)
    for i in range(5):
        store.record_failure(world.conn.cursor(), {
            "ref": f"job:{10 + i}", "source": "job", "at": world.clock.now,
            "cause_key": "auto-x", "user_id": 2846, "project_id": 5600,
            "severity": "P0", "title": "x", "detail": "d"})
        world.clock.now += timedelta(hours=1)
    world.c.execute("UPDATE tickets SET updated_at = %s", (world.clock.now,))
    assert [r["id"] for r in intake.pending_p0(world.c, world.clock.now)] == [tid]


def test_a_stuck_fixing_p0_reaches_the_owner(world):
    tid = p0(world, "fixing")
    assert intake.unhandled(world.c, world.clock.now) == []
    world.clock.now += timedelta(hours=1)
    store.record_failure(world.conn.cursor(), {
        "ref": "job:3", "source": "job", "at": world.clock.now, "user_id": 2846,
        "cause_key": "auto-x", "project_id": 5600, "severity": "P0",
        "title": "x", "detail": "d"})
    world.clock.now += timedelta(hours=5, minutes=1)
    got = intake.unhandled(world.c, world.clock.now)
    assert [t["id"] for t in got] == [tid] and got[0]["waiting_min"] == 361


def test_a_stale_claim_nobody_failed_on_since_is_the_watchs_not_the_owners(world):
    """The owner's test (7 Nov): email only when a customer is still stuck and
    no Fixer claimed or released the ticket since that customer's failure."""
    tid = p0(world, "fixing")
    world.clock.now += timedelta(hours=6, minutes=1)
    assert [r["id"] for r in intake.pending_p0(world.c, world.clock.now)] == [tid]
    assert intake.unhandled(world.c, world.clock.now) == []


def test_a_p0_released_with_its_fix_live_is_no_customer_blocked_email(world):
    """#387 (and #386, #861), 7 Nov: claimed, fix live, released at 07:30 to
    wait on the replay. Nobody failed on it after; at 13:40, 10 minutes after
    the 6 h quiet ended, the owner was emailed "Customer blocked, not handled
    yet". A new failure after the release is still an email at once."""
    tid = p0(world, "fixing")
    store.mark_live(world.conn.cursor(), tid, "a" * 40)
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid,
                         t["updated_at"].isoformat(), "live, waiting on replay")
    for hours in (1, 6, 7, 30):
        later = world.clock.now + timedelta(hours=hours)
        assert intake.unhandled(world.c, later) == [], hours
    world.clock.now += timedelta(hours=7)
    assert [r["id"] for r in intake.pending_p0(world.c, world.clock.now)] == [tid]
    store.record_failure(world.conn.cursor(), {
        "ref": "job:9", "source": "job", "at": world.clock.now, "user_id": 2869,
        "cause_key": "auto-x", "project_id": 5543, "severity": "P0",
        "title": "x", "detail": "d"})
    assert intake.unhandled(world.c, world.clock.now + timedelta(minutes=9)) == []
    got = intake.unhandled(world.c, world.clock.now + intake.UNHANDLED_AFTER)
    assert [t["id"] for t in got] == [tid]
    assert got[0]["opened_at"] == world.clock.now
    # A Fixer claiming it again answers that failure.
    store.set_fixing(world.conn.cursor(), tid, "on it again")
    assert intake.unhandled(world.c, world.clock.now + timedelta(hours=1)) == []


def test_every_p0_has_a_state_for_the_owner_read_from_the_table(world):
    """Owner, 7 Nov: every P0 is mailed with whether it was handled. The
    states come from the real tickets table and the store's own events."""
    tid = p0(world)
    assert intake.p0_states(world.c, world.clock.now + MIN)[0]["state"] is None
    got = intake.p0_states(world.c, world.clock.now + intake.UNHANDLED_AFTER)
    assert [(s["id"], s["state"]) for s in got] == [(tid, "NOT HANDLED")]
    world.clock.now += 12 * MIN
    store.set_fixing(world.conn.cursor(), tid, "on it")
    got = intake.p0_states(world.c, world.clock.now)
    assert got[0]["state"] == "BEING FIXED"
    assert got[0]["claimed_at"] == world.clock.now
    world.clock.now += 30 * MIN
    store.mark_live(world.conn.cursor(), tid, "b" * 40)
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid,
                         t["updated_at"].isoformat(), "live, waiting on replay")
    got = intake.p0_states(world.c, world.clock.now + timedelta(hours=7))
    assert got[0]["state"] == "FIX LIVE" and got[0]["variant"] == "b" * 12
    assert got[0]["replay_owed"] and not got[0]["stuck_now"]
    world.clock.now += timedelta(hours=1)
    store.record_failure(world.conn.cursor(), {
        "ref": "job:9", "source": "job", "at": world.clock.now, "user_id": 2846,
        "cause_key": "auto-x", "project_id": 5600, "severity": "P0",
        "title": "x", "detail": "d"})
    got = intake.p0_states(world.c, world.clock.now + intake.UNHANDLED_AFTER)
    # A fix shipped, a customer failed after it, nobody holds it (7 Nov).
    assert got[0]["state"] == "STILL STUCK"
    assert got[0]["failed_after_fix_at"] == world.clock.now


def test_a_release_flagged_waiting_on_customer_is_its_own_state(world):
    """#883 (7 Nov): a Fixer handed back an upload the customer left mid-way
    and the owner read STILL STUCK. The release CLI's explicit field, never
    the note's words, makes it WAITING ON CUSTOMER; without it, NOT HANDLED."""
    tid = p0(world)
    world.clock.now += 9 * MIN
    store.set_fixing(world.conn.cursor(), tid, "on it")
    world.clock.now += 4 * MIN
    t = world.only_ticket()
    with pytest.raises(store.StoreError):
        store.release_fixing(world.conn.cursor(), tid,
                             t["updated_at"].isoformat(), "left",
                             waiting_on_customer="  ")
    store.release_fixing(world.conn.cursor(), tid, t["updated_at"].isoformat(),
                         "customer left mid-upload at part 43 of 200",
                         waiting_on_customer="resumes from part 44 on re-pick")
    t = world.only_ticket()
    assert t["history"][-1]["waiting_on_customer"] == \
        "resumes from part 44 on re-pick"
    got = intake.p0_states(world.c, world.clock.now)
    assert got[0]["state"] == "WAITING ON CUSTOMER"
    assert got[0]["waiting_on_customer"] == "resumes from part 44 on re-pick"
    world.clock.now += MIN
    store.set_fixing(world.conn.cursor(), tid, "again")
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid, t["updated_at"].isoformat(),
                         "nothing more until they return")
    got = intake.p0_states(world.c, world.clock.now)
    assert got[0]["state"] == "NOT HANDLED" and got[0]["handed_back"]
    # A plain hand-back waits RELEASE_QUIET for the watch ...
    assert intake.pending_p0(world.c, world.clock.now + MIN) == []


def test_a_failed_proof_is_taken_again_at_once_and_mailed_not_fixed(world):
    """Owner, 7 Nov: "you must be sure that it's fixed". release
    --verify-failed records 'verify_failed' and skips the 6 h quiet."""
    tid = p0(world)
    store.set_fixing(world.conn.cursor(), tid, "on it")
    world.clock.now += 20 * MIN
    store.mark_live(world.conn.cursor(), tid, "c" * 40)
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid, t["updated_at"].isoformat(),
                         "live, VERIFY next")
    assert intake.pending_p0(world.c, world.clock.now + MIN) == []
    world.clock.now += 30 * MIN
    store.set_fixing(world.conn.cursor(), tid, "VERIFY: replaying on a copy")
    t = world.only_ticket()
    with pytest.raises(store.StoreError):
        store.release_fixing(world.conn.cursor(), tid,
                             t["updated_at"].isoformat(), "x",
                             waiting_on_customer="y", verify_failed=True)
    store.release_fixing(world.conn.cursor(), tid, t["updated_at"].isoformat(),
                         "the replay still ends partial", verify_failed=True)
    t = world.only_ticket()
    assert t["status"] == "open" and t["history"][-1]["event"] == "verify_failed"
    assert [r["id"] for r in intake.pending_p0(world.c,
                                               world.clock.now + MIN)] == [tid]
    got = intake.p0_states(world.c, world.clock.now + MIN)
    assert got[0]["state"] == "NOT FIXED" and got[0]["quiet_until"] is None


def test_the_alert_gives_the_5_minute_watch_its_turn_and_no_more(world):
    tid = p0(world)
    assert intake.UNHANDLED_AFTER == timedelta(minutes=10)
    assert intake.unhandled(world.c, world.clock.now + timedelta(minutes=9)) == []
    assert [t["id"] for t in intake.unhandled(
        world.c, world.clock.now + intake.UNHANDLED_AFTER)] == [tid]


def test_a_p0_handed_back_waits_quietly_until_a_new_failure_lands(world):
    """A strict close means a P0 can wait on a replay or a rating. Starting a
    Fixer on it every 30 minutes would burn the weekly limit that stopped every
    agent for 70 hours; a customer hit by it ends the quiet at once."""
    tid = p0(world, "fixing")
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid,
                         t["updated_at"].isoformat(), "waiting on the replay")
    world.clock.now += timedelta(hours=1)
    assert intake.pending_p0(world.c, world.clock.now) == []
    assert intake.unhandled(world.c, world.clock.now) == []
    store.record_failure(world.conn.cursor(), {
        "ref": "job:2", "source": "job", "at": world.clock.now, "user_id": 2846,
        "cause_key": "auto-x", "project_id": 5600, "severity": "P0",
        "title": "x", "detail": "d"})
    assert [r["id"] for r in intake.pending_p0(world.c, world.clock.now)] == [tid]


def test_a_quiet_ends_by_itself(world):
    tid = p0(world, "fixing")
    t = world.only_ticket()
    store.release_fixing(world.conn.cursor(), tid,
                         t["updated_at"].isoformat(), "waiting on the replay")
    later = world.clock.now + intake.RELEASE_QUIET + MIN
    assert [r["id"] for r in intake.pending_p0(world.c, later)] == [tid]
    # The watch takes it again; nobody failed on it since the release, so the
    # owner is not told a customer is blocked and unhandled.
    later += intake.UNHANDLED_AFTER
    assert intake.unhandled(world.c, later) == []


def test_a_stopped_agent_is_reported_and_a_never_armed_one_is_not(world):
    assert intake.silent_agents(world.c, world.clock.now) == []
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": world.clock.now.isoformat()})
    store.put_state(world.c, "ops_heartbeat_fixer",
                    {"at": (world.clock.now - timedelta(hours=27)).isoformat()})
    later = world.clock.now + timedelta(minutes=14)
    assert [a["agent"] for a in intake.silent_agents(world.c, later)] == ["fixer"]
    later = world.clock.now + timedelta(hours=2)
    assert {a["agent"] for a in intake.silent_agents(world.c, later)} == \
        {"p0_watch", "fixer", "intake"}


def test_an_intake_that_stops_passing_is_reported_within_minutes(world):
    """It writes its watermark every minute, so a pass that keeps raising leaves
    nothing behind. Until now only the next morning's digest said so."""
    assert intake.silent_agents(world.c, T0 + 14 * MIN) == []
    got = intake.silent_agents(world.c, T0 + 16 * MIN)
    assert [a["agent"] for a in got] == ["intake"] and got[0]["silent_min"] == 16


def test_reviews_that_stop_arriving_are_reported(world):
    world.c.execute(
        """INSERT INTO session_reviews (session_key, surface, user_id, project_id,
           started_at, settled_at, fingerprint, verdict, score, reasoning,
           reviewed_at) VALUES ('k', 'studio', 2846, 5600, %s, %s, 'f',
           'delivered_poor', 3, 'r', %s)""",
        (T0, T0, T0 - timedelta(hours=25)))
    assert [a["agent"] for a in intake.silent_agents(world.c, T0)] == \
        ["session reviews"]


def test_a_mute_sweep_reopens_only_what_is_not_the_owners(world):
    world.closed("auto-a", "wont_fix", "device-side: x", severity="P0")
    world.closed("auto-b", "wont_fix", "owner decision: y")
    world.c.execute("SELECT id FROM tickets WHERE cause_key = 'auto-a'")
    tid = world.c.fetchone()["id"]
    t = store.unmute(world.conn.cursor(), tid, "wont_fix is the owner's only")
    assert t["status"] == "open" and t["severity"] == "P1"
    assert store.is_owner_decision(world.ticket("auto-b"))
    assert not store.is_owner_decision(world.ticket("auto-a"))
    assert events_of(t, "reopened") and events_of(t, "lowered")


def test_nothing_about_payment_or_error_words_decides_severity():
    src = open(intake.__file__, encoding="utf-8").read()
    assert "DEVICE_SIDE" not in src and "paying" not in src
    assert not re.search(r"defs\.paying", src)
    # The error text names the cause and never ranks it.
    assert "device_side" not in src and src.count('"truncated-media"') == 1


# ── The CLI reads the proof, it does not take the closer's word ──────────

def cli():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import ops_review
    return ops_review


class Args:
    rated_by, fixer = "claude-opus-reviewer", "claude-opus-fixer"

    def __init__(self, replay):
        self.replay = replay


def test_the_replay_is_read_from_the_run_on_disk(tmp_path, monkeypatch):
    ops = cli()
    monkeypatch.setattr(ops, "GAUNTLET_RUNS", str(tmp_path))
    session = tmp_path / "r1" / "sessions" / "p5345"
    (session / "judge").mkdir(parents=True)
    (session / "journey.json").write_text(json.dumps({
        "run": "r1", "sid": "p5345", "state": "done", "be_sha": "abc",
        "failures": [{"kind": "download_event", "detail": "no download"}]}))
    (session / "judge" / "verdict3.json").write_text(json.dumps({
        "rubric": "v3", "score_exact": 10, "tier": "standard"}))
    got = ops.load_replay(Args("r1/p5345"))
    assert got["session"] == "p5345" and got["failures"] == ["download_event"]
    assert got["score_exact"] == 10 and got["rubric"] == "v3"
    assert got["rated_by"] == "claude-opus-reviewer"
    assert ops.load_replay(Args(None)) is None
    with pytest.raises(ops.CliError, match="cannot read the replay"):
        ops.load_replay(Args("r1/p1"))


class Git:
    def __init__(self, files, on_main=True):
        self.files, self.on_main, self.branch = files, on_main, "HEAD"

    def __call__(self, *args):
        ok = self.on_main or args[0] == "cat-file"
        out = self.branch if args[0] == "rev-parse" else "\n".join(self.files)
        return type("R", (), {"returncode": 0 if ok else 1, "stdout": out})


def test_a_fix_is_product_code_on_main(monkeypatch):
    """Six tickets cite 0bc9a5b9, which is the ops CLI; #12 cites a docstring."""
    ops = cli()
    monkeypatch.setattr(ops, "_git", Git(["backend/ops_loop/store.py",
                                          "backend/scripts/ops_review.py",
                                          "backend/tests/test_x.py"]))
    with pytest.raises(ops.CliError, match="not the product"):
        ops.check_fix_sha("0bc9a5b9")
    monkeypatch.setattr(ops, "_git", Git(["worker/agent_loop.py"], on_main=False))
    with pytest.raises(ops.CliError, match="not on main"):
        ops.check_fix_sha("33c8764f")
    monkeypatch.setattr(ops, "_git", Git(["worker/agent_loop.py",
                                          "worker/tests/test_x.py"]))
    ops.check_fix_sha("abc1234,fe:def5678")


@pytest.mark.parametrize("files", [[], ["AGENTS.md", "CLAUDE.md"],
                                   ["worker/CLOUDFLARE_EXECUTOR.md"]])
def test_an_empty_or_docs_only_commit_is_not_a_fix(monkeypatch, files):
    """8645c9a0 on this very branch changes only the project brief."""
    ops = cli()
    monkeypatch.setattr(ops, "_git", Git(files))
    with pytest.raises(ops.CliError, match="not the product"):
        ops.check_fix_sha("8645c9a0")


def test_a_skill_in_markdown_is_the_product(monkeypatch):
    ops = cli()
    monkeypatch.setattr(ops, "_git", Git(["plugins/valmera/skills/edit/SKILL.md"]))
    ops.check_fix_sha("abc1234")


def test_a_branch_checkout_cannot_prove_a_fix_is_on_main(monkeypatch):
    """ops.sh runs a branch with OPS_BACKEND, where HEAD holds the unmerged fix."""
    ops = cli()
    git = Git(["worker/agent_loop.py"])
    git.branch = "claude/fix-1106"
    monkeypatch.setattr(ops, "_git", git)
    with pytest.raises(ops.CliError, match="close without OPS_BACKEND"):
        ops.check_fix_sha("abc1234")


@pytest.mark.parametrize("field,value,says", [
    ("steps", [{"name": "request 1", "kind": "canned"}], "canned_reply"),
    ("prompts_capped", {"replayed": 2, "of": 9}, "prompts_capped"),
    ("budget_stopped", True, "budget_stopped")])
def test_a_replay_that_did_not_do_the_request_proves_nothing(tmp_path, monkeypatch,
                                                             field, value, says):
    """A journey can end `done` with no failure while the request reached no
    editor, or was never sent: the scoreboard counts these, so the gate does."""
    ops = cli()
    monkeypatch.setattr(ops, "GAUNTLET_RUNS", str(tmp_path))
    session = tmp_path / "r1" / "sessions" / "p5345"
    session.mkdir(parents=True)
    (session / "journey.json").write_text(json.dumps({
        "run": "r1", "sid": "p5345", "state": "done", "failures": [],
        field: value}))
    replay = ops.load_replay(Args("r1/p5345"))
    assert replay["failures"] == [says]


def test_the_digest_says_what_is_held_and_who_stopped():
    import cockpit_alerts
    now = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)
    sections = cockpit_alerts.digest_sections({"failures": {
        "total": 2, "customers": 1, "by_ticket": [], "fixed": [], "unhandled": [],
        "open": 1, "fixing": 0, "p0_active": 0, "intake_stale": False,
        "held_for_owner": [{"id": 3, "title": "Finals over 4K", "failures": 2,
                            "users": 1, "reason": "owner decision: the 4K cap",
                            "url": "https://valmera.io/admin?ticket=3"}],
        "silent_agents": [{"agent": "p0_watch", "last_at": now, "silent_min": 90}]}})
    text = " ".join(str(s) for s in sections)
    assert "Held on your decision" in text and "the 4K cap" in text
    assert "Not running" in text and "p0_watch" in text


def test_the_owner_hears_when_an_agent_stops_and_when_a_claim_goes_stale(monkeypatch):
    import cockpit_alerts
    now = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)
    claim, failed = now - timedelta(hours=7), now - timedelta(hours=2)
    row = {"id": 77, "title": "A request ended partial", "severity": "P0",
           "status": "fixing", "last_seen": failed, "history": [
               {"event": "opened", "at": (claim - 5 * MIN).isoformat(),
                "severity": "P0"},
               {"event": "failure", "at": (claim - 5 * MIN).isoformat(),
                "user": 1},
               {"event": "fixing", "at": claim.isoformat()},
               {"event": "failure", "at": failed.isoformat(), "user": 2},
               {"event": "failure", "at": failed.isoformat(), "user": 3}]}
    monkeypatch.setattr(intake, "p0_states", lambda cur, at: [
        dict(intake.p0_state(row, at), row=row)])
    monkeypatch.setattr(intake, "silent_agents", lambda cur, at: [{
        "agent": "p0_watch", "last_at": now, "silent_min": 125}])

    class C:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return {}

        def fetchall(self):
            return []
    got = cockpit_alerts.event_alerts(C(), now, now)
    text = " ".join(line for _k, _s, line in got)
    assert "the run has probably died" in text and "P0 #77" in text
    assert "3 customers" in text
    assert "p0_watch" in text and "2 h 5 min" in text
    assert got[0][1] == "Blocked: A request ended partial (P0 #77)"
    assert "wake the Mac" in got[0][2]
    assert [s for _k, s, _l in got][1:] == ["An automatic agent stopped"]


def test_a_tool_defect_is_closed_on_the_defect_not_the_films_score():
    from ops_loop import store as _store
    assert _store.is_quality({"cause_key": "tool:captions:plate-offset"}) is False
    assert _store.is_quality({"cause_key": "first-cut-title-covers-speaker-face"}) is True
