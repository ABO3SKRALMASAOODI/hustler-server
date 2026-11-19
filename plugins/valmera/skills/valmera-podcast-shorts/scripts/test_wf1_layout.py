import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(__file__))
import wf1_film as film
from wf1_layout import caption_restarts, face_boxes, intersects, seat_display


def test_fast_page_tail_preserves_spoken_negation_and_every_other_word():
    F=film.F
    tokens=['and','I','just',"didn't",'think','I','was','gonna','like','it.']
    edges=[35.02,35.26,35.38,35.5,35.58,35.68,35.78,35.86,36.,36.14,36.72]
    words=dict(duration=37,words=[dict(w=w,start=edges[i],end=edges[i+1],seg=0)
                                 for i,w in enumerate(tokens)])
    clock=F.Clock(words,{'segments':[{'say':' '.join(tokens)}]})
    plan=F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    F.caption_build(plan,clock,'cap',.65)
    caps=[i for i in plan.items if i['k']=='word']
    assert [i['t'] for i in caps]==tokens
    negation=next(i for i in caps if i['t']=="didn't")
    assert negation['f'][1]-negation['f'][0]>=6
    assert caption_restarts({'items':caps})==[]
    assert all(not isinstance(i['x'],list) for i in caps)


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
def test_missing_documentary_supply_cannot_be_hidden_by_speaker_recrops(tmp_path,monkeypatch,style):
    words='Every new discovery changes how we understand ourselves and our place in the world'.split()
    script=dict(title='Our Place',segments=[dict(say=' '.join(words),pictures=[])])
    wj=dict(duration=5.5,words=[dict(w=w,start=.1+i*.34,end=.4+i*.34,seg=0) for i,w in enumerate(words)])
    sup=dict(words=wj,work=str(tmp_path),ffmpeg='ffmpeg',pictures={},footage=[],
             take=dict(path='/take.mp4',w=1920,h=1080,fps=30,dur=5.5,faces=[]))
    monkeypatch.setattr(film.F,'voice_stress',lambda sup:[])
    monkeypatch.setattr(film.F,'engines_finish',lambda l1,*a,**k:l1)
    sup['coverage'] = dict(words=wj['words'], moves=[], windows=[dict(t0=.1,t1=5.2,name='world',must_show='actual Earth')])
    with pytest.raises(RuntimeError,match='documentary supply incomplete'):
        film.build(script,sup,style)


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


def test_captions_take_a_seat_per_page_when_faces_fill_every_height():
    """19 Nov m01: faces sat high in some shots and low in others, so no single caption seat cleared them all
    and the short stopped. Each page now takes its own seat; a page that keeps clear of faces keeps the shared one."""
    F=film.F
    plan=F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    plan.word([0,15],'one','cap',.3,.615,'#fff',1,role='cap')
    plan.word([15,30],'two','cap',.3,.615,'#fff',1,role='cap')
    plan.word([30,45],'three','cap',.3,.615,'#fff',1,role='cap')
    low=dict(k='shot',pic='low',f=[0,15],crop=[0,0,1,1])
    high=dict(k='shot',pic='high',f=[15,30],crop=[0,0,1,1])
    empty=dict(k='shot',pic='none',f=[30,45],crop=[0,0,1,1])
    meta={'low':dict(faces=[[.2,.55,.6,.3,.4]]),'high':dict(faces=[[.2,.15,.6,.3,.4]]),'none':dict(faces=[])}
    l1=dict(frame=[1080,1920],voices=plan.voices,items=[low,high,empty]+plan.items)
    result=seat_display(l1,meta,plan.R)
    assert result['overlaps']==0
    one,two,three=plan.items
    assert three['y']==.615
    for word,shot in ((one,low),(two,high)):
        q=plan.R.T.Face.of(plan.voices[word['v']]).quads(word['t'],58,0)
        box=[word['x']+min(x[1] for x in q)/1080,word['y']+min(x[2] for x in q)/1920,
             word['x']+max(x[3] for x in q)/1080,word['y']+max(x[4] for x in q)/1920]
        assert all(not intersects(box,b) for f in range(*shot['f']) for b in face_boxes(shot,meta[shot['pic']],f))
    assert caption_restarts(l1)==[]


def test_a_block_no_seat_can_clear_takes_the_least_covering_seat_and_says_so():
    F=film.F
    plan=F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    plan.word([0,20],'covered','cap',.3,.615,'#fff',1,role='cap')
    shot=dict(k='shot',pic='full',f=[0,10],crop=[0,0,1,1])
    shot2=dict(k='shot',pic='half',f=[10,20],crop=[0,0,1,1])
    meta={'full':dict(faces=[[.0,.0,1,1,.4]]),'half':dict(faces=[[.0,.0,1,.5,.4]])}
    l1=dict(frame=[1080,1920],voices=plan.voices,items=[shot,shot2]+plan.items)
    result=seat_display(l1,meta,plan.R)
    assert result['overlaps']==10                      # the full-frame face is covered; the half-frame one is cleared
    assert plan.items[0]['y']>.5


def test_shade_follows_each_caption_page():
    flash=dict(id='fl1',k='flash',f=[0,40],mode='pool',a=.42,rect=[.06,.5,.94,.7])
    pieces=film.follow_seat(flash,[(0,.0),(15,.1),(30,.1)])
    assert [p['f'] for p in pieces]==[[0,15],[15,40]]
    assert pieces[0]['rect']==[.06,.5,.94,.7] and pieces[1]['rect']==pytest.approx([.06,.6,.94,.8])
    assert [p['id'] for p in pieces]==['fl1','fl1.1']
    assert film.follow_seat(flash,[(0,.05)])[0]['rect']==pytest.approx([.06,.55,.94,.75])


def test_every_frame_gets_exactly_one_base_picture():
    """19 Nov m12: the short stopped in render on '1 frames without exactly one base picture'."""
    shots=[dict(id='a',k='shot',f=[2,10],crop=[[0,0,1,1]]*8),dict(id='b',k='shot',f=[11,20]),
           dict(id='c',k='shot',f=[18,25]),dict(id='d',k='shot',f=[20,22]),dict(id='w',k='word',f=[0,30])]
    l1=dict(dur_f=30,items=shots)
    changed=film.heal_base(l1)
    base=sorted((it for it in l1['items'] if it['k']=='shot'),key=lambda it:it['f'][0])
    cov=[0]*30
    for it in base:
        for f in range(*it['f']): cov[f]+=1
    assert cov==[1]*30
    a=next(it for it in base if it['id']=='a')
    assert a['f']==[0,11] and len(a['crop'])==11
    assert any(it['k']=='word' for it in l1['items'])
    assert {c['id'] for c in changed}>={'a','b','c'}
    assert film.heal_base(l1)==[]


def test_captions_seat_inside_the_taste_rules_safe_area():
    """20 Nov m06 stopped twice: the seat pushed captions below faces to a baseline of 0.825, under the taste rule's
    0.80 floor. The seat's band is now the safe area itself."""
    F=film.F
    plan=F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    plan.word([0,20],'below','cap',.3,.62,'#fff',1,role='cap')
    shot=dict(k='shot',pic='main',f=[0,20],crop=[0,0,1,1])
    l1=dict(frame=[1080,1920],voices=plan.voices,items=[shot]+plan.items)
    seat_display(l1,{'main':dict(faces=[[.2,.35,.6,.4,.4]])},plan.R)
    assert plan.items[0]['y'] <= .80 + 1e-6
