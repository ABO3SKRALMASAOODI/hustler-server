import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cinema as C


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


def test_checked_picture_follows_its_word_when_narration_timing_changes():
    import timeline_repair as R
    sc={'segments':[{'say':'Alpha beta.','chapter':'c'}]}
    original={'duration':4,'words':[dict(w='Alpha',start=.1,end=1,seg=0),dict(w='beta.',start=2,end=3.8,seg=0)]}
    changed={'duration':5,'words':[dict(w='Alpha',start=.1,end=1,seg=0),dict(w='beta.',start=3,end=4.8,seg=0)]}
    a=C.F.Clock(original,sc);b=C.F.Clock(changed,sc)
    anchor=R.anchor(a,2.1)
    assert abs(R.resolve(b,anchor)-3.1)<1e-6


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
    C.save(tmp_path/'initial/footage.json',dict(lines=[dict(i=3,clips=[])]))
    assert not C.photo_research_ready(tmp_path,gap,100)
    C.save(tmp_path/'second/footage.json',dict(lines=[dict(i=3,clips=[])]))
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
    C.save(tmp_path/'longform_voice.json',{'after':{'ok':True}})
    C.save(tmp_path/'check.json',{'gate':{'pass':True}})
    monkeypatch.setattr(C,'guarded_voice',lambda *a:pytest.fail('completed checkpoint restarted'))
    monkeypatch.setattr(C.PX,'narrate',lambda *a,**k:{'cached':True})
    class Context:run_id='test'
    assert C.narrated(Context(),{'segments':[]},tmp_path,'reader.py')=={'cached':True}


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
