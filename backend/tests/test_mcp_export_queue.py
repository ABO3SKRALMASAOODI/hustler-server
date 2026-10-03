"""MCP final requests share Studio's ownership, safety, and durable queue."""
import os
import sys
from pathlib import Path
os.environ.setdefault('SKIP_DB_INIT','1')
os.environ.setdefault('DATABASE_URL','postgresql://stub/stub')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from flask import Flask
from routes import video

class Cur:
    def __init__(self, running=None, asset=None, repair=False):
        self.running=running; self.asset=asset; self.repair=repair; self.row=None; self.locked=False
    def execute(self,sql,params=()):
        self.row=None
        if 'pg_advisory_xact_lock' in sql: self.locked=True
        elif 'SELECT version, json FROM edls' in sql: self.row={'version':4,'json':{}}
        elif 'SELECT cm.meta' in sql and self.repair: self.row={'meta':{'edl_version':4,'quality_status':'repair_required'}}
        elif "kind='render'" in sql: self.row=self.asset
        elif "type = 'final'" in sql and "'queued','running'" in sql: self.row=self.running
    def fetchone(self):return self.row

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(video,'_project_for_user',lambda cur,pid,uid: {'id':pid} if (pid,uid)==(3,60) else None)
    monkeypatch.setattr(video,'_obsolete_failed_render_version',lambda *a:None)
    monkeypatch.setattr(video,'_active_original',lambda *a:{'duration_s':20,'meta':{}})
    monkeypatch.setattr(video,'_export_edl_error',lambda *a:None)
    monkeypatch.setattr(video,'_final_gate',lambda *a:lambda *a:True)
    monkeypatch.setattr(video,'record_client_event',lambda *a,**k:None)
    monkeypatch.setattr(video,'_enqueue',lambda *a,**k:777)

def run(cur,uid=60):
    with Flask(__name__).app_context():
        result=video._request_final(cur,uid,3,4,reuse_existing=True)
        response,status=result if isinstance(result,tuple) else (result,200)
        return response.get_json(),status

def test_other_account_cannot_export():
    cur=Cur()
    assert run(cur,61)[1]==404
    assert not cur.locked

def test_retry_joins_same_version_instead_of_creating_duplicate():
    cur=Cur(running={'id':888,'payload':{'edl_version':4}})
    assert run(cur)==({'job_id':888,'reused':True},200)
    assert cur.locked

def test_different_running_version_remains_a_clear_conflict():
    data,status=run(Cur(running={'id':888,'payload':{'edl_version':3}}))
    assert status==409 and data['code']=='already_running'

def test_existing_current_final_is_reused_without_encoding():
    assert run(Cur(asset={'id':11,'meta':{}}))==({'asset_id':11,'reused':True},200)

def test_new_export_enters_the_shared_final_queue():
    assert run(Cur())==({'job_id':777},200)

def test_known_open_repair_is_not_bypassed():
    data,status=run(Cur(repair=True))
    assert status==409 and data['code']=='repair_required'
