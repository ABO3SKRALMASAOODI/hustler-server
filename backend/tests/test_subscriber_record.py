from datetime import datetime, timezone
from admin_metrics.subscriber_record import build_record, cancellation_times


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def event(sid, at, status='active', action=None, kind='subscription.updated', true=True, end=None):
    return dict(subscription_id=sid, provider_updated_at=dt(at), status=status,
                scheduled_action=action, scheduled_effective_at=dt(end) if end else None,
                canceled_at=dt(end) if end and status=='canceled' else None,
                next_billed_at=dt('2026-11-01'), event_id='evt_'+at if true else None,
                event_type=kind if true else None)


def contract(sid, uid, at):
    return dict(subscription_id=sid,user_id=uid,first_paid_at=dt(at))


def test_peak_counts_people_once_and_excludes_cancelled_before_access_ends():
    c=[contract('a',1,'2026-09-10'),contract('b',1,'2026-09-11'),contract('c',2,'2026-09-12')]
    h=[event(x['subscription_id'],x['first_paid_at'].isoformat(),kind='subscription.created') for x in c]
    h += [event('c','2026-09-13',action='cancel',end='2026-10-12'),
          event('a','2026-09-14',action='cancel',end='2026-10-10')]
    r=build_record(c,h,dt('2026-09-20'))
    assert r['complete'] and r['current']==1 and r['high']==2
    assert r['high_at']=='2026-09-12T00:00:00Z'
    assert next(x for x in r['series'] if x['day']=='2026-09-13')['value']==2


def test_trial_unpaid_and_past_due_are_not_active_paid_people_and_resume_counts():
    c=[contract('a',1,'2026-09-12')]
    h=[event('a','2026-09-10','trialing',kind='subscription.created'),
       event('a','2026-09-12',kind='subscription.activated'),
       event('a','2026-09-13','past_due'),event('a','2026-09-15',action='cancel',end='2026-10-12')]
    assert build_record(c,h,dt('2026-09-11'))['current']==0
    assert build_record(c,h,dt('2026-09-14'))['current']==0
    h.append(event('a','2026-09-16'))
    r=build_record(c,h,dt('2026-09-20'))
    assert r['current']==1 and r['high']==1
    assert cancellation_times(h)=={'cancelled_at':None,'plan_ends_at':None}


def test_out_of_order_replays_and_gets_do_not_move_cancellation_time():
    h=[event('a','2026-09-01',kind='subscription.created'),
       event('a','2026-09-10',action='cancel',end='2026-10-01'),
       event('a','2026-09-11',action='cancel',true=False,end='2026-10-01'),
       event('a','2026-10-01','canceled',end='2026-10-01')]
    r=cancellation_times(list(reversed(h)))
    assert r=={'cancelled_at':'2026-09-10T00:00:00Z','plan_ends_at':'2026-10-01T00:00:00Z'}
    # A GET of an already cancelled plan cannot prove when it was requested.
    assert cancellation_times(h[2:])['cancelled_at'] is None


def test_cancel_resume_cancel_uses_latest_request_and_immediate_end_is_distinct():
    h=[event('a','2026-09-01'),event('a','2026-09-10',action='cancel',end='2026-10-01'),
       event('a','2026-09-11'),event('a','2026-09-12',action='cancel',end='2026-10-01')]
    assert cancellation_times(h)['cancelled_at']=='2026-09-12T00:00:00Z'
    h=[event('b','2026-09-01'),event('b','2026-09-05','canceled',end='2026-09-05')]
    assert cancellation_times(h)['cancelled_at']=='2026-09-05T00:00:00Z'


def test_missing_history_never_claims_all_time_high():
    c=[contract('a',1,'2026-09-01')]
    r=build_record(c,[event('a','2026-09-20',true=False)],dt('2026-09-21'))
    assert not r['complete'] and r['missing_contracts']==1
    assert r['high']==1


def test_simultaneous_contract_switch_does_not_create_false_peak():
    c=[contract('a',1,'2026-09-01'),contract('b',2,'2026-09-02')]
    h=[event('a','2026-09-01',kind='subscription.created'),event('a','2026-09-02',action='cancel'),
       event('b','2026-09-02',kind='subscription.created')]
    assert build_record(c,h,dt('2026-09-03'))['high']==1


def test_postgres_peak_preserves_deleted_customers_but_excludes_owner(monkeypatch):
    import os
    from urllib.parse import urlparse
    import pytest
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from admin_metrics import subscriber_record as mod
    url=os.getenv('QUEUE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires isolated CI PostgreSQL')
    assert urlparse(url).hostname in {'localhost','127.0.0.1','postgres'}
    conn=psycopg2.connect(url,cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE users(id int,email text,is_verified int,created_at timestamp);
                CREATE TEMP TABLE payments(user_id int,subscription_id text,occurred_at timestamp,
                    created_at timestamp,status text,amount_cents int);
                CREATE TEMP TABLE admin_subscription_history(subscription_id text,
                    provider_updated_at timestamptz,status text,scheduled_action text,next_billed_at timestamptz,
                    event_id text,event_type text);
                INSERT INTO users VALUES (1,'customer@example.test',1,'2026-08-01'),
                    (2,'thevalmera@gmail.com',1,'2026-08-01'),(3,'old@example.test',1,'2025-01-01');
                INSERT INTO payments SELECT id, 'sub_'||id, '2026-09-01', '2026-09-01','paid',1500
                    FROM generate_series(1,4) id;
                INSERT INTO admin_subscription_history SELECT 'sub_'||id, '2026-09-01','active',NULL,
                    '2027-09-01','evt_'||id,'subscription.created' FROM generate_series(1,4) id;''')
            monkeypatch.setattr(mod.db,'has_table',lambda cur,table: True)
            result=mod.read_record(cur)
            assert result['current']==2 and result['high']==2 and result['complete']
    finally:
        conn.rollback();conn.close()
