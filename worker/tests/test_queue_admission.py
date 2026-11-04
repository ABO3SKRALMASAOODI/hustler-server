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


@pytest.mark.parametrize('terminal_state', ['done', 'failed'])
def test_serial_short_job_runs_before_its_waiting_editor_and_new_followups(queue_db, terminal_state):
    """Real PostgreSQL: the parent must yield, and its dependency inherits priority."""
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO video_jobs
            (id,project_id,user_id,type,state,heartbeat_at,payload) VALUES
            (10,1,1,'agent_turn','running',now(),'{}'),
            (11,1,1,'agent_turn','queued',NULL,'{}'),
            (12,1,1,'shorts_plan','queued',NULL,'{"parent_agent_job_id":10}'),
            (13,1,1,'agent_turn','queued',NULL,'{"logical_turn_continuation":true,"root_agent_job_id":10}')''')
    with queue_db() as conn:
        assert db.claim_job(conn, ('shorts_plan',), 3) is None
        assert db.claim_job(conn, ('agent_turn',), 3) is None
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE video_jobs SET state='done' WHERE id=10")
    with queue_db() as conn:
        child = db.claim_job(conn, ('shorts_plan',), 3)
        assert child and child['id'] == 12
        assert db.claim_job(conn, ('agent_turn',), 3) is None
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE video_jobs SET state=%s WHERE id=12", (terminal_state,))
    with queue_db() as conn:
        assert db.claim_job(conn, ('agent_turn',), 3)['id'] == 13
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE video_jobs SET state='done' WHERE id=13")
    with queue_db() as conn:
        assert db.claim_job(conn, ('agent_turn',), 3)['id'] == 11


@pytest.mark.parametrize('parent_id,project_id,user_id,expected', [
    ('10', 1, 1, 0), ('10', 2, 1, 1), ('10', 1, 2, 1),
    ('999', 1, 1, 1), ('bad', 1, 1, 1), ('99999999999999999999999', 1, 1, 1)])
def test_serial_dependency_priority_requires_a_valid_same_owner_parent(
        queue_db, parent_id, project_id, user_id, expected):
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO video_jobs (id,project_id,user_id,type,state) VALUES (10,1,1,'agent_turn','done')")
        cur.execute('''INSERT INTO video_jobs (id,project_id,user_id,type,payload)
                       VALUES (12,%s,%s,'shorts_plan',%s::jsonb)''',
                    (project_id, user_id, json.dumps({'parent_agent_job_id': parent_id})))
        cur.execute('SELECT ' + db._serial_priority_sql('video_jobs') +
                    ' AS priority FROM video_jobs WHERE id=12')
        assert cur.fetchone()['priority'] == expected


def test_limits_leave_room_for_other_accounts(monkeypatch):
    monkeypatch.setattr(config, '_CLOUDFLARE_REMOTE_EXEC', True)
    for family, total, account in queue_admission.policies():
        assert 0 < account < total
        # A lone account grows past its share but always leaves a reserve.
        assert account <= queue_admission.lone_account_limit(total, account) < total
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
def box_wait_db(queue_db, monkeypatch):
    """A dispatcher died after persisting Box's pre-execution refusal."""
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("""ALTER TABLE video_jobs ADD COLUMN progress float DEFAULT 0;
            CREATE TABLE remote_executions (
                job_id int, total_claims int, provider text, call_id text,
                function_name text, state text, deadline_at timestamptz,
                submitted_at timestamptz DEFAULT now(), started_at timestamptz,
                last_observed_at timestamptz, completed_at timestamptz,
                error text, meta jsonb DEFAULT '{}', PRIMARY KEY(job_id,total_claims));
            INSERT INTO video_jobs (id,project_id,user_id,type,state,attempts,
                total_claims,heartbeat_at,payload)
                VALUES (1,1,1,'agent_turn','running',1,1,now()-interval '1 hour',
                        '{"execution_provider":"box"}');
            INSERT INTO remote_executions (job_id,total_claims,provider,call_id,
                function_name,state,deadline_at,error,meta)
                VALUES (1,1,'box','bx-refused','agent','cancelled',now()+interval '1 hour',
                        'No compatible slot; nothing ran',
                        '{"unstarted_box_wait":true,"unstarted_box_wait_call_id":"bx-refused"}');""")
    monkeypatch.setattr(db, 'remote_executions_table_ready', lambda conn: True)
    monkeypatch.setattr(config, 'MAX_ATTEMPTS_AGENT', 1)
    return queue_db


@pytest.mark.parametrize('state,started,recorded', [
    ('submitted', False, True), ('running', True, False), ('done', False, False),
])
def test_box_refusal_proof_is_bound_to_an_unadopted_call(box_wait_db, state, started, recorded):
    with box_wait_db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE remote_executions SET state=%s, meta='{}', started_at=CASE WHEN %s THEN now() ELSE NULL END",
                    (state, started))
        assert db.finish_remote_execution(conn, 1, 1, 'cancelled', 'Box refused',
                                          'box', 'bx-refused',
                                          unstarted_box_wait=True) is recorded
        cur.execute('SELECT state,meta FROM remote_executions WHERE job_id=1')
        row = cur.fetchone()
        if recorded:
            assert row['state'] == 'cancelled'
            assert row['meta'] == {'unstarted_box_wait': True,
                                   'unstarted_box_wait_call_id': 'bx-refused'}
        else:
            assert row['state'] == state and row['meta'] == {}


def test_restart_after_box_refusal_refunds_only_the_unstarted_attempt(box_wait_db):
    with box_wait_db() as conn:
        assert db.fail_exhausted_jobs(conn) == []
    with box_wait_db() as conn, conn.cursor() as cur:
        cur.execute('SELECT state,attempts,total_claims,payload FROM video_jobs WHERE id=1')
        row = cur.fetchone()
        assert (row['state'], row['attempts'], row['total_claims']) == ('queued', 0, 1)
        assert row['payload']['box_wait_claims'] == 1
        assert row['payload']['box_wait_deferrals'] == 1
        assert db.fail_ceilinged_jobs(conn) == []
        assert db.claim_job(conn, ('agent_turn',), 3) is None  # Keep the normal cooldown.
        cur.execute('UPDATE video_jobs SET updated_at=now()-make_interval(secs => %s)',
                    (config.CLOUDFLARE_BUSY_RETRY_DELAY_S + 1,))
        claimed = db.claim_job(conn, ('agent_turn',), 3)
        assert claimed and claimed['total_claims'] == 2
        # The old call cannot adopt the replacement claim or apply edits.
        assert not db.lease_is_current(conn, 1, 1)


@pytest.mark.parametrize('change,expected_state', [
    ("UPDATE remote_executions SET started_at=now()", 'failed'),
    ("UPDATE remote_executions SET meta='{}'", 'failed'),
    ('''UPDATE remote_executions SET meta='{"unstarted_box_wait":"true"}' ''', 'failed'),
    ("UPDATE remote_executions SET total_claims=0", 'failed'),
    ("UPDATE remote_executions SET provider='cloudflare'", 'failed'),
    ("UPDATE remote_executions SET call_id='bx-another-launch'", 'failed'),
    ("UPDATE video_jobs SET state='cancelled'", 'cancelled'),
    ("UPDATE video_jobs SET heartbeat_at=now()", 'running'),
    ("UPDATE remote_executions SET state='running',started_at=now()", 'running'),
])
def test_box_refusal_recovery_preserves_ambiguous_started_cancelled_and_newer_work(
        box_wait_db, change, expected_state):
    with box_wait_db() as conn, conn.cursor() as cur:
        cur.execute(change)
        db.fail_exhausted_jobs(conn)
        cur.execute('SELECT state,attempts FROM video_jobs WHERE id=1')
        row = cur.fetchone()
        assert row['state'] == expected_state and row['attempts'] == 1


def test_box_refusal_recovery_keeps_the_existing_deferral_limit(box_wait_db, monkeypatch):
    monkeypatch.setattr(config, 'BOX_MAX_WAIT_DEFERRALS', 1)
    with box_wait_db() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE video_jobs SET payload=payload || '{"box_wait_deferrals":1}'::jsonb''')
        assert [r['id'] for r in db.fail_exhausted_jobs(conn)] == [1]
        cur.execute('SELECT state FROM video_jobs WHERE id=1')
        assert cur.fetchone()['state'] == 'failed'


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
    # A fixed number of concurrent polls does not promise saturation:
    # advisory-lock losers deliberately yield without claiming. Finish with
    # uncontended polls, then retain the exact fleet/share/untouched-attempt
    # assertions below. Any parallel over-admission remains visible here.
    for _ in range(31):
        if claim(None) is None:
            break
    # The share is work-conserving (Oct 2026): it binds while another
    # account waits; once account 2 runs, account 1 grows to the fleet less
    # the reserve (queue_admission.lone_account_limit: 18 of 20).
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id,count(*) n FROM video_jobs WHERE state='running' GROUP BY 1")
        assert {r['user_id']: r['n'] for r in cur.fetchall()} == {1:18, 2:1}
        cur.execute("SELECT count(*) n FROM video_jobs WHERE state='queued' AND attempts=0 AND total_claims=0")
        assert cur.fetchone()['n'] == 12
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
    # Nobody else waits now, so account 1 may exceed its share of 2.
    with queue_db() as conn:
        assert db.claim_job(conn, ('final',), 3)['user_id'] == 1


def test_account_share_binds_while_another_account_waits(queue_db):
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO video_jobs (project_id,user_id,type,state,heartbeat_at)
            VALUES (1,1,'index','running',now()), (2,1,'index','running',now());
            INSERT INTO video_jobs (project_id,user_id,type)
            VALUES (3,1,'index'), (4,2,'index')''')
    with queue_db() as conn:
        assert db.claim_job(conn, ('index',), 3)['user_id'] == 2
    with queue_db() as conn:
        assert db.claim_job(conn, ('index',), 3)['user_id'] == 1
