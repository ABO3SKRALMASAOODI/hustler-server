import copy
import json
from pathlib import Path

import pytest
from edit_batch import apply_batch
from playback_plan import compile_plan, changed_work
from schemas import default_edl, canvas_edl


@pytest.mark.parametrize("fixture", json.loads((Path(__file__).parent / "fixtures/playback-plan.json").read_text()), ids=lambda f:f["name"])
def test_shared_playback_contract(fixture):
    assert compile_plan(fixture["edl"]) == fixture["expected"]


def test_invalid_last_operation_does_not_mutate_first():
    edl=default_edl(20)
    original=copy.deepcopy(edl)
    with pytest.raises(ValueError):
        apply_batch(edl,[dict(action="set",layer="keep",value=[[1,5]]),
                        dict(action="set",layer="not_a_layer",value=3)],20,[])
    assert edl == original


def test_batch_updates_sound_and_framing_together():
    edl=apply_batch(default_edl(20),[
        dict(action="set",layer="keep",value=[[1,5],[10,15]]),
        dict(action="set",layer="frame",value=dict(ratio="9:16",mode="crop")),
        dict(action="set",layer="volume",value=[dict(start=1,end=5,gain_db=-6)]),
        dict(action="upsert",layer="music",id="score",value=dict(storage_key="score.mp3",start=0,end=9,duck=False)),
    ],20,["score.mp3"])
    assert compile_plan(edl)["duration"] == 9
    assert edl["frame"]["ratio"] == "9:16"
    assert edl["music"][0]["id"] == "score"


def test_cross_project_asset_cannot_enter_a_batch():
    with pytest.raises(ValueError,match="attached to this project"):
        apply_batch(default_edl(20),[dict(action="upsert",layer="music",id="m",
                    value=dict(storage_key="another-user/song.mp3",start=0,end=10))],20,[])


def test_canvas_reorder_keeps_ids_and_selected_windows():
    edl=canvas_edl()
    edl["inserts"]=[dict(id=k,asset_key=k,kind="video",at_output_s=i*2,duration_s=2,source_start_s=10)
                    for i,k in enumerate(("a","b","c"))]
    new=apply_batch(edl,[dict(action="reorder",layer="inserts",value=["c","a","b"])],None,["a","b","c"])
    plan=compile_plan(new)
    assert [c["id"] for c in plan["clips"]] == ["c","a","b"]
    assert [c["start"] for c in plan["clips"]] == [0,2,4]
    assert all(c["source_start"]==10 for c in plan["clips"])


def test_review_scope_reuses_picture_after_sound_change_and_keeps_global_checks():
    old=default_edl(20)
    new=copy.deepcopy(old)
    new["volume"]=[dict(start=2,end=4,gain_db=-6)]
    scope=changed_work(old,new)
    assert scope["picture_unchanged"] and scope["audio_changed"]
    assert scope["full_review_required"]
    new["frame"]={"ratio":"9:16"}
    assert changed_work(old,new)["picture_ranges"] == [[0,20]]


@pytest.mark.parametrize('operation',[
    dict(action='set',layer='music',value=['malformed']),
    dict(action='set',layer='frame',value=dict(ratio='9:16',focus_xx=.5)),
    dict(action='upsert',layer='music',id='m',value=dict(storage_key=['bad'],start=0,end=4)),
])
def test_malformed_or_misspelled_batch_is_rejected(operation):
    with pytest.raises(ValueError):apply_batch(default_edl(20),[operation],20,['song'])


def test_selected_clip_must_fit_its_own_media():
    operation=dict(action='upsert',layer='inserts',id='i',value=dict(kind='video',asset_key='clip',at_output_s=0,duration_s=4,source_start_s=8))
    with pytest.raises(ValueError,match='beyond its source'):
        apply_batch(default_edl(20),[operation],20,{'clip':{'duration_s':10}})


def test_caption_mutes_report_their_actual_intervals():
    before=default_edl(20);after=copy.deepcopy(before);after['caption_mutes']=[[3,5]]
    assert changed_work(before,after)['picture_ranges']==[[3,5]]


def test_music_overhang_does_not_extend_the_export_clock():
    edl=default_edl(20);edl['music']=[{'storage_key':'music','start':0,'end':90}]
    assert compile_plan(edl)['duration']==20
