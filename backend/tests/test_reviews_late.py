"""The "session reviews" alert (15 Nov 2026): it said the reviews had stopped
when no review had been written for 24 h, while the Reviewer ran every hour and
no customer session was waiting (the paywall is on). Reviews are late only when
a settled, reviewable session has waited REVIEWS_LATE with no review covering
it, found as `ops_review.py pending` finds it. A Reviewer that stops running is
its heartbeat's alert.

No database: cursors are scripted fakes and the pending sessions are given."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cockpit_alerts as alerts                              # noqa: E402
from ops_loop import intake, sessions                        # noqa: E402

# 15 Nov: the alert went out at 17:23:48, the last review was 14 Nov 17:23:07.
NOW = datetime(2026, 11, 15, 17, 23, 48, tzinfo=timezone.utc)
H = timedelta(hours=1)
MIN = timedelta(minutes=1)


class Cur:
    """app_kv as rows {key: value}; records statements and writes."""

    def __init__(self, kv=None):
        self.kv, self.sql, self.last = dict(kv or {}), [], None

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.last = (sql, params)
        if sql.lstrip().upper().startswith("INSERT INTO APP_KV"):
            self.kv[params[0]] = json.loads(params[1])

    def fetchall(self):
        sql, params = self.last
        if "FROM app_kv WHERE key = ANY" not in sql:
            return []
        keys = params[0]
        return [{"key": k, "value": json.dumps(v)} for k, v in self.kv.items()
                if k in keys]

    def fetchone(self):
        sql, params = self.last
        if "FROM app_kv WHERE key = %s" in sql and params[0] in self.kv:
            return {"value": json.dumps(self.kv[params[0]])}
        return None


def beat(at):
    return {"at": at.isoformat()}


def session(key, ended_at, previous_review=None, next_events=()):
    s = sessions.Session(key=key, ended_at=ended_at,
                         previous_review=previous_review)
    s.next_events = list(next_events)
    s.next_payments, s.next_subscription = [], []
    return s


def given(monkeypatch, found):
    calls = []

    def pending(cur, since, now=None, **kw):
        calls.append((since, now))
        if isinstance(found, Exception):
            raise found
        return list(found), {"pending": len(found)}
    monkeypatch.setattr(sessions, "pending", pending)
    return calls


def agents(got):
    return [a["agent"] for a in got]


def looked(monkeypatch, found, at=NOW, kv=None):
    """The intake tick's look at `at`; returns the app_kv it leaves."""
    given(monkeypatch, found)
    cur = Cur(kv)
    intake.look_review_backlog(cur, cur, at)
    return cur


def test_nothing_to_review_is_not_an_alert_however_old_the_last_review(
        monkeypatch):
    calls = given(monkeypatch, [])
    cur = Cur({"ops_heartbeat_reviewer": beat(NOW - 8 * MIN)})
    kept = intake.look_review_backlog(cur, cur, NOW)
    assert kept == {"at": NOW.isoformat(), "since": None, "key": None,
                    "waiting": 0}
    # It looked over the Reviewer's own window, up to now.
    assert calls == [(NOW - intake.REVIEWS_LOOKBACK, NOW)]
    assert intake.silent_agents(cur, NOW) == []
    # No statement reads the last review's time any more.
    assert not any("reviewed_at" in s for s in cur.sql)


def test_the_15_nov_alert_is_not_raised(monkeypatch):
    """At 17:23 two sessions had settled about an hour before (reviewed at
    17:25); none had waited a day."""
    cur = looked(monkeypatch, [
        session("p5843-1794758487",
                datetime(2026, 11, 15, 16, 2, 44, tzinfo=timezone.utc)),
        session("p5845-1794758532",
                datetime(2026, 11, 15, 16, 25, 12, tzinfo=timezone.utc))],
        kv={"ops_heartbeat_reviewer": beat(NOW - 8 * MIN)})
    assert cur.kv["ops_review_backlog"]["waiting"] == 2
    assert intake.silent_agents(cur, NOW) == []


def test_a_session_waiting_more_than_a_day_is_an_alert(monkeypatch):
    cur = looked(monkeypatch, [session("p2-2", NOW - 2 * H),
                               session("p1-1", NOW - 25 * H)])
    got = intake.silent_agents(cur, NOW)
    assert agents(got) == ["session reviews"]
    a = got[0]
    assert a["oldest"] == "p1-1" and a["waiting"] == 2
    assert a["last_at"] == NOW - 25 * H + sessions.SETTLE
    assert a["silent_min"] == int((25 * H - sessions.SETTLE).total_seconds()
                                  // 60)
    # The age keeps growing between looks.
    later = intake.silent_agents(cur, NOW + 10 * MIN)
    assert later[0]["silent_min"] == a["silent_min"] + 10


def test_a_day_counts_from_when_the_session_settled(monkeypatch):
    # Ended 24 h 10 min ago: settled 23 h 40 min ago, not yet late.
    cur = looked(monkeypatch, [session("p1-1", NOW - 24 * H - 10 * MIN)])
    assert intake.silent_agents(cur, NOW) == []
    assert agents(intake.silent_agents(cur, NOW + 21 * MIN)) == \
        ["session reviews"]


def test_a_reviewed_session_that_moved_waits_from_the_move(monkeypatch):
    """A review 30 h ago, then the customer exported an hour ago: the
    fingerprint moved and it is pending again, waiting one hour, not 30."""
    s = session("p1-1", NOW - 31 * H,
                previous_review={"reviewed_at": NOW - 30 * H},
                next_events=[{"created_at": NOW - H}])
    assert intake._waiting_since(s) == NOW - H
    cur = looked(monkeypatch, [s])
    assert intake.silent_agents(cur, NOW) == []


def test_a_stopped_reviewer_is_still_its_heartbeats_alert(monkeypatch):
    cur = looked(monkeypatch, [],
                 kv={"ops_heartbeat_reviewer": beat(NOW - 4 * H)})
    assert agents(intake.silent_agents(cur, NOW)) == ["reviewer"]


def test_one_look_a_quarter_hour(monkeypatch):
    calls = given(monkeypatch, [])
    cur = Cur()
    for m in (0, 1, 14):
        intake.look_review_backlog(cur, cur, NOW + m * MIN)
    assert len(calls) == 1
    intake.look_review_backlog(cur, cur, NOW + 15 * MIN)
    assert len(calls) == 2


def test_a_look_that_stops_is_reported_not_silent(monkeypatch):
    """A failing look leaves its row stale; after two hours that is an
    alert of its own, never a quiet pass."""
    cur = looked(monkeypatch, [])
    assert intake.silent_agents(cur, NOW + 2 * H) == []
    got = intake.silent_agents(cur, NOW + 2 * H + MIN)
    assert agents(got) == ["session review check"]


def test_a_failed_read_inside_a_transaction_is_rolled_back_to_its_savepoint(
        monkeypatch):
    given(monkeypatch, RuntimeError("no such column"))

    class Conn:
        autocommit = False
    cur = Cur()
    cur.connection = Conn()
    try:
        intake.look_review_backlog(cur, cur, NOW)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the look's failure must reach the tick's log")
    assert "SAVEPOINT review_backlog" in cur.sql
    assert "ROLLBACK TO SAVEPOINT review_backlog" in cur.sql
    assert "ops_review_backlog" not in cur.kv


def test_the_tick_looks_after_its_pass_and_survives_a_failed_look(
        monkeypatch, capsys):
    class Conn:
        def __init__(self):
            self.cur, self.commits, self.rollbacks = Cur(), 0, 0
            self.cur.fetchone = lambda: {"got": True}

        def cursor(self):
            conn = self

            class Ctx:
                def __enter__(self):
                    return conn.cur

                def __exit__(self, *a):
                    return False
            return Ctx()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1
    order = []
    monkeypatch.setattr(intake, "run", lambda r, w, now: order.append("run")
                        or {"seen": 0, "filed": 0})

    def boom(r, w, now):
        order.append("look")
        raise RuntimeError("statement timeout")
    monkeypatch.setattr(intake, "look_review_backlog", boom)
    r, w = Conn(), Conn()
    assert intake.run_tick(NOW, r, w) == {"seen": 0, "filed": 0}
    assert order == ["run", "look"]
    assert w.rollbacks == 1
    assert any("pg_advisory_unlock" in s for s in w.cur.sql)
    assert "review backlog look failed: RuntimeError: statement timeout" in \
        capsys.readouterr().out


def test_the_owner_is_told_how_many_sessions_wait_and_since_when(monkeypatch):
    cur = looked(monkeypatch, [session("p1-1", NOW - 25 * H)])

    class AlertCur(Cur):
        def fetchone(self):
            return super().fetchone() or {"n": 0}
    acur = AlertCur(cur.kv)
    items = [i for i in alerts.event_alerts(acur, NOW - H, NOW)
             if i[0].startswith("silent:")]
    assert [i[0] for i in items] == [f"silent:session reviews:{NOW:%Y%m%d}"]
    _, section, line = items[0]
    assert section == "Customer sessions wait for a review"
    assert "1 customer session</b> waiting for a review" in line
    assert "p1-1" in line and "has not run" not in line
