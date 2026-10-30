"""Per-input byte accounting safe under concurrent Modal inputs.

The orchestration pools intentionally share one container among independent
projects. Process-global counters would blend those bills together, so byte
accounting follows the current execution context instead.

Database cost rides the same context. Every MCP call paid a fixed 3.5-5 s
while using ~0.2 s of CPU, and the suspected cause (synchronous round trips to
a Postgres in another region) had never been measured. ``db_*`` counters make
that cost visible per job: ``db_calls`` transactions (``Db.run``),
``db_statements`` executed, ``db_connects`` new connections, and the wall
seconds spent in each. ``db_roundtrips_est`` adds the implicit BEGIN and
COMMIT of each transaction to the statement count.

31 Oct 2026: ``db_roundtrips_est`` is now counted, not estimated (a lean
connection folds BEGIN and the statement timeout into a transaction's first
statement, db._LeanConnection), and ``db_top`` names the Db.run functions
that spent the most time, so the next cut is measured, not guessed.
"""

from contextvars import ContextVar


_COUNTERS = ContextVar("valmera_io_counters", default=None)


DB_TOP_N = 12


def begin():
    return _COUNTERS.set({"downloaded_bytes": 0, "uploaded_bytes": 0,
                          "db_calls": 0, "db_statements": 0, "db_s": 0.0,
                          "db_connects": 0, "db_connect_s": 0.0,
                          "db_rt": 0, "db_fns": {}})


def add_downloaded(value):
    row = _COUNTERS.get()
    if row is not None:
        row["downloaded_bytes"] += max(0, int(value or 0))


def add_uploaded(value):
    row = _COUNTERS.get()
    if row is not None:
        row["uploaded_bytes"] += max(0, int(value or 0))


def add_db_call(seconds, name=None):
    row = _COUNTERS.get()
    if row is not None:
        spent = max(0.0, float(seconds or 0.0))
        row["db_calls"] = row.get("db_calls", 0) + 1
        row["db_s"] = row.get("db_s", 0.0) + spent
        fns = row.setdefault("db_fns", {})
        key = str(name or "?")[:48]
        got = fns.get(key)
        if got is None:
            if len(fns) >= 200:
                key = "(other)"
                got = fns.get(key)
            if got is None:
                got = fns[key] = [0, 0.0]
        got[0] += 1
        got[1] += spent


def add_db_statement():
    """One executed statement: one round trip."""
    row = _COUNTERS.get()
    if row is not None:
        row["db_statements"] = row.get("db_statements", 0) + 1
        row["db_rt"] = row.get("db_rt", 0) + 1


def add_db_roundtrip(n=1):
    """A round trip that is not a counted statement (COMMIT, ROLLBACK, a
    separate BEGIN)."""
    row = _COUNTERS.get()
    if row is not None:
        row["db_rt"] = row.get("db_rt", 0) + int(n)


def add_db_connect(seconds):
    row = _COUNTERS.get()
    if row is not None:
        row["db_connects"] = row.get("db_connects", 0) + 1
        row["db_connect_s"] = (row.get("db_connect_s", 0.0)
                               + max(0.0, float(seconds or 0.0)))


def finish(token):
    row = dict(_COUNTERS.get() or {})
    _COUNTERS.reset(token)
    if "db_calls" in row:
        row["db_s"] = round(row.get("db_s", 0.0), 3)
        row["db_connect_s"] = round(row.get("db_connect_s", 0.0), 3)
        row["db_roundtrips_est"] = int(row.pop("db_rt", 0) or 0)
        fns = row.pop("db_fns", None) or {}
        if fns:
            top = sorted(fns.items(), key=lambda kv: -kv[1][1])[:DB_TOP_N]
            row["db_top"] = {k: {"n": v[0], "s": round(v[1], 3)}
                             for k, v in top}
    return row
