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

    sql = open(os.path.join(HERE, "migrations", "001_video_editor.sql")).read()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn, conn.cursor() as cur:
        cur.execute(sql)
    conn.close()
    print("migrations applied")


if __name__ == "__main__":
    main()
