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
        assert source=='original.mp4' and (a,b)==pytest.approx((100.24,110.8))
        from pathlib import Path
        Path(out).write_bytes(b'aligned source')
    llm=SimpleNamespace(ask=ask,json_block=json.loads)
    chain=SimpleNamespace(cut=cut,brand=SimpleNamespace(probe=lambda _:dict(dur=9.68)),shots_of=lambda *a:[])
    out,changed=A.reconcile(ctx,script,index(),[],llm,chain)
    assert changed and script['window']==pytest.approx([100.24,110.8])
    assert (tmp_path/'coverage.json.before-alignment').exists()
    assert not (tmp_path/'coverage.json').exists()
    again,changed=A.reconcile(ctx,script,out,[],llm,chain)
    assert not changed and again==out and len(calls)==1


def test_out_point_keeps_a_breath_after_the_last_word():
    """19 Nov: the card dissolved over the payoff word in all 12 films; the clip now holds a breath after it."""
    end,fade=A.out_point(index(),dict(first=1,last=3),[],100)
    assert end==pytest.approx(100+9.8+A.BREATH_S) and fade is None


def test_out_point_fades_out_before_a_word_the_clip_does_not_keep():
    idx=index();idx['words'].append(dict(w='And',t0=10.1,t1=10.3))
    end,fade=A.out_point(idx,dict(first=1,last=3),[],100)
    assert end==pytest.approx(110.8)
    assert 109.8<fade<110.1-A.FADE_S+1e-6
    # past the heard window, the source captions say where the next word starts
    end,fade=A.out_point(index(),dict(first=1,last=3),[(109.0,'last.'),(110.3,'Next')],100)
    assert end==pytest.approx(110.8) and 109.8<fade<110.3-A.FADE_S+1e-6


def test_reconcile_passes_the_fade_to_the_cut(tmp_path):
    src=tmp_path/'source.mp4';src.write_bytes(b'old source')
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path))
    script=dict(source='original.mp4',window=[100,110],hook_line='The moat',close_line='last.')
    seen={}
    def ask(*args,**kwargs):
        return json.dumps(dict(first=1,last=3)),dict(usd=0)
    def cut(ctx,source,a,b,out,fade_at=None):
        seen.update(a=a,b=b,fade_at=fade_at)
        from pathlib import Path
        Path(out).write_bytes(b'aligned source')
    llm=SimpleNamespace(ask=ask,json_block=json.loads)
    chain=SimpleNamespace(cut=cut,brand=SimpleNamespace(probe=lambda _:dict(dur=10.56)),shots_of=lambda *a:[])
    A.reconcile(ctx,script,index(),[(109.0,'last.'),(110.2,'And')],llm,chain)
    assert seen['b']==pytest.approx(110.8)
    assert seen['fade_at'] is not None and 0<seen['fade_at']<110.2-100.24
