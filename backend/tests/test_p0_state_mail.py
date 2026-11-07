"""Every P0 reaches the owner, with whether it was handled (owner, 7 Nov 2026):
"i should still receive the mail so i dont miss something and i become blind
but its a good idea to indicate whether it was handled correctly or not so i
know whether i should freak out or no".

358fd95f emailed only a P0 nobody claimed: #387, released with its fix live,
was never mailed at all. Now each P0 episode gets one email once its state is
known, whose subject starts NOT HANDLED / BEING FIXED / FIX LIVE / STILL STUCK,
and one more each time it turns to FIX LIVE, STILL STUCK or NOT HANDLED. The
dedupe keys live in app_kv, so a restart sends nothing twice.

Pure Python: the tickets table and app_kv are in memory (the SQL of
intake.p0_states runs on real PostgreSQL in test_ticket_truth.py)."""
import copy
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

import cockpit_alerts as alerts                           # noqa: E402
from ops_loop import intake                               # noqa: E402

T0 = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)    # 13:00 Dubai
MIN = timedelta(minutes=1)
SHA = "8f3c2a1b9d0e4f5a6b7c8d9e0f1a2b3c4d5e6f70"
SHA2 = "1e2d3c4b5a69788796a5b4c3d2e1f00112233445"


def ev(event, at, **kw):
    return dict(event=event, at=at.isoformat(timespec="seconds"), **kw)


class Db:
    """The tickets table and app_kv, as the alert tick reads and writes them."""

    def __init__(self):
        self.kv, self.tickets = {}, {}

    def open(self, tid, at, user=2837, severity="P0",
             title="Export failed: the render ran out of memory",
             detail="final job 91234 failed: ffmpeg killed (OOM) at 61%"):
        self.tickets[tid] = {
            "id": tid, "title": title, "severity": severity, "status": "open",
            "last_seen": at, "created_at": at, "updated_at": at,
            "history": [ev("opened", at, severity=severity, auto=True),
                        ev("failure", at, user=user, detail=detail)]}
        return tid

    def add(self, tid, entry, status=None, at=None):
        t = self.tickets[tid]
        t["history"].append(entry)
        t["updated_at"] = at or datetime.fromisoformat(entry["at"])
        if status:
            t["status"] = status

    def fail(self, tid, at, user=2846, detail="final job failed again"):
        self.add(tid, ev("failure", at, user=user, detail=detail), at=at)
        self.tickets[tid]["last_seen"] = at

    def claim(self, tid, at):
        self.add(tid, ev("fixing", at, note="on it"), "fixing")

    def live(self, tid, at, sha=SHA):
        self.add(tid, ev("live", at, sha=sha))

    def release(self, tid, at):
        self.add(tid, ev("released", at, note="live, replay owed"), "open")

    def close(self, tid, at, sha=SHA):
        self.add(tid, ev("fixed", at, fix_sha=sha), "fixed")


class Cur:
    def __init__(self, db):
        self.db, self._row, self._rows = db, None, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        kv = self.db.kv
        self._row, self._rows = None, []
        if "pg_try_advisory_lock" in flat:
            self._row = {"got": True}
        elif "to_regclass" in flat:
            self._row = {"t": "tickets"}
        elif "FROM tickets" in flat:
            self._rows = [copy.deepcopy(t) for t in self.db.tickets.values()
                          if t["severity"] == "P0"
                          and t["status"] in ("open", "fixing", "fixed")
                          and max(t["updated_at"], t["last_seen"])
                          >= params[0]]
        elif flat.startswith("SELECT key FROM app_kv WHERE left(key"):
            self._rows = [{"key": k} for k in kv if k.startswith(params[1])]
        elif flat.startswith("INSERT INTO app_kv") and "DO NOTHING" in flat:
            if params[0] not in kv:
                kv[params[0]] = params[1]
                self._row = {"key": params[0]}
        elif flat.startswith("INSERT INTO app_kv"):
            kv[params[0]] = params[1]
        elif flat.startswith("DELETE FROM app_kv"):
            kv.pop(params[0], None)
        elif flat.startswith("SELECT value FROM app_kv"):
            v = kv.get(params[0])
            self._row = {"value": v} if v is not None else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cur(self.db)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    from ops_loop import paid_out
    monkeypatch.setattr(intake, "silent_agents", lambda cur, now=None: [])
    monkeypatch.setattr(paid_out, "alerts", lambda cur, now: [])
    monkeypatch.setattr(alerts, "maybe_digest", lambda r, w, now, send: False)
    d = Db()
    d.kv["ops_heartbeat_p0_watch"] = json.dumps({"at": T0.isoformat()})
    return d


def tick(db, at, ok=True):
    """One alert tick; each call is a fresh process (new connections), as
    after a restart: only app_kv carries what was sent."""
    sent = []

    def send(subject, body):
        sent.append((subject, body))
        return ok
    alerts.run_tick(at, send=send, rconn=Conn(db), wconn=Conn(db))
    return [s for s, _ in sent]


def state(db, tid, at):
    return intake.p0_state(copy.deepcopy(db.tickets[tid]), at)


# ── The four states, read from the ticket's history ──────────────────────

def test_unclaimed_is_not_known_for_10_minutes_then_not_handled(db):
    db.open(1, T0)
    assert state(db, 1, T0 + 9 * MIN)["state"] is None
    s = state(db, 1, T0 + 10 * MIN)
    assert s["state"] == "NOT HANDLED" and s["stuck_since"] == T0
    assert s["stuck_now"] and s["customers"] == 1


def test_a_claim_is_being_fixed_with_its_time(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    s = state(db, 1, T0 + 3 * MIN)
    assert s["state"] == "BEING FIXED" and s["claimed_at"] == T0 + 3 * MIN


def test_a_live_fix_nobody_failed_after_is_fix_live_and_owes_its_replay(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.live(1, T0 + 40 * MIN)
    db.release(1, T0 + 41 * MIN)
    # #387: released with its fix live, 6 h quiet over, nobody failed since.
    s = state(db, 1, T0 + 7 * 60 * MIN)
    assert s["state"] == "FIX LIVE" and s["variant"] == SHA[:12]
    assert s["replay_owed"] and not s["stuck_now"]
    db.close(1, T0 + 8 * 60 * MIN)
    s = state(db, 1, T0 + 9 * 60 * MIN)
    assert s["state"] == "FIX LIVE" and not s["replay_owed"]
    assert s["variant"] == SHA[:12]


def test_a_failure_after_the_fix_is_still_stuck_once_known(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.live(1, T0 + 40 * MIN)
    db.fail(1, T0 + 60 * MIN)
    assert state(db, 1, T0 + 65 * MIN)["state"] is None   # watch's turn
    s = state(db, 1, T0 + 70 * MIN)
    assert s["state"] == "STILL STUCK" and s["stuck_now"]
    assert s["failed_after_fix_at"] == T0 + 60 * MIN
    assert s["customers"] == 2 and s["customers_after_fix"] == 1
    # A Fixer answering it makes it known at once.
    db.claim(1, T0 + 61 * MIN)
    assert state(db, 1, T0 + 62 * MIN)["state"] == "STILL STUCK"


def test_handed_back_with_no_fix_live_is_still_stuck(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.release(1, T0 + 90 * MIN)
    s = state(db, 1, T0 + 91 * MIN)
    assert s["state"] == "STILL STUCK" and s["variant"] == "no-fix"
    assert s["stuck_now"]


def test_a_failure_after_a_release_nobody_answers_is_not_handled(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.live(1, T0 + 40 * MIN)
    db.release(1, T0 + 41 * MIN)
    db.fail(1, T0 + 120 * MIN)
    s = state(db, 1, T0 + 130 * MIN)
    assert s["state"] == "NOT HANDLED" and s["stuck_since"] == T0 + 120 * MIN


def test_a_stale_claim_a_customer_failed_after_is_not_handled(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    assert state(db, 1, T0 + 7 * 60 * MIN)["state"] == "BEING FIXED"
    db.fail(1, T0 + 60 * MIN)
    s = state(db, 1, T0 + 6 * 60 * MIN + 3 * MIN)
    assert s["state"] == "NOT HANDLED" and s["stale_claim"]


def test_a_p1_raised_to_p0_is_timed_from_the_raising(db):
    db.open(1, T0, severity="P1")
    db.tickets[1]["severity"] = "P0"
    db.add(1, ev("escalated", T0 + 30 * MIN, severity="P0",
                 why="left_blocked"))
    assert state(db, 1, T0 + 35 * MIN)["state"] is None
    s = state(db, 1, T0 + 40 * MIN)
    assert s["state"] == "NOT HANDLED" and s["p0_at"] == T0 + 30 * MIN


# ── The emails: one when known, one more per change, never twice ─────────

def test_one_email_when_known_then_one_per_change_across_restarts(db):
    db.open(1, T0)
    assert tick(db, T0 + 5 * MIN) == []
    got = tick(db, T0 + 10 * MIN)
    assert len(got) == 1 and got[0].startswith(
        "NOT HANDLED, act now: P0 #1 Export failed")
    assert tick(db, T0 + 11 * MIN) == []                   # restart: nothing
    db.claim(1, T0 + 12 * MIN)
    assert tick(db, T0 + 13 * MIN) == []        # BEING FIXED is not a change
    db.live(1, T0 + 30 * MIN)
    got = tick(db, T0 + 31 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE: P0 #1 ")
    assert "8f3c2a1" in got[0] and "replay owed" in got[0]
    assert tick(db, T0 + 32 * MIN) == []
    db.fail(1, T0 + 40 * MIN)
    assert tick(db, T0 + 45 * MIN) == []        # the watch has its 10 min
    got = tick(db, T0 + 50 * MIN)
    assert len(got) == 1 and got[0].startswith("STILL STUCK: P0 #1 ")
    assert tick(db, T0 + 51 * MIN) == []
    db.live(1, T0 + 60 * MIN, sha=SHA2)
    db.release(1, T0 + 61 * MIN)
    got = tick(db, T0 + 62 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE: P0 #1 ")
    assert "1e2d3c4" in got[0]
    db.close(1, T0 + 90 * MIN, sha=SHA2)
    assert tick(db, T0 + 91 * MIN) == []        # same fix: not a new state
    assert tick(db, T0 + 8 * 60 * MIN) == []    # the quiet ending says nothing
    assert sorted(k.split(":")[4] for k in db.kv
                  if k.startswith("cockpit_alert:p0:")) == [
        "fix-live", "fix-live", "not-handled", "still-stuck"]


def test_a_p0_claimed_in_time_is_mailed_at_the_claim(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    got = tick(db, T0 + 4 * MIN)
    assert got == ["BEING FIXED: P0 #2 Export failed: the render ran out of "
                   "memory (claimed 13:03 Dubai, 3 min after it opened)"]
    assert tick(db, T0 + 10 * MIN) == [] and tick(db, T0 + 6 * 60 * MIN) == []


def test_the_387_case_is_mailed_not_silenced(db):
    """358fd95f: released with its fix live, #387 got no email at all."""
    db.open(387, T0)
    db.claim(387, T0 + 4 * MIN)
    db.live(387, T0 + 50 * MIN)
    db.release(387, T0 + 51 * MIN)
    got = tick(db, T0 + 52 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE: P0 #387 ")
    for hours in (1, 6, 7, 30):
        assert tick(db, T0 + timedelta(hours=hours, minutes=52)) == [], hours


def test_a_recurrence_is_a_new_episode_and_mailed_again(db):
    db.open(3, T0)
    db.claim(3, T0 + 2 * MIN)
    assert len(tick(db, T0 + 3 * MIN)) == 1
    db.live(3, T0 + 20 * MIN)
    db.close(3, T0 + 60 * MIN)
    assert len(tick(db, T0 + 61 * MIN)) == 1
    later = T0 + 5 * 60 * MIN
    db.add(3, ev("recurred", later, auto=True), "open")
    db.fail(3, later)
    assert tick(db, later + 5 * MIN) == []
    got = tick(db, later + 10 * MIN)
    assert len(got) == 1 and got[0].startswith("NOT HANDLED, act now: P0 #3")


def test_an_episode_from_before_the_switch_mails_its_changes_only(db):
    at = alerts.STATE_MAIL_FROM - timedelta(hours=2)
    db.open(4, at)
    db.claim(4, at + 5 * MIN)
    now = alerts.STATE_MAIL_FROM + timedelta(hours=1)
    assert tick(db, now) == []                  # BEING FIXED was known
    db.live(4, now + MIN)
    got = tick(db, now + 2 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE: P0 #4 ")


def test_not_handled_passes_the_daily_cap_and_the_rest_wait(db):
    db.kv[alerts._count_key(T0 + 20 * MIN)] = json.dumps(
        {"n": alerts.MAX_ALERT_EMAILS_PER_DAY})
    db.open(5, T0)
    db.open(6, T0)
    db.claim(6, T0 + 2 * MIN)
    got = tick(db, T0 + 20 * MIN)
    assert len(got) == 1 and got[0].startswith("NOT HANDLED, act now: P0 #5")
    assert not any(":6:" in k for k in db.kv)   # waits unclaimed


def test_a_failed_send_is_sent_by_the_next_tick(db):
    db.open(7, T0)
    assert len(tick(db, T0 + 10 * MIN, ok=False)) == 1
    assert not any(k.startswith("cockpit_alert:p0:") for k in db.kv)
    assert len(tick(db, T0 + 11 * MIN)) == 1
    assert tick(db, T0 + 12 * MIN) == []


def test_each_p0_state_is_its_own_email_and_others_share_one(db, monkeypatch):
    from ops_loop import paid_out
    monkeypatch.setattr(paid_out, "alerts", lambda cur, now: [
        (91, "Subscriber #2123 ran out of credits with a request undone")])
    db.open(8, T0)
    db.open(9, T0)
    got = tick(db, T0 + 10 * MIN)
    assert sorted(got) == [
        "NOT HANDLED, act now: P0 #8 Export failed: the render ran out of "
        "memory (no Fixer 10 min after it opened)",
        "NOT HANDLED, act now: P0 #9 Export failed: the render ran out of "
        "memory (no Fixer 10 min after it opened)",
        "Valmera alert: A subscriber ran out of credits with their request "
        "undone"]


# ── What the owner reads ─────────────────────────────────────────────────

def _samples():
    db = Db()
    db.open(861, T0)
    db.fail(861, T0 + 2 * MIN, user=2846)
    out = {"NOT HANDLED": (state(db, 861, T0 + 12 * MIN), T0 + 12 * MIN)}
    db.claim(861, T0 + 14 * MIN)
    out["BEING FIXED"] = (state(db, 861, T0 + 14 * MIN), T0 + 14 * MIN)
    db.live(861, T0 + 52 * MIN)
    db.release(861, T0 + 53 * MIN)
    out["FIX LIVE"] = (state(db, 861, T0 + 53 * MIN), T0 + 53 * MIN)
    db.claim(861, T0 + 70 * MIN)
    db.fail(861, T0 + 69 * MIN, user=3001)
    out["STILL STUCK"] = (state(db, 861, T0 + 71 * MIN), T0 + 71 * MIN)
    return out


def test_rendered_samples_say_state_customers_stuck_link_and_what(capsys):
    from routes.newsletter_content import plain_text
    for name, (s, now) in _samples().items():
        assert s["state"] == name, (name, s["state"])
        subject, body = alerts.p0_mail(s, now, beat=3, late=20)
        assert subject.startswith(name)
        text = plain_text(body)
        assert "Customers:" in text and "Stuck right now:" in text
        assert "What happened:" in text
        assert "?tab=tickets&ticket=861" in body
        with capsys.disabled():
            print(f"\n──── {name} ────\nSubject: {subject}\n{text}")
    s, now = _samples()["NOT HANDLED"]
    subject, body = alerts.p0_mail(s, now, beat=3, late=20)
    assert subject == ("NOT HANDLED, act now: P0 #861 Export failed: the render "
                       "ran out of memory (no Fixer 12 min after it opened)")
    assert "#dc2626" in body and "Needs you now" in body
    assert "2 customers" in plain_text(body)
    assert "Yes. No fix is live" in plain_text(body)
    s, now = _samples()["FIX LIVE"]
    text = plain_text(alerts.p0_mail(s, now)[1])
    assert "No. Nobody has failed on it since the fix went live" in text
    assert "still owed" in text
