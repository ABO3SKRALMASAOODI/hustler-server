"""What hurts a customer and writes no failure row (audit-invisible): each gap
check on real PostgreSQL, on the rows the customers' cases actually left.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import os
from datetime import timedelta

import pytest
from psycopg2.extras import Json

from test_ticket_truth import MIN, T0, URL, world    # noqa: F401
from ops_loop import gaps, intake, recovery, store    # noqa: E402

pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")


def fired(world, check, minutes=1):      # noqa: F811
    now = world.clock.now
    return check(world.c, now - minutes * MIN, now, {
        "since": now - minutes * MIN, "now": now, "internal": [], "limit": 100})


def at_deadline(world, delay):           # noqa: F811
    world.clock.now += delay + timedelta(seconds=1)


def index_job(world, state, seconds, age, updated_age=0):    # noqa: F811
    world.c.execute("INSERT INTO assets (project_id, kind, duration_s, bytes) "
                    "VALUES (5499, 'original', %s, 1000) RETURNING id", (seconds,))
    asset = world.c.fetchone()["id"]
    jid = world.job("index", state=state, age=age,
                    payload={"asset_id": str(asset)})
    world.c.execute("UPDATE video_jobs SET updated_at = %s WHERE id = %s",
                    (world.clock.now - timedelta(seconds=updated_age), jid))
    return jid


def test_analysis_past_its_deadline_fires_while_it_is_open(world):    # noqa: F811
    """Job 130284: a 5 s clip took 3,569 s for 36 s of work."""
    jid = index_job(world, "queued", 5, 600)
    got = fired(world, gaps._analysis_overdue)
    assert [f["ref"] for f in got] == [f"gap:analysis:{jid}"]
    assert got[0]["cause_key"] == "auto-gap-analysis-overdue-queued"
    assert got[0]["severity"] == "P0" and got[0]["job_id"] == jid


def test_a_long_source_has_a_longer_deadline(world):    # noqa: F811
    index_job(world, "running", 3600, 700)
    assert fired(world, gaps._analysis_overdue) == []
    world.clock.now += timedelta(seconds=4700)
    assert len(fired(world, gaps._analysis_overdue)) == 1


def test_analysis_that_finished_in_time_never_fires(world):    # noqa: F811
    index_job(world, "done", 5, 600, updated_age=300)
    assert fired(world, gaps._analysis_overdue) == []


def test_an_overdue_analysis_that_finishes_is_not_paged(world):    # noqa: F811
    jid = index_job(world, "running", 5, 599)
    world.ticks(2)
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(3)
    world.c.execute("UPDATE video_jobs SET state = 'done', updated_at = %s "
                    "WHERE id = %s", (world.clock.now, jid))
    world.ticks(10)
    assert world.only_ticket()["severity"] == "P1"


def test_an_analysis_that_stays_stuck_pages(world):    # noqa: F811
    index_job(world, "queued", 5, 599)
    world.ticks(13)
    assert world.only_ticket()["severity"] == "P0"


def test_an_export_that_never_arrived_fires(world):    # noqa: F811
    """Paying account 2818: render done 05:42:07, no download followed."""
    eid = world.event("export_render_done", user=2123, project=5498,
                      job_id=137173, version=15)
    at_deadline(world, gaps.EXPORT_WAIT)
    got = fired(world, gaps._export_never_arrived)
    assert [f["ref"] for f in got] == [f"gap:export:{eid}"]
    assert got[0]["cause_key"] == "auto-gap-export-no-download"


@pytest.mark.parametrize("arrived", ["download_url_ready", "mcp_export_delivered"])
def test_an_export_that_arrived_is_silent(world, arrived):    # noqa: F811
    world.event("export_render_done", user=2123, project=5498)
    world.event(arrived, user=2123, project=5498)
    at_deadline(world, gaps.EXPORT_WAIT)
    assert fired(world, gaps._export_never_arrived) == []


def test_a_checkout_failure_files_by_stage(world):    # noqa: F811
    """2821 and 2824: setup_failed on Pro, never paid, 0 tickets."""
    eid = world.event("checkout_stage", user=2846, project=None,
                      plan="pro_2026", stage="setup_failed", billing="monthly")
    got = fired(world, gaps._checkout)
    assert [f["ref"] for f in got] == [f"gap:checkout:{eid}"]
    assert got[0]["cause_key"] == "auto-gap-checkout-setup-failed"
    assert got[0]["severity"] == "P0"


def test_a_declined_card_never_pages(world):    # noqa: F811
    world.event("checkout_stage", user=2846, project=None, stage="payment_failed")
    assert fired(world, gaps._checkout)[0]["severity"] == "P1"


def test_a_checkout_that_never_loaded_fires(world):    # noqa: F811
    """2765, 2790, 2539: opened, never loaded."""
    eid = world.event("checkout_stage", user=2846, project=None, stage="opened",
                      plan="max_2026")
    at_deadline(world, gaps.CHECKOUT_WAIT)
    got = fired(world, gaps._checkout)
    assert [(f["ref"], f["cause_key"]) for f in got] == \
        [(f"gap:checkout:{eid}", "auto-gap-checkout-not-loaded")]


def test_a_checkout_that_loaded_is_silent(world):    # noqa: F811
    world.event("checkout_stage", user=2846, project=None, stage="opened")
    world.event("checkout_stage", user=2846, project=None, stage="loaded", after=4)
    at_deadline(world, gaps.CHECKOUT_WAIT)
    assert fired(world, gaps._checkout) == []


def turn(world, outcome="fulfilled", payload=None, **result):    # noqa: F811
    return world.job("agent_turn", project=5600, user=2846,
                     result=dict(result, outcome=outcome),
                     payload=dict({"message_id": 1}, **(payload or {})))


def test_a_request_answered_with_nothing_changed_fires(world):    # noqa: F811
    """Chain 134612: "Your cut stays as it is", recorded fulfilled."""
    jid = turn(world, edl_changed=False)
    got = fired(world, gaps._request_changed_nothing)
    assert [f["ref"] for f in got] == [f"gap:nochange:{jid}"]
    assert got[0]["cause_key"] == "auto-gap-request-no-change-fulfilled"
    assert got[0]["severity"] == "P1"


def test_a_request_that_changed_or_rendered_is_silent(world):    # noqa: F811
    turn(world, edl_changed=True)
    assert fired(world, gaps._request_changed_nothing) == []
    world.c.execute("INSERT INTO assets (project_id, kind, created_at) VALUES "
                    "(5600, 'render', %s)", (world.clock.now,))
    turn(world, edl_changed=False)
    assert fired(world, gaps._request_changed_nothing) == []


def test_a_continued_chain_is_judged_at_its_end(world):    # noqa: F811
    turn(world, continued_job_id="9", edl_changed=False)
    assert fired(world, gaps._request_changed_nothing) == []


def test_the_first_cut_that_is_the_upload_fires(world):    # noqa: F811
    jid = turn(world, payload={"first_cut": True}, edl_version=1)
    got = fired(world, gaps._first_cut_is_the_upload)
    assert [f["ref"] for f in got] == [f"gap:firstcut:{jid}"]
    assert got[0]["cause_key"] == "auto-gap-first-cut-is-the-upload"
    assert fired(world, gaps._request_changed_nothing) == []


def test_a_first_cut_that_edited_is_silent(world):    # noqa: F811
    turn(world, payload={"first_cut": True}, edl_version=6)
    assert fired(world, gaps._first_cut_is_the_upload) == []


def test_a_reply_stripped_of_a_failure_fires_by_its_reason_code(world):    # noqa: F811
    jid = turn(world, honesty={"trial_reply_dropped": [
        {"why": "deny", "sentence": "my two attempts were rejected"},
        {"why": "length", "sentence": "tidy"}]})
    got = fired(world, gaps._reply_hid_a_failure)
    assert [f["ref"] for f in got] == [f"gap:hidden:{jid}"]
    assert got[0]["cause_key"] == "auto-gap-reply-hid-failure-deny"


def test_tidying_a_reply_is_not_hiding(world):    # noqa: F811
    turn(world, honesty={"trial_reply_dropped": [{"why": "length", "sentence": "x"}]})
    assert fired(world, gaps._reply_hid_a_failure) == []


def calls(world, purpose, *bad):    # noqa: F811
    for i, b in enumerate(bad):
        world.c.execute(
            "INSERT INTO llm_calls (purpose, response, created_at) VALUES "
            "(%s, %s, %s)", (purpose, Json({"error": "429"} if b else {"ok": 1}),
                             world.clock.now - timedelta(seconds=len(bad) - i)))


def test_three_failed_model_calls_in_a_row_fire_at_once(world):    # noqa: F811
    """24 canned replies to 8 accounts over 39 hours."""
    calls(world, "concierge", False, True, True, True)
    got = fired(world, gaps._model_failing)
    assert got[0]["cause_key"] == "auto-gap-model-failing-concierge"
    assert got[0]["severity"] == "P0"
    assert recovery.plan(got[0], world.c)[0] == "P0", "no grace on a dry provider"


def test_one_success_in_the_last_three_is_no_streak(world):    # noqa: F811
    calls(world, "concierge", True, False, True)
    assert fired(world, gaps._model_failing) == []


def test_a_cancel_within_a_day_of_paying_fires_and_a_later_one_does_not(world):    # noqa: F811
    world.c.execute(
        """INSERT INTO payments (user_id, status, amount_cents, subscription_id,
           occurred_at) VALUES (2123, 'completed', 1500, 'sub_1', %s),
           (2846, 'completed', 1500, 'sub_2', %s)""",
        (world.clock.now.replace(tzinfo=None) - timedelta(minutes=8),
         world.clock.now.replace(tzinfo=None) - timedelta(days=5)))
    world.c.execute(
        """INSERT INTO admin_subscription_history (subscription_id,
           provider_updated_at, status, scheduled_action) VALUES
           ('sub_1', %s, 'active', 'cancel'), ('sub_2', %s, 'active', 'cancel')""",
        (world.clock.now, world.clock.now))
    got = fired(world, gaps._cancelled_within_a_day)
    assert [f["ref"] for f in got] == ["gap:cancel:2123"]
    assert got[0]["severity"] == "P1" and got[0]["project_id"] == 5498


def review(world, score=2, cls="same_upload", defects=(), started=None):    # noqa: F811
    world.c.execute(
        """INSERT INTO session_reviews (session_key, surface, user_id, project_id,
           started_at, settled_at, fingerprint, verdict, score, reasoning,
           signals, reviewed_at)
           VALUES (%s, 'studio', 2846, 5600, %s, %s, 'f', 'delivered_poor', %s,
                   'r', %s, %s) RETURNING id""",
        (f"p5600-{score}", started or world.clock.now, world.clock.now, score,
         Json({"score_exact": score, "edit_delta": {"class": cls},
               "defects": [{"id": d} for d in defects]}), world.clock.now))
    return world.c.fetchone()["id"]


def test_a_rating_below_the_bar_files_by_what_was_measured(world):    # noqa: F811
    rid = review(world)
    at_deadline(world, gaps.REVIEW_SETTLE)
    got = fired(world, gaps._below_the_bar)
    assert got[0]["ref"] == f"review:{rid}:1"
    assert got[0]["cause_key"] == "auto-quality-same-upload"
    assert got[0]["review_id"] == rid and got[0]["severity"] == "P1"


def test_ratings_fold_into_causes_not_one_umbrella(world):    # noqa: F811
    trait = gaps._quality_trait
    r = {"verdict": "delivered_poor"}
    dims = {"edit_delta": {"class": "edited"},
            "dimensions": {"cuts_pacing": 3, "type_captions": 1.5, "sound": None}}
    assert trait(r, dims) == "type_captions"
    assert trait(r, dict(dims, defects=[{"id": "ask_missed"}])) == "ask_missed"
    assert trait(r, {"edit_delta": {"class": "trim_only"}}) == "trim_only"
    assert trait({"verdict": "not_delivered"}, {}) == "not-delivered"
    assert trait(r, {}) == "below-the-bar"


def test_a_rating_at_the_bar_or_already_on_an_open_ticket_files_nothing(world):    # noqa: F811
    review(world, score=10, cls="edited")
    rid = review(world, score=3, cls="edited", defects=["ask_missed"])
    world.c.execute("INSERT INTO tickets (cause_key, title, severity, status, "
                    "first_seen, last_seen) VALUES ('k1', 't', 'P1', 'open', "
                    "%s, %s) RETURNING id", (T0, T0))
    world.c.execute("INSERT INTO ticket_reviews VALUES (%s, %s)",
                    (world.c.fetchone()["id"], rid))
    at_deadline(world, gaps.REVIEW_SETTLE)
    assert fired(world, gaps._below_the_bar) == []


def test_an_honest_rating_reopens_only_what_it_can_disprove(world):    # noqa: F811
    world.closed("auto-quality-same-upload", "fixed", days=1)
    review(world, started=T0 - timedelta(days=3))
    world.tick(world.clock.now + gaps.REVIEW_SETTLE + timedelta(seconds=1))
    assert world.only_ticket()["status"] == "fixed", "the session predates the fix"
    assert world.only_ticket()["occurrences"] == 2
    review(world, score=3)
    world.tick(world.clock.now + gaps.REVIEW_SETTLE + timedelta(seconds=1))
    assert world.only_ticket()["status"] == "open"


def test_a_rating_shows_on_its_ticket(world):    # noqa: F811
    rid = review(world)
    world.tick(world.clock.now + gaps.REVIEW_SETTLE + timedelta(seconds=1))
    world.c.execute("SELECT review_id FROM ticket_reviews")
    assert [r["review_id"] for r in world.c.fetchall()] == [rid]


def test_a_turn_that_goes_silent_fires_and_pages_if_it_stays_silent(world):    # noqa: F811
    world.job("agent_turn", state="running", project=5600, user=2846)
    at_deadline(world, gaps.TURN_SILENCE)
    got = fired(world, gaps._turn_went_silent, minutes=2)
    assert got[0]["cause_key"] == "auto-gap-turn-silent-running"
    world.ticks(13)
    assert world.only_ticket()["severity"] == "P0"


def test_a_turn_that_speaks_again_is_not_paged(world):    # noqa: F811
    world.job("agent_turn", state="running", project=5600, user=2846)
    world.ticks(3)
    world.c.execute("INSERT INTO chat_messages (session_id, role, created_at) "
                    "VALUES (3, 'assistant', %s)",
                    (world.clock.now.replace(tzinfo=None),))
    world.ticks(11)
    t = world.ticket("auto-gap-turn-silent-running")
    assert t and t["severity"] == "P1"


def customer_says(world, text="?"):    # noqa: F811
    world.c.execute("INSERT INTO chat_messages (session_id, role, content, "
                    "created_at) VALUES (3, 'user', %s, %s)",
                    (text, world.clock.now.replace(tzinfo=None)))


def test_the_customer_asking_is_not_the_turn_speaking(world):    # noqa: F811
    """Account 2752 sent "؟" and waited another 1,060 s. That row called the turn
    alive, and the look called the customer recovered."""
    world.job("agent_turn", state="running", project=5600, user=2846)
    world.ticks(3)
    customer_says(world)
    world.ticks(12)
    assert world.only_ticket()["severity"] == "P0"


def test_a_customer_asking_does_not_put_off_the_silence(world):    # noqa: F811
    world.job("agent_turn", state="running", project=5600, user=2846)
    world.clock.now += timedelta(seconds=100)
    customer_says(world)
    world.clock.now += timedelta(seconds=25)
    assert fired(world, gaps._turn_went_silent, minutes=2)


def test_a_request_moving_through_slices_in_silence_pages(world):    # noqa: F811
    """A slice hands over every few minutes, so each one ended before its second
    look and read `recovered`, while the customer watched nothing for 15 minutes."""
    root = jid = world.job("agent_turn", state="running", project=5600, user=2846)
    for _ in range(3):
        world.ticks(5)
        world.c.execute(
            "UPDATE video_jobs SET state = 'done', updated_at = %s, result = %s "
            "WHERE id = %s", (world.clock.now, Json({
                "outcome": "in_progress", "status": "continued",
                "continued_job_id": "next"}), jid))
        jid = world.job("agent_turn", state="running", project=5600, user=2846,
                        payload={"root_agent_job_id": str(root)})
    assert world.only_ticket()["severity"] == "P0"


def test_the_silence_is_counted_from_the_request_not_the_slice(world):    # noqa: F811
    root = world.job("agent_turn", state="done", project=5600, user=2846, age=130,
                     result={"outcome": "in_progress", "status": "continued",
                             "continued_job_id": "next"})
    world.job("agent_turn", state="running", project=5600, user=2846, age=30,
              payload={"root_agent_job_id": str(root)})
    assert fired(world, gaps._turn_went_silent, minutes=2)


def test_a_request_that_ended_is_past_the_silence(world):    # noqa: F811
    jid = world.job("agent_turn", state="running", project=5600, user=2846)
    world.ticks(3)
    world.c.execute("UPDATE video_jobs SET state = 'done', updated_at = %s "
                    "WHERE id = %s", (world.clock.now, jid))
    world.ticks(11)
    assert world.ticket("auto-gap-turn-silent-running")["severity"] == "P1"


def render_job(world, type_="final", state="running", age=1201, seconds=None):    # noqa: F811
    if seconds is not None:
        world.c.execute("INSERT INTO assets (project_id, kind, duration_s, bytes)"
                        " VALUES (5499, 'original', %s, 1000)", (seconds,))
    return world.job(type_, state=state, age=age)


def test_a_render_past_its_deadline_fires_while_it_is_open(world):    # noqa: F811
    """Account 2820's final ran 4,397 s and the first signal was its death."""
    jid = render_job(world)
    got = fired(world, gaps._render_overdue)
    assert [f["ref"] for f in got] == [f"gap:render:{jid}"]
    assert got[0]["cause_key"] == "auto-gap-render-overdue-final"
    assert got[0]["severity"] == "P0" and got[0]["job_id"] == jid


def test_a_render_of_a_long_source_has_a_longer_deadline(world):    # noqa: F811
    render_job(world, seconds=3600)
    assert fired(world, gaps._render_overdue) == []
    world.clock.now += timedelta(seconds=4200)
    assert len(fired(world, gaps._render_overdue)) == 1


@pytest.mark.parametrize("type_", ["preview_check", "filmstrip", "agent_turn"])
def test_only_what_a_customer_waits_on_is_a_render(world, type_):    # noqa: F811
    render_job(world, type_=type_)
    assert fired(world, gaps._render_overdue) == []


def test_a_render_that_finished_in_time_never_fires(world):    # noqa: F811
    render_job(world, state="done")
    world.c.execute("UPDATE video_jobs SET updated_at = created_at")
    assert fired(world, gaps._render_overdue) == []


def test_a_render_that_stays_stuck_pages_and_one_that_ends_does_not(world):    # noqa: F811
    jid = render_job(world, age=1199)
    world.ticks(2)
    assert world.only_ticket()["severity"] == "P1"
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"
    world.c.execute("DELETE FROM tickets")
    jid = render_job(world, age=1199)
    world.ticks(3)
    world.c.execute("UPDATE video_jobs SET state = 'done', updated_at = %s "
                    "WHERE id = %s", (world.clock.now, jid))
    world.ticks(10)
    assert world.only_ticket()["severity"] == "P1"


@pytest.mark.parametrize("code", ["render_failed", "repair_required", "http_0",
                                  "http_502", "device_degraded", "edit_required",
                                  "obsolete_failed_render", "a_code_of_tomorrow"])
def test_an_export_refused_is_a_failure(world, code):    # noqa: F811
    world.event("export_blocked", code=code, version=5)
    world.tick()
    t = world.only_ticket()
    assert t["cause_key"] == intake.cause_key("gap", "export-blocked", code)
    assert t["severity"] == "P1", "on a ticket within a minute"
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


@pytest.mark.parametrize("code", gaps.EXPORT_HOLDS)
def test_the_paywall_and_a_hold_are_not_failures(world, code):    # noqa: F811
    world.event("export_blocked", code=code, version=5)
    world.tick()
    world.c.execute("SELECT count(*) AS n FROM tickets")
    assert world.c.fetchone()["n"] == 0


def test_an_export_refused_then_downloaded_pages_nobody(world):    # noqa: F811
    world.event("export_blocked", code="render_failed", version=5)
    world.tick()
    world.ticks(3)
    world.event("download_url_ready", version=5)
    world.ticks(11)
    t = world.only_ticket()
    assert t["severity"] == "P1" and t["status"] == "open"


def test_a_download_that_failed_files(world):    # noqa: F811
    world.event("download_failed", code="http_500", version=5)
    world.tick()
    assert world.only_ticket()["cause_key"] == \
        "auto-gap-download-failed-http-500"


def test_an_outcome_nobody_listed_is_a_failure(world):    # noqa: F811
    """Job 135056 ended internal_error; intake reads partial and blocked."""
    jid = turn(world, outcome="internal_error")
    got = fired(world, gaps._unread_endings)
    assert [f["ref"] for f in got] == [f"request:{jid}"]
    assert got[0]["cause_key"] == "auto-request-internal-error"


def test_one_broken_check_never_costs_the_tick(world, capsys):    # noqa: F811
    def broken(*a):
        raise RuntimeError("no such column")

    def fine(cur, since, now, p):
        return [{"ref": "x"}]
    got = gaps.scan(world.c, T0, T0 + MIN, checks=(broken, fine))
    assert {"ref": "x"} in got and len(got) == 2
    assert "no such column" in capsys.readouterr().out


def test_a_broken_check_files_itself(world):    # noqa: F811
    """A check that dies in a log line sees nothing for as long as nobody reads
    the log, which is how every other silence in this audit began."""
    def broken(*a):
        raise RuntimeError("no such column")
    world.clock.now = T0 + MIN
    got = gaps.scan(world.c, T0, world.clock.now, checks=(broken,))[0]
    assert got["cause_key"] == "auto-gap-check-broken-broken"
    assert got["severity"] == "P1" and "no such column" in got["detail"]
    t, action = store.record_failure(world.conn.cursor(), got)
    assert action == "opened" and t["status"] == "open"
    again = gaps.scan(world.c, T0, world.clock.now + MIN, checks=(broken,))[0]
    assert again["ref"] == got["ref"], "once an hour, not once a minute"


def test_every_check_runs_on_a_tick_together(world):    # noqa: F811
    world.ticks(3)
    assert intake.run(world.c, world.conn.cursor(), world.clock.now)["filed"] == 0
