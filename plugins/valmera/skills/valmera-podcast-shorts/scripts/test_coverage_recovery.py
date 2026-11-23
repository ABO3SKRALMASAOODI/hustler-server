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


def test_a_shown_interval_is_never_placed_again():
    """19 Nov m10: vnGC4YS36gU 139.82-141.42 s was placed three times. A window whose candidates were all shown takes
    nothing without a kept original to cut a fresh interval from (the ladder and the gap fill take the slot)."""
    a=dict(path='/a.mp4',seconds=1.6);b=dict(path='/b.mp4',seconds=1.6)
    seen=[]
    def check(rows):
        seen.extend(r[4]['window'] for r in rows)
        for r in rows:r[4].update(verified_motion=True,placed_motion_p90=2.)
        decisions=[dict(ok=True,motion_ok=True) for r in rows]
        return rows,dict(placements=decisions,rejected=0,spend=[])
    accepted,report=coverage.checked_proposals(repeated_subject(),{0:[a],1:[b]}, {},3,check)
    assert 2 not in seen and [r[4]['path'] for r in accepted]==['/a.mp4','/b.mp4'] and report['rejected_paths']==[]


def test_repeated_candidate_does_not_inflate_new_picture_density():
    shots=coverage.proposals(repeated_subject(),{0:[dict(path='/a.mp4',seconds=1.6)],1:[dict(path='/b.mp4',seconds=1.6)]},{})
    with pytest.raises(coverage.CoverageError) as error:
        coverage.require_coverage(shots,repeated_subject(),3)
    assert error.value.report['new_pictures']==2
    assert error.value.report['repeated_pictures']==0 and error.value.report['missing_windows']==[2]
    assert error.value.report['moving_seconds']==0


FF='/Users/masaoodi/Documents/Valmera/.valmera/week-1106/gauntlet/tools/ff/ffmpeg'
WORKER=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','..','..','worker'))


@pytest.fixture
def worker_path(monkeypatch):
    """The worker beside this plugin on the path (footage.fetch, footage.motion), for the tests that reach it."""
    monkeypatch.syspath_prepend(WORKER)


@pytest.mark.skipif(not os.path.exists(FF),reason='needs the gauntlet ffmpeg')
def test_a_later_reference_takes_a_fresh_interval_of_the_kept_original(tmp_path,worker_path):
    import subprocess
    original=tmp_path/'sources'/'original.mp4';original.parent.mkdir()
    subprocess.run([FF,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=12','-c:v','libx264',
                    '-pix_fmt','yuv420p',str(original)],check=True)
    url='https://www.youtube.com/watch?v=vnGC4YS36gU'
    (tmp_path/'sources'/'manifest.json').write_text(json.dumps([dict(url=url,path=str(original))]))
    clip=tmp_path/'clip.mp4'
    subprocess.run([FF,'-v','error','-y','-ss','4','-i',str(original),'-t','1.6','-c:v','libx264','-pix_fmt','yuv420p',str(clip)],check=True)
    a=dict(path=str(clip),seconds=1.6,source=dict(url=url,title='Servers',channel='Meta'),time_range=[4.0,5.6],w=320,h=180)
    b=dict(path='/b.mp4',seconds=1.6)
    originals=coverage.originals_of(str(tmp_path))
    assert originals=={url:str(original)}
    shots=coverage.proposals(repeated_subject(),{0:[a],1:[b]},{},originals=originals,cut_dir=str(tmp_path/'fresh'),ffmpeg=FF)
    later=[s for s in shots if s[4]['window']==2]
    assert len(later)==1 and later[0][4]['fresh_of']==str(clip) and os.path.isfile(later[0][4]['path'])
    lo,hi=later[0][4]['time_range']
    assert lo>=5.6 or hi<=4.0                                                      # never the interval already shown
    assert abs((hi-lo)-later[0][4]['seconds'])<0.1 and os.path.isfile(later[0][4]['still'])
    report=coverage.require_coverage([s for s in shots],repeated_subject(),3,final=True)
    assert report['repeated_pictures']==0 and report['missing_windows']==[] and report['new_pictures']==3
    # an interval this window's check turned down is not cut again: the next one beside it is
    again=coverage.proposals(repeated_subject(),{0:[a],1:[b]},{},rejected={(2,later[0][4]['path'])},
                             originals=originals,cut_dir=str(tmp_path/'fresh'),ffmpeg=FF)
    other=[s for s in again if s[4]['window']==2]
    assert other and other[0][4]['path']!=later[0][4]['path']


def test_the_plan_starts_from_the_scripts_briefs_and_gives_each_window_a_ladder(tmp_path):
    words=[dict(w=w,start=i,end=i+.8) for i,w in enumerate('Alex called me and I joined his livestream'.split())]
    named=[dict(name='Alexandr Wang',**{'is':'person'},must_show='Alexandr Wang of Scale AI at an event'),
           dict(name='Mark Zuckerberg Facebook Live',**{'is':'event'},must_show='Zuckerberg hosting a Facebook Live stream')]
    answer={'entities':[dict(name='Alexandr Wang',word_ids=[0])],
            'windows':[dict(first=0,last=3,name='Alexandr Wang',external=True,must_show='Alexandr Wang at Scale AI',
                            depictions=[dict(name='Scale AI',must_show='Scale AI offices',video_searches=['Scale AI office tour']),
                                        dict(name='junk'),dict(name='AI data labelling',must_show='people labelling data'),
                                        dict(name='a',must_show='b'),dict(name='c',must_show='d')]),
                       dict(first=5,last=7,name='Mark Zuckerberg Facebook Live',external=True,must_show='Zuckerberg live')]}
    asked=[]
    llm=SimpleNamespace(ask=lambda system,content,**k:asked.append((system,json.loads(content))) or (json.dumps(answer),{}),json_block=json.loads)
    out=coverage.plan(words,'Two-day plan',str(tmp_path),llm,named=named)
    system,content=asked[0]
    assert content['named']==[dict(name=n['name'],must_show=n['must_show'],**{'is':n['is']}) for n in named]
    assert "original synchronous interview" in system and "event" in system
    ladder=out['windows'][0]['depictions']
    assert [r['name'] for r in ladder]==['Scale AI','AI data labelling','a'] and ladder[0]['video_searches']==['Scale AI office tour']
    assert out['windows'][0]['rung']==0 and out['windows'][1]['depictions']==[] and out['named']==content['named']
    assert coverage.plan(words,'Two-day plan',str(tmp_path),llm,named=named) is not None and len(asked)==1     # cached
    coverage.plan(words,'Two-day plan',str(tmp_path),llm,named=named[:1])                                     # new briefs: read again
    assert len(asked)==2


def test_the_speaker_s_own_filmed_past_is_invited_and_only_this_take_excluded():
    import wf1_context
    src=open(os.path.join(os.path.dirname(__file__),'wf1_primary_sources.py')).read()
    assert 'Do not return the original podcast speaker' not in src and 'Never return the current interview itself' in src
    assert "speaker's OWN\nearlier filmed appearances" in src
    import wf1_short
    title=wf1_short.footage_title('Watermelon','Speaker: Mark Zuckerberg')
    assert 'never him as he looks today' not in title and 'never show this conversation' in title and 'livestream' in title
    rr=open(os.path.join(os.path.dirname(__file__),'wf1_coverage.py')).read()
    assert 'resolved_referents=coverage.get' in rr and 'current interview take (this conversation' in rr


def test_referent_research_reads_the_scripts_resolved_referents(tmp_path,monkeypatch):
    import wf1_context,writer
    sent=[]
    monkeypatch.setattr(writer,'ask_openai',lambda system,content,**k:sent.append((system,json.loads(content))) or ('{"references":[],"unresolved":[]}',{}))
    llm=SimpleNamespace(openai_key=lambda:None,json_block=json.loads)
    cov=dict(context='Speaker: Mark Zuckerberg',words=[dict(w='Alex',start=0,end=.5)],
             windows=[dict(name='Alex',say='Alex')],named=[dict(name='Alexandr Wang',must_show='Wang at Scale AI',**{'is':'person'})])
    wf1_context.research(cov,str(tmp_path),llm)
    assert sent[0][1]['resolved_referents'][0]['name']=='Alexandr Wang' and 'resolved_referents' in sent[0][0]


def test_reopen_fetches_exactly_the_refused_sources_and_sorts_the_wall_from_the_gone(tmp_path):
    rows=[dict(url=f'https://www.youtube.com/watch?v=v{n}',title=f'T{n}',channel='C',duration=30,subjects=['Lab']) for n in range(4)]
    asked=[]

    def opener(url,dest,duration=None):
        asked.append(url)
        if url.endswith('1'):raise RuntimeError('tv: ERROR: Sign in to confirm you are not a bot')
        if url.endswith('2'):raise RuntimeError('tv: ERROR: Video unavailable. This video is private')
        os.makedirs(dest,exist_ok=True);p=os.path.join(dest,'whole.mp4');open(p,'wb').write(b'x'*3000);return p,'mweb+proxy'
    keep=tmp_path/'sources'
    opened,refused,gone=primary.reopen(rows,str(keep),log=lambda _:None,space_s=0,opener=opener)
    assert asked==[r['url'] for r in rows]
    assert [r['url'] for r in opened]==[rows[0]['url'],rows[3]['url']]
    assert [r['url'] for r in refused]==[rows[1]['url']] and refused[0]['tries']==1
    assert [r['url'] for r in gone]==[rows[2]['url']]
    kept=primary.retained(str(tmp_path))
    assert sorted(r['url'] for r in kept['Lab'])==sorted([rows[0]['url'],rows[3]['url']]) and kept['Lab'][0]['channel']=='C'
    again,_,_=primary.reopen([rows[0]],str(keep),opener=lambda *a:pytest.fail('kept: not fetched again'))
    assert again[0]['url']==rows[0]['url']


# yt-dlp's own words for the bot check, as this Mac's footage fetches met it on 19 Nov, and the Wayback rung's answer
BOT_STDERR=('WARNING: [youtube] o6f0B9oo87w: Some web client https formats have been skipped\n'
            'ERROR: [youtube] o6f0B9oo87w: Sign in to confirm you\u2019re not a bot. Use --cookies-from-browser or --cookies '
            'for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-youtube-dl  '
            'for how to manually pass cookies. Also see  https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies  '
            'for tips on effectively exporting YouTube cookies\n')
WAYBACK_STDERR='ERROR: [web.archive:youtube] o6f0B9oo87w: The requested video is not archived or indexed\n'


def _walled_ladder(monkeypatch):
    """The real fetch.run_ladder with yt-dlp answering the bot check on every YouTube rung and 'not archived' on the
    Wayback rung (no transport, no proxy: this Mac's only route)."""
    from footage import fetch
    for k in ('FOOTAGE_FETCH_CMD','FOOTAGE_LOCAL_SOURCES','FOOTAGE_PACE_DIR','FOOTAGE_WAYBACK'):
        monkeypatch.delenv(k,raising=False)
    calls=[]

    def run(cmd,**kw):
        calls.append(cmd[-1])
        err=WAYBACK_STDERR if cmd[-1].startswith(fetch.WAYBACK) else BOT_STDERR
        return SimpleNamespace(returncode=1,stdout='',stderr=err)
    monkeypatch.setattr(fetch.subprocess,'run',run)
    return calls


def test_a_source_still_behind_the_wall_is_refused_not_gone_through_the_real_ladder(tmp_path,monkeypatch,worker_path):
    """The ladder's error ends with the Wayback rung's answer; the wall is read on the YouTube rungs' part."""
    from footage import fetch
    calls=_walled_ladder(monkeypatch)
    url='https://www.youtube.com/watch?v=o6f0B9oo87w'
    with pytest.raises(fetch.FetchError) as e:
        fetch.run_ladder(url,[])
    assert 'not archived' in str(e.value) and 'sign in to confirm' not in str(e.value)[-240:].lower()   # the tail hides it
    assert primary.wall_error(str(e.value))
    assert not primary.wall_error('tv: ERROR: [youtube] x: Video unavailable. This video has been removed || wayback: timed out')
    calls.clear()
    row=dict(url=url,title='Watermelon',channel='Meta',duration=40,subjects=['Lab'])
    opened,refused,gone=primary.reopen([row],str(tmp_path/'sources'),log=lambda _:None,space_s=0)
    assert calls and calls[-1].startswith(fetch.WAYBACK)                         # the default opener walked the real ladder
    assert opened==[] and gone==[] and [r['url'] for r in refused]==[url]
    assert len(refused[0]['error'])<=260 and 'Sign in to confirm' in refused[0]['error']


def test_exhausted_source_routes_leave_the_synchronous_camera_available(tmp_path,monkeypatch,worker_path):
    import wf1_short as wf
    _walled_ladder(monkeypatch)
    rows=[dict(url=f'https://www.youtube.com/watch?v=video{n:05d}',title=f'Launch {n}',channel='Bloomberg Tech',where='youtube',
               duration=120,subjects=['Lab'],error='default: Sign in to confirm you are not a bot') for n in range(3)]
    pack=dict(platform_walled=True,coverage=dict(contract=3,windows=[dict(name='Lab',must_show='Its launch',say='a lab',t0=0,t1=4)]),
              footage=[],pictures={},credits=[],wall=dict(sources=3,refused=rows))
    (tmp_path/'pack.json').write_text(json.dumps(dict(pack=pack,record=dict(footage=None))))
    monkeypatch.setattr(primary.time,'sleep',lambda s:None)
    monkeypatch.setattr(wf.chain,'footage',lambda *a,**k:pytest.fail('nothing opened: nothing to cut'))
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    wf.recover_wall(ctx,dict(title='Lab',style={'id':'window'}),pack,waits=(0,))
    stored=json.load(open(tmp_path/'pack.json'))['pack']
    assert stored['coverage_exhausted'] and not stored['platform_walled'] and stored['wall']['sources']==3 and len(stored['wall']['refused'])==3


def test_an_operator_transport_that_fails_falls_through_to_every_local_route(tmp_path,monkeypatch,worker_path):
    from footage import fetch
    monkeypatch.setenv('FOOTAGE_FETCH_CMD','operator-fetch')
    monkeypatch.setattr(fetch,'whole_av',lambda *a:(_ for _ in ()).throw(fetch.FetchError('external original fetch failed: walled')))
    seen=[]

    def ladder(url,args,timeout_s=None,done=None):
        seen.append(args)
        p=tmp_path/'d'/'whole.mp4';p.write_bytes(b'x'*3000)
        return '',  'tv+proxy'
    monkeypatch.setattr(fetch,'run_ladder',ladder)
    path,rung=primary._open_exact('https://www.youtube.com/watch?v=x',str(tmp_path/'d'),duration=40)
    assert rung=='tv+proxy' and path.endswith('whole.mp4') and '--download-sections' not in seen[0]
    path,rung=primary._open_exact('https://www.youtube.com/watch?v=y',str(tmp_path/'d'),duration=2400)
    assert rung=='tv+proxy+head' and '--download-sections' in seen[1]               # a long source: its head, not 400 MB


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


def test_composed_findings_repair_boundaries_but_never_rewrite_the_brief(tmp_path,monkeypatch):
    """Round 1, 19 Nov (m04, m05): composed rejections handed to repair_requirements came back as exclusions in
    must_show ('Not a weather instrument...'), and the narrower brief re-judged approved pictures. A composed finding
    may move a boundary; it never reaches the brief, and the media is never edited."""
    import wf1_short as wf
    window=dict(name='Research',first=0,last=3,t0=0,t1=4,say='the history of science',must_show='People')
    pack=dict(coverage=dict(words=[],windows=[window]),footage=[dict(i=0,clips=[dict(path='unchanged.mp4')])])
    path=tmp_path/'pack.json';path.write_text(json.dumps(dict(pack=pack,record={})))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:json.loads(path.read_text())['pack'])
    monkeypatch.setattr(wf,'_llm',lambda:None)
    boundaries=[]
    monkeypatch.setattr(coverage,'repair_boundaries',lambda data,rejected,llm:boundaries.append(rejected) or False)
    monkeypatch.setattr(coverage,'repair_requirements',lambda *a:pytest.fail('a composed finding reached the brief'))
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    rejection=[dict(f=[3,33],reason='Unrelated activity',source='https://publisher.example/film')]
    wf._repair_window_boundaries(ctx,rejection)
    saved=json.loads(path.read_text())['pack']
    assert boundaries==[rejection]
    assert saved['footage']==pack['footage']
    assert saved['coverage']['windows'][0]['must_show']=='People' and 'composed_requirement_windows' not in saved


def test_a_boundary_answer_that_cannot_be_applied_leaves_the_film_going(tmp_path,monkeypatch):
    import wf1_short as wf
    pack=dict(coverage=dict(words=[],windows=[dict(name='A',t0=0,t1=1,say='a',must_show='a')]),footage=[])
    path=tmp_path/'pack.json';path.write_text(json.dumps(dict(pack=pack,record={})))
    monkeypatch.setattr(wf.wf3,'load_pack',lambda _:json.loads(path.read_text())['pack'])
    monkeypatch.setattr(wf,'_llm',lambda:None)
    def invalid(*a):raise RuntimeError('invalid documentary boundary repair')
    monkeypatch.setattr(coverage,'repair_boundaries',invalid)
    logged=[]
    wf._repair_window_boundaries(SimpleNamespace(supply=str(tmp_path),log=logged.append),[dict(f=[0,30],reason='x')])
    assert logged and 'not applied' in logged[0]


def test_the_refill_brief_never_carries_rejection_reasons(tmp_path,monkeypatch):
    """19 Nov: the last 60 rejection reasons went to the director as 'research', and its brief excluded what the
    checker had objected to. The refill sends what was found, never why a check said no."""
    import wf1_short as wf
    (tmp_path/'placement_checks.json').write_text(json.dumps({'k':dict(ok=False,reason='no readable attribution')}))
    pack=dict(coverage=dict(contract=3,windows=[dict(name='A',must_show='A',say='a',t0=0,t1=2)]),
              referent_research={'sources':[]},footage=[dict(i=0,clips=[dict(subject='A',shows='a stage',source={'url':'u'})])],
              pictures={},credits=[])
    (tmp_path/'pack.json').write_text(json.dumps({'pack':pack}))
    monkeypatch.setattr(wf,'_llm',lambda:None)
    seen=[]
    def revise(data,wanted,evidence,llm):
        seen.append(evidence);return {}
    monkeypatch.setattr(coverage,'repair_requirements',revise)
    monkeypatch.setattr(wf,'motion_research',lambda *a:True)
    wf.refill(SimpleNamespace(supply=str(tmp_path),log=lambda _:None),dict(title='A',style={'id':'window'}),pack,
              dict(weak_windows=[0],real_seconds=2,moving_seconds=0))
    assert seen and 'rejected' not in seen[0] and 'no readable attribution' not in json.dumps(seen[0])


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


def test_the_final_build_returns_the_shortfall_instead_of_losing_the_short():
    """Owner, 16 Nov 2026: a fix reaches the outcome. While recovery passes remain a shortfall goes back to research;
    the final build is made from every approved picture and the record says what was short."""
    shots=coverage.proposals(repeated_subject(),{0:[dict(path='/a.mp4',seconds=1.6)],1:[dict(path='/b.mp4',seconds=1.6)]},{})
    with pytest.raises(coverage.CoverageError):
        coverage.require_coverage(shots,repeated_subject(),3)
    report=coverage.require_coverage(shots,repeated_subject(),3,final=True)
    assert report['short'] is True and report['moving_seconds']==0 and report['new_pictures']==2


def test_a_plan_with_a_correction_still_open_after_three_readings_keeps_its_valid_windows(tmp_path):
    words=[dict(w=w,start=i,end=i+.8) for i,w in enumerate('Nvidia built the chips in Taiwan for years'.split())]
    plan={'entities':[dict(name='TSMC',word_ids=[5])],
          'windows':[dict(first=0,last=3,name='Nvidia',external=True,must_show='Nvidia chips'),
                     dict(first=2,last=4,name='overlap',external=True,must_show='x')]}
    asked=[]
    llm=SimpleNamespace(ask=lambda *a,**k:asked.append(1) or (json.dumps(plan),{}),json_block=json.loads)
    out=coverage.plan(words,'Chips',str(tmp_path),llm)
    assert len(asked)==3 and [w['name'] for w in out['windows']]==['Nvidia'] and out['plan_problems']


def test_speaker_holds_the_director_cannot_break_are_recorded_not_fatal(tmp_path):
    data=dict(words=[dict(w=str(i),start=i,end=i+.8) for i in range(12)],moves=[])
    llm=SimpleNamespace(ask=lambda *a,**k:('{"moves":[]}',{}),json_block=json.loads)
    moves,_=coverage.speaker_moves(data,[],12*30,[],str(tmp_path),llm=llm)
    assert moves==[] and data['unresolved_holds']==[(0,360)]


@pytest.mark.skipif(not os.path.exists(FF),reason='needs the gauntlet ffmpeg')
def test_when_found_pictures_run_out_the_gap_fill_cuts_fresh_intervals_of_kept_originals(tmp_path,worker_path):
    """19 Nov m10: 22 s with no cutaway. The film's spares were used up; the kept original of a verified clip still has
    seconds never shown beside the verified interval. They fill the hole, each checked on its words."""
    import subprocess,wf1_film
    original=tmp_path/'sources'/'servers.mp4';original.parent.mkdir()
    subprocess.run([FF,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=30','-c:v','libx264',
                    '-pix_fmt','yuv420p',str(original)],check=True)
    url='https://www.youtube.com/watch?v=vnGC4YS36gU'
    (tmp_path/'sources'/'manifest.json').write_text(json.dumps([dict(url=url,path=str(original))]))
    shown=dict(path=str(tmp_path/'clip.mp4'),seconds=1.6,subject='Servers',source=dict(url=url),time_range=[12.0,13.6],w=320,h=180)
    FPS=wf1_film.FPS
    cov=dict(windows=[dict(name='Servers',must_show='Meta data-centre servers',t0=1.0,t1=15.0)])
    ins=[[wf1_film.LEAD_F+FPS,wf1_film.LEAD_F+3*FPS,'clip','Servers',dict(shown,window=0,src=[.1,1.5])]]
    first,last=wf1_film.LEAD_F+FPS,ins[0][1]+12*FPS
    checked=[]
    def checker(rows):
        checked.extend(rows)
        return rows,dict(placements=[dict(ok=True) for _ in rows],spend=[])
    out,rep=wf1_film.fill_holes(ins,cov,{0:[shown]},{},dict(segments=[dict(say='a',pictures=['Servers'])]),[0],first,last,[],
                                checker,originals=coverage.originals_of(str(tmp_path)),cut_dir=str(tmp_path/'fresh'),ffmpeg=FF)
    added=[x for x in out if x[4].get('gap_fill')]
    assert rep['fresh']>=3 and len(added)==len(checked)>=3
    ranges=[tuple(x[4]['time_range']) for x in out]
    assert all(min(b,d)-max(a,c)<=0 for i,(a,b) in enumerate(ranges) for (c,d) in ranges[i+1:])   # no interval shown twice
    edges=[first]+[f for x in out for f in (x[0],x[1])]+[last]
    assert max(b-a for a,b in zip(edges[0::2],edges[1::2]))<=wf1_film.FILL_GAP_S*FPS


def test_same_subject_in_different_roles_keeps_its_own_words():
    rows = [dict(first=i, last=i, t0=i, t1=i+1, say=words, name="Investors", must_show=need)
            for i, (words, need) in enumerate([("raised money", "Scale's completed round"),
                                                ("passed on us", "documented rejection of Scale")])]
    assert coverage.join_windows(rows) == rows


def test_resume_keeps_an_applied_word_boundary_for_the_same_picture_finding():
    import wf1_coverage as coverage
    from types import SimpleNamespace
    calls=[]
    llm=SimpleNamespace(ask=lambda *a,**k:(calls.append(1),{}),json_block=lambda _:dict(updates=[dict(i=0,first=1,last=2,why="named lab begins here")]))
    words=[dict(w=w,start=i,end=i+.9) for i,w in enumerate(["recalled","the","lab"])]
    data=dict(words=words,windows=[dict(name="lab",first=0,last=2,t0=0,t1=2.9)])
    row=dict(f=[3,25],reason="other presenter under the recollection")
    assert coverage.repair_boundaries(data,[row],llm)
    assert not coverage.repair_boundaries(data,[row],llm)
    assert calls==[1]
