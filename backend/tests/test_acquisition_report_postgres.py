"""Source rollups must deduplicate people before combining link groups."""
import os
from urllib.parse import urlparse
from unittest.mock import MagicMock
import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
from acquisition import report

SETUP_SQL = '''
CREATE TEMP TABLE users(id int,is_verified int,email text);
INSERT INTO users VALUES (1,1,'one'),(2,1,'two'),(3,1,'unknown'),(4,0,'unverified'),(5,1,'excluded');
CREATE TEMP TABLE payments(user_id int,amount_cents int,status text,currency text);
INSERT INTO payments VALUES (1,1000,'completed','USD'),(1,1000,'paid','USD'),(2,0,'paid','USD'),(2,500,'pending','USD'),(2,600,'paid','EUR'),(5,9900,'paid','USD');
CREATE TEMP TABLE website_signups(user_id int,attribution jsonb);
INSERT INTO website_signups VALUES
(1,'{"first":{"source":"instagram","medium":"outreach","code":"AbcdEFgh1234_5678"},"last":{"source":"newsletter","medium":"email"}}'),
(2,'{"first":{"source":"instagram","medium":"outreach","code":"ZyxxEFgh1234_5678"},"last":{"source":"instagram","medium":"outreach","code":"ZyxxEFgh1234_5678"}}');
CREATE TEMP TABLE page_visits(device_id text,analytics_id text,attribution jsonb);
INSERT INTO page_visits VALUES
('browser1','v1','{"first":{"source":"instagram","medium":"outreach","code":"AbcdEFgh1234_5678"},"last":{"source":"instagram","medium":"outreach","code":"AbcdEFgh1234_5678"}}'),
('browser1','v2','{"first":{"source":"instagram","medium":"outreach","code":"ZyxxEFgh1234_5678"},"last":{"source":"newsletter","medium":"email"}}'),
('browser2','v3','{"first":{"source":"direct","medium":"none"},"last":{"source":"direct","medium":"none"}}');
'''

def assert_reports(run):
    source=run('source');ig=next(r for r in source['rows'] if r['model']=='first' and r['source']=='instagram')
    assert (ig['visitors'],ig['signups'],ig['paying_users'],ig['revenue_usd_cents'])==(1,2,2,2000)
    total=next(r for r in run('all')['rows'] if r['model']=='first')
    assert (total['visitors'],total['signups'],total['paying_users'])==(2,3,2)
    link=run('link');assert sum(r['visitors'] for r in link['rows'] if r['model']=='first')==3
    latest=next(r for r in source['rows'] if r['model']=='last' and r['source']=='newsletter')
    assert (latest['signups'],latest['paying_users'])==(1,1)

def test_source_counts_on_postgres():
    url=os.getenv('QUEUE_TEST_DATABASE_URL')
    if not url:pytest.skip('requires isolated CI PostgreSQL')
    assert urlparse(url).hostname in {'localhost','127.0.0.1','postgres'}
    conn=psycopg2.connect(url,cursor_factory=RealDictCursor)
    try:
        with conn,conn.cursor() as cur:
            cur.execute(SETUP_SQL)
            assert_reports(lambda grouping:report(cur,"u.email <> 'excluded'",group_by=grouping))
    finally:conn.close()

def test_report_rejects_untrusted_grouping():
    with pytest.raises(ValueError):report(MagicMock(),'TRUE',group_by='unsafe')
