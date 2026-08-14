"""Release gating stays family-specific, fail-closed and score-free."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark_gate
import benchmark_runner
import editorial_benchmark


FAMILY = "talking_head_social"


def _channel(channel, winner="left", missing=None):
    dimensions = {
        name: {"winner": winner, "confidence": .9,
               "evidence": ["specific left fact", "specific right fact"]}
        for name in benchmark_gate.CHANNEL_DIMENSIONS[channel]
        if name != missing
    }
    return {"overall_winner": winner, "confidence": .9,
            "dimensions": dimensions, "positional_consistency": True,
            "positional_disagreements": []}


def _result(case_id="case-1", winner="left", *, human="left",
            opponent="previous_build", missing=None,
            candidate_metrics=None, opponent_metrics=None):
    return {
        "case_id": case_id, "family": FAMILY,
        "candidate_side": "left", "opponent_kind": opponent,
        "human_winner": human,
        "channels": {"visual": _channel("visual", winner, missing)},
        "side_metrics": {
            "left": candidate_metrics or {"wall_time_s": 40,
                                            "model_cost_usd": .4},
            "right": opponent_metrics or {"wall_time_s": 100,
                                             "model_cost_usd": 1.0},
        },
    }


def _policy(**quality_overrides):
    quality = {
        "min_cases_per_family": 1,
        "max_loss_rate": 0,
        "min_win_or_tie_rate": 1,
        "max_insufficient_rate": 0,
    }
    quality.update(quality_overrides)
    return {
        "name": "test release",
        "required_families": [FAMILY],
        "required_channels_by_family": {FAMILY: ["visual"]},
        "quality": quality,
        "human_labels": dict(quality),
        "efficiency": {
            "wall_time_s": {"min_pairs_per_family": 1,
                            "max_p50_ratio": .5,
                            "max_p95_ratio": .5},
            "model_cost_usd": {"min_pairs_per_family": 1,
                               "max_p50_ratio": .5,
                               "max_p95_ratio": .5},
        },
    }


def test_release_passes_only_when_each_dimension_human_label_and_metric_pass():
    report = benchmark_gate.evaluate_release([_result()], _policy())
    assert report["status"] == "pass"
    assert report["release_allowed"] is True
    assert report["failures"] == []
    dims = report["quality"][FAMILY]["channels"]["visual"]
    assert dims["typography"]["win"] == 1
    assert report["human_labels"][FAMILY]["win_rate"] == 1
    assert report["efficiency"]["wall_time_s"][FAMILY]["p50_ratio"] == .4


def test_missing_dimension_is_insufficient_not_an_inherited_overall_win():
    report = benchmark_gate.evaluate_release(
        [_result(missing="typography")], _policy())
    assert report["release_allowed"] is False
    assert report["quality"][FAMILY]["channels"]["visual"][
        "typography"]["insufficient"] == 1
    assert any(row["dimension"] == "typography" and
               row["code"] == "min_win_or_tie_rate"
               for row in report["failures"])


def test_one_family_cannot_hide_an_absent_required_family():
    policy = _policy()
    policy["required_families"].append("podcast_conversation")
    policy["required_channels_by_family"]["podcast_conversation"] = [
        "visual", "story"]
    report = benchmark_gate.evaluate_release([_result()], policy)
    absent = [row for row in report["failures"]
              if row.get("family") == "podcast_conversation"]
    assert absent
    assert any(row["scope"] == "quality" and
               row["code"] == "insufficient_cases" for row in absent)
    assert any(row["scope"] == "human_labels" and
               row["code"] == "insufficient_cases" for row in absent)
    assert any(row["scope"] == "efficiency" and
               row["code"] == "insufficient_metric_pairs" for row in absent)


def test_candidate_loss_and_slowdown_remain_separate_failures():
    result = _result(
        winner="right", human="right",
        candidate_metrics={"wall_time_s": 130, "model_cost_usd": 1.4})
    report = benchmark_gate.evaluate_release([result], _policy())
    scopes = {row["scope"] for row in report["failures"]}
    assert "quality" in scopes
    assert "human_labels" in scopes
    assert "efficiency" in scopes
    assert report["efficiency"]["wall_time_s"][FAMILY]["p50_ratio"] == 1.3


def test_zero_baseline_metric_ties_at_zero_but_any_regression_fails():
    policy = _policy()
    policy["efficiency"] = {
        "corrective_turns": {"min_pairs_per_family": 1,
                             "max_p50_ratio": 1}}
    tied = _result(
        candidate_metrics={"corrective_turns": 0},
        opponent_metrics={"corrective_turns": 0})
    assert benchmark_gate.evaluate_release(
        [tied], policy)["release_allowed"] is True

    regressed = _result(
        candidate_metrics={"corrective_turns": 1},
        opponent_metrics={"corrective_turns": 0})
    report = benchmark_gate.evaluate_release([regressed], policy)
    assert any(row["code"] == "regression_from_zero_baseline"
               for row in report["failures"])


def test_opponent_specific_gate_cannot_be_satisfied_by_other_comparisons():
    policy = _policy()
    policy["quality_by_opponent"] = {
        "human_reference": {
            "min_cases_per_family": 1,
            "max_loss_rate": .25,
            "min_win_or_tie_rate": .75,
            "min_win_rate": .5,
            "max_insufficient_rate": 0,
        }}
    report = benchmark_gate.evaluate_release([_result()], policy)
    assert any(row["scope"] ==
               "quality_by_opponent:human_reference" and
               row["code"] == "insufficient_cases"
               for row in report["failures"])


def test_missing_human_labels_count_as_insufficient_evidence():
    report = benchmark_gate.evaluate_release(
        [_result(human=None)], _policy())
    assert report["release_allowed"] is False
    assert report["human_labels"][FAMILY]["total"] == 1
    assert report["human_labels"][FAMILY]["insufficient"] == 1
    assert any(row["scope"] == "human_labels" and
               row["code"] == "max_insufficient_rate"
               for row in report["failures"])


def test_missing_metric_pair_cannot_hide_behind_a_complete_pair():
    results = [
        _result(case_id="complete"),
        _result(case_id="missing-cost",
                candidate_metrics={"wall_time_s": 40},
                opponent_metrics={"wall_time_s": 100}),
    ]
    report = benchmark_gate.evaluate_release(results, _policy())
    assert report["release_allowed"] is False
    row = report["efficiency"]["model_cost_usd"][FAMILY]
    assert row["pairs"] == 1
    assert row["missing_or_invalid_pairs"] == 1
    assert any(failure["scope"] == "efficiency" and
               failure["metric"] == "model_cost_usd" and
               failure["code"] == "missing_metric_pairs"
               for failure in report["failures"])


def test_non_finite_or_boolean_metrics_are_missing_not_release_wins():
    policy = _policy()
    policy["efficiency"] = {
        "wall_time_s": {"min_pairs_per_family": 1,
                         "max_p50_ratio": 1,
                         "max_p95_ratio": 1}}
    for invalid in (float("nan"), float("inf"), True):
        report = benchmark_gate.evaluate_release([
            _result(candidate_metrics={"wall_time_s": invalid},
                    opponent_metrics={"wall_time_s": 100})], policy)
        assert report["release_allowed"] is False
        assert any(row["code"] == "missing_metric_pairs"
                   for row in report["failures"])


def test_malformed_rows_and_required_identity_fail_without_crashing():
    policy = _policy()
    policy["quality_by_opponent"] = {
        "previous_build": dict(policy["quality"])}
    report = benchmark_gate.evaluate_release(
        [42, _result(case_id="", opponent="")], policy)
    codes = {row["code"] for row in report["failures"]
             if row["scope"] == "manifest"}
    assert {"invalid_result_rows", "case_id_missing",
            "opponent_kind_missing"}.issubset(codes)


def test_manifest_identity_and_metrics_are_copied_after_blind_evaluation(
        monkeypatch):
    monkeypatch.setattr(editorial_benchmark, "compare_visual",
                        lambda *_a, **_k: _channel("visual"))
    monkeypatch.setattr(editorial_benchmark, "compare_story",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(editorial_benchmark, "compare_audio",
                        lambda *_a, **_k: None)
    result = editorial_benchmark.evaluate_pair({
        "id": "blind", "family": FAMILY, "candidate_side": "right",
        "opponent_kind": "human_reference",
        "left": {"visual_paths": ["a"], "metrics": {"wall_time_s": 100}},
        "right": {"visual_paths": ["b"], "metrics": {"wall_time_s": 20}},
    })
    assert result["candidate_side"] == "right"
    assert result["opponent_kind"] == "human_reference"
    assert result["side_metrics"]["right"]["wall_time_s"] == 20


def test_gate_cli_writes_report_and_returns_two_on_regression(tmp_path):
    results_path = tmp_path / "results.json"
    policy_path = tmp_path / "policy.json"
    report_path = tmp_path / "gate.json"
    results_path.write_text(json.dumps({"results": [
        _result(winner="right", human="right")]}), encoding="utf-8")
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

    code = benchmark_runner.main([
        "gate", str(results_path), "--policy", str(policy_path),
        "--report", str(report_path)])
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert code == 2
    assert saved["release_allowed"] is False


def test_policy_has_no_implicit_defaults_for_missing_evidence_rules():
    try:
        benchmark_gate.normalize_policy({"required_families": [FAMILY]})
        assert False, "incomplete release policy must fail closed"
    except benchmark_gate.PolicyError as exc:
        assert "required_channels_by_family" in str(exc)
