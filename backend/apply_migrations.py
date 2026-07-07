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
    for name in sorted(os.listdir(mig_dir)):
        if not name.endswith(".sql"):
            continue
        with conn, conn.cursor() as cur:
            cur.execute(open(os.path.join(mig_dir, name)).read())
        print(f"applied {name}")
    conn.close()
    print("migrations applied")


if __name__ == "__main__":
    main()
