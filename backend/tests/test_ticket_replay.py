"""What the 14 day replay on real rows found (tickets/replay14): pages with nobody
stuck behind them, each pinned here with its guard, so a customer who really is
stuck still pages.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
from datetime import timedelta

import pytest

from test_ticket_gaps import index_job, render_job
from test_ticket_truth import MIN, T0, URL, world    # noqa: F401
from ops_loop import intake, recovery    # noqa: E402

pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")


def partial(world, payload=None, **result):    # noqa: F811
    return world.job("agent_turn", project=5600, user=2846,
                     payload=payload or {"message_id": 1},
                     result=dict({"outcome": "partial", "status": "replied",
                                  "edl_changed": True}, **result))


REQUEST = "auto-request-partial-replied"


# ── Nobody is waiting on an operator's turn ──────────────────────────────

def test_an_operators_failed_turn_is_filed_and_pages_nobody(world):    # noqa: F811
    """Eight pages on 3-4 Nov were the repair batch, "stuck" with no customer
    there to get past it. An error is still a ticket."""
    world.job("agent_turn", state="failed", project=5600, user=2846,
              payload={"operator_repair": True}, error="Worker died")
    world.ticks(60)
    t = world.only_ticket()
    assert t["status"] == "open" and t["severity"] == "P1"


def test_an_operators_request_is_filed_and_pages_nobody(world):    # noqa: F811
    partial(world, payload={"operator_repair": True})
    world.ticks(60)
    t = world.ticket(REQUEST)
    assert t["status"] == "open" and t["severity"] == "P1"


def test_a_customers_same_failure_still_pages(world):    # noqa: F811
    world.job("agent_turn", state="failed", project=5600, user=2846,
              error="Worker died")
    world.ticks(12)
    assert world.only_ticket()["severity"] == "P0"


def test_a_customer_stuck_on_a_ticket_an_operator_opened_pages(world):    # noqa: F811
    world.job("agent_turn", state="failed", project=5600, user=2846,
              payload={"operator_repair": True}, error="Worker died")
    world.ticks(2)
    world.job("agent_turn", state="failed", project=5498, user=2123,
              error="Worker died")
    world.ticks(12)
    assert world.only_ticket()["severity"] == "P0"


# ── An assistant that carried on is not stuck ────────────────────────────

def mcp_error(world, tool="wait_for_job"):    # noqa: F811
    world.event("mcp_error_response", project=None, user=2123, tool=tool,
                outcome="unavailable", category="unclassified")
    return f"auto-mcp-{tool.replace('_', '-')}-unavailable"


def test_an_assistant_that_carried_on_pages_nobody(world):    # noqa: F811
    """wait_for_job writes no job of its own, so account 2555, 15 tool calls past
    the error, stayed stuck on it for good."""
    key = mcp_error(world)
    world.ticks(3)
    world.job("mcp_tool", state="done", project=5498, user=2123,
              payload={"tool": "keep_segments"})
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P1"


def test_reading_state_is_not_carrying_on(world):    # noqa: F811
    key = mcp_error(world, "fetch_url")
    world.ticks(3)
    world.job("mcp_tool", state="done", project=5498, user=2123,
              payload={"tool": "__state__"})
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P0"


def test_another_call_that_failed_is_not_carrying_on(world):    # noqa: F811
    key = mcp_error(world, "fetch_url")
    world.ticks(3)
    world.job("mcp_tool", state="done", project=5498, user=2123,
              payload={"tool": "look_at"},
              result={"tool_outcome": {"status": "transient_failure"}})
    world.ticks(11)
    assert world.ticket(key)["severity"] == "P0"


def test_an_assistant_that_stopped_after_the_error_pages(world):    # noqa: F811
    key = mcp_error(world, "open_project")
    world.ticks(12)
    assert world.ticket(key)["severity"] == "P0"


# ── A customer who went on with the result is not stuck ──────────────────

@pytest.mark.parametrize("kind", ["free_result_watched_pct", "export_clicked",
                                  "export_job_started"])
def test_a_customer_who_went_on_with_the_result_pages_nobody(world, kind):    # noqa: F811
    """Accounts 2675 and 2710 got a changed edit, watched it and pressed Export,
    and paged as left stuck. The ticket stays open: the cause is still fixed."""
    partial(world)
    world.ticks(3)
    world.event(kind, user=2846, project=5600)
    world.ticks(11)
    t = world.ticket(REQUEST)
    assert t["status"] == "open" and t["severity"] == "P1"


def test_a_customer_who_left_after_a_short_request_pages(world):    # noqa: F811
    partial(world)
    world.ticks(12)
    assert world.ticket(REQUEST)["severity"] == "P0"


def test_watching_before_the_request_ended_proves_nothing(world):    # noqa: F811
    world.event("free_result_watched_pct", user=2846, project=5600, after=-60)
    partial(world)
    world.ticks(12)
    assert world.ticket(REQUEST)["severity"] == "P0"


def test_a_failed_turn_is_not_got_past_by_watching_the_old_cut(world):    # noqa: F811
    world.job("agent_turn", state="failed", project=5600, user=2846,
              error="Worker died")
    world.ticks(3)
    world.event("free_result_watched_pct", user=2846, project=5600)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


# ── A player that failed while the work went on ──────────────────────────

def player_error(world):    # noqa: F811
    world.event("player_error", user=2846, project=5600, media_error=2,
                src_path="/valmera-media/renders/5/a.mp4")


def test_new_work_after_a_player_error_pages_nobody(world):    # noqa: F811
    """Nothing writes a row when a video simply plays: account 2647, back at work
    5 minutes later, was stuck on one network error for good."""
    player_error(world)
    world.ticks(3)
    world.job("preview", state="done", project=5600, user=2846, age=30)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P1"


def test_a_render_already_running_proves_nothing_about_the_player(world):    # noqa: F811
    player_error(world)
    world.ticks(3)
    world.job("preview", state="done", project=5600, user=2846, age=600)
    world.ticks(11)
    assert world.only_ticket()["severity"] == "P0"


def test_a_dead_player_with_nothing_after_it_pages(world):    # noqa: F811
    player_error(world)
    world.ticks(12)
    assert world.only_ticket()["severity"] == "P0"


# ── Slow is not stuck ────────────────────────────────────────────────────

def test_a_slow_analysis_is_filed_at_once_and_does_not_page(world):    # noqa: F811
    index_job(world, "running", 5, 599)
    world.ticks(40)
    t = world.only_ticket()
    assert t["status"] == "open" and t["severity"] == "P1"


def test_a_hung_analysis_pages(world):    # noqa: F811
    index_job(world, "running", 5, 599)
    world.ticks(48)
    assert world.only_ticket()["severity"] == "P0"


def test_a_silent_turn_that_ends_pages_nobody(world):    # noqa: F811
    jid = world.job("agent_turn", state="running", project=5600, user=2846)
    world.ticks(30)
    world.c.execute("UPDATE video_jobs SET state = 'done', updated_at = %s "
                    "WHERE id = %s", (world.clock.now, jid))
    world.ticks(30)
    assert world.ticket("auto-gap-turn-silent-running")["severity"] == "P1"


def test_a_hung_turn_pages(world):    # noqa: F811
    world.job("agent_turn", state="running", project=5600, user=2846)
    world.ticks(50)
    assert world.ticket("auto-gap-turn-silent-running")["severity"] == "P0"


def test_a_failure_is_not_slow(world):    # noqa: F811
    """Only work still running waits longer: what failed is looked at in 10."""
    world.job("index", state="failed", error="scratch space")
    assert recovery.grace({"source": "job"}) == recovery.GRACE


# ── A refused file was never moving ──────────────────────────────────────

def test_a_refused_file_is_looked_at_in_10_minutes_whatever_its_size(world):    # noqa: F811
    """A 110 GB file was refused at once and the look waited 2 hours for a
    transfer that never started."""
    world.event("upload_rejected", project=None, user=2846,
                reason="over size cap", bytes=109_805_883_163)
    world.ticks(12)
    assert world.only_ticket()["severity"] == "P0"


def test_a_failed_transfer_still_gets_the_time_its_file_needs(world):    # noqa: F811
    assert recovery.grace({"source": "upload", "bytes": 7_845_957_336}) == \
        recovery.UPLOAD_GRACE_MAX
