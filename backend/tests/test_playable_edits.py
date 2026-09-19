"""The fast path commits documents atomically without dispatching media work."""
import contextlib
import copy
import os
from pathlib import Path
import sys
import pytest
from flask import Flask
os.environ.setdefault('SKIP_DB_INIT','1')
os.environ.setdefault('DATABASE_URL','postgresql://stub/stub')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from routes import video


@pytest.fixture
def api(monkeypatch):
    class Store:
        edls={1:video.wschemas.default_edl(20)}
        messages=[]
        busy=False
        subscribed=True
        owned=True
        def cursor(self):return self
        def execute(self,sql,args=()):
            self.one=None;self.many=[]
            q=' '.join(sql.split())
            if 'pg_advisory_xact_lock' in q:return
            if q.startswith('SELECT id FROM video_jobs'):self.one={'id':9} if self.busy else None
            elif q.startswith('SELECT storage_key,duration_s FROM assets'):self.many=[{'storage_key':'song','duration_s':20}]
            elif q.startswith('INSERT INTO edls'):
                _,version,edl,_=args;self.edls[version]=copy.deepcopy(edl.adapted);self.one={'version':version}
            elif q.startswith('INSERT INTO chat_messages'):self.messages.append(copy.deepcopy(args[-1].adapted))
            elif q.startswith('UPDATE video_jobs SET state='):pass
            else:raise AssertionError(q)
        def fetchone(self):return self.one
        def fetchall(self):return self.many
    db=Store();db.edls=copy.deepcopy(db.edls);db.messages=[]
    @contextlib.contextmanager
    def connect():yield db
    monkeypatch.setattr(video,'vdb',connect)
    monkeypatch.setattr(video,'_project_for_user',lambda *_:dict(chat_session_id=12) if db.owned else None)
    monkeypatch.setattr(video,'_subscribe_gate_applies',lambda *_:not db.subscribed)
    monkeypatch.setattr(video,'_latest_edl',lambda *_:dict(version=max(db.edls),json=db.edls[max(db.edls)]))
    monkeypatch.setattr(video,'_edl_at',lambda _,__,v:dict(version=v,json=db.edls[v]))
    monkeypatch.setattr(video,'_active_original',lambda *_:dict(duration_s=20))
    original=video.read_receipt
    monkeypatch.setattr(video,'read_receipt_original',original,raising=False)
    # Receipts live under editor_command; inspect through the real SQL reader.
    def receipt_reader(_,session,operation_id,fingerprint):
        class Cursor:
            def execute(self,*_):pass
            def fetchone(self):
                for m in reversed(db.messages):
                    if m.get('operation_id')==operation_id:return {'meta':m}
                return None
        return original(Cursor(),session,operation_id,fingerprint)
    monkeypatch.setattr(video,'read_receipt',receipt_reader)
    return db


def batch(**kw):
    return dict(base_version=1,operation_id='edit-batch-0123456789',operations=[
        dict(action='set',layer='keep',value=[[1,6]]),dict(action='set',layer='frame',value={'ratio':'9:16'})],**kw)


def test_batch_is_one_revision_and_no_media_job(api):
    result,status=video.apply_edit_batch_core(1,2,batch(),origin='mcp')
    assert status==200 and result['version']==2 and result['preview_on_demand']
    assert result['review_status']=='pending'
    assert list(api.edls)==[1,2] and len(api.messages)==1
    assert api.edls[2]['keep']==[[1,6]] and api.edls[2]['frame']['ratio']=='9:16'


def test_bad_last_operation_leaves_no_saved_edits(api):
    data=batch();data['operations'].append(dict(action='set',layer='music',value=['bad']))
    result,status=video.apply_edit_batch_core(1,2,data)
    assert status==400 and len(api.edls)==1 and not api.messages


@pytest.mark.parametrize('field,value,status',[('owned',False,404),('subscribed',False,402),('busy',True,409)])
def test_batch_requires_ownership_subscription_and_free_editor(api,field,value,status):
    setattr(api,field,value)
    assert video.apply_edit_batch_core(1,2,batch())[1]==status
    assert len(api.edls)==1


def test_stale_batch_cannot_overwrite_newer_edit(api):
    api.edls[2]=copy.deepcopy(api.edls[1])
    assert video.apply_edit_batch_core(1,2,batch())[1]==409
    assert len(api.edls)==2


def test_retry_replays_committed_version_and_changed_retry_is_rejected(api):
    first,status=video.apply_edit_batch_core(1,2,batch())
    assert status==200
    again,status=video.apply_edit_batch_core(1,2,batch())
    assert status==200 and again['replayed'] and again['edl']==first['edl']
    changed=batch();changed['operations'][0]['value']=[[2,8]]
    assert video.apply_edit_batch_core(1,2,changed)[1]==409
    assert len(api.edls)==2


def test_batch_export_checks_all_members_before_queueing(monkeypatch):
    app=Flask(__name__)
    class Cur:
        def execute(self,*_):raise AssertionError('Must validate membership first')
    @contextlib.contextmanager
    def connect():
        yield type('Conn',(),{'cursor':lambda _:Cur()})()
    monkeypatch.setattr(video,'vdb',connect)
    monkeypatch.setattr(video,'_project_for_user',lambda _,pid,__: {'id':pid,'parent_project_id':7 if pid==10 else None})
    with app.test_request_context(json={'clips':[{'project_id':10,'edl_version':2},{'project_id':11,'edl_version':2}]}):
        response,status=video.export_shorts.__wrapped__(1,7)
    assert status==404


def test_batch_exports_use_common_gate_and_only_two_shared_groups(monkeypatch):
    app=Flask(__name__);groups=[]
    class Cur:
        def execute(self,sql,args):assert 'pg_advisory_xact_lock' in sql
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:Cur()})()
    monkeypatch.setattr(video,'vdb',connect)
    monkeypatch.setattr(video,'_project_for_user',lambda _,pid,__: {'id':pid,'parent_project_id':7})
    monkeypatch.setattr(video,'_latest_edl',lambda *_:{'version':3})
    def gate(cur,uid,pid,version,render_group):
        groups.append(render_group)
        return video.jsonify(job_id=pid+100)
    monkeypatch.setattr(video,'_request_final',gate)
    with app.test_request_context(json={'clips':[{'project_id':pid,'edl_version':3} for pid in range(10,40)]}):
        response=video.export_shorts.__wrapped__(1,7)
    assert len(response.get_json()['clips'])==30 and len(groups)==30
    assert set(groups)=={'7-0','7-1'}


def test_batch_export_rejects_stale_version_without_rendering(monkeypatch):
    app=Flask(__name__)
    class Cur:
        def execute(self,*_):pass
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:Cur()})()
    monkeypatch.setattr(video,'vdb',connect)
    monkeypatch.setattr(video,'_project_for_user',lambda _,pid,__: {'id':pid,'parent_project_id':7})
    monkeypatch.setattr(video,'_latest_edl',lambda *_:{'version':4})
    monkeypatch.setattr(video,'_request_final',lambda *a,**k:pytest.fail('Stale edits must not render'))
    with app.test_request_context(json={'clips':[{'project_id':10,'edl_version':3}]}):
        response=video.export_shorts.__wrapped__(1,7)
    assert response.get_json()['clips'][0]['code']=='stale_version'


def test_short_edit_batches_validate_every_owner_before_saving(monkeypatch):
    from routes import mcp
    saved=[]
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:object()})()
    monkeypatch.setattr(mcp,'vdb',connect)
    monkeypatch.setattr(mcp,'_project_for_user',lambda _,pid,uid:
        {'parent_project_id':7} if pid in (7,10) else None)
    monkeypatch.setattr(mcp,'apply_edit_batch_core',lambda *a,**k:saved.append(a))
    response=mcp._t_apply_short_edit_batches({'user_id':1},{'project_id':7,
        'batches':[dict(project_id=10,**batch()),dict(project_id=11,**batch())]})
    assert not saved and 'no edits were saved' in str(response)


def test_short_edit_batches_return_individual_receipts_without_edl_bloat(monkeypatch):
    import json
    from routes import mcp
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:object()})()
    monkeypatch.setattr(mcp,'vdb',connect)
    monkeypatch.setattr(mcp,'_project_for_user',lambda *_:{'parent_project_id':7})
    monkeypatch.setattr(mcp,'apply_edit_batch_core',lambda uid,pid,data,origin:
        ({'version':2,'edl':{'large':'document'}},200) if pid==10 else ({'error':'stale'},409))
    response=json.loads(mcp._t_apply_short_edit_batches({'user_id':1},{'project_id':7,
        'batches':[dict(project_id=10,**batch()),dict(project_id=11,**batch())]}))
    assert [r['status'] for r in response['batches']]==[200,409]
    assert 'edl' not in response['batches'][0]


@pytest.mark.parametrize('owned',[False,True])
def test_edit_head_checks_owner_and_omits_unchanged_document(monkeypatch,owned):
    app=Flask(__name__)
    class Cur:
        def execute(self,sql,args):
            assert 'p.user_id=%s' in sql and args==(7,1)
            assert 'video_jobs' not in sql
        def fetchone(self):return {'version':4,'json':{'keep':[[0,5]]},'created_by':'agent'} if owned else None
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:Cur()})()
    monkeypatch.setattr(video,'vdb',connect)
    with app.test_request_context('/?since=4'):
        response=video.edit_head.__wrapped__(1,7)
    if owned:assert response.get_json()=={'version':4,'edl':None}
    else:assert response[1]==404


def test_playback_manifest_uses_bound_proxy_and_project_owned_media(monkeypatch):
    app=Flask(__name__)
    original={'id':1,'kind':'original','storage_key':'orig','sha256':'current','duration_s':20}
    assets=[original,dict(original,id=2,kind='proxy',storage_key='good-proxy'),
        dict(original,id=3,kind='proxy',storage_key='stale-proxy',sha256='old')]
    class Cur:
        def execute(self,sql,args):assert 'project_id=%s' in sql and args==(7,)
        def fetchall(self):return assets
    @contextlib.contextmanager
    def connect():yield type('Conn',(),{'cursor':lambda _:Cur()})()
    monkeypatch.setattr(video,'vdb',connect)
    monkeypatch.setattr(video,'_project_for_user',lambda *_:{'id':7})
    monkeypatch.setattr(video,'_latest_edl',lambda *_:{'version':1,'json':{
        'keep':[[0,10]],'music':[{'storage_key':'unowned','start':0,'end':10}]}})
    monkeypatch.setattr(video,'_active_original',lambda *_:original)
    monkeypatch.setattr(video.storage,'presign_get',lambda key:'https://media.test/'+key)
    with app.test_request_context():response=video.playback_endpoint.__wrapped__(1,7).get_json()
    assert response['sources']['source']['asset_id']==2
    assert 'unowned' not in response['sources']
