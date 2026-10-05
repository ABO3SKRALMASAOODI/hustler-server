"""Exercise authoring and media evidence as tools, not quality-score proxies."""
from pathlib import Path
import sys
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'worker'))
sys.path.insert(0,str(ROOT/'plugins/valmera/skills/valmera-podcast-shorts/scripts'))
from design_graphics import compile_design
from compose_short import compile_short
from inspect_cut import inspect
from schemas import default_edl
from edit_batch import apply_batch
import graphics


def test_short_compiler_keeps_source_clock_and_places_cues_after_cuts():
    index={'video':{'duration':120},'words':[{'w':'first','t0':10,'t1':10.5},
        {'w':'removed','t0':18,'t1':18.5},{'w':'payoff','t0':32,'t1':32.5}]}
    plan={'id':'test','keep':[[10,15],[30,35]],'emphasis_words':['payoff'],
        'graphics':{'beats':[{'kind':'word','text':'The payoff',
            'source_start':32,'source_end':34}]}}
    result=compile_short(plan,index)
    assert [w['w'] for w in result['program_words']]==['first','payoff']
    assert result['program_words'][1]['start']==7
    assert result['cues'][0]['start']==7
    assert result['edl']['texts'][0]['start']==7
    actual=apply_batch(default_edl(120),result['operations'],120,set())
    assert actual['frame']['picture']==[0,.2890625,1,.7109375]
    assert actual['captions']['style']['dynamic'] is True
    plan['graphics']['beats'][0]['source_start']=14
    with pytest.raises(ValueError,match='crosses a cut'):compile_short(plan,index)


def storyboard():
    return {'id':'example','duration_s':9,'headline':{'text':'What changes the outcome?'},
      'beats':[{'kind':'word','text':'One decision','start':0,'end':2},
               {'kind':'contrast','before':'More answers','after':'Better questions','start':2,'end':5},
               {'kind':'metric','value':'10×','label':'More work completed','start':5,'end':9}]}


def test_design_batch_is_editable_and_revision_preserves_unrelated_work(tmp_path):
    existing=default_edl(9)
    existing['texts']=[{'id':'owner-note','text':'Owner note','start':8.,'end':9.}]
    first=compile_design(storyboard(),existing)
    result=apply_batch(existing,first['operations'],9,set())
    assert len(result['texts'])==7
    assert any(x['id']=='owner-note' and x['text']=='Owner note' for x in result['texts'])
    assert next(x for x in result['texts'] if x['id'].endswith('headline'))['mute_captions'] is False
    changed=storyboard();changed['beats']=changed['beats'][:1]
    second=compile_design(changed,result)
    revised=apply_batch(result,second['operations'],9,set())
    assert {x['id'] for x in revised['texts']}=={
        'owner-note','design-example-headline','design-example-b0-word'}
    assert len(revised['vectors'])==1
    ass=tmp_path/'graphic.ass'
    graphics.build_gfx_ass(revised,9,str(ass),play_res=(1080,1920))
    assert r'\t(' in ass.read_text(), 'native motion must reach the renderer'


def test_native_type_cues_use_retained_program_clock_and_are_explicitly_pending():
    import copy
    index={'video':{'duration':120},'words':[]}
    plan={'id':'timed','keep':[[10,15],[30,35]],'typography_scenes':[
        {'id':'payoff','source_start':30,'source_end':35,'lines':[
            {'runs':[{'text':'One','source_at':30.2},{'text':'idea','source_at':32}]}]}]}
    original=copy.deepcopy(plan)
    result=compile_short(plan,index)
    assert plan==original
    op=result['pending_native_operations'][0]
    assert op['tool']=='set_typography_scene'
    assert (op['args']['start'],op['args']['end'])==(5,10)
    assert [r['at'] for r in op['args']['lines'][0]['runs']]==[5.2,7]
    assert result['edl']['texts']==[], 'deferred native calls are not falsely claimed as applied'
    plan['typography_scenes'][0]['lines'][0]['runs'][0]['source_at']=20
    with pytest.raises(ValueError,match='removed'):compile_short(plan,index)
    plan=copy.deepcopy(original)
    plan['typography_scenes'][0]['source_start']=14
    with pytest.raises(ValueError,match='crosses a cut'):compile_short(plan,index)
    plan=copy.deepcopy(original)
    plan['typography_scenes'][0]['lines'][0]['runs'][0]['at']=.2
    with pytest.raises(ValueError,match='not both'):compile_short(plan,index)


def test_speaker_headline_is_a_native_pending_operation_not_a_second_title():
    plan={'id':'identity','keep':[[10,18]],'headline':{
        'speaker':'Ada Lovelace','text':'An idea worth remembering',
        'box':[.08,.1,.92,.25]}}
    result=compile_short(plan,{'video':{'duration':20},'words':[]})
    assert result['edl']['texts']==[]
    assert result['pending_native_operations']==[{'tool':'set_editorial_graphic','args':{
        'id':'identity-headline','kind':'headline','start':0,'end':8.,**plan['headline']}}]


def test_graphic_rejects_unreadable_card_instead_of_shrinking_type():
    design=storyboard()
    design['beats']=[{'kind':'statement','text':'This sentence cannot be read in one flash',
                     'start':0,'end':.8}]
    with pytest.raises(ValueError,match='needs at least'):compile_design(design)


def test_inspection_decodes_real_media_and_invalidates_stale_evidence(tmp_path):
    video=tmp_path/'sample.mp4'
    def make(color):
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
            f'color=c={color}:s=90x160:d=1:r=10','-f','lavfi','-i',
            'sine=frequency=660:duration=1','-c:v','libx264','-pix_fmt','yuv420p',
            '-c:a','aac','-shortest',str(video)],check=True,timeout=20)
    make('blue')
    first=inspect(video,tmp_path/'packet')
    assert first['decode_ok'] is True
    assert first['judgment']=='unreviewed'
    assert inspect(video,tmp_path/'packet')['reused'] is True
    make('red')
    next_report=inspect(video,tmp_path/'packet')
    assert next_report['sha256']!=first['sha256']
    assert 'reused' not in next_report
