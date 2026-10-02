"""Admission policy and real PostgreSQL concurrency regression coverage."""
import os
import uuid
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

import config
import db
import queue_admission


def test_limits_leave_room_for_other_accounts(monkeypatch):
    monkeypatch.setattr(config, '_CLOUDFLARE_REMOTE_EXEC', True)
    for family, total, account in queue_admission.policies():
        assert 0 < account < total
    mcp = next(p for p in queue_admission.policies() if 'mcp_tool' in p[0])
    assert mcp[2] >= 3  # the supported three-editor marketing workflow
    deployed = json.loads((Path(__file__).resolve().parents[1] / 'cloudflare/wrangler.jsonc').read_text())
    pools = {row['class_name']:row['max_instances'] for row in deployed['containers']}
    assert [p[1] for p in queue_admission.policies()] == [pools[key] for key in
        ('ValmeraMcp','ValmeraAgent','ValmeraShorts','ValmeraBatch','ValmeraInteractive')]


def test_local_dispatcher_keeps_its_existing_small_limits(monkeypatch):
    monkeypatch.setattr(config, '_CLOUDFLARE_REMOTE_EXEC', False)
    assert queue_admission.claim_filter(('mcp_tool',), False) == ('', [])


@pytest.fixture
def queue_db(monkeypatch):
    url = os.getenv('QUEUE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires the isolated CI PostgreSQL service')
    # Deliberately separate from DATABASE_URL: production credentials must
    # never be sufficient to opt into this schema-writing test fixture.
    from urllib.parse import urlparse
    assert urlparse(url).hostname in {'localhost', '127.0.0.1', 'postgres'}
    schema = 'queue_test_' + uuid.uuid4().hex
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA {schema}')
        cur.execute(f'SET search_path TO {schema}')
        cur.execute('''CREATE TABLE users (id int PRIMARY KEY, is_subscribed int);
            INSERT INTO users VALUES (1,1), (2,1);
            CREATE TABLE assets (id serial, project_id int, kind text, sha256 text);
            CREATE TABLE indexes (video_sha256 text);
            CREATE TABLE video_jobs (id serial PRIMARY KEY, project_id int,
                user_id int, type text, state text DEFAULT 'queued',
                attempts int DEFAULT 0, total_claims int DEFAULT 0,
                payload jsonb DEFAULT '{}', heartbeat_at timestamptz,
                updated_at timestamptz DEFAULT now(), error text)''')
    monkeypatch.setattr(config, '_CLOUDFLARE_REMOTE_EXEC', True)
    monkeypatch.setattr(db, 'claims_column_ready', lambda conn: True)
    monkeypatch.setattr(db, 'remote_executions_table_ready', lambda conn: False)
    @contextmanager
    def connect():
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO {schema}')
            conn.commit()
            with conn:
                yield conn
        finally:
            conn.close()
    try:
        yield connect
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA {schema} CASCADE')
        admin.close()


def test_parallel_claims_cannot_exhaust_pool_for_one_account(queue_db):
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO video_jobs (project_id,user_id,type)
            SELECT n,1,'mcp_tool' FROM generate_series(1,30) n;
            INSERT INTO video_jobs (project_id,user_id,type)
            VALUES (100,2,'mcp_tool')''')
    def claim(_):
        with queue_db() as conn:
            return db.claim_job(conn, ('mcp_tool',), 1)
    # Repeat bounded polls because try-lock losers intentionally yield.
    with ThreadPoolExecutor(max_workers=12) as pool:
        for _ in range(8):
            list(pool.map(claim, range(12)))
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id,count(*) n FROM video_jobs WHERE state='running' GROUP BY 1")
        assert {r['user_id']: r['n'] for r in cur.fetchall()} == {1:6, 2:1}
        cur.execute("SELECT count(*) n FROM video_jobs WHERE state='queued' AND attempts=0 AND total_claims=0")
        assert cur.fetchone()['n'] == 24
        cur.execute("UPDATE video_jobs SET state='done' WHERE id=(SELECT min(id) FROM video_jobs WHERE state='running' AND user_id=1)")
    assert claim(None)['user_id'] == 1


def test_index_and_final_share_the_account_budget(queue_db):
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO video_jobs (project_id,user_id,type,state,heartbeat_at)
            VALUES (1,1,'index','running',now()), (2,1,'final','running',now());
            INSERT INTO video_jobs (project_id,user_id,type)
            VALUES (3,1,'final'), (4,2,'final')''')
    with queue_db() as conn:
        assert db.claim_job(conn, ('final',), 3)['user_id'] == 2
    with queue_db() as conn:
        assert db.claim_job(conn, ('final',), 3) is None
