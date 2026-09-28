"""A finished preview must survive the worker boundary before the final reply."""
import json
from types import SimpleNamespace
import agent_loop


def context(version=47):
    return SimpleNamespace(latest_edl=lambda: {'version':version}, last_preview=None,
        versions_written=[46,47], rendered_versions={47}, checked_versions=set(),
        verification_records={47:{'status':'passed','unresolved_findings':[]}},
        turn_tool_outcomes=[], write_attempts=0, has_main_video=True)


def test_completed_preview_survives_json_checkpoint_and_final_handoff():
    before=context()
    before.last_preview={'edl_version':47,'duration_s':30,'storage_key':'media/7/proof.mp4','quality':'approval'}
    state=json.loads(json.dumps(agent_loop._preview_checkpoint(before)))
    after=context()
    agent_loop._restore_preview_checkpoint(after,state)
    assert after.last_preview==before.last_preview
    assert agent_loop._quality_handoff(after)['export_ready'] is True
    assert agent_loop._turn_completion(after)==('fulfilled',True)


def test_old_preview_cannot_certify_newer_edit():
    before=context();before.last_preview={'edl_version':46,'storage_key':'media/7/old.mp4'}
    after=context();agent_loop._restore_preview_checkpoint(after,agent_loop._preview_checkpoint(before))
    assert agent_loop._quality_handoff(after)['export_ready'] is False
    assert agent_loop._turn_completion(after)[0]=='partial'


def test_open_review_is_not_lost_at_checkpoint():
    before=context();before.last_preview={'edl_version':47}
    before.last_audio_qc_findings=['true peak clipping']
    after=context();after.verification_records={47:{'status':'repair_required','unresolved_findings':[{'message':'true peak clipping'}]}}
    agent_loop._restore_preview_checkpoint(after,agent_loop._preview_checkpoint(before))
    assert agent_loop._quality_handoff(after)['quality_status']=='repair_required'
    assert 'Verification remains open' in agent_loop._disclose_outstanding_quality(after,'Draft saved.')


def test_legacy_checkpoint_with_missing_receipt_adopts_existing_preview(monkeypatch):
    ctx=context();ctx.job={'id':10};ctx.turn_start_edl=None
    calls=[]
    def adopt(c, **kwargs):
        calls.append(kwargs)
        c.last_preview={'edl_version':47,'storage_key':'existing.mp4','cached':True}
        return 'Preview v47 was ALREADY rendered'
    monkeypatch.setattr(agent_loop.agent_tools,'render_preview',adopt)
    monkeypatch.setattr(agent_loop,'_activity',lambda *a,**k:None)
    agent_loop._auto_render_if_needed(ctx,None,1,{})
    assert len(calls)==1 and ctx.last_preview['storage_key']=='existing.mp4'
    agent_loop._auto_render_if_needed(ctx,None,1,{})
    assert len(calls)==1
