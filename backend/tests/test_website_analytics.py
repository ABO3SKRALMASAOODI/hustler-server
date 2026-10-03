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
