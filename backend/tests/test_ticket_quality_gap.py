"""A proven cause closes; the film's quality gap becomes its own ticket.

#386 and #387 (10 Nov, PLAN.md "Ticket loop"): the cause was fixed, live and
proven by a replay a session that made no fix rated, nobody failed on it since
and nobody was stuck, yet each stayed an open P0 because the replay's whole film
rated under 10. A Fixer re-picked each one every 24 h and handed it back a
minute later. The cause now closes fixed and the gap moves to a P1 quality
ticket, linked, whose bar is still 10 on rubric v3.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
from datetime import timedelta

import pytest

from test_ticket_truth import MIN, URL, cli, events_of, world  # noqa: F401
from ops_loop import intake, store                             # noqa: E402

pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")
HOUR = timedelta(hours=1)
CAUSE = "fetched-clip-never-gets-word-index"


def review(w, key, at):
    w.c.execute(
        """INSERT INTO session_reviews (session_key, surface, user_id,
           project_id, started_at, settled_at, fingerprint, verdict, score,
           reasoning) VALUES (%s, 'studio', 2837, 5499, %s, %s, %s,
           'not_delivered', 1, 'r') RETURNING id""",
        (key, at, at + MIN, "f-" + key))
    return w.c.fetchone()["id"]


def cause_ticket(w, key=CAUSE):
    """A P0 cause the Reviewer filed from a session (no auto- key)."""
    review(w, "p5499-1", w.clock.now - HOUR)
    t, _ = store.open_or_attach(w.conn.cursor(), key, "x", "P0",
                                session_key="p5499-1")
    return t["id"]


def go_live(w, tid, after=HOUR):
    w.clock.now += after
    store.mark_live(w.conn.cursor(), tid, "c0f1b0e6")
    w.clock.now += HOUR


def rated(score=1.5, **kw):
    return dict({"run": "p0386h", "session": "p5499",
                 "state": "done", "failures": [], "score_exact": score,
                 "rubric": "v3", "rated_by": "claude-opus-verify",
                 "fixer": "claude-opus-fixer"}, **kw)


def close(w, tid, replay, **kw):
    return store.close(w.conn.cursor(), tid, "fixed", root_cause="r",
                       fix_sha="c0f1b0e6", regression_check="tests/t.py::x",
                       replay=replay, **kw)


def ticket(w, tid):
    w.c.execute("SELECT * FROM tickets WHERE id = %s", (tid,))
    return w.c.fetchone()


def test_a_proven_cause_closes_and_its_film_gap_becomes_a_p1_ticket(world):
    tid = cause_ticket(world)
    go_live(world, tid)
    # Without saying so, a film under 10 still leaves it open, and says how.
    with pytest.raises(store.StoreError, match="--quality-gap"):
        close(world, tid, rated())
    t = close(world, tid, rated(), quality_gap=True)
    assert t["status"] == "fixed"
    assert events_of(t, "verified")[0]["replay"]["score_exact"] == 1.5
    gap = ticket(world, t["quality_gap_ticket"])
    assert gap["cause_key"] == f"auto-quality-after-{tid}"
    assert (gap["severity"], gap["status"]) == ("P1", "open")
    assert store.is_quality(gap) and store.is_rating(gap)
    opened = events_of(gap, "opened")[0]
    assert opened["from_ticket"] == tid and opened["score"] == 1.5
    assert opened["replay"]["session"] == "p5499"
    # Linked both ways, and it holds the cause's sessions.
    assert events_of(t, "quality_gap")[0]["ticket"] == gap["id"]
    assert gap["occurrences"] == 1
    assert store.ticket_projects(world.c, gap) == {5499}
    # The watch stops re-picking it: nothing P0 is left to take, ever.
    later = world.clock.now + intake.RELEASE_QUIET + HOUR
    assert intake.pending_p0(world.c, later) == []
    assert intake.unhandled(world.c, later) == []


def test_the_gap_keeps_the_bar_of_10(world):
    tid = cause_ticket(world)
    go_live(world, tid)
    gap = close(world, tid, rated(), quality_gap=True)["quality_gap_ticket"]
    with pytest.raises(store.StoreError, match="below the bar"):
        close(world, gap, rated(3))
    # It is the film's score itself: there is no other cause to prove gone.
    with pytest.raises(store.StoreError, match="score itself"):
        close(world, gap, rated(3), quality_gap=True)
    assert close(world, gap, rated(10))["status"] == "fixed"


def test_a_cause_met_after_its_fix_went_live_is_not_proven(world):
    tid = cause_ticket(world)
    with pytest.raises(store.StoreError, match="no fix recorded live"):
        close(world, tid, rated(), quality_gap=True)
    go_live(world, tid)
    review(world, "p5499-2", world.clock.now)          # met it again
    store.open_or_attach(world.conn.cursor(), CAUSE, "x", "P0",
                         session_key="p5499-2")
    world.clock.now += HOUR
    with pytest.raises(store.StoreError, match="after its fix went live"):
        close(world, tid, rated(), quality_gap=True)
    assert ticket(world, tid)["status"] == "open"


def test_the_proof_is_rated_by_a_session_that_made_no_fix(world):
    tid = cause_ticket(world)
    go_live(world, tid)
    with pytest.raises(store.StoreError, match="no rating"):
        close(world, tid, rated(None), quality_gap=True)
    with pytest.raises(store.StoreError, match="never rates its own"):
        close(world, tid, rated(rated_by="claude-opus-fixer"),
              quality_gap=True)
    with pytest.raises(store.StoreError, match="failed"):
        close(world, tid, rated(failures=["export_failed"]), quality_gap=True)


def tool_ticket(w):
    t, _ = store.record_failure(w.conn.cursor(), {
        "ref": "job:9", "source": "job", "at": w.clock.now,
        "cause_key": "tool:captions:shadow-offset", "user_id": 2837,
        "project_id": 5499, "severity": "P0", "title": "x", "detail": "d"})
    return t["id"]


def test_a_defect_closed_on_a_rated_film_carries_its_gap(world):
    tid = tool_ticket(world)
    go_live(world, tid)
    t = close(world, tid, rated(5.0))
    gap = ticket(world, t["quality_gap_ticket"])
    assert (gap["severity"], gap["status"]) == ("P1", "open")
    assert store.ticket_projects(world.c, gap) == {5499}
    # An unrated replay of a defect closes with nothing to carry.
    tid2 = tool_ticket_2(world)
    assert close(world, tid2, rated(None))["quality_gap_ticket"] is None


def tool_ticket_2(w):
    t, _ = store.record_failure(w.conn.cursor(), {
        "ref": "job:10", "source": "job", "at": w.clock.now,
        "cause_key": "tool:captions:plate-offset", "user_id": 2837,
        "project_id": 5499, "severity": "P0", "title": "x", "detail": "d"})
    return t["id"]


def test_the_gap_can_join_the_quality_ticket_that_owns_it(world):
    tid = tool_ticket(world)
    owner = cause_ticket(world, key="promo-short-restyles-upload-only")
    go_live(world, tid)
    with pytest.raises(store.StoreError, match="needs a quality ticket"):
        close(world, tid, rated(5.0),
              quality_ticket=tool_ticket_2(world))
    t = close(world, tid, rated(5.0), quality_ticket=owner)
    assert t["quality_gap_ticket"] == owner
    got = ticket(world, owner)
    assert got["severity"] == "P0"        # its own, never lowered by a link
    assert events_of(got, "linked_gap")[0]["from_ticket"] == tid
    world.c.execute("SELECT count(*) AS n FROM tickets")
    assert world.c.fetchone()["n"] == 3   # nothing new filed


def test_the_reviewer_is_late_after_3_hours_again(world):
    """The Reviewer runs hourly again from 12 Nov; 7 h was for its 6-hourly
    runs during the usage cap."""
    store.put_state(world.c, "ops_heartbeat_reviewer",
                    {"at": world.clock.now.isoformat()})
    later = world.clock.now + timedelta(hours=3, minutes=5)
    assert "reviewer" in {a["agent"] for a in
                          intake.silent_agents(world.c, later)}


class Git:
    def __init__(self, inside):
        self.inside = inside

    def __call__(self, *args):
        ok = True
        if args[:2] == ("merge-base", "--is-ancestor"):
            ok = args[2] in self.inside
        return type("R", (), {"returncode": 0 if ok else 1, "stdout": ""})


def test_a_replay_that_ran_before_the_fix_proves_nothing(monkeypatch):
    ops = cli()
    monkeypatch.setattr(ops, "_git", Git({"c0f1b0e6"}))
    ops.check_replay_has_fix("c0f1b0e6,fe:abc1234", {"be_sha": "cc0864e1"})
    with pytest.raises(ops.CliError, match="does not contain the fix"):
        ops.check_replay_has_fix("2d12ee11", {"be_sha": "819fe6ec"})
