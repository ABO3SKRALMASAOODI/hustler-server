import copy
import json
from types import SimpleNamespace
import pytest
import wf1_alignment as A


def index():
    words=[dict(w=w,t0=a,t1=b) for w,a,b in [('sense',0,.2),('The',.3,.5),('mote',.6,.9),('last.',9.5,9.8)]]
    return dict(video=dict(duration=10),words=words,sentences=[dict(id='s1',wi0=0,wi1=3,t0=0,t1=9.8,text='sense The mote last.')],shots=[])


def test_hook_trim_and_spelling_keep_measured_interior_timing():
    original=index();before=copy.deepcopy(original)
    out,(a,b)=A.transform(original,dict(first=1,last=3,corrections=[dict(id=2,text='moat')]))
    assert original==before
    assert (a,b)==pytest.approx((.24,9.92))
    assert [w['w'] for w in out['words']]==['The','moat','last.']
    assert out['words'][1]['t0']==pytest.approx(.6-a)
    assert out['sentences'][0]['text']=='The moat last.'
    assert out['sentences'][0]['wi1']==2


@pytest.mark.parametrize('decision',[
    dict(first=3,last=3),dict(first=0,last=1),
    dict(first=0,last=3,corrections=[dict(id=1,text='a new claim')]),
    dict(first=1,last=3,corrections=[dict(id=0,text='hidden')])])
def test_alignment_cannot_rewrite_or_remove_the_interior(decision):
    with pytest.raises(ValueError):A.transform(index(),decision)


def test_reconcile_recuts_product_source_and_reuses_bound_result(tmp_path):
    src=tmp_path/'source.mp4';src.write_bytes(b'old source')
    (tmp_path/'coverage.json').write_text('{}')
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path))
    script=dict(source='original.mp4',window=[100,110],hook_line='The moat',close_line='last.')
    calls=[]
    def ask(*args,**kwargs):
        calls.append(1)
        return json.dumps(dict(first=1,last=3,corrections=[dict(id=2,text='moat')])),dict(usd=0)
    def cut(ctx,source,a,b,out):
        assert source=='original.mp4' and (a,b)==pytest.approx((100.24,109.92))
        from pathlib import Path
        Path(out).write_bytes(b'aligned source')
    llm=SimpleNamespace(ask=ask,json_block=json.loads)
    chain=SimpleNamespace(cut=cut,brand=SimpleNamespace(probe=lambda _:dict(dur=9.68)),shots_of=lambda *a:[])
    out,changed=A.reconcile(ctx,script,index(),[],llm,chain)
    assert changed and script['window']==pytest.approx([100.24,109.92])
    assert (tmp_path/'coverage.json.before-alignment').exists()
    assert not (tmp_path/'coverage.json').exists()
    again,changed=A.reconcile(ctx,script,out,[],llm,chain)
    assert not changed and again==out and len(calls)==1


def test_run_tokens_join_a_split_token_on_the_run_transcripts_boundary():
    """Round1 default-design: the window heard '49' '%' and 'self' '-critical.'; the run transcript has '49%' and
    'self-critical.'. The caption tokens become the run's, timed from the words they cover; nothing else moves."""
    words=[dict(w=w,t0=a,t1=b) for w,a,b in [('And',0.0,.2),('49',.3,.5),('%',.5,.7),('are',.8,.9),
                                              ('very',1.0,1.2),('self',1.3,1.5),('-critical.',1.5,1.9),('in',2.0,2.1),('to',2.1,2.2)]]
    idx=dict(video=dict(duration=3),words=words,sentences=[dict(id='s1',wi0=0,wi1=4,t0=0,t1=1.2,text='x'),
                                                           dict(id='s2',wi0=5,wi1=8,t0=1.3,t1=2.2,text='y')])
    run=[(100.0,'And'),(100.32,'49%'),(100.8,'are'),(101.0,'very'),(101.35,'self-critical.'),(102.0,'in'),(102.1,'to')]
    out,n=A.run_tokens(idx,run,100.0)
    assert n==2
    assert [w['w'] for w in out['words']]==['And','49%','are','very','self-critical.','in','to']
    assert out['words'][1]['t0']==.3 and out['words'][1]['t1']==.7
    assert out['words'][4]['t0']==1.3 and out['words'][4]['t1']==1.9
    assert [(s['wi0'],s['wi1']) for s in out['sentences']]==[(0,3),(4,6)]
    assert out['sentences'][1]['text']=='self-critical. in to'
    assert idx['words'][1]['w']=='49'                       # the input is not touched
    # words the run transcript keeps apart stay apart; a token far in time is not this one
    same,n0=A.run_tokens(idx,[(100.32,'49'),(100.5,'%'),(105.0,'self-critical.')],100.0)
    assert n0==0 and same is idx
    # a token split across two sentences ('self' ends one, '-critical.' opens the next) is captioned once, in the first
    words2=[dict(w=w,t0=a,t1=b) for w,a,b in [('very',0.0,.2),('self',.3,.5),('-critical.',.5,.9),('Then',1.0,1.2)]]
    idx2=dict(video=dict(duration=2),words=words2,sentences=[dict(id='s1',wi0=0,wi1=1,t0=0,t1=.5,text='very self'),
                                                             dict(id='s2',wi0=2,wi1=3,t0=.5,t1=1.2,text='-critical. Then')])
    out2,n2=A.run_tokens(idx2,[(100.0,'very'),(100.3,'self-critical.'),(101.0,'Then')],100.0)
    assert n2==1 and [(s['wi0'],s['wi1'],s['text']) for s in out2['sentences']]==[(0,1,'very self-critical.'),(2,2,'Then')]
    assert out2['sentences'][0]['t1']==.9 and out2['sentences'][1]['t0']==1.0
    flat=[out2['words'][q]['w'] for s in out2['sentences'] for q in range(s['wi0'],s['wi1']+1)]
    assert flat==['very','self-critical.','Then']                              # each word once (supply's words.json)
    # a sentence that was only the tail of the token is folded into the one before it
    idx3=dict(video=dict(duration=2),words=words2,sentences=[dict(id='s1',wi0=0,wi1=1,t0=0,t1=.5,text='a'),dict(id='s2',wi0=2,wi1=2,t0=.5,t1=.9,text='b'),
                                                             dict(id='s3',wi0=3,wi1=3,t0=1.0,t1=1.2,text='c')])
    out3,_=A.run_tokens(idx3,[(100.0,'very'),(100.3,'self-critical.'),(101.0,'Then')],100.0)
    assert [(s['id'],s['wi0'],s['wi1']) for s in out3['sentences']]==[('s1',0,1),('s3',2,2)] and out3['sentences'][0]['t1']==.9
