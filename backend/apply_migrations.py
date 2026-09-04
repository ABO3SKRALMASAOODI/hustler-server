"""Apply the complete local/CI schema in deterministic filename order.

Production schema remains managed manually via psql per project convention;
do not point this convenience runner at a production database. Historical
production migrations predate the ledger and include one-time data backfills.

    python apply_migrations.py

For an explicitly disposable remote development database only:

    python apply_migrations.py --allow-remote-development-database
"""

import argparse
import os
from urllib.parse import urlsplit

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}
NO_TRANSACTION_MARKER = "-- migrate: no-transaction"


def is_local_database_url(dsn):
    try:
        return (urlsplit(dsn).hostname or "").lower() in LOCAL_DATABASE_HOSTS
    except (TypeError, ValueError):
        return False


def migration_requires_autocommit(source):
    """Whether a migration contains commands forbidden in a transaction."""
    return any(line.strip().lower() == NO_TRANSACTION_MARKER
               for line in source.splitlines()[:10])


def nontransactional_statements(source):
    """Split the deliberately-simple concurrent-index migration format.

    These files contain only line comments and top-level CREATE INDEX
    statements. Sending the whole file as one PostgreSQL query would still
    create an implicit transaction even with psycopg2 autocommit enabled.
    """
    sql = "\n".join(line for line in source.splitlines()
                    if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in sql.split(";")
            if statement.strip()]


def main(*, allow_remote=False):
    dsn = os.environ["DATABASE_URL"]
    if not is_local_database_url(dsn) and not allow_remote:
        raise RuntimeError(
            "refusing to replay the convenience migration runner against a "
            "remote database; apply reviewed SQL manually in production")
    mig_dir = os.path.join(HERE, "migrations")
    conn = psycopg2.connect(dsn)

    # Migration ledger: record which files have run so local re-application is
    # a no-op and drift is auditable (SELECT * FROM schema_migrations).
    with conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                           name        TEXT PRIMARY KEY,
                           applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cur.execute("SELECT name FROM schema_migrations")
        applied = {r[0] for r in cur.fetchall()}

    for name in sorted(os.listdir(mig_dir)):
        if not name.endswith(".sql"):
            continue
        if name in applied:
            print(f"skip {name} (already applied)")
            continue
        with open(os.path.join(mig_dir, name), encoding="utf-8") as handle:
            source = handle.read()
        if migration_requires_autocommit(source):
            # CREATE INDEX CONCURRENTLY is forbidden inside a transaction.
            # Each statement is idempotent, so a crash before the separate
            # ledger insert safely resumes the same file on the next run.
            conn.commit()
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    for statement in nontransactional_statements(source):
                        cur.execute(statement)
            finally:
                conn.autocommit = False
            with conn, conn.cursor() as cur:
                cur.execute("INSERT INTO schema_migrations (name) VALUES (%s) "
                            "ON CONFLICT (name) DO NOTHING", (name,))
        else:
            with conn, conn.cursor() as cur:
                cur.execute(source)
                cur.execute("INSERT INTO schema_migrations (name) VALUES (%s) "
                            "ON CONFLICT (name) DO NOTHING", (name,))
        print(f"applied {name}")
    conn.close()
    print("migrations applied")


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-remote-development-database", action="store_true",
        help="allow only an explicitly disposable remote development database",
    )
    args = parser.parse_args()
    try:
        main(allow_remote=args.allow_remote_development_database)
    except RuntimeError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    cli()
