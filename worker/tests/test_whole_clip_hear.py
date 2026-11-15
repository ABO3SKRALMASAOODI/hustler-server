import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
import httpx
import openai_budget
from footage import hear


@pytest.fixture
def clip(tmp_path, monkeypatch):
    path=tmp_path/'input.wav';path.write_bytes(b'same audio')
    monkeypatch.setenv('FOOTAGE_TRANSCRIPT_CACHE',str(tmp_path/'cache'))
    def encode(argv,**kw):
        import wave
        with wave.open(str(argv[-1]),'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'))
            out.writeframes(b'\x00\x20\x00\xe0'*160)
        return NS(returncode=0)
    monkeypatch.setattr(hear.subprocess,'run',encode)
    return str(path)


def test_local_pass_retains_actual_segment_times_and_memo(clip,monkeypatch):
    import transcribe
    calls=[]
    def recognize(path,**kw):
        import numpy as np
        assert isinstance(path,np.ndarray) and path.dtype==np.float32
        assert np.allclose(path[:2],[.25,-.25])
        calls.append(kw)
        return iter([NS(start=.71,end=2.83,text=' Hello, world!'), NS(start=13.19,end=16.07,text=' Second phrase.')]),NS()
    monkeypatch.setattr(transcribe,'get_model',lambda:NS(transcribe=recognize))
    monkeypatch.setenv('FOOTAGE_HEAR_PROVIDER','local')
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:pytest.fail('OpenAI sent'))
    rows,usd=hear.transcribe(clip,40)
    assert rows==[dict(t0=.71,t1=2.83,text='Hello, world!'),dict(t0=13.19,t1=16.07,text='Second phrase.')]
    assert hear.transcribe(clip,40)==(rows,0)
    assert len(calls)==1 and calls[0]['word_timestamps'] is True and usd==0


def test_openai_opt_in_sends_one_clip_with_timestamps(clip,monkeypatch):
    monkeypatch.setenv('FOOTAGE_HEAR_PROVIDER','openai')
    monkeypatch.setattr(openai_budget,'reserve',lambda *a,**k:openai_budget.Lease())
    import llm
    monkeypatch.setattr(llm,'record',lambda *a,**k:None)
    calls=[]
    def post(*a,**kw):
        calls.append(kw['data'])
        return NS(status_code=200,json=lambda:dict(duration=60,segments=[dict(start=8.25,end=10.5,text='Exact span.')]))
    monkeypatch.setattr(httpx,'post',post)
    rows,usd=hear.transcribe(clip,60,workers=8)
    assert len(calls)==1 and calls[0]['model']=='whisper-1'
    assert calls[0]['response_format']=='verbose_json'
    assert rows==[dict(t0=8.25,t1=10.5,text='Exact span.')]
    assert usd==.006 and hear.transcribe(clip,60)==(rows,0)
    assert len(calls)==1


def test_budget_stop_never_falls_back_or_sends(clip,monkeypatch):
    monkeypatch.setenv('FOOTAGE_HEAR_PROVIDER','openai')
    monkeypatch.setattr(openai_budget,'reserve',lambda *a,**k: (_ for _ in ()).throw(openai_budget.OverBudget('cap')))
    monkeypatch.setattr(httpx,'post',lambda *a,**k:pytest.fail('transport sent'))
    monkeypatch.setattr(hear,'_local_clip',lambda *a:pytest.fail('budget stop swallowed'))
    with pytest.raises(openai_budget.OverBudget):hear.transcribe(clip,60)


def test_bad_timestamps_cannot_become_a_cached_success():
    with pytest.raises(ValueError):
        hear._timed_rows([dict(start=5,end=7,text='later'),dict(start=1,end=3,text='earlier')],10)


def test_silent_whole_clip_is_cached(clip,monkeypatch):
    monkeypatch.setenv('FOOTAGE_HEAR_PROVIDER','local')
    calls=[]
    monkeypatch.setattr(hear,'_local_clip',lambda *a: calls.append(1) or [])
    assert hear.transcribe(clip,4)==([],0)
    assert hear.transcribe(clip,4)==([],0)
    assert calls==[1]
