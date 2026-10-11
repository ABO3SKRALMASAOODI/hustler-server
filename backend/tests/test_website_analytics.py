import uuid
from unittest.mock import Mock
import pytest
from flask import Flask
import website_analytics as wa
from routes import admin


def data(**changes):
    return dict(device_id='d_test12345',session_id='s_test12345',visit_id=str(uuid.uuid4()),page='/register',active_seconds=17,**changes)

def test_sensitive_urls_and_values_are_not_stored():
    payload=data(events=[{'id':str(uuid.uuid4()),'kind':'register_error','status':502,'active_seconds':10,'email':'secret@example.com','password':'private'}, {'id':str(uuid.uuid4()),'kind':'password','value':'private'}],referrer='https://google.com/search?q=private')
    payload['page']='/google-callback/private-token?email=secret@example.com'
    cleaned=wa.clean_payload(payload)
    assert cleaned['page']=='/google-callback/[redacted]'
    assert cleaned['referrer']=='google.com'
    assert len(cleaned['events'])==1 and cleaned['events'][0][-1]==502
    assert 'private' not in str(cleaned) and 'secret@example' not in str(cleaned)

@pytest.mark.parametrize('value',[None,[],{'visit_id':'not-a-uuid'}, {'device_id':'token@secret.com','session_id':'session123'}])
def test_malformed_identity_is_rejected(value):
    with pytest.raises(ValueError): wa.clean_payload(value)

@pytest.mark.parametrize('ua',['AdsBot-Google','Google-NotebookLM','SEBot-WA','Mozilla HeadlessChrome','SolvedEarthPriceBot/2.0'])
def test_bots_do_not_open_database(monkeypatch,ua):
    connect=Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(wa,'connect',connect)
    assert wa.save_visit(data(),ua) is False
    connect.assert_not_called()

def test_measurement_failure_does_not_break_signup(monkeypatch):
    monkeypatch.setattr(wa,'connect',Mock(side_effect=RuntimeError('unavailable')))
    with Flask(__name__).app_context(): wa.record_signup(123,data())

def test_tracking_rejects_large_or_invalid_payload_and_reports_failed_storage(monkeypatch):
    app=Flask(__name__);app.register_blueprint(admin.admin_bp,url_prefix='/admin')
    client=app.test_client()
    assert client.post('/admin/journey',json=[]).status_code==400
    assert client.post('/admin/journey',data='x'*17000).status_code==413
    assert client.post('/admin/journey',json={}).status_code==400
    monkeypatch.setattr(wa,'save_visit',Mock(side_effect=RuntimeError('DB offline')))
    response=client.post('/admin/journey',json=data())
    assert response.status_code==503 and response.json=={'stored':False}

def test_new_reports_require_admin():
    app=Flask(__name__);app.config['SECRET_KEY']='test';app.register_blueprint(admin.admin_bp,url_prefix='/admin')
    assert app.test_client().get('/admin/journeys').status_code==401

def test_counters_are_finite_and_bounded():
    p=data(scroll_depth=999,events=[]);p['active_seconds']=float('inf')
    cleaned=wa.clean_payload(p)
    assert cleaned['scroll']==100 and cleaned['active']==0
    assert wa.safe_path('/enter-password?email=private@example.com')=='/enter-password'
    assert wa.safe_path('//evil.com/private')=='/[other]'


class _SchemaCursor:
    """Records SQL; answers the 031 column probe and the visit queries."""

    def __init__(self, columns):
        self.columns = columns
        self.sql = []
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append((' '.join(sql.split()), params))
        if 'information_schema.columns' in sql:
            self._next = ('all', [dict(table_name=t, column_name=c, is_nullable=n)
                                  for t, c, n in self.columns])
        elif 'count(*) AS n' in sql:
            self._next = ('one', {'n': 0})
        elif 'RETURNING analytics_id' in sql:
            self._next = ('one', {'analytics_id': 'x'})
        else:
            self._next = ('one', None)

    def fetchall(self):
        return self._next[1]

    def fetchone(self):
        kind, value = self._next
        return value[0] if kind == 'all' and value else value


class _SchemaConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


MIGRATED = [('page_visits', 'interacted', 'NO'), ('page_visits', 'signed_in', 'NO'),
            ('website_signups', 'tracking', 'YES'), ('website_signups', 'device_id', 'YES')]


def test_interaction_and_signed_in_only_ever_turn_on(monkeypatch):
    cur = _SchemaCursor(MIGRATED)
    monkeypatch.setattr(wa, 'connect', lambda: _SchemaConn(cur))
    wa.reset_schema_cache()
    assert wa.save_visit(data(interacted=True, signed_in=False), 'Mozilla/5.0 (iPhone)') is True
    insert = next(s for s, _ in cur.sql if 'INSERT INTO page_visits' in s)
    params = next(p for s, p in cur.sql if 'INSERT INTO page_visits' in s)
    assert 'interacted=page_visits.interacted OR EXCLUDED.interacted' in insert
    assert 'signed_in=page_visits.signed_in OR EXCLUDED.signed_in' in insert
    assert params[-2:] == (True, False)
    wa.reset_schema_cache()


def test_beacon_works_before_migration_031(monkeypatch):
    cur = _SchemaCursor([])
    monkeypatch.setattr(wa, 'connect', lambda: _SchemaConn(cur))
    wa.reset_schema_cache()
    assert wa.save_visit(data(interacted=True), 'Mozilla/5.0') is True
    insert = next(s for s, _ in cur.sql if 'INSERT INTO page_visits' in s)
    assert 'interacted' not in insert and 'signed_in' not in insert
    wa.reset_schema_cache()


def test_interaction_flags_accept_only_real_booleans():
    assert wa.clean_payload(data(interacted='yes'))['interacted'] is False
    assert wa.clean_payload(data(interacted=True, signed_in=True))['signed_in'] is True


@pytest.mark.parametrize('analytics, tracking', [
    ({'tracking': 'privacy_signal'}, 'privacy_signal'),
    (None, 'no_identity'),
    ({'device_id': 'x'}, 'no_identity'),
    (data(), 'tracked'),
])
def test_every_new_signup_gets_exactly_one_row_with_a_reason(monkeypatch, analytics, tracking):
    cur = _SchemaCursor(MIGRATED)
    monkeypatch.setattr(wa, 'connect', lambda: _SchemaConn(cur))
    wa.reset_schema_cache()
    with Flask(__name__).app_context():
        wa.record_signup(42, analytics)
    sql, params = next((s, p) for s, p in cur.sql if 'INSERT INTO website_signups' in s)
    assert 'tracking' in sql and 'ON CONFLICT(user_id) DO NOTHING' in sql
    assert params[3] == tracking and params[4] == 42
    if tracking == 'tracked':
        assert params[0] == 'd_test12345' and params[2] is not None
    else:
        # Privacy browsers and empty payloads store no ids and no labels.
        assert params[0] is None and params[1] is None and params[2] is None
    wa.reset_schema_cache()


def test_signup_without_ids_writes_nothing_before_migration_031(monkeypatch):
    cur = _SchemaCursor([])
    monkeypatch.setattr(wa, 'connect', lambda: _SchemaConn(cur))
    wa.reset_schema_cache()
    with Flask(__name__).app_context():
        wa.record_signup(42, {'tracking': 'privacy_signal'})
    assert not any('INSERT INTO website_signups' in s for s, _ in cur.sql)
    wa.reset_schema_cache()


def test_signup_write_failures_are_logged_and_counted(monkeypatch):
    monkeypatch.setattr(wa, 'connect', Mock(side_effect=RuntimeError('db down')))
    before = wa.SIGNUP_WRITE_FAILURES['count']
    with Flask(__name__).app_context():
        wa.record_signup(7, data())
    assert wa.SIGNUP_WRITE_FAILURES['count'] == before + 1


@pytest.mark.parametrize('ua', ['Mozilla/5.0 (compatible; GoogleOther)',
                                'Google-InspectionTool/1.0', 'facebookexternalhit/1.1',
                                'Mozilla/5.0 Claude/1.0', 'Bytespider'])
def test_shared_robot_list_drops_tools_on_the_write_path(monkeypatch, ua):
    connect = Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(wa, 'connect', connect)
    assert wa.save_visit(data(), ua) is False

