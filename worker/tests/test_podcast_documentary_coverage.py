"""Regressions from the independent WF1 round-four teardown."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest

from scene import pace
from score import audibility
from score import music

path = Path(__file__).resolve().parents[2]/'plugins/valmera/skills/valmera-podcast-shorts/scripts/wf1_coverage.py'
spec = importlib.util.spec_from_file_location('wf1_coverage_test',path)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def test_empty_picture_audit_cannot_pass_coverage():
    coverage = {'windows':[{'t0':3.,'t1':10.}]}
    with pytest.raises(RuntimeError,match='documentary supply incomplete'):
        C.require_coverage([],coverage,12)


def test_research_budget_reaches_later_subjects_before_repeating_first():
    from footage.run import source_rounds
    ranked = [(subject, {'id':f'{subject}-{i}'}, 'checked') for subject in range(6) for i in range(5)]
    opened = source_rounds(ranked)[:16]
    assert {row[0] for row in opened} == set(range(6))
    assert max(sum(row[0]==s for row in opened) for s in range(6)) <= 3


def test_preliminary_motion_scans_only_the_actual_scout_candidates(monkeypatch):
    from footage import run
    seen=[]
    monkeypatch.setattr(run.look,'pick_times',lambda *a,**k:[1.,7.,8.])
    monkeypatch.setattr(run.motion,'of_shots',lambda proxy,spans,**k:seen.extend(spans) or {})
    source=dict(proxy='source.mp4',shots=[(0,3),(3,6),(6,9),(9,100)])
    times,_=run.motion_at_candidates(source,[])
    assert times==[1.,7.,8.] and seen==[(0,3),(6,9)]


def test_documentary_context_reaches_frame_checker_not_only_search_planner():
    from footage.look import _listing,has_brief
    lines=[dict(i=2,say='At Stanford in 2005',visual='Campus illustrates the setting, not a recording of this conversation.')]
    assert lines[0]['visual'] in _listing(lines)
    # Contextual depiction is not an explicit claim that this records the exact historical event.
    assert not has_brief(lines)
    assert has_brief([dict(lines[0],see=[dict(thing='Stanford',as_of='2005')])])


def test_contextual_admission_does_not_require_archival_claim_fields(tmp_path,monkeypatch):
    from footage import look
    import json
    monkeypatch.setattr(look,'sheet',lambda *a,**k:'sheet.jpg')
    monkeypatch.setattr(look.ask,'image_block',lambda *a:{})
    monkeypatch.setattr(look.ask,'ask',lambda *a,**k:(json.dumps(dict(cells=[dict(n=1,ok=True,clean=True,grade=4,lines=[0],shows='Stanford campus')])),0.))
    subject=dict(name='Stanford',must_show='Actual campus exterior')
    lines=[dict(i=0,say='I studied at Stanford',visual='Campus setting, not the actual conversation.')]
    candidates=[dict(src=0,t=1.,jpg='frame.jpg')];sources=[dict(title='Campus tour',channel='Stanford')]
    accepted,_=look.check(subject,lines,candidates,sources,str(tmp_path))
    assert accepted[0]['lines']==[0]
    exact=[dict(lines[0],see=[dict(thing='Stanford',as_of='2005')])]
    checked,_=look.check(subject,exact,candidates,sources,str(tmp_path))
    assert checked[0]['lines']==[]  # an archival claim still needs all three explicit checks


def test_research_clauses_join_only_identical_director_subjects():
    rows = [dict(name=n,first=i,last=i,t0=i,t1=i+1,say=str(i),must_show=n)
            for i,n in enumerate(['Google','Google','Facebook','Google'])]
    joined = C.join_windows(rows)
    assert [(r['name'],r['first'],r['last']) for r in joined] == [('Google',0,1),('Facebook',2,2),('Google',3,3)]


def test_static_video_files_do_not_satisfy_moving_coverage():
    coverage = {'windows':[{'t0':0.,'t1':10.}]}
    rows = [[i*30,(i+1)*30,'clip','subject',dict(path=f'static-{i}.mp4',window=0,placed_motion_p90=0.)] for i in range(8)]
    with pytest.raises(RuntimeError,match='moving=0.000'):
        C.require_coverage(rows,coverage,10)
    for row in rows: row[4]['placed_motion_p90']=2.
    # Moving a card or cursor can cross the flow threshold without depicting action.
    with pytest.raises(RuntimeError,match='moving=0.000'):
        C.require_coverage(rows,coverage,10)
    for row in rows: row[4]['verified_motion']=True
    assert C.require_coverage(rows,coverage,10)['moving_seconds']==8


def test_picture_starts_at_its_word_window_not_sentence_start():
    coverage = {'windows':[{'t0':6.3,'t1':8.,'name':'Facebook','must_show':'actual social network'}]}
    footage = {0:[{'path':f'new-shot-{i}', 'seconds':2.,'subject':'Facebook'} for i in range(3)]}
    rows = C.proposals(coverage,footage,{})
    assert rows[0][0] == 192
    assert all(192 <= r[0] < r[1] <= 243 for r in rows)
    assert len({r[4]['path'] for r in rows})==len(rows)


def test_source_rejection_is_confined_to_the_contradictory_passage():
    import json
    class LLM:
        ask=staticmethod(lambda *a,**k:(json.dumps(dict(updates=[],blocked_sources=[dict(window=0,source='later-interview',why='The entire source is a later event.')])),{}))
        json_block=staticmethod(json.loads)
    words=[dict(w='earlier',start=0.,end=1.),dict(w='today',start=2.,end=3.)]
    cov=dict(words=words,windows=[dict(name='Institution',must_show='The institution in its stated context',first=i,last=i,t0=w['start'],t1=w['end']) for i,w in enumerate(words)])
    rejected=[dict(f=[3,33],source='later-interview',reason='Later event under an earlier offer')]
    assert C.repair_boundaries(cov,rejected,LLM())
    footage={i:[dict(path=f'clip-{i}',seconds=1.,source=dict(url='later-interview'))] for i in range(2)}
    rows=C.proposals(cov,footage,{})
    assert len(rows)==1 and rows[0][4]['window']==1
    pages={'Institution':[dict(path='later-page',page='later-interview')]}
    stills=C.proposals(cov,{},pages)
    assert len(stills)==1 and stills[0][4]['window']==1
    assert cov['windows'][0]['first']==0


def test_source_rejection_cannot_invent_evidence():
    import json
    class LLM:
        ask=staticmethod(lambda *a,**k:(json.dumps(dict(updates=[],blocked_sources=[dict(window=0,source='unseen',why='unsupported')])),{}))
        json_block=staticmethod(json.loads)
    cov=dict(words=[dict(w='place',start=0.,end=1.)],windows=[dict(name='Place',first=0,last=0,t0=0.,t1=1.)])
    with pytest.raises(RuntimeError,match='matching rejected placement'):
        C.repair_boundaries(cov,[dict(f=[3,33],source='actual',reason='Local face crop')],LLM())


def test_speaker_camera_moves_only_at_directed_words():
    shots = [dict(id='s',f=[0,300],pic='main',pw=1920,ph=1080,src=[0,10],faces=[[.4,.2,.12,.35]])]
    stress = [(i,.9) for i in range(10,300,10)]
    a, log = pace.drive(shots,stress,family='podcast',band={'motivated_frames':[]})
    assert len(a)==1 and log['eased']==0 and log['punches']==0
    b, log = pace.drive(shots,stress,family='podcast',band={'motivated_frames':[150]})
    assert len(b)==1 and log['eased']==1 and log['punches']==0
    crop = np.array(b[0]['crop'])
    assert np.max(np.abs(np.diff(crop,axis=0))) < .02


def test_spectral_or_planned_presence_cannot_count_a_masked_hit():
    sr=48000; t=np.arange(sr*4)/sr
    voice=.1*np.sin(2*np.pi*220*t);bed=.05*np.sin(2*np.pi*110*t)
    hit=np.zeros_like(voice);at=sr*2;hit[at:at+4800]=.002*np.sin(2*np.pi*1200*t[:4800])*np.hanning(4800)
    assert audibility.measure(voice,hit,bed)['T2']==0
    hit*=200
    assert audibility.measure(voice,hit,bed)['T2']>=1
    calibrated,_=audibility.calibrate_bed(voice,bed)
    assert abs(audibility.measure(voice,hit,calibrated)['bed_under_voice_db']-5.5)<.1


def test_encoded_ruler_recovers_independent_stem_gains_and_codec_delay():
    rng=np.random.default_rng(71)
    stems=rng.normal(0,.01,(3,48000))
    coefficients=np.array([2.4,1.1,1.8])
    encoded=np.pad(coefficients@stems,(144,0))
    result=audibility.verified_mix(*stems,encoded)
    assert result['lag_ms']==3
    assert np.allclose(result['gains'],coefficients,atol=1e-8)
    assert result['fit_residual_db'] < -100


def test_podcast_payoff_hole_survives_half_second_measurement():
    p=music.profile_for('podcast')
    assert 1 <= p['hole_min_s'] <= p['hole_max_s'] <= 2.3
    assert 10 <= p['hole_db'] <= 24
    assert p['broadband_bed_db']==5.5


def test_primary_document_reader_rejects_private_urls(monkeypatch):
    import socket
    spec=importlib.util.spec_from_file_location('wf1_documents_test',path.with_name('wf1_documents.py'))
    docs=importlib.util.module_from_spec(spec);spec.loader.exec_module(docs)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='non-public'):
        docs.public_url('https://example.org/paper.pdf')
    for url in ('http://example.org/a','https://user:password@example.org/a','https://example.org:8443/a'):
        with pytest.raises(ValueError,match='public HTTPS'):docs.public_url(url)


def test_primary_document_pixels_remain_stills_and_canonical_subject_is_available(tmp_path):
    from PIL import Image
    import io
    spec=importlib.util.spec_from_file_location('wf1_documents_test',path.with_name('wf1_documents.py'))
    docs=importlib.util.module_from_spec(spec);spec.loader.exec_module(docs)
    buf=io.BytesIO();Image.new('RGB',(800,600),'white').save(buf,format='PNG')
    assert docs.pages(buf.getvalue())[0].size==(800,600)
    coverage={'windows':[dict(t0=0.,t1=2.,name='Solar System',subjects=['NASA visualization'],must_show='Actual planetary system')]}
    rows=C.proposals(coverage,{}, {'Solar System':[dict(path='actual-document.jpg')]})
    assert len(rows)==1 and rows[0][2]=='photo'
    with pytest.raises(C.CoverageError,match='moving=0.000'):
        C.require_coverage(rows,coverage,2.)


def test_research_revision_preserves_named_subject_and_word_boundaries():
    import json
    class LLM:
        @staticmethod
        def ask(*a,**k):
            return json.dumps(dict(windows=[dict(i=0,name='Different company',must_show='wrong'),dict(i=1,name='Actual company',must_show='Its visibly identified premises OR product interface',first=999,last=999,video_searches=['actual company official tour'])])),{}
        json_block=staticmethod(json.loads)
    coverage=dict(windows=[dict(name='First',first=0,last=4,t0=0.,t1=2.),dict(name='Actual company',first=5,last=9,t0=2.,t1=4.)])
    C.repair_requirements(coverage,[0,1],{},LLM())
    assert coverage['windows'][0]['name']=='First' and 'must_show' not in coverage['windows'][0]
    assert coverage['windows'][1]['first']==5 and coverage['windows'][1]['last']==9
    assert coverage['windows'][1]['t0']==2. and coverage['windows'][1]['t1']==4.


def test_original_publisher_candidates_require_exact_retained_source(monkeypatch):
    from footage import run
    monkeypatch.setattr(run.fetch,'local_source',lambda url:'/retained.mp4' if url=='https://publisher/real.mp4' else None)
    def source(url):return dict(id=url,url=url,where='primary',title='Original film',channel='Publisher')
    named=[dict(name='Actual subject',video_sources=[source('https://publisher/real.mp4'),source('https://publisher/missing.mp4')])]
    got=run.primary_hits([dict(name='Other subject'),dict(name='Actual subject')],named)
    assert list(got)==[1] and [r['url'] for r in got[1]]==['https://publisher/real.mp4']


def test_exposed_speaker_gap_requires_actual_director_motivation(tmp_path):
    import json
    from types import SimpleNamespace
    coverage=dict(words=[dict(w='then',start=0,end=.3),dict(w='but',start=2,end=2.3),dict(w='now',start=4,end=4.3)],moves=[])
    llm=SimpleNamespace(ask=lambda *a,**k:(json.dumps({'moves':[{'word':1,'why':'The explicit contrast changes the argument.'}]}),{}),json_block=json.loads)
    moves,_=C.speaker_moves(coverage,[],150,[],str(tmp_path),llm=llm)
    assert moves==[dict(word=1,why='The explicit contrast changes the argument.')]
    empty=SimpleNamespace(ask=lambda *a,**k:('{"moves":[]}',{}),json_block=json.loads)
    other=tmp_path/'unresolved';other.mkdir()
    # a hold the director cannot honestly break is recorded, never a stop; the film breaks it on the voice's
    # strongest stress (wf1_film.stress_moves)
    cov=dict(coverage,moves=[])
    moves,_=C.speaker_moves(cov,[],150,[],str(other),llm=empty)
    assert moves==[] and [list(h) for h in cov['unresolved_holds']]==[[0,150]]


def test_new_picture_identity_survives_research_cache_paths():
    c=C
    a=dict(path='first.jpg',document=dict(sha256='same',page=1,box=[.1,.1,.8,.4]))
    b=dict(path='retry.jpg',document=dict(sha256='same',page=1,box=[.11,.12,.81,.42]))
    other=dict(path='figure.jpg',document=dict(sha256='same',page=1,box=[.1,.6,.8,.9]))
    assert c.same_picture(a,b) and not c.same_picture(a,other)
    v=dict(path='a.mp4',source=dict(url='original'),time_range=[10,12])
    assert c.same_picture(v,dict(v,path='retry.mp4',time_range=[10.1,12.1]))
    assert not c.same_picture(v,dict(v,path='new.mp4',time_range=[20,22]))


def test_camera_feedback_uses_remaining_span_without_automatic_recrops(tmp_path):
    import json
    from types import SimpleNamespace
    seen=[]
    def ask(prompt,content,**kwargs):
        data=json.loads(content);seen.append(data)
        word=0 if len(seen)==1 else 1
        return json.dumps(dict(moves=[dict(word=word,why='The spoken contrast changes the argument') ])),{}
    cov=dict(words=[dict(w='but',start=.2,end=.4),dict(w='instead',start=2.1,end=2.4)],moves=[])
    moves,_=C.speaker_moves(cov,[],135,[],str(tmp_path),llm=SimpleNamespace(ask=ask,json_block=json.loads))
    assert [m['word'] for m in moves]==[0,1]
    assert seen[0]['unchanged_spans']==[[0,135]]
    assert seen[1]['unchanged_spans']==[[9,135]]


def test_director_can_earn_a_move_in_a_silent_reaction_but_not_another_word(tmp_path):
    import json
    from types import SimpleNamespace
    cov=dict(words=[dict(w='impressive',start=0,end=.2),dict(w='then',start=4,end=4.4)],moves=[])
    def ask(*args,**kwargs):return json.dumps(dict(moves=[dict(word=0,offset_s=2,why='The silent reaction undercuts his stated admiration')])),{}
    moves,_=C.speaker_moves(cov,[],135,[],str(tmp_path),llm=SimpleNamespace(ask=ask,json_block=json.loads))
    assert len(moves)==1 and C.move_frame(cov,moves[0])==63


def test_context_rejection_does_not_destroy_a_picture_valid_under_a_later_clause():
    cov=dict(windows=[dict(t0=0,t1=2,name='Project',subjects=['Accelerator'],must_show='the project'),dict(t0=3,t1=5,name='Accelerator',must_show='the accelerator')])
    picture=dict(path='original-program-document.jpg')
    first=C.proposals(cov,{},dict(Accelerator=[picture]))
    # nothing is placed twice (supply lane, 20 Nov): a photo shown in window 0 leaves window 1 to its ladder and the gap fill
    assert [r[4]['window'] for r in first]==[0]
    repaired=C.proposals(cov,{},dict(Accelerator=[picture]),rejected={(0,picture['path'])})
    assert [r[4]['window'] for r in repaired]==[1]


def test_research_can_resolve_a_wrong_pronoun_label_without_changing_word_bounds():
    import json
    from types import SimpleNamespace
    cov=dict(windows=[dict(name='City',first=2,last=5,t0=1,t1=3,must_show='City')])
    row=dict(i=0,name='Internship',must_show='Original employer premises',referent_evidence='The preceding clause identifies the internship as what he expects to dislike.',video_searches=['employer historical headquarters'])
    llm=SimpleNamespace(ask=lambda *a,**k:(json.dumps({'windows':[row]}),{}),json_block=json.loads)
    C.repair_requirements(cov,[0],{},llm)
    assert cov['windows'][0]['name']=='Internship'
    assert cov['windows'][0]['original_name']=='City'
    assert (cov['windows'][0]['first'],cov['windows'][0]['last'])==(2,5)


def test_repeated_primary_evidence_covers_a_callback_but_is_not_a_new_picture():
    cov=dict(windows=[dict(name='Exact experiment',must_show='Original source',t0=0,t1=1),dict(name='Exact experiment',must_show='Original source',t0=2,t1=3)])
    rows=C.proposals(cov,{}, {'Exact experiment':[dict(path='original-document.jpg')]})
    # the same evidence is never placed twice (19 Nov m10: one clip three times); a still has no fresh interval, so the
    # callback window is left to its ladder rungs and the gap fill, and the shortage names it
    assert len(rows)==1 and [r[4]['window'] for r in rows]==[0]
    with pytest.raises(C.CoverageError) as error:C.require_coverage(rows,cov,3)
    assert error.value.report['new_pictures']==1
    assert error.value.report['repeated_pictures']==0
    assert error.value.report['new_per_10s']==5
    assert error.value.report['missing_windows']==[1]


def test_recorded_attack_after_half_second_is_levelled_at_its_actual_peak():
    sr=48000;t=np.arange(sr*4)/sr
    voice=.1*np.sin(2*np.pi*220*t);bed=.045*np.sin(2*np.pi*110*t)
    sample=np.zeros_like(t);a=int(1.9*sr);sample[a:a+4800]=.03*np.sin(2*np.pi*1200*t[:4800])*np.hanning(4800)
    def render(items):return sample*sum(10**(x['db']/20) for x in items)
    out=audibility.present_hits([dict(layer='hit',start_s=1.,peak_s=.9,db=-5.)],voice,bed,render)
    assert out[0]['broadband_lift_db']<20
    assert audibility.measure(voice,render(out),bed)['T2']==1


def test_attack_in_speech_pause_can_clear_bed_without_adding_events():
    sr=48000;t=np.arange(sr*5)/sr
    voice=.1*np.sin(2*np.pi*220*t);voice[int(1.5*sr):int(4.5*sr)]=0
    bed=.04*np.sin(2*np.pi*110*t);sample=np.zeros_like(t);a=sr*3
    sample[a:a+4800]=.05*np.sin(2*np.pi*1200*t[:4800])*np.hanning(4800)
    def render(items):return sample*sum(10**(x['db']/20) for x in items)
    original=[dict(id='earned-picture',layer='hit',start_s=3.,peak_s=.05,db=-9.)]
    out=audibility.present_hits(original,voice,bed,render)
    assert len(out)==1 and out[0]['id']==original[0]['id']
    assert audibility.measure(voice,render(out),bed)['T2']==1
    assert np.max(np.abs(render(out)))<=.701


def test_attack_remains_distinct_after_measured_master_attenuation():
    sr=48000;t=np.arange(sr*4)/sr
    voice=.1*np.sin(2*np.pi*220*t);bed=.045*np.sin(2*np.pi*110*t)
    sample=np.zeros_like(t);a=sr*2
    sample[a:a+4800]=.03*np.sin(2*np.pi*1200*t[:4800])*np.hanning(4800)
    def render(items):return sample*sum(10**(x['db']/20) for x in items)
    sounds=[dict(id='actual-picture-change',layer='hit',start_s=2.,peak_s=.05,db=-5.)]
    out=audibility.present_hits(sounds,voice,bed,render)
    # Final-export stem fits found 1.6 dB less gain on SFX than on the bed.
    # Exercise a conservative 2 dB loss without changing the audibility ruler.
    assert audibility.measure(voice,render(out)*10**(-2/20),bed)['T2']==1
    assert [s['id'] for s in out]==[s['id'] for s in sounds]


def test_semantic_boundary_repair_preserves_subjects_and_moves_the_actual_word():
    import json
    from types import SimpleNamespace
    words=[dict(w=w,start=i,end=i+1) for i,w in enumerate(['the','Sun','around','Earth','and','beyond'])]
    cov=dict(words=words,windows=[dict(name='Sun',first=0,last=3,t0=0,t1=4),dict(name='Earth',first=4,last=5,t0=4,t1=6)])
    answer=dict(updates=[dict(i=0,first=0,last=2,why='The next named object begins at word 3.'),dict(i=1,first=3,last=5,why='Enter Earth on its own word.')])
    llm=SimpleNamespace(ask=lambda *a,**k:(json.dumps(answer),{}),json_block=json.loads)
    assert C.repair_boundaries(cov,[dict(reason='Earth is read over the Sun')],llm)
    assert [x['name'] for x in cov['windows']]==['Sun','Earth']
    assert cov['windows'][0]['t1']==cov['windows'][1]['t0']==3


def test_cropped_face_does_not_need_a_semantic_boundary_change():
    import json
    from types import SimpleNamespace
    cov=dict(words=[dict(w='people',start=0,end=1)],windows=[dict(name='People',first=0,last=0,t0=0,t1=1)])
    llm=SimpleNamespace(ask=lambda *a,**k:(json.dumps(dict(updates=[])),{}),json_block=json.loads)
    assert not C.repair_boundaries(cov,[dict(reason='A face is cropped')],llm)
    assert cov['windows'][0]['first']==cov['windows'][0]['last']==0


@pytest.mark.parametrize('initial_gain',[-5.,10.])
def test_needle_click_uses_fuller_same_event_recording_without_clipping(initial_gain):
    from score import audibility
    sr=48000; t=np.arange(sr*3)/sr
    voice=.08*np.sin(2*np.pi*220*t); bed=.04*np.sin(2*np.pi*110*t)
    def render(items):
        out=np.zeros_like(t)
        for it in items:
            at=int(it['start_s']*sr)
            length=1 if it['s']=='needle' else 2400
            out[at:at+length]+=.6*np.cos(2*np.pi*800*np.arange(length)/sr)*10**(it['db']/20)
        return out
    original=[dict(s='needle',layer='hit',tier='tick',start_s=1.,peak_s=0.,db=initial_gain)]
    out=audibility.present_hits(original,voice,bed,render,alternatives=lambda item:[dict(item,s='fuller')])
    assert len(out)==1 and out[0]['s']=='fuller' and out[0]['start_s']==1.
    assert np.max(np.abs(render(out)))<.701
    assert audibility.measure(voice,render(out),bed)['T2']==1


def test_podcast_build_does_not_stack_designed_hole_on_a_silent_source_bar():
    from score import music
    bars=[dict(t0=i*2.,t1=(i+1)*2.,e=.9,k=-14.,voc=0.,hit=7.,sec_start=i%8==0) for i in range(32)]
    for i in [6,7,14,15,22,23]:bars[i].update(e=.01,k=-40.)
    for i in [8,16,24]:bars[i]['drop']=True
    song=dict(bars=bars,bar_s=2.,sim=np.ones((32,32)).tolist())
    marks=dict(dur_s=26.,words=[(.2,25.)],cuts=[],payoff_s=18.,turn_s=None,end_hit_s=25.,quiet=[])
    ordinary=music.arrange(song,marks)
    assert ordinary['ok'] and any(bars[b['bar']]['k']<-20 for b in ordinary['bars_played'] if b['role']=='build')
    safe=music.arrange(song,marks,profile=music.profile_for('podcast'))
    assert safe['ok'] and all(bars[b['bar']]['k']>=-20 for b in safe['bars_played'] if b['role']=='build')


def test_generic_approved_click_can_replace_a_thin_type_click(monkeypatch):
    from score import audibility,sound
    class Library:
        def row(self,sid,*args):
            return dict(id=sid,role='click',family='type' if sid=='thin' else None,file=sid+'.flac',peak_s=.1)
        def ranking(self,*args):return [(0.1,'generic')]
    monkeypatch.setattr(sound,'SF',lambda:Library())
    item=dict(s='thin',start_s=2.,trim_s=0.,peak_s=.1,db=-10.,event_f=63)
    choices=audibility.recording_alternatives(item)
    assert len(choices)==1 and choices[0]['s']=='generic'
    assert choices[0]['start_s']+choices[0]['peak_s']==2.1 and choices[0]['event_f']==63


def test_workflow_seam_keeps_zero_hearing_and_motion_scout(monkeypatch,tmp_path):
    from footage import seam,run
    monkeypatch.setattr(seam.switch,'allowed',lambda _:True)
    calls=[]
    monkeypatch.setattr(run,'find',lambda *a,**kw:calls.append(kw) or {})
    seam.for_lines([],[],'test',str(tmp_path),hear_s=0,scout_motion=True,source_only=True)
    assert calls[0]['hear_s']==0
    assert calls[0]['scout_motion'] is True and calls[0]['source_only'] is True


def test_originals_only_recovery_does_not_repeat_platform_search(monkeypatch,tmp_path):
    from footage import run
    monkeypatch.setenv('SPEND_LANE','customer')
    monkeypatch.setattr(run.plan,'plan',lambda *a,**kw:({'subjects':[{'name':'Campus','searches':[{'where':'youtube','q':'Campus tour'}]}],'lines':[]},0))
    monkeypatch.setattr(run.plan,'rank',lambda *a,**kw:([],0))
    monkeypatch.setattr(run.search,'run',lambda *a,**kw:pytest.fail('repeated blocked platform search'))
    result=run.find([],[],'test',str(tmp_path),source_only=True,hear_s=0)
    assert result['counts']['clips']==0
    assert result['counts']['sources_tried']==0


def test_source_programming_error_is_not_a_platform_refusal(monkeypatch,tmp_path):
    from footage import run
    monkeypatch.setattr(run.fetch,'whole_av',lambda *a:('video.mp4','cached'))
    monkeypatch.setattr(run.shots,'probe',lambda _:dict(dur=3,w=1920,h=1080,audio=True))
    monkeypatch.setattr(run.shots,'cuts',lambda *a:[(0,3)])
    monkeypatch.setattr(run.hear,'transcribe',lambda *a:(_ for _ in ()).throw(TypeError("metadata_errors")))
    with pytest.raises(TypeError,match='metadata_errors'):
        run._open(dict(key='s0',url='https://video.example/source',duration=3,title='Source'),str(tmp_path),lambda _:None,whole_first=True)


def test_source_fetch_failure_keeps_its_stage(monkeypatch,tmp_path):
    from footage import run
    monkeypatch.setattr(run.fetch,'whole_av',lambda *a:(_ for _ in ()).throw(run.fetch.FetchError('provider refused')))
    out=run._open(dict(key='s0',url='https://video.example/source',duration=3,title='Source'),str(tmp_path),lambda _:None,whole_first=True)
    assert out['error_stage']=='fetch' and out['error_type']=='FetchError'
def test_verified_motion_outranks_unknown_high_pixel_motion():
    coverage=dict(windows=[dict(name='Company',must_show='Actual company',t0=0,t1=1)])
    clips=[dict(path='cursor.mp4',seconds=2,motion_p90=100),
           dict(path='actual-action.mp4',seconds=2,motion_p90=2,subject_motion=True)]
    rows=C.proposals(coverage,{0:clips},{})
    assert rows[0][4]['path']=='actual-action.mp4'


def test_local_motion_alternatives_are_checked_before_research():
    coverage=dict(windows=[dict(name='Company',must_show='Actual company',t0=0,t1=4)])
    stills=[dict(path=f'cursor-{i}.mp4',seconds=2,motion_p90=100-i) for i in range(4)]
    moving=[dict(path=f'action-{i}.mp4',seconds=2,motion_p90=10-i) for i in range(4)]
    moving_paths={r['path'] for r in moving};calls=[]
    def check(rows):
        calls.append([r[4]['path'] for r in rows]);decisions=[]
        for row in rows:
            motion=row[4]['path'] in moving_paths
            row[4].update(verified_motion=motion,placed_motion_p90=2)
            decisions.append(dict(ok=True,motion_ok=motion))
        return rows,dict(placements=decisions,rejected=0,spend=[])
    rows,report=C.checked_proposals(coverage,{0:stills+moving},{},5,check)
    assert len(calls)==2 and set(calls[1])==moving_paths
    assert C.require_coverage(rows,coverage,5)['moving_seconds']==4
    assert len(report['attempts'])==2


def test_exhausted_local_stills_do_not_loop_or_gain_motion_credit():
    coverage=dict(windows=[dict(name='Company',must_show='Actual company',t0=0,t1=1)])
    calls=[]
    def check(rows):
        calls.append(rows)
        return rows,dict(placements=[dict(ok=True,motion_ok=False)],rejected=0,spend=[])
    rows,_=C.checked_proposals(coverage,{0:[dict(path='still.mp4',seconds=2,motion_p90=100)]},{},2,check)
    assert len(calls)==1
    with pytest.raises(C.CoverageError):C.require_coverage(rows,coverage,2)
