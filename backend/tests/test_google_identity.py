from unittest.mock import Mock, MagicMock
from urllib.parse import urlparse, parse_qs
import base64
import hashlib
import pytest
from flask import Flask
from routes import google_auth as route


@pytest.fixture
def app(monkeypatch):
    app=Flask(__name__);app.config['SECRET_KEY']='auth-test-only-secret-0000000000000'
    app.register_blueprint(route.google_auth_bp,url_prefix='/auth')
    monkeypatch.setenv('GOOGLE_CLIENT_ID','test.apps.googleusercontent.com')
    monkeypatch.delenv('GOOGLE_REDIRECT_URI',raising=False)
    monkeypatch.setenv('BACKEND_URL','https://legacy.example.invalid')
    return app


def test_start_uses_valmera_domain_random_state_pkce_and_secure_cookie(app):
    client=app.test_client();response=client.get('/auth/google/login',base_url='https://valmera.io')
    query=parse_qs(urlparse(response.location).query)
    assert query['redirect_uri']==['https://valmera.io/api-backend/auth/google/callback']
    assert query['scope']==['openid email profile'] and query['code_challenge_method']==['S256']
    cookie=response.headers['Set-Cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Path=/' in cookie
    with app.app_context():
        saved=route._state_signer().loads(client.get_cookie('__Host-valmera-google',domain='valmera.io').value)
    assert saved['state']==query['state'][0]
    assert base64.urlsafe_b64encode(hashlib.sha256(saved['verifier'].encode()).digest()).rstrip(b'=').decode()==query['code_challenge'][0]
    assert response.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('state',['','forged'])
def test_callback_rejects_missing_or_forged_state_before_google(app,monkeypatch,state):
    post=Mock();monkeypatch.setattr(route.requests,'post',post)
    client=app.test_client();client.get('/auth/google/login',base_url='https://valmera.io')
    response=client.get('/auth/google/callback?code=anything&state='+state,base_url='https://valmera.io')
    assert 'google_failed' in response.location
    post.assert_not_called()
    assert 'Max-Age=0' in response.headers['Set-Cookie']


@pytest.mark.parametrize('verified',[False,True])
def test_callback_requires_verified_google_email_and_uses_pkce(app,monkeypatch,verified):
    post=Mock(return_value=Mock(json=lambda:{'access_token':'test-access-token'}));monkeypatch.setattr(route.requests,'post',post)
    monkeypatch.setattr(route.requests,'get',Mock(return_value=Mock(json=lambda:{'email':'test@example.com','verified_email':verified})))
    conn=MagicMock();monkeypatch.setattr(route,'get_db',Mock(return_value=conn))
    account=Mock(return_value=(1,'test@example.com','mcp',False));monkeypatch.setattr(route,'verified_account',account)
    client=app.test_client();start=client.get('/auth/google/login',base_url='https://valmera.io')
    state=parse_qs(urlparse(start.location).query)['state'][0]
    response=client.get('/auth/google/callback?code=anything&state='+state,base_url='https://valmera.io')
    assert 'code_verifier' in post.call_args.kwargs['data']
    if verified:
        assert '/google-callback/' in response.location and 'token=' not in response.location
        account.assert_called_once();conn.commit.assert_called_once()
    else:
        assert 'google_failed' in response.location;account.assert_not_called()
