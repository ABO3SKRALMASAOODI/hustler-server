"""Verify attribution against real, disposable PostgreSQL data in CI."""
import os
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

from video_services.conversion_report import read_report


def test_campaigns_and_checkout_count_people_and_confirmed_money():
    url = os.getenv('QUEUE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires the isolated CI PostgreSQL service')
    assert urlparse(url).hostname in {'localhost', '127.0.0.1', 'postgres'}
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        with conn, conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE users (id int,email text);
                INSERT INTO users VALUES (1,'customer'),(2,'other'),(3,'owner');
                CREATE TEMP TABLE client_events (id serial,user_id int,kind text,
                    detail jsonb,created_at timestamptz DEFAULT now()-interval '2 days');
                CREATE TEMP TABLE payments (user_id int,status text,amount_cents int,
                    occurred_at timestamptz DEFAULT now()-interval '1 day');
                CREATE TEMP TABLE video_jobs (user_id int,state text,type text,
                    updated_at timestamptz DEFAULT now()-interval '1 day');
                INSERT INTO client_events (user_id,kind,detail) VALUES
                    (1,'checkout_stage','{"stage":"opened"}'),
                    (1,'checkout_stage','{"stage":"opened"}'),
                    (1,'checkout_stage','{"stage":"completed"}'),
                    (2,'checkout_stage','{"stage":"opened"}'),
                    (3,'checkout_stage','{"stage":"opened"}'),
                    (1,'campaign_visit','{"campaign":"old","source":"valmera","medium":"email"}'),
                    (1,'campaign_visit','{"campaign":"new","source":"valmera","medium":"email"}'),
                    (2,'campaign_visit','{"campaign":"new","source":"valmera","medium":"email"}'),
                    (3,'campaign_visit','{"campaign":"new","source":"valmera","medium":"email"}');
                INSERT INTO payments (user_id,status,amount_cents) VALUES
                    (1,'completed',1500),(1,'completed',1500),
                    (2,'completed',0),(2,'ready',1500),(3,'completed',1500);
                INSERT INTO video_jobs (user_id,state,type) VALUES
                    (1,'done','final'),(1,'done','final'),(2,'failed','final');''')
            result = read_report(cur, "u.email <> 'owner'")
            assert result['checkout']['opened'] == 2
            assert result['checkout']['confirmed_payers'] == 1
            assert result['campaigns'] == [dict(campaign='new', source='valmera',
                medium='email', visitors=2, exporters=1, payers=1)]
    finally:
        conn.close()
