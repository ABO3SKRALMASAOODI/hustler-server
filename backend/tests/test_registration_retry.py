"""Registration retries must issue usable codes without replacing accounts."""
import datetime
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.security import check_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('SKIP_DB_INIT', '1')
os.environ.setdefault('DATABASE_URL', 'postgresql://stub/stub')
from routes import auth, verify_email


class Cursor:
    def __init__(self, db): self.cur = db.cursor()
    def execute(self, sql, params=()):
        self.cur.execute(sql.replace('%s', '?'), params)
    def fetchone(self):
        row = self.cur.fetchone()
        if row is None: return None
        result = dict(row)
        for key in ('created_at',):
            if key in result and isinstance(result[key], str):
                result[key] = datetime.datetime.fromisoformat(result[key])
        return result
    def close(self): self.cur.close()


class Connection:
    def __init__(self, db): self.db = db
    def cursor(self): return Cursor(self.db)
    def commit(self): self.db.commit()
    def close(self): pass


@pytest.fixture
def signup(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.create_function('NOW', 0, lambda: datetime.datetime.utcnow().isoformat(' '))
    db.executescript('''
      CREATE TABLE users (
        id INTEGER PRIMARY KEY, email TEXT UNIQUE, password TEXT,
        is_verified INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        auth_provider TEXT, credits_daily NUMERIC, credits_bonus NUMERIC,
        credits_monthly NUMERIC, credits_balance NUMERIC);
      CREATE TABLE email_codes (
        email TEXT PRIMARY KEY, code TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    for module in (auth, verify_email):
        monkeypatch.setattr(module, 'get_db', lambda: Connection(db))
    sent = []
    monkeypatch.setattr(auth, 'send_code_to_email', lambda email, code: sent.append((email, code)) or True)
    app = Flask(__name__)
    app.register_blueprint(auth.auth_bp, url_prefix='/auth')
    app.register_blueprint(verify_email.verify_bp, url_prefix='/verify')
    yield app.test_client(), db, sent
    db.close()


@pytest.mark.parametrize('existing_user', [False, True])
def test_retry_replaces_expired_code_with_full_lifetime(signup, existing_user):
    client, db, sent = signup
    email = 'retry@example.com'
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat(' ')
    if existing_user:
        db.execute('INSERT INTO users(id,email,password,created_at) VALUES(42,?,?,?)', (email,'old',old))
    db.execute('INSERT INTO email_codes VALUES(?,?,?)', (email,'111111',old))
    db.commit()
    before = datetime.datetime.utcnow()
    r = client.post('/auth/register', json={'email':email,'password':'new-test-password'})
    assert r.status_code == (200 if existing_user else 201)
    row = db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if existing_user:
        assert row['id'] == 42
        assert row['created_at'] == old
    assert check_password_hash(row['password'], 'new-test-password')
    fresh = db.execute('SELECT * FROM email_codes WHERE email=?',(email,)).fetchone()
    assert datetime.datetime.fromisoformat(fresh['created_at']) >= before
    assert sent[-1] == (email, fresh['code'])
    r = client.post('/verify/verify-code',json={'email':email,'code':fresh['code']})
    assert r.status_code == 200
    assert db.execute('SELECT is_verified FROM users WHERE email=?',(email,)).fetchone()[0] == 1
    assert db.execute('SELECT * FROM email_codes').fetchone() is None


def test_verified_account_is_never_overwritten(signup):
    client,db,sent=signup
    db.execute("INSERT INTO users(email,password,is_verified) VALUES('existing@example.com','unchanged',1)")
    db.commit()
    r=client.post('/auth/register',json={'email':'existing@example.com','password':'replacement'})
    assert r.status_code == 409
    assert db.execute('SELECT password FROM users').fetchone()[0] == 'unchanged'
    assert sent == []


def test_delivery_failure_is_visible_and_retry_recovers(signup,monkeypatch):
    client,db,sent=signup
    monkeypatch.setattr(auth,'send_code_to_email',lambda *_:False)
    data={'email':'delivery@example.com','password':'test-password'}
    assert client.post('/auth/register',json=data).status_code == 502
    account_id=db.execute('SELECT id FROM users').fetchone()[0]
    monkeypatch.setattr(auth,'send_code_to_email',lambda email,code:sent.append((email,code)) or True)
    assert client.post('/auth/register',json=data).status_code == 200
    assert db.execute('SELECT id FROM users').fetchone()[0] == account_id
    assert client.post('/verify/verify-code',json={'email':data['email'],'code':sent[-1][1]}).status_code == 200


def test_signup_is_attributed_only_after_verification(signup, monkeypatch):
    import website_analytics
    client, db, sent=signup
    events=[]
    monkeypatch.setattr(website_analytics,'record_signup',lambda user_id,data:events.append((user_id,data)))
    email='journey@example.com'
    client.post('/auth/register',json={'email':email,'password':'local-test-password'})
    assert not events
    ids={'device_id':'d_test12345','session_id':'s_test12345'}
    bad=client.post('/verify/verify-code',json={'email':email,'code':'wrong','analytics':ids})
    assert bad.status_code==400 and not events
    good=client.post('/verify/verify-code',json={'email':email,'code':sent[-1][1],'analytics':ids})
    assert good.status_code==200 and len(events)==1 and events[0][1]==ids
    client.post('/verify/verify-code',json={'email':email,'code':sent[-1][1],'analytics':ids})
    assert len(events)==1
