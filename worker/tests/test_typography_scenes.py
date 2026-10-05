import copy
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent_tools
import captions
import graphics
import stitch
import typography_scenes as scenes
from schemas import default_edl, validate_edl, edl_signature
from timeline import Timeline, remap_program_items


def design(**kw):
    return scenes.compose(id='focus',start=0,end=2,
        lines=[{'runs':[{'text':'Your','font':'Instrument Serif','italic':True}]},
               {'runs':[{'text':'only','at':.4},{'text':'focus','at':.8,'color':'#C6B999'}]}],
        font_size=.13,box=[.08,.2,.92,.65],**kw)


def test_group_is_editable_registered_and_replaces_atomically():
    class Ctx:
        index={'video':{'width':1080,'height':1920}}
        def __init__(self): self.edl=default_edl(3);self.n=0
        def latest_edl(self): return {'json':self.edl}
        def write_edl(self,e,d):
            self.edl=validate_edl(e,3).model_dump();self.n+=1;return 'EDL v2'
    ctx=Ctx()
    agent_tools.add_text(ctx,'Independent topic',0,3,mute_captions=False)
    original=copy.deepcopy(ctx.edl['texts'])
    args=dict(id='focus',start=0,end=2,lines=[{'runs':[{'text':'One','at':0},{'text':'idea','at':.5}]}])
    assert agent_tools.set_typography_scene(ctx,**args).startswith('EDL v')
    assert len(ctx.edl['texts'])==3
    assert captions.effective_caption_mutes(ctx.edl)==[[0,2]]
    args['lines']=[{'runs':[{'text':'Another idea'}]}]
    assert agent_tools.set_typography_scene(ctx,**args).startswith('EDL v')
    assert len(ctx.edl['texts'])==2
    assert agent_tools.remove_typography_scene(ctx,'focus').startswith('EDL v')
    assert ctx.edl['texts']==original and captions.effective_caption_mutes(ctx.edl)==[]
    assert 'set_typography_scene' in agent_tools.RECIPE_TOOLS
    assert 'set_typography_scene' in agent_tools.WRITE_TOOLS
    assert 'set_typography_scene' in agent_tools.TOOL_DOMAINS['graphics']


@pytest.mark.parametrize('dims',[(1080,1920),(1920,1080),(1080,1080)])
@pytest.mark.parametrize('align',['left','center','right'])
def test_layout_reserves_complete_phrase_and_fits(dims,align):
    W,H=dims
    result=design(W=W,H=H,align=align)
    e=default_edl(2);e['texts']=result['texts'];validate_edl(e,2)
    boxes=[graphics._compile_item(t,2,dims) for t in result['texts']]
    for b in boxes:
        assert .075*W<=b['left']<b['right']<=.925*W
        assert .195*H<=b['top']<b['bottom']<=.655*H
    assert boxes[1]['right']<boxes[2]['left']
    assert result['texts'][1]['start']==.4 and result['texts'][2]['start']==.8
    assert len({t['end'] for t in result['texts']})==1


def test_legacy_signatures_and_quote_italics_unchanged():
    e=default_edl(3);e['texts']=[dict(id='q',text='An old quotation',start=0.,end=3.,template='quote')]
    valid=validate_edl(e,3).model_dump()
    assert edl_signature(e)==edl_signature(valid)
    assert graphics._compile_item(e['texts'][0],3,(1080,1920))==graphics._compile_item(valid['texts'][0],3,(1080,1920))


def test_bad_scene_rejects_instead_of_silent_font_shrink_or_flash():
    for kw in [{'font_size':float('nan')},{'end':.4},{'box':[.2,.2,.21,.3]},
               {'lines':[{'runs':[{'text':'word','at':1.9}]}]},
               {'lines':[{'runs':[{'text':'word','bogus':1}]}]}]:
        args=dict(id='bad',start=0,end=2,lines=[{'runs':[{'text':'A very long line that cannot fit here'}]}],font_size=.1)
        args.update(kw)
        with pytest.raises((ValueError,TypeError)):scenes.compose(**args)


def test_completed_run_stays_pixel_stationary_as_later_run_arrives(tmp_path):
    result=design(W=360,H=640)
    e=default_edl(2);e['frame']={'ratio':'9:16'};e['texts']=result['texts']
    validate_edl(e,2)
    path=tmp_path/'type.ass'
    graphics.build_gfx_ass(e,2,str(path),play_res=(360,640))
    raw=subprocess.check_output(['ffmpeg','-v','error','-f','lavfi','-i',
        'color=black:s=360x640:r=25:d=2','-vf',f"ass='{path}':fontsdir='{graphics.FONTS_DIR}'",
        '-f','rawvideo','-pix_fmt','rgb24','-'],timeout=30)
    frames=np.frombuffer(raw,np.uint8).reshape(-1,640,360,3)
    # The serif first row has settled by 0.3 seconds. Neither subsequent
    # word may relocate it, hide it, blur it, or repeat its entrance.
    top=frames[:,100:245]
    assert top[8].max()>200
    assert np.array_equal(top[8],top[28])
    assert frames[28,245:400].max()>200
    # Proof windows should not restart the completed scene's entrances.
    proof=stitch.window_edl(e,Timeline(e['keep']),.9,1.8)
    validate_edl(proof,2)
    for t in proof['texts']:
        assert not isinstance((t.get('motion') or {}).get('opacity'),list) or (t['motion']['opacity'][0]['v']==1)


def test_authored_italic_and_measured_alignment_survive_roundtrip():
    e=default_edl(2);e['texts']=design()['texts']
    v=validate_edl(e,2).model_dump()
    a=v['texts'][0]
    assert a['italic'] is True and a['tracking']==0 and a['text_align']=='center'
    assert r'\i1' in graphics._compile_item(a,2,(1080,1920))['text']


def test_taste_checks_motion_envelopes_not_number_of_phrase_runs():
    import taste
    runs=design()['texts']
    for i,a in enumerate(runs):
        for b in runs[i+1:]:
            assert taste._separate_authored_text(a,b,2,(1080,1920))
    # Removing the blanket movement warning must not hide an actual sweep
    # through another run, or a rotated design that needs rendered review.
    a,b=copy.deepcopy(runs[1:])
    a['motion']['x']=[{'t':0,'v':a['x']},{'t':1,'v':b['x']}]
    assert not taste._separate_authored_text(a,b,2,(1080,1920))
    a=copy.deepcopy(runs[0]);a['motion']['rotation']=20
    assert not taste._separate_authored_text(a,runs[2],2,(1080,1920))
