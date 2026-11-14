from score import sound as S
from score import sound_choice as C


def shot(n, start, end, crop):
    return dict(id=str(n), k='shot', pic='main', f=[start,end], src=[start/30,end/30], crop=crop)


def test_visible_reframes_reach_sound_design_but_timing_splits_do_not():
    wide=[0,0,1,1];tight=[.15,.15,.85,.85]
    rows=[shot(0,0,30,wide),shot(1,30,60,tight),shot(2,60,90,tight),shot(3,90,120,wide)]
    e=S.l1_events(dict(fps=30,dur_f=120,items=rows))
    assert [x['f'] for x in e]==[30,90]
    assert all(x['kind']=='cut' and x['designed'] for x in e)
    assert C.weight_of(e[0],'plain') > C.weight_of(dict(kind='cut'),'plain')


def test_continuous_motion_is_not_mistaken_for_a_cut():
    wide=[0,0,1,1];tight=[.15,.15,.85,.85]
    a=shot(0,0,30,[wide,tight]);b=shot(1,30,60,[tight,wide])
    assert S.reframe_change(a,b)==0
    assert S.l1_events(dict(fps=30,dur_f=60,items=[a,b]))==[]


def test_original_camera_cut_keeps_its_original_cut_budget():
    rows=[shot(0,0,30,[0,0,1,1]),shot(1,30,60,[0,0,1,1])]
    events=S.l1_events(dict(fps=30,dur_f=60,items=rows),kshots=[(0,1),(1,2)])
    assert len(events)==1 and events[0]['kind']=='cut' and not events[0].get('designed')
