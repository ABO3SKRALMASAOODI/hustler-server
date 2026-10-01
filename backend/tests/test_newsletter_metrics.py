"""Audience activity from every product signal, using isolated PostgreSQL."""
import os
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

from video_services.newsletter_metrics import read_counts


def test_counts_preserve_activity_windows_and_completed_exports():
    url = os.getenv('QUEUE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires the isolated CI PostgreSQL service')
    assert urlparse(url).hostname in {'localhost', '127.0.0.1', 'postgres'}
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        with conn, conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE users (id int, is_verified int,
                unsubscribed_at timestamptz, created_at timestamptz, plan text);
                INSERT INTO users SELECT id,1,NULL,now()-interval '40 days','free'
                    FROM generate_series(1,6) id;
                UPDATE users SET created_at=now()-interval '1 day' WHERE id=1;
                UPDATE users SET plan='ai_pro' WHERE id=2;
                UPDATE users SET unsubscribed_at=now() WHERE id=6;
                CREATE TEMP TABLE client_events (user_id int,created_at timestamptz);
                CREATE TEMP TABLE projects (user_id int,created_at timestamptz);
                CREATE TEMP TABLE chat_sessions (user_id int,created_at timestamptz);
                CREATE TEMP TABLE video_jobs (user_id int,created_at timestamptz,type text,state text);
                INSERT INTO client_events VALUES (2,now()-interval '1 day'),(2,now()-interval '1 day');
                INSERT INTO projects VALUES (3,now()-interval '5 days'),(4,now()-interval '40 days');
                INSERT INTO chat_sessions VALUES (4,now()-interval '1 day');
                INSERT INTO video_jobs VALUES
                    (3,now()-interval '5 days','final','failed'),
                    (4,now()-interval '40 days','final','done');''')
            counts = read_counts(cur, 'u.is_verified=1 AND u.unsubscribed_at IS NULL', "('done', 'completed')")
            assert counts == dict(verified=5, unsubscribed=1, new_7d=1,
                                  active=3, dormant=1, inactive=1, paid=1,
                                  never_exported=1)
    finally:
        conn.close()
