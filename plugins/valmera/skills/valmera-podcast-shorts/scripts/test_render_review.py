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
    monkeypatch.setattr(wf,'score',lambda *a:dict(beats=dict(pictures=[],caption_lines=4,caption_contract=1),spend=[]))
    def render(ctx,script,score):
        assert score['beats']['caption_contract']==1
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


def test_a_short_spends_at_most_its_recovery_passes_then_is_built_from_every_approved_picture(tmp_path,monkeypatch):
    """15-16 Nov: up to seven research passes a short, and a short lost when they ran out. Now RECOVERY_PASSES, counted
    across rebuilds, then the final build."""
    import wf1_coverage,wf1_alignment
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[]),alignment_contract=wf1_alignment.CONTRACT,
              platform_walled=True,footage=[],lines=[],blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg')
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
    l1=tmp_path/'l1.json';l1.write_text(json.dumps(dict(items=[dict(k='word',role='big',f=[10,20],t='SUN')])))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda ctx:dict(lines=[dict(text='the Sun')],words=[],ffmpeg='ffmpeg'))
    monkeypatch.setattr(wf.F,'Clock',lambda words,script:None)
    passes=[]
    def check(rows,script,clock,work,ffmpeg,review='placement',**kw):
        passes.append((review,len(rows)))
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


def test_score_hands_the_findings_to_the_build_by_window(tmp_path,monkeypatch):
    import wf1_coverage,wf1_alignment
    from placement_check import CONTRACT
    pack=dict(coverage=dict(contract=wf1_coverage.CONTRACT,windows=[]),alignment_contract=wf1_alignment.CONTRACT,
              platform_walled=True,footage=[],lines=[],blank_frame_contract=1,ffmpeg='/usr/bin/ffmpeg')
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack)))
    (tmp_path/'render_rejected_contract.json').write_text(json.dumps(CONTRACT))
    (tmp_path/'render_rejected.json').write_text(json.dumps([dict(window=3,path='a.mp4',fault='contradiction'),
                                                             dict(window=1,path='b.mp4',fault='reframe')]))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:pack)
    monkeypatch.setattr(wf.wf3,'env_path',lambda:'')
    monkeypatch.setattr(wf,'direction_of',lambda *a:dict())
    monkeypatch.setattr(wf.chain,'wants_music',lambda _:True)
    monkeypatch.setattr(wf.F,'taste_check',lambda *a:dict(ok=True,problems=[]))
    monkeypatch.setattr(wf.F,'audio_scene',lambda *a:{})
    seen=[]
    def build(sc,pack,style,knobs):
        seen.append(knobs);return dict(items=[],dur_f=90),dict(pictures=[])
    monkeypatch.setattr(wf.wf1_film,'build',build)
    wf.score(SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),args={},log=lambda x:None),
             dict(title='t',style=dict(id='window'),hook_line='h',close_line='c'))
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
