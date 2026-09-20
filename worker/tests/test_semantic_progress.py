"""Turn renewal follows editorial progress, not raw EDL churn."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_loop  # noqa: E402


def _ctx(**updates):
    base = dict(
        edit_plan=None, editing_metrics={}, versions_written=[],
        write_calls=[], rendered_versions=set(), last_visual_critic=None,
        last_story_review=None, last_audio_review=None,
        images_generated=[], videos_generated=[], urls_fetched=[],
        web_recordings=[], audio_extracted=[], audio_fetched=[], stock_added=[])
    base.update(updates)
    return SimpleNamespace(**base)


def _plan(step_status="pending", check_status="pending", generation=1):
    return {
        "steps": ["build the treatment"], "generation": generation,
        "step_states": [{"id": 1, "task": "build the treatment",
                         "status": step_status, "evidence": None}],
        "acceptance_criteria": ["the treatment is coherent"],
        "acceptance_checks": [{"id": 1,
                               "criterion": "the treatment is coherent",
                               "status": check_status, "evidence": None}],
    }


def test_more_versions_and_previews_alone_do_not_renew_forever():
    ctx = _ctx(versions_written=[2], write_calls=["apply_edit_recipe"],
               rendered_versions={2})
    before = agent_loop._semantic_progress_marker(ctx)
    ctx.versions_written.extend(range(3, 98))
    ctx.write_calls.extend(["apply_edit_recipe"] * 42)
    ctx.rendered_versions.update(range(3, 16))
    after = agent_loop._semantic_progress_marker(ctx)

    assert not agent_loop._semantic_progressed(before, after)


def test_replanning_the_same_turn_cannot_buy_unlimited_windows():
    before = agent_loop._semantic_progress_marker(
        _ctx(edit_plan=_plan(generation=1)))
    after = agent_loop._semantic_progress_marker(_ctx(edit_plan=_plan(generation=99)))
    assert not agent_loop._semantic_progressed(before, after)


def test_new_department_plan_closure_or_asset_is_semantic_progress():
    before = agent_loop._semantic_progress_marker(
        _ctx(edit_plan=_plan(), versions_written=[2],
             write_calls=["apply_edit_recipe"], rendered_versions={2}))

    department = agent_loop._semantic_progress_marker(
        _ctx(edit_plan=_plan(), versions_written=[2, 3],
             write_calls=["apply_edit_recipe", "add_music"],
             rendered_versions={2}))
    assert agent_loop._semantic_progressed(before, department)

    closed = agent_loop._semantic_progress_marker(
        _ctx(edit_plan=_plan("completed", "passed"),
             versions_written=[2], write_calls=["apply_edit_recipe"],
             rendered_versions={2}))
    assert agent_loop._semantic_progressed(before, closed)

    asset = agent_loop._semantic_progress_marker(
        _ctx(edit_plan=_plan(), versions_written=[2],
             write_calls=["apply_edit_recipe"], rendered_versions={2},
             stock_added=[{"storage_key": "broll/one"}]))
    assert agent_loop._semantic_progressed(before, asset)


def test_independent_review_improvement_renews_but_repeated_defects_do_not():
    before = agent_loop._semantic_progress_marker(_ctx(
        versions_written=[2], write_calls=["apply_edit_recipe"],
        rendered_versions={2},
        last_visual_critic={"verdict": "repair",
                            "findings": [{"category": "collision"},
                                         {"category": "hierarchy"}]}))
    same = agent_loop._semantic_progress_marker(_ctx(
        versions_written=[2, 3], write_calls=["apply_edit_recipe"] * 2,
        rendered_versions={2, 3},
        last_visual_critic={"verdict": "repair",
                            "findings": [{"category": "collision"},
                                         {"category": "hierarchy"}]}))
    assert not agent_loop._semantic_progressed(before, same)

    cleaner = agent_loop._semantic_progress_marker(_ctx(
        versions_written=[2, 3], write_calls=["apply_edit_recipe"] * 2,
        rendered_versions={2, 3},
        last_visual_critic={"verdict": "pass", "findings": []}))
    assert agent_loop._semantic_progressed(before, cleaner)


def test_repair_requires_quality_progress_not_new_tools_or_assets():
    before = {'verification_rank': 1, 'verification_findings': 2,
              'write_tools': ['add_text'], 'assets': [0]}
    after = dict(before, write_tools=['add_text', 'set_text_motion'], assets=[5])
    assert not agent_loop._semantic_progressed(before, after)
    assert agent_loop._semantic_progressed(before, dict(after, verification_findings=1))


def test_progress_frontier_cannot_oscillate_to_buy_new_slices():
    before = {'review_verdicts': [3, 1, 1], 'review_findings': [0, 3, 2],
              'verification_rank': 1, 'verification_findings': 2}
    current = {'review_verdicts': [1, 3, 1], 'review_findings': [3, 0, 2],
               'verification_rank': 1, 'verification_findings': 2}
    frontier = agent_loop._slice_boundary_resolution(before, current, 'a')['frontier']
    assert frontier['review_verdicts'] == [3, 3, 1]
    assert not agent_loop._semantic_progressed(frontier, before)
    assert not agent_loop._semantic_progressed(frontier, current)


def test_rewording_blocker_does_not_reset_stagnation():
    result = agent_loop._slice_boundary_resolution({}, {}, 'different', 'old', 2)
    assert result['action'] == 'block'


def test_failed_plan_criteria_are_not_completed_work():
    before = agent_loop._semantic_progress_marker(_ctx(edit_plan=_plan()))
    after = agent_loop._semantic_progress_marker(_ctx(edit_plan=_plan('blocked', 'failed')))
    assert not agent_loop._semantic_progressed(before, after)
