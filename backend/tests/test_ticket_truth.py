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
  billing_status text, trial_status text, plan text DEFAULT 'free');
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
    world.clock.now += timedelta(hours=6, minutes=1)
    got = intake.unhandled(world.c, world.clock.now)
    assert [t["id"] for t in got] == [tid] and got[0]["waiting_min"] == 361


def test_the_alert_gives_the_30_minute_watch_its_turn(world):
    tid = p0(world)
    assert intake.UNHANDLED_AFTER > timedelta(minutes=37)
    assert intake.unhandled(world.c, world.clock.now + timedelta(minutes=37)) == []
    assert [t["id"] for t in intake.unhandled(
        world.c, world.clock.now + intake.UNHANDLED_AFTER)] == [tid]


def test_a_stopped_agent_is_reported_and_a_never_armed_one_is_not(world):
    assert intake.silent_agents(world.c, world.clock.now) == []
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": world.clock.now.isoformat()})
    store.put_state(world.c, "ops_heartbeat_fixer",
                    {"at": (world.clock.now - timedelta(hours=27)).isoformat()})
    later = world.clock.now + timedelta(minutes=30)
    assert [a["agent"] for a in intake.silent_agents(world.c, later)] == ["fixer"]
    later = world.clock.now + timedelta(hours=2)
    assert {a["agent"] for a in intake.silent_agents(world.c, later)} == \
        {"p0_watch", "fixer"}


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
