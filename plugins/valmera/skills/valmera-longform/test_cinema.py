import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cinema as C


def test_worker_imports_precede_the_shell_even_when_already_on_pythonpath(monkeypatch):
    p=C.PX.worker_dir()
    monkeypatch.setattr(C.sys,'path',['/some/shell',p])
    C.worker()
    assert C.sys.path==[p,'/some/shell']


def test_cached_idea_helper_cannot_shadow_worker_models(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setitem(C.sys.modules,'llm',SimpleNamespace(__file__='/some/shell/llm.py'))
    C.worker()
    import llm
    assert Path(llm.__file__).resolve()==(Path(C.PX.worker_dir())/'llm.py').resolve()
    assert callable(llm.fence)


def test_passing_narration_for_old_words_cannot_skip_a_new_read(tmp_path,monkeypatch):
    from types import SimpleNamespace
    C.save(tmp_path/'read-stage.json',dict(script={'segments':[dict(text='Before.')]}))
    C.save(tmp_path/'longform_voice.json',dict(after=dict(ok=True)))
    C.save(tmp_path/'check.json',dict(gate={'pass':True}))
    calls=[]
    monkeypatch.setattr(C,'guarded_voice',lambda ctx,cmd,label:calls.append(cmd))
    monkeypatch.setattr(C.PX,'narrate',lambda *a:dict(test=True))
    C.narrated(SimpleNamespace(run_id='test',log=lambda *a:None),
               {'segments':[dict(text='After.')]},tmp_path,'reader.py')
    assert calls and calls[0][2]=='render'


def test_witness_splice_uses_guarded_narrator_environment(tmp_path,monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(C,'guarded_voice',lambda ctx,cmd,label:calls.append((cmd,label)))
    C.splice_voices(SimpleNamespace(supply=str(tmp_path),run_id='test'),tmp_path/'narration',[dict(person='Witness')],tmp_path/'footage')
    cmd,label=calls[0]
    assert cmd[0]==C.PX.NARRATE_PY and Path(cmd[1]).name=='witness_audio.py'
    assert C.read(cmd[2])['voices']==[dict(person='Witness')]
    assert label=='wf4-witness-audio-test'


@pytest.mark.skipif(not Path(C.PX.NARRATE_PY).exists(),reason='installed narrator environment is local')
def test_witness_audio_resume_preserves_narrator_and_does_not_insert_twice(tmp_path):
    import subprocess,wave
    import numpy as np
    sr=24000
    def audio(path,seconds,hz):
        samples=(np.sin(np.arange(round(seconds*sr))*2*np.pi*hz/sr)*4000).astype('<i2')
        with wave.open(str(path),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr);f.writeframes(samples.tobytes())
    audio(tmp_path/'narration.wav',4,180);audio(tmp_path/'witness.wav',1,220)
    original=(tmp_path/'narration.wav').read_bytes()
    C.save(tmp_path/'read-stage.json',dict(script='fixture'))
    C.save(tmp_path/'words.json',dict(duration=4,words=[dict(w='Before.',start=.2,end=.8,seg=0),dict(w='After.',start=2,end=3.8,seg=1)]))
    job=tmp_path/'job.json';C.save(job,dict(narration_dir=str(tmp_path),footage_dir=str(tmp_path),voices=[dict(after=0,person='Witness',wav='witness.wav',file='witness.mp4',said='Here.',words=[dict(w='Here.',start=0,end=1)],source=dict(url='https://archive.test/interview'),time_range=[0,1])]))
    cmd=[C.PX.NARRATE_PY,str(C.HERE/'witness_audio.py'),str(job)]
    subprocess.run(cmd,check=True,capture_output=True,timeout=60)
    first=(tmp_path/'narration.wav').read_bytes();words=C.read(tmp_path/'words.json')
    subprocess.run(cmd,check=True,capture_output=True,timeout=60)
    assert (tmp_path/'narration_narrator.wav').read_bytes()==original
    assert (tmp_path/'narration.wav').read_bytes()==first and C.read(tmp_path/'words.json')==words
    assert len(words['voices'])==1 and sum(bool(w.get('voice')) for w in words['words'])==1
    assert words['duration']==pytest.approx(5)
    import witness_audio as WA
    spec=C.read(job)
    assert WA.completed(spec,job)
    (tmp_path/'witness-complete.json').unlink()
    assert WA.completed(spec,job)  # adopt a fully completed older splice
    assert (tmp_path/'witness-complete.json').exists()
    # Changed inputs or a partially overwritten output cannot reuse the splice.
    changed=dict(spec,voices=[dict(spec['voices'][0],said='Changed.')])
    assert not WA.completed(changed,job)
    with (tmp_path/'narration.wav').open('ab') as f:f.write(b'changed')
    assert not WA.completed(spec,job)


def test_interview_tail_keeps_witness_context_not_preceding_narration():
    sc=dict(segments=[dict(say='They occupied it for months.',chapter='c'),dict(say='Then they returned.',chapter='c')])
    quote='Our goal was to build space stations.'
    words=dict(duration=12,words=[dict(w='Months.',start=.1,end=.8,seg=0),
        dict(w='Our',start=1,end=2,seg=0,voice='Witness'),dict(w='goal',start=2,end=4,seg=0,voice='Witness'),
        dict(w='stations.',start=8.9,end=9.4,seg=0,voice='Witness'),dict(w='Returned.',start=10,end=11.9,seg=1)],
        voices=[dict(start=1,end=9.5,after=0,file='interview',source={'url':'https://archive.test/witness'},time_range=[20,28.5],person='Witness',said=quote)])
    ft=dict(lines=[dict(i=0,clips=[clip('before',0,8)]),dict(i=1,clips=[clip('after',20,28)])])
    shots,_,_=C.schedule(sc,words,ft)
    witness=[r for r in shots if r.get('real_voice')]
    assert len(witness)==2 and witness[-1]['say']=='stations.'
    assert all(r['context']==quote for r in witness)


def test_rater_stem_fit_recovers_delay_and_final_gains():
    import numpy as np
    import stem_evidence as E
    rng=np.random.default_rng(4);n=E.SR*3
    stems=[rng.normal(0,.1,n) for _ in range(3)]
    gains=np.array([.7,.4,.2]);m=np.zeros(n)
    m[48:]=sum(g*s[:-48] for g,s in zip(gains,stems))
    q,lag,got=E.fitted(m,stems,n)
    assert lag==48 and q<-60
    assert np.allclose(got,gains,atol=1e-6)


def test_rater_sfx_count_excludes_effects_masked_by_voice_or_bed():
    import numpy as np
    import stem_evidence as E
    rng=np.random.default_rng(7);n=E.SR*6
    voice=rng.normal(0,.3,n);bed=rng.normal(0,.01,n);sfx=np.zeros(n)
    for t,g in ((1,.5),(3,.04),(5,.001)):
        start=t*E.SR;sfx[start:start+1920]=rng.normal(0,g,1920)
    events,_=E.audible(voice,sfx,bed)
    assert sum(e['T1'] for e in events)==2
    assert sum(e['T2'] for e in events)==1


def clip(name,a,b):
    return dict(path=name,source={'url':'https://archive.test/one'},time_range=[a,b],seconds=b-a,w=1920,h=1080)


def test_renamed_download_cannot_replay_source_seconds():
    used={}
    assert C.reserve(used,clip('a',0,8),4)==0
    assert C.reserve(used,clip('b',0,8),4)==4
    assert C.reserve(used,clip('c',0,8),1) is None


def test_photo_request_cannot_reuse_a_segment_number_from_another_draft():
    gaps=[dict(seg=0,heard='The restraint.',seconds=1.1)]
    a=dict(script=dict(segments=[dict(say='The restraint was intact.')]))
    b=dict(script=dict(segments=[dict(say='They examined a tooth.')]))
    assert C.evidence_request(a,gaps)!=C.evidence_request(b,gaps)
    assert C.evidence_request(a,gaps)!=C.evidence_request(a,[dict(gaps[0],heard='It was cut.')])


def test_legacy_photo_invalidation_preserves_checked_uses_and_source_bank(tmp_path):
    root=tmp_path/'evidence-photos'
    def photo(path):return dict(kind='image',path=str(root/path))
    wrong=photo('pics/old.jpg');checked=photo('primary/good.jpg');current=photo('request-new/pics/new.jpg')
    ft=dict(lines=[dict(i=0,clips=[wrong,checked,current])],editorial=dict(pins=[dict(seg=0,clip=checked)],bank=[wrong,checked],
            assignments=[dict(seg=0,candidates=[wrong]),dict(seg=0,candidates=[checked])]))
    C.prune_unchecked_photos(ft,root)
    assert ft['lines'][0]['clips']==[checked,current]
    assert ft['editorial']['bank']==[wrong,checked]
    assert ft['editorial']['assignments']==[dict(seg=0,candidates=[checked])]
    C.prune_unchecked_photos(ft,root,root/'request-other',{0})
    assert ft['lines'][0]['clips']==[checked]


def test_long_interview_camera_continues_across_internal_source_slices():
    c=clip('interview',0,19)
    a=dict(f=[0,234],src=[0,7.8],clip=c,real_voice='Speaker')
    b=dict(f=[234,468],src=[7.8,15.6],clip=c,real_voice='Speaker')
    whole=dict(f=[0,570],src=[0,19],clip=c,real_voice='Speaker')
    full=C.witness_crop(whole)
    assert C.witness_crop(a)==full[:234]
    import numpy as np
    assert np.allclose(C.witness_crop(b),full[234:468])
    assert full[0]==[0,0,1,1] and full[-1][0]<.06


def test_document_display_uses_title_and_never_renders_full_research_citation():
    c=dict(source=dict(title='Apollo Docking System',credit='Long citation and research context. '*100))
    assert C.archive_label(c,len,80)=='Apollo Docking System • archive record'
    c['source']['title']='A very long original report title '*100
    label=C.archive_label(c,len,80)
    assert len(label)<=80 and label.endswith('… • archive record')
    assert len(c['source']['credit'])>1000
    c['editorial_label']='Ground rehearsal, not the actual repair'
    assert C.archive_label(c,len,80)==c['editorial_label']
    with pytest.raises(ValueError,match='factual archive label'):C.archive_label(c,len,20)


def test_render_environment_preserves_subscription_network_guard(monkeypatch):
    import film_long as L
    monkeypatch.setenv('PYTHONPATH','/subscription/guard')
    assert '/subscription/guard' in L.env_path().split(C.os.pathsep)


def test_partial_picture_edit_research_includes_rejected_coverage(tmp_path,monkeypatch):
    import timeline_repair as R
    import evidence_allocate as A
    from types import SimpleNamespace
    bad=clip('wrong-picture',0,3)
    sc=dict(script=dict(segments=[dict(say='The right subject.',chapter='c')]))
    words=dict(duration=2,words=[dict(w='subject',start=.1,end=1.9,seg=0)])
    shot=dict(f=[0,57],seg=0,clip=bad,src=[0,1.9],say='The right subject.')
    ft=dict(lines=[dict(i=0,clips=[bad])],editorial=dict(pins=[],rejected=[],bank=[]))
    def schedule(*args):
        seconds=2 if ft['editorial']['rejected'] else .1
        raise C.CoverageError([dict(seg=0,seconds=seconds)],[] if seconds==2 else [shot])
    monkeypatch.setattr(C,'schedule',schedule)
    monkeypatch.setattr(C,'source_review',lambda *a,**kw:dict(ok=False,rejected=[0],evidence={'0':dict(matches=False,reason='Wrong subject.')}))
    observed=[]
    def allocate(ctx,script,ft,failure,words):
        observed.append(sum(g['seconds'] for g in failure.gaps))
        raise RuntimeError('stop after observing research scope')
    monkeypatch.setattr(A,'allocate',allocate)
    with pytest.raises(RuntimeError,match='observing research scope'):
        R.repair(SimpleNamespace(supply=str(tmp_path),log=lambda *a:None),sc,ft,words)
    assert observed==[2]


def test_partly_used_clip_can_supply_only_its_unseen_remainder():
    sc={'segments':[{'say':'First.','chapter':'c'},{'say':'Second.','chapter':'c'}]}
    words={'duration':10,'words':[dict(w='First.',start=.1,end=2.5,seg=0),dict(w='Second.',start=3,end=9.8,seg=1)]}
    ft={'lines':[dict(i=0,clips=[clip('a',0,8)]),dict(i=1,clips=[clip('b',0,8),clip('c',10,16)])]}
    shots,_,_=C.schedule(sc,words,ft)
    assert not C.duplicate_ranges(shots)
    assert abs(sum((r['f'][1]-r['f'][0])/30 for r in shots)-10.1)<.04
    assert shots[1]['src'][0]>3


def test_scarce_evidence_is_reserved_before_a_flexible_earlier_line():
    sc={'segments':[{'say':'First.','chapter':'c'},{'say':'Second.','chapter':'c'}]}
    words={'duration':9.9,'words':[dict(w='First.',start=.1,end=4.5,seg=0),dict(w='Second.',start=5,end=9.8,seg=1)]}
    shared=clip('shared',0,5.1);other=clip('other',10,16)
    ft={'lines':[dict(i=0,clips=[shared,other]),dict(i=1,clips=[shared])]}
    shots,_,_=C.schedule(sc,words,ft)
    assert shots[-1]['clip']['path']=='shared'
    assert not C.duplicate_ranges(shots)


def test_visual_subject_changes_follow_spoken_word_anchors():
    sc={'segments':[{'say':'Station. Rehearsal.','chapter':'c','visual_beats':[
        dict(word_start=0,archive_ids=['station']),dict(word_start=1,archive_ids=['pool'])]}]}
    words={'duration':7.9,'words':[dict(w='Station.',start=.1,end=3.5,seg=0),dict(w='Rehearsal.',start=4,end=7.8,seg=0)]}
    a=dict(clip('station',0,6),catalogue_id='station');b=dict(clip('pool',10,16),catalogue_id='pool')
    shots,clock,_=C.schedule(sc,words,dict(lines=[dict(i=0,clips=[b,a])]))
    assert [r['clip']['path'] for r in shots]==['station','pool']
    assert shots[0]['f'][1]==round(clock.seg_words(0)[1]['t0']*30)
    assert not C.duplicate_ranges(shots)


def test_exact_gap_assignment_does_not_replace_its_neighbour():
    import timeline_repair as R
    sc={'segments':[{'say':'Station. Rehearsal.','chapter':'c','visual_beats':[
        dict(word_start=0,archive_ids=['station']),dict(word_start=1,archive_ids=['missing'])]}]}
    words={'duration':7.9,'words':[dict(w='Station.',start=.1,end=3.5,seg=0),dict(w='Rehearsal.',start=4,end=7.8,seg=0)]}
    a=dict(clip('station',0,6),catalogue_id='station');b=clip('repaired-pool',10,16)
    clock=C.F.Clock(words,sc);cut=round(clock.seg_words(0)[1]['t0']*30)/30
    ft=dict(lines=[dict(i=0,clips=[a])],editorial=dict(assignments=[dict(seg=0,anchors=[R.anchor(clock,cut),R.anchor(clock,8)],candidates=[b])]))
    shots,_,_=C.schedule(sc,words,ft)
    assert [r['clip']['path'] for r in shots]==['station','repaired-pool']


def test_scarce_image_moves_only_with_a_complete_replacement_for_its_old_use(tmp_path,monkeypatch):
    import evidence_allocate as A
    import timeline_repair as R
    import wf4
    from types import SimpleNamespace
    from PIL import Image
    photo=tmp_path/'airlock.jpg';Image.new('RGB',(32,32),'blue').save(photo)
    image=dict(clip(str(photo),0,7.8),kind='image',content_hash='airlock-photo',shows='The experiment airlock')
    image['source']={'url':'https://archive.test/airlock.jpg'}
    video=dict(clip('interior',20,26),shows='The workshop interior')
    sc=dict(title='Example',script=dict(segments=[dict(say='Inside.',chapter='c'),dict(say='Airlock.',chapter='c')]))
    words=dict(duration=9.9,words=[dict(w='Inside.',start=.1,end=4.8,seg=0),dict(w='Airlock.',start=5,end=9.8,seg=1)])
    clock=C.F.Clock(words,sc['script']);cut=clock.segs[1][0]
    prior=dict(f=[0,round(cut*30)],seg=0,clip=image,src=[0,cut],say='Inside.')
    ft=dict(lines=[dict(i=0,clips=[image]),dict(i=1,clips=[])],editorial=dict(
        pins=[dict(seg=0,clip=image,src=prior['src'],anchors=[R.anchor(clock,0),R.anchor(clock,cut)])],
        rejected=[],bank=[image,video]))
    failure=C.CoverageError([dict(seg=1,seconds=clock.dur-cut,heard='Airlock.',words='Airlock.',start=cut,end=clock.dur)],[prior])
    def ask(system,payload,**kw):
        rows=json.loads(payload)['candidates'];new=next(r['i'] for r in rows if not r.get('move_only'));old=next(r['i'] for r in rows if r.get('move_only'))
        return json.dumps(dict(allocations=[dict(gap=0,candidates=[old],why='Specific evidence')],
                               swaps=[dict(move=old,replacements=[new],why='Interior covers earlier context')])),{}
    monkeypatch.setattr(wf4.story.writer,'ask',ask)
    A.allocate(SimpleNamespace(supply=str(tmp_path)),sc,ft,failure,words)
    shots,_,_=C.schedule(sc['script'],words,ft)
    assert [s['clip']['path'] for s in shots]==['interior',str(photo)]
    assert not C.duplicate_ranges(shots)


def test_evidence_context_label_changes_presentation_but_never_approves_picture(tmp_path,monkeypatch):
    import evidence_allocate as A
    import timeline_repair as R
    import wf4
    from types import SimpleNamespace
    original=dict(clip('rehearsal',0,5),shows='Underwater rehearsal of the repair')
    script=dict(title='Repair',script=dict(segments=[dict(say='They positioned the cutter.',chapter='c')]))
    words=dict(duration=3.9,words=[dict(w='Cutter.',start=.1,end=3.8,seg=0)])
    ft=dict(lines=[dict(i=0,clips=[])],editorial=dict(pins=[],bank=[original],rejected=[dict(seg=0,clip=R.identity(original),why='Rehearsal presented as flight',words='Cutter.')]))
    failure=C.CoverageError([dict(seg=0,seconds=4,heard='Cutter.',words='Cutter.',start=0,end=4)])
    answer=dict(allocations=[dict(gap=0,candidates=[0])],context_labels=[dict(candidate=0,label='Ground rehearsal • repair method',why='Visible rehearsal')])
    monkeypatch.setattr(wf4.story.writer,'ask',lambda *a,**k:(json.dumps(answer),{}))
    A.allocate(SimpleNamespace(supply=str(tmp_path)),script,ft,failure,words)
    shots,clock,_=C.schedule(script['script'],words,ft)
    assert shots[0]['clip']['editorial_label']=='Ground rehearsal • repair method'
    assert 'editorial_label' not in original and not ft['editorial']['pins']
    R.retain(ft,shots,clock,dict(evidence={'0':dict(matches=False,reason='Still the wrong mechanism')}))
    assert not ft['editorial']['pins']


def test_moving_replacement_preserves_still_when_new_view_fails(tmp_path,monkeypatch):
    import moving_picture as M
    from timeline_repair import anchor
    from types import SimpleNamespace
    from PIL import Image
    photo=tmp_path/'photo.jpg';Image.new('RGB',(32,32),'blue').save(photo)
    image=dict(clip(str(photo),0,7.8),kind='image',content_hash='one-photo')
    image['source']={'url':'https://archive.test/photo.jpg'}
    sc=dict(script=dict(segments=[dict(say='Workshop.',chapter='c')]))
    words=tmp_path/'words.json';C.save(words,dict(duration=4.9,words=[dict(w='Workshop.',start=.1,end=4.8,seg=0)]))
    clock=C.F.Clock(str(words),sc['script'])
    shot=dict(f=[0,150],seg=0,clip=image,src=[0,5],say='Workshop.')
    ft=dict(lines=[dict(i=0,clips=[image])],editorial=dict(pins=[dict(seg=0,clip=image,src=[0,5],anchors=[anchor(clock,0),anchor(clock,5)])],rejected=[],bank=[]))
    original=json.dumps(ft,sort_keys=True)
    monkeypatch.setattr(C,'source_review',lambda *a,**kw:dict(ok=False,moving_share=1))
    assert M.replacement(SimpleNamespace(),sc,ft,words,shot,[clip('wrong-flight',20,25)]) is None
    assert json.dumps(ft,sort_keys=True)==original
    monkeypatch.setattr(C,'source_review',lambda *a,**kw:dict(ok=True,moving_share=1))
    trial=M.replacement(SimpleNamespace(),sc,ft,words,shot,[clip('right-interior',20,25)])
    assert trial is not None and json.dumps(ft,sort_keys=True)==original
    rows,_,_=C.schedule(sc['script'],words,trial)
    assert len(rows)==1 and rows[0]['clip']['path']=='right-interior'


def test_source_verdict_cache_binds_words_and_original_bytes(tmp_path,monkeypatch):
    import picture_check,document_context,caption_context
    from types import SimpleNamespace
    monkeypatch.setattr(document_context,'enrich',lambda *a:None)
    monkeypatch.setattr(caption_context,'enrich',lambda *a:None)
    calls=[]
    def check(film,shots,*args,**kw):
        calls.append(len(shots))
        return dict(evidence={i:dict(i=i,matches=True,real_moving_footage=True) for i in range(len(shots))},spend_usd=0)
    monkeypatch.setattr(picture_check,'check',check)
    source=tmp_path/'source';source.write_bytes(b'original')
    row=dict(f=[0,90],seg=0,clip=clip(str(source),0,3),src=[0,3],say='First words.')
    ctx=SimpleNamespace(supply=str(tmp_path))
    assert C.source_review(ctx,[row])['ok']
    shifted=dict(row,f=[90,180])
    assert C.source_review(ctx,[shifted])['cached_shots']==1
    changed=dict(shifted,say='Different words.')
    C.source_review(ctx,[changed])
    source.write_bytes(b'replaced original')
    C.source_review(ctx,[changed])
    assert calls==[1,1,1]


def test_later_document_gap_can_find_a_new_report_without_page_cache_collision(tmp_path):
    import document_evidence as D
    assert D.discovery_key([dict(seg=1,heard='Shade.')])!=D.discovery_key([dict(seg=2,heard='Docking.')])
    assert D.figure_base(tmp_path,dict(url='https://archive.test/first.pdf'),12)!=D.figure_base(tmp_path,dict(url='https://archive.test/second.pdf'),12)


def test_document_review_preserves_each_heard_interval_and_its_context():
    import document_evidence as D
    gaps=[dict(seg=4,heard='Still intact.',words='They positioned the cutter. The restraint was still intact.'),
          dict(seg=4,heard='The cutter.',words='They positioned the cutter. The restraint was still intact.'),
          dict(seg=5,words='Then it opened.')]
    rows=D.spoken_intervals(gaps)
    assert [r['heard'] for r in rows[4]]==['Still intact.','The cutter.']
    assert all(r['context']==gaps[0]['words'] for r in rows[4])
    assert rows[5]==[dict(heard='Then it opened.',context='Then it opened.')]


def test_checked_figure_can_continue_as_one_moving_use_but_never_skip_word_review(tmp_path,monkeypatch):
    import copy
    import timeline_repair as R
    from types import SimpleNamespace
    from PIL import Image
    path=tmp_path/'figure.jpg';Image.new('RGB',(32,32),'blue').save(path)
    image=dict(clip(str(path),0,7.8),kind='image',content_hash='figure')
    sc=dict(script=dict(segments=[dict(say='Mechanism.',chapter='c')]))
    words=tmp_path/'words.json';C.save(words,dict(duration=9.9,words=[dict(w='Mechanism.',start=.1,end=9.8,seg=0)]))
    clock=C.F.Clock(str(words),sc['script'])
    ft=dict(lines=[dict(i=0,clips=[image])],editorial=dict(pins=[dict(seg=0,clip=image,src=[0,7.8],anchors=[R.anchor(clock,0),R.anchor(clock,7.8)])]))
    with pytest.raises(C.CoverageError) as caught:C.schedule(sc['script'],words,ft)
    before=copy.deepcopy(ft)
    monkeypatch.setattr(C,'source_review',lambda *a,**k:dict(ok=False))
    assert not R.continue_still(SimpleNamespace(),sc,ft,caught.value,words) and ft==before
    monkeypatch.setattr(C,'source_review',lambda *a,**k:dict(ok=True))
    assert R.continue_still(SimpleNamespace(),sc,ft,caught.value,words)
    rows,_,end=C.schedule(sc['script'],words,ft)
    assert len(rows)==1 and rows[0]['f']==[0,end] and rows[0]['clip']['kind']=='image'
    assert rows[0]['clip']['editorial_hold_move'] and not C.duplicate_ranges(rows)


def test_new_figure_can_cover_adjacent_sentences_only_after_complete_review(tmp_path,monkeypatch):
    import copy
    import timeline_repair as R
    from types import SimpleNamespace
    from PIL import Image
    path=tmp_path/'figure.jpg';Image.new('RGB',(32,32),'blue').save(path)
    image=dict(clip(str(path),0,7.8),kind='image',content_hash='figure')
    script=dict(script=dict(segments=[dict(say='It opened.',chapter='c'),dict(say='They recoiled.',chapter='c')]))
    words=dict(duration=9.2,words=[dict(w='Opened.',start=.1,end=.9,seg=0),dict(w='Recoiled.',start=1.1,end=9.1,seg=1)])
    row=dict(f=[30,264],seg=1,clip=image,src=[0,7.8])
    failure=C.CoverageError([dict(seg=0,start=0,end=1),dict(seg=1,start=8.8,end=9.3)],[row])
    ft=dict(lines=[dict(i=0,clips=[]),dict(i=1,clips=[image])],editorial=dict(pins=[],bank=[],rejected=[]))
    before=copy.deepcopy(ft);checked=[]
    monkeypatch.setattr(C,'source_review',lambda ctx,rows,**kw:checked.extend(rows) or dict(ok=False))
    assert not R.continue_still(SimpleNamespace(),script,ft,failure,words) and ft==before
    assert checked[0]['say']=='Opened. Recoiled.' and checked[0]['context']=='It opened. They recoiled.'
    checked.clear()
    monkeypatch.setattr(C,'source_review',lambda ctx,rows,**kw:checked.extend(rows) or dict(ok=True))
    assert R.continue_still(SimpleNamespace(),script,ft,failure,words)
    rows,_,end=C.schedule(script['script'],words,ft)
    assert len(rows)==1 and rows[0]['f']==[0,end] and len(ft['editorial']['pins'])==1
    assert rows[0]['clip']['editorial_hold_move'] and not C.duplicate_ranges(rows)
    assert len(checked)==2 and checked[-1]['f']==[0,279]
    # A longer hold cannot gain coverage by bypassing the bounded continuation.
    excessive=C.CoverageError([dict(seg=1,start=9.3,end=13)],rows)
    before=copy.deepcopy(ft)
    assert not R.continue_still(SimpleNamespace(),script,ft,excessive,words) and ft==before


def test_archive_revision_preserves_prior_read_and_resumes_without_erasing_new_audio(tmp_path):
    from types import SimpleNamespace
    import archive_script as A
    supply=tmp_path/'supply';supply.mkdir();(supply/'narration').mkdir()
    (supply/'narration'/'old.wav').write_bytes(b'prior-read')
    ctx=SimpleNamespace(run_dir=tmp_path,supply=supply)
    script=dict(title='Old',script={},dossier={});ft=dict(lines=[],voices=[],spend=[])
    segment=dict(say='True words.',facts=['f'],visual_beats=[dict(word_start=0,archive_ids=['a'],label='Ground rehearsal')])
    proposal=dict(script=dict(title='Revised',segments=[segment]),dossier=dict(facts=[dict(id='f')]),check=dict(words=2))
    catalogue=[dict(id='a',usable=True,clip=clip('archive',0,8))]
    A.apply(ctx,script,ft,proposal,catalogue)
    assert (supply/'archive-script/narration-before/old.wav').read_bytes()==b'prior-read'
    assert ft['lines'][0]['clips'][0]['editorial_label']=='Ground rehearsal'
    assert script['title']=='Revised'
    (supply/'narration/new.wav').write_bytes(b'revised-read')
    A.apply(ctx,script,ft,proposal,catalogue)
    assert (supply/'narration/new.wav').read_bytes()==b'revised-read'


def test_script_reorder_keeps_unchanged_sentence_take_budget(tmp_path):
    from archive_script import preserve_takes
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir();(old/'_takes').mkdir()
    C.save(old/'_generations.json',dict(segments=[dict(text='Keep this sentence.'),dict(text='Rewrite this one.')]))
    for i in range(30):(old/'_takes'/f's00_t{i:02d}.wav').write_bytes(str(i).encode())
    groups=dict(segments=[dict(text='A new sentence.'),dict(text='Keep this sentence.')])
    kept=preserve_takes(old,new,groups)
    assert kept==[dict(before=0,after=1,takes=30)]
    assert len(list((new/'_takes').glob('s01_t*.wav')))==30
    assert not list((new/'_takes').glob('s00_t*.wav'))


def test_factual_repair_preserves_unaffected_script_and_order():
    from archive_script import patch_script
    sc=dict(chapters=[dict(id='c',turn='Old')],segments=[dict(say='Keep.',facts=['a']),dict(say='Correct.',facts=['b'])])
    revised=patch_script(sc,dict(segments=[dict(index=1,changes=dict(say='Corrected.'))],chapters=[dict(id='c',changes=dict(turn='Supported'))]))
    assert revised['segments']==[sc['segments'][0],dict(say='Corrected.',facts=['b'])]
    assert sc['segments'][1]['say']=='Correct.'
    with pytest.raises(ValueError):patch_script(sc,dict(segments=[dict(index=2,changes=dict(say='Extra.'))]))


def test_visual_repair_cannot_change_a_recording_or_its_verified_turn():
    from archive_visual import apply_choices
    original=dict(say='Keep these words.',beat='turn',visual_beats=[])
    sc=dict(script=dict(segments=[original]));ft=dict(voices=[])
    cat=[dict(id='a',usable=True,clip=clip('archive',0,5))]
    with pytest.raises(ValueError):apply_choices(sc,ft,dict(script=dict(segments=[dict(say='Changed.')])),cat)
    revised=dict(script=dict(segments=[dict(say='Keep these words.',beat='setup',visual_beats=[dict(word_start=0,archive_ids=['a'])])]))
    apply_choices(sc,ft,revised,cat)
    assert original['beat']=='turn' and original['say']=='Keep these words.'
    assert ft['lines'][0]['clips'][0]['catalogue_id']=='a'


def test_checked_picture_follows_its_word_when_narration_timing_changes():
    import timeline_repair as R
    sc={'segments':[{'say':'Alpha beta.','chapter':'c'}]}
    original={'duration':4,'words':[dict(w='Alpha',start=.1,end=1,seg=0),dict(w='beta.',start=2,end=3.8,seg=0)]}
    changed={'duration':5,'words':[dict(w='Alpha',start=.1,end=1,seg=0),dict(w='beta.',start=3,end=4.8,seg=0)]}
    a=C.F.Clock(original,sc);b=C.F.Clock(changed,sc)
    anchor=R.anchor(a,2.1)
    assert abs(R.resolve(b,anchor)-3.1)<1e-6


def test_blink_repair_requires_a_checked_expanded_pairing(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import timeline_repair as R
    sc=dict(segments=[dict(say='Alpha beta.',chapter='c')])
    words=dict(duration=6.1,words=[dict(w='Alpha',start=.1,end=2.9,seg=0),dict(w='beta.',start=2.95,end=6,seg=0)])
    clock=C.F.Clock(words,sc)
    from PIL import Image
    image=tmp_path/'still.jpg';Image.new('RGB',(80,50),'gray').save(image)
    a=dict(clip(str(image),0,3),kind='image',content_hash='a')
    a['source']={'url':'https://archive.test/photograph'}
    b=clip('blink',10,10.1);c=clip('next',20,23.1)
    shots=[dict(f=f,seg=0,clip=p,src=[0,(f[1]-f[0])/30]) for f,p in [([0,90],a),([90,93],b),([93,186],c)]]
    ft=dict(lines=[dict(i=0,clips=[a,b,c])],editorial=dict(pins=[dict(seg=0,clip=r['clip'],src=r['src'],anchors=[R.anchor(clock,f/30) for f in r['f']]) for r in shots],rejected=[],bank=[]))
    ctx=SimpleNamespace(supply=str(tmp_path));script=dict(script=sc)
    monkeypatch.setattr(C,'source_review',lambda *a,**kw:dict(ok=False))
    assert len(R.smooth(ctx,script,ft,words)[0])==3
    checked=[]
    monkeypatch.setattr(C,'source_review',lambda ctx,rows,**kw:checked.extend(rows) or dict(ok=True))
    result,_=R.smooth(ctx,script,ft,words)
    assert len(result)==2 and result[0]['f']==[0,93]
    assert checked[0]['say']=='Alpha beta.' and not C.duplicate_ranges(result)


def test_download_duration_cannot_reserve_frames_beyond_encoded_end(tmp_path,monkeypatch):
    p=tmp_path/'clip.mp4';p.write_bytes(b'fixture')
    c=dict(clip(str(p),10,15),file='clip.mp4')
    monkeypatch.setattr(C.subprocess,'check_output',lambda *a,**k:json.dumps(dict(streams=[dict(duration='4.7',nb_frames='141',avg_frame_rate='30/1')])).encode())
    C.encoded_durations(dict(lines=[dict(clips=[c])]))
    assert c['seconds']==4.7
    assert C.unused_seconds({},c)==pytest.approx(4.7)


def test_a_broad_line_releases_the_only_image_needed_by_a_scarce_line():
    import timeline_repair as R
    image=dict(kind='image',content_hash='same',path='one')
    ft=dict(lines=[dict(i=1,clips=[image])],editorial=dict(pins=[dict(seg=0,clip=image)]))
    released=R.release_borrowed_images(ft,C.CoverageError([dict(seg=1)]))
    assert len(released)==1 and not ft['editorial']['pins']


def test_failed_clip_cannot_return_to_rejected_line_and_checked_slot_stays():
    import timeline_repair as R
    sc={'segments':[{'say':'Words.','chapter':'c'}]}
    words={'duration':7.9,'words':[dict(w='Words.',start=.1,end=7.8,seg=0)]}
    a,b=clip('a',0,4),clip('b',10,14)
    ft={'lines':[dict(i=0,clips=[a,b])]}
    shots,clock,_=C.schedule(sc,words,ft)
    R.retain(ft,shots,clock,dict(evidence={0:dict(matches=True),1:dict(matches=False,reason='wrong subject')}))
    ft['lines'][0]['clips'].append(b)
    with pytest.raises(C.CoverageError) as caught:C.schedule(sc,words,ft)
    assert len(caught.value.shots)==1
    assert caught.value.shots[0]['clip']['path']=='a'
    assert caught.value.shots[0]['f']==shots[0]['f']


def test_alternate_scans_and_mirrors_are_one_picture(tmp_path):
    import numpy as np
    from PIL import Image,ImageOps
    rng=np.random.default_rng(2)
    im=Image.fromarray(rng.integers(0,255,(80,100),dtype=np.uint8))
    a=tmp_path/'a.png';b=tmp_path/'b.jpg'
    im.save(a);ImageOps.mirror(im.resize((500,400))).save(b,quality=90)
    clips=[dict(kind='image',path=str(p),content_hash=C.digest(p)) for p in (a,b)]
    C.unify_image_copies(dict(lines=[dict(clips=clips)]))
    assert clips[0]['content_hash']==clips[1]['content_hash']


def test_archive_parser_ignores_site_navigation_images():
    from archive_pictures import ArticleImages
    parser=ArticleImages()
    parser.feed('<article><nav><img src="news.jpg" alt="unrelated"></nav><div class="entry-content"><figure><img src="archive.jpg" alt="actual subject"></figure><figcaption>Named person, NASA 1973</figcaption></div><img src="footer.jpg" alt="footer"></article>')
    assert [r['src'] for r in parser.rows]==['archive.jpg']
    assert parser.rows[0]['caption'].strip()=='Named person, NASA 1973'


def test_coverage_failure_is_explicit():
    sc={'segments':[{'say':'The real words.','chapter':'c'}]}
    words={'duration':8,'words':[{'w':'The','start':0.1,'end':1,'seg':0}]}
    with pytest.raises(C.CoverageError) as e:
        C.schedule(sc,words,{'lines':[{'i':0,'clips':[clip('a',0,2)]}]})
    assert e.value.gaps[0]['seconds']>5


def test_photo_fallback_requires_completed_research_and_moving_majority(tmp_path):
    gap=[dict(seg=3,seconds=40)]
    C.save(tmp_path/'initial/footage.json',dict(lines=[dict(i=3,clips=[])],counts=dict(frames_checked=20,sources_opened=2)))
    assert not C.photo_research_ready(tmp_path,gap,100)
    C.save(tmp_path/'second/footage.json',dict(lines=[dict(i=3,clips=[])],counts=dict(frames_checked=20,sources_opened=2)))
    assert C.photo_research_ready(tmp_path,gap,100)
    assert not C.photo_research_ready(tmp_path,[dict(seg=3,seconds=46)],100)
    assert not C.photo_research_ready(tmp_path,[dict(seg=4,seconds=10)],100)


def test_unique_footage_covers_narration_without_freeze():
    sc={'segments':[{'say':'Words.','chapter':'c'}]}
    words={'duration':8,'words':[{'w':'Words.','start':0.1,'end':1,'seg':0}]}
    clips=[clip('a',0,5),clip('b',10,15)]
    shots,clock,end=C.schedule(sc,words,{'lines':[{'i':0,'clips':clips}]})
    assert shots[0]['f'][0]==0 and shots[-1]['f'][1]==end
    assert all(a['f'][1]==b['f'][0] for a,b in zip(shots,shots[1:]))
    assert all(r['src'][1]-r['src'][0]<=r['clip']['seconds'] for r in shots)


def test_a_chapter_needs_a_timed_turn():
    sc={'chapters':[{'id':'c','title':'A','turn':'A reversal'}], 'segments':[{'chapter':'c','say':'Words.','beat':'setup'}]}
    class Clock: segs=[(0,10)]
    with pytest.raises(ValueError,match='no timed turn'):
        C.chapter_clock(sc,Clock(),300)


def test_footage_remote_program_compiles_without_embedded_secret():
    import footage_box as B
    compile(B.REMOTE,'remote','exec')
    assert 'sys.stdin.readline' in B.REMOTE


@pytest.mark.parametrize('attributed,frames,expected',[(False,[True,True,True],False),(True,[False,True,False],False),(True,[True,True,True],True)])
def test_witness_requires_both_text_attribution_and_on_camera_presentation(tmp_path,monkeypatch,attributed,frames,expected):
    import witness_evidence as W
    from footage import ask,shots
    monkeypatch.setattr(shots,'frames',lambda *a:{1:'a',2:'b',3:'c'})
    monkeypatch.setattr(ask,'image_block',lambda path:dict(type='text',text=path))
    monkeypatch.setattr(ask,'ask',lambda *a,**k:(json.dumps(dict(attributed=attributed,attribution_text='Interview with the named subject',on_camera=frames)),0))
    result=W.verify(dict(proxy='original',url='https://archive.test/interview',title='Interview',transcript=[]),'Subject',1,4,tmp_path)
    assert result['ok'] is expected


def test_pause_crossfade_keeps_word_clock_on_audio():
    import numpy as np
    import voice
    sr=10000
    x=np.zeros(sr*4,np.float32)
    words=[]
    for i,(a,b) in enumerate([(0.1,0.7),(1.1,1.7),(2.1,2.7),(3.1,3.7)]):
        x[round(a*sr):round(b*sr)]=0.1*np.sin(2*np.pi*220*np.arange(round((b-a)*sr))/sr)
        words.append(dict(w='word.',start=a,end=b,seg=i))
    y,shifted,cuts=voice.tighten(x,sr,words,[{'beat':'setup'}]*4)
    assert abs((len(x)-len(y))/sr-sum(r for _,r in cuts))<1/sr
    for w in shifted:
        at=round((w['start']+0.1)*sr)
        assert np.max(np.abs(y[at:at+100]))>0.09


def test_final_measure_detects_cross_file_source_reuse():
    shots=[dict(clip=clip('download-a',100,110),src=[1,4]),dict(clip=clip('download-b',102,112),src=[0,4])]
    assert C.duplicate_ranges(shots)==[[0,1]]
    shots[1]['src']=[2,4]
    assert C.duplicate_ranges(shots)==[]


def test_research_keeps_new_testimony_without_repeating_a_source(tmp_path):
    ft=dict(lines=[],voices=[],spend=[],credits=[])
    v=dict(after=3,person='Witness',file='v.mp4',wav='v.wav',source={'url':'one'},time_range=[10,15])
    C.add_footage(ft,dict(lines=[],voices=[v,v],credits=[]),tmp_path)
    assert len(ft['voices'])==1
    assert ft['voices'][0]['file']==str(tmp_path/'v.mp4')


def test_short_tail_is_shared_between_adjacent_pictures():
    sc={'segments':[{'say':'Words.','chapter':'c'}]}
    words={'duration':8,'words':[{'w':'Words.','start':0.1,'end':1,'seg':0}]}
    shots,_,_=C.schedule(sc,words,{'lines':[{'i':0,'clips':[clip('a',0,7.8),clip('b',10,15)]}]})
    assert min(r['f'][1]-r['f'][0] for r in shots)>=44


@pytest.mark.parametrize('arguments,topic', [([],None),(['--topic','The first rescue'],'The first rescue')])
def test_same_entry_command_accepts_nothing_or_a_topic(monkeypatch,arguments,topic):
    import importlib.util
    spec=importlib.util.spec_from_file_location('wf4_entry_test',Path(__file__).with_name('run.py'))
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    monkeypatch.setattr(sys,'argv',['run.py',*arguments])
    monkeypatch.setenv('OPENAI_API_KEY','test-not-a-secret')
    monkeypatch.setattr(entry.shell,'main',lambda:list(sys.argv))
    monkeypatch.setattr(entry.shell,'load_workflow',entry.shell.load_workflow)
    got=entry.main();args=json.loads(got[got.index('--args')+1])
    assert got[1]=='run' and args['look'] is False
    assert args['delivered_ledger']=='off'
    assert args.get('line')==topic
    assert got[got.index('--out')+1].endswith('/lanes/WF4-LONGFORM')


def test_failed_witness_search_can_resume_after_adapter_fix(tmp_path,monkeypatch):
    import footage_box as B
    (tmp_path/'footage.json').write_text(json.dumps(dict(voices=[],adapter_sha256='old')))
    def retried(*a,**k):raise RuntimeError('network retry reached')
    monkeypatch.setattr(B,'ssh',retried)
    with pytest.raises(RuntimeError,match='network retry reached'):
        B.fetch({'_voices':True},tmp_path)
    assert (tmp_path/'footage-rejected-old.json').exists()
    assert not (tmp_path/'footage.json').exists()


def test_nested_research_shares_only_its_own_film_cache(tmp_path):
    import footage_box as B
    root=tmp_path/'run-one'/'footage'
    assert B.cache_path(root)==B.cache_path(root/'witnesses')
    assert B.cache_path(root)!=B.cache_path(tmp_path/'run-two'/'footage')


def test_completed_voice_does_not_start_another_model(tmp_path,monkeypatch):
    C.save(tmp_path/'read-stage.json',{'script':{'segments':[]}})
    C.save(tmp_path/'longform_voice.json',{'after':{'ok':True}})
    C.save(tmp_path/'check.json',{'gate':{'pass':True}})
    monkeypatch.setattr(C,'guarded_voice',lambda *a:pytest.fail('completed checkpoint restarted'))
    monkeypatch.setattr(C.PX,'narrate',lambda *a,**k:{'cached':True})
    class Context:run_id='test'
    assert C.narrated(Context(),{'segments':[]},tmp_path,'reader.py')=={'cached':True}


def test_join_level_ride_keeps_time_and_samples_outside_its_envelope():
    import numpy as np
    import join_repair as J
    sr=1000;x=np.ones(6000,dtype=np.float32)
    J.ride(x,sr,[1,2,3,4],[0,-3,-3,0])
    assert len(x)==6000 and np.all(x[:1000]==1) and np.all(x[4000:]==1)
    assert np.allclose(x[2000:3000],10**(-3/20))
    assert np.max(np.abs(np.diff(x)))<.001


def test_narration_phases_exit_before_next_memory_heavy_phase(tmp_path,monkeypatch):
    import argparse
    import longform_voice as V
    script=tmp_path/'script.json';C.save(script,dict(segments=[]))
    calls=[]
    def child(command,**kwargs):
        calls.append(command[2]);C.save(tmp_path/'render.json',{})
    monkeypatch.setattr(V.subprocess,'run',child)
    V.isolated_render(argparse.Namespace(out=str(tmp_path),script=str(script),band='04',rerender=False))
    assert calls==['_render','_shared-finish','finish']


def test_fast_room_floor_preserves_level_and_spectral_shape():
    import numpy as np
    import longform_voice as V
    n=192113;sr=48000;tone=V.room_tone(n,sr,-60)
    assert len(tone)==n and tone.dtype==np.float32
    assert abs(20*np.log10(np.sqrt(np.mean(tone*tone)))+60)<.01
    power=np.abs(np.fft.rfft(tone))**2;freq=np.fft.rfftfreq(n,1/sr)
    high=power[(freq>10000)&(freq<12000)].sum();low=power[(freq>1000)&(freq<2000)].sum()
    assert 10*np.log10(high/low)<-12


def test_rater_sound_measure_separates_bed_and_audible_hit(tmp_path,monkeypatch):
    import numpy as np
    import measure_sound as M
    t=np.arange(M.SR*8)/M.SR
    voice=.1*np.sin(2*np.pi*200*t)
    voice[3*M.SR:round(3.1*M.SR)]+=.5*np.sin(2*np.pi*70*t[:round(.1*M.SR)])
    bed=(.002+.02*t/8)*np.sin(2*np.pi*517*t)
    clean=tmp_path/'clean';clean.write_text('clean')
    final=tmp_path/'final';final.write_text('final')
    monkeypatch.setattr(M,'pcm',lambda p,ff:voice+bed if Path(p)==final else voice)
    plan=tmp_path/'plan.json';C.save(plan,dict(dur_f=240,fps=30,chapters=[dict(id='c1',start=0,end=8)],sound_design=dict(placed=[dict(t=3)])))
    words=tmp_path/'words.json';C.save(words,dict(words=[dict(start=0,end=8)]))
    import stem_evidence as E
    t48=np.arange(E.SR*8)/E.SR;v48=.1*np.sin(2*np.pi*200*t48)
    s48=np.zeros_like(t48);s48[3*E.SR:round(3.1*E.SR)]=.5*np.sin(2*np.pi*70*t48[:round(.1*E.SR)])
    b48=(.002+.02*t48/8)*np.sin(2*np.pi*517*t48)
    arrays={final:v48+s48+b48,tmp_path/'voice.flac':v48,tmp_path/'sfx.flac':s48,tmp_path/'music.flac':b48}
    for p in list(arrays)[1:]:p.write_text(p.name)
    monkeypatch.setattr(E,'pcm',lambda p,ff:arrays[Path(p)])
    r=M.measure(final,clean,plan,words,tmp_path/'measured.json','unused',stem_dir=tmp_path,music_stem=tmp_path/'music.flac')
    assert r['verified'] and r['stems']['T2']>=1
    assert r['music_share']>.8
    assert r['chapter_rises'][0]['rise_db']>3
    assert r['audible_placed']==1
    assert r['blind_events']>=1


def test_sentence_repair_invalidates_only_changed_takes(tmp_path):
    import read_repair as R
    nd=tmp_path/'voice';(nd/'_takes').mkdir(parents=True);(nd/'take-analysis').mkdir()
    ns=dict(title='Test',names=[],band='04',segments=[dict(text=' '.join(['alpha']*35)+'.'),dict(text=' '.join(['beta']*35)+'.')])
    old=R.voice.group_script(ns,R.voice.groups_of(ns['segments']))
    C.save(nd/'_generations.json',old)
    (nd/'_takes/s00_t0.wav').write_bytes(b'old first take')
    (nd/'_takes/s01_t0.wav').write_bytes(b'unchanged second take')
    ns['segments'][0]['text']=' '.join(['gamma']*35)+'.'
    backup=tmp_path/'rejected';backup.mkdir()
    r=R.prepare_takes(nd,ns,backup)
    assert r['changed_generations']==[0]
    assert (backup/'s00_t0.wav').read_bytes()==b'old first take'
    assert not (nd/'_takes/s00_t0.wav').exists()
    assert (nd/'_takes/s01_t0.wav').read_bytes()==b'unchanged second take'


def test_unresolved_preflight_does_not_erase_existing_source_candidates(tmp_path,monkeypatch):
    import archive_visual as A
    from types import SimpleNamespace
    proposal=dict(script=dict(segments=[dict(say='A scarce event.',visual_beats=[dict(word_start=0,archive_ids=['a'])])]))
    catalogue=[dict(id='a',usable=True,visible='view',form='still',real_moving_footage=False,limitations=[],clip=dict(seconds=7.8,source={}))]
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda *args:None)
    root=A.work_dir(ctx,proposal,catalogue);root.mkdir(parents=True)
    C.save(root/'edit-0.json',dict(segments=[dict(index=0,visual_beats=[dict(word_start=0,archive_ids=[])])]))
    monkeypatch.setattr(A,'preview',lambda *a:([],[dict(seg=0,seconds=2)],3))
    monkeypatch.setattr(C,'source_review',lambda *a,**k:dict(ok=True,rejected=[],moving_share=0,evidence={}))
    result=A.refine(ctx,proposal,catalogue)
    assert result['script']['segments'][0]['say']=='A scarce event.'
    assert result['script']['segments'][0]['visual_beats'][0]['archive_ids']==['a']
    assert result['preflight']['complete'] is False
    assert result['preflight']['gaps']==[dict(seg=0,seconds=2)]


def test_published_caption_keeps_exact_description_and_excludes_other_rows():
    from caption_context import CommonsDescription
    p=CommonsDescription()
    p.feed('<table><tr><td id="fileinfotpl_desc">Description</td><td>NASA: <b>two</b> astronauts.<br>Original art.</td></tr><tr><td>Unrelated licence navigation</td></tr></table>')
    assert p.description=='Description NASA: two astronauts. Original art.'


def test_prior_draft_searches_do_not_count_for_revised_words(tmp_path):
    C.save(tmp_path/'old/footage.json',dict(lines=[dict(i=3,say='Previous sentence.',clips=[])],counts=dict(frames_checked=20,sources_opened=2)))
    C.save(tmp_path/'current/footage.json',dict(lines=[dict(i=3,say='Revised sentence.',clips=[])],counts=dict(frames_checked=20,sources_opened=2)))
    assert C.video_search_counts(tmp_path,{3:'Revised sentence.'})=={3:1}


def test_visual_repreflight_preserves_newly_researched_sources():
    from archive_visual import apply_choices
    old=clip('catalogued',0,5);fresh=clip('newly-researched',0,5)
    sc=dict(script=dict(segments=[dict(say='Unchanged words.',visual_beats=[])]))
    ft=dict(lines=[dict(i=0,clips=[old,fresh])],voices=[])
    cat=[dict(id='a',usable=True,clip=old)]
    revised=dict(script=dict(segments=[dict(say='Unchanged words.',visual_beats=[dict(word_start=0,archive_ids=['a'])])]))
    apply_choices(sc,ft,revised,cat)
    assert [c['path'] for c in ft['lines'][0]['clips']]==['catalogued','newly-researched']


def test_visual_repair_retains_checked_interval_when_paragraph_choices_change():
    import archive_visual as A
    import copy
    sc=dict(segments=[dict(say='one two three four five six seven eight nine ten eleven twelve',chapter='c',
                          visual_beats=[dict(word_start=0,archive_ids=['good']),dict(word_start=6,archive_ids=['bad'])])])
    cat=[dict(id=k,usable=True,clip=dict(clip(k,a,a+4),catalogue_id=k))
         for k,a in [('good',0),('bad',10),('replacement',20)]]
    shots,_,_=A.preview(sc,cat)
    report=dict(evidence={str(i):dict(matches=r['clip']['path']=='good') for i,r in enumerate(shots)})
    pins=A.checked_pins(sc,shots,report)
    changed=copy.deepcopy(sc)
    changed['segments'][0]['visual_beats']=[dict(word_start=0,archive_ids=['replacement'])]
    revised,gaps,_=A.preview(changed,cat,3.3,pins)
    assert not gaps
    assert [r['clip']['path'] for r in revised]==['good','replacement']
    assert revised[0]['f']==shots[0]['f']
    assert not C.duplicate_ranges(revised)


def test_hold_gate_uses_detected_shots_even_if_plan_would_split_them(tmp_path,monkeypatch):
    import measure_picture as M
    import numpy as np
    import sys
    from types import SimpleNamespace
    (tmp_path/'edit_delta.py').write_text('test rater')
    monkeypatch.setattr(M,'RATER',tmp_path)
    film=tmp_path/'film.mp4';film.write_bytes(b'encoded picture')
    fake=SimpleNamespace(probe=lambda p:{},cut_analysis=lambda *a:dict(gray=np.zeros((160,2,2)),fps=10,duration=16,cuts=[]),
                         shots_from=lambda *a:[(0,16)],camera_moves=lambda *a:dict(moves=0))
    monkeypatch.setitem(sys.modules,'edit_delta',fake)
    result=M.measure(film,tmp_path/'result.json')
    assert result['cuts']==0 and result['holds_ok'] is False
    assert result['holds_over_8s_without_camera_move'][0]['seconds']==16
