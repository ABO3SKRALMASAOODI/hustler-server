import copy
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editorial_graphics
import captions
import graphics
import picture_cards
import renderer
import stitch
from schemas import EDLValidationError, default_edl, validate_edl
from timeline import Timeline, remap_program_items


def sample():
    e=default_edl(4)
    e['frame']={'ratio':'9:16','mode':'pad','picture':[.06,.25,.94,.75]}
    e['effects']={'picture_cards':[{'id':'p','start':1,'end':3,
        'box':[.1,.3,.9,.7],'radius':.12,'entrance':'reveal','exit':'reveal',
        'background':'#101012','border':.002,'duration_s':.6}]}
    e['texts']=[dict(id='label',text='Independent type',start=0,end=4,
       template='title',font_size=.065,max_width=.9,x=.5,y=.15,
       uppercase=False,entrance='none',exit='none',outline_width=0,shadow=0)]
    return validate_edl(e,4).model_dump()


def timeline(e):
    return Timeline(e['keep'], e.get('inserts') or [], e.get('speed') or [])


def test_invalid_geometry_and_overlapping_cards_rejected():
    for bad in ([0,0,float('nan'),1],[.4,.2,.3,.8],[0,0,1,2]):
        e=sample();e['effects']['picture_cards'][0]['box']=bad
        with pytest.raises((ValueError,EDLValidationError)):
            validate_edl(e,4)
    e=sample();e['effects']['picture_cards'].append({**e['effects']['picture_cards'][0],'id':'q'})
    with pytest.raises(EDLValidationError,match='overlap'):
        validate_edl(e,4)


def test_plate_has_transparent_picture_opaque_corners_and_antialiasing(tmp_path):
    spec=sample()['effects']['picture_cards'][0]
    path=picture_cards.build_plate(str(tmp_path/'plate.png'),320,568,spec)
    a=np.asarray(Image.open(path))
    assert a[284,160,3]==0
    assert a[170,32,3]>245
    assert np.any((a[:,:,3]>0)&(a[:,:,3]<255))


@pytest.mark.parametrize('kind',editorial_graphics.KINDS)
@pytest.mark.parametrize('dims',[(1080,1920),(1920,1080),(1080,1080)])
def test_compositions_fit_and_keep_one_editable_hierarchy(kind,dims):
    W,H=dims
    design=editorial_graphics.compose(id='test',kind=kind,text='$8 million' if kind=='metric' else 'Build something people need',
        secondary='Make it matter',eyebrow='The idea',start=0,end=6,W=W,H=H)
    e=default_edl(6);e.update({k:design[k] for k in ('texts','vectors')})
    validate_edl(e,6)
    boxes=[graphics._compile_item(t,6,(W,H)) for t in design['texts']]
    for box in boxes:
        assert box['left']>=0 and box['right']<=W and box['top']>=0 and box['bottom']<=H
    for a,b in zip(sorted(boxes,key=lambda b:b['top']),sorted(boxes,key=lambda b:b['top'])[1:]):
        assert a['bottom']<=b['top'],f'{kind}: type levels overlap'
    assert all(t['shadow']==0 and t['outline_width']==0 for t in design['texts'])
    assert all(not t['mute_captions'] for t in design['texts'])


def test_short_hold_and_dense_label_rejected():
    with pytest.raises(ValueError,match='read'):
        editorial_graphics.compose(id='x',kind='statement',text='This is a long sentence that needs a readable hold',start=0,end=1)
    with pytest.raises(ValueError,match='dense'):
        editorial_graphics.compose(id='x',kind='label',text='These words do not belong in a tiny badge',start=0,end=6,box=[.1,.1,.22,.22])


def test_atomic_group_replacement_and_removal_preserve_other_work():
    import agent_tools

    class Context:
        index = {'video': {'width': 1920, 'height': 1080}}
        edit_plan = None
        def __init__(self):
            self.edl = sample()
            self.count = 0
        def latest_edl(self):
            return {'json': self.edl}
        def write_edl(self, edl, description):
            self.edl = validate_edl(edl, 4).model_dump()
            self.count += 1
            return 'saved'

    ctx = Context()
    before = copy.deepcopy(ctx.edl)
    assert agent_tools.set_editorial_graphic(ctx, 'thesis', 'statement', 'One clear idea', 0, 3, mute_captions=True) == 'saved'
    assert any(t['mute_captions'] for t in ctx.edl['texts'] if t['id'].startswith('eg_thesis__'))
    assert agent_tools.set_editorial_graphic(ctx, 'thesis', 'label', 'Revised idea', 0, 3, treatment='type') == 'saved'
    assert ctx.count == 2
    group = [t for t in ctx.edl['texts'] if t['id'].startswith('eg_thesis__')]
    assert len(group) == 1 and not group[0]['mute_captions']
    assert not [v for v in ctx.edl['vectors'] if v['id'].startswith('eg_thesis__')]
    assert ctx.edl['effects'] == before['effects']
    assert next(t for t in ctx.edl['texts'] if t['id']=='label') == before['texts'][0]
    assert agent_tools.remove_editorial_graphic(ctx, 'thesis') == 'saved'
    assert ctx.edl['texts'] == before['texts']
    assert agent_tools.set_picture_card(ctx, 'p', 1, 3, entrance='none') == 'saved'
    assert len(ctx.edl['effects']['picture_cards']) == 1
    assert agent_tools.set_picture_card(ctx, 'p', 1, 3, motion_motif='hold').startswith('REJECTED')
    assert ctx.count == 4


def test_composed_captions_show_the_phrase_and_highlight_current_word(tmp_path):
    e=default_edl(3)
    e['captions']={'mode':'from_transcript','design_version':2,
        'min_words_per_caption':3,'max_words_per_caption':4,
        'emphasis_mode':'manual','emphasis_words':['idea'],
        'style':{'preset':'composed','dynamic':True,'highlight_color':'#D5C5AA'}}
    idx={'words':[{'w':w,'t0':i*.5,'t1':i*.5+.4} for i,w in enumerate(['It','is','one','idea'])]}
    path=str(tmp_path/'captions.ass')
    captions.build_ass(validate_edl(e,3).model_dump(),idx,timeline(e),path)
    events=[line for line in Path(path).read_text().splitlines() if line.startswith('Dialogue:')]
    first=[line for line in events if ',0:00:00.00,' in line]
    # Complete phrase exists at its start; semantic emphasis and the active
    # word's colour remain independent, with no bounce/scale transforms.
    assert all(any(w in line for line in first) for w in ['It','is','one','idea'])
    assert any('&HAAC5D5&' in line for line in first)
    assert all('\\t(' not in line for line in first)
    assert any('\\fs56' in line for line in first)


def test_fragment_keeps_animation_phase_and_edit_clamps_cards():
    e=sample();tl=timeline(e)
    proof=stitch.window_edl(e,tl,1.2,2.7)
    c=proof['effects']['picture_cards'][0]
    assert c['phase_s']==pytest.approx(.2) and c['full_duration_s']==2
    validate_edl(proof,4)
    shorter=copy.deepcopy(e);shorter['keep']=[[0,2]]
    remap_program_items(shorter,tl,timeline(shorter))
    assert shorter['effects']['picture_cards'][0]['end']==2
    validate_edl(shorter,4)


def render_fixture(tmp_path,e,name):
    W,H=320,568
    source=tmp_path/'source.mp4'
    if not source.exists():
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0xCC5522:s=320x180:r=30:d=4',
                         '-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    tl=timeline(e)
    args=['-i',str(source),'-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    inputs,_=picture_cards.prepare_inputs(e,str(tmp_path),W,H,30,args,2)
    gfx=graphics.build_gfx_ass(e,tl.out_duration,str(tmp_path/f'{name}.ass'),play_res=(W,H))
    graph=renderer.build_filtergraph(e,4,False,tl,None,[],{},False,W=W,H=H,fps=30,
              frame_mode='pad',src_w=320,src_h=180,silence_idx=1,gfx_ass_path=gfx,picture_card_inputs=inputs)
    output=tmp_path/f'{name}.mp4'
    result=subprocess.run(['ffmpeg','-v','error','-y','-filter_complex_threads','1',*args,'-filter_complex',graph,
                   '-map','[vout]','-map','[aout]','-t',str(tl.out_duration),'-c:v','libx264','-preset','ultrafast','-crf','10',
                   '-pix_fmt','yuv420p','-c:a','aac',str(output)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr[-6000:]
    return output


def frame(path,t):
    data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    return np.frombuffer(data,np.uint8).reshape(568,320,3)


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required for pixel proof')
def test_render_rounded_picture_sharp_type_and_fragment_phase(tmp_path):
    e=sample();out=render_fixture(tmp_path,e,'full')
    before,opening,settled=frame(out,.5),frame(out,1.1),frame(out,2)
    assert settled[170,32].max()<55 # rounded corner is backdrop, not footage
    assert settled[280,160,0]>150 # picture remains visible inside its card
    assert opening[215,160,0]<60 and settled[215,160,0]>150 # actual reveal
    # The same label occupies the same delivery pixels before/inside the card.
    assert np.logical_xor(before[60:110].min(axis=2)>180,
                         settled[60:110].min(axis=2)>180).mean()<.01
    proof=stitch.window_edl(e,timeline(e),1.2,2.7)
    partial=render_fixture(tmp_path,proof,'partial')
    assert np.abs(frame(out,1.3).astype(float)-frame(partial,.1)).mean()<4
