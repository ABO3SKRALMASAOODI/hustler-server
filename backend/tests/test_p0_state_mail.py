"""Every P0 reaches the owner, with whether it was handled (owner, 7 Nov 2026):
"i should still receive the mail so i dont miss something and i become blind
but its a good idea to indicate whether it was handled correctly or not so i
know whether i should freak out or no".

Superseded the same day (owner: "not fixed yet, no action needed" is
"absurd"): three kinds of email only, ISSUE, FIXED (proven by a replay) and
BLOCKED, and every ISSUE ends in FIXED or BLOCKED, at the latest 4 h after it
and then every 24 h while unproven. History of the earlier versions:

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

    def wont_fix(self, tid, at, reason="owner decision: not offered"):
        self.add(tid, ev("wont_fix", at, reason=reason), "wont_fix")

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
        elif "/* ticket watch */" in flat:
            self._rows = [copy.deepcopy(t) for t in self.db.tickets.values()
                          if t["status"] in ("open", "fixing")
                          or t["updated_at"] >= params[0]]
        elif "FROM tickets" in flat:
            self._rows = [copy.deepcopy(t) for t in self.db.tickets.values()
                          if (t["severity"] == "P0" or any(
                              h.get("severity") == "P0" for h in t["history"]))
                          and (t["status"] in ("open", "fixing") or (
                              t["status"] in ("fixed", "wont_fix")
                              and max(t["updated_at"], t["last_seen"])
                              >= params[0]))]
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


# ── The emails: ISSUE, then FIXED or BLOCKED, and nothing else ───────────

D883 = datetime(2026, 11, 7, 15, 56, tzinfo=timezone.utc)
UPLOAD = ("film.mov · 3592318976 bytes · original · sent 806354944 · last "
          "heard upload_interrupted")
TITLE = "Export failed: the render ran out of memory"
FORBIDDEN = ("no action needed", "not fixed yet", "act now", "still stuck",
             "waiting on customer,", "fix live")


def run(db, start, end, step=MIN, beat=True):
    """Every alert tick from `start` to `end` (the watch beating every 5 min
    when `beat`): [(at, subject, body)]."""
    got, at = [], start
    while at <= end:
        if beat:
            db.beat(at - timedelta(minutes=at.minute % 5, seconds=at.second))
        sent = []
        alerts.run_tick(at, send=lambda s, b: sent.append((s, b)) or True,
                        rconn=Conn(db), wconn=Conn(db))
        got += [(at, s, b) for s, b in sent]
        at += step
    for _, subject, _ in got:
        assert not any(w in subject.lower() for w in FORBIDDEN), subject
    return got


def subjects(got):
    return [s for _, s, _ in got]


def test_issue_at_the_first_claim_and_nothing_while_it_is_worked(db):
    db.open(2, T0)
    db.claim(2, T0 + 3 * MIN)
    db.live(2, T0 + 40 * MIN)                # live is not proven: nothing
    got = run(db, T0, T0 + 4 * 60 * MIN)
    assert [(at, s) for at, s, _ in got] == [
        (T0 + 3 * MIN, f"Issue: {TITLE} (P0 #2), being fixed")]


def test_issue_ten_minutes_after_opening_when_nobody_claimed(db):
    db.open(1, T0)
    got = run(db, T0, T0 + 60 * MIN)
    assert [(at, s) for at, s, _ in got] == [
        (T0 + 10 * MIN, f"Issue: {TITLE} (P0 #1), being fixed")]


def test_883_replayed_is_an_issue_then_nothing_until_fixed(db):
    """#883, 7 Nov: opened 15:56, claimed 16:05:29, handed back at 16:09:30
    because customer 2878 left mid-upload. Owner, 7 Nov: Blocked only for what
    he alone can clear, so waiting on the customer sends nothing more."""
    title = "An upload stopped without landing: the page went away"
    db.open(883, D883, user=2878, detail=UPLOAD, title=title)
    db.claim(883, D883 + timedelta(minutes=9, seconds=29))
    db.release(883, D883 + timedelta(minutes=13, seconds=30),
               note="customer 2878 left mid-upload at part 43 of 200",
               waiting="the studio resumes from part 44 of 200")
    got = run(db, D883, D883 + timedelta(hours=8), step=timedelta(minutes=1))
    assert subjects(got) == [f"Issue: {title} (P0 #883), being fixed"]
    db.live(883, D883 + timedelta(hours=9))
    db.verified(883, D883 + timedelta(hours=9, minutes=30))
    got = run(db, D883 + timedelta(hours=9), D883 + timedelta(hours=10))
    assert subjects(got) == [f"Fixed: {title} (P0 #883)"]


def test_a_live_fix_sends_nothing_until_a_replay_proves_it(db):
    db.open(1, T0)
    db.claim(1, T0 + 3 * MIN)
    db.live(1, T0 + 20 * MIN)
    db.release(1, T0 + 21 * MIN)
    db.verify_failed(1, T0 + 45 * MIN)       # once: a Fixer takes it again
    db.claim(1, T0 + 50 * MIN)
    db.live(1, T0 + 70 * MIN, sha=SHA2)
    db.verified(1, T0 + 100 * MIN)
    got = run(db, T0, T0 + 30 * 60 * MIN, step=5 * MIN)
    assert [(at, s) for at, s, _ in got] == [
        (T0 + 5 * MIN, f"Issue: {TITLE} (P0 #1), being fixed"),
        (T0 + 100 * MIN, f"Fixed: {TITLE} (P0 #1)")]
    from routes.newsletter_content import plain_text
    text = plain_text(got[1][2])
    assert "What was wrong:" in text and "Proof: a replay" in text
    assert "1e2d3c4" in text


def test_a_close_through_the_gate_is_fixed_with_what_was_wrong(db):
    db.open(3, T0)
    db.claim(3, T0 + 2 * MIN)
    db.live(3, T0 + 20 * MIN)
    got = run(db, T0, T0 + 55 * MIN, step=5 * MIN)
    db.tickets[3]["root_cause"] = "the final render sized its memory by RAM"
    db.add(3, ev("fixed", T0 + 60 * MIN, fix_sha=SHA,
                 replay={"session": "p5302", "run": "r1"}), "fixed")
    got += run(db, T0 + 60 * MIN, T0 + 3 * 60 * MIN, step=5 * MIN)
    assert subjects(got) == [f"Issue: {TITLE} (P0 #3), being fixed",
                             f"Fixed: {TITLE} (P0 #3)"]
    from routes.newsletter_content import plain_text
    text = plain_text(got[1][2])
    assert ("What was wrong: the final render sized its memory by RAM. "
            "Proof: a replay of the customer's request on a copy passed at "
            "14:00 Dubai (session p5302, run r1)") in text


def test_blocked_with_the_step_when_the_mac_sleeps_never_an_issue_too(db):
    db.open(4, T0)
    db.beat(T0 - 30 * MIN)                   # the watch stopped
    got = run(db, T0, T0 + 30 * MIN, beat=False)
    assert subjects(got) == [f"Blocked: {TITLE} (P0 #4)"]
    from routes.newsletter_content import plain_text
    text = plain_text(got[0][2])
    assert "Only you can clear it: wake the Mac" in text
    db.claim(4, T0 + 60 * MIN)
    assert run(db, T0 + 60 * MIN, T0 + 3 * 60 * MIN) == []


def test_blocked_with_the_step_when_a_provider_is_out_of_credit(db):
    db.open(5, T0, detail="final job 91240 failed: Your credit balance is "
                          "too low to access the Anthropic API")
    got = run(db, T0, T0 + 20 * MIN)
    assert subjects(got) == [f"Blocked: {TITLE} (P0 #5)"]
    assert "top up Anthropic" in got[0][2]


def test_failed_proofs_and_slow_proofs_never_email_blocked(db):
    """Owner, 7 Nov: a failed or slow proof is the system's own work (VERIFY
    and the Fixers take it again); it is never a Blocked email."""
    db.open(6, T0)
    db.claim(6, T0 + 2 * MIN)
    db.live(6, T0 + 20 * MIN)
    db.verify_failed(6, T0 + 45 * MIN)
    db.claim(6, T0 + 50 * MIN)
    db.live(6, T0 + 70 * MIN, sha=SHA2)
    db.verify_failed(6, T0 + 95 * MIN)
    got = run(db, T0, T0 + 80 * 60 * MIN, step=10 * MIN)
    assert subjects(got) == [f"Issue: {TITLE} (P0 #6), being fixed"]


def _path(db, name):
    tid = 10
    db.open(tid, T0)
    if name == "released":
        db.claim(tid, T0 + 3 * MIN)
        db.release(tid, T0 + 30 * MIN, note="could not reproduce")
    elif name == "waiting":
        db.claim(tid, T0 + 3 * MIN)
        db.release(tid, T0 + 9 * MIN, waiting="resumes on re-pick")
    elif name == "verify_failed":
        db.claim(tid, T0 + 3 * MIN)
        db.live(tid, T0 + 20 * MIN)
        db.release(tid, T0 + 21 * MIN)
        db.verify_failed(tid, T0 + 60 * MIN)
    elif name == "wont_fix":
        db.claim(tid, T0 + 3 * MIN)
        db.wont_fix(tid, T0 + 30 * MIN)
    elif name == "deduped":
        db.fail(tid, T0 + 5 * MIN, user=2900)
        db.fail(tid, T0 + 20 * MIN, user=2901)
    elif name == "lowered_to_p1":
        db.claim(tid, T0 + 3 * MIN)
        db.release(tid, T0 + 20 * MIN)
        db.tickets[tid]["severity"] = "P1"
        db.add(tid, ev("lowered", T0 + 30 * MIN, severity="P1"))
    elif name == "closed_fixed":
        db.claim(tid, T0 + 3 * MIN)
        db.live(tid, T0 + 20 * MIN)
        db.close(tid, T0 + 60 * MIN)
    elif name == "live_unproven":
        db.claim(tid, T0 + 3 * MIN)
        db.live(tid, T0 + 20 * MIN)
        db.release(tid, T0 + 21 * MIN)
    elif name == "failed_after_fix":
        db.claim(tid, T0 + 3 * MIN)
        db.live(tid, T0 + 20 * MIN)
        db.release(tid, T0 + 21 * MIN)
        db.fail(tid, T0 + 40 * MIN, user=2950)
    # "no_failures": opened, nothing else ever happens


@pytest.mark.parametrize("name", [
    "released", "waiting", "verify_failed", "wont_fix", "deduped",
    "lowered_to_p1", "closed_fixed", "live_unproven", "failed_after_fix",
    "no_failures"])
def test_no_path_emails_blocked_unless_only_the_owner_can_clear_it(db, name):
    """Every path ends in Fixed, or stays an Issue the digest lists until it
    is proven; none sends Blocked (no Mac or provider cause here)."""
    _path(db, name)
    got = run(db, T0, T0 + 5 * 60 * MIN, step=5 * MIN)
    kinds = [s.split(":")[0] for s in subjects(got)]
    assert "Blocked" not in kinds, (name, kinds)
    assert set(kinds) <= {"Issue", "Fixed"}, (name, kinds)


def test_keys_from_the_earlier_versions_count_as_the_issue_told(db):
    """#883 in production already had BEING FIXED and STILL STUCK sent."""
    db.open(883, D883, user=2878, detail=UPLOAD)
    db.claim(883, D883 + timedelta(minutes=9, seconds=29))
    db.release(883, D883 + timedelta(minutes=13, seconds=30))
    ep = "cockpit_alert:p0:883:20261107T155600:"
    db.kv[ep + "being-fixed:20261107T160529"] = "1"
    db.kv[ep + "still-stuck:no-fix"] = "1"
    got = run(db, D883 + 20 * MIN, D883 + timedelta(hours=8), step=5 * MIN)
    assert subjects(got) == []


def test_no_backlog_of_fixed_or_issue_at_the_switch(db):
    at = alerts.STATE_MAIL_FROM - timedelta(hours=2)
    db.open(8, at)
    db.claim(8, at + 5 * MIN)
    db.live(8, at + 20 * MIN)
    db.close(8, alerts.FIXED_MAIL_FROM - 30 * MIN)
    assert run(db, alerts.FIXED_MAIL_FROM, alerts.FIXED_MAIL_FROM + 2 * 60 *
               MIN, step=10 * MIN) == []


def test_a_recurrence_is_a_new_episode_and_a_new_issue(db):
    db.open(3, T0)
    db.claim(3, T0 + 2 * MIN)
    db.live(3, T0 + 20 * MIN)
    db.close(3, T0 + 60 * MIN)
    later = T0 + 5 * 60 * MIN
    db.add(3, ev("recurred", later, auto=True), "open")
    db.fail(3, later)
    got = run(db, T0, later + 20 * MIN, step=5 * MIN)
    assert [(at, s) for at, s, _ in got] == [
        (T0 + 5 * MIN, f"Issue: {TITLE} (P0 #3), being fixed"),
        (T0 + 60 * MIN, f"Fixed: {TITLE} (P0 #3)"),
        (later + 10 * MIN, f"Issue: {TITLE} (P0 #3), being fixed")]


def test_a_failed_send_is_sent_by_the_next_tick(db):
    db.open(7, T0)
    assert len(tick(db, T0 + 10 * MIN, ok=False)) == 1
    assert not any(k.startswith("cockpit_alert:p0:") for k in db.kv)
    assert len(tick(db, T0 + 11 * MIN)) == 1
    assert tick(db, T0 + 12 * MIN) == []


def test_only_what_the_owner_can_clear_passes_the_daily_cap(db):
    db.kv[alerts._count_key(T0 + 20 * MIN)] = json.dumps(
        {"n": alerts.MAX_ALERT_EMAILS_PER_DAY})
    db.open(5, T0)
    db.open(6, T0)
    db.claim(6, T0 + 2 * MIN)
    got = tick(db, T0 + 20 * MIN)         # the watch last beat at T0
    assert got == [f"Blocked: {TITLE} (P0 #5)"]
    assert not any(":6:" in k for k in db.kv)   # its ISSUE waits unclaimed


def test_each_p0_email_is_its_own_and_others_share_one(db, monkeypatch):
    from ops_loop import paid_out
    monkeypatch.setattr(paid_out, "alerts", lambda cur, now: [
        (91, "Subscriber #2123 ran out of credits with a request undone")])
    db.open(8, T0)
    db.open(9, T0)
    got = tick(db, T0 + 10 * MIN)
    assert sorted(got) == [
        f"Issue: {TITLE} (P0 #8), being fixed",
        f"Issue: {TITLE} (P0 #9), being fixed",
        "Valmera alert: A subscriber ran out of credits with their request "
        "undone"]


def test_the_digest_lists_new_tickets_and_every_one_open_over_24_h(db):
    now = T0 + 30 * 60 * MIN
    db.open(20, now - 2 * 60 * MIN)                          # P0, new
    db.open(21, now - 3 * 24 * 60 * MIN, severity="P1", title="Slow index")
    db.open(22, now - 5 * 60 * MIN, severity="P2", title="Caption drift")
    db.claim(22, now - 4 * 60 * MIN)
    db.open(23, now - 2 * 24 * 60 * MIN, severity="P1", title="Old, fixed")
    db.close(23, now - 30 * 60 * MIN)
    tw = alerts.ticket_watch(Cur(db), now)
    from routes.newsletter_content import plain_text
    new = [plain_text(x) for x in tw["new"]]
    old = [plain_text(x) for x in tw["old"]]
    assert [x.split(" ")[0] for x in new] == ["#22", "#20"]
    assert new[1].endswith("nobody on it yet") and "being fixed" in new[0]
    assert len(old) == 1 and old[0].startswith("#21 ")
    assert old[0].endswith("[P1] Slow index: open for 3 d 0 h")
    lines = dict(alerts.digest_sections({"ticket_watch": tw}))[
        "Every issue, and what is still open"]
    assert any("Slow index" in x for x in lines)


# ── What the owner reads ─────────────────────────────────────────────────

def test_rendered_samples_of_the_three_kinds(db, capsys):
    from routes.newsletter_content import plain_text
    db.open(861, T0)
    db.fail(861, T0 + 2 * MIN, user=2846)
    db.claim(861, T0 + 4 * MIN)
    db.live(861, T0 + 40 * MIN)
    db.open(862, T0)
    db.beat(T0 - 30 * MIN)
    got = run(db, T0, T0 + 60 * MIN, step=2 * MIN, beat=False)
    db.tickets[861]["root_cause"] = ("the final render sized its memory by "
                                     "the host's RAM, not the container's")
    db.add(861, ev("fixed", T0 + 70 * MIN, fix_sha=SHA,
                   replay={"session": "p5302", "run": "r1"}), "fixed")
    got += run(db, T0 + 70 * MIN, T0 + 80 * MIN, step=2 * MIN, beat=False)
    with capsys.disabled():
        for _, subject, body in got:
            print(f"\n──── {subject.split(':')[0]} ────\nSubject: {subject}\n"
                  f"{plain_text(body)}")
    assert [s.split(":")[0] for s in subjects(got)] == ["Issue", "Blocked",
                                                        "Fixed"]
