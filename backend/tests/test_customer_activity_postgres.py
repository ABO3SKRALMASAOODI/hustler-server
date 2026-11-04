"""Exercise activity ownership, per-source limits and ordering in PostgreSQL."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

from admin_metrics import customers

DSN = os.environ.get("ADMIN_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="local/CI PostgreSQL required")


@pytest.fixture
def activity_db():
    params = psycopg2.extensions.parse_dsn(DSN)
    if (params.get('host') not in ('localhost','127.0.0.1')
            or params.get('dbname') not in ('valmera_test','valmera_migration_test')):
        pytest.fail('SQL fixtures require an explicitly configured local test database')
    schema = "activity_" + uuid.uuid4().hex
    conn = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute('CREATE SCHEMA "' + schema + '"')
        cur.execute('SET search_path TO "' + schema + '"')
        cur.execute('''CREATE TABLE projects(id int PRIMARY KEY,user_id int,
            title text,chat_session_id int);
          CREATE TABLE chat_messages(id int PRIMARY KEY,session_id int,
            role text,created_at timestamp,content text);
          CREATE TABLE assets(id int PRIMARY KEY,project_id int,kind text,
            created_at timestamptz,meta jsonb);
          CREATE TABLE video_jobs(id int PRIMARY KEY,user_id int,project_id int,
            type text,state text,updated_at timestamptz);
          CREATE INDEX ON chat_messages(session_id,role,id);
          CREATE INDEX ON projects(user_id)''')
        cur.execute("INSERT INTO projects VALUES (1,60,'one',10),(2,60,'two',20),(3,61,'other',30)")
        base = datetime(2026, 11, 4, tzinfo=timezone.utc)
        own_messages = []
        for i in range(1, 81):
            project = 1 if i % 2 else 2
            at = base + timedelta(seconds=i)
            cur.execute('INSERT INTO chat_messages VALUES(%s,%s,%s,%s,%s)',
                        (i,project*10,'user',at.replace(tzinfo=None),f'own-{i}'))
            own_messages.append((i, at, 'message', project, f'own-{i}'))
        cur.execute("""INSERT INTO chat_messages
          SELECT 1000+i,30,'user',TIMESTAMP '2026-11-05','other'
            FROM generate_series(1,300) i""")
        cur.execute("INSERT INTO chat_messages VALUES (2000,10,'assistant','2026-11-06','assistant')")
        cur.execute("INSERT INTO assets VALUES (1,1,'original',%s,'{\"filename\":\"upload\"}'),(2,3,'original',%s,'{\"filename\":\"other\"}')",
                    (base+timedelta(seconds=82),base+timedelta(days=2)))
        cur.execute("INSERT INTO video_jobs VALUES (1,60,2,'final','done',%s),(2,60,1,'final','failed',%s),(3,61,3,'final','done',%s),(4,60,1,'preview','done',%s)",
                    (base+timedelta(seconds=81),base+timedelta(seconds=83),base+timedelta(days=2),base+timedelta(days=3)))
    try:
        yield conn, own_messages, base
    finally:
        with conn.cursor() as cur:
            cur.execute('SET search_path TO public')
            cur.execute('DROP SCHEMA "' + schema + '" CASCADE')
        conn.close()


@pytest.mark.parametrize('limit',[1,3,40])
def test_activity_keeps_owned_recent_requests_uploads_and_exports(activity_db,limit):
    conn, messages, base = activity_db
    with conn.cursor() as cur:
        got = customers.customer_activity(cur,60,[],limit=limit)
    expected = sorted(messages,key=lambda r:r[0],reverse=True)[:limit]
    expected += [(1,base+timedelta(seconds=82),'upload',1,'upload')]
    expected += sorted([
        (1,base+timedelta(seconds=81),'export',2,None),
        (2,base+timedelta(seconds=83),'export_failed',1,None),
    ],key=lambda r:r[0],reverse=True)[:limit]
    expected = sorted(expected,key=lambda r:r[1],reverse=True)[:limit]
    assert [(r['kind'],r['project']['id'],r['text']) for r in got] == [
        (r[2],r[3],r[4]) for r in expected]


def test_recent_activity_does_not_multiply_unrelated_messages(activity_db):
    conn, _, _ = activity_db
    with conn.cursor() as cur:
        # An MCP-heavy account owns many projects, but its last chat request
        # predates many requests by other users. LIMIT cannot assume that the
        # globally newest messages belong to this account.
        cur.execute("""INSERT INTO projects
          SELECT 1000+i,60,'MCP project',10000+i
            FROM generate_series(1,1000) i;
          INSERT INTO chat_messages
          SELECT 3000+i,30,'user',TIMESTAMP '2026-11-05','unrelated'
            FROM generate_series(1,20000) i;
          ANALYZE projects; ANALYZE chat_messages;
          ANALYZE assets; ANALYZE video_jobs""")

        class Capture:
            def execute(self, sql, params):
                self.sql, self.params = sql, params
                cur.execute(sql, params)

            def fetchall(self):
                return cur.fetchall()

        captured = Capture()
        customers.customer_activity(captured,60,[],limit=1)
        cur.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + captured.sql,
                    captured.params)
        plan = cur.fetchone()['QUERY PLAN'][0]['Plan']

    def work(node):
        rows = sum(node.get(key,0) for key in (
            'Actual Rows','Rows Removed by Filter','Rows Removed by Join Filter'))
        return rows * node['Actual Loops'] + sum(work(p) for p in node.get('Plans',[]))

    # Bound actual work, not wall time: CI speed and concurrent load vary.
    # The regressed nested loop visits >20 million row combinations here.
    source_rows = 20381 + 1003
    assert work(plan) < 20 * source_rows
