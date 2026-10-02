"""Regressions traced from palantir-karp-20260905-132939.

Pure/offline fixtures; no application startup, database, provider or storage.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
import agent_tools
import captions
import mcp_exec
import quality_verifier
import renderer
import media
import transcribe
from schemas import default_edl, validate_edl
from timeline import Timeline, remap_program_items


def context(tmp_path, edl, words=None):
    row = {"version": 1, "json": edl}
    ctx = SimpleNamespace(latest_edl=lambda: row, project_id=1,
        index={"words": words or [], "video": {"width": 1920, "height": 1080}},
        db=SimpleNamespace(run=lambda *_: None), workdir=str(tmp_path),
        job={"id": 9}, pending_images=[])
    def write(value, _description):
        row.update(version=row["version"]+1, json=value)
        return f"EDL v{row['version']} saved"
    ctx.write_edl = write
    return ctx


def test_crop_verifier_ignores_unused_source_and_maps_kept_edges():
    edl = default_edl(2400)
    edl["keep"] = [[105, 109], [200, 205]]
    edl["speed"] = [{"start": 200, "end": 205, "factor": 2}]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 105, "t1": 109, "x": .3, "y": .5}]}
    index = {"shots": [{"id": i, "start": a, "end": b} for i, (a,b) in
                       enumerate([(0,100),(100,110),(110,200),(200,220),(220,2400)])]}
    found = quality_verifier._reframe_findings(edl, index)
    assert found[0]["evidence"]["shot_ids"] == [3]
    assert found[0]["evidence"]["intervals"][0]["source_range"] == [200,205]
    assert found[0]["evidence"]["intervals"][0]["timeline_ranges"] == [(4,6.5)]
    edl["frame"]["focus_track"].append({"t0": 200, "t1": 205})
    assert quality_verifier._reframe_findings(edl, index) == []


def test_crop_track_must_cover_whole_active_fragment_not_just_midpoint():
    edl = default_edl(20)
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 4, "t1": 6}, {"t0": 14, "t1": 16}]}
    assert quality_verifier._reframe_findings(edl, {"shots": [
        {"id": 1,"start": 0,"end": 10}, {"id": 2,"start": 10,"end": 20}]})


def test_only_stationary_full_video_cover_hides_source_crop_findings():
    edl = default_edl(20)
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    index = {"shots": [{"id": 1,"start": 0,"end": 10}, {"id": 2,"start": 10,"end": 20}]}
    cover = {"kind": "video", "fit": "cover", "start": 0, "duration_s": 20}
    edl["overlays"] = [cover]
    assert quality_verifier._reframe_findings(edl, index) == []
    for change in ({"fit": None}, {"kind": "image"}, {"rotation": 30},
                   {"x": .7}, {"opacity": .5}, {"entrance": "fade"}):
        edl["overlays"] = [{**cover, **change}]
        assert quality_verifier._reframe_findings(edl, index), change


def test_datetime_justification_roundtrips_nested_database_evidence():
    stamp = datetime(2026,9,5,12,30,tzinfo=timezone.utc)
    record = {"unresolved_findings": [{"finding_id":"vf_1", "code":"scene_unaware_reframe",
               "evidence":{"created_at":stamp}}], "created_at":stamp}
    wrapped = {"record":record, "updated_at":stamp}
    result = quality_verifier.justify_findings(wrapped,["vf_1"],
        "Direct frames show the intended speaker in every surviving shot.")
    assert result["status"] == "justified"
    assert result["created_at"] == "2026-09-05T12:30:00+00:00"
    json.dumps(result)
    assert isinstance(record["created_at"], datetime)
    assert quality_verifier.justify_findings(result, ["vf_1"],
        "Direct frames show the intended speaker in every surviving shot.") == result


def test_caption_replace_clears_stale_global_rule_and_returns_preview(tmp_path):
    edl = default_edl(4)
    edl["captions"] = {"mode":"from_transcript", "text_fixes":[["was","Was"]]}
    words = [{"w":"policy", "t0":0, "t1":.4},{"w":"was","t0":.4,"t1":.8}]
    ctx = context(tmp_path,edl,words)
    result = agent_tools.set_caption_fixes(ctx, [["policy", "Policy"]])
    assert 'CAPTION PREVIEW' in result
    assert ctx.latest_edl()["json"]["captions"]["text_fixes"] is None
    assert 'Policy was' in result
    listed = json.loads(agent_tools.set_caption_fixes(ctx,operation="list"))
    assert listed["active_corrections"] == [{"from":"policy","to":"Policy"}]
    agent_tools.set_caption_fixes(ctx, [["POLICY", "policy"]],operation="append")
    assert len(ctx.latest_edl()["json"]["captions"]["corrections"]) == 1


def test_scoped_merge_split_and_exact_punctuation_preserve_other_occurrences():
    words = [{"w":token,"t0":i*.3,"t1":(i+1)*.3} for i,token in
             enumerate(["foreign", "policy", "was,", "foreign", "policy", "was."])]
    fixed = captions.apply_scoped_fixes(words,[
        {"from":"foreign policy", "to":"Diplomacy?", "start":0,"end":.6},
        {"from":"was", "to":"has been.","start":.6,"end":.9}])
    assert [w["w"] for w in fixed] == ["Diplomacy?","has","been.","foreign","policy","was."]
    assert fixed[0]["t0"] == 0 and fixed[0]["t1"] == .6
    assert fixed[-1] == words[-1]
    assert words[0]["w"] == "foreign"


def test_scoped_fixes_do_not_bridge_insert_break():
    words=[{"w":"foreign","t0":0,"t1":.3},
           {"w":"policy","t0":2,"t1":2.3,"brk":True}]
    assert captions.apply_scoped_fixes(words,[{"from":"foreign policy","to":"Diplomacy"}]) == words


def test_scoped_fix_wins_over_global_and_append_preserves_legacy_punctuation(tmp_path):
    words=[{"w":"chat,","t0":0,"t1":1}, {"w":"chat.","t0":1,"t1":2}]
    fixed=captions.apply_scoped_fixes(words,[
        {"from":"chat","to":"Chat"},
        {"from":"chat","to":"shat!","start":0,"end":1}])
    assert [w['w'] for w in fixed] == ['shat!','Chat']
    edl=default_edl(2)
    edl['captions']={'mode':'from_transcript','text_fixes':[['chat','Chat']]}
    ctx=context(tmp_path,edl,words)
    result=agent_tools.set_caption_fixes(ctx,[{'from':'chat','to':'shat!','start':0,'end':1}],operation='append')
    assert 'shat! Chat.' in result


@pytest.mark.parametrize('rule', ['mistake', ['bad'], ['bad','good','ignored'],
    {'from':'bad','to':'good','start_time':1},
    {'from':'bad','to':'good','start':float('inf'),'end':float('inf')}])
def test_invalid_caption_scope_cannot_silently_become_global(tmp_path,rule):
    edl=default_edl(2); edl['captions']={'mode':'from_transcript'}
    ctx=context(tmp_path,edl)
    assert agent_tools.set_caption_fixes(ctx,[rule]).startswith('REJECTED:')
    assert ctx.latest_edl()['version']==1


def test_caption_endpoint_clamped_in_all_families():
    for style in ({}, {"dynamic":True}, {"preset":"clean"}):
        edl = {"keep":[[0,31.107]], "captions":{"mode":"from_transcript", "style":style}}
        events,_ = captions.compiled_events(edl,{"words":[{"w":"ending", "t0":30.9,"t1":31.1}]},Timeline(edl["keep"]))
        assert events and max(e["end"] for e in events) <= 31.10


def test_owned_title_mute_is_not_late_caption_and_bare_mute_needs_review(tmp_path):
    edl = default_edl(4)
    edl.update(captions={"mode":"from_transcript"}, texts=[
        {"id":"title", "text":"Opening attribution", "start":0,"end":1.6,"mute_captions":True}])
    ctx = context(tmp_path,edl,[{"w":"title", "t0":0,"t1":1},
                                {"w":"speech", "t0":1.6,"t1":2}])
    result=json.loads(agent_tools.audit_captions(ctx))
    assert result["first_caption_late_by_s"] == 0
    assert result["uncovered_word_count"] == 0
    assert result["mute_windows_without_text_coverage"] == []
    edl["texts"]=[]; edl["caption_mutes"]=[[0,1.6]]
    assert json.loads(agent_tools.audit_captions(ctx))["mute_windows_without_text_coverage"] == [[0,1.6]]


def test_minimum_phrase_avoids_orphan_where_valid_grouping_exists():
    words=[{"w":w,"t0":i*.25,"t1":i*.25+.2} for i,w in
           enumerate("I am wondering how we can change foreign policy".split())]
    p={"mode":"static","min_words":3}
    groups=captions._premium_chunks_v2(words,4,80,p)
    assert all(3 <= len(group) <= 4 for group in groups)


def test_asr_confidence_is_retained():
    payload={"results":{"channels":[{"alternatives":[{"words":[
        {"word":"regressive","start":0,"end":1,"confidence":.44},
        {"word":"policy","start":1,"end":2}]}]}]}}
    words,_=transcribe._parse_deepgram(payload)
    assert words[0].confidence == .44
    assert words[1].confidence is None


def test_multiple_faces_do_not_select_largest_as_speaker():
    points,coverage=agent_tools._spatial_face_points({"samples":[
        {"t":1,"faces":[[.1,.1,.3,.5],[.5,.1,.95,.7]]}]},[[0,2]])
    assert points == [] and coverage == 0


def test_frame_track_validates_and_survives_timeline_trim():
    edl=default_edl(20)
    edl["frame"]={"ratio":"9:16","mode":"crop","focus_track":[
        {"t0":0,"t1":10,"x":.2,"y":.4},{"t0":10,"t1":20,"x":.8,"y":.4}]}
    old=deepcopy(edl["frame"])
    edl["keep"]=[[5,18]]
    remap_program_items(edl,Timeline([[0,20]]),Timeline([[5,18]]))
    assert edl["frame"] == old
    assert validate_edl(edl,20).frame.focus_track[1].x == .8


def test_external_overlay_warns_when_opening_boundary_moves():
    edl={"keep":[[2,10]],"overlays":[{"id":"portrait","kind":"video",
        "asset_key":"crop.mp4","start":0,"duration_s":10,"fit":"cover"}]}
    notes=remap_program_items(edl,Timeline([[0,10]]),Timeline([[2,10]]))
    assert any("SYNC REVIEW REQUIRED" in n for n in notes)


def test_approval_geometry_and_context_isolation():
    token=renderer._PREVIEW_QUALITY.set("approval")
    try:
        assert renderer.preview_geometry(1080,1920,30)[:2] == (720,1280)
        assert not renderer._needs_preview_downscale(1280)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)
    assert renderer.preview_geometry(1080,1920,30)[1] == 480


def test_frame_upload_failure_is_returned_and_retryable(tmp_path,monkeypatch):
    ctx=context(tmp_path,default_edl(2))
    frame=tmp_path/'frame.png'; frame.write_bytes(b'\x89PNG\r\n\x1a\n')
    ctx.pending_images=[('output 1.00s',str(frame))]
    def fail(*_): raise OSError('offline')
    monkeypatch.setattr(mcp_exec.storage,'upload_file',fail)
    result,_attempts=mcp_exec._drain_images(ctx)
    assert result[0]['error'] == 'frame_upload_failed'
    assert len(ctx.pending_images) == 1
    ctx.write_edl(default_edl(2),'unrelated change')
    monkeypatch.setattr(mcp_exec.storage,'upload_file',lambda *_:None)
    assert mcp_exec._drain_images(ctx)[0][0]['edl_version'] == 1


def test_frame_urls_never_reuse_mutable_object_keys(tmp_path,monkeypatch):
    ctx=context(tmp_path,default_edl(2))
    frame=tmp_path/'frame.png'; frame.write_bytes(b'\x89PNG\r\n\x1a\n')
    monkeypatch.setattr(mcp_exec.storage,'upload_file',lambda *_:None)
    ctx.pending_images=[('output 1.00s',str(frame))]
    first=mcp_exec._drain_images(ctx)[0][0]
    ctx.pending_images=[('output 1.00s',str(frame))]
    second=mcp_exec._drain_images(ctx)[0][0]
    assert first['storage_key'] != second['storage_key']
    assert first['mime_type']=='image/png' and first['edl_version']==1


def test_approval_render_real_pixels_and_branding(tmp_path):
    source = str(tmp_path/'source.mp4')
    output = str(tmp_path/'approval.mp4')
    media.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',
               'testsrc2=size=1920x1080:rate=10:duration=2',
               '-f','lavfi','-i','sine=frequency=440:duration=2',
               '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',
               '-c:a','aac','-shortest',source],timeout=60)
    edl=default_edl(2)
    edl['frame']={'ratio':'9:16','mode':'crop'}
    edl['captions']={'mode':'from_transcript','style':{'preset':'clean'},'design_version':2}
    index={'words':[{'w':'Approval','t0':0,'t1':.8}, {'w':'preview','t0':.8,'t1':2}]}
    token=renderer._PREVIEW_QUALITY.set('approval')
    try:
        duration=renderer.render_edl(edl,index,source,output,str(tmp_path),True,want_wm=True)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)
    info=media.probe(output)
    assert (info['width'],info['height']) == (720,1280)
    assert abs(duration - (2+renderer.outro_seconds(True))) < .15
    assert renderer.outro_seconds(False) == 5


def test_explicit_complete_preview_is_not_routed_to_changed_section(tmp_path,monkeypatch):
    ctx=context(tmp_path,default_edl(2))
    ctx.checked_versions=set(); ctx.rendered_versions=set()
    ctx.last_preview=None; ctx.failed_preview_versions={}; ctx.spec_preview_jobs={}
    ctx.job['user_id']=1
    monkeypatch.setattr(agent_tools,'_verify_plan_for',lambda *_: [])
    monkeypatch.setattr(agent_tools,'_sequence_screening_frames',lambda *_: [])
    monkeypatch.setattr(agent_tools,'_child_payload',lambda _ctx,payload:payload)
    class Enqueued(Exception): pass
    captured=[]
    def enqueue(_fn,_pid,_uid,payload):
        captured.append(payload)
        raise Enqueued()
    ctx.db.run=enqueue
    with pytest.raises(Enqueued):
        agent_tools.render_preview(ctx,complete=True,quality='approval')
    assert captured[0]['quality']=='approval'
    assert 'check_ranges' not in captured[0]
