"""Every P0 reaches the owner, with whether it was handled (owner, 7 Nov 2026):
"i should still receive the mail so i dont miss something and i become blind
but its a good idea to indicate whether it was handled correctly or not so i
know whether i should freak out or no".

358fd95f emailed only a P0 nobody claimed: #387, released with its fix live,
was never mailed at all. fa908840 mailed every state change, and #883 (a
customer who left mid-upload) sent BEING FIXED, then STILL STUCK 4 minutes
later with no fix shipped: "try not to make it annoying ... how it is being
fixed and still stuck?" Now an episode gets at most two emails, its first state
that held 10 minutes (NOT HANDLED and STILL STUCK at once) and its outcome; a
hand-back flagged --waiting-on-customer is WAITING ON CUSTOMER, one without a
fix is NOT HANDLED; and the subject says "no action needed" unless only the
owner can fix the cause ("needs you (wake the Mac)", "(top up Anthropic)").
The dedupe keys live in app_kv, so a restart sends nothing twice.

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

    def release(self, tid, at, waiting=None, note="live, replay owed"):
        extra = {"waiting_on_customer": waiting} if waiting else {}
        self.add(tid, ev("released", at, note=note, **extra), "open")

    def beat(self, at, running=None, top=None):
        v = {"at": at.isoformat()}
        if running is not None:
            v.update(fixers_running=running, fixers_max=top)
        self.kv["ops_heartbeat_p0_watch"] = json.dumps(v)

    def close(self, tid, at, sha=SHA):
        self.add(tid, ev("fixed", at, fix_sha=sha), "fixed")

    def verified(self, tid, at):
        self.add(tid, ev("verified", at))

    def verify_failed(self, tid, at):
        self.add(tid, ev("verify_failed", at, note="replay failed"), "open")


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


# ── The five states, read from the ticket's history ──────────────────────

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
    assert s["since"] == T0 + 3 * MIN


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
    assert s["state"] == "FIXED" and not s["replay_owed"]
    assert s["verified_at"] == T0 + 8 * 60 * MIN


def test_still_stuck_only_when_a_fix_shipped_failed_and_nobody_holds_it(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.live(1, T0 + 40 * MIN)
    db.fail(1, T0 + 60 * MIN)
    # The Fixer that shipped still holds it: someone is on it.
    assert state(db, 1, T0 + 70 * MIN)["state"] == "BEING FIXED"
    db.release(1, T0 + 41 * MIN)            # handed back before the failure
    db.tickets[1]["history"].sort(key=lambda h: h["at"])
    assert state(db, 1, T0 + 65 * MIN)["state"] is None   # the watch's turn
    s = state(db, 1, T0 + 70 * MIN)
    assert s["state"] == "STILL STUCK" and s["stuck_now"]
    assert s["failed_after_fix_at"] == T0 + 60 * MIN
    assert s["customers"] == 2 and s["customers_after_fix"] == 1
    # Picked up again: BEING FIXED, never STILL STUCK.
    db.claim(1, T0 + 71 * MIN)
    s = state(db, 1, T0 + 72 * MIN)
    assert s["state"] == "BEING FIXED" and s["reclaimed_at"] == T0 + 71 * MIN


def test_handed_back_with_no_fix_is_not_handled_not_still_stuck(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.release(1, T0 + 90 * MIN, note="could not reproduce yet")
    s = state(db, 1, T0 + 90 * MIN)
    assert s["state"] == "NOT HANDLED" and s["handed_back"]
    assert s["quiet_until"] == T0 + 90 * MIN + intake.RELEASE_QUIET


def test_handed_back_waiting_on_the_customer_is_its_own_state(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.release(1, T0 + 9 * MIN, waiting="resumes from part 44 on re-pick")
    s = state(db, 1, T0 + 9 * MIN)
    assert s["state"] == "WAITING ON CUSTOMER"
    assert s["waiting_on_customer"] == "resumes from part 44 on re-pick"
    # The customer came back and failed again: the watch's turn, then nobody.
    db.fail(1, T0 + 60 * MIN, user=2837)
    assert state(db, 1, T0 + 65 * MIN)["state"] is None
    assert state(db, 1, T0 + 70 * MIN)["state"] == "NOT HANDLED"


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


# ── The emails: at most two an episode, held 10 minutes, never twice ─────

D883 = datetime(2026, 11, 7, 15, 56, tzinfo=timezone.utc)
UPLOAD = ("film.mov · 3592318976 bytes · original · sent 806354944 · last "
          "heard upload_interrupted")


def _replay(db, start, end, step=timedelta(seconds=15)):
    """Every alert tick from `start` to `end`, the watch beating every 5 min."""
    got, at = [], start
    while at <= end:
        db.beat(at - timedelta(minutes=at.minute % 5, seconds=at.second))
        got += [(at, s) for s in tick(db, at)]
        at += step
    return got


def test_883_replayed_is_one_waiting_on_customer_never_still_stuck(db):
    """#883, 7 Nov: opened 15:56, a Fixer claimed it at 16:05:29 (old code:
    BEING FIXED at 16:05:45), released it at 16:09:30 because customer 2878
    left mid-upload (old code: STILL STUCK at 16:09:45)."""
    db.open(883, D883, user=2878, severity="P0", detail=UPLOAD,
            title="An upload stopped without landing: the page went away")
    db.claim(883, D883 + timedelta(minutes=9, seconds=29))
    db.release(883, D883 + timedelta(minutes=13, seconds=30),
               note="customer 2878 left mid-upload at part 43 of 200",
               waiting="the studio resumes from part 44 of 200 when they pick "
                       "the same file again in the same browser (6 days)")
    got = _replay(db, D883, D883 + timedelta(hours=8))
    subjects = [s for _, s in got]
    assert not any("STILL STUCK" in s for s in subjects), subjects
    assert len(subjects) <= 2
    assert sum(s.startswith("WAITING ON CUSTOMER") for s in subjects) == 1
    assert subjects == [
        "WAITING ON CUSTOMER, no action needed: P0 #883 An upload stopped "
        "without landing: the page went away (our side ready since 20:09 "
        "Dubai, it moves when the customer returns)"]
    # Sent once the hand-back held 10 minutes, not at it.
    assert got[0][0] == D883 + timedelta(minutes=23, seconds=30)


def test_883_released_without_the_flag_is_one_not_handled(db):
    db.open(883, D883, user=2878, detail=UPLOAD,
            title="An upload stopped without landing: the page went away")
    db.claim(883, D883 + timedelta(minutes=9, seconds=29))
    db.release(883, D883 + timedelta(minutes=13, seconds=30),
               note="nothing more until they return")
    got = _replay(db, D883, D883 + timedelta(hours=8))
    assert [s for _, s in got] == [
        "NOT HANDLED, no action needed: P0 #883 An upload stopped without "
        "landing: the page went away (handed back at 20:09 Dubai with no fix)"]
    assert got[0][0] == D883 + timedelta(minutes=13, seconds=30)


def test_at_most_two_emails_an_episode_first_held_state_then_outcome(db):
    db.open(1, T0)
    got = tick(db, T0 + 10 * MIN)
    assert len(got) == 1 and got[0].startswith(
        "NOT HANDLED, no action needed: P0 #1 Export failed")
    assert tick(db, T0 + 11 * MIN) == []                   # restart: nothing
    db.claim(1, T0 + 12 * MIN)
    assert tick(db, T0 + 30 * MIN) == []     # BEING FIXED is never the outcome
    db.live(1, T0 + 31 * MIN)
    assert tick(db, T0 + 50 * MIN) == []     # FIX LIVE waits for its proof
    db.verified(1, T0 + 60 * MIN)
    assert tick(db, T0 + 69 * MIN) == []     # held 9 min: pending
    got = tick(db, T0 + 70 * MIN)
    assert got == ["FIXED, no action needed: P0 #1 Export failed: the render "
                   "ran out of memory (proven by a replay at 14:00 Dubai)"]
    db.release(1, T0 + 72 * MIN)
    db.fail(1, T0 + 80 * MIN)
    db.beat(T0 + 88 * MIN)
    assert tick(db, T0 + 90 * MIN) == []     # STILL STUCK: two already sent
    assert sorted(k.split(":")[4] for k in db.kv
                  if k.startswith("cockpit_alert:p0:")) == [
        "fixed", "not-handled"]


def test_the_proof_is_the_outcome_that_wins_over_fix_live(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    db.live(2, T0 + 20 * MIN)
    sent = [s for m in range(4, 61) for s in tick(db, T0 + m * MIN)]
    assert [s.split(",")[0] for s in sent] == ["BEING FIXED"]
    db.verified(2, T0 + 50 * MIN)
    sent += [s for m in range(61, 300, 5) for s in tick(db, T0 + m * MIN)]
    assert [s.split(",")[0] for s in sent] == ["BEING FIXED", "FIXED"]


def test_fix_live_unproven_is_the_outcome_after_the_proof_wait(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    assert len(tick(db, T0 + 13 * MIN)) == 1                 # BEING FIXED
    db.live(2, T0 + 20 * MIN)
    assert tick(db, T0 + 20 * MIN + intake.P0_PROOF_WAIT - MIN) == []
    got = tick(db, T0 + 20 * MIN + intake.P0_PROOF_WAIT)
    assert len(got) == 1 and got[0].startswith("FIX LIVE, no action needed")
    assert "not proven yet" in got[0]


def test_a_failed_proof_is_not_fixed_yet_and_taken_again_at_once(db):
    db.open(3, T0)
    db.claim(3, T0 + 3 * MIN)
    db.live(3, T0 + 5 * MIN)
    got = tick(db, T0 + 15 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE, no action needed")
    db.verify_failed(3, T0 + 30 * MIN)
    s = state(db, 3, T0 + 31 * MIN)
    assert s["state"] == "NOT FIXED" and s["quiet_until"] is None
    db.beat(T0 + 36 * MIN)
    assert tick(db, T0 + 39 * MIN) == []                     # held 9 min
    got = tick(db, T0 + 40 * MIN)
    assert got == ["NOT FIXED yet, no action needed: P0 #3 Export failed: the "
                   "render ran out of memory (the proof of fix 8f3c2a1 failed "
                   "at 13:30 Dubai, a Fixer takes it again automatically)"]
    db.claim(3, T0 + 41 * MIN)
    s = state(db, 3, T0 + 42 * MIN)
    assert s["state"] == "NOT FIXED" and s["retaken_at"] == T0 + 41 * MIN
    db.live(3, T0 + 60 * MIN, sha=SHA2)
    db.verified(3, T0 + 90 * MIN)
    assert state(db, 3, T0 + 91 * MIN)["state"] == "FIXED"
    assert tick(db, T0 + 120 * MIN) == []                    # two sent


def test_a_state_that_changes_within_the_hold_replaces_the_pending_one(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    assert tick(db, T0 + 4 * MIN) == []      # BEING FIXED pending
    db.live(2, T0 + 8 * MIN)
    db.release(2, T0 + 9 * MIN)
    assert tick(db, T0 + 13 * MIN) == []     # BEING FIXED replaced, not sent
    got = tick(db, T0 + 18 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE, no action needed")
    assert tick(db, T0 + 6 * 60 * MIN) == []


def test_a_p0_claimed_in_time_is_mailed_once_the_claim_held(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    assert tick(db, T0 + 12 * MIN) == []
    got = tick(db, T0 + 13 * MIN)
    assert got == ["BEING FIXED, no action needed: P0 #2 Export failed: the "
                   "render ran out of memory (a Fixer is on it since 13:03 "
                   "Dubai)"]
    assert tick(db, T0 + 20 * MIN) == [] and tick(db, T0 + 6 * 60 * MIN) == []


def test_the_387_case_is_mailed_not_silenced(db):
    """358fd95f: released with its fix live, #387 got no email at all."""
    db.open(387, T0)
    db.claim(387, T0 + 4 * MIN)
    db.live(387, T0 + 50 * MIN)
    db.release(387, T0 + 51 * MIN)
    got = tick(db, T0 + 60 * MIN)
    assert len(got) == 1 and got[0].startswith("FIX LIVE, no action needed: "
                                               "P0 #387 ")
    for hours in (1, 6, 7, 30):
        assert tick(db, T0 + timedelta(hours=hours, minutes=52)) == [], hours


def test_still_stuck_says_the_watch_takes_it_again_and_asks_nothing(db):
    db.open(3, T0)
    db.claim(3, T0 + 2 * MIN)
    db.live(3, T0 + 3 * MIN)
    db.release(3, T0 + 4 * MIN)
    db.fail(3, T0 + 5 * MIN)                 # FIX LIVE held 2 min: replaced
    db.beat(T0 + 13 * MIN, running=1, top=3)
    sent = []
    for m in range(4, 16):
        alerts.run_tick(T0 + m * MIN, rconn=Conn(db), wconn=Conn(db),
                        send=lambda s, b: sent.append((m, s, b)) or True)
    (m, subject, body), = sent
    assert m == 15 and subject.startswith("STILL STUCK, no action needed: "
                                          "P0 #3 ")
    from routes.newsletter_content import plain_text
    assert "starts a Fixer on it again automatically" in plain_text(body)


def test_needs_you_only_when_only_the_owner_can_fix_it(db):
    db.open(4, T0)
    db.beat(T0 - 30 * MIN)                  # the watch stopped 40 min ago
    got = tick(db, T0 + 10 * MIN)
    assert got == ["NOT HANDLED, needs you (wake the Mac): P0 #4 Export "
                   "failed: the render ran out of memory (no Fixer 10 min "
                   "after it opened)"]
    db.open(5, T0, detail="final job 91240 failed: Your credit balance is "
                          "too low to access the Anthropic API")
    db.beat(T0 + 8 * MIN, running=3, top=3)
    got = tick(db, T0 + 10 * MIN)
    assert got == ["NOT HANDLED, needs you (top up Anthropic): P0 #5 Export "
                   "failed: the render ran out of memory (no Fixer 10 min "
                   "after it opened)"]
    db.open(6, T0)
    sent = []
    alerts.run_tick(T0 + 11 * MIN, send=lambda s, b: sent.append((s, b)) or True,
                    rconn=Conn(db), wconn=Conn(db))
    (subject, body), = sent
    assert subject.startswith("NOT HANDLED, no action needed: P0 #6 ")
    from routes.newsletter_content import plain_text
    assert "All 3 Fixers are busy" in plain_text(body)
    assert "act now" not in subject


def test_a_recurrence_is_a_new_episode_and_mailed_again(db):
    db.open(3, T0)
    db.claim(3, T0 + 2 * MIN)
    db.live(3, T0 + 20 * MIN)
    db.close(3, T0 + 60 * MIN)
    assert len(tick(db, T0 + 70 * MIN)) == 1
    later = T0 + 5 * 60 * MIN
    db.add(3, ev("recurred", later, auto=True), "open")
    db.fail(3, later)
    db.beat(later + 5 * MIN)
    assert tick(db, later + 5 * MIN) == []
    got = tick(db, later + 10 * MIN)
    assert len(got) == 1 and got[0].startswith("NOT HANDLED, no action needed: "
                                               "P0 #3")


def test_an_episode_from_before_the_switch_mails_its_outcome_only(db):
    at = alerts.STATE_MAIL_FROM - timedelta(hours=2)
    db.open(4, at)
    db.claim(4, at + 5 * MIN)
    now = alerts.STATE_MAIL_FROM + timedelta(hours=1)
    assert tick(db, now) == []                  # BEING FIXED was known
    db.live(4, now + MIN)
    assert tick(db, now + 11 * MIN) == []       # the proof is the outcome
    got = tick(db, now + MIN + intake.P0_PROOF_WAIT)
    assert len(got) == 1 and got[0].startswith("FIX LIVE, no action needed: "
                                               "P0 #4 ")


def test_keys_sent_before_this_change_count_toward_the_two(db):
    """#883 in production already had BEING FIXED and STILL STUCK sent."""
    db.open(883, D883, user=2878, detail=UPLOAD)
    db.claim(883, D883 + timedelta(minutes=9, seconds=29))
    db.release(883, D883 + timedelta(minutes=13, seconds=30))
    ep = "cockpit_alert:p0:883:20261107T155600:"
    db.kv[ep + "being-fixed:20261107T160529"] = "1"
    db.kv[ep + "still-stuck:no-fix"] = "1"
    assert _replay(db, D883 + 20 * MIN, D883 + timedelta(hours=8),
                   step=MIN) == []


def test_not_handled_passes_the_daily_cap_and_the_rest_wait(db):
    db.kv[alerts._count_key(T0 + 20 * MIN)] = json.dumps(
        {"n": alerts.MAX_ALERT_EMAILS_PER_DAY})
    db.open(5, T0)
    db.open(6, T0)
    db.claim(6, T0 + 2 * MIN)
    db.beat(T0 + 18 * MIN)
    got = tick(db, T0 + 20 * MIN)
    assert len(got) == 1 and got[0].startswith("NOT HANDLED, no action "
                                               "needed: P0 #5")
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
        "NOT HANDLED, no action needed: P0 #8 Export failed: the render ran "
        "out of memory (no Fixer 10 min after it opened)",
        "NOT HANDLED, no action needed: P0 #9 Export failed: the render ran "
        "out of memory (no Fixer 10 min after it opened)",
        "Valmera alert: A subscriber ran out of credits with their request "
        "undone"]


# ── What the owner reads ─────────────────────────────────────────────────

def _samples():
    db = Db()
    db.open(861, T0)
    db.fail(861, T0 + 2 * MIN, user=2846)
    out = {"NOT HANDLED": (state(db, 861, T0 + 12 * MIN), T0 + 12 * MIN)}
    db.claim(861, T0 + 14 * MIN)
    out["BEING FIXED"] = (state(db, 861, T0 + 24 * MIN), T0 + 24 * MIN)
    db.live(861, T0 + 52 * MIN)
    db.release(861, T0 + 53 * MIN)
    out["FIX LIVE"] = (state(db, 861, T0 + 62 * MIN), T0 + 62 * MIN)
    db.fail(861, T0 + 69 * MIN, user=3001)
    out["STILL STUCK"] = (state(db, 861, T0 + 80 * MIN), T0 + 80 * MIN)
    db.claim(861, T0 + 82 * MIN)
    db.live(861, T0 + 100 * MIN, sha=SHA2)
    db.verify_failed(861, T0 + 125 * MIN)
    out["NOT FIXED"] = (state(db, 861, T0 + 135 * MIN), T0 + 135 * MIN)
    db.claim(861, T0 + 137 * MIN)
    db.live(861, T0 + 160 * MIN)
    db.verified(861, T0 + 190 * MIN)
    out["FIXED"] = (state(db, 861, T0 + 200 * MIN), T0 + 200 * MIN)
    w = Db()
    w.open(883, D883, user=2878, detail=UPLOAD,
           title="An upload stopped without landing: the page went away")
    w.claim(883, D883 + timedelta(minutes=9, seconds=29))
    w.release(883, D883 + timedelta(minutes=13, seconds=30),
              waiting="the studio resumes from part 44 of 200 when they pick "
                      "the same file again in the same browser within 6 days")
    out["WAITING ON CUSTOMER"] = (state(w, 883, D883 + 24 * MIN),
                                  D883 + 24 * MIN)
    return out


def test_rendered_samples_say_state_doing_customers_stuck_link_and_what(capsys):
    from routes.newsletter_content import plain_text
    for name, (s, now) in _samples().items():
        assert s["state"] == name, (name, s["state"])
        subject, body = alerts.p0_mail(s, now, beat=3, late=20, fixers=(1, 3))
        assert subject.startswith(alerts.STATE_LABELS.get(name, name)
                                  + ", no action needed: ")
        text = plain_text(body)
        assert "What the system is doing:" in text
        assert "Customers:" in text and "Stuck right now:" in text
        assert "What happened:" in text and "act now" not in text
        assert f"?tab=tickets&ticket={s['id']}" in body
        with capsys.disabled():
            print(f"\n──── {name} ────\nSubject: {subject}\n{text}")
    s, now = _samples()["NOT HANDLED"]
    subject, body = alerts.p0_mail(s, now, beat=3, late=20)
    assert subject == ("NOT HANDLED, no action needed: P0 #861 Export failed: "
                       "the render ran out of memory (no Fixer 12 min after "
                       "it opened)")
    assert "#dc2626" not in body and "2 customers" in plain_text(body)
    assert "Yes. No fix is live" in plain_text(body)
    assert "starts a Fixer on it automatically" in plain_text(body)
    subject, body = alerts.p0_mail(s, now, beat=25, late=20)
    assert subject.startswith("NOT HANDLED, needs you (wake the Mac): ")
    assert "#dc2626" in body and "Needs you: wake the Mac" in plain_text(body)
    s, now = _samples()["FIX LIVE"]
    text = plain_text(alerts.p0_mail(s, now)[1])
    assert "No. Nobody has failed on it since the fix went live" in text
    assert "not proven yet" in text and "VERIFY run" in text
    s, now = _samples()["FIXED"]
    subject, body = alerts.p0_mail(s, now)
    assert "(proven by a replay at 16:10 Dubai)" in subject
    s, now = _samples()["NOT FIXED"]
    subject, body = alerts.p0_mail(s, now, beat=3, late=20)
    assert subject.startswith("NOT FIXED yet, no action needed: ")
    assert ("the proof of fix 1e2d3c4 failed at 15:05 Dubai, a Fixer takes "
            "it again automatically") in subject
    assert "starts a Fixer on it again automatically" in plain_text(body)
    s, now = _samples()["WAITING ON CUSTOMER"]
    text = plain_text(alerts.p0_mail(s, now)[1])
    assert "part 44 of 200" in text and "only their return moves it" in text
