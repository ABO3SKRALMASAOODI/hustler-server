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
