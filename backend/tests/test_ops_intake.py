"""Automatic intake (29 Oct 2026): every new customer failure becomes a
ticket within a minute, one ticket per cause, and a fixed cause that fails
again reopens. No database: an in-memory tickets table answers the exact
statements ops_loop.store.record_failure sends."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_loop import intake, store                     # noqa: E402

NOW = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)


class TicketsDB:
    """The tickets table, enough for record_failure."""

    def __init__(self):
        self.rows, self.next_id, self.kv = {}, 1, {}

    def cursor(self):
        return TicketsCur(self)


class TicketsCur:
    def __init__(self, db):
        self.db, self._one = db, None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._one = None
        if flat.startswith("SAVEPOINT") or flat.startswith("RELEASE") or \
                flat.startswith("ROLLBACK"):
            return
        if flat.startswith("INSERT INTO tickets"):
            key, title, sev, summary, first, last, history = params
            if key in self.db.rows:
                return
            row = {"id": self.db.next_id, "cause_key": key, "title": title,
                   "severity": sev, "status": "open", "summary": summary,
                   "root_cause": None, "fix_sha": None,
                   "regression_check": None, "recurrence_count": 0,
                   "occurrences": 1, "first_seen": first, "last_seen": last,
                   "closed_at": None, "history": json.loads(history)}
            self.db.next_id += 1
            self.db.rows[key] = row
            self._one = dict(row)
        elif flat.startswith("SELECT * FROM tickets WHERE cause_key"):
            self._one = dict(self.db.rows[params[0]])
        elif flat.startswith("SELECT EXISTS"):
            ref, tid = params
            row = self._by_id(tid)
            self._one = {"seen": any(h.get("ref") == ref and
                                     h.get("event") == "failure"
                                     for h in row["history"])}
        elif flat.startswith("UPDATE tickets SET occurrences"):
            last, first, sev, summary, events, tid = params
            row = self._by_id(tid)
            row["occurrences"] += 1
            row["last_seen"] = max(row["last_seen"], last)
            row["first_seen"] = min(row["first_seen"], first)
            row["severity"] = sev
            if row["cause_key"].startswith("auto-"):
                row["summary"] = summary
            row["history"] += json.loads(events)
            if "status = 'open'" in flat:
                row.update(status="open", fix_sha=None,
                           regression_check=None, closed_at=None,
                           recurrence_count=row["recurrence_count"] + 1)
        elif flat.startswith("SELECT * FROM tickets WHERE id"):
            self._one = dict(self._by_id(params[0]))
        elif flat.startswith("INSERT INTO app_kv"):
            self.db.kv[params[0]] = params[1]
        else:
            raise AssertionError(f"unexpected statement: {flat[:90]}")

    def _by_id(self, tid):
        return next(r for r in self.db.rows.values() if r["id"] == tid)

    def fetchone(self):
        return self._one


def _f(ref, at, key="auto-job-index-oom", severity="P0", user=7, **kw):
    f = {"ref": ref, "source": "job", "at": at, "user_id": user,
         "project_id": 70, "severity": severity, "cause_key": key,
         "title": "index job failed: ffmpeg was killed (exit -9)",
         "detail": "ffmpeg was killed by the out-of-memory killer"}
    f.update(kw)
    return f


# ── One ticket per cause ─────────────────────────────────────────────────

def test_the_same_failure_with_other_ids_and_numbers_is_one_cause():
    a = ("this job needs about 15.5 GB of scratch space in "
         "/tmp/valmera/index_108884 and only 11.8 GB is free")
    b = ("this job needs about 9.1 GB of scratch space in "
         "/tmp/valmera/index_115549 and only 2.0 GB is free")
    assert intake.signature(a) == intake.signature(b) == "scratch-space"
    assert intake.cause_key("job", "index", intake.signature(a)) == \
        "auto-job-index-scratch-space"
    u1 = "texts[0]: end 7.48 exceeds the limit 7.47s."
    u2 = "texts[3]: end 12.01 exceeds the limit 11.9s."
    assert intake.signature(u1) == intake.signature(u2)
    assert intake.signature(u1) != intake.signature("caption font missing")
    for parts in (("job", "mcp_tool-export_final", "x" * 200),
                  ("upload-rejected", "über die Größe — 100%"),
                  ("mcp", "", "")):
        key = intake.cause_key(*parts)
        assert store.CAUSE_RE.match(key), key


def test_a_failure_opens_a_p0_and_a_retry_loop_folds_into_it():
    db = TicketsDB()
    t, action = store.record_failure(db.cursor(), _f("job:1", NOW))
    assert action == "opened" and t["severity"] == "P0" and \
        t["status"] == "open" and t["occurrences"] == 1
    assert t["history"][0]["auto"] is True
    # The same row seen again (the intake's overlap): a duplicate.
    _, action = store.record_failure(db.cursor(), _f("job:1", NOW))
    assert action == "duplicate"
    # The retry, and another customer: attached to the same ticket.
    t, action = store.record_failure(
        db.cursor(), _f("job:2", NOW + timedelta(minutes=1)))
    t, action = store.record_failure(
        db.cursor(), _f("job:3", NOW + timedelta(minutes=2), user=8))
    assert action == "attached" and t["occurrences"] == 3
    assert "3 failures from 2 customers" in t["summary"]
    assert len(db.rows) == 1


def test_a_fixed_cause_that_fails_again_after_the_fix_reopens():
    db = TicketsDB()
    store.record_failure(db.cursor(), _f("job:1", NOW))
    row = db.rows["auto-job-index-oom"]
    row.update(status="fixed", fix_sha="abc1234", root_cause="x",
               regression_check="tests/t.py", closed_at=NOW +
               timedelta(hours=2))
    # A failure from BEFORE the fix closed only counts.
    t, action = store.record_failure(
        db.cursor(), _f("job:2", NOW + timedelta(hours=1)))
    assert action == "attached_to_closed" and t["status"] == "fixed"
    # One after it reopens the cause, one level deeper for the Fixer.
    t, action = store.record_failure(
        db.cursor(), _f("job:3", NOW + timedelta(hours=3)))
    assert action == "reopened" and t["status"] == "open"
    assert t["recurrence_count"] == 1 and t["fix_sha"] is None
    recurred = [h for h in t["history"] if h["event"] == "recurred"]
    assert recurred[0]["previous_fix_sha"] == "abc1234"


def test_a_p0_failure_escalates_a_p1_cause_and_wont_fix_only_counts():
    db = TicketsDB()
    store.record_failure(db.cursor(), _f("event:1", NOW, key="auto-upload-x",
                                         severity="P1"))
    t, action = store.record_failure(
        db.cursor(), _f("event:2", NOW, key="auto-upload-x", severity="P0"))
    assert t["severity"] == "P0"
    assert any(h["event"] == "escalated" for h in t["history"])
    db.rows["auto-upload-x"].update(status="wont_fix",
                                    closed_at=NOW - timedelta(days=1))
    t, action = store.record_failure(
        db.cursor(), _f("event:3", NOW, key="auto-upload-x"))
    assert action == "attached_to_closed" and t["status"] == "wont_fix"


def test_history_keeps_a_bounded_number_of_refs():
    db = TicketsDB()
    for i in range(store.FAILURE_REFS_KEPT + 15):
        store.record_failure(db.cursor(), _f(f"job:{i}", NOW +
                                             timedelta(seconds=i)))
    row = db.rows["auto-job-index-oom"]
    assert row["occurrences"] == store.FAILURE_REFS_KEPT + 15
    assert sum(1 for h in row["history"] if h["event"] == "failure") == \
        store.FAILURE_REFS_KEPT


def test_the_intakes_only_app_kv_write_is_its_watermark():
    with pytest.raises(store.StoreError):
        store.guard_state("INSERT INTO app_kv (key) VALUES (%s)",
                          "cockpit_digest:2026-10-29")
    with pytest.raises(store.StoreError):
        store.guard_state("UPDATE users SET plan = 'x'",
                          intake.WATERMARK_KEY)
    assert store.guard_state("INSERT INTO app_kv (key) VALUES (%s)",
                             intake.WATERMARK_KEY)


# ── What counts as a customer failure ────────────────────────────────────

def _ev(kind, detail, paying=False):
    return {"id": 9, "kind": kind, "user_id": 7, "project_id": 70,
            "at": NOW, "detail": json.dumps(detail), "paying": paying}


def test_which_client_events_are_failures_and_how_severe():
    ev = intake._event_failure
    # Stored and analysed anyway: not a failure.
    assert ev(_ev("upload_rejected", {"reason": "over duration cap (accepted,"
                                      " first part analysed)",
                                      "accepted": True})) is None
    # Our own refusal blocks the customer: P0.
    got = ev(_ev("upload_rejected", {"reason": "over size cap",
                                     "bytes": 109805883163}))
    assert got["severity"] == "P0" and \
        got["cause_key"] == "auto-upload-rejected-over-size-cap"
    # A cloud placeholder with no bytes on the device: P1 for a free account,
    # P0 for a paying one.
    empty = {"reason": "empty or incomplete file", "bytes": 0}
    assert ev(_ev("upload_rejected", empty))["severity"] == "P1"
    assert ev(_ev("upload_rejected", empty, paying=True))["severity"] == "P0"
    # Ticket #12: the Studio now READS a file that says 0 bytes before it
    # refuses it; a proven-empty or unreadable file is device-side too.
    for reason in ("empty file", "unreadable file on this device"):
        got = ev(_ev("upload_rejected", {"reason": reason, "bytes": 0,
                                         "proven": True}))
        assert got["severity"] == "P1", reason
        assert ev(_ev("upload_rejected", {"reason": reason},
                      paying=True))["severity"] == "P0"
    # The browser cannot decode a gated free upload (no server proxy yet).
    player = {"media_error": 4, "src_path": "/valmera-media/originals/1/a.mp4",
              "is_proxy": True}
    assert ev(_ev("player_error", player))["severity"] == "P1"
    got = ev(_ev("player_error", player, paying=True))
    assert got["severity"] == "P0" and \
        got["cause_key"] == "auto-player-error-code4-mp4"
    # MCP: the AI app's corrections are not our failures; ours are P0 and
    # carry the sentence.
    assert ev(_ev("mcp_error_response", {
        "tool": "set_junction", "category": "invalid_request",
        "outcome": "correction_needed"})) is None
    assert ev(_ev("mcp_error_response", {
        "tool": "list_assets", "category": "upload_error",
        "outcome": "prerequisite"})) is None
    got = ev(_ev("mcp_error_response", {
        "tool": "export_final", "category": "unclassified",
        "message": "export_final could not start the render"}))
    # Unclassified: often the caller's to fix; filed with its sentence, P1.
    assert got["severity"] == "P1" and \
        got["cause_key"] == "auto-mcp-export-final-unclassified"
    assert "could not start the render" in got["title"]
    got = ev(_ev("mcp_error_response", {
        "tool": "upload_finish", "category": "internal_error",
        "message": "internal error. Reference abcdef0123456789."}))
    assert got["severity"] == "P0"
    got = ev(_ev("mcp_error_response", {
        "tool": "wait_for_job", "category": "unclassified",
        "outcome": "unavailable"}))
    assert got["severity"] == "P0" and \
        got["cause_key"] == "auto-mcp-wait-for-job-unavailable"


class ScanCur:
    """Answers scan()'s six reads in order."""

    def __init__(self, jobs=(), replies=(), reply_jobs=(), requests=(),
                 events=(), held=()):
        self.answers = [list(jobs), list(replies)]
        if any((r.get("job") or "").isdigit() for r in replies):
            self.answers.append(list(reply_jobs))
        self.answers += [list(requests), list(events), list(held)]
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.answers.pop(0)


def test_scan_files_jobs_requests_and_replies_once():
    jobs = [{"id": 115549, "type": "index", "user_id": 2589,
             "project_id": 4911, "at": NOW, "tool": None,
             "error": "ffmpeg killed: wall-clock 3000s exceeded"}]
    replies = [
        # The reply to the failed job above: the job is the failure.
        {"id": 1, "at": NOW.replace(tzinfo=None), "error": "job_failed",
         "job": "115549", "text": "Something went wrong", "user_id": 2589,
         "project_id": 4911},
        # A reply whose job failed in an earlier tick: already filed.
        {"id": 2, "at": NOW.replace(tzinfo=None), "error": "job_failed",
         "job": "115000", "text": "x", "user_id": 2589, "project_id": 4911},
        # An error reply with no failed job behind it: its own failure.
        {"id": 3, "at": NOW.replace(tzinfo=None), "error": "job_died",
         "job": "", "text": "The editing server restarted",
         "user_id": 2600, "project_id": 5000}]
    requests = [{"id": 99, "user_id": 2600, "project_id": 5000, "at": NOW,
                 "outcome": "partial", "why": "verifier held it"}]
    cur = ScanCur(jobs, replies, [{"id": 115000}], requests, [])
    got = intake.scan(cur, NOW - timedelta(minutes=10), NOW)
    assert [f["ref"] for f in got] == ["job:115549", "msg:3", "request:99"]
    assert got[0]["cause_key"] == "auto-job-index-wall-clock"
    assert got[1]["cause_key"].startswith("auto-reply-job-died-")
    assert got[2]["cause_key"] == "auto-request-partial"
    # Real customers only: every read carries the internal ids.
    for sql, params in cur.sql:
        if "users u" in sql:
            assert "NOT (u.id = ANY(%(internal)s))" in sql
            assert 60 in params["internal"] and 22 in params["internal"]


def test_a_question_to_the_user_is_not_filed_as_a_blocked_request():
    # TRIAL_QUALITY_BUILD (31 Oct): the editor asking the user (music
    # choices) ended the request "blocked" and filed P0 noise. The worker
    # now records awaiting_user; the rule also leaves out an older worker's
    # status awaiting_user rows, and never matches outcome awaiting_user.
    cur = ScanCur([], [], [], [], [])
    intake.scan(cur, NOW - timedelta(minutes=10), NOW)
    req = [sql for sql, _p in cur.sql if "j.type = 'agent_turn'" in sql]
    assert req, "the request read is issued"
    assert "j.result->>'outcome' IN ('partial', 'blocked')" in req[0]
    assert "COALESCE(j.result->>'status', '') <> 'awaiting_user'" in req[0]
    assert "awaiting_user', 'partial" not in req[0]


def test_a_pass_never_counts_the_overlap_twice(monkeypatch):
    db = TicketsDB()
    seen = [_f("job:1", NOW - timedelta(minutes=3)),
            _f("job:2", NOW - timedelta(minutes=1))]
    monkeypatch.setattr(intake, "scan", lambda cur, since, now: list(seen))
    monkeypatch.setattr(intake, "_tables_ready", lambda cur: True)

    class R:
        def execute(self, sql, params=None):
            self.key = params[0]

        def fetchone(self):
            v = db.kv.get(self.key)
            return {"value": v} if v else None
    first = intake.run(R(), db.cursor(), NOW)
    assert first["filed"] == 2 and first["tickets"][0]["action"] in (
        "opened", "attached")
    again = intake.run(R(), db.cursor(), NOW + timedelta(minutes=1))
    assert again["filed"] == 0 and again["seen"] == 0
    assert db.rows["auto-job-index-oom"]["occurrences"] == 2
    mark = json.loads(db.kv[intake.WATERMARK_KEY])
    assert set(mark["recent"]) == {"job:1", "job:2"}


def test_one_bad_failure_never_loses_the_others(monkeypatch):
    db = TicketsDB()
    seen = [_f("job:1", NOW, key="NOT A SLUG"), _f("job:2", NOW)]
    monkeypatch.setattr(intake, "scan", lambda cur, since, now: list(seen))
    monkeypatch.setattr(intake, "_tables_ready", lambda cur: True)

    class R:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None
    out = intake.run(R(), db.cursor(), NOW)
    assert out["filed"] == 1 and list(db.rows) == ["auto-job-index-oom"]


# ── Who acts, and when ───────────────────────────────────────────────────

def test_the_p0_watch_takes_open_p0s_and_stale_fixing_ones():
    class C:
        def execute(self, sql, params=None):
            self.sql, self.params = " ".join(sql.split()), params

        def fetchone(self):
            return {"t": "tickets"}

        def fetchall(self):
            return []
    c = C()
    intake.pending_p0(c, NOW)
    assert "severity = 'P0'" in c.sql and "status = 'open'" in c.sql
    assert "status = 'fixing' AND updated_at <" in c.sql
    assert c.params == (NOW - timedelta(hours=6),)


def test_the_intake_runs_every_minute_from_the_web_process():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_src = open(os.path.join(root, "app.py")).read()
    assert "from ops_loop.intake import start_scheduler" in app_src
    src = open(os.path.join(root, "ops_loop", "intake.py")).read()
    assert re.search(r'"interval", minutes=1', src)
    assert "pg_try_advisory_lock" in src


def test_the_scheduler_never_starts_without_a_real_database():
    class App:
        config = {"DATABASE_URL": "postgresql://stub/stub"}
    intake._scheduler = None
    intake.start_scheduler(App())
    assert intake._scheduler is None


def test_the_cli_mirrors_p0s_into_the_marker(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import ops_review
    marker = tmp_path / "P0_PENDING"
    monkeypatch.setattr(ops_review, "P0_MARKER", str(marker))
    ops_review.sync_p0_marker([{"id": 5, "title": "index failed"},
                               {"id": 2, "title": "final failed"}])
    got = json.loads(marker.read_text())
    assert got["tickets"] == [2, 5] and got["titles"]["5"] == "index failed"
    ops_review.sync_p0_marker([])
    assert not marker.exists()


def test_the_days_failures_by_ticket_with_fixes_and_waiting_p0s():
    from ops_loop import views
    start, end = NOW - timedelta(hours=16), NOW

    class C:
        def __init__(self):
            self.answers = [
                [{"id": 3, "u": "7", "project": "70"},
                 {"id": 3, "u": "7", "project": "71"},
                 {"id": 3, "u": "8", "project": "72"},
                 {"id": 5, "u": "9", "project": "90"}],
                [{"id": 3, "cause_key": "auto-job-index-oom",
                  "title": "index failed", "severity": "P0",
                  "status": "open", "root_cause": None, "fix_sha": None,
                  "regression_check": None, "recurrence_count": 0,
                  "closed_at": None, "created_at": NOW - timedelta(hours=2),
                  "opened_at": NOW - timedelta(hours=2)},
                 {"id": 5, "cause_key": "auto-upload-rejected-over-size-cap",
                  "title": "Upload refused", "severity": "P0",
                  "status": "fixed", "root_cause": "cap was 100 GB\nmore",
                  "fix_sha": "abc1234,fe:9876543",
                  "regression_check": "tests/x.py", "recurrence_count": 0,
                  "closed_at": NOW - timedelta(hours=1),
                  "created_at": NOW - timedelta(hours=5),
                  "opened_at": NOW - timedelta(hours=5)},
                 {"id": 6, "cause_key": "render-video-ends-before-audio",
                  "title": "picture stops", "severity": "P1",
                  "status": "fixing", "root_cause": None, "fix_sha": None,
                  "regression_check": None, "recurrence_count": 0,
                  "closed_at": None, "created_at": NOW - timedelta(days=7),
                  "opened_at": NOW - timedelta(days=7)}]]

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return self.answers.pop(0)

        def fetchone(self):          # the intake's watermark
            return {"value": json.dumps({"at": (NOW - timedelta(
                minutes=1)).isoformat()})}
    got = views.failures_between(C(), start, end, NOW)
    assert got["intake_stale"] is False
    assert got["total"] == 4 and got["customers"] == 3
    assert [(t["id"], t["failures"], t["users"]) for t in got["by_ticket"]] \
        == [(3, 3, 2), (5, 1, 1)]
    assert got["by_ticket"][0]["url"].endswith("ticket=3")
    assert [t["id"] for t in got["fixed"]] == [5]
    assert got["fixed"][0]["root_cause"] == "cap was 100 GB"
    assert [c["url"].split("/")[-3] for c in got["fixed"][0]["commits"]] == \
        ["hustler-server", "startup_frontend"]
    assert [u["id"] for u in got["unhandled"]] == [3]
    assert (got["open"], got["fixing"], got["p0_active"]) == (1, 1, 1)
