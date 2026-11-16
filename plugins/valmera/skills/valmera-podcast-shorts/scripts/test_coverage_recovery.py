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


def repeated_subject():
    return dict(windows=[dict(name=n,t0=i,t1=i+1,must_show='Actual '+n)
                         for i,n in enumerate(['A','B','A'])])


def test_repeated_director_referent_reuses_candidates_not_other_subjects():
    a=dict(path='/a.mp4',seconds=1.6);b=dict(path='/b.mp4',seconds=1.6)
    pool=coverage.shared_candidates(repeated_subject(),{0:[a,a],1:[b]})
    assert pool=={0:[a],1:[b],2:[a]}
    assert pool[2][0] is a  # references, no copied media or invented attribution


def test_repeated_candidate_is_checked_under_its_new_words_and_can_fail():
    a=dict(path='/a.mp4',seconds=1.6);b=dict(path='/b.mp4',seconds=1.6)
    seen=[]
    def check(rows):
        seen.extend(r[4]['window'] for r in rows)
        for r in rows:r[4].update(verified_motion=True,placed_motion_p90=2.)
        decisions=[dict(ok=r[4]['window']!=2,motion_ok=True) for r in rows]
        return [r for r,d in zip(rows,decisions) if d['ok']],dict(placements=decisions,rejected=sum(not d['ok'] for d in decisions),spend=[])
    accepted,_=coverage.checked_proposals(repeated_subject(),{0:[a],1:[b]}, {},3,check)
    assert 2 in seen and all(r[4]['window']!=2 for r in accepted)


def test_repeated_candidate_does_not_inflate_new_picture_density():
    shots=coverage.proposals(repeated_subject(),{0:[dict(path='/a.mp4',seconds=1.6)],1:[dict(path='/b.mp4',seconds=1.6)]},{})
    with pytest.raises(coverage.CoverageError) as error:
        coverage.require_coverage(shots,repeated_subject(),3)
    assert error.value.report['new_pictures']==2
    assert error.value.report['repeated_pictures']==1
    assert error.value.report['moving_seconds']==0


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


def test_revised_brief_can_reconsider_other_retained_subjects_without_approval(tmp_path):
    import wf1_short as wf
    sources=tmp_path/'sources';sources.mkdir();original=sources/'original.mp4';original.write_bytes(b'original')
    row=dict(url='https://publisher.example/orbits',path=str(original),title='Solar system animation',
             channel='Research institute',subjects=['Solar system'],where='primary')
    (sources/'manifest.json').write_text(json.dumps([row]))
    ordinary=wf.retained_candidates(str(tmp_path),{},['Scientific history'])
    assert 'Scientific history' not in ordinary
    revised=wf.retained_candidates(str(tmp_path),{},['Scientific history'],revised=True)
    candidate=revised['Scientific history'][0]
    assert candidate['url']==row['url'] and candidate['subjects']==['Solar system']
    assert candidate['path']==str(original) and not candidate.get('ok') and not candidate.get('verified_motion')
    again=wf.retained_candidates(str(tmp_path),revised,['Scientific history'],revised=True)
    assert len(again['Scientific history'])==1 and original.read_bytes()==b'original'


@pytest.mark.parametrize('content,expected',[(False,True),(True,False)])
def test_dark_visible_content_is_checked_instead_of_discarded_as_blank(tmp_path,content,expected):
    from PIL import Image,ImageDraw
    import wf1_short as wf
    im=Image.new('RGB',(320,200),(4,4,4))
    if content:
        draw=ImageDraw.Draw(im);draw.ellipse((30,30,290,170),outline='white',width=2)
        draw.text((150,140),'Earth',fill='white');draw.ellipse((150,90,170,110),fill='white')
    path=tmp_path/'frame.png';im.save(path)
    result=wf.dull(dict(still=str(path)))
    assert result['luma'][0]<.11 and result['dull'] is expected
    assert not result.get('verified_motion') and not result.get('ok')


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


@pytest.mark.parametrize('changed,missing',[(True,False),(False,False),(True,True)])
def test_motion_feedback_changes_research_once_without_resetting_budget(tmp_path,monkeypatch,changed,missing):
    import wf1_short as wf
    data=story();data['windows'][0]['must_show']='Authentic software'
    pack=dict(coverage=data,coverage_repairs=4,footage=[dict(i=0,clips=[dict(path='unchanged.mp4')])])
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack,record={})))
    (tmp_path/'placement_checks.json').write_text(json.dumps({'a':dict(ok=True,motion_ok=False,
        reason='Faithful interface',motion_reason='Cursor movement over an unchanged screen')}))
    calls=[]
    def revise(coverage,wanted,evidence,llm):
        assert wanted==[0] and evidence['faithful_stills'][0]['motion_reason']
        calls.append('direction')
        if changed:coverage['windows'][0]['must_show']='Authentic live software workflow'
        return {}
    def acquire(windows,work,llm):
        calls.append('publisher');assert windows[0]['say']==story()['windows'][0]['say']
        return dict(sources={},spend=[])
    monkeypatch.setattr(coverage,'repair_requirements',revise)
    monkeypatch.setattr(primary,'acquire',acquire)
    monkeypatch.setattr(wf,'_llm',lambda:None)
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    shortage=dict(weak_windows=[0],missing_windows=[0] if missing else [],moving_seconds=1,required_moving_seconds=4)
    assert wf.motion_research(ctx,pack,shortage) is (changed and not missing)
    assert wf.motion_research(ctx,pack,shortage) is False
    assert calls==([] if missing else ['direction','publisher'] if changed else ['direction'])
    assert pack['coverage_repairs']==4 and pack['footage'][0]['clips'][0]['path']=='unchanged.mp4'
    for old,new in zip(story()['windows'],pack['coverage']['windows']):
        for key in ('name','first','last','t0','t1','say'):assert old[key]==new[key]
    assert bool(pack.get('coverage_context_rescout_pending')) is (changed and not missing)


def test_failed_motion_research_is_not_retried_unchanged(tmp_path,monkeypatch):
    import wf1_short as wf
    pack=dict(coverage=story(),coverage_repairs=4,footage=[])
    path=tmp_path/'pack.json';path.write_text(json.dumps(dict(pack=pack,record={})))
    (tmp_path/'placement_checks.json').write_text(json.dumps({'a':dict(ok=True,motion_ok=False)}))
    calls=[]
    def failed(*args):
        calls.append(1);raise RuntimeError('local adapter unavailable')
    monkeypatch.setattr(coverage,'repair_requirements',failed)
    monkeypatch.setattr(wf,'_llm',lambda:None)
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    shortage=dict(weak_windows=[0],missing_windows=[],moving_seconds=1,required_moving_seconds=4)
    with pytest.raises(RuntimeError):wf.motion_research(ctx,pack,shortage)
    resumed=json.loads(path.read_text())['pack']
    assert wf.motion_research(ctx,resumed,shortage) is False and calls==[1]


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
