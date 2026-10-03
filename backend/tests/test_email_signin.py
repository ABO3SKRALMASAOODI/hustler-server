from unittest.mock import MagicMock, Mock
import secrets
import jwt
import pytest
from flask import Flask
from werkzeug.security import check_password_hash
from routes import email_signin as route


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'auth-tests-only-not-production-0000'
    app.register_blueprint(route.email_signin_bp, url_prefix='/auth')
    monkeypatch.setattr('website_analytics.record_signup', Mock())
    return app


@pytest.fixture
def db(monkeypatch):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    monkeypatch.setattr(route, 'get_db', Mock(return_value=conn))
    return conn, cur


@pytest.mark.parametrize('email', [None, [], '', 'x', 'x\n@y.com', 'a@bad..com', 'a' * 255 + '@example.com'])
def test_invalid_email_does_not_send(app, monkeypatch, email):
    send = Mock(); monkeypatch.setattr(route, 'send_code_to_email', send)
    assert app.test_client().post('/auth/email-code/start', json={'email': email}).status_code == 400
    send.assert_not_called()


def test_start_sends_hashed_code_without_creating_account(app, db, monkeypatch):
    conn, cur = db
    cur.fetchone.return_value = dict(daily=0, recent=0, burst=0)
    send = Mock(return_value=True); monkeypatch.setattr(route, 'send_code_to_email', send)
    response = app.test_client().post('/auth/email-code/start', json={'email': ' Test@Example.com '})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    email, code = send.call_args.args
    assert email == 'test@example.com' and len(code) == 6
    assert code not in response.get_data(as_text=True)
    sql = ' '.join(call.args[0] for call in cur.execute.call_args_list)
    assert 'INSERT INTO users' not in sql
    insert = next(c.args[1] for c in cur.execute.call_args_list if 'INSERT INTO email_signin_challenges' in c.args[0])
    assert insert[2] != code and len(insert[2]) == 64
    assert insert[0] == response.json['challenge']


@pytest.mark.parametrize('counts', [dict(daily=5,recent=0,burst=0),dict(daily=1,recent=1,burst=1),dict(daily=0,recent=0,burst=30)])
def test_request_limits_precede_delivery(app, db, monkeypatch, counts):
    db[1].fetchone.return_value = counts
    send = Mock(); monkeypatch.setattr(route,'send_code_to_email',send)
    response = app.test_client().post('/auth/email-code/start',json={'email':'test@example.com'})
    assert response.status_code == 429 and 'Retry-After' in response.headers
    send.assert_not_called()


def test_delivery_failure_never_claims_code_sent(app, db, monkeypatch):
    db[1].fetchone.return_value = dict(daily=0,recent=0,burst=0)
    monkeypatch.setattr(route, 'send_code_to_email', Mock(return_value=False))
    response = app.test_client().post('/auth/email-code/start',json={'email':'test@example.com'})
    assert response.status_code == 503 and 'challenge' not in response.json
    assert db[1].execute.call_args.args[1][:2] == (False, True)


def challenge_row(app, challenge, **changes):
    with app.app_context():
        row = dict(email='test@example.com', sent=True, consumed=False, valid_time=True, attempts=0,
                   code_hash=route.code_digest(challenge,'012345'))
    return {**row, **changes}


@pytest.mark.parametrize('changes', [dict(sent=False),dict(consumed=True),dict(valid_time=False),dict(attempts=5)])
def test_expired_consumed_or_exhausted_code_cannot_login(app,db,monkeypatch,changes):
    challenge=secrets.token_urlsafe(32);db[1].fetchone.return_value=challenge_row(app,challenge,**changes)
    account=Mock();monkeypatch.setattr(route,'verified_account',account)
    response=app.test_client().post('/auth/email-code/finish',json={'challenge':challenge,'code':'012345'})
    assert response.status_code==400 and response.json['restart']
    account.assert_not_called()


def test_wrong_code_persists_failed_attempt(app,db,monkeypatch):
    challenge=secrets.token_urlsafe(32);db[1].fetchone.return_value=challenge_row(app,challenge,attempts=4)
    account=Mock();monkeypatch.setattr(route,'verified_account',account)
    response=app.test_client().post('/auth/email-code/finish',json={'challenge':challenge,'code':'654321'})
    assert response.status_code==400 and response.json['restart']
    db[0].commit.assert_called_once();account.assert_not_called()
    assert 'attempts = attempts + 1' in db[1].execute.call_args.args[0]


def test_success_preserves_account_and_consumes_code(app,db,monkeypatch):
    challenge=secrets.token_urlsafe(32);db[1].fetchone.return_value=challenge_row(app,challenge)
    monkeypatch.setattr(route,'verified_account',Mock(return_value=(123,'Test@example.com','advanced',False)))
    response=app.test_client().post('/auth/email-code/finish',json={'challenge':challenge,'code':'012345'})
    assert response.status_code==200 and response.json['plan']=='advanced'
    claims=jwt.decode(response.json['token'],app.config['SECRET_KEY'],algorithms=['HS256'])
    assert claims['sub']=='123' and claims['email']=='Test@example.com'
    assert 'consumed = TRUE' in db[1].execute.call_args.args[0]
    db[0].commit.assert_called_once()


def test_verified_account_never_overwrites_paid_user(app):
    cur=MagicMock();cur.fetchall.return_value=[dict(id=4,email='Owner@example.com',plan='mcp',is_verified=1)]
    with app.app_context(): assert route.verified_account(cur,'owner@example.com','email')==(4,'Owner@example.com','mcp',False)
    assert not any('UPDATE users' in c.args[0] or 'INSERT INTO users' in c.args[0] for c in cur.execute.call_args_list)


def test_preclaimed_unverified_password_is_replaced(app):
    cur=MagicMock();cur.fetchall.return_value=[dict(id=4,email='owner@example.com',plan='free',is_verified=0)]
    with app.app_context(): assert route.verified_account(cur,'owner@example.com','email')[-1] is True
    sql,params=cur.execute.call_args.args
    assert 'password = %s' in sql and not check_password_hash(params[0],'attacker-password')


def test_ambiguous_legacy_case_accounts_are_not_merged(app):
    cur=MagicMock();cur.fetchall.return_value=[dict(id=1),dict(id=2)]
    with app.app_context(),pytest.raises(ValueError): route.verified_account(cur,'owner@example.com','email')


def test_abandoned_case_variant_does_not_block_verified_account(app):
    cur=MagicMock();cur.fetchall.return_value=[dict(id=1,is_verified=0),dict(id=2,is_verified=1,email='owner@example.com',plan='mcp')]
    with app.app_context(): assert route.verified_account(cur,'Owner@example.com','google')==(2,'owner@example.com','mcp',False)
    assert not any('UPDATE users' in c.args[0] for c in cur.execute.call_args_list)


def test_digest_binds_code_to_challenge_and_server_key(app):
    with app.app_context():
        a=route.code_digest('first','123456')
        assert a!=route.code_digest('second','123456')
        app.config['SECRET_KEY']='another-test-key'
        assert a!=route.code_digest('first','123456')
