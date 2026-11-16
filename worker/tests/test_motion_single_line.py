"""The single-row caption contract must reach browser type, without lost words."""
import asyncio
import pytest
import caption_place
import motion_captions
import motion_templates
from test_motion_captions import _setup, _dom, needs_browser, WORDS


@pytest.mark.parametrize('look', list(motion_captions.LOOKS))
def test_motion_single_row_preserves_words_and_measures_one_row(look):
    edl,index,tl=_setup(look,style={'single_line':True,'size_scale':1.6})
    cues=motion_captions.cues(edl,index,tl)
    assert [w['t'].casefold().rstrip('.,') for c in cues for w in c['w']] == [w.casefold().rstrip('.,') for w,_,_ in WORDS]
    assert all(c.get('n')==1 for c in cues)
    one,tall,_=caption_place.block_metrics(edl,720,1280)
    assert one <= tall < one*1.1
    # A stored false/absent contract keeps the look's authored phrase layout.
    edl['captions']['style']['single_line']=False
    assert not any(c.get('n') for c in motion_captions.cues(edl,index,tl))


@needs_browser
@pytest.mark.parametrize('look',['clean','serif','editorial','lockup','stack'])
def test_motion_single_row_uses_a_real_row_at_readable_size(look):
    edl,index,tl=_setup(look,style={'single_line':True,'size_scale':1.6,'anchor_y':.15})
    items=motion_captions.items(edl,index,tl,canvas=(540,960))
    script="""() => Array.from(document.querySelectorAll('.cue.on > .blk')).map(b => ({
      rect: [b.getBoundingClientRect().top,b.getBoundingClientRect().bottom],
      words: Array.from(b.querySelectorAll('.mg-w')).filter(w=>!w.closest('.ink')).map(w=>{
        const r=w.getBoundingClientRect();return [r.left,r.right,r.top,r.bottom];})
    }))"""
    for item in items:
        job=motion_templates.build_job(item,540,960,30)
        times=[min(c['e']-.03,c['w'][-1]['s']+.15) for c in item['params']['cues']]
        for rows in asyncio.run(_dom(job,times,script)):
            assert len(rows)==1
            words=rows[0]['words'];assert words
            # Reading order cannot wrap to the left onto another row.
            assert all(b[0]>=a[1]-2 for a,b in zip(words,words[1:])),rows
            assert max(w[3] for w in words)-min(w[2] for w in words)<.12*1920,rows
            assert min(w[3]-w[2] for w in words)>25,rows
            assert rows[0]['rect'][0]>=.08*1920-1 and rows[0]['rect'][1]<=.8*1920+1
