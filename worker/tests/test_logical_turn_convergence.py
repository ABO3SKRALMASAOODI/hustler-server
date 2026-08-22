"""Production-shaped convergence tests for durable logical editor turns."""

import inspect

import agent_loop


def _marker(**overrides):
    marker = {
        "has_write": False,
        "write_tools": frozenset(),
        "has_render": False,
        "has_plan": False,
        "resolved_steps": frozenset(),
        "resolved_checks": frozenset(),
        "decisions": frozenset(),
        "assets": (0, 0, 0, 0, 0, 0, 0),
        "review_verdicts": (0, 0, 0),
        "review_findings": (None, None, None),
        "department_gaps": None,
        "motion_gaps": None,
        "verification_rank": 0,
        "verification_findings": 0,
    }
    marker.update(overrides)
    return marker


def test_elapsed_slice_alone_cannot_renew_a_logical_turn_forever():
    """Regression: the 13.45-hour turn renewed 61 times on elapsed time."""
    source = inspect.getsource(agent_loop._run_loop)
    assert "if total_expired or _progressed" not in source

    frontier = _marker(has_write=True, write_tools=frozenset({"add_captions"}))
    fingerprint = "same-unresolved-objective"
    first = agent_loop._slice_boundary_resolution(
        frontier, dict(frontier), fingerprint)
    second = agent_loop._slice_boundary_resolution(
        first["frontier"], dict(frontier), fingerprint,
        first["blocker_fingerprint"], first["blocker_repeats"])
    third = agent_loop._slice_boundary_resolution(
        second["frontier"], dict(frontier), fingerprint,
        second["blocker_fingerprint"], second["blocker_repeats"])

    assert first["action"] == "continue_recovery"
    assert second["action"] == "continue_recovery"
    assert third["action"] == "block"


def test_real_objective_frontier_renews_and_survives_json_round_trip():
    before = _marker(has_write=True,
                     write_tools=frozenset({"keep_segments"}),
                     decisions=frozenset({("story", "hook", "keep")}))
    after = _marker(
        has_write=True,
        has_render=True,
        write_tools=frozenset({"keep_segments"}),
        decisions=frozenset({("story", "hook", "keep")}),
        verification_rank=1,
        verification_findings=2,
    )
    result = agent_loop._slice_boundary_resolution(
        before, after, "repair-a", "repair-a", 2)

    assert result["action"] == "continue_progress"
    assert result["blocker_repeats"] == 0
    # The serialized tuple becomes a list in durable JSON. It must compare as
    # the same frontier on the next process, not counterfeit new progress.
    restored = result["frontier"]
    unchanged = agent_loop._slice_boundary_resolution(
        restored, agent_loop._serializable_progress_marker(after), "repair-a")
    assert unchanged["action"] == "continue_recovery"


def test_verification_blocker_identity_ignores_edl_version_and_timestamps():
    v540 = {
        "edl_version": 540,
        "unresolved_findings": [{
            "finding_id": "v540-caption-22",
            "code": "caption_overlap",
            "category": "captions",
            "message": "Caption overlap at 11.42s in EDL v540",
        }],
    }
    v541 = {
        "edl_version": 541,
        "unresolved_findings": [{
            "finding_id": "v541-caption-23",
            "code": "caption_overlap",
            "category": "captions",
            "message": "Caption overlap at 11.47s in EDL v541",
        }],
    }

    first, _ = agent_loop._objective_blocker_fingerprint(None, v540)
    second, _ = agent_loop._objective_blocker_fingerprint(None, v541)
    assert first == second


def test_new_finite_plan_criterion_is_objective_progress():
    before = _marker(has_plan=True, resolved_steps=frozenset({1}))
    after = _marker(has_plan=True, resolved_steps=frozenset({1, 2}))
    result = agent_loop._slice_boundary_resolution(
        before, after, "criterion-3", "criterion-3", 2)
    assert result["action"] == "continue_progress"
    assert result["blocker_repeats"] == 0
