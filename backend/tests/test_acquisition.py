import hashlib
import time
from unittest.mock import MagicMock
from flask import Flask
from acquisition import clean_touch, clean_attribution, TTL_MS
from routes import admin
import website_analytics as wa

CODE='AbcdEFgh1234_5678'
def touch(**kwargs):
    return dict(source='instagram',medium='outreach',campaign='instagram_outreach',code=CODE,at=int(time.time()*1000),**kwargs)

def test_only_bounded_campaign_labels_and_opaque_codes_are_kept():
    cleaned=clean_touch(touch(email='private@example.com',password='secret'))
    assert set(cleaned)=={'source','medium','campaign','content','code','at'}
    assert cleaned['code']==CODE
    assert clean_touch({'source':'secret@example.com','at':time.time()*1000}) is None
    assert clean_touch({'source':'direct','at':float('nan')}) is None
    assert clean_touch({'source':'direct','at':time.time()*1000-TTL_MS-1000}) is None
    assert clean_attribution([])=={'first':None,'last':None}

def test_signup_snapshot_is_insert_only_and_keeps_first_and_latest(monkeypatch):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(wa,'connect',lambda:conn)
    data={'device_id':'device123','session_id':'session123','attribution':{'first':touch(),'last':{'source':'newsletter','at':time.time()*1000}}}
    wa.record_signup(123,data)
    sql,params=cur.execute.call_args.args
    assert 'ON CONFLICT(user_id) DO NOTHING' in sql
    assert 'is_verified=1' in sql
    assert params[2].adapted['first']['code']==CODE
    assert params[2].adapted['last']['source']=='newsletter'
    assert params[2].adapted['last']['code']==''

def test_admin_and_scoped_crm_report_authorization(monkeypatch):
    app=Flask(__name__);app.config['SECRET_KEY']='test';app.register_blueprint(admin.admin_bp,url_prefix='/admin')
    client=app.test_client()
    assert client.get('/admin/acquisition').status_code==401
    assert client.post('/admin/outreach-conversions',json={'codes':[CODE]}).status_code==401
    key='a'*48; conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value={'value':hashlib.sha256(key.encode()).hexdigest()}
    cur.fetchall.return_value=[]
    monkeypatch.setattr(admin,'get_db',lambda:conn)
    assert client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+'b'*48},json={'codes':[CODE]}).status_code==401
    response=client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+key},json={'codes':[CODE]})
    assert response.status_code==200 and response.json['rows']==[]
    query,params=cur.execute.call_args.args
    assert params==[[CODE],[CODE]] and "amount_cents > 0" in query
    assert client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+key},json={'codes':['private@example.com']}).status_code==400
