import hashlib
import time
from unittest.mock import MagicMock

import pytest
from flask import Flask

from acquisition import (clean_touch, clean_attribution, TTL_MS, channel,
                         campaign_label, not_recorded, CHANNELS)
from admin_metrics import db as admin_db
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


def test_a_fast_browser_clock_is_clamped_not_rejected():
    now = 1_760_000_000_000
    cleaned = clean_touch({'source': 'www.bing.com', 'medium': 'organic',
                           'at': now + 3_600_000}, now=now)
    assert cleaned is not None and cleaned['at'] == now
    # The 30-day expiry still applies.
    assert clean_touch({'source': 'direct', 'at': now - TTL_MS - 1}, now=now) is None


def test_outreach_code_keeps_the_campaign_it_was_sent_with():
    now = 1_760_000_000_000
    tagged = clean_touch({'code': CODE, 'source': 'tiktok', 'medium': 'dm',
                          'campaign': 'ig_creators_2026_10', 'at': now}, now=now)
    assert (tagged['source'], tagged['medium'], tagged['campaign']) == \
        ('tiktok', 'dm', 'ig_creators_2026_10')
    bare = clean_touch({'code': CODE, 'at': now}, now=now)
    assert (bare['source'], bare['medium'], bare['campaign']) == \
        ('instagram', 'outreach', 'outreach_untagged')


@pytest.mark.parametrize('touch_, expected', [
    ({'source': 'chatgpt.com'}, ('ai_assistant', 'chatgpt', 'ChatGPT')),
    ({'source': 'openai'}, ('ai_assistant', 'chatgpt', 'ChatGPT')),
    ({'source': 'claude.ai', 'medium': 'referral'}, ('ai_assistant', 'claude', 'Claude')),
    ({'source': 'gemini.google.com', 'medium': 'organic'}, ('ai_assistant', 'gemini', 'Gemini')),
    ({'source': 'perplexity.ai'}, ('ai_assistant', 'perplexity', 'Perplexity')),
    ({'source': 'mail.google.com', 'medium': 'organic'}, ('email', 'gmail', 'Gmail')),
    ({'source': 'docs.google.com', 'medium': 'organic'}, ('other_website', 'docs.google.com', 'docs.google.com')),
    ({'source': 'www.bing.com', 'medium': 'organic'}, ('search', 'bing', 'Bing')),
    ({'source': 'bing.com'}, ('search', 'bing', 'Bing')),
    ({'source': 'cn.bing.com'}, ('search', 'bing', 'Bing')),
    ({'source': 'www.google.com', 'medium': 'organic'}, ('search', 'google', 'Google')),
    ({'source': 'google.co.uk'}, ('search', 'google', 'Google')),
    ({'source': 'google', 'medium': 'cpc'}, ('search', 'google_paid', 'Google Ads (paid)')),
    ({'source': 'yandex.ru'}, ('search', 'yandex', 'Yandex')),
    ({'source': 'tiktok', 'medium': 'social', 'content': 'in_app'}, ('social', 'tiktok', 'TikTok (in-app)')),
    ({'source': 'l.facebook.com', 'medium': 'social'}, ('social', 'facebook', 'Facebook')),
    ({'source': 't.co'}, ('social', 'x', 'X')),
    ({'source': 'gmail', 'medium': 'email'}, ('email', 'gmail', 'Gmail')),
    ({'source': 'direct', 'medium': 'none'}, ('no_referrer', 'direct', 'No referrer')),
    ({'source': 'example.org', 'medium': 'referral'}, ('other_website', 'example.org', 'example.org')),
    ({'source': 'instagram', 'medium': 'outreach', 'campaign': 'ig_creators_2026_10', 'code': CODE},
     ('outreach', 'ig_creators_2026_10', 'Ig creators 2026 10')),
    ({'source': 'instagram', 'medium': 'outreach', 'campaign': 'instagram_outreach', 'code': CODE},
     ('outreach', 'untagged', 'Untagged')),
    (None, ('not_recorded', None, None)),
    ({'source': 'valmera.io'}, ('not_recorded', None, None)),
    # Instagram tags every bio link itself; that is Instagram, not a
    # website called "ig".
    ({'source': 'ig', 'medium': 'social', 'content': 'link_in_bio'},
     ('social', 'instagram', 'Instagram')),
    ({'source': 'yt', 'medium': 'social'}, ('social', 'youtube', 'YouTube')),
    ({'source': 'bluesky', 'medium': 'social'}, ('social', 'bluesky', 'Bluesky')),
    ({'source': 'bsky.app', 'medium': 'social'}, ('social', 'bsky.app', 'bsky.app')),
    ({'source': 'bsky.app', 'medium': 'referral'},
     ('other_website', 'bsky.app', 'bsky.app')),
    # Google's AI tools are AI assistants, not Google Search.
    ({'source': 'notebooklm.google.com', 'medium': 'organic'},
     ('ai_assistant', 'notebooklm', 'NotebookLM')),
    ({'source': 'aistudio.google.com', 'medium': 'organic'},
     ('ai_assistant', 'aistudio', 'Google AI Studio')),
    ({'source': 'scholar.google.com', 'medium': 'organic'}, ('search', 'google', 'Google')),
])
def test_one_server_side_classifier(touch_, expected):
    c = channel(touch_)
    assert (c['channel'], c['detail'], c['detail_label']) == expected
    assert c['channel_label'] == dict(CHANNELS)[c['channel']]


def test_an_outreach_link_tagged_ig_names_instagram():
    c = channel({'source': 'ig', 'medium': 'outreach', 'campaign': 'spring',
                 'code': CODE})
    assert (c['channel'], c['network']) == ('outreach', 'Instagram')


def test_mcp_connector_landing_is_an_ai_assistant():
    assert channel({'source': 'direct'}, '/mcp/authorize')['detail'] == 'mcp'
    claude = channel({'source': 'claude.ai'}, '/mcp/authorize')
    assert (claude['channel'], claude['detail_label']) == \
        ('ai_assistant', 'Claude (connector)')


def test_campaign_labels_and_reasons_are_plain_words():
    assert campaign_label('ig_creators_2026_10') == 'Ig creators 2026 10'
    assert campaign_label('') == campaign_label('instagram_outreach') == 'Untagged'
    r = not_recorded('privacy_browser')
    assert r['channel'] == 'not_recorded' and 'Privacy browser' in r['reason_label']


def test_signup_snapshot_is_insert_only_and_keeps_first_and_latest(monkeypatch):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[]          # migration 031 not applied yet
    monkeypatch.setattr(wa,'connect',lambda:conn)
    wa.reset_schema_cache()
    data={'device_id':'device123','session_id':'session123','attribution':{'first':touch(),'last':{'source':'newsletter','at':time.time()*1000}}}
    wa.record_signup(123,data)
    sql,params=cur.execute.call_args.args
    assert 'ON CONFLICT(user_id) DO NOTHING' in sql
    assert 'is_verified=1' in sql
    assert params[2].adapted['first']['code']==CODE
    assert params[2].adapted['last']['source']=='newsletter'
    assert params[2].adapted['last']['code']==''
    wa.reset_schema_cache()


def _crm_client(monkeypatch, key):
    app=Flask(__name__);app.config['SECRET_KEY']='test';app.register_blueprint(admin.admin_bp,url_prefix='/admin')
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value={'value':hashlib.sha256(key.encode()).hexdigest()}
    monkeypatch.setattr(admin,'get_db',lambda:conn)
    # The owner's browsers (ever opened the admin) are never people.
    from admin_metrics import visitors as admin_visitors
    monkeypatch.setattr(admin_visitors,'internal_ids',lambda cur:['ownerdevice1'])
    admin_db.reset_features()
    return app.test_client(), cur


def test_admin_and_scoped_crm_report_authorization(monkeypatch):
    key='a'*48
    client, cur = _crm_client(monkeypatch, key)
    cur.fetchall.return_value=[]
    assert client.get('/admin/acquisition').status_code==401
    assert client.post('/admin/outreach-conversions',json={'codes':[CODE]}).status_code==401
    assert client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+'b'*48},json={'codes':[CODE]}).status_code==401
    response=client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+key},json={'codes':[CODE]})
    assert response.status_code==200 and response.json['rows']==[]
    assert response.json['version']==2
    query,params=cur.execute.call_args.args
    # Codes filter both signups and visits; the robot list and Meta's preview
    # referrers classify each browser (link previews are not people).
    assert params[0]==[CODE] and params[-1]==[CODE]
    assert 'facebookexternalhit' in params[1] and 'm.facebook.com' in params[2]
    assert params[3]==['ownerdevice1'] and 'AS internal' in query
    assert "amount_cents > 0" in query and 'link_previews' in query
    assert client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+key},json={'codes':['private@example.com']}).status_code==400
    admin_db.reset_features()


def test_crm_rows_keep_every_old_key_and_add_people_and_previews(monkeypatch):
    key='c'*48
    client, cur = _crm_client(monkeypatch, key)
    cur.fetchall.return_value=[{
        'model': 'first', 'source': 'instagram', 'medium': 'outreach',
        'campaign': 'instagram_outreach', 'code': CODE, 'visitors': 3,
        'people': 1, 'link_previews': 2, 'signups': 0, 'paying_users': 0,
        'revenue_usd_cents': 0}]
    body=client.post('/admin/outreach-conversions',headers={'Authorization':'Bearer '+key},json={'codes':[CODE]}).json
    row=body['rows'][0]
    for old in ('model','source','medium','campaign','code','visitors','signups','paying_users','revenue_usd_cents'):
        assert old in row
    assert (row['visitors'], row['people'], row['link_previews']) == (3, 1, 2)
    assert row['channel']=='outreach' and row['campaign_label']=='Untagged'
    assert body['version']==2 and body['truncated'] is False
    admin_db.reset_features()
