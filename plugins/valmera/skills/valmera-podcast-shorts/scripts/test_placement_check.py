import os,sys
sys.path.insert(0,os.path.dirname(__file__))
from placement_check import accepted, propose, words_under


def test_missing_duplicate_and_truthy_model_values_cannot_approve():
    rows={'placements':[{'id':0,'ok':True},{'id':1,'ok':'true'}, {'id':2,'ok':True},{'id':2,'ok':False}]}
    got=accepted(rows,[0,1,2,3])
    assert [got[i]['ok'] for i in range(4)] == [True,False,False,False]


def test_proposals_do_not_borrow_footage_from_another_sentence_or_repeat_it():
    class Clock:
        segs=[(0,3),(3,6),(6,9)]
    clip=dict(path='a.mp4',seconds=2,subject='a')
    script={'segments':[{'say':'a'},{'say':'unrelated'},{'say':'a again'}]}
    rows=propose(script,Clock(),{0:[clip],2:[clip]}, {},0,270,[])
    assert len(rows)==1 and rows[0][1]<=90
    assert abs(rows[0][4]['src'][1]-rows[0][4]['src'][0]-(rows[0][1]-rows[0][0])/30)<0.001


def test_words_use_overlap_with_displayed_interval():
    class Clock:
        words=[dict(w='Earth',t0=1,t1=1.3),dict(w='Sun',t0=1.4,t1=1.8)]
    assert words_under(Clock(),30,40)=='Earth'
