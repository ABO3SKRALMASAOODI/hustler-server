import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(__file__))
import wf1_film as film
from wf1_layout import caption_restarts, face_boxes, intersects, seat_display


def test_display_handoff_does_not_replay_the_words_hidden_by_the_title():
    F = film.F
    script = {'segments':[{'say':'one two three four'}]}
    words = {'duration':2.4, 'words':[dict(w=t,start=i*.5,end=i*.5+.45,seg=0)
             for i,t in enumerate(script['segments'][0]['say'].split())]}
    plan = F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    F.caption_build(plan,F.Clock(words,script),'cap',.65,hold=[(0,35)])
    caps = [i for i in plan.items if i['k']=='word']
    assert [i['t'] for i in caps] == ['three','four']
    assert caps[0]['f'][0] == 35 < caps[1]['f'][0]
    assert caption_restarts({'items':caps}) == []
    assert all(not isinstance(i['x'],list) for i in caps)


def test_title_block_clears_the_mouth_in_every_final_camera_crop():
    F = film.F
    plan = F.Plan({'dsp_t':dict(font='InterDisplay-Black.ttf',px=120,track=0)}, {})
    for text,y in [('A long opening',.53),('title on two lines',.60)]:
        plan.word([0,10],text,'dsp_t',.5,y,'#FFFFFF',1,align='c',role='big')
    shot=dict(k='shot',pic='main',f=[0,10],crop=[[.2,0,.8,1]]*5+[[.18,0,.82,1]]*5)
    l1={'frame':[1080,1920],'voices':plan.voices,'items':[shot]+plan.items}
    meta={'main':dict(faces=[[.4,.3,.2,.28,.4]])}
    before=[i['y'] for i in plan.items]
    result=seat_display(l1,meta,plan.R)
    assert result['overlaps']==0 and result['groups'][0]['face_boxes_checked']==10
    assert plan.items[0]['y']>before[0]
    assert plan.items[1]['y']-plan.items[0]['y']==pytest.approx(.07)
    for i in plan.items:
        q=plan.R.T.Face.of(plan.voices[i['v']]).quads(i['t'],120,0)
        box=[i['x']+min(x[1] for x in q)/1080,i['y']+min(x[2] for x in q)/1920,
             i['x']+max(x[3] for x in q)/1080,i['y']+max(x[4] for x in q)/1920]
        assert all(not intersects(box,b) for f in range(10) for b in face_boxes(shot,meta['main'],f))


def test_ink_change_preserves_the_words_already_visible():
    items=[dict(k='word',role='cap',t=t,f=span) for t,span in
           [('one',[1,10]),('two',[6,10]),('one',[10,20]),('two',[10,20]),('three',[14,20])]]
    assert caption_restarts({'items':items})==[]
    items.append(dict(k='word',role='cap',t='four',f=[14,20]))
    assert caption_restarts({'items':items})==[dict(frame=14,words=['three','four'])]


def test_lower_type_seat_moves_a_face_up_and_never_partly_down_into_it():
    shot=dict(id='sh1',k='shot',pic='main',f=[0,3],crop=[.2,.2,.8,.9])
    meta={'main':{'faces':[[.4,.4,.2,.2,.45]]}}
    assert film.F.clear_of([shot],meta,[.05,.5,.95,.62],{'sh1'})==1
    assert shot['crop'][1]>.2
    assert face_boxes(shot,meta['main'],0)[0][3]<=.50001
    shot['crop']=[.2,.1,.8,.9]
    meta['main']['faces']=[[.4,.48,.2,.4,.5]]
    assert film.F.clear_of([shot],meta,[.05,.5,.95,.62],{'sh1'})==0
    assert shot['crop']==[.2,.1,.8,.9]


@pytest.mark.parametrize("style", ["window", "archive-reel", "footage-cut", "prints"])
def test_long_speaker_fallback_alternates_full_source_views_on_the_same_voice_clock(tmp_path,monkeypatch,style):
    words='Every new discovery changes how we understand ourselves and our place in the world'.split()
    script=dict(title='Our Place',segments=[dict(say=' '.join(words),pictures=[])])
    wj=dict(duration=5.5,words=[dict(w=w,start=.1+i*.34,end=.4+i*.34,seg=0) for i,w in enumerate(words)])
    sup=dict(words=wj,work=str(tmp_path),ffmpeg='ffmpeg',pictures={},footage=[],
             take=dict(path='/take.mp4',w=1920,h=1080,fps=30,dur=5.5,faces=[]))
    monkeypatch.setattr(film.F,'voice_stress',lambda sup:[])
    monkeypatch.setattr(film.F,'engines_finish',lambda l1,*a,**k:l1)
    l1,beats=film.build(script,sup,style)
    cards=[i for i in l1['items'] if i['k']=='card' and i['pic']=='main']
    assert len(cards)>=3 and beats['pictures']==[]
    for card in cards:
        assert card['crop']==[0,0,1,1]
        assert card['w']*1080/(card['h']*1920)==pytest.approx(1920/1080,abs=.001)
        assert card['src'][0]==pytest.approx(max(card['f'][0]-3,0)/30,abs=.001)
        assert card['src'][1]-card['src'][0]==pytest.approx((card['f'][1]-card['f'][0])/30,abs=.001)


def test_all_caption_phrases_share_one_face_safe_seat():
    F=film.F
    plan=F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    plan.word([0,15],'one','cap',.3,.615,'#fff',1,role='cap')
    plan.word([5,15],'two','cap',.4,.615,'#fff',1,role='cap')
    plan.word([15,30],'three','cap',.3,.615,'#fff',1,role='cap')
    shot=dict(k='shot',pic='main',f=[0,30],crop=[0,0,1,1])
    l1=dict(frame=[1080,1920],voices=plan.voices,items=[shot]+plan.items)
    result=seat_display(l1,{'main':dict(faces=[[.2,.3,.5,.4,.4]])},plan.R)
    assert result['overlaps']==0
    assert len({i['y'] for i in plan.items})==1
    assert .73<plan.items[0]['y']<.80
    assert caption_restarts(l1)==[]
