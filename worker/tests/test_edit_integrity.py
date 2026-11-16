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
    assert ei.face_cover_refusal(ctx, {}, {"overlays": [shot]}) is None
    assert "covers a measured face" in ei.face_cover_refusal(
        ctx, {}, {"overlays": [dict(shot, scale=.6)]})
    assert "covers a measured face" in ei.face_cover_refusal(
        ctx, {}, {"overlays": [dict(shot, source_crop=[0, 0, .8, 1])]})


def test_removed_spans_measures_exact_source_coverage():
    assert ei.removed_spans([[0, 11.264]], [[0, 3.6]]) == [(3.6, 11.264)]
    assert ei.removed_spans([[0, 2], [4, 7]], [[0, 7]]) == []
    assert ei.removed_spans([[0, 10]], [[1, 3], [5, 8]]) == [(0, 1), (3, 5), (8, 10)]


def test_eye_patch_rejected_on_source_even_if_render_lost_face():
    ctx = NS(duration=5.6, index={"spatial": {"samples": [{"t": 2., "faces": [[.3, .1, .7, .4]]}]}})
    assert "face" in ei.patch_face_refusal(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1}])
    assert ei.patch_face_refusal(ctx, [{"x": .3, "y": .6, "w": .2, "h": .1}]) is None
    assert ei.patch_face_refusal(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1, "start": 3, "end": 4}]) is None


def test_cut_checks_walk_pixels_not_just_silence(monkeypatch, tmp_path):
    import media, llm
    seen = []
    monkeypatch.setattr(media, "frame_at", lambda *a, **k: seen.append(a[1]))
    monkeypatch.setattr(llm, "image_part", lambda p: {"type": "image_url", "image_url": {"url": p}})
    def call(ctx, name, schema, system, parts, **kw):
        import json
        assert name == "check_cut_evidence"
        assert len([p for p in parts if p['type'] == 'image_url']) == 3
        assert json.loads(parts[0]['text'])['source_words'] == [{'w':'still','t0':5,'t1':5.5}]
        assert json.loads(parts[0]['text'])['retained_context_words'][0]['w'] == 'outside'
        assert json.loads(parts[0]['text'])['after_keep'] == [[0, 3.6]]
        return {"allow": False, "reason": "continuous walking, not a wait"}, {}
    monkeypatch.setattr(asks, "_call", call)
    ctx = NS(user_message="Remove waiting gaps, preserve natural pauses", llm_client=object(), agent_model="any",
             workdir=str(tmp_path), proxy_path=lambda: "source.mp4",
             index={'words':[{'w':'outside','t0':0,'t1':.5},{'w':'still','t0':5,'t1':5.5}]})
    assert "continuous walking" in ei.cut_refusal(ctx, {"keep": [[0,11.264]]}, {"keep": [[0,3.6]]})
    assert len(seen) == 3
    assert ei.cut_refusal(ctx, {"keep": [[0,3.6]]}, {"keep": [[0,11.264]]}) is None


def test_failed_truth_rewrite_never_sends_done(monkeypatch):
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    verdict = {"asks": [{"id": "a1", "state": "not_done", "seen": "dancers darkened", "line": "The background change is unfinished."}]}
    assert asks.true_reply(NS(), {"asks": []}, verdict, "Done!") == "The background change is unfinished."


def test_stale_check_cannot_back_completion():
    ctx = NS(ask_ledger={"asks": [{"id":"a1", "kind":"edit"}]},
             ask_verdict={"edl_version":1, "asks": [{"id":"a1","state":"done"}]},
             latest_edl=lambda: {"version":2})
    reply, outcome = agent_loop._asks_settle(ctx, "Done!", "fulfilled")
    assert "can't confirm" in reply and outcome == "partial"


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
    assert "covers a measured face" in ei.face_cover_refusal(ctx,before,after)
    after['fx'][0]['region']['box']=[0,0,.15,.7]
    assert ei.face_cover_refusal(ctx,before,after) is None


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
    assert (ei.face_cover_refusal(ctx,before,after) is None) is allowed
    if allowed:
        after['overlays'][0]['screen'] = {'rect':[0,0,.2,.2]}
        assert ei.face_cover_refusal(ctx,before,after) is not None


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
    assert "face" in ei.face_cover_refusal(ctx,before,after)


def test_batch_write_cannot_bypass_eye_guard():
    import agent_tools
    ctx = NS(_states_seen={}, duration=5.6, latest_edl=lambda:{"version":1,"json":{"keep":[[0,5.6]]}},
             index={"spatial":{"samples":[{"t":2,"faces":[[.3,.1,.7,.4]]}]}})
    proposed={"keep":[[0,5.6]],"patches":[{"id":"pa1","asset_key":"patches/1/p.mp4","fp":"x",
                  "src_start":0,"src_end":5.6,"regions":[{"id":"er1","x":.3,"y":.2,"w":.2,"h":.1}]}]}
    result=agent_tools.ToolContext.write_edl(ctx,proposed,"remove mark")
    assert result.startswith("REJECTED") and "face" in result


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
    assert outcome=='partial' and "can't confirm" in reply


def test_authorized_shorter_cut_is_allowed_and_evidence_is_cached(monkeypatch,tmp_path):
    import media,llm
    monkeypatch.setattr(media,'frame_at',lambda *a,**k:None)
    monkeypatch.setattr(llm,'image_part',lambda p:{'type':'image_url','image_url':{'url':p}})
    calls=[]
    monkeypatch.setattr(asks,'_call',lambda *a,**k: (calls.append(a[1]) or {'allow':True,'reason':'The customer explicitly requested the last five seconds.'},{}))
    ctx=NS(user_message='Keep only the last five seconds',llm_client=object(),agent_model='any',
           proxy_path=lambda:'source.mp4',workdir=str(tmp_path),index={})
    for _ in range(2):
        assert ei.cut_refusal(ctx,{'keep':[[0,11]]},{'keep':[[6,11]]}) is None
    assert calls==['check_cut_evidence']


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
    assert "can't confirm" in posted[-1][2]


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


def test_length_target_cut_with_many_stretches_is_checked_not_refused(monkeypatch, tmp_path):
    """Ticket #1301 (user 3120, 15 Nov): "reduce the footage to just under 60 minutes" on an
    81-minute source needed ~40 removed stretches; the guard refused anything above 12 without
    looking, three times in a row. Every stretch is now checked on its own frames, in groups,
    and the judge is given the measured lengths so a length target can authorize the cut."""
    import json, media, llm
    monkeypatch.setattr(media, "frame_at", lambda *a, **k: None)
    monkeypatch.setattr(llm, "image_part", lambda p: {"type": "image_url", "image_url": {"url": p}})
    groups = []
    def call(ctx, name, schema, system, parts, **kw):
        assert "target" in system and "length" in system
        body = json.loads(parts[0]["text"])
        groups.append(body)
        assert len([p for p in parts if p["type"] == "image_url"]) == 3 * len(body["removed_source_intervals"])
        assert body["measured_length_before_s"] == 4858.0
        return {"allow": True, "reason": "the target length needs these idle stretches gone"}, {}
    monkeypatch.setattr(asks, "_call", call)
    after = [[i * 120.0, i * 120.0 + 88.0] for i in range(40)] + [[4800.0, 4858.0]]
    ctx = NS(user_message="help me reduce the footage to just under 60 minutes", llm_client=object(),
             agent_model="any", workdir=str(tmp_path), proxy_path=lambda: "source.mp4", index={})
    assert ei.cut_refusal(ctx, {"keep": [[0, 4858.0]]}, {"keep": after}) is None
    removed = sum(len(g["removed_source_intervals"]) for g in groups)
    assert removed == 40 and len(groups) == 4
    assert all(len(g["removed_source_intervals"]) <= ei.CUT_GROUP for g in groups)
    assert groups[0]["measured_length_after_s"] == 40 * 88.0 + 58.0
    # A refusal in any group refuses the whole write, and a cached verdict is reused.
    groups.clear()
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (groups.append(1) or {"allow": False, "reason": "keeps the payoff"}, {}))
    ctx2 = NS(**{**vars(ctx), "_cut_evidence": {}})
    assert "keeps the payoff" in ei.cut_refusal(ctx2, {"keep": [[0, 4858.0]]}, {"keep": after})
    assert groups == [1]


def test_length_target_hands_selection_to_editor_and_blank_refusal_is_rechecked(monkeypatch, tmp_path):
    """Ticket #1318 (user 3120, project 5857, 15 Nov): after #1301 the judge accepted a
    58.5-minute cut of an 81-minute family video as on target, then refused it because the
    removed events were "not clearly weaker than what stays"; on footage that is all content no
    selection could pass, three requests in a row. The judge is now told that a length or
    highlights request hands the choice of moments to the editor, and a refusal with no
    reason is a malformed check (retried, never cached), not a final verdict."""
    import media, llm
    assert "weaker, idler or more" not in ei.CUT_SYSTEM
    assert "editor's" in ei.CUT_SYSTEM and "never a reason to refuse" in ei.CUT_SYSTEM
    monkeypatch.setattr(media, "frame_at", lambda *a, **k: None)
    monkeypatch.setattr(llm, "image_part", lambda p: {"type": "image_url", "image_url": {"url": p}})
    answers = [{"allow": False, "reason": ""}, {"allow": True, "reason": "58.5 min, on target"}]
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (answers.pop(0), {}))
    after = [[0, 780], [1285, 1849], [2251, 2751], [3192, 4858.22]]
    ctx = NS(user_message="compile highlights from this video and make it last close to 60 mins",
             llm_client=object(), agent_model="any", workdir=str(tmp_path),
             proxy_path=lambda: "source.mp4", index={})
    first = ei.cut_refusal(ctx, {"keep": [[0, 4858.22]]}, {"keep": after})
    assert first == ei._UNCHECKED
    assert ei.cut_refusal(ctx, {"keep": [[0, 4858.22]]}, {"keep": after}) is None
    assert answers == []


def test_named_kind_and_pause_asks_are_removal_criteria():
    """Tickets #1345/#1346/#123 (user 3120, project 5857, 16 Nov): "remove the long stretches of
    the stage shows, ride and performance footage and speed up the pauses in the speech". The
    judge saw a stage show, a choir, a swing ride and bumper cars in the removed frames and still
    refused, for want of a target length or highlights request; it refused every pause removal
    because the picture moved during the gap. Five refusals, nothing changed. The judge is now
    told a named kind of footage is itself the criterion, each stretch needs to serve only one
    of the asks, and a gap between phrases is a pause, not a sentence cut in the middle."""
    s = ei.CUT_SYSTEM
    assert "names WHAT to remove" in s and "is itself the removal criterion" in s
    assert "ANY one of them" in s
    assert "pauses or\nsilences in speech authorizes" in s and "not cutting a\nsentence" in s
    assert "do not ask for a target length" in s


def test_repair_turn_judges_cuts_against_brief_and_repair_list(monkeypatch, tmp_path):
    """Tickets #1351/#1352 (user 3120, project 5857, 16 Nov): a Repair edit press made the
    turn's message the product's repeated-word list, so the cut judge never saw the brief
    ("... speed up the pauses in the speech") and refused every pause removal, and it refused
    the named first sayings because word snapping moved the boundaries a fraction."""
    import media, llm
    monkeypatch.setattr(media, 'frame_at', lambda *a, **k: None)
    monkeypatch.setattr(llm, 'image_part', lambda p: {'type': 'image_url', 'image_url': {'url': p}})
    seen = []
    monkeypatch.setattr(asks, '_call', lambda *a, **k: (seen.append(json.loads(a[4][0]['text'])['request'])
                                                     or {'allow': True, 'reason': 'ok'}, {}))
    brief = 'remove the long stretches of the stage shows and speed up the pauses in the speech'
    repair = 'Fix the remaining quality issues before export: Repeated words: "say hi say hi" at 567.85s'
    ctx = NS(user_message=repair, verification_request=brief, llm_client=object(), agent_model='any',
             proxy_path=lambda: 'source.mp4', workdir=str(tmp_path), index={})
    assert ei.cut_refusal(ctx, {'keep': [[0, 600]]}, {'keep': [[0, 128.3], [129.2, 600]]}) is None
    assert brief in seen[0] and repair in seen[0]
    assert ei._cut_request(NS(user_message=brief, verification_request=brief)) == brief
    s = ei.CUT_SYSTEM
    assert "first saying" in s and "snaps the named numbers to whole" in s
    assert "serves either" in s


def test_length_target_steps_and_clean_sentence_cuts_are_allowed():
    """Tickets #1358/#1360/#1363/#1364 (user 3120, project 5857, 16 Nov 07:54 UTC, after
    fd49d844 and 0efbb5ad were live): "use your best judgement to create 60 minutes worth of
    footage from this video". The judge refused every small cut_range step because that one
    step left the film far over 60 minutes, refused two whole keep lists that landed at 60.5 and
    58.5 minutes over a 400 s stretch that ran from a finished sentence to the next spoken word,
    and refused a false start ("We have [pause] we have other people") as a sentence cut. Twelve
    and seventeen refusals in a row; the 81-minute film never changed. The judge is now told a
    write toward a length target is a step, length refuses only a result far below the target, a
    stretch between sentences is clean, a false start is cleaning, and doubt about which moments
    matter is the editor's call."""
    s = ei.CUT_SYSTEM
    assert "never refuse a write for not reaching the\ntarget on its own" in s
    assert "far BELOW the target" in s and "still well\nover it" not in s
    assert "begins after a complete sentence" in s and "topic carries on" in s
    assert "false start" in s and "is\ncleaning" in s
    assert "doubt about which moments matter is the editor's call" in s
    assert "exact second to move that edge to" in s
