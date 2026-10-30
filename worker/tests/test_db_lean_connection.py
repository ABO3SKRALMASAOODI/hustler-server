"""The lean connection: one Db.run is still exactly one transaction, with
its statement timeout, in two fewer round trips (31 Oct 2026).

Runs against a real Postgres when VALMERA_TEST_PG_URL names one (the harness
Postgres); the fake-connection tests below run everywhere.
"""
import os
import threading

import pytest

import config
import db as dbx
import io_telemetry
import psycopg2

PG_URL = os.getenv("VALMERA_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="no VALMERA_TEST_PG_URL")


@pytest.fixture
def lean(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", PG_URL)
    conns = []

    def make():
        db = dbx.Db()
        conns.append(db)
        return db
    yield make
    for db in conns:
        db.reset()


@pytest.fixture
def table(lean):
    db = lean()
    name = "lean_t_%d_%d" % (os.getpid(), threading.get_ident() % 100000)
    db.run(lambda c: c.cursor().execute(
        f"DROP TABLE IF EXISTS {name}; CREATE TABLE {name} "
        "(id serial primary key, v text)"))
    yield name
    db.run(lambda c: c.cursor().execute(f"DROP TABLE IF EXISTS {name}"))


def _status(conn):
    return conn.info.transaction_status


@needs_pg
def test_a_db_run_is_one_transaction_with_its_timeout(lean, table):
    db = lean()

    def work(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT (SELECT setting::int FROM pg_settings WHERE "
                        "name = 'statement_timeout') AS t, "
                        "txid_current() AS a")
            first = cur.fetchone()
            cur.execute(f"INSERT INTO {table} (v) VALUES (%s) RETURNING id",
                        ("x",))
            new_id = cur.fetchone()["id"]
            cur.execute("SELECT txid_current() AS a")
            second = cur.fetchone()
        return first, new_id, second

    first, new_id, second = db.run(work)
    assert first["t"] == dbx._STATEMENT_TIMEOUT_MS
    assert first["a"] == second["a"], "statements ran in one transaction"
    assert new_id == 1
    assert _status(db.conn) == psycopg2.extensions.TRANSACTION_STATUS_IDLE
    # the timeout is transaction-local: it never leaks onto the session
    raw = psycopg2.extensions.connection.cursor(
        db.conn, cursor_factory=psycopg2.extensions.cursor)
    psycopg2.extensions.cursor.execute(
        raw, "SELECT current_setting('statement_timeout')")
    assert raw.fetchone()[0] == "0"
    raw.close()
    assert _status(db.conn) == psycopg2.extensions.TRANSACTION_STATUS_IDLE


@needs_pg
def test_a_failed_db_run_rolls_back_everything(lean, table):
    db = lean()

    def work(conn):
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (v) VALUES ('a')")
            cur.execute("SELECT 1/0")

    with pytest.raises(psycopg2.Error):
        db.run(work)
    assert _status(db.conn) == psycopg2.extensions.TRANSACTION_STATUS_IDLE
    got = db.run(lambda c: (c.cursor().execute(
        f"SELECT count(*) AS n FROM {table}") or True) and None)
    with db.conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        assert cur.fetchone()["n"] == 0
    db.conn.commit()
    assert got is None


@needs_pg
def test_a_python_error_rolls_back_too(lean, table):
    db = lean()

    def work(conn):
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (v) VALUES ('a')")
        raise ValueError("boom")

    with pytest.raises(ValueError):
        db.run(work)
    n = db.run(lambda c: _count(c, table))
    assert n == 0


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        return cur.fetchone()["n"]


@needs_pg
def test_an_uncommitted_write_is_invisible_until_the_run_ends(lean, table):
    writer, reader = lean(), lean()
    seen = {}

    def work(conn):
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (v) VALUES ('a')")
        seen["during"] = reader.run(lambda c: _count(c, table))

    writer.run(work)
    assert seen["during"] == 0
    assert reader.run(lambda c: _count(c, table)) == 1


@needs_pg
def test_rollback_inside_a_run_starts_a_fresh_transaction(lean, table):
    """read_tools probes a column, rolls back on error and continues."""
    db = lean()

    def work(conn):
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT no_such_column FROM " + table)
            except psycopg2.Error:
                conn.rollback()
            cur.execute(f"INSERT INTO {table} (v) VALUES ('kept')")
            cur.execute("SELECT current_setting('statement_timeout') AS t")
            return cur.fetchone()["t"]

    timeout = db.run(work)
    assert timeout != "0"
    assert db.run(lambda c: _count(c, table)) == 1


@needs_pg
def test_for_update_skip_locked_still_holds_its_lock(lean, table):
    a, b = lean(), lean()
    a.run(lambda c: c.cursor().execute(
        f"INSERT INTO {table} (v) VALUES ('job')"))
    gate, done = threading.Event(), threading.Event()
    seen = {}

    def hold(conn):
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} FOR UPDATE SKIP LOCKED")
            seen["a"] = cur.fetchall()
            gate.set()
            done.wait(5)

    t = threading.Thread(target=lambda: a.run(hold))
    t.start()
    gate.wait(5)

    def other(conn):
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} FOR UPDATE SKIP LOCKED")
            return cur.fetchall()

    seen["b"] = b.run(other)
    done.set()
    t.join(5)
    assert len(seen["a"]) == 1 and seen["b"] == []


@needs_pg
def test_round_trips_drop_from_four_to_two(lean, table):
    db = lean()
    db.run(lambda c: None)            # connect outside the measurement
    token = io_telemetry.begin()

    def one(conn):
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            return cur.fetchone()["n"]

    for _ in range(5):
        assert db.run(one) == 0
    db.run(lambda c: None)            # nothing executed: no round trip
    row = io_telemetry.finish(token)
    assert row["db_calls"] == 6
    assert row["db_statements"] == 5
    assert row["db_roundtrips_est"] == 10      # was 4 per call: 20 (+2)
    assert row["db_top"]["one"]["n"] == 5


@needs_pg
def test_other_cursor_classes_and_executemany_run_in_the_transaction(
        lean, table):
    db = lean()

    def work(conn):
        with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
            cur.execute("SELECT txid_current()")
            a = cur.fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {table} (v) VALUES (%s)",
                            [("1",), ("2",)])
            cur.execute("SELECT txid_current() AS t")
            b = cur.fetchone()["t"]
        return a, b

    a, b = db.run(work)
    assert a == b
    assert db.run(lambda c: _count(c, table)) == 2


@needs_pg
def test_composed_and_bytes_queries_are_prefixed(lean, table):
    db = lean()

    def work(conn):
        with conn.cursor() as cur:
            cur.execute(psycopg2.sql.SQL("SELECT {} AS v").format(
                psycopg2.sql.Literal(3)))
            first = cur.fetchone()["v"]
            conn.rollback()
            cur.execute(b"SELECT current_setting('statement_timeout') AS t")
            return first, cur.fetchone()["t"]

    first, timeout = db.run(work)
    assert first == 3 and timeout != "0"


def test_a_lean_open_carries_no_percent_sign():
    assert "%" not in dbx._open_sql()
    assert dbx._open_sql().startswith("BEGIN; SELECT set_config(")


def test_non_lean_connections_keep_the_separate_timeout_statement():
    """Fakes (and any non-lean connection) keep the old protocol."""
    events = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, query, params):
            events.append(query)

    class Conn:
        closed = False

        def cursor(self):
            return Cursor()

        def commit(self):
            events.append("commit")

    db = dbx.Db()
    db._conn = Conn()
    token = io_telemetry.begin()
    db.run(lambda c: None)
    row = io_telemetry.finish(token)
    assert events[0].startswith("SELECT set_config") and events[-1] == "commit"
    assert row["db_roundtrips_est"] == 2


# ── the real queue and EDL functions on a lean connection ────────────────────
# VALMERA_TEST_SCHEMA_URL: a disposable local database with the full schema
# (backend/apply_migrations.py). Never a remote database.
SCHEMA_URL = os.getenv("VALMERA_TEST_SCHEMA_URL")
needs_schema = pytest.mark.skipif(not SCHEMA_URL,
                                  reason="no VALMERA_TEST_SCHEMA_URL")


@needs_schema
def test_real_queue_and_edl_functions_round_trip(monkeypatch):
    from urllib.parse import urlparse
    assert urlparse(SCHEMA_URL).hostname in {"localhost", "127.0.0.1"}
    monkeypatch.setattr(config, "DATABASE_URL", SCHEMA_URL)
    db = dbx.Db()
    other = dbx.Db()
    try:
        def seed(conn):
            with conn.cursor() as cur:
                # the disposable database's earlier runs
                cur.execute("UPDATE video_jobs SET state = 'failed' "
                            "WHERE state IN ('queued', 'running')")
                cur.execute("INSERT INTO users (email, password) VALUES "
                            "(%s, 'x') RETURNING id",
                            ("lean-%s@example.test" % os.urandom(4).hex(),))
                uid = cur.fetchone()["id"]
                cur.execute("INSERT INTO projects (user_id) VALUES (%s) "
                            "RETURNING id", (uid,))
                return uid, cur.fetchone()["id"]

        uid, pid = db.run(seed)
        token = io_telemetry.begin()
        v1 = db.run(dbx.insert_edl, pid, {"keep": [[0, 1]]}, "agent")
        v2 = db.run(dbx.insert_edl, pid, {"keep": [[0, 2]]}, "agent")
        assert v2 == v1 + 1
        assert db.run(dbx.latest_edl, pid)["version"] == v2
        # another connection sees the committed version at once
        assert other.run(dbx.latest_edl, pid)["version"] == v2
        jid = db.run(dbx.enqueue_job, pid, uid, "filmstrip", {"x": 1})
        assert other.run(dbx.get_job, jid)["state"] == "queued"
        claimed = db.run(dbx.claim_job, ("filmstrip",), 3)
        assert claimed and claimed["id"] == jid
        # claimed and committed: nobody else can claim it
        assert other.run(dbx.claim_job, ("filmstrip",), 3) is None
        db.run(dbx.set_progress, jid, 40)
        assert db.run(dbx.finish_job, jid, "done", result={"ok": True})
        got = other.run(dbx.get_job, jid)
        assert got["state"] == "done" and got["result"] == {"ok": True}
        assert db.run(dbx.kv_get, "no-such-key-lean") is None
        row = io_telemetry.finish(token)
        # every call: its statements plus one COMMIT, no BEGIN/timeout trips
        assert row["db_roundtrips_est"] == (row["db_statements"]
                                            + row["db_calls"])
        assert "insert_edl" in row["db_top"]
    finally:
        db.reset()
        other.reset()
