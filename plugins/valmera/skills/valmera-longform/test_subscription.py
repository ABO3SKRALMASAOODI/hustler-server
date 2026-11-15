"""Local workflow requests cannot retry into a paid provider."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_footage_proxy_failure_is_one_attempt_with_buffered_deadline(monkeypatch):
    import cinema as C
    C.worker()
    from footage import ask
    from score import call
    seen = []
    def fail(*args, **kwargs):
        seen.append(kwargs)
        raise TimeoutError('buffered response did not arrive')
    monkeypatch.setenv('CODEX_PROXY_LOCAL', '1')
    monkeypatch.setattr(ask.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(call, 'stream', fail)
    with pytest.raises(TimeoutError):
        ask.ask('Review the picture', [], 'test', budget_s=120)
    assert len(seen) == 1
    assert seen[0]['deadline'] == 1305.


def test_cached_box_media_does_not_open_network(tmp_path, monkeypatch):
    import footage_box as F
    (tmp_path / 'footage.json').write_text('{"lines": [], "voices": [], "counts": {"frames_checked": 20, "sources_opened": 2}}')
    monkeypatch.setenv('CODEX_PROXY_LOCAL', '1')
    monkeypatch.setattr(F, 'ssh', lambda *a, **k: pytest.fail('cached media opened SSH'))
    assert F.fetch({}, tmp_path) == dict(lines=[], voices=[], counts=dict(frames_checked=20,sources_opened=2))


def test_private_container_installs_guard_before_worker(tmp_path, monkeypatch):
    import footage_box as F
    import subprocess
    (tmp_path / 'input.json').write_text('{}')
    commands = []
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k:
        b'[{"Config":{"Env":["DATABASE_URL=never-inherit","YTDLP_PROXY=fetch-route"],"Image":"worker"},"Image":"sha256:installed"}]')
    monkeypatch.setattr(subprocess, 'call', lambda cmd, **kw: commands.append((cmd,kw)) or 0)
    monkeypatch.setattr(sys, 'argv', ['runner.py','batch',str(tmp_path),str(tmp_path/'cache'),'own-job','44535'])
    with pytest.raises(SystemExit) as done:
        exec(compile(F.RUNNER, 'private_runner', 'exec'), {})
    assert done.value.code == 0
    cmd = commands[0][0]
    assert cmd[cmd.index('--network') + 1] == 'host'
    assert 'CODEX_PROXY_LOCAL=1' in cmd
    assert 'PYTHONPATH='+str(tmp_path)+'/client' in cmd
    assert 'OPENAI_BASE_URL=http://127.0.0.1:44535/v1' in cmd
    assert not any('DATABASE_URL' in arg for arg in cmd)


def test_review_cache_keeps_semantics_and_ignores_only_concurrency():
    import hashlib
    from review_cache import checker_bytes
    # Published review identities before the subscription scheduling changes.
    assert hashlib.sha256(checker_bytes()).hexdigest() == '7637971d1b898fcc9e6ac97ab6f2f19d7f7c365864aa04e3d59b3e34c494b5e6'
    text=Path(__file__).with_name('picture_check.py').read_text()
    assert checker_bytes(text.replace('Reject wrong people','Reject incorrect people')) != checker_bytes(text)
    assert checker_bytes(text.replace('(lo+hi)/2','lo+(hi-lo)*.4')) != checker_bytes(text)


def test_recorded_edit_resume_requires_same_words_and_pins(tmp_path,monkeypatch):
    import archive_visual as A
    import cinema as C
    ctx=SimpleNamespace(supply=str(tmp_path))
    nd=tmp_path/'narration';nd.mkdir()
    C.save(nd/'read-stage.json',{'script':{'words':'same'}})
    C.save(nd/'check.json',{'gate':{'pass':True}})
    C.save(nd/'words.json',{'duration':20})
    monkeypatch.setattr(C.wf4.story,'narration_script',lambda s:{'words':s['words']})
    calls=[]
    monkeypatch.setattr(C,'schedule',lambda *a:calls.append(a) or ([{'say':'same'}],None,None))
    ft={'editorial':{'pins':[{'previously_checked':True}]}}
    assert not A.recorded_edit_ready(ctx,{'script':{'words':'changed'}},ft)
    assert not A.recorded_edit_ready(ctx,{'script':{'words':'same'}},{})
    assert not calls
    assert A.recorded_edit_ready(ctx,{'script':{'words':'same'}},ft)
    assert not calls  # Actual-clock coverage and picture gates run in supply, never in provisional preflight.


def test_parallel_scanners_share_one_subscription_request_slot(monkeypatch):
    import cinema as C
    C.worker()
    from footage import ask
    from score import call
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    active=0;peak=0
    lock=threading.Lock()
    def respond(*args,**kwargs):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.02)
        with lock:active-=1
        return SimpleNamespace(text='checked',usd=0)
    monkeypatch.setenv('CODEX_PROXY_LOCAL','1')
    monkeypatch.setattr(call,'stream',respond)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda i:ask.ask('Check',[],'test'),range(4)))
    assert results==[('checked',0.)]*4 and peak==1


def test_source_table_keeps_every_provenance_field_without_repeating_it():
    from model_payload import source_table
    import copy,json
    source={'url':'https://archive.test/document','caption':'Original evidence. '*200,'licence':'public domain'}
    payload={'candidates':[{'i':i,'source':copy.deepcopy(source),'rejected':['wrong mission']} for i in range(20)]}
    before=copy.deepcopy(payload);packed=source_table(payload)
    restored=copy.deepcopy({k:v for k,v in packed.items() if k not in ('source_records','reading_note')})
    for row in restored['candidates']:row['source']=packed['source_records'][row['source']]
    assert restored==payload==before
    assert len(json.dumps(packed))<len(json.dumps(payload))*.15


def test_box_search_uses_only_model_referenced_subjects():
    import ast
    import footage_box as F
    tree=ast.parse(F.REMOTE)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='scoped_plan')
    result={'subjects':[{'name':'needed mechanism'},{'name':'unrelated person'}],
            'lines':[{'i':21,'subjects':['needed mechanism']}]}
    namespace={'original_plan':lambda *a,**k:(result,0)}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'scoped_plan','exec'),namespace)
    scoped,usd=namespace['scoped_plan']([],[])
    assert scoped['subjects']==[{'name':'needed mechanism'}]
    assert scoped['lines']==result['lines'] and usd==0


def test_box_listener_hears_whole_source_once_and_reuses_transcript(tmp_path,monkeypatch):
    import cinema as C
    C.worker()
    import box_hear as B
    from footage import hear
    audio=tmp_path/'source.mp4';audio.write_bytes(b'original archive')
    monkeypatch.setenv('FOOTAGE_TRANSCRIPT_CACHE',str(tmp_path/'transcripts'))
    seen=[]
    def listen(path,**kw):
        seen.append(kw)
        return iter([SimpleNamespace(start=1.25,end=4.8,text='First thought.'),
                     SimpleNamespace(start=17.6,end=19.2,text='Second thought.')]),None
    model=SimpleNamespace(transcribe=listen)
    monkeypatch.setitem(sys.modules,'transcribe',SimpleNamespace(get_model=lambda:model))
    monkeypatch.setattr(B.subprocess,'run',lambda *a,**k:None)
    expected=[{'t0':1.25,'t1':4.8,'text':'First thought.'},{'t0':17.6,'t1':19.2,'text':'Second thought.'}]
    assert B.transcribe(str(audio),24.)==(expected,0.)
    assert B.transcribe(str(audio),24.)==(expected,0.)
    assert len(seen)==1 and seen[0]['beam_size']==5 and seen[0]['word_timestamps']
    assert not list(tmp_path.glob('local-hear-*'))


def test_failed_picture_checks_cannot_count_as_completed_research(tmp_path):
    import cinema as C
    import footage_box as F
    valid=dict(lines=[dict(i=21,say='A repair.',clips=[])],counts=dict(frames_checked=20,sources_opened=2))
    failed=dict(valid,research_errors=['HTTP 413'])
    C.save(tmp_path/'failed/footage.json',failed)
    C.save(tmp_path/'unseen/footage.json',dict(valid,counts=dict(frames_checked=0,sources_opened=2)))
    assert not F.completed_research(failed)
    assert C.video_search_counts(tmp_path,{21:'A repair.'})=={}
    C.save(tmp_path/'checked/footage.json',valid)
    assert C.video_search_counts(tmp_path,{21:'A repair.'})=={21:1}
    assert not C.photo_research_ready(tmp_path,[dict(seg=21,seconds=5)],100,{21:'A repair.'})
