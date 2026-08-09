"""Round 101 — the heartbeat that stopped, and took eight users' turns with it.

THE FAILURE. `psycopg2.connect` was called with no timeouts and no
keepalives. A blocking socket whose peer has gone away does not raise: the
next execute() waits in recv() forever. heartbeat_forever is one thread, one
shared connection, one `while True` — so a single wedged socket does not slow
the heartbeat down, it ENDS it, silently, for the life of the process. Every
in-flight job then stops being heartbeated while the worker is otherwise
completely healthy (still claiming, still rendering, still replying), and
STALE_AFTER_S later the reaper — a different thread, on a different
connection — fails each long-running one as "Worker died and retries are
exhausted".

THE EVIDENCE it was this and not a crash (last-50-user audit, Aug 9 2026):
  * agent turns died in CLUSTERS — five inside eleven minutes on Aug 8
    (3389/3411/3423/3430/3431), across four different users;
  * the process was demonstrably alive throughout: previews on the same box
    started and finished inside those same windows;
  * the last tool before each death was different every time
    (get_kept_transcript, list_assets, reset_edit, add_zoom, render_preview,
    look_at, set_volume…) — no tool in common, because no tool was at fault;
  * the gap from that last tool to the reaper was 128-378s in all 18 cases:
    just past the 120s stale window, every time. That is the shape of "the
    beats stopped" and of nothing else.
  * only agent_turn ever showed the error, because it alone runs
    MAX_ATTEMPTS_AGENT=1 — media jobs were reaped too and quietly succeeded
    on retry, which is why this hid for so long.

Run:  python -m pytest tests/test_heartbeat_resilience.py -q   (from worker/)
"""

import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                 # noqa: E402
import db as dbx                                              # noqa: E402


# ── the connection can no longer hang ───────────────────────────────────────

def test_every_phase_of_the_connection_is_bounded():
    kw = dbx._CONNECT_KW
    assert kw["connect_timeout"] > 0          # the connect phase
    assert kw["keepalives"] == 1              # ...and the idle phase
    assert "statement_timeout" in kw["options"]   # ...and the query phase


def test_a_dead_socket_is_detected_before_the_reaper_acts():
    """The whole point. Keepalive detection has to complete inside the stale
    window, or the connection fails only AFTER the jobs it was keeping alive
    have already been declared dead — a fix that changes the error message
    and nothing else."""
    kw = dbx._CONNECT_KW
    detect_s = (kw["keepalives_idle"]
                + kw["keepalives_interval"] * kw["keepalives_count"])
    # plus one reconnect, which is itself bounded
    assert detect_s + kw["connect_timeout"] < config.STALE_AFTER_S


def test_the_statement_backstop_clears_every_real_query():
    """Largest rows this worker writes in production (Aug 2026): a 448 kB
    index, a 3.6 kB EDL, a 103 kB llm_calls request — all single indexed
    writes. A query here that has run for a minute is wedged, not slow."""
    ms = int(kw_opt(dbx._CONNECT_KW["options"]))
    assert 10_000 <= ms <= 300_000


def kw_opt(options):
    return options.split("statement_timeout=")[1].split()[0]


def test_connect_passes_the_hardening_through(monkeypatch):
    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        return _FakeConn()

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://x/y")
    dbx.connect()
    for k in ("connect_timeout", "keepalives", "keepalives_idle",
              "keepalives_interval", "keepalives_count", "options"):
        assert k in seen, f"{k} was dropped on the way to psycopg2"


# ── the heartbeat survives its own database ─────────────────────────────────

class _FakeConn:
    closed = False

    def cursor(self):
        raise AssertionError("not used")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _Loop(BaseException):
    """Breaks out of heartbeat_forever's `while True` at a chosen beat.

    BaseException, not Exception, and that is the whole trick: the loop's
    entire job is to swallow `except Exception` and keep beating, so a
    stop-signal derived from Exception would be caught by the very code under
    test and the suite would hang instead of finishing.
    """


def _run_beats(monkeypatch, outcomes, ids=(11, 22), max_ticks=None):
    """Drive heartbeat_forever through `outcomes` (an exception to raise, or
    None to succeed) and return (resets, printed lines).

    Two ways out, because an idle worker never reaches Db.run at all: the
    scripted outcomes running out, or `max_ticks` sleeps having elapsed. The
    sleep is the loop's only other edge, so counting it is what lets the
    empty-ACTIVE_JOBS path be tested without hanging the suite forever.
    """
    state = {"i": 0, "resets": 0, "ticks": 0}
    logs = []

    class FakeDb:
        def run(self, _fn, *a, **k):
            i = state["i"]
            state["i"] += 1
            if i >= len(outcomes):
                raise _Loop()
            exc = outcomes[i]
            if exc:
                raise exc
            return None

        def reset(self):
            state["resets"] += 1

    def fake_sleep(_s):
        state["ticks"] += 1
        if max_ticks is not None and state["ticks"] > max_ticks:
            raise _Loop()

    monkeypatch.setattr(dbx, "Db", FakeDb)
    monkeypatch.setattr(dbx.time, "sleep", fake_sleep)
    monkeypatch.setattr(dbx.config, "HEARTBEAT_EVERY_S", 0)
    monkeypatch.setattr(dbx, "ACTIVE_JOBS", set(ids))
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: logs.append(" ".join(map(str, a))))
    with pytest.raises(_Loop):
        dbx.heartbeat_forever()
    return state["resets"], logs


def test_a_failed_beat_drops_the_connection(monkeypatch):
    """Db.run only reconnects on the errors it recognises as connection loss.
    A statement timeout arrives as QueryCanceled — not one of them — so
    without this the thread keeps re-using a session already proven sick."""
    resets, _ = _run_beats(monkeypatch, [psycopg2.errors.QueryCanceled()])
    assert resets == 1


def test_the_loop_keeps_beating_after_a_failure(monkeypatch):
    """One bad beat must not end the thread — that IS the bug."""
    resets, _ = _run_beats(monkeypatch, [
        psycopg2.OperationalError("server closed the connection"),
        None, None,
    ])
    assert resets == 1        # only the failure reset; the good beats did not


def test_the_silence_is_announced_with_its_consequence(monkeypatch):
    """The eight users lost on Aug 8 were lost invisibly. A stopped heartbeat
    now says what it will cost, in the log, on the first failed beat."""
    _, logs = _run_beats(monkeypatch, [OSError("timeout")])
    line = next(x for x in logs if "[heartbeat]" in x)
    assert "FAILED" in line
    assert "Worker died" in line and "2 running job(s)" in line


def test_recovery_is_announced_too(monkeypatch):
    _, logs = _run_beats(monkeypatch, [OSError("boom"), None])
    assert any("recovered after 1 failed beat" in x for x in logs)


def test_no_active_jobs_is_not_a_failure(monkeypatch):
    """An idle worker must not log an outage every 20 seconds — and must not
    let its idle clock count toward the 'how long have we been silent'
    warning either, or the first beat after a quiet spell reads as an outage
    that never happened."""
    _, logs = _run_beats(monkeypatch, [None], ids=(), max_ticks=3)
    assert not [x for x in logs if "FAILED" in x]
