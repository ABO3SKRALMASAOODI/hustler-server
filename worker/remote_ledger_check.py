"""Read-only deployment prerequisite for durable provider ownership.

The executor can remain migration-compatible while code and schema roll in a
controlled order, but a production provider deployment must not advertise
exact-call recovery until the complete additive ledger exists. This command
prints only aggregate schema facts and never includes the database URL.
"""

import argparse
import json
import os


REQUIRED_COLUMNS = {
    "job_id", "total_claims", "provider", "call_id", "function_name",
    "state", "deadline_at", "submitted_at", "started_at",
    "last_observed_at", "completed_at", "error", "meta",
}
REQUIRED_INDEXES = {
    "remote_executions_pkey",
    "remote_executions_provider_call_id_key",
    "idx_remote_executions_active",
    "idx_remote_executions_claim",
}


def evaluate(table_present, columns, indexes):
    columns = set(columns or ())
    indexes = set(indexes or ())
    missing_columns = sorted(REQUIRED_COLUMNS - columns)
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    ready = bool(table_present) and not missing_columns and not missing_indexes
    return {
        "status": "pass" if ready else "fail",
        "ready": ready,
        "table_present": bool(table_present),
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
    }


def load(conn):
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '15s'")
        cur.execute(
            "SELECT to_regclass('public.remote_executions') AS name")
        present = bool((cur.fetchone() or {}).get("name"))
        if not present:
            return evaluate(False, (), ())
        cur.execute("""SELECT column_name
                       FROM information_schema.columns
                       WHERE table_schema = 'public'
                         AND table_name = 'remote_executions'""")
        columns = {row["column_name"] for row in cur.fetchall()}
        cur.execute("""SELECT indexname
                       FROM pg_indexes
                       WHERE schemaname = 'public'
                         AND tablename = 'remote_executions'""")
        indexes = {row["indexname"] for row in cur.fetchall()}
        return evaluate(True, columns, indexes)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-env", default="PRODUCTION_DATABASE_URL")
    args = parser.parse_args(argv)
    dsn = os.getenv(args.database_env, "")
    if not dsn:
        parser.error(f"environment variable {args.database_env} is empty")
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(
        dsn, cursor_factory=RealDictCursor, connect_timeout=8)
    try:
        report = load(conn)
        conn.rollback()
    finally:
        conn.close()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
