from types import SimpleNamespace
import pytest
import fulfillment
import agent_loop


@pytest.mark.parametrize('text,n',[
    ('Create 5 short vertical videos (9:16, 8–15 seconds).',5),
    ('Using the uploads, create 5 separate vertical 9:16 UGC-style videos.',5),
    ('Make five videos from different POVs.',5),
    ('Make a video with 5 clips.',1),
    ('Make 5 second clips.',1),
    ('Make five videos. Instead create one video.',1),
])
def test_count_is_outputs_not_source_clips_or_seconds(text,n):
    assert fulfillment.requested_video_count(text)==n


def test_one_verified_edit_does_not_fulfill_five_requested_videos(monkeypatch):
    monkeypatch.setattr(agent_loop,'_quality_handoff',lambda _: {'export_ready':True})
    ctx=SimpleNamespace(user_message='Create 5 short vertical videos.',
        versions_written=[14],rendered_versions={14},last_preview={'cached':False},
        turn_tool_outcomes=[],write_attempts=3,latest_edl=lambda:{'version':14},
        db=SimpleNamespace(run=lambda *a:1),project_id=2464,project={'user_id':1931})
    assert agent_loop._turn_completion(ctx)==('partial',True)
    assert ctx.delivery_requirement == {'requested_videos':5,'playable_projects':1,'complete':False}
    assert '1 of 5' in fulfillment.disclose(ctx, 'The first edit is ready.')
    ctx.db.run=lambda *a:5
    assert agent_loop._turn_completion(ctx)==('fulfilled',True)
    assert fulfillment.disclose(ctx, 'Ready.') == 'Ready.'
