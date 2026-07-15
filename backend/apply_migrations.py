"""Apply the video-editor migration (idempotent). Used by docker-compose dev
and CI; production schema is managed manually via psql per project
convention.

    python apply_migrations.py
"""

import os

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    # Base tables first (users, chat_sessions, ...) via the app's own init.
    from app import create_app
    create_app()

    mig_dir = os.path.join(HERE, "migrations")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    # Migration ledger: record which files have run so re-applying is a no-op
    # and drift is auditable (SELECT * FROM schema_migrations). The .sql files
    # are still idempotent, so this is a safety net + record, not the only
    # guard. Production is applied manually via psql; run this on prod once to
    # backfill the ledger for the already-applied 001-005.
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
        with conn, conn.cursor() as cur:
            cur.execute(open(os.path.join(mig_dir, name)).read())
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s) "
                        "ON CONFLICT (name) DO NOTHING", (name,))
        print(f"applied {name}")
    conn.close()
    print("migrations applied")


if __name__ == "__main__":
    main()
