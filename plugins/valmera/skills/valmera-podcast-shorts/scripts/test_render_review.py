import json
import os
import sys
import pytest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
import wf1_short as wf


@pytest.fixture(autouse=True)
def isolated_retry_bookkeeping(monkeypatch):
    # These tests isolate render invalidation; semantic word-boundary repair
    # is exercised with complete timed coverage in documentary-coverage tests.
    monkeypatch.setattr(wf, '_repair_window_boundaries', lambda *args: None)


def test_newly_available_source_invalidates_exhausted_research_without_path_churn(tmp_path):
    sources=tmp_path/'sources';sources.mkdir();path=sources/'original.mp4'
    manifest=sources/'manifest.json';manifest.write_text(json.dumps([dict(url='https://publisher/film',path=str(path))]))
    absent=wf.source_signature(str(tmp_path));path.write_bytes(b'original source bytes')
    present=wf.source_signature(str(tmp_path));assert present!=absent
    assert wf.source_signature(str(tmp_path))==present


def test_final_audit_uses_extended_coverage_not_shorter_initial_ruler(tmp_path,monkeypatch):
    supply=tmp_path/'supply';supply.mkdir();audit=tmp_path/'audit'
    initial=dict(windows=[dict(t0=2,t1=4,name='Subject')])
    final=dict(windows=[dict(t0=1,t1=6,name='Subject')])
    (supply/'coverage.json').write_text(json.dumps(initial))
    (supply/'pack.json').write_text(json.dumps(dict(pack=dict(coverage=final))))
    for name in ('script.json','score.json'):(tmp_path/name).write_text('{}')
    monkeypatch.setattr(wf.wf3,'load_pack',lambda ctx:json.loads((supply/'pack.json').read_text())['pack'])
    wf.retain_audit(SimpleNamespace(supply=str(supply),run_dir=str(tmp_path)),str(audit))
    assert json.loads((audit/'coverage.json').read_text())==final
    assert json.loads((supply/'coverage.json').read_text())==initial
    assert json.loads((audit/'pack.json').read_text())['pack']['coverage']==final


def test_recovered_original_rescans_frames_without_repeating_exhausted_searches(tmp_path,monkeypatch):
    import wf1_primary_sources
    sources=tmp_path/'sources';sources.mkdir();movie=sources/'original.mp4';movie.write_bytes(b'original')
    (sources/'manifest.json').write_text(json.dumps([dict(url='https://publisher.example/film',path=str(movie),
        title='Company tour',channel='Company',subjects=['Company'])]))
    pack=dict(coverage=dict(contract=3,windows=[dict(name='Company',must_show='Actual company',say='Company',t0=0,t1=4)]),
              referent_research={},research_revision=7,coverage_context_checked=True,missing_document_attempted=True,
              coverage_repairs=4,research_contract=wf.RESEARCH_CONTRACT,research_sources='before recovery',
              footage=[],pictures={},credits=[])
    pack['referent_research']={'sources':[]}
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    monkeypatch.setattr(wf1_primary_sources,'acquire',lambda *a:pytest.fail('already exhausted research must not repeat'))
    calls=[]
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**kw:calls.append(kw) or dict(lines=[],credits=[]))
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    script=dict(title='Company',style={'id':'window'})
    shortage=dict(weak_windows=[0],missing_windows=[],real_seconds=4,moving_seconds=0)
    wf.refill(ctx,script,pack,shortage)
    assert len(calls)==1 and calls[0]['source_only'] is True
    assert pack['coverage_repairs']==4 and pack['research_sources']==wf.source_signature(str(tmp_path))
    with pytest.raises(RuntimeError,match='coverage still incomplete'):
        wf.refill(ctx,script,pack,shortage)
    assert len(calls)==1


def test_composed_rejection_rebuilds_product_plan_and_invalidates_render(tmp_path, monkeypatch):
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), log=lambda x: None)
    sc = dict(beats=dict(pictures=[dict(path='/supply/bad.jpg')]), spend=[])
    calls = []

    def draw(ctx, script, score):
        if calls:
            assert not (tmp_path/'film_engine.mp4').exists()
            assert not (tmp_path/'sound.m4a').exists()
        for name in ('film_engine.mp4', 'sound.m4a'):
            (tmp_path/name).write_text('old render')
        calls.append(1)
        return dict(film=str(tmp_path/'film_engine.mp4'))

    def review(ctx, rd, score):
        bad = len(calls)==1
        return dict(moving_share=1.,rejected=int(bad), placements=[dict(id=0,ok=not bad)], spend=[])

    def rebuild(ctx, script):
        assert json.load(open(tmp_path/'render_rejected.json'))==['/supply/bad.jpg']
        return dict(beats=dict(pictures=[]),spend=[])

    monkeypatch.setattr(wf.wf3,'render',draw)
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',rebuild)
    result=wf.render(ctx,{},sc)
    assert len(calls)==2 and sc['beats']['rendered_check']['rejected']==0
    assert len(result['picture_review'])==2
    assert json.load(open(tmp_path/'score.json'))['beats']['pictures']==[]


def test_unresolved_composed_rejection_cannot_claim_a_passing_check(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    def fresh():return dict(beats=dict(pictures=[dict(path='/supply/bad.jpg')]),spend=[])
    sc=fresh()
    monkeypatch.setattr(wf.wf3,'render',lambda *a:dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(moving_share=1.,rejected=1,placements=[dict(id=0,ok=False)],spend=[]))
    monkeypatch.setattr(wf,'score',lambda *a:fresh())
    with pytest.raises(RuntimeError, match='picture'):
        wf.render(ctx,{},sc)


def test_resume_repairs_a_persisted_rejection_before_rendering_stale_score(tmp_path,monkeypatch):
    from placement_check import CONTRACT
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda _:None)
    (tmp_path/'render_rejected_contract.json').write_text(json.dumps(CONTRACT))
    (tmp_path/'render_rejected.json').write_text('["bad.mp4"]')
    (tmp_path/'render_reviews.json').write_text(json.dumps([dict(placements=[dict(id=0,ok=False)],spend=[])]))
    (tmp_path/'film_engine.mp4').write_bytes(b'stale rejected render')
    sc=dict(beats=dict(pictures=[dict(path='bad.mp4')]),spend=[])
    monkeypatch.setattr(wf,'score',lambda *a:dict(beats=dict(pictures=[dict(path='replacement.mp4')]),spend=[]))
    def render(ctx,script,score):
        assert not (tmp_path/'film_engine.mp4').exists()
        assert score['beats']['pictures']==[dict(path='replacement.mp4')]
        return dict(film='replacement-render.mp4')
    monkeypatch.setattr(wf.wf3,'render',render)
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(rejected=0,moving_share=1.,placements=[],spend=[]))
    result=wf.render(ctx,{},sc)
    assert result['film']=='replacement-render.mp4'
    assert json.loads((tmp_path/'render_rejected.json').read_text())==['bad.mp4']


def test_later_repairs_keep_the_picture_that_passed_every_review(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    names=['a.jpg','b.jpg','c.jpg','d.jpg','good.jpg']
    def fresh(*args):
        p=tmp_path/'render_rejected.json'
        rejected=json.load(open(p)) if p.exists() else []
        return dict(beats=dict(pictures=[dict(path='/supply/'+n) for n in names if '/supply/'+n not in rejected]),spend=[])
    def review(ctx,rd,sc):
        pics=sc['beats']['pictures']
        bad=len(pics)>1
        return dict(moving_share=1.,rejected=int(bad),placements=[dict(id=i,ok=not bad or i!=0) for i in range(len(pics))],spend=[])
    monkeypatch.setattr(wf.wf3,'render',lambda *a:dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',fresh)
    sc=fresh();result=wf.render(ctx,{},sc)
    assert len(result['picture_review'])==5
    assert sc['beats']['pictures']==[dict(path='/supply/good.jpg')]
    assert '/supply/good.jpg' not in json.load(open(tmp_path/'render_rejected.json'))


def test_delivery_only_rejection_invalidates_brand_and_music_and_rebuilds(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None)
    (tmp_path/'script.json').write_text('{}')
    sc=dict(frames=90,beats=dict(pictures=[dict(path='/supply/bad.jpg')]),spend=[])
    rd=dict(film='engine.mp4');checks=[]
    def finish(ctx,rd,sc):
        if checks:
            assert not (tmp_path/'final_music.mp4').exists()
            assert not (tmp_path/'music.json').exists()
        for name in ('final.mp4','final_music.mp4','music.json'):(tmp_path/name).write_text('old')
        checks.append(1)
        return dict(final='clean.mp4',final_music='branded.mp4',checks=dict(ok=True))
    def review(ctx,rd,sc):
        assert rd['film']=='branded.mp4'
        bad=len(checks)==1
        return dict(moving_share=1.,rejected=int(bad),placements=[dict(id=0,ok=not bad)] if bad else [],spend=[])
    monkeypatch.setattr(wf.wf3,'check',finish)
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',lambda *a:dict(frames=90,beats=dict(pictures=[]),spend=[]))
    monkeypatch.setattr(wf,'render',lambda *a:dict(film='new-engine.mp4'))
    result=wf.check(ctx,rd,sc)
    assert len(checks)==2 and result['checks']['ok']
    assert result['checks']['podcast']['delivered_check']['rejected']==0
    assert json.load(open(tmp_path/'render_rejected.json'))==['/supply/bad.jpg']


def test_rejection_identifies_asset_when_research_passes_reuse_filenames(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    paths=['/supply/first/clips/000.mp4','/supply/repair/clips/000.mp4']
    sc=dict(beats=dict(pictures=[dict(path=p) for p in paths]),spend=[])
    def rebuilt(*args):
        rejected=json.load(open(tmp_path/'render_rejected.json'))
        return dict(beats=dict(pictures=[dict(path=p) for p in paths if p not in rejected]),spend=[])
    monkeypatch.setattr(wf,'score',rebuilt)
    wf._rebuild_rejected(ctx,{},sc,dict(placements=[dict(id=0,ok=False),dict(id=1,ok=True)]))
    assert sc['beats']['pictures']==[dict(path=paths[1])]


def test_changed_review_contract_rechecks_previously_rejected_assets(tmp_path):
    from placement_check import CONTRACT
    (tmp_path/'render_rejected.json').write_text('["old-layout.jpg"]')
    assert wf.current_rejections(str(tmp_path))==[]
    (tmp_path/'render_rejected_contract.json').write_text(str(CONTRACT))
    assert wf.current_rejections(str(tmp_path))==['old-layout.jpg']
    (tmp_path/'render_rejected_contract.json').write_text(str(CONTRACT-1))
    assert wf.current_rejections(str(tmp_path))==[]


def test_changed_plan_invalidates_old_render_before_frame_check(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    l1=tmp_path/'l1.json';l1.write_text('{"picture":"new"}')
    (tmp_path/'film_engine.mp4').write_text('old')
    (tmp_path/'final_music.mp4').write_text('old')
    def draw(*args):
        assert not (tmp_path/'film_engine.mp4').exists()
        assert not (tmp_path/'final_music.mp4').exists()
        return dict(film='new.mp4')
    monkeypatch.setattr(wf.wf3,'render',draw)
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(moving_share=1.,rejected=0,spend=[]))
    wf.render(ctx,{},dict(l1=str(l1),beats={}))
    assert json.load(open(tmp_path/'render_revision.json'))==wf.render_revision(dict(l1=str(l1)))


def test_document_recovery_does_not_starve_moving_footage_research(tmp_path,monkeypatch):
    import wf1_documents,wf1_coverage,wf1_primary_sources
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    pack=dict(coverage=dict(contract=3,windows=[dict(name='Company',must_show='Original product footage',say='Company',t0=0,t1=5)]),
              referent_research={'sources':[]},research_revision=7,footage=[],pictures={},credits=[])
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    monkeypatch.setattr(wf1_documents,'acquire',lambda *a,**kw:dict(pictures={'Company':[dict(path='new-original.jpg')]},credits=[],spend=[]))
    calls=[]
    def footage(*a,**kw):
        calls.append(kw);return dict(lines=[],credits=[])
    monkeypatch.setattr(wf.chain,'footage',footage)
    wf.refill(ctx,dict(title='Company',style={'id':'window'}),pack,
              dict(weak_windows=[0],real_seconds=12,moving_seconds=2))
    assert len(calls)==1 and calls[0]['motion_first']
    assert pack['pictures']=={}
    assert pack['coverage_repairs']==1
    monkeypatch.setattr(wf1_coverage,'expand_context',lambda *a:(False,{}))
    shortage=dict(weak_windows=[0],missing_windows=[0],real_seconds=12,moving_seconds=2)
    wf.refill(ctx,dict(title='Company',style={'id':'window'}),pack,shortage)
    assert len(calls)==1  # One factual-document attempt, then moving research continues.
    assert pack['pictures']['Company'][0]['path']=='new-original.jpg'
    def publisher(*args):
        path=tmp_path/'primary-videos';path.mkdir();(path/'manifest.json').write_text('[]')
        return dict(sources={'Company':[dict(url='https://publisher.example/demo')]},spend=[])
    monkeypatch.setattr(wf1_primary_sources,'acquire',publisher)
    wf.refill(ctx,dict(title='Company',style={'id':'window'}),pack,shortage)
    assert len(calls)==2 and calls[-1]['motion_first'] and calls[-1]['source_only']
    assert pack['coverage_repairs']==4  # No coverage gain: change approach, not another identical search.


def test_cached_primary_document_requires_exact_url_and_original_hash(tmp_path):
    import wf1_documents as D
    import hashlib
    path=tmp_path/'original.html';data=b'<h1>Original publication</h1>';path.write_bytes(data)
    sources=[dict(url='https://publisher.example/paper',path=str(path),sha256=hashlib.sha256(data).hexdigest())]
    assert D.cached_document(sources[0]['url'],sources)==data
    assert D.cached_document('https://different.example/paper',sources) is None
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):D.cached_document(sources[0]['url'],sources)


@pytest.mark.parametrize('remote,expected',[(None,True),('1',False)])
def test_remote_render_adapter_does_not_reserve_a_mac_compute_slot(tmp_path,monkeypatch,remote,expected):
    ctx=SimpleNamespace(supply=str(tmp_path),tmp=str(tmp_path/'tmp'),run_id='test')
    l1=tmp_path/'l1.json';l1.write_text('{}');(tmp_path/'sound.m4a').write_bytes(b'already mixed')
    monkeypatch.setenv('SHELL_RENDER_CMD','/remote-render-adapter.sh')
    if remote:monkeypatch.setenv('SHELL_RENDER_REMOTE',remote)
    else:monkeypatch.delenv('SHELL_RENDER_REMOTE',raising=False)
    monkeypatch.setattr(wf.wf3,'load_pack',lambda ctx:{})
    monkeypatch.setattr(wf.chain,'alien_bank',lambda ctx,path:path)
    def run(ctx,args,**kw):
        assert kw['heavy'] is expected
        from pathlib import Path
        Path(args[-1]).write_bytes(b'engine output')
        return '{}'
    monkeypatch.setattr(wf.chain,'sh',run)
    result=wf.wf3.render(ctx,{},dict(l1=str(l1)))
    assert os.path.exists(result['film'])


def test_remote_music_adapter_keeps_payoff_and_shared_track_exclusions(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),state_root=str(tmp_path),run_id='test',workflow='wf1',log=lambda _:None,args={'music':True,'music_ledger':str(tmp_path/'used.json')})
    (tmp_path/'used.json').write_text('["previous-track"]')
    monkeypatch.setenv('SHELL_MUSIC_CMD','/remote-music.sh');monkeypatch.setenv('SHELL_MUSIC_REMOTE','1')
    calls=[]
    def run(ctx,args,**kw):
        calls.append(1);a=json.load(open(args[-1]))
        assert args[:2]==['zsh','/remote-music.sh'] and not kw['heavy']
        assert a['used']==['previous-track'] and a['payoff']==12.5
        assert a['l1']=='audio-scene.json' and a['kind']=='podcast'
        raise RuntimeError('test dispatch boundary')
    monkeypatch.setattr(wf.chain,'sh',run)
    assert wf.chain.music_version(ctx,dict(film='actual-engine.mp4'),dict(l1='scene.json',l1_audio='audio-scene.json',turn_s=12.5)) is None
    assert calls and json.load(open(tmp_path/'used.json'))==['previous-track']


def test_corrected_source_eligibility_and_subjects_invalidate_exhausted_search(tmp_path):
    sources=tmp_path/'sources';sources.mkdir();path=sources/'original.mp4';path.write_bytes(b'original')
    row=dict(url='https://publisher/film',path=str(path),where='unsupported',subjects=['Old subject'])
    manifest=sources/'manifest.json';manifest.write_text(json.dumps([row]));first=wf.source_signature(str(tmp_path))
    row.update(where='primary',subjects=['Actual subject']);manifest.write_text(json.dumps([row]))
    assert wf.source_signature(str(tmp_path))!=first


def test_composed_still_is_retained_but_cannot_certify_moving_coverage(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda _:None)
    pack=dict(footage=[dict(i=0,clips=[dict(path='faithful-still.mp4')])])
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:json.load(open(tmp_path/'pack.json'))['pack'])
    sc=dict(beats=dict(pictures=[dict(path='faithful-still.mp4')]),spend=[])
    calls=[]
    def review(*args):
        calls.append(1)
        return dict(rejected=0,moving_share=.2 if len(calls)==1 else .7,
                    placements=[dict(id=0,ok=True,motion_ok=False)],spend=[])
    def rebuild(*args):
        saved=json.load(open(tmp_path/'pack.json'))['pack']
        assert saved['composed_stills']==['faithful-still.mp4']
        assert saved['footage']==pack['footage']
        assert not (tmp_path/'render_rejected.json').exists()
        return dict(beats=dict(pictures=[dict(path='new-motion.mp4')]),spend=[])
    monkeypatch.setattr(wf.wf3,'render',lambda *a:dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',rebuild)
    result=wf.render(ctx,{},sc)
    assert len(result['picture_review'])==2
    assert sc['beats']['rendered_check']['moving_share']==.7


def test_unchanged_final_motion_failure_stops_without_retry(tmp_path,monkeypatch):
    pack=dict(composed_stills=['still.mp4'])
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path))
    sc=dict(beats=dict(pictures=[dict(path='still.mp4')]))
    with pytest.raises(RuntimeError,match='lack verified moving footage'):
        wf._rebuild_motion(ctx,{},sc,dict(placements=[dict(id=0,ok=True,motion_ok=False)]))


@pytest.mark.parametrize('wrong_hash,bad_time',[(True,False),(False,True),(False,False)])
def test_external_hearing_is_bound_to_source_and_valid_times(tmp_path,monkeypatch,wrong_hash,bad_time):
    import hashlib
    import chain
    source=tmp_path/'source.mp4';source.write_bytes(b'original media')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    hearing=dict(lines=[dict(words=[dict(w='Actual',t0=0,t1=99 if bad_time else .4)])])
    response=dict(source_sha256='wrong' if wrong_hash else digest,hearing=hearing,engine={'provider':'local-whisper'})
    monkeypatch.setenv('SHELL_HEAR_CMD','isolated-transcriber')
    def process(cmd,**kwargs):
        assert json.loads(kwargs['input'])['source_sha256']==digest
        return SimpleNamespace(returncode=0,stdout=json.dumps(response),stderr='')
    monkeypatch.setattr(chain.subprocess,'run',process)
    ctx=SimpleNamespace(supply=str(tmp_path))
    if wrong_hash or bad_time:
        with pytest.raises(ValueError):chain.external_hearing(ctx,str(source),1.)
        assert not (tmp_path/'hearing.json').exists()
    else:
        assert chain.external_hearing(ctx,str(source),1.)
        assert json.load(open(tmp_path/'hearing.json'))['transport']['source_sha256']==digest
        monkeypatch.setattr(chain.subprocess,'run',lambda *a,**kw:pytest.fail('same aligned source must not transcribe again'))
        assert chain.external_hearing(ctx,str(source),1.)


def test_platform_wall_goes_directly_to_original_publishers(tmp_path,monkeypatch):
    import wf1_documents,wf1_primary_sources
    pack=dict(platform_walled=True,coverage=dict(contract=3,windows=[dict(name='Campus',must_show='Actual campus',say='Campus',t0=0,t1=4)]),
              referent_research={'sources':[]},research_revision=7,footage=[],pictures={},credits=[])
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    monkeypatch.setattr(wf1_documents,'acquire',lambda *a,**kw:dict(pictures={},credits=[],spend=[]))
    def acquire(windows,work,llm):
        from pathlib import Path
        p=Path(work);p.mkdir();(p/'manifest.json').write_text('[]')
        return dict(sources={'Campus':[dict(url='https://university.example/tour.mp4')]},spend=[])
    monkeypatch.setattr(wf1_primary_sources,'acquire',acquire)
    calls=[]
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**kw:calls.append(kw) or dict(lines=[],credits=[]))
    wf.refill(SimpleNamespace(supply=str(tmp_path),log=lambda _:None),dict(title='Campus',style={'id':'window'}),pack,
              dict(weak_windows=[0],real_seconds=0,moving_seconds=0))
    assert calls[0]['source_only'] is True and calls[0]['hear_s']==0
    assert pack['coverage_repairs']==4
