"""Offline regressions: actual request builders, no provider/DB calls."""
import copy
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
import openai_cache as cache
from footage import hear, transcript_cache


def test_schema_order_and_static_prefix_bytes_are_identical():
    from score.call_openai import body
    a = body('gpt-6-sol', 'rubric '*600, 'first film', 200, 'low', 'picture')
    b = body('gpt-6-sol', 'rubric '*600, 'second film', 200, 'low', 'picture')
    assert json.dumps(a['input'][0]).encode() == json.dumps(b['input'][0]).encode()
    assert a['input'][0]['content'][0]['prompt_cache_breakpoint']['ttl'] == '12h'
    tools=[{'type':'function','name':'z','parameters':{'required':['b','a'],'properties':{'b':{},'a':{}}}},
           {'type':'function','name':'a','parameters':{}}]
    original=copy.deepcopy(tools)
    one=cache.responses({'model':'gpt-6-sol','tools':tools},'pictures')
    two=cache.responses({'model':'gpt-6-sol','tools':list(reversed(tools))},'pictures')
    assert json.dumps(one['tools']) == json.dumps(two['tools'])
    assert one['tools'][1]['parameters']['required']==['b','a']
    assert tools == original


def test_context_boundary_preserves_text_and_image_detail():
    from score.call_openai import body
    content=[{'type':'text','text':'shared project lines','cache_control':{'type':'ephemeral'}},
             {'type':'text','text':'variable frame label'},
             {'type':'image','detail':'low','source':{'type':'base64','media_type':'image/jpeg','data':'test'}}]
    before=copy.deepcopy(content)
    req=body('gpt-6-sol','fixed rubric',content,1000,'low','footage_look')
    parts=req['input'][1]['content']
    assert parts[0]['text']==content[0]['text']
    assert parts[0]['prompt_cache_breakpoint']['ttl']=='30m'
    assert parts[-1]['detail']=='low'
    assert content==before
    evidence=cache.describe(req)
    assert 'shared project lines' not in json.dumps(evidence)
    assert 'data:image' not in json.dumps(evidence)
    assert evidence['boundaries'][0]['prefix_sha256']==cache.describe(req)['boundaries'][0]['prefix_sha256']


def test_role_does_not_hash_variable_system_content():
    assert cache.role_for('film A') == cache.role_for('film B')
    assert cache.key('x'*60+'a') != cache.key('x'*60+'b')


@pytest.fixture
def speech(tmp_path,monkeypatch):
    monkeypatch.setenv('FOOTAGE_TRANSCRIPT_CACHE',str(tmp_path/'cache'))
    p=tmp_path/'clip.mp4';p.write_bytes(b'same content')
    monkeypatch.setattr(hear,'splits',lambda path,dur:[0.,12.,dur])
    calls=[]
    def one(path,t0,t1,tmp,i):
        calls.append((t0,t1))
        return dict(t0=t0,t1=t1,text=f'words {i}',usd=.01)
    monkeypatch.setattr(hear,'_transcribe_piece',one)
    import llm
    monkeypatch.setattr(llm,'record',lambda *a,**kw:None)
    return p,calls


def test_transcript_cache_reuses_content_across_paths_and_concurrent_runs(speech,tmp_path):
    p,calls=speech;q=tmp_path/'other.mp4';q.write_bytes(p.read_bytes())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda x:hear.transcribe(str(x),20.,workers=2),[p,q]))
    assert len(calls)==2
    assert results[0][0]==results[1][0]
    assert sum(usd for _,usd in results)==pytest.approx(.02)
    assert hear.transcribe(str(p),20.)[1]==0
    assert not list(tmp_path.glob('hear-*'))


def test_errors_are_not_cached_but_successful_pieces_are(speech,monkeypatch):
    p,calls=speech
    original=hear._transcribe_piece
    def fails(path,t0,t1,tmp,i):
        if i==1:return dict(t0=t0,t1=t1,text='',error='failed')
        return original(path,t0,t1,tmp,i)
    monkeypatch.setattr(hear,'_transcribe_piece',fails)
    hear.transcribe(str(p),20.)
    monkeypatch.setattr(hear,'_transcribe_piece',original)
    rows,usd=hear.transcribe(str(p),20.)
    assert len(calls)==2 and rows[1]['text']=='words 1' and usd==.01


def test_cache_model_and_content_invalidation(speech,monkeypatch):
    p,calls=speech
    hear.transcribe(str(p),20.)
    p.write_bytes(b'changed content');hear.transcribe(str(p),20.)
    monkeypatch.setattr(hear,'MODEL','other-model');hear.transcribe(str(p),20.)
    assert len(calls)==6


def test_batched_look_warms_first_and_preserves_candidate_mapping(tmp_path,monkeypatch):
    from footage import look
    calls=[]
    monkeypatch.setattr(look,'PER_SHEET',2)
    monkeypatch.setattr(look,'sheet',lambda cells,path,**kw:path)
    monkeypatch.setattr(look.ask,'image_block',lambda p:{'type':'image','source':{'data':p}})
    def ask(system,content,purpose,**kw):
        calls.append(copy.deepcopy(content))
        return json.dumps({'cells':[{'n':1,'ok':True,'clean':True,'grade':4},
                                    {'n':2,'ok':True,'clean':True,'grade':4}]}),.01
    monkeypatch.setattr(look.ask,'ask',ask)
    cands=[dict(src=0,t=float(i),jpg=f'{i}.jpg') for i in range(5)]
    out,usd=look.check(dict(name='subject',must_show='actual subject'),[dict(i=1,say='words')],
                       cands,[dict(title='source')],str(tmp_path))
    assert len(calls)==3 and [c['t'] for c in out]==list(range(5))
    assert 'at 0.0 s' in calls[0][1]['text']
    assert all(c[0]==calls[0][0] for c in calls)
    assert all(c[-1]['type']=='image' for c in calls)
    assert usd==pytest.approx(.03)


def test_critic_static_rubric_does_not_change_with_project(monkeypatch):
    import preview_critic
    bodies=[]
    monkeypatch.setattr(preview_critic.llm,'vision_available',lambda:True)
    def ask(prompt,paths,**kw):
        bodies.append((prompt,kw['system']))
        return '{"verdict":"pass","findings":[]}'
    monkeypatch.setattr(preview_critic.llm,'ask_vision',ask)
    preview_critic.review(['a.jpg'],['a'],'project one')
    preview_critic.review(['b.jpg'],['b'],'project two')
    assert bodies[0][1].encode()==bodies[1][1].encode()
    assert bodies[0][0]!=bodies[1][0]


def test_writer_web_search_keeps_implicit_history(monkeypatch,tmp_path):
    import httpx, importlib.util
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('cache_writer',root/'plugins/valmera/skills/valmera-stories/writer.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    requests=[];client=httpx.Client
    def handler(request):
        requests.append(json.loads(request.content))
        event={'type':'response.completed','response':{'id':'test','status':'completed','usage':{'input_tokens':1200,'output_tokens':1},'output':[]}}
        return httpx.Response(200,text='data: '+json.dumps(event)+'\n\n')
    monkeypatch.setattr(httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG',str(tmp_path/'spend.jsonl'))
    for searches in (0,1):m.ask_openai('fixed rubric','project',searches=searches)
    assert requests[0]['prompt_cache_options']['mode']=='explicit'
    assert requests[1]['prompt_cache_options']['mode']=='implicit'
    assert requests[0]['input']==requests[1]['input']


def test_failed_cache_storage_does_not_lose_the_transcript(speech,tmp_path,monkeypatch):
    p,calls=speech
    blocked=tmp_path/'not-a-directory';blocked.write_text('file')
    monkeypatch.setenv('FOOTAGE_TRANSCRIPT_CACHE',str(blocked))
    rows,usd=hear.transcribe(str(p),20.)
    assert len(rows)==2 and usd==pytest.approx(.02)


def test_cache_prunes_old_and_over_budget_entries(tmp_path):
    import os,time
    old=tmp_path/'old.json';old.write_text('x'*20);os.utime(old,(1,1))
    new=tmp_path/'new.json';new.write_text('y'*20)
    partial=tmp_path/'.transcript-crashed';partial.write_text('partial');os.utime(partial,(1,1))
    transcript_cache.prune(tmp_path,max_bytes=20,max_age=86400)
    assert not old.exists() and not partial.exists() and new.exists()
