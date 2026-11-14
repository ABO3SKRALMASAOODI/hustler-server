"""Issues are seen before the owner sees them (owner, 7 Nov 2026). Refusals read
as they land, a paying customer out of credits said at once, and each ticket's
time to claim and to live. Real PostgreSQL through intake.run, as
test_ticket_truth does, with project 5520's own rows replayed tick by tick.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg2.extras import Json

from test_ticket_truth import MIN, URL, world         # noqa: F401
import cockpit_alerts as cockpit                      # noqa: E402
from ops_loop import intake, paid_out, recovery, refusals, store, views   # noqa: E402

pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "p5520_refusals.json")
SEC = timedelta(seconds=1)
USER, PROJECT = 2837, 5499              # a free account: plan changes nothing
SESSION = 1
PAYER, PAID_PROJECT = 2123, 5498        # active subscription
NO_CUT = "REJECTED: this program has no cut to decorate."


def call(w, tool, said=None, at=None, session=SESSION, source="agent", id_=None):
    status = "correction_needed" if said else "success"
    w.c.execute(
        """INSERT INTO chat_messages (id, session_id, role, content, meta,
                                      created_at)
           VALUES (COALESCE(%s, nextval('chat_messages_id_seq')), %s, 'activity',
                   %s, %s, %s) RETURNING id""",
        (id_, session, f'{tool}{{"text": "the customer words"}} → {said or "ok"}',
         Json({"tool": tool, "source": source,
               "tool_outcome": {"status": status}}),
         (at or w.clock.now).astimezone(timezone.utc).replace(tzinfo=None)))
    return w.c.fetchone()["id"]


def turn(w, project=PROJECT, user=USER, state="running", result=None, age=60,
         payload=None):
    return w.job("agent_turn", state=state, project=project, user=user, age=age,
                 result=result, payload=payload)


def tickets(w):
    w.c.execute("SELECT * FROM tickets ORDER BY id")
    return w.c.fetchall()


def storm_tickets(w):
    return [t for t in tickets(w) if t["cause_key"].startswith("auto-refusal-")]


def only_storm(w):
    got = storm_tickets(w)
    assert len(got) == 1, [t["cause_key"] for t in got]
    return got[0]


def close_fixed(world, key):
    world.c.execute("""UPDATE tickets SET status = 'fixed', closed_at = %s,
                           root_cause = 'x', fix_sha = 'abc1234',
                           regression_check = 't.py'
                        WHERE cause_key = %s""", (world.clock.now, key))


# ── Refusals, while the turn runs ────────────────────────────────────────

def test_no_ticket_on_one_refusal(world):
    turn(world)
    call(world, "set_junction", NO_CUT)
    world.tick()
    assert storm_tickets(world) == []


def test_twice_in_a_row_is_a_ticket_within_the_minute_on_any_plan(world):
    turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.tick()
    t = only_storm(world)
    assert (t["severity"], t["status"]) == ("P1", "open")
    assert "set_junction refused twice in a row" in t["title"]
    assert "no cut to decorate" in t["title"]
    assert "customer words" not in json.dumps(t["history"]) + t["title"]


def test_the_third_raises_it_to_p0_while_the_turn_still_runs(world):
    job = turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.tick()
    call(world, "set_junction", NO_CUT)
    world.tick()
    t = only_storm(world)
    assert t["severity"] == "P0"
    assert [h["event"] for h in t["history"]].count("escalated") == 1
    world.c.execute("SELECT state FROM video_jobs WHERE id = %s", (job,))
    assert world.c.fetchone()["state"] == "running"
    assert [p["id"] for p in intake.pending_p0(world.c, world.clock.now)] == [t["id"]]


def test_a_paying_customer_is_judged_the_same(world):
    turn(world, project=PAID_PROJECT, user=PAYER)
    for _ in range(3):
        call(world, "add_zoom", "REJECTED: a zoom target needs BOTH cx and cy.",
             session=2)
    world.tick()
    assert only_storm(world)["severity"] == "P0"


def test_a_success_of_that_tool_ends_the_streak(world):
    turn(world)
    call(world, "add_zoom", "REJECTED: a zoom target needs BOTH cx and cy.")
    call(world, "add_zoom")
    call(world, "add_zoom", "REJECTED: a zoom target needs BOTH cx and cy.")
    world.tick()
    assert storm_tickets(world) == []


def test_another_tool_working_does_not_end_it(world):
    turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "get_edl")
    call(world, "set_junction", NO_CUT)
    world.tick()
    assert only_storm(world)["severity"] == "P1"


def test_a_request_refused_again_counts_across_requests(world):
    """The colours in 5520: refused in one request, asked again, refused again."""
    said = "REJECTED (EDL v20 unchanged): this proposal changes color grade"
    turn(world)
    call(world, "set_color_grade", said)
    world.tick()
    turn(world)
    world.ticks(7)
    call(world, "set_color_grade", said.replace("20", "23"))
    world.tick()
    assert only_storm(world)["severity"] == "P1"


def test_a_streak_from_another_sitting_does_not_count(world):
    call(world, "set_junction", NO_CUT, at=world.clock.now - timedelta(hours=7))
    call(world, "set_junction", NO_CUT)
    world.tick()
    assert storm_tickets(world) == []


def test_one_cause_in_two_projects_is_one_ticket(world):
    turn(world)
    turn(world, project=PAID_PROJECT, user=PAYER)
    for session in (1, 2, 1, 2):
        call(world, "set_junction", NO_CUT, session=session)
    world.tick()
    t = only_storm(world)
    assert len({h.get("project") for h in t["history"]
                if h["event"] == "failure"}) == 2


def test_each_threshold_is_filed_once(world):
    turn(world)
    for _ in range(5):
        call(world, "set_junction", NO_CUT)
    world.ticks(4)
    t = only_storm(world)
    assert sum(1 for h in t["history"] if h["event"] == "failure") == 2


def test_an_operator_repair_is_a_ticket_and_pages_nobody(world):
    turn(world, payload={"operator_repair": "batch"})
    for _ in range(3):
        call(world, "set_junction", NO_CUT)
    world.tick()
    assert only_storm(world)["severity"] == "P1"


def test_mirror_rows_of_an_mcp_call_are_not_studio_refusals(world):
    for _ in range(3):
        call(world, "set_junction", NO_CUT, source="mcp")
    world.tick()
    assert storm_tickets(world) == []


def test_a_request_that_ended_short_raises_the_streak_at_once(world):
    job = turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.tick()
    assert only_storm(world)["severity"] == "P1"
    world.clock.now += MIN
    world.c.execute(
        """UPDATE video_jobs SET state = 'done', updated_at = %s, result = %s
            WHERE id = %s""",
        (world.clock.now, Json({"outcome": "partial", "status": "replied"}), job))
    world.tick()
    assert only_storm(world)["severity"] == "P0"


def test_a_question_to_the_customer_is_not_an_end(world):
    job = turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.clock.now += MIN
    world.c.execute(
        """UPDATE video_jobs SET state = 'done', updated_at = %s, result = %s
            WHERE id = %s""",
        (world.clock.now, Json({"outcome": "blocked", "status": "awaiting_user"}),
         job))
    world.tick()
    assert only_storm(world)["severity"] == "P1"


def test_a_later_request_does_not_raise_an_old_streak(world):
    turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.ticks(2)
    turn(world, state="done", age=30, result={"outcome": "partial",
                                               "status": "replied"})
    world.tick()
    assert only_storm(world)["severity"] == "P1"


def test_a_ticket_that_closed_comes_back_with_the_refusal(world):
    turn(world)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.tick()
    key = only_storm(world)["cause_key"]
    close_fixed(world, key)
    world.ticks(2)
    call(world, "set_junction", NO_CUT)
    call(world, "set_junction", NO_CUT)
    world.tick()
    t = only_storm(world)
    assert (t["status"], t["recurrence_count"]) == ("open", 1)


def test_a_broken_reader_files_itself_and_costs_nothing_else(world, monkeypatch):
    def boom(cur, since, now, p):
        raise RuntimeError("no such thing")
    monkeypatch.setattr(refusals, "scan", boom)
    world.event("upload_failed", reason="a reason", bytes=10, kind="original")
    world.tick()
    keys = [t["cause_key"] for t in tickets(world)]
    assert any("check-broken" in k for k in keys)
    assert any(k.startswith("auto-upload-failed") for k in keys)


# ── MCP: no request and no project on the row ────────────────────────────

def mcp_error(w, tool="upload_start", outcome="correction_needed",
              category="invalid_request", user=USER):
    return w.event("mcp_error_response", user=user, project=None, tool=tool,
                   outcome=outcome, category=category)


def test_mcp_twice_is_a_ticket_and_the_gateway_rows_count(world):
    mcp_error(world)
    mcp_error(world)
    world.tick()
    t = only_storm(world)
    assert t["severity"] == "P1"
    assert "MCP upload_start refused twice" in t["title"]


def test_mcp_the_third_is_looked_at_and_pages_when_nothing_worked(world):
    for _ in range(3):
        mcp_error(world)
    world.tick()
    assert only_storm(world)["severity"] == "P1"
    world.ticks(11)
    assert only_storm(world)["severity"] == "P0"


def test_mcp_an_assistant_that_got_on_with_it_pages_nobody(world):
    for _ in range(3):
        mcp_error(world)
    world.tick()
    world.job("mcp_tool", state="done", user=USER, age=0,
              payload={"tool": "upload_start"},
              result={"tool_outcome": {"status": "success"}})
    world.ticks(11)
    assert only_storm(world)["severity"] == "P1"


def test_mcp_a_success_ends_the_streak(world):
    mcp_error(world)
    world.clock.now += MIN
    world.job("mcp_tool", state="done", user=USER, age=0,
              payload={"tool": "upload_start"},
              result={"tool_outcome": {"status": "success"}})
    world.clock.now += MIN
    mcp_error(world)
    world.tick()
    assert storm_tickets(world) == []


def test_mcp_retries_a_day_apart_are_not_a_storm(world):
    mcp_error(world)
    world.clock.now += timedelta(minutes=31)
    mcp_error(world)
    world.tick()
    assert storm_tickets(world) == []


def test_mcp_errors_of_ours_keep_their_own_ticket(world):
    for _ in range(3):
        mcp_error(world, outcome="transient_failure", category="timeout")
    world.tick()
    assert storm_tickets(world) == []


# ── A paying customer out of credits ─────────────────────────────────────

def paying(w, balance=0, user=PAYER):
    w.c.execute("""UPDATE users SET credits_balance = %s, plan = 'pro_2026',
                       credits_monthly_limit = 1500 WHERE id = %s""",
                (balance, user))


def ended(w, status="budget", outcome="partial", user=PAYER,
          project=PAID_PROJECT, charged=169.36):
    return turn(w, project=project, user=user, state="done", result={
        "outcome": outcome, "status": status, "credits_charged": charged})


def test_paid_out_is_p0_at_once_with_no_second_look(world):
    paying(world)
    job = ended(world)
    world.tick()
    t = world.ticket(paid_out.CAUSE_KEY)
    assert (t["severity"], t["status"]) == ("P0", "open")
    assert f"credits:{job}" in json.dumps(t["history"])
    assert "0 of 1,500 credits left" in json.dumps(t["history"])
    assert [p["id"] for p in intake.pending_p0(world.c, world.clock.now)] == [t["id"]]


def test_paid_out_counts_an_unfulfilled_turn_whatever_stopped_it(world):
    paying(world)
    ended(world, status="replied")
    world.tick()
    assert world.ticket(paid_out.CAUSE_KEY)["severity"] == "P0"


def test_credits_left_is_not_paid_out(world):
    paying(world, balance=250)
    ended(world, status="replied")
    world.tick()
    assert world.ticket(paid_out.CAUSE_KEY) is None


def test_only_the_latest_turn_speaks_for_todays_balance(world):
    paying(world, balance=500)
    ended(world, status="replied", charged=300)
    world.ticks(2)
    world.c.execute("UPDATE users SET credits_balance = 0 WHERE id = %s", (PAYER,))
    job = ended(world)
    world.tick()
    t = world.ticket(paid_out.CAUSE_KEY)
    assert [h["ref"] for h in t["history"] if h["event"] == "failure"] == [
        f"credits:{job}"]


def test_a_fulfilled_turn_is_not_paid_out(world):
    paying(world)
    ended(world, status="replied", outcome="fulfilled")
    world.tick()
    assert world.ticket(paid_out.CAUSE_KEY) is None


def test_the_trials_own_limit_stays_a_budget_stop(world):
    ended(world, user=USER, project=PROJECT)
    world.tick()
    assert world.ticket(paid_out.CAUSE_KEY) is None
    assert world.ticket("auto-request-partial-budget")["severity"] == "P2"


def test_a_slice_handing_over_is_not_an_end(world):
    paying(world)
    turn(world, project=PAID_PROJECT, user=PAYER, state="done", result={
        "outcome": "partial", "status": "budget", "continued_job_id": "9"})
    world.tick()
    assert world.ticket(paid_out.CAUSE_KEY) is None


def alert_tick(world, monkeypatch, sent):
    monkeypatch.setattr(cockpit, "maybe_digest", lambda *a: False)

    def send(subject, body):
        sent.append((subject, body))
        return True
    return cockpit.run_tick(now=world.clock.now, send=send, rconn=world.conn,
                            wconn=world.conn)


def test_the_owner_hears_it_once_and_it_is_only_the_facts(world, monkeypatch):
    paying(world)
    ended(world)
    world.tick()
    sent = []
    alert_tick(world, monkeypatch, sent)
    alert_tick(world, monkeypatch, sent)
    assert len(sent) == 1
    subject, body = sent[0]
    assert "ran out of credits" in body
    assert "account #2123" in body and "0 of 1,500 credits left" in body
    from admin_metrics import cockpit as board
    assert not board.BILLING_ADVICE.search(body)


def test_the_daily_email_limit_cannot_swallow_it(world, monkeypatch):
    world.c.execute("INSERT INTO app_kv (key, value) VALUES (%s, %s)",
                    (cockpit._count_key(world.clock.now),
                     json.dumps({"n": cockpit.MAX_ALERT_EMAILS_PER_DAY})))
    paying(world)
    ended(world)
    world.tick()
    sent = []
    alert_tick(world, monkeypatch, sent)
    assert len(sent) == 1
    assert "ran out of credits" in sent[0][1]


def test_the_daily_limit_holds_system_owned_p0_mail(world, monkeypatch):
    """Only provider blockers bypass the cap; the watcher owns these P0s."""
    t = open_p0(world)
    other = open_p0(world, key="auto-job-final-oom")
    store.set_fixing(world.c, other["id"], "on it")
    world.c.execute("INSERT INTO app_kv (key, value) VALUES (%s, %s)",
                    (cockpit._count_key(world.clock.now),
                     json.dumps({"n": cockpit.MAX_ALERT_EMAILS_PER_DAY})))
    world.clock.now += 11 * MIN
    sent = []
    alert_tick(world, monkeypatch, sent)
    assert sent == []
    assert t["id"] in pending_ids(world)  # still tracked, not swallowed
    world.c.execute("SELECT count(*) AS n FROM app_kv WHERE key LIKE %s",
                    (f"cockpit_alert:p0:{other['id']}:%",))
    assert world.c.fetchone()["n"] == 0


def test_a_failed_send_comes_back_on_the_next_tick(world, monkeypatch):
    paying(world)
    ended(world)
    world.tick()
    monkeypatch.setattr(cockpit, "maybe_digest", lambda *a: False)
    cockpit.run_tick(now=world.clock.now, send=lambda s, b: False,
                     rconn=world.conn, wconn=world.conn)
    world.clock.now += 5 * MIN
    sent = []
    alert_tick(world, monkeypatch, sent)
    assert len(sent) == 1


# ── The backend agrees with the watch ────────────────────────────────────

def open_p0(world, key="auto-job-index-oom"):
    t, _ = store.record_failure(world.c, {
        "ref": f"job:{world.clock.now.timestamp()}", "at": world.clock.now,
        "source": "job", "user_id": USER, "project_id": PROJECT, "severity": "P0",
        "cause_key": key, "title": "x", "detail": "y"})
    return t


def unhandled_ids(world):
    return [t["id"] for t in intake.unhandled(world.c, world.clock.now)]


def pending_ids(world):
    return [t["id"] for t in intake.pending_p0(world.c, world.clock.now)]


def test_unclaimed_for_10_minutes_is_the_email(world):
    t = open_p0(world)
    assert pending_ids(world) == [t["id"]]
    world.clock.now += 9 * MIN
    assert unhandled_ids(world) == []
    world.clock.now += MIN
    assert unhandled_ids(world) == [t["id"]]


def test_a_claim_within_the_10_minutes_is_no_email(world):
    t = open_p0(world)
    world.clock.now += 4 * MIN
    store.set_fixing(world.c, t["id"], "on it")
    world.clock.now += 30 * MIN
    assert unhandled_ids(world) == pending_ids(world) == []


def release(world, t):
    store.set_fixing(world.c, t["id"])
    world.c.execute("SELECT updated_at FROM tickets WHERE id = %s", (t["id"],))
    store.release_fixing(world.c, t["id"],
                         world.c.fetchone()["updated_at"].isoformat(),
                         "waiting on the release")


def test_a_handed_back_ticket_is_the_watchs_again_and_never_the_owners(world):
    """The quiet ending hands it back to the watch. Nobody failed on it after
    the release, so no customer is blocked and unhandled (#387, 7 Nov)."""
    t = open_p0(world)
    release(world, t)
    world.clock.now += intake.RELEASE_QUIET - MIN
    assert pending_ids(world) == unhandled_ids(world) == []
    world.clock.now += 2 * MIN
    assert pending_ids(world) == [t["id"]]
    world.clock.now += 9 * MIN
    assert unhandled_ids(world) == []


def test_a_new_failure_on_a_handed_back_ticket_starts_at_that_failure(world):
    t = open_p0(world)
    release(world, t)
    world.clock.now += 30 * MIN
    open_p0(world)
    assert pending_ids(world) == [t["id"]]
    assert unhandled_ids(world) == []
    world.clock.now += 5 * MIN
    open_p0(world)          # more customers do not make it look fresher
    world.clock.now += 5 * MIN
    assert unhandled_ids(world) == [t["id"]]


def test_pending_and_unhandled_name_the_same_tickets_once_the_wait_is_over(world):
    plain = open_p0(world, "auto-job-a-oom")
    handed = open_p0(world, "auto-job-b-oom")
    release(world, handed)
    world.clock.now += intake.RELEASE_QUIET + 11 * MIN
    assert sorted(pending_ids(world)) == sorted([plain["id"], handed["id"]])
    assert unhandled_ids(world) == [plain["id"]]


def test_a_claim_gone_stale_is_on_both_lists_at_the_same_moment(world):
    t = open_p0(world)
    store.set_fixing(world.c, t["id"])
    world.clock.now += 30 * MIN
    open_p0(world)          # a customer failed on it after the claim
    world.clock.now += intake.FIXING_STALE - 31 * MIN
    assert pending_ids(world) == unhandled_ids(world) == []
    world.clock.now += 2 * MIN
    assert pending_ids(world) == unhandled_ids(world) == [t["id"]]


def test_the_digest_uses_the_same_answer(world):
    t = open_p0(world)
    release(world, t)
    world.clock.now += 11 * MIN
    assert unhandled_ids(world) == []
    got = views.failures_between(world.c, world.clock.now - timedelta(days=1),
                                 world.clock.now, world.clock.now)
    assert got["unhandled"] == []


def test_a_silent_watch_is_late_after_three_runs_missed(world):
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": (world.clock.now - 19 * MIN).isoformat()})
    assert "p0_watch" not in [a["agent"] for a in
                              intake.silent_agents(world.c, world.clock.now)]
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": (world.clock.now - 21 * MIN).isoformat()})
    assert "p0_watch" in [a["agent"] for a in
                          intake.silent_agents(world.c, world.clock.now)]


def test_a_live_watch_gets_issue_mail_and_a_silent_watch_one_agent_notice(world):
    open_p0(world)
    world.clock.now += 11 * MIN
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": (world.clock.now - 3 * MIN).isoformat()})
    lines = [i[2] for i in cockpit.event_alerts(world.c, world.clock.now,
                                                world.clock.now)]
    assert any("The P0 watch starts a Fixer" in x for x in lines)
    store.put_state(world.c, "ops_heartbeat_p0_watch",
                    {"at": (world.clock.now - 40 * MIN).isoformat()})
    lines = [i[2] for i in cockpit.event_alerts(world.c, world.clock.now,
                                                world.clock.now)]
    assert any("Mac asleep" in x for x in lines)
    assert not any("P0 #" in x for x in lines)


# ── Time to claim and time to live ───────────────────────────────────────

def history(world, tid):
    world.c.execute("SELECT * FROM tickets WHERE id = %s", (tid,))
    return world.c.fetchone()["history"]


def test_time_to_claim_and_to_live_are_stored_on_the_ticket(world):
    t = open_p0(world)
    world.clock.now += 4 * MIN
    store.set_fixing(world.c, t["id"])
    world.clock.now += 26 * MIN
    store.mark_live(world.c, t["id"], "abc1234")
    h = history(world, t["id"])
    assert [x for x in h if x["event"] == "fixing"][0]["claim_s"] == 240
    assert [x for x in h if x["event"] == "live"][0]["live_s"] == 1800
    c = store.clock(h)
    assert (c["claim_s"], c["live_s"]) == (240, 1800)
    assert "open_s" not in store.clock(h, world.clock.now)


def test_a_p1_raised_to_p0_is_timed_from_the_raising(world):
    t, _ = store.record_failure(world.c, {
        "ref": "a", "at": world.clock.now, "source": "job", "user_id": USER,
        "project_id": PROJECT, "severity": "P1", "cause_key": "auto-x-y",
        "title": "x", "detail": "y"})
    world.clock.now += 30 * MIN
    store.record_failure(world.c, {
        "ref": "b", "at": world.clock.now, "source": "job", "user_id": USER,
        "project_id": PROJECT, "severity": "P0", "cause_key": "auto-x-y",
        "title": "x", "detail": "y"})
    world.clock.now += 2 * MIN
    store.set_fixing(world.c, t["id"])
    assert store.clock(history(world, t["id"]))["claim_s"] == 120


def test_a_hand_back_never_stops_the_customers_clock(world):
    t = open_p0(world)
    release(world, t)
    world.clock.now += 3 * 60 * MIN
    store.mark_live(world.c, t["id"], "abc1234")
    assert store.clock(history(world, t["id"]))["live_s"] == 3 * 3600


def test_the_latest_fix_counts_and_a_recurrence_is_a_new_clock(world):
    t = open_p0(world)
    world.clock.now += 10 * MIN
    store.mark_live(world.c, t["id"], "aaa1111")
    world.clock.now += 20 * MIN
    store.mark_live(world.c, t["id"], "bbb2222")
    assert store.clock(history(world, t["id"]))["live_s"] == 1800
    close_fixed(world, t["cause_key"])
    world.clock.now += 5 * MIN
    open_p0(world)
    c = store.clock(history(world, t["id"]), world.clock.now)
    assert "live_s" not in c and c["open_s"] == 0


def test_a_closing_with_no_live_mark_is_only_a_bound(world):
    h = [{"event": "opened", "at": "2026-11-06T12:00:00+00:00"},
         {"event": "fixed", "at": "2026-11-06T13:00:00+00:00"}]
    assert store.clock(h) == {"opened_at": "2026-11-06T12:00:00+00:00",
                              "live_at": "2026-11-06T13:00:00+00:00",
                              "live_s": 3600, "live_is_closing": True}
    h.append({"event": "live", "at": "2026-11-06T12:30:00+00:00"})
    assert "live_is_closing" not in store.clock(h)


def test_the_clock_is_seen_through_the_cli_and_the_admin_view(world, monkeypatch):
    monkeypatch.setattr(views, "tables_ready", lambda cur: True)
    t = open_p0(world)
    world.clock.now += 4 * MIN
    store.set_fixing(world.c, t["id"])
    got = views.clocks(world.c, now=world.clock.now)
    assert got["p0"]["claim"] == {"n": 1, "p50_s": 240, "p90_s": 240,
                                  "max_s": 240}
    row = got["tickets"][0]
    assert row["clock"]["claim_s"] == 240
    assert views.clocks(world.c, ticket=t["id"],
                        now=world.clock.now)["tickets"][0]["id"] == t["id"]
    admin = views.tickets(world.c, now=world.clock.now)
    shown = [x for g in admin["groups"] for x in g["tickets"]][0]
    assert shown["clock"]["claim_s"] == 240


def test_a_fix_that_is_not_in_production_is_not_live(monkeypatch):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts",
                        "ops_review.py")
    spec = importlib.util.spec_from_file_location("ops_review_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "live_commit", lambda: "abcdef123456")

    class Done:
        def __init__(self, rc):
            self.returncode = rc
    monkeypatch.setattr(cli, "_git", lambda *a: Done(
        1 if a[0] == "merge-base" else 0))
    with pytest.raises(cli.CliError):
        cli.check_live("1234567")
    assert cli.check_live("fe:1234567") is None
    monkeypatch.setattr(cli, "_git", lambda *a: Done(0))
    assert cli.check_live("1234567,fe:89abcde") == "abcdef123456"
    assert {"live", "clock"} <= set(
        cli.parser()._subparsers._group_actions[0].choices)


# ── Project 5520, as its rows landed ─────────────────────────────────────

def stamp(text):
    v = datetime.fromisoformat(text)
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class Replay:
    """Lands 5520's rows at their own times, a tick a minute as the web process
    does, and the payment at 17:13 with its 1,500 credits."""

    def __init__(self, w):
        self.w = w
        self.data = json.load(open(FIXTURE, encoding="utf-8"))
        self.pid, self.uid = self.data["project_id"], self.data["user_id"]
        w.c.execute("INSERT INTO users (id, email) VALUES (%s, 'p@example.com')",
                    (self.uid,))
        w.c.execute("INSERT INTO projects VALUES (%s, %s, 55)",
                    (self.pid, self.uid))
        self.calls = sorted(self.data["calls"], key=lambda c: c["id"])
        self.turns = self.data["turns"]
        self.started, self.finished = set(), set()
        self.paid, self.balance = False, 0.0
        self.first = {}             # ref prefix -> first tick that filed it

    def land(self, now):
        c = self.w.c
        if not self.paid and now >= stamp("2026-11-06T17:13:00+00:00"):
            self.paid, self.balance = True, 1500.0
            c.execute("""UPDATE users SET is_subscribed = 1, plan = %s,
                             billing_status = 'active', credits_balance = 1500,
                             credits_monthly_limit = %s WHERE id = %s""",
                      (self.data["plan"], self.data["credits_monthly_limit"],
                       self.uid))
        while self.calls and stamp(self.calls[0]["at"]) <= now:
            r = self.calls.pop(0)
            c.execute(
                """INSERT INTO chat_messages (id, session_id, role, content, meta,
                       created_at) VALUES (%s, 55, 'activity', %s, %s, %s)""",
                (r["id"], f"{r['tool']}() → {r.get('said', 'ok')}",
                 Json({"tool": r["tool"], "source": "agent",
                       "tool_outcome": {"status": r["status"]}}),
                 stamp(r["at"]).replace(tzinfo=None)))
        for t in self.turns:
            if t["id"] not in self.started and stamp(t["created_at"]) <= now:
                self.started.add(t["id"])
                c.execute(
                    """INSERT INTO video_jobs (id, project_id, user_id, type,
                           state, payload, result, created_at, updated_at)
                       VALUES (%s, %s, %s, 'agent_turn', 'running', %s, '{}', %s,
                               %s)""",
                    (t["id"], self.pid, self.uid,
                     Json({"root_agent_job_id": t["root"]}),
                     stamp(t["created_at"]), stamp(t["created_at"])))
            if t["id"] in self.started and t["id"] not in self.finished and \
                    stamp(t["updated_at"]) <= now:
                self.finished.add(t["id"])
                result = {"outcome": t["outcome"], "status": t["status"],
                          "credits_charged": t["charged"]}
                if t["continued"]:
                    result["continued_job_id"] = t["continued"]
                if self.paid:
                    self.balance = max(0.0, self.balance
                                       - float(t["charged"] or 0))
                c.execute("""UPDATE video_jobs SET state = 'done', result = %s,
                                 updated_at = %s WHERE id = %s""",
                          (Json(result), stamp(t["updated_at"]), t["id"]))
                c.execute("UPDATE users SET credits_balance = %s WHERE id = %s",
                          (self.balance, self.uid))

    def run(self, start, end):
        now = start
        while now <= end:
            self.land(now)
            self.w.tick(now)
            now += MIN


@pytest.fixture
def replay(world):                          # noqa: F811
    r = Replay(world)
    r.run(stamp("2026-11-06T16:26:00+00:00"), stamp("2026-11-06T19:10:00+00:00"))
    return r


def filed_at(world, key=None, severity=None, prefix="auto-refusal-"):
    """The earliest tick a ticket of this kind reached this severity."""
    best = None
    for t in tickets(world):
        if not t["cause_key"].startswith(prefix):
            continue
        for h in t["history"]:
            if h["event"] == "opened" and (not severity
                                           or h.get("severity") == severity):
                at = store._event_at(h)
            elif h["event"] == "escalated" and severity == "P0":
                at = store._event_at(h)
            else:
                continue
            best = at if best is None or at < best else best
    return best


FIRST_REFUSAL = datetime(2026, 11, 6, 16, 27, 38, tzinfo=timezone.utc)


def test_5520_replayed_ticket_at_the_second_refusal_p0_at_the_third(world, replay):
    first = store._event_at(json.loads(json.dumps({"at": min(
        c["at"] for c in replay.data["calls"]
        if c["status"] != "success")})))
    assert first.replace(microsecond=0) == FIRST_REFUSAL
    ticket = filed_at(world)
    p0 = filed_at(world, severity="P0")
    print(f"\n5520: first refusal 16:27:38 UTC · ticket {ticket:%H:%M:%S} · "
          f"P0 {p0:%H:%M:%S}")
    assert ticket == datetime(2026, 11, 6, 16, 28, tzinfo=timezone.utc)
    assert p0 == datetime(2026, 11, 6, 16, 29, tzinfo=timezone.utc)
    assert (p0 - FIRST_REFUSAL).total_seconds() <= 90


def test_5520_replayed_names_what_the_customer_asked_for_being_refused(world, replay):
    got = storm_tickets(world)
    filed = json.dumps([x["history"] for x in got])
    for tool in ("set_junction", "add_zoom", "set_color_grade", "set_grade_custom",
                 "set_transitions"):
        assert f"{tool} refused" in filed
    assert "no cut to decorate" in filed
    print("\n" + "\n".join(f"{x['severity']} {x['title'][:110]}" for x in got))


def test_5520_replayed_the_paid_out_page_lands_within_the_minute(world, replay,
                                                                 monkeypatch):
    t = world.ticket(paid_out.CAUSE_KEY)
    assert t["severity"] == "P0"
    filed = [h for h in t["history"] if h["event"] == "opened"][0]
    assert store._event_at(filed) == datetime(2026, 11, 6, 19, 9,
                                              tzinfo=timezone.utc)
    detail = [h for h in t["history"] if h["event"] == "failure"][0]["detail"]
    assert "0 of 1,500 credits left" in detail
    assert "6 paid turns" in detail and "6 ended unfulfilled" in detail
    assert "project 5520" in detail
    sent = []
    alert_tick(world, monkeypatch, sent)
    assert sum("ran out of credits" in b for _, b in sent) == 1
    print(f"\n5520: {detail}")


def test_5520_replayed_no_fixer_is_paged_twice_for_one_cause(world, replay):
    keys = [t["cause_key"] for t in storm_tickets(world)]
    assert len(keys) == len(set(keys))
    bounded = [k for k in keys if "bounded-edit" in k]
    assert len(bounded) == 1        # 8 tools said it, one gate


def test_recovery_leaves_a_refusal_alone(world):
    f = {"ref": "refusal:1:3", "source": "refusal", "severity": "P0",
         "at": world.clock.now, "cause_key": "auto-refusal-x"}
    assert recovery.plan(f) == ("P0", None)
