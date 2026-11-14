import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cinema as C


def clip(name,a,b):
    return dict(path=name,source={'url':'https://archive.test/one'},time_range=[a,b],seconds=b-a,w=1920,h=1080)


def test_renamed_download_cannot_replay_source_seconds():
    used={}
    assert C.reserve(used,clip('a',0,8),4)==0
    assert C.reserve(used,clip('b',0,8),4)==4
    assert C.reserve(used,clip('c',0,8),1) is None


def test_coverage_failure_is_explicit():
    sc={'segments':[{'say':'The real words.','chapter':'c'}]}
    words={'duration':8,'words':[{'w':'The','start':0.1,'end':1,'seg':0}]}
    with pytest.raises(C.CoverageError) as e:
        C.schedule(sc,words,{'lines':[{'i':0,'clips':[clip('a',0,2)]}]})
    assert e.value.gaps[0]['seconds']>5


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
    r=M.measure(final,clean,plan,words,tmp_path/'measured.json','unused')
    assert r['music_share']>.8
    assert r['chapter_rises'][0]['rise_db']>3
    assert r['audible_placed']==1
    assert r['blind_events']>=1
