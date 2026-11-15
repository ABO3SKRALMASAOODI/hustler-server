"""Recovery preserves exact source identity and the original measurement intervals."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import wf1_coverage as coverage
import wf1_primary_sources as primary


def decision(updates):
    return SimpleNamespace(ask=lambda *a,**k:(json.dumps({'updates':updates}),{}),json_block=json.loads)


def story():
    return dict(words=[dict(w=str(i),start=i,end=i+.8) for i in range(8)],
                windows=[dict(name='A',first=1,last=2,t0=1,t1=2.8,say='1 2'),
                         dict(name='B',first=6,last=7,t0=6,t1=7.8,say='6 7')])


def test_retained_originals_need_bytes_and_attribution_and_keep_exact_subjects(tmp_path):
    sources=tmp_path/'sources';sources.mkdir()
    original=sources/'original.mp4';original.write_bytes(b'original')
    good=dict(url='https://publisher.example/film',path=str(original),title='Original demo',
              channel='Researcher',subjects=['Deep learning'],where='primary')
    rows=[good,good,dict(good,url='https://publisher.example/missing',path=str(sources/'missing')),
          dict(good,url='https://publisher.example/unattributed',channel='')]
    (sources/'manifest.json').write_text(json.dumps(rows))
    result=primary.retained(str(tmp_path))
    assert list(result)==['Deep learning'] and len(result['Deep learning'])==1
    candidate=result['Deep learning'][0]
    assert candidate['path']==str(original) and candidate['where']=='primary' and candidate['id']
    assert not candidate.get('verified_motion') and not candidate.get('ok')
    assert original.read_bytes()==b'original' and len(list(sources.iterdir()))==2


def test_composed_feedback_revises_a_window_brief_once_without_editing_media(tmp_path,monkeypatch):
    import wf1_short as wf
    window=dict(name='Research',first=0,last=3,t0=0,t1=4,say='the history of science',must_show='People')
    pack=dict(coverage=dict(words=[],windows=[window]),footage=[dict(i=0,clips=[dict(path='unchanged.mp4')])])
    path=tmp_path/'pack.json';path.write_text(json.dumps(dict(pack=pack,record={})))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:json.loads(path.read_text())['pack'])
    monkeypatch.setattr(wf,'_llm',lambda:None)
    monkeypatch.setattr(coverage,'repair_boundaries',lambda *a:False)
    calls=[]
    def revise(data,wanted,evidence,llm):
        assert wanted==[0] and evidence['stage']=='composed-frame rejection'
        calls.append(evidence);data['windows'][0]['must_show']='Actual scientific work'
        return {}
    monkeypatch.setattr(coverage,'repair_requirements',revise)
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    rejection=[dict(f=[3,33],reason='Unrelated activity',source='https://publisher.example/film')]
    wf._repair_window_boundaries(ctx,rejection);wf._repair_window_boundaries(ctx,rejection)
    saved=json.loads(path.read_text())['pack']
    assert len(calls)==1 and saved['coverage_context_rescout_pending'] is True
    assert saved['footage']==pack['footage']
    assert saved['coverage']['windows'][0]['say']==window['say']
    assert saved['composed_requirement_windows']==[0]


def test_primary_referent_direction_precedes_acquisition_and_is_cached(tmp_path,monkeypatch):
    import wf1_context
    calls=[]
    found=dict(references=[dict(referent='Documented project',sources=[dict(url='https://publisher.example/original')])],spend=[{}])
    monkeypatch.setattr(wf1_context,'research',lambda *a:calls.append('research') or found)
    def revise(data,wanted,evidence,llm):
        assert data['research_context']==found
        assert evidence['stage']=='before first footage search'
        calls.append('direction');data['windows'][0]['name']='Documented project'
        return {}
    monkeypatch.setattr(coverage,'repair_requirements',revise)
    first=story();wf1_context.direct(first,str(tmp_path),None)
    assert calls==['research','direction']
    second=story();wf1_context.direct(second,str(tmp_path),None)
    assert calls==['research','direction'] and first==second
    assert first['windows'][0]['first']==1 and first['windows'][0]['last']==2


def test_context_extension_keeps_all_named_words_and_source_identity(tmp_path):
    data=story()
    changed,_=coverage.expand_context(data,[],str(tmp_path),decision([dict(i=0,first=0,last=4,why='The explanation continues')]))
    assert changed
    assert data['windows'][0]==dict(name='A',first=0,last=4,t0=0,t1=4.8,say='0 1 2 3 4')
    assert data['windows'][1]==story()['windows'][1]


@pytest.mark.parametrize('first,last',[(2,2),(1,1),(0,6)])
def test_context_rejects_shrinking_ruler_or_overlapping_subjects(tmp_path,first,last):
    data=story()
    with pytest.raises(RuntimeError):
        coverage.expand_context(data,[],str(tmp_path),decision([dict(i=0,first=first,last=last,why='Candidate')]))
    assert data['windows']==story()['windows']


@pytest.mark.parametrize('shared,valid',[(False,True),(True,True),(True,False),(False,False)])
def test_publisher_page_uses_transport_and_preserves_shared_original(tmp_path,monkeypatch,shared,valid):
    import writer,net_fetch
    from footage import fetch,shots
    corpus=tmp_path/'corpus.mp4';corpus.write_bytes(b'x'*3000)
    item=dict(window=0,kind='page',url='https://publisher.example/video',page_url='https://publisher.example/video',
              title='Original demonstration',publisher='Research lab')
    monkeypatch.setattr(writer,'ask_openai',lambda *a,**k:(json.dumps({'sources':[item]}),{}))
    monkeypatch.setattr(net_fetch,'check_url',lambda _:None)
    def transport(url,out):
        assert url==item['url']
        if shared:return str(corpus),'verified-local-source'
        path=os.path.join(out,'whole.mp4')
        with open(path,'wb') as f:f.write(b'y'*3000)
        return path,'external-original'
    monkeypatch.setattr(fetch,'whole_av',transport)
    monkeypatch.setattr(shots,'probe',lambda _:dict(dur=20 if valid else 700,w=1280,h=720))
    work=tmp_path/'sources'
    result=primary.acquire([dict(i=0,name='Demo')],str(work),SimpleNamespace(openai_key=lambda:None,json_block=json.loads))
    assert corpus.read_bytes()==b'x'*3000
    assert not list(work.glob('publisher-*'))
    if valid:
        assert result['sources']['Demo'][0]['url']==item['url']
        manifest=json.loads((work/'manifest.json').read_text())
        assert os.path.isfile(manifest[0]['path']) and manifest[0]['sha256']
    else:
        assert not result['sources'] and result['errors']
        assert not list(work.glob('*.mp4'))


@pytest.mark.parametrize('size_mb,accepted',[(153,True),(257,False)])
def test_short_hd_publisher_video_has_a_bounded_download_budget(tmp_path,monkeypatch,size_mb,accepted):
    import writer,net_fetch
    from footage import shots
    item=dict(window=0,kind='file',url='https://publisher.example/original.mp4',
              page_url='https://publisher.example/article',title='Original film',publisher='Publisher')
    monkeypatch.setattr(writer,'ask_openai',lambda *a,**k:(json.dumps({'sources':[item]}),{}))
    monkeypatch.setattr(net_fetch,'check_url',lambda _:None)
    def download(url,path,**options):
        assert options['max_bytes']==256<<20
        with open(path,'wb') as f:f.write(b'original media')
    monkeypatch.setattr(net_fetch,'download',download)
    monkeypatch.setattr(primary.os.path,'getsize',lambda _:size_mb<<20)
    monkeypatch.setattr(shots,'probe',lambda _:dict(dur=60,w=1920,h=1080))
    result=primary.acquire([dict(i=0,name='Subject')],str(tmp_path/'sources'),
                           SimpleNamespace(openai_key=lambda:None,json_block=json.loads))
    assert bool(result['sources']) is accepted
    assert bool(result['errors']) is not accepted
