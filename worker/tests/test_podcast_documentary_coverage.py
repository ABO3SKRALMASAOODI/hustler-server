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
    assert C.require_coverage(rows,coverage,10)['moving_seconds']==8


def test_picture_starts_at_its_word_window_not_sentence_start():
    coverage = {'windows':[{'t0':6.3,'t1':8.,'name':'Facebook','must_show':'actual social network'}]}
    footage = {0:[{'path':f'new-shot-{i}', 'seconds':2.,'subject':'Facebook'} for i in range(3)]}
    rows = C.proposals(coverage,footage,{})
    assert rows[0][0] == 192
    assert all(192 <= r[0] < r[1] <= 243 for r in rows)
    assert len({r[4]['path'] for r in rows})==len(rows)


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
    with pytest.raises(RuntimeError,match='no automatic recrops'):
        C.speaker_moves(coverage,[],150,[],str(other),llm=empty)


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
    assert [r[4]['window'] for r in first]==[0,1]
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
    assert len(rows)==2 and [r[4]['window'] for r in rows]==[0,1]
    with pytest.raises(C.CoverageError) as error:C.require_coverage(rows,cov,3)
    assert error.value.report['new_pictures']==1
    assert error.value.report['repeated_pictures']==1
    assert error.value.report['new_per_10s']==5
    assert not error.value.report['missing_windows']
