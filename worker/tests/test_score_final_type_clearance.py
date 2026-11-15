"""Final crop changes must not invalidate settled type placement."""
import copy
from types import SimpleNamespace

from score import compiler as S


def scene(face=True):
    shot = dict(k='shot', id='s1', pic='main', f=[0,60], src=[0,2], crop=[0,0,1,1])
    words = [dict(k='word', id='title.'+str(n), t=text, v='a', x=.3,
                  y=y, f=[0,60], s=[1.2]*60, role='big')
             for n,(text,y) in enumerate([('ONE CAREER',.25),('many frontiers',.31)])]
    originals = [{**w, '_g':'opening'} for w in words]
    ctx = SimpleNamespace(W=1080,H=1920,fps=30,voices={'a':{'px':65,'font':''}},
                          items=originals,index={},perc={},bin={},
                          faces=[dict(t=t,boxes=[[.28,.17,.7,.3]]) for t in (0,.5,1,1.5,2)] if face else [])
    return ctx, dict(items=[shot]+copy.deepcopy(words))


def test_final_crop_moves_whole_title_off_face_without_hiding_words():
    ctx,l1=scene();before=copy.deepcopy(l1)
    result=S.final_type_clearance(ctx,l1)
    assert len(result['moved'])==1 and not result['unresolved']
    changes=[]
    for original,after in zip(before['items'][1:],l1['items'][1:]):
        changes.append(after['y']-original['y'])
        assert {k:v for k,v in original.items() if k!='y'} == {k:v for k,v in after.items() if k!='y'}
        assert not S.rects_touch(S.type_rect(ctx,after),[.28,.17,.7,.3])
    assert abs(changes[0]-changes[1])<1e-9
    assert before['items'][0]==l1['items'][0]


def test_final_crop_clears_print_and_keeps_caption_chunk_together():
    ctx,l1=scene(False)
    ctx.perc={'text':[{'t':[0,2],'box':[.2,.18,.8,.32]}]}
    ctx.items=[]
    for word in l1['items'][1:]:word.update(role='cap',chunk='sentence')
    result=S.final_type_clearance(ctx,l1)
    assert [r['id'] for r in result['moved']]==['sentence']
    assert not result['unresolved']
    assert all(not S.rects_touch(S.type_rect(ctx,w),[.2,.18,.8,.32]) for w in l1['items'][1:])


def test_no_safe_place_reports_collision_and_preserves_content():
    ctx,l1=scene(False);ctx.perc={'text':[{'t':[0,2],'box':[0,0,1,1]}]}
    before=copy.deepcopy(l1)
    assert S.final_type_clearance(ctx,l1)=={'moved':[],'unresolved':['opening']}
    assert l1==before


def test_clear_final_picture_keeps_every_item_exactly():
    ctx,l1=scene(False);before=copy.deepcopy(l1)
    assert S.final_type_clearance(ctx,l1)=={'moved':[],'unresolved':[]}
    assert l1==before


def test_authored_type_behind_subject_keeps_its_composition():
    ctx,l1=scene()
    for word in l1['items'][1:]:word['behind']=True
    before=copy.deepcopy(l1)
    assert S.final_type_clearance(ctx,l1)=={'moved':[],'unresolved':[]}
    assert l1==before


def test_compile_checks_type_against_late_crop_not_authored_picture(monkeypatch):
    import json
    from score import source_guard
    from tests.test_score_compiler import talk_index
    monkeypatch.setattr(S,'GOVERN',False)
    monkeypatch.setattr(S,'thumbs',lambda *args:None)
    calls=[]
    original=S.final_type_clearance
    def change_crop(l1,*args,**kwargs):
        for it in l1['items']:
            if it['k']=='shot':it['crop']=[.4,0,.8,1]
        calls.append('crop')
        return {'changed':True}
    def inspect(ctx,l1):
        assert calls==['crop']
        assert all(it['crop']==[.4,0,.8,1] for it in l1['items'] if it['k']=='shot')
        calls.append('placement')
        return original(ctx,l1)
    monkeypatch.setattr(source_guard,'apply',change_crop)
    monkeypatch.setattr(S,'final_type_clearance',inspect)
    text='\n'.join(json.dumps(r) for r in [
      {'k':'film','v':1,'fps':30,'frame':'9:16','spine':'main','band':'own','own':{'med':2,'p10':1,'p90':4},'seed':3},
      {'k':'dna','plate':'#101014','ink':'#FFFFFF','pair':{'a':'grotesk-bold@.03','b':'serif-italic@.034'},'seat':[.5,.62]},
      {'k':'b','id':'b01','w':'0-3','job':'speech','pic':[{'src':'main'}],'type':'0-3'}])
    result=S.compile_score(text,index=talk_index(5,faces=True))
    assert calls==['crop','placement']
    assert not S.validate_l1(result.l1)
    assert result.meter['final_type_clearance'] is not None
