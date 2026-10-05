import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import editorial_graphics
import graphics


@pytest.mark.parametrize('dims',[(1080,1920),(1920,1080),(1080,1080)])
def test_speaker_starts_measured_heading_without_covering_dialogue(dims):
    W,H=dims
    result=editorial_graphics.compose(id='topic',kind='headline',
        speaker='Elon Musk',text='What is money actually worth?',start=0,end=8,
        box=[.08,.08,.92,.30],W=W,H=H)
    assert result['vectors']==[]
    assert result['texts'][0]['text']=='Elon Musk:'
    assert all(t['start']==0 and t['end']==8 and not t['mute_captions']
               and not t.get('motion') for t in result['texts'])
    assert result['texts'][0]['color']!=result['texts'][1]['color']
    boxes=[graphics._compile_item(t,8,dims) for t in result['texts']]
    assert all(b['left']>=.075*W and b['right']<=.925*W and
               b['top']>=.075*H and b['bottom']<=.305*H for b in boxes)
    for a,b in zip(boxes,boxes[1:]):
        assert a['right']<=b['left']+1 or a['bottom']<=b['top']+1


def test_no_guessed_identity_tiny_copy_or_extra_labels():
    args=dict(id='topic',kind='headline',speaker='Ada Lovelace',
              text='A clear idea',start=0,end=12)
    for change,match in [({'speaker':''},'verified speaker'),
                         ({'font_size':.02},'font_size'),
                         ({'secondary':'more labels'},'without extra'),
                         ({'speaker':'Ada','text':'A clear idea '*6,'box':[.1,.1,.35,.3]},'three lines')]:
        with pytest.raises(ValueError,match=match):
            editorial_graphics.compose(**{**args,**change})


@pytest.mark.parametrize('ratio,dims',[
    ('9:16',(1080,1920)),('16:9',(1920,1080)),('1:1',(1080,1080))])
def test_quality_advisory_uses_actual_headline_geometry(ratio,dims):
    import copy
    import quality_gate
    from schemas import default_edl, validate_edl
    before=default_edl(12)
    after=copy.deepcopy(before)
    after['frame']={'ratio':ratio,'mode':'pad'}
    result=editorial_graphics.compose(id='topic',kind='headline',
        speaker='Elon Musk',text='SpaceX builds its own engines',start=0,end=8,
        box=[.08,.08,.92,.30],W=dims[0],H=dims[1])
    after['texts']=result['texts']
    # Exercise the persisted schema, including optional motion fields.
    after=validate_edl(after,12).model_dump()
    assert quality_gate.advisory_findings(before,after)==[]

    # A common generated prefix must not hide a real placement collision.
    colliding=copy.deepcopy(after)
    colliding['texts'][1]['x']=colliding['texts'][0]['x']
    colliding['texts'][1]['y']=colliding['texts'][0]['y']
    assert any('overlaps designed text' in f for f in
               quality_gate.advisory_findings(before,colliding))

    # Static separation cannot waive a move that might cross another word.
    moving=copy.deepcopy(after)
    moving['texts'][1]['motion']={'x':[{'t':0,'v':.05},{'t':1,'v':.95}]}
    assert any('overlaps designed text' in f for f in
               quality_gate.advisory_findings(before,moving))


def test_quality_advisory_preserves_separated_moving_headline_and_payoff():
    import quality_gate
    import typography_scenes
    from schemas import default_edl, validate_edl
    before=default_edl(12)
    after=default_edl(12)
    after['frame']={'ratio':'9:16','mode':'pad'}
    after['texts']=editorial_graphics.compose(id='topic',kind='headline',
        speaker='Elon Musk',text='What is money worth?',start=0,end=10,
        box=[.08,.08,.92,.30])['texts']
    after['texts']+=typography_scenes.compose(id='answer',start=4,end=10,
        lines=[{'runs':[{'text':'Goods','at':4},{'text':'and services','at':4.5}]}],
        box=[.08,.70,.92,.86],motion='settle')['texts']
    after=validate_edl(after,12).model_dump()
    assert quality_gate.advisory_findings(before,after)==[]
    # A low block that sweeps through the title still needs review.
    after['texts'][-1]['motion']['y']=[{'t':0,'v':.78},{'t':1,'v':.19}]
    assert any('overlaps designed text' in f for f in
               quality_gate.advisory_findings(before,after))


def test_native_recipe_exposes_and_replaces_speaker_heading():
    import agent_tools
    from schemas import default_edl,validate_edl

    class Context:
        index={'video':{'width':1920,'height':1080}}
        edit_plan=None
        edl=default_edl(12)
        edl['frame']={'ratio':'9:16','mode':'pad'}
        def latest_edl(self):return {'json':self.edl}
        def write_edl(self,e,note):
            self.edl=validate_edl(e,12).model_dump();return 'saved'
    ctx=Context()
    assert agent_tools.set_editorial_graphic(ctx,'topic','headline','A clear idea',0,12,speaker='Ada Lovelace')=='saved'
    assert agent_tools.set_editorial_graphic(ctx,'topic','headline','An updated idea',0,12,speaker='Ada Lovelace')=='saved'
    texts=ctx.edl['texts']
    assert len({t['id'] for t in texts})==len(texts)
    assert not any(t['text']=='clear' for t in texts)
    assert 'set_editorial_graphic' in agent_tools.RECIPE_TOOLS
    schema=agent_tools.TOOLS['set_editorial_graphic'][2]
    assert 'headline' in schema['kind']['enum'] and 'speaker' in schema
    assert agent_tools.remove_editorial_graphic(ctx,'topic')=='saved'
    assert ctx.edl['texts']==[]
