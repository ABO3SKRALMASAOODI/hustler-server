from types import SimpleNamespace as NS
import json
import pytest
import edit_integrity as ei
from score import asks, patch
import agent_loop


def test_contained_original_cutaway_is_not_a_local_face_patch(monkeypatch):
    ctx = NS(index={"video": {"width": 576, "height": 1024}})
    monkeypatch.setattr(ei, "_output_faces", lambda *a: [(2., [[.4, .2, .6, .4]])])
    monkeypatch.setattr(asks, "project_assets", lambda *a: [
        {"storage_key": "original", "kind": "original", "width": 576, "height": 1024}])
    monkeypatch.setattr(asks, "output_size", lambda *a: (720, 1280))
    shot = {"id": "ov.shot", "asset_key": "original", "kind": "video",
            "start": 0, "duration_s": 4, "x": .5, "y": .5, "scale": 1.}
    assert ei.face_cover_note(ctx, {}, {"overlays": [shot]}) == ""
    assert "covers a face" in ei.face_cover_note(
        ctx, {}, {"overlays": [dict(shot, scale=.6)]})
    assert "covers a face" in ei.face_cover_note(
        ctx, {}, {"overlays": [dict(shot, source_crop=[0, 0, .8, 1])]})


def test_removed_spans_measures_exact_source_coverage():
    assert ei.removed_spans([[0, 11.264]], [[0, 3.6]]) == [(3.6, 11.264)]
    assert ei.removed_spans([[0, 2], [4, 7]], [[0, 7]]) == []
    assert ei.removed_spans([[0, 10]], [[1, 3], [5, 8]]) == [(0, 1), (3, 5), (8, 10)]


def test_eye_patch_is_measured_on_source_even_if_render_lost_face():
    ctx = NS(duration=5.6, index={"spatial": {"samples": [{"t": 2., "faces": [[.3, .1, .7, .4]]}]}})
    assert "face" in ei.patch_face_note(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1}])
    assert ei.patch_face_note(ctx, [{"x": .3, "y": .6, "w": .2, "h": .1}]) == ""
    assert ei.patch_face_note(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1, "start": 3, "end": 4}]) == ""


def test_failed_truth_rewrite_never_sends_done(monkeypatch):
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    verdict = {"asks": [{"id": "a1", "state": "not_done", "seen": "dancers darkened", "line": "The background change is unfinished."}]}
    assert asks.true_reply(NS(), {"asks": []}, verdict, "Done!") == "The background change is unfinished."


def test_stale_check_cannot_back_completion():
    ctx = NS(ask_ledger={"asks": [{"id":"a1", "kind":"edit"}]},
             ask_verdict={"edl_version":1, "asks": [{"id":"a1","state":"done"}]},
             latest_edl=lambda: {"version":2})
    reply, outcome = agent_loop._asks_settle(ctx, "Done!", "fulfilled")
    # a verdict on another version is no evidence: recorded partial, the reply is not hedged
    assert reply == "Done!" and outcome == "partial"


def test_unused_image_pixels_reach_ledger(monkeypatch):
    import llm
    monkeypatch.setattr(patch, "_own_media", lambda *a: [{"n":2,"key":"images/new.jpg","kind":"image_ref","name":"hair-down.jpg","frame":"image.jpg"}])
    monkeypatch.setattr(llm, "image_part", lambda p: {"type":"image_url", "image_url":{"url":p}})
    def call(ctx, name, schema, system, content, **kw):
        assert isinstance(content,list)
        assert any("images/new.jpg" in p.get('text','') for p in content)
        assert any(p['type'] == 'image_url' for p in content)
        return {"asks":[{"kind":"edit","want":"use uploaded hair-down image"}]}, {}
    monkeypatch.setattr(asks, "_call", call)
    ledger = asks.build(NS(latest_edl=lambda: {"json":{"keep":[[0,7]]}}, index={}, duration=7), "Use the hair-down version")
    assert ledger['asks'][0]['want'] == "use uploaded hair-down image"


def test_background_rectangle_cannot_dim_face():
    ctx = NS(index={"spatial":{"samples":[{"t":2,"faces":[[.4,.2,.6,.4]]}]}})
    before={"keep":[[0,11]]}
    after=dict(before,fx=[{"id":"rg1","type":"region","start":0,"end":11,
                          "effect":"exposure","ev":-2,"region":{"shape":"rect","box":[0,0,1,.7]}}])
    assert "covers a face" in ei.face_cover_note(ctx,before,after)
    after['fx'][0]['region']['box']=[0,0,.15,.7]
    assert ei.face_cover_note(ctx,before,after) == ""


@pytest.mark.parametrize('opaque,opacity,entrance,at,allowed', [
    (True, None, None, 2., True),
    (False, None, None, 2., False),
    (None, None, None, 2., False),
    (True, .5, None, 2., False),
    (True, None, 'fade', 2., False),
    (True, None, None, 4., False),
])
def test_split_backdrop_hides_source_only_with_pixel_evidence(monkeypatch, opaque, opacity, entrance, at, allowed):
    ctx = NS(index={"video":{"width":100,"height":100}, "spatial":{"samples":[{"t":at,"faces":[[.4,.2,.6,.4]]}]}})
    monkeypatch.setattr(asks, 'project_assets', lambda ctx: [
        {'storage_key':'plate.png','meta':{'made_from':'design','pixel_opaque':opaque}},
        {'storage_key':'card.mp4','width':100,'height':100},
    ])
    before = {'keep':[[0,10]]}
    after = dict(before, overlays=[
        {'id':'backdrop','asset_key':'plate.png','fit':'cover','start':1.,'duration_s':2.,
         'opacity':opacity,'entrance':entrance},
        {'id':'window','asset_key':'card.mp4','fit':'contain','x':.5,'y':.3,'scale':.4,'start':0.,'duration_s':10.},
    ])
    assert (ei.face_cover_note(ctx,before,after) == "") is allowed
    if allowed:
        after['overlays'][0]['screen'] = {'rect':[0,0,.2,.2]}
        assert ei.face_cover_note(ctx,before,after) != ""


def test_generated_backdrop_opacity_is_measured_from_pixels(tmp_path):
    from PIL import Image
    from score.film import _baked_image_facts
    p = tmp_path/'plate.png'
    Image.new('RGB',(8,8),'black').save(p)
    assert _baked_image_facts(p)['opaque'] is True
    im = Image.new('RGBA',(8,8),(0,0,0,255))
    im.putpixel((3,3),(0,0,0,0)); im.save(p)
    assert _baked_image_facts(p)['opaque'] is False


def test_missing_frames_cannot_pass_visual_ask(monkeypatch):
    monkeypatch.setattr(asks,"frame_pages", lambda *a: [])
    monkeypatch.setattr(asks,"_call", lambda *a,**k: ({"asks":[{"id":"a1","state":"done","rests_on":"frames","seen":"changed","line":"Done"}],"unasked":[]},{}))
    ledger=asks.settle({"asks":[{"kind":"edit","how":"look","want":"background"}]}, {"keep":[[0,10]]})
    v=asks.check(NS(index={},duration=10),ledger,"Done",{"keep":[[0,10]]},preview={"edl_version":2})
    assert v['asks'][0]['state']=='cannot_tell'
    assert v['asks'][0]['line']!='Done'


def test_reframed_face_uses_output_not_source_coordinates():
    ctx = NS(index={"video":{"width":1920,"height":1080}, "spatial":{"samples":[{"t":2,"faces":[[.45,.2,.55,.4]]}]}})
    before={"keep":[[0,11]],"frame":{"ratio":"9:16","mode":"crop"}}
    after=dict(before,fx=[{"id":"rg1","type":"region","start":0,"end":11,
                          "effect":"exposure","region":{"shape":"rect","box":[.35,.2,.65,.3]}}])
    assert "face" in ei.face_cover_note(ctx,before,after)


def test_a_repaint_over_a_face_saves_and_names_where_the_face_is():
    """Owner, 16 Nov 2026: a face blur is a legitimate repaint over a face. The write saves and
    carries the measured face box; the editor decides."""
    from w0c_support import tool_context
    from schemas import default_edl
    ctx, fake = tool_context(default_edl(5.6), duration=5.6)
    ctx.index["spatial"] = {"samples": [{"t": 2, "faces": [[.3, .1, .7, .4]]}]}
    proposed = default_edl(5.6)
    proposed["patches"] = [{"id": "pa1", "asset_key": "patches/1/p.mp4", "fp": "x",
                            "src_start": 0, "src_end": 5.6,
                            "regions": [{"id": "er1", "x": .3, "y": .2, "w": .2, "h": .1}]}]
    result = ctx.write_edl(proposed, "blur the face")
    assert result.startswith("EDL v"), result
    assert fake.rows[-1]["json"]["patches"][0]["id"] == "pa1"
    assert "FACE MEASURED (information; the repaint is saved)" in result
    assert "x 0.30-0.70, y 0.10-0.40" in result


def test_same_edl_new_mask_render_is_checked_again(monkeypatch):
    ledger=asks.settle({"asks":[{"kind":"edit","how":"look"}]},{"keep":[[0,5]]})
    ledger['cfg']={"check":True,"repairs":0}
    ctx=NS(ask_ledger=ledger, ask_verdict=None, latest_edl=lambda:{"version":2,"json":{"keep":[[0,5]]}},
           last_preview={"edl_version":2,"media_key":"before-mask.mp4"})
    seen=[]
    def check(*a,**k):
        seen.append(k)
        return {"asks":[{"id":"a1","state":"not_done","seen":"unchanged","line":"Not done"}]}
    monkeypatch.setattr(asks,'check',check)
    monkeypatch.setattr(agent_loop,'_turn_change_list',lambda c:'')
    agent_loop._asks_check(ctx,'Done')
    ctx.last_preview={"edl_version":2,"media_key":"after-mask.mp4"}
    agent_loop._asks_check(ctx,'Done')
    assert len(seen)==2 and all('fixed' not in k for k in seen)


def test_finished_matte_invalidates_same_edl_preview_cache(monkeypatch):
    import render_dependencies as rd
    monkeypatch.setattr(rd,'needed',lambda *a:False)
    monkeypatch.setattr(rd,'_wait_for_matte',lambda *a:{"status":"pending"})
    ctx=NS(rendered_versions=set(), last_preview={})
    row={"version":2,"json":{"keep":[[0,5]]}}
    rd.before_preview(ctx,row)
    old=ctx.render_dependency_key
    ctx.last_preview={"edl_version":2,"render_dependency_key":old}
    ctx.rendered_versions={2}
    monkeypatch.setattr(rd,'_wait_for_matte',lambda *a:{"status":"ready"})
    rd.before_preview(ctx,row)
    assert ctx.render_dependency_key!=old and 2 not in ctx.rendered_versions


def test_new_tray_image_has_pixels_without_message_attachment_id(monkeypatch):
    images=[{"id":i,"kind":"image_ref","storage_key":f"images/{i}","meta":{},"found":False} for i in range(1,202)]
    monkeypatch.setattr(asks,'project_assets',lambda ctx:images)
    ids=agent_loop._visual_priority_ids(NS(),{"meta":{}})
    assert ids[0]==201
    by_id={r['id']:r for r in images}
    selected, omitted=agent_loop._image_visual_plan([by_id[i] for i in ids],4,ids)
    assert selected[0]['id']==201 and omitted==197


def test_missing_ledger_cannot_lose_required_check_on_resume():
    original=NS(ask_ledger=None,ask_verdict=None,ask_check_required=True)
    saved=agent_loop._asks_checkpoint(original)
    resumed=NS()
    agent_loop._typed_asks_start(resumed,None,{},None,{},saved)
    reply,outcome=agent_loop._asks_settle(resumed,'Done','fulfilled')
    assert outcome=='partial' and reply=='Done' 


def test_default_check_does_not_change_existing_route_opt_in():
    assert asks.parse(None)['mode']=='on'
    assert not asks.parse(None)['director'] and not asks.parse(None)['patch']
    assert asks.parse({'mode':'on'})['director'] and asks.parse({'mode':'on'})['patch']


def test_budget_stop_cannot_claim_completion_after_ledger_failure(monkeypatch):
    import fulfillment
    posted = []
    ctx = NS(ask_ledger=None, ask_check_required=True, last_preview=None,
             autorendered=False, latest_edl=lambda: {'version': 2})
    monkeypatch.setattr(agent_loop, '_auto_render_if_needed', lambda *a, **k: ({'version':2}, ''))
    monkeypatch.setattr(agent_loop, '_disclose_outstanding_quality', lambda c,t:t)
    monkeypatch.setattr(agent_loop, '_unused_fetched_audio_note', lambda c:'')
    monkeypatch.setattr(agent_loop, '_quality_handoff', lambda c:{})
    monkeypatch.setattr(agent_loop, '_turn_completion', lambda *a, **k:('fulfilled',True))
    monkeypatch.setattr(agent_loop, '_outcome_meta', lambda *a:{})
    monkeypatch.setattr(agent_loop, '_turn_edl_changed', lambda c:True)
    monkeypatch.setattr(fulfillment, 'disclose', lambda c,t:t)
    db = NS(run=lambda fn,*a:posted.append(a))
    result = agent_loop._finalize(ctx,db,1,'Completed and saved.','budget',3,{})
    assert result['outcome']=='partial'
    assert posted[-1][2] == 'Completed and saved.' and "can't confirm" not in posted[-1][2]


@pytest.mark.parametrize("proofs,with_frames,state", [
    (["numbers", "frames"], True, "done"),
    (["numbers"], True, "cannot_tell"),
    (["reply"], True, "cannot_tell"),
    (["numbers", "frames"], False, "cannot_tell"),
    ("frames", True, "done"),
])
def test_visual_ask_keeps_all_evidence_sources(monkeypatch, proofs, with_frames, state):
    import llm
    monkeypatch.setattr(asks, "frame_pages", lambda *a: [("frame.jpg", "render at 2 s")] if with_frames else [])
    monkeypatch.setattr(llm, "image_part", lambda *a: {"type": "image_url", "image_url": {"url": "frame"}})
    monkeypatch.setattr(asks, "_call", lambda *a, **k: ({"asks": [{
        "id": "a1", "state": "done", "rests_on": proofs,
        "seen": "1080x1920 output; rendered frames show the speaker with the face whole.",
        "line": "Made a vertical short cropped to the speaker."}], "unasked": []}, {}))
    ledger = asks.settle({"asks": [{"kind": "edit", "how": "look", "want": "vertical cropped to speaker"}]}, {"keep": [[0, 10]]})
    verdict = asks.check(NS(index={}, duration=10), ledger, "Done", {"keep": [[0, 10]]}, preview={"edl_version": 2})
    assert verdict["asks"][0]["state"] == state

def test_check_schema_can_record_numbers_and_pixels_together():
    proof = asks.CHECK_SCHEMA["properties"]["asks"]["items"]["properties"]["rests_on"]
    assert proof["type"] == "array"
    assert {"numbers", "frames"} <= set(proof["items"]["enum"])



def test_editor_cuts_are_never_vetoed_by_a_second_model(monkeypatch, tmp_path):
    """Owner, 16 Nov: smart tools and a smart model decide; no judge overrules the editor.
    p5857 (16 Nov): 'use your best judgement to create 60 minutes worth of footage from this
    video' on an 81-minute upload was refused 17 times by the cut-evidence judge."""
    calls = []
    monkeypatch.setattr(asks, "_call", lambda *a, **k: calls.append(a[1]) or ({"allow": False, "reason": "no"}, {}))
    ctx = NS(user_message="use your best judgement to create 60 minutes worth of footage from this video",
             verification_request="remove the long stretches of the stage shows and compile highlights close to 60 mins",
             llm_client=object(), agent_model="claude-haiku-5-5", workdir=str(tmp_path), index={},
             ask_ledger={"asks": [{"id": "a1", "kind": "film"}]})
    before = {"keep": [[0, 4858.2]]}
    after = {"keep": [[0, 780], [1285, 1849], [2251, 2751], [3192, 4858.2]]}
    assert ei.write_note(ctx, before, after) == ""
    assert calls == []
    assert not hasattr(ei, "cut_refusal") and not hasattr(ei, "CUT_SYSTEM")
