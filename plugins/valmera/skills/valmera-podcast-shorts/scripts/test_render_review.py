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
    # exhausted research ends research, not the short: the film is built from every approved picture
    wf.refill(ctx,script,pack,shortage)
    assert len(calls)==1 and 'still short' in pack['coverage_exhausted'] and wf.coverage_final(pack)
    assert json.load(open(tmp_path/'pack.json'))['pack']['coverage_exhausted']==pack['coverage_exhausted']


def test_composed_rejection_rebuilds_product_plan_and_invalidates_render(tmp_path, monkeypatch):
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), log=lambda x: None)
    sc = dict(beats=dict(pictures=[dict(path='/supply/bad.jpg', window=2)]), spend=[])
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
        return dict(moving_share=1.,rejected=int(bad), placements=[dict(id=0,ok=not bad,fault='contradiction',reason='wrong era')], spend=[])

    def rebuild(ctx, script):
        # the finding is local to the window it was found in: the picture stays eligible everywhere else
        assert json.load(open(tmp_path/'render_rejected.json'))==[dict(window=2,path='/supply/bad.jpg',fault='contradiction',
                                                                      placed='/supply/bad.jpg',reason='wrong era')]
        return dict(beats=dict(pictures=[]),spend=[])

    monkeypatch.setattr(wf.wf3,'render',draw)
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',rebuild)
    result=wf.render(ctx,{},sc)
    assert len(calls)==2 and sc['beats']['rendered_check']['rejected']==0
    assert len(result['picture_review'])==2
    assert json.load(open(tmp_path/'score.json'))['beats']['pictures']==[]


def test_unresolved_composed_rejection_cannot_claim_a_passing_check(tmp_path,monkeypatch):
    """A finding that comes back after every repair (framed again, given away) is recorded on the film, which is
    delivered: the record never claims a passing check, and the short is never lost to its repair (20 Nov)."""
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    def fresh():return dict(beats=dict(pictures=[dict(path='/supply/bad.jpg')]),spend=[])
    sc=fresh()
    renders=[]
    monkeypatch.setattr(wf.wf3,'render',lambda *a:renders.append(1) or dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(moving_share=1.,rejected=1,placements=[dict(id=0,ok=False)],spend=[]))
    monkeypatch.setattr(wf,'score',lambda *a:fresh())
    rd=wf.render(ctx,{},sc)
    assert rd['film']=='fake.mp4' and len(renders)==3
    assert sc['beats']['rendered_check']['rejected']==1 and sc['beats']['rendered_check']['unrepaired']==[0]
    assert [r['fault'] for r in wf.current_rejections(str(tmp_path))]==['reframe','contradiction']


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


def test_resume_rebuilds_caption_pagination_before_reusing_render(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda _:None)
    (tmp_path/'film_engine.mp4').write_bytes(b'old caption omission')
    sc=dict(beats=dict(pictures=[],caption_lines=4),spend=[])
    monkeypatch.setattr(wf,'score',lambda *a:dict(beats=dict(pictures=[],caption_lines=4,caption_contract=wf.wf1_film.CAPTION_CONTRACT),spend=[]))
    def render(ctx,script,score):
        assert score['beats']['caption_contract']==wf.wf1_film.CAPTION_CONTRACT
        assert not (tmp_path/'film_engine.mp4').exists()
        return dict(film='complete-captions.mp4')
    monkeypatch.setattr(wf.wf3,'render',render)
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(rejected=0,moving_share=1.,placements=[],spend=[]))
    assert wf.render(ctx,{},sc)['film']=='complete-captions.mp4'


def test_later_repairs_keep_the_picture_that_passed_every_review(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    names=['a.jpg','b.jpg','c.jpg','d.jpg','good.jpg']
    def fresh(*args):
        p=tmp_path/'render_rejected.json'
        rejected={r['path'] for r in json.load(open(p)) if r['fault']=='contradiction'} if p.exists() else set()
        return dict(beats=dict(pictures=[dict(path='/supply/'+n) for n in names if '/supply/'+n not in rejected]),spend=[])
    def review(ctx,rd,sc):
        pics=sc['beats']['pictures']
        bad=len(pics)>1
        return dict(moving_share=1.,rejected=int(bad),placements=[dict(id=i,ok=not bad or i!=0,fault='contradiction')
                                                                  for i in range(len(pics))],spend=[])
    monkeypatch.setattr(wf.wf3,'render',lambda *a:dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',fresh)
    sc=fresh();result=wf.render(ctx,{},sc)
    assert len(result['picture_review'])==5
    assert sc['beats']['pictures']==[dict(path='/supply/good.jpg')]
    assert '/supply/good.jpg' not in {r['path'] for r in json.load(open(tmp_path/'render_rejected.json'))}


def test_the_delivered_film_is_not_reviewed_again_on_the_frames_the_render_review_checked(tmp_path,monkeypatch):
    """16 Nov: the brand finish and the music change no programme frame; the second review cost 50-350 model calls a
    short. The delivered record carries the render review."""
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None)
    rendered=dict(moving_share=.8,rejected=0,placements=[dict(id=0,ok=True)],spend=[])
    sc=dict(frames=90,beats=dict(pictures=[dict(path='/supply/good.mp4')],rendered_check=rendered),spend=[])
    checks=[]
    monkeypatch.setattr(wf.wf3,'check',lambda ctx,rd,sc:checks.append(1) or dict(final='clean.mp4',final_music='branded.mp4',checks=dict(ok=True)))
    monkeypatch.setattr(wf,'_render_review',lambda *a:pytest.fail('the delivered film was reviewed again'))
    result=wf.check(ctx,dict(film='engine.mp4'),sc)
    assert checks==[1] and result['checks']['ok']
    podcast=result['checks']['podcast']
    assert podcast['delivered_check']['rejected']==0 and podcast['delivered_check']['placements']==rendered['placements']


def test_rejection_identifies_asset_when_research_passes_reuse_filenames(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    paths=['/supply/first/clips/000.mp4','/supply/repair/clips/000.mp4']
    sc=dict(beats=dict(pictures=[dict(path=p) for p in paths]),spend=[])
    def rebuilt(*args):
        rejected={r['path'] for r in json.load(open(tmp_path/'render_rejected.json'))}
        return dict(beats=dict(pictures=[dict(path=p) for p in paths if p not in rejected]),spend=[])
    monkeypatch.setattr(wf,'score',rebuilt)
    wf._rebuild_rejected(ctx,{},sc,dict(placements=[dict(id=0,ok=False,fault='contradiction'),dict(id=1,ok=True)]))
    assert sc['beats']['pictures']==[dict(path=paths[1])]


def test_changed_review_contract_rechecks_previously_rejected_assets(tmp_path):
    from placement_check import CONTRACT
    (tmp_path/'render_rejected.json').write_text('["old-layout.jpg"]')
    assert wf.current_rejections(str(tmp_path))==[]
    (tmp_path/'render_rejected_contract.json').write_text(str(CONTRACT))
    assert wf.current_rejections(str(tmp_path))==[dict(window=None,path='old-layout.jpg',fault='contradiction')]
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
    monkeypatch.setattr(wf.chain,'lay_devices',lambda ctx,path:path)
    monkeypatch.setattr(wf.chain,'measure_devices',lambda ctx,film,scene:None)
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


def test_unchanged_final_motion_shortfall_keeps_the_film_without_retry(tmp_path,monkeypatch):
    pack=dict(composed_stills=['still.mp4'])
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf,'score',lambda *a:pytest.fail('nothing new to research: no rebuild'))
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    sc=dict(beats=dict(pictures=[dict(path='still.mp4')]))
    assert wf._rebuild_motion(ctx,{},sc,dict(moving_share=.3,placements=[dict(id=0,ok=True,motion_ok=False)])) is False


def test_motion_is_researched_once_a_short_then_the_film_stands_on_its_faithful_stills(tmp_path,monkeypatch):
    pack=dict(composed_stills=[])
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    rebuilt=[]
    monkeypatch.setattr(wf,'score',lambda *a:rebuilt.append(1) or dict(beats=dict(pictures=[dict(path='new.jpg')]),spend=[]))
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    report=dict(moving_share=.3,placements=[dict(id=0,ok=True,motion_ok=False)])
    assert wf._rebuild_motion(ctx,{},dict(beats=dict(pictures=[dict(path='a.jpg')])),report) is True
    assert wf._rebuild_motion(ctx,{},dict(beats=dict(pictures=[dict(path='b.jpg')])),report) is False
    assert rebuilt==[1] and pack['motion_recovered'] is True


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


def test_a_walled_pack_searches_the_platform_again_instead_of_freezing(tmp_path,monkeypatch):
    """19 Nov: a walled pack jumped to pass 4 (source only, no platform search), found no publisher video and was built
    from no picture at all. Now the wall is an outage: research runs its ordinary passes and searches again."""
    import wf1_documents,wf1_primary_sources
    pack=dict(platform_walled=True,coverage=dict(contract=3,windows=[dict(name='Campus',must_show='Actual campus',say='Campus',t0=0,t1=4)]),
              referent_research={'sources':[]},research_revision=7,footage=[],pictures={},credits=[])
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    monkeypatch.setattr(wf1_documents,'acquire',lambda *a,**kw:dict(pictures={},credits=[],spend=[]))
    monkeypatch.setattr(wf1_primary_sources,'acquire',lambda *a,**kw:pytest.fail('a wall is not a reason to skip the platform search'))
    calls=[]
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**kw:calls.append(kw) or dict(lines=[],credits=[]))
    wf.refill(SimpleNamespace(supply=str(tmp_path),log=lambda _:None),dict(title='Campus',style={'id':'window'}),pack,
              dict(weak_windows=[0],real_seconds=0,moving_seconds=0))
    assert calls[0]['source_only'] is False and calls[0]['hear_s']==0
    assert pack['coverage_repairs']==1 and 'coverage_exhausted' not in pack


def _walled_supply(tmp_path, rows, sources=4):
    pack=dict(platform_walled=True,coverage=dict(contract=3,context='Speaker: Mark Zuckerberg',
              windows=[dict(name='Meta Superintelligence Labs',must_show='Its launch',say='we started a lab',t0=0,t1=4)]),
              footage=[],pictures={},credits=[],wall=dict(sources=sources,refused=rows))
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack,record=dict(footage=None))))
    return pack


def _refused_row(n):
    return dict(url=f'https://www.youtube.com/watch?v=video{n:05d}',title=f'Launch {n}',channel='Bloomberg Tech',where='youtube',
                duration=120,subjects=['Meta Superintelligence Labs'],error='default: Sign in to confirm you are not a bot')


def test_the_wall_refetches_the_exact_refused_sources_and_cuts_what_opens(tmp_path,monkeypatch):
    import wf1_primary_sources
    rows=[_refused_row(n) for n in range(4)]
    pack=_walled_supply(tmp_path,rows)
    fetched=[]

    def opener(url,dest,duration=None):
        fetched.append(url)
        if url.endswith('3'):
            raise RuntimeError('mweb: ERROR: Sign in to confirm you are not a bot')
        os.makedirs(dest,exist_ok=True);p=os.path.join(dest,'whole.mp4');open(p,'wb').write(b'x'*3000)
        return p,'tv+proxy'
    monkeypatch.setattr(wf1_primary_sources,'_open_exact',opener)
    monkeypatch.setattr(wf1_primary_sources.time,'sleep',lambda s:None)
    calls=[]

    def footage(ctx,script,durs,**kw):
        calls.append(kw)
        named=script['script']['named'][0]
        assert {r['url'] for r in named['video_sources']}=={r['url'] for r in rows[:3]}     # exactly the reopened sources
        out=os.path.join(ctx.supply,kw['out_name']);os.makedirs(out,exist_ok=True)
        return dict(lines=[dict(i=0,clips=[dict(file='c0.mp4',seconds=1.6,source=dict(url=rows[0]['url']))])],
                    credits=[dict(line=0,url=rows[0]['url'],channel='Bloomberg Tech',title='Launch 0',time_range=[3,4.6])],counts=dict(clips=1))
    monkeypatch.setattr(wf.chain,'footage',footage)
    monkeypatch.setattr(wf.wf3,'clip_facts',lambda c:c)
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    rec=wf.recover_wall(ctx,dict(title='Lab',style={'id':'window'}),pack,waits=(0,))
    assert fetched==[r['url'] for r in rows]                                  # the search is not run again: only these
    assert len(calls)==1 and calls[0]['source_only'] is True and calls[0]['lines']==[0]
    assert pack['platform_walled'] is False and [r['url'] for r in pack['wall']['refused']]==[rows[3]['url']]
    assert pack['footage'][0]['clips'][0]['path'].endswith('c0.mp4') and pack['credits'][0]['credit']=='Bloomberg Tech'
    kept=json.load(open(tmp_path/'sources'/'manifest.json'))
    assert {r['url'] for r in kept}=={r['url'] for r in rows[:3]} and kept[0]['subjects']==['Meta Superintelligence Labs']
    assert rec['footage']['clips']==1
    # the same reopened sources are never cut and merged twice (a resume after the merge)
    assert wf.reopened_footage(ctx,dict(title='Lab',style={'id':'window'}),pack,[dict(r) for r in rows[:3]])==0


def test_a_wall_after_backoff_returns_available_pictures_and_original_camera(tmp_path,monkeypatch):
    import wf1_film,wf1_primary_sources
    rows=[_refused_row(n) for n in range(4)]
    pack=_walled_supply(tmp_path,rows)
    tries,slept=[],[]

    def opener(url,dest,duration=None):
        tries.append(url)
        raise RuntimeError('tv+proxy: ERROR: [youtube] x: Sign in to confirm you are not a bot')
    monkeypatch.setattr(wf1_primary_sources,'_open_exact',opener)
    monkeypatch.setattr(wf1_primary_sources.time,'sleep',lambda s:None)
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**k:pytest.fail('nothing opened: nothing to cut'))
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    wf.recover_wall(ctx,dict(title='Lab',style={'id':'window'}),pack,waits=(0,120,300),sleep=slept.append)
    assert pack['coverage_exhausted']
    assert slept==[120,300] and len(tries)==12                                # every refused source, at each try
    stored=json.load(open(tmp_path/'pack.json'))['pack']
    assert stored['platform_walled'] is False and len(stored['wall']['refused'])==4 and stored['wall']['refused'][0]['tries']==3


def test_a_wall_is_never_declared_lifted_on_sources_sorted_gone_while_nothing_was_cut(tmp_path,monkeypatch):
    """Words the wall makes that the sort does not know (here a 'Video unavailable' under the wall) send every row to
    'gone': with nothing opened and no clip in the pack the rows wait with the rest, and the short stops with WALL
    rather than going on to a film with no footage. Once the short has footage, a gone source is not waited for."""
    import wf1_film,wf1_primary_sources
    rows=[_refused_row(n) for n in range(4)]
    pack=_walled_supply(tmp_path,rows)
    monkeypatch.setattr(wf1_primary_sources,'_open_exact',lambda *a:(_ for _ in ()).throw(RuntimeError('tv: ERROR: [youtube] x: Video unavailable')))
    monkeypatch.setattr(wf1_primary_sources.time,'sleep',lambda s:None)
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**k:pytest.fail('nothing opened: nothing to cut'))
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    wf.recover_wall(ctx,dict(title='Lab',style={'id':'window'}),pack,waits=(0,120),sleep=lambda s:None)
    assert pack['coverage_exhausted']
    stored=json.load(open(tmp_path/'pack.json'))['pack']
    assert stored['platform_walled'] is False and stored['wall']['sources']==4 and len(stored['wall']['refused'])==4
    assert stored['wall']['refused'][0]['tries']==2 and not stored.get('gone_sources')      # tried again at each wait
    # the same sort on a short that has footage: the gone sources are not waited for and the short goes on
    pack=_walled_supply(tmp_path,[_refused_row(n) for n in range(4)])
    pack['footage']=[dict(i=0,clips=[dict(path='/c0.mp4')])]
    rec=wf.recover_wall(ctx,dict(title='Lab',style={'id':'window'}),pack,waits=(0,))
    assert pack['platform_walled'] is False and pack['wall']['sources']==0 and len(pack['gone_sources'])==4 and rec is not None


def test_a_walled_pack_is_never_returned_as_final_by_supply(tmp_path,monkeypatch):
    import wf1_coverage,wf1_alignment
    pack=_walled_supply(tmp_path,[_refused_row(0),_refused_row(1)],sources=2)
    pack["coverage"]["contract"] = wf1_coverage.CONTRACT
    pack.update(alignment_contract=wf1_alignment.CONTRACT)
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack,record=dict(footage=None))))
    seen=[]
    monkeypatch.setattr(wf,'recover_wall',lambda ctx,script,p,rec,waits=None:seen.append((p['wall']['sources'],waits)) or dict(rec,recovered=True))
    monkeypatch.setattr(wf.chain,'pack',lambda *a:pytest.fail('a walled pack keeps its supply: nothing is planned or searched anew'))
    ctx=SimpleNamespace(supply=str(tmp_path),tmp=str(tmp_path),log=lambda _:None)
    assert wf.supply(ctx,dict(title='Lab',style={'id':'window'}))['recovered'] and seen==[(2,wf.WALL_RESUME_S)]
    assert wf.WALL_RESUME_S[0]==0 and wf.WALL_BACKOFF_S[0]>0                  # at once on a resume; after a wait when just met


def test_a_wall_met_in_research_does_not_spend_a_recovery_pass(tmp_path,monkeypatch):
    import wf1_coverage,wf1_alignment,wf1_film
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[]),alignment_contract=wf1_alignment.CONTRACT,
              footage=[dict(i=0,clips=[dict(path='/c.mp4')])],lines=[],blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg')
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf.wf3,'env_path',lambda:'')
    monkeypatch.setattr(wf,'direction_of',lambda *a:dict())
    monkeypatch.setattr(wf.chain,'wants_music',lambda _:True)

    def refill(ctx,script,pack,report):
        raise RuntimeError(f'{wf1_film.WALL}: 9 of 10 footage sources still refused')
    monkeypatch.setattr(wf,'refill',refill)
    monkeypatch.setattr(wf.wf1_film,'build',lambda *a:(_ for _ in ()).throw(wf1_coverage.CoverageError('short',dict(weak_windows=[0]))))
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None)
    with pytest.raises(RuntimeError,match=wf1_film.WALL):
        wf.score(ctx,dict(title='t',style=dict(id='window'),hook_line='h',close_line='c'))
    assert json.load(open(tmp_path/'pack.json'))['pack']['coverage_recoveries']==0


def test_an_empty_window_takes_its_next_depiction_rung_before_the_final_build(tmp_path,monkeypatch):
    """19 Nov m03: the watermelon windows' pictures were all turned down and 34 s ran on the speaker. A window research
    leaves empty goes down its ladder (acquired at once) before the build is final."""
    import wf1_coverage,wf1_alignment
    window=dict(name='Mark Zuckerberg eating watermelon on a Meta livestream',must_show='Zuckerberg with watermelon on Facebook Live',
                say='a watermelon',t0=0,t1=4,rung=0,
                depictions=[dict(name='Mark Zuckerberg Facebook Live',must_show='Zuckerberg on one of his livestreams',video_searches=['Zuckerberg Facebook Live 2016']),
                            dict(name='Watermelon',must_show='A watermelon being cut',video_searches=['cutting a watermelon'])])
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[window]),alignment_contract=wf1_alignment.CONTRACT,
              footage=[dict(i=0,clips=[dict(path='/c.mp4')])],lines=[],pictures={'Watermelon':[dict(path='/w.jpg')]},credits=[],
              blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg',coverage_recoveries=wf.RECOVERY_PASSES)
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf.wf3,'env_path',lambda:'')
    monkeypatch.setattr(wf,'direction_of',lambda *a:dict())
    monkeypatch.setattr(wf.chain,'wants_music',lambda _:True)
    monkeypatch.setattr(wf.F,'taste_check',lambda *a:dict(ok=True,problems=[]))
    monkeypatch.setattr(wf.F,'audio_scene',lambda *a:{})
    monkeypatch.setattr(wf,'refill',lambda *a:pytest.fail('research is spent: the ladder acquires, not another research pass'))
    monkeypatch.setattr(wf.PX,'pictures',lambda named,*a,**k:({n['name']:[] for n in named},[],0.0))
    asked=[]

    def footage(ctx,script,durs,**kw):
        asked.append((kw['lines'],[(n['name'],n['video_searches']) for n in script['script']['named']],kw['source_only']))
        return dict(lines=[],credits=[],counts=dict(clips=0))
    monkeypatch.setattr(wf.chain,'footage',footage)
    finals=[]

    def build(sc,pack,style,knobs):
        finals.append(knobs['coverage_final'])
        if not knobs['coverage_final']:
            raise wf1_coverage.CoverageError('short',dict(weak_windows=[0],missing_windows=[0]))
        return dict(items=[],dur_f=90),dict(pictures=[])
    monkeypatch.setattr(wf.wf1_film,'build',build)
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None)
    pack['coverage_missing']=[0]
    wf.score(ctx,dict(title='t',style=dict(id='window'),hook_line='h',close_line='c'))
    assert asked==[([0],[('Mark Zuckerberg Facebook Live',['Zuckerberg Facebook Live 2016'])],False),
                   ([0],[('Watermelon',['cutting a watermelon'])],False)]
    assert finals==[False,False,True] and window['rung']==2 and window['ladder'][0]['name'].startswith('Mark Zuckerberg eating')


def test_a_short_spends_at_most_its_recovery_passes_then_is_built_from_every_approved_picture(tmp_path,monkeypatch):
    """15-16 Nov: up to seven research passes a short, and a short lost when they ran out. Now RECOVERY_PASSES, counted
    across rebuilds, then the final build."""
    import wf1_coverage,wf1_alignment
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[]),alignment_contract=wf1_alignment.CONTRACT,
              footage=[dict(i=0,clips=[dict(path='/c.mp4')])],lines=[],blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg')
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf.wf3,'env_path',lambda:'')
    monkeypatch.setattr(wf,'direction_of',lambda *a:dict())
    monkeypatch.setattr(wf.chain,'wants_music',lambda _:True)
    monkeypatch.setattr(wf.F,'taste_check',lambda *a:dict(ok=True,problems=[]))
    monkeypatch.setattr(wf.F,'audio_scene',lambda *a:{})
    refills,finals=[],[]
    monkeypatch.setattr(wf,'refill',lambda ctx,script,pack,report:refills.append(1))
    def build(sc,pack,style,knobs):
        finals.append(knobs['coverage_final'])
        if not knobs['coverage_final']:
            raise wf1_coverage.CoverageError('short',dict(weak_windows=[0]))
        return dict(items=[],dur_f=90),dict(pictures=[])
    monkeypatch.setattr(wf.wf1_film,'build',build)
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None)
    script=dict(title='t',style=dict(id='window'),hook_line='h',close_line='c')
    wf.score(ctx,script)
    assert len(refills)==wf.RECOVERY_PASSES and finals==[False]*wf.RECOVERY_PASSES+[True]
    wf.score(ctx,script)                                      # a rebuild after a composed rejection: no new research
    assert len(refills)==wf.RECOVERY_PASSES and finals[-1] is True
    assert json.load(open(tmp_path/'pack.json'))['pack']['coverage_recoveries']==wf.RECOVERY_PASSES


def test_the_composed_frames_get_one_look_that_also_audits_transitions(tmp_path,monkeypatch):
    """16 Nov cost lane: the transition audit used to be a second pass over the frames the first pass had just
    passed. One composed look now carries both; a rejection there still rebuilds the film."""
    import placement_check
    l1=tmp_path/'l1.json';l1.write_text(json.dumps(dict(items=[dict(k='word',role='big',f=[10,20],t='SUN'),
        dict(k='word',role='graphic',fact_type='source_fact',f=[2,15],t='NASA · Example')])))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda ctx:dict(lines=[dict(text='the Sun')],words=[],ffmpeg='ffmpeg'))
    monkeypatch.setattr(wf.F,'Clock',lambda words,script:None)
    passes=[]
    def check(rows,script,clock,work,ffmpeg,review='placement',**kw):
        passes.append((review,len(rows)))
        assert rows[0][4]['display_words'][-1] == dict(f=[2,15],text='NASA · Example')
        return [],dict(rejected=1,accepted=len(rows)-1,spend=[{}],
                       placements=[dict(id=i,f=r[:2],ok=i!=1,reason='wrong era' if i==1 else 'fits',motion_ok=True) for i,r in enumerate(rows)])
    monkeypatch.setattr(placement_check,'check',check)
    sc=dict(l1=str(l1),beats=dict(pictures=[dict(f=[0,30],name='Sun',source='u'),dict(f=[30,60],name='Sun',source='u')]))
    report=wf._render_review(SimpleNamespace(supply=str(tmp_path)),dict(film='film.mp4'),sc)
    assert passes==[('composed',2)]
    assert [p['transition_audit']['ok'] for p in report['placements']]==[True,False] and report['rejected']==1


def test_a_composed_fault_is_framed_again_first_then_gives_only_its_window_to_the_next_picture(tmp_path,monkeypatch):
    """Round 1, 19 Nov: one composed 'no' removed a picture from every window for good. A crop through a face is
    framed again first; a second finding on the same placement gives that one window's slot away; the picture stays
    eligible in every other window."""
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    plans=[]
    def rebuild(*a):
        found=wf.current_rejections(str(tmp_path))
        plans.append(found)
        return dict(beats=dict(pictures=[dict(path='/supply/clip.mp4',window=1,reframed=True)]),spend=[])
    monkeypatch.setattr(wf,'score',rebuild)
    sc=dict(beats=dict(pictures=[dict(path='/supply/clip.mp4',window=1)]),spend=[])
    face_cut=dict(placements=[dict(id=0,ok=False,fault='reframe',reason='the crop cuts his chin')])
    wf._rebuild_rejected(ctx,{},sc,face_cut)
    assert plans[-1]==[dict(window=1,path='/supply/clip.mp4',fault='reframe',placed='/supply/clip.mp4',reason='the crop cuts his chin')]
    assert not wf.unrepaired(sc['beats']['pictures'][0],plans[-1])          # the rebuilt plan framed it again
    wf._rebuild_rejected(ctx,{},sc,face_cut)                                # framed again and still cut: next picture
    assert [r['fault'] for r in plans[-1]]==['reframe','contradiction'] and {r['window'] for r in plans[-1]}=={1}
    knobs={}
    found=wf.current_rejections(str(tmp_path))
    knobs['reject']=[r for r in found if r.get('fault')!='reframe']
    import wf1_coverage
    assert wf1_coverage.is_rejected({(r['window'],r['path']) for r in knobs['reject']},1,'/supply/clip.mp4')
    assert not wf1_coverage.is_rejected({(r['window'],r['path']) for r in knobs['reject']},2,'/supply/clip.mp4')


def test_compose_hands_the_findings_to_the_build_by_window(tmp_path,monkeypatch):
    import wf1_coverage,wf1_alignment
    from placement_check import CONTRACT
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[]),alignment_contract=wf1_alignment.CONTRACT,
              footage=[dict(clips=[dict(path='a.mp4')])],lines=[],blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg')
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    (tmp_path/'render_rejected_contract.json').write_text(json.dumps(CONTRACT))
    (tmp_path/'render_rejected.json').write_text(json.dumps([dict(window=3,path='a.mp4',fault='contradiction'),
                                                             dict(window=1,path='b.mp4',fault='reframe')]))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf.wf3,'env_path',lambda:'')
    monkeypatch.setattr(wf.chain,'wants_music',lambda _:True)
    monkeypatch.setattr(wf.F,'taste_check',lambda *a:dict(ok=True,problems=[]))
    monkeypatch.setattr(wf.F,'audio_scene',lambda *a:{})
    seen=[]
    def build(sc,pack,style,knobs):
        seen.append(knobs);return dict(items=[],dur_f=90),dict(pictures=[])
    monkeypatch.setattr(wf.wf1_film,'build',build)
    wf.compose(SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None),
             dict(title='t',style=dict(id='window'),hook_line='h',close_line='c'), pack, {})
    assert seen[0]['reject']==[dict(window=3,path='a.mp4',fault='contradiction')]
    assert seen[0]['reframe']==[dict(window=1,path='b.mp4',fault='reframe')]
    assert 'drop' not in seen[0]


def test_the_delivered_record_measures_its_cuts_on_the_pixels(tmp_path):
    import shutil,subprocess
    ff=shutil.which('ffmpeg') or next((p for p in ['/Users/masaoodi/Documents/Valmera/.valmera/week-1106/gauntlet/tools/ff/ffmpeg']
                                       if os.path.exists(p)),None)
    if not ff:pytest.skip('no ffmpeg')
    film=tmp_path/'film.mp4'
    subprocess.run([ff,'-v','error','-f','lavfi','-i','color=c=red:size=160x284:rate=30:d=2','-f','lavfi','-i',
                    'color=c=blue:size=160x284:rate=30:d=1','-filter_complex','[0][1]concat=n=2:v=1','-y',str(film)],check=True)
    m=wf.measure_cuts(str(film),ff)
    assert m['cuts']==1 and m['longest_hold_s']==pytest.approx(2.0,abs=.1) and m['seconds']==pytest.approx(3.0,abs=.1)
    assert wf.measure_cuts(str(tmp_path/'missing.mp4'),ff) is None


def test_every_style_asks_the_footage_engine_for_the_full_phone(tmp_path,monkeypatch):
    """Round 1, 19 Nov: window and prints asked for their card size (1080x810, 1080x1080), so every 16:9 clip arrived
    1080x608 and needed 3.16x to fill the phone: each one fell into a window card."""
    pack=dict(coverage=dict(contract=3,windows=[dict(name='Campus',must_show='Actual campus',say='Campus',t0=0,t1=4)]),
              referent_research={'sources':[]},research_revision=7,footage=[],pictures={},credits=[],
              coverage_context_rescout_pending=True)
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    import wf1_documents
    monkeypatch.setattr(wf1_documents,'acquire',lambda *a,**kw:dict(pictures={},credits=[],spend=[]))
    calls=[]
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**kw:calls.append(kw) or dict(lines=[],credits=[]))
    for style in wf.wf1_film.STYLES:
        wf.refill(SimpleNamespace(supply=str(tmp_path),log=lambda _:None),dict(title='Campus',style={'id':style}),pack,
                  dict(weak_windows=[0],real_seconds=4,moving_seconds=4,required_moving_seconds=1))
        pack['coverage_context_rescout_pending']=True
    assert len(calls)==len(wf.wf1_film.STYLES)
    assert all((kw['W'],kw['H'])==(1080,1920)==wf.wf1_film.FOOTAGE_FRAME for kw in calls)
    import inspect
    assert 'style["footage"]' not in inspect.getsource(wf.supply)


def test_all_findings_on_one_render_share_one_rescore(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda _:None)
    repaired=set()
    scores=[]
    film=dict(film='programme.mp4')
    monkeypatch.setattr(wf.wf3,'render',lambda *args:film)
    def review(*args):
        return dict(rejected=int('placement' not in repaired),moving_share=1. if 'motion' in repaired else .4,
                    placements=[],spend=[])
    monkeypatch.setattr(wf,'_render_review',review)
    def repair(name):
        def apply(*args,**kwargs):
            if name in repaired:return False
            repaired.add(name)
            if not kwargs.get('defer'):scores.append(set(repaired))
            return True
        return apply
    for step,name in (('_rebuild_rejected','placement'),('_rebuild_motion','motion'),('_rebuild_holds','hold')):
        monkeypatch.setattr(wf,step,repair(name))
    monkeypatch.setattr(wf,'_rescore',lambda *args:scores.append(set(repaired)),raising=False)
    wf.render(ctx,{},dict(beats={'pictures':[]}))
    assert scores==[{'placement','motion','hold'}]


def test_revised_words_try_retained_moving_footage_before_more_acquisition(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    pack=dict(motion_recovered=True,composed_stills=[],coverage=dict(windows=[dict(first=20,last=31)],
              boundary_reviews=[dict(updates=[dict(i=0)])]),footage=[dict(i=0,clips=[dict(path="gesture",motion_p90=1.6)])])
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3,"load_pack",lambda _:pack)
    sc=dict(beats=dict(pictures=[dict(path="slide",window=0)]))
    report=dict(moving_share=.25,placements=[dict(id=0,ok=True,motion_ok=False)])
    assert wf._rebuild_motion(ctx,{},sc,report,defer=True)
    assert pack["coverage"]["motion_windows"]==[0] and not sc["beats"].get("research_windows")
    assert not wf._rebuild_motion(ctx,{},sc,report,defer=True)


def test_changed_window_rechecks_candidate_rejected_on_earlier_words(tmp_path):
    from placement_check import CONTRACT
    (tmp_path/"render_rejected_contract.json").write_text(json.dumps(CONTRACT))
    rows=[dict(window=0,path="lecture",fault="contradiction",f=[185,220],clause_t=[6.,14.]),
          dict(window=0,path="slide",fault="reframe",f=[315,335],clause_t=[6.,14.])]
    (tmp_path/"render_rejected.json").write_text(json.dumps(rows))
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=dict(coverage=dict(windows=[dict(t0=9.34,t1=14.)])))))
    assert wf.current_rejections(str(tmp_path))==[rows[1]]


def test_full_source_archive_checks_its_spoken_boundary_before_trying_another_crop(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda msg:None)
    notes=[]
    monkeypatch.setattr(wf,"_repair_window_boundaries",lambda ctx,rows:notes.extend(rows))
    finding=dict(id=0,ok=False,f=[343,372],fault="reframe",reason="The source itself ends halfway through its diagram")
    pic=dict(path="berkeley.mp4",source="uc",window=0,f=[343,372],mode="window",source_crop=[0,0,1,1],clause_t=[9.34,13.34])
    wf._rebuild_rejected(ctx,{},dict(beats=dict(pictures=[pic])),dict(placements=[finding]),defer=True)
    assert notes == [finding]
    assert json.loads((tmp_path/"render_rejected.json").read_text())[0]["fault"]=="contradiction"


def test_m09_final_mix_uses_composed_picture_and_repairs_resumed_audio(tmp_path, monkeypatch):
    from score import sound
    from types import SimpleNamespace
    cue = dict(f=347, role="contrast", beat=4, sound="impact", why="Explorer to underbody",
               partner=dict(before="explorer",after="chassis"))
    picture = dict(f=347,kind="picture",id="chassis",before="explorer",after="chassis")
    stale = dict(id="accent0",s="mx_hit_2148",picture_event=picture,event_f=347)
    base = dict(dur_f=400,fps=30,items=[dict(k="shot",pic=key,id=key,f=span)
               for key,span in [("explorer",[0,347]),("chassis",[347,400])]],
               story_cues=[cue],picture_bound_sound=True,sound=[stale],sound_design=dict(picture_bindings=[picture]))
    assert sound.story_partners(base) == [picture]
    src, audio, drawn = (tmp_path/name for name in ("l1.json","audio.json","composed.json"))
    src.write_text(json.dumps(base))
    audio.write_text(json.dumps(dict(base, spine=["interview"], music_marks={"payoff_s":21})))
    drawn.write_text(json.dumps(dict(base,items=[dict(k="shot",id="host",pic="main",f=[0,400])])))
    monkeypatch.setattr(wf.chain,"lay_devices",lambda ctx,path:str(drawn))
    monkeypatch.setattr(wf.wf3,"load_pack",lambda ctx:{})
    def replan(doc,pack):
        assert sound.story_partners(doc) == [None]
        return [],dict(placed=[],picture_bindings=[None])
    monkeypatch.setattr(wf.F,"replan_sound",replan)
    sc = dict(l1=str(src), l1_audio=str(audio), beats={})
    wf.prepare_picture_mix(SimpleNamespace(),sc)
    assert sc["composed_l1"] == str(drawn)
    for path in (src,audio,drawn):
        doc = json.loads(path.read_text())
        assert doc["sound"] == [] and doc["sound_design"] == sc["beats"]["sound_design"]
    assert json.loads(audio.read_text())["spine"] == ["interview"]
    assert json.loads(audio.read_text())["music_marks"] == {"payoff_s":21}


def test_no_new_source_after_hold_research_reuses_the_reviewed_render(tmp_path,monkeypatch):
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path))
    pack = dict(coverage=dict(windows=[dict(name="place",t0=0,t1=1)]))
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3, "render", lambda *args: dict(film="copy.mp4"))
    monkeypatch.setattr(wf, "_render_review", lambda *args: dict(placements=[],rejected=0,moving_share=1.))
    def research(ctx, script, sc, rd, defer):
        sc["beats"]["research_windows"] = [0]
        return True
    monkeypatch.setattr(wf, "_rebuild_holds", research)
    monkeypatch.setattr(wf, "refill", lambda ctx,script,pack,shortage: pack.update(coverage_repairs=4))
    monkeypatch.setattr(wf, "_rescore", lambda *args: pytest.fail("research changed only its receipt"))
    rd = wf.render(ctx, {}, dict(beats=dict(pictures=[])))
    assert len(rd["picture_review"]) == 1


def test_renamed_source_carries_the_actual_crop_finding(tmp_path):
    ctx = SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda _:None)
    pictures = [dict(path=str(tmp_path/"scouted.mp4"),window=0,source="publisher/original",
                     source_crop=[.3,0,.7,1],f=[64,87])]
    (tmp_path/"scouted.mp4").write_bytes(b"source frames")
    report = dict(placements=[dict(id=0,ok=False,fault="reframe",framing_key="framing",
                                   reason="File list cuts off which source file the agent changed.")])
    assert wf._rebuild_rejected(ctx,{},dict(beats=dict(pictures=pictures)),report,defer=True)
    (tmp_path/"rescout.mp4").write_bytes(b"source frames")
    pictures[0].update(path=str(tmp_path/"rescout.mp4"),mode="source-insert")
    assert wf._rebuild_rejected(ctx,{},dict(beats=dict(pictures=pictures)),report,defer=True)
    attempts = wf.current_rejections(str(tmp_path))
    assert [a["fault"] for a in attempts] == ["reframe","contradiction"]
    assert all(a["reason"] == report["placements"][0]["reason"] and a["framing_key"] == "framing" for a in attempts)


def test_only_identical_unwidened_composed_findings_recover_the_original_crop(tmp_path):
    from placement_check import CONTRACT
    original = dict(window=2, path="coding.mp4", f=[331,388], origin_sha256="original",
                    source="publisher", clause_t=[10.92,12.82], source_crop=[.11,.06,.69,.70],
                    source_slice=[.02,1.85], framing_key="same", fault="reframe", review_key="first")
    repeated = dict(original, reframed=True, fault="contradiction", review_key="retry")
    genuine = dict(repeated, review_key="different", framing_key="wider")
    (tmp_path/"render_rejected.json").write_text(json.dumps([original,repeated,genuine]))
    (tmp_path/"render_rejected_contract.json").write_text(json.dumps(CONTRACT))
    rows = [dict(key=k, fault="reframe", ok=False) for k in ("first","retry","different")]
    (tmp_path/"render_reviews.json").write_text(json.dumps([dict(contract=CONTRACT, placements=rows)]))
    found = wf.current_rejections(str(tmp_path))
    assert [r["review_key"] for r in found] == ["first","different"]
