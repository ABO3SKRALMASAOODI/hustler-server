"""A built timeline is not an exported film; preserve every ordinary export gate."""
import os,sys
from pathlib import Path
os.environ.setdefault('SKIP_DB_INIT','1')
os.environ.setdefault('DATABASE_URL','postgresql://stub/stub')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from routes import mcp

def built():
    return {'id':5,'project_id':3,'type':'mcp_tool','state':'done',
            'result':{'text':'Built','film':{'edl_version':4}}}

def test_running_final_stays_pending_under_original_build_job(monkeypatch):
    calls=[]
    def export(tok,args,**policy):
        assert policy == {'documentary': True}
        calls.append((tok,args));return {'structuredContent':{'export':{'job_id':77}}}
    monkeypatch.setattr(mcp,'_t_export_final',export)
    monkeypatch.setattr(mcp,'_wait',lambda *a,**k:{'id':77,'state':'running','progress':42})
    row=mcp._settle_film_export(built(),60,0)
    assert row['id']==5 and row['state']=='running' and row['progress']==42
    assert calls==[({'user_id':60},{'project_id':3,'edl_version':4})]
    assert 'film_delivery' not in row['result']

def test_export_gate_refusal_is_not_a_delivery(monkeypatch):
    monkeypatch.setattr(mcp,'_t_export_final',lambda *a,**kw:{'isError':True,'content':[{'type':'text','text':'verification_open'}]})
    row=mcp._settle_film_export(built(),60,0)
    assert row['result']['is_error'] and 'verification_open' in row['result']['text']
    assert 'film_delivery' not in row['result']

def test_final_download_still_checks_final_gate(monkeypatch):
    monkeypatch.setattr(mcp,'_t_export_final',lambda *a,**kw:{'structuredContent':{'export':{'job_id':77}}})
    monkeypatch.setattr(mcp,'_wait',lambda *a,**k:{'id':77,'state':'done'})
    monkeypatch.setattr(mcp,'_t_download_url',lambda *a,**kw:{'isError':True,'content':[{'type':'text','text':'final held'}]})
    row=mcp._settle_film_export(built(),60,0)
    assert row['result']['is_error'] and row['result']['text']=='final held'

def test_only_real_final_receipt_counts_as_delivered(monkeypatch):
    monkeypatch.setattr(mcp,'_t_export_final',lambda *a,**kw:{'structuredContent':{'export':{'job_id':77}}})
    monkeypatch.setattr(mcp,'_wait',lambda *a,**k:{'id':77,'state':'done'})
    receipt={'download':{'kind':'final','asset_id':222,'edl_version':4}}
    monkeypatch.setattr(mcp,'_t_download_url',lambda *a:{'content':[{'type':'text','text':'Ready'}],'structuredContent':receipt})
    row=mcp._settle_film_export(built(),60,0)
    assert row['state']=='done' and not row['result']['is_error']
    assert row['result']['film_delivery']==receipt

def test_chapter_continuation_keeps_waiting_on_same_request(monkeypatch):
    parked=dict(built(),result={'parked_render':{'job_id':6,'type':'film_chapter'}})
    answered=dict(built(),id=6,result={'text':'Final chapter built'})
    rows=[parked,answered]
    monkeypatch.setattr(mcp,'_wait',lambda *a,**k:rows.pop(0))
    assert mcp._settle(5,60,1)==dict(answered,id=5)


def test_film_export_runs_after_client_disconnects(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import json

    operations = []

    class Cursor:
        def execute(self, sql, params):
            operations.append((sql.split()[0], params))

        def fetchall(self):
            return [{"key": "mcp_film_pending:5", "value": json.dumps([5, 60])}]

    @contextmanager
    def database():
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(mcp, "vdb", database)
    monkeypatch.setattr(mcp, "_job_row", lambda *args: built())
    exports = []
    def request_final(cur, user_id, project_id, version, **policy):
        exports.append((user_id, project_id, version, policy))
        return SimpleNamespace(get_json=lambda: {"job_id": 77})
    monkeypatch.setattr(mcp, "_request_final", request_final)
    assert mcp._advance_film_exports()
    assert exports == [(60, 3, 4, {"reuse_existing": True, "audio_model_review": False})]
    assert operations[-1] == ("DELETE", ("mcp_film_pending:5",))
