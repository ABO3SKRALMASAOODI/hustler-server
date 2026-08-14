"""Fail-closed release gates for blinded finished-edit benchmarks.

The pairwise benchmark intentionally has no aggregate quality score.  This
module keeps that property while turning the evidence into a release decision:
every required editorial family, channel and craft dimension must independently
meet coverage and non-regression rules.  Human labels and paired efficiency
metrics are gated beside model evidence, never averaged into it.

Policies are explicit data.  A missing family, candidate side, channel, human
label or metric is a failure when the policy requires it; absence can never be
mistaken for a tie or a clean release.
"""

import math

import editorial_benchmark
import editorial_contracts


GATE_VERSION = 1
SIDES = {"left", "right"}
CHANNEL_DIMENSIONS = {
    "visual": editorial_benchmark.VISUAL_DIMENSIONS,
    "story": editorial_benchmark.STORY_DIMENSIONS,
    "audio": editorial_benchmark.AUDIO_DIMENSIONS,
}


class PolicyError(ValueError):
    pass


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rate(value, name):
    parsed = _number(value)
    if parsed is None or not 0.0 <= parsed <= 1.0:
        raise PolicyError(f"{name} must be a number from 0 to 1")
    return parsed


def _positive_int(value, name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise PolicyError(f"{name} must be a positive integer")
    if parsed < 1:
        raise PolicyError(f"{name} must be a positive integer")
    return parsed


def _quality_rule(raw, name):
    if not isinstance(raw, dict):
        raise PolicyError(f"{name} must be an object")
    rule = {
        "min_cases_per_family": _positive_int(
            raw.get("min_cases_per_family"),
            f"{name}.min_cases_per_family"),
        "max_loss_rate": _rate(raw.get("max_loss_rate"),
                               f"{name}.max_loss_rate"),
        "min_win_or_tie_rate": _rate(
            raw.get("min_win_or_tie_rate"),
            f"{name}.min_win_or_tie_rate"),
        "max_insufficient_rate": _rate(
            raw.get("max_insufficient_rate"),
            f"{name}.max_insufficient_rate"),
    }
    if raw.get("min_win_rate") is not None:
        rule["min_win_rate"] = _rate(
            raw["min_win_rate"], f"{name}.min_win_rate")
    return rule


def normalize_policy(policy):
    """Validate the policy up front; release gating never guesses defaults."""
    if not isinstance(policy, dict):
        raise PolicyError("policy must be an object")
    families = policy.get("required_families")
    if not isinstance(families, list) or not families:
        raise PolicyError("required_families must be a non-empty array")
    families = list(dict.fromkeys(str(value) for value in families))
    unknown = [value for value in families
               if value not in editorial_contracts.FAMILIES]
    if unknown:
        raise PolicyError("unknown editorial families: " + ", ".join(unknown))

    raw_channels = policy.get("required_channels_by_family")
    if not isinstance(raw_channels, dict):
        raise PolicyError("required_channels_by_family must be an object")
    channels = {}
    for family in families:
        values = raw_channels.get(family)
        if not isinstance(values, list) or not values:
            raise PolicyError(f"{family} needs at least one required channel")
        values = list(dict.fromkeys(str(value) for value in values))
        bad = [value for value in values if value not in CHANNEL_DIMENSIONS]
        if bad:
            raise PolicyError(f"{family} has unknown channels: {', '.join(bad)}")
        channels[family] = values

    normalized = {
        "name": str(policy.get("name") or "unnamed release policy")[:120],
        "required_families": families,
        "required_channels_by_family": channels,
        "quality": _quality_rule(policy.get("quality"), "quality"),
        "quality_by_opponent": {},
        "efficiency": {},
    }
    for opponent, raw in (policy.get("quality_by_opponent") or {}).items():
        opponent = str(opponent).strip()
        if not opponent:
            raise PolicyError("quality_by_opponent keys must be non-empty")
        normalized["quality_by_opponent"][opponent] = _quality_rule(
            raw, f"quality_by_opponent.{opponent}")

    human = policy.get("human_labels")
    if human is not None:
        normalized["human_labels"] = _quality_rule(human, "human_labels")

    efficiency = policy.get("efficiency")
    if not isinstance(efficiency, dict) or not efficiency:
        raise PolicyError("efficiency must contain at least one metric rule")
    for metric, raw in efficiency.items():
        if not isinstance(raw, dict):
            raise PolicyError(f"efficiency.{metric} must be an object")
        rule = {"min_pairs_per_family": _positive_int(
            raw.get("min_pairs_per_family"),
            f"efficiency.{metric}.min_pairs_per_family")}
        for key in ("max_p50_ratio", "max_p95_ratio", "max_candidate_value"):
            if raw.get(key) is not None:
                value = _number(raw[key])
                if value is None or value < 0:
                    raise PolicyError(
                        f"efficiency.{metric}.{key} must be non-negative")
                rule[key] = value
        if len(rule) == 1:
            raise PolicyError(
                f"efficiency.{metric} needs a ratio or absolute ceiling")
        kinds = raw.get("opponent_kinds")
        if kinds is not None:
            if not isinstance(kinds, list) or not kinds:
                raise PolicyError(
                    f"efficiency.{metric}.opponent_kinds must be a non-empty array")
            rule["opponent_kinds"] = list(dict.fromkeys(
                str(value) for value in kinds))
        normalized["efficiency"][str(metric)] = rule
    return normalized


def _candidate_outcome(winner, candidate_side):
    winner = str(winner or "").lower()
    if candidate_side not in SIDES or winner == "insufficient":
        return "insufficient"
    if winner == "tie":
        return "tie"
    if winner == candidate_side:
        return "win"
    if winner in SIDES:
        return "loss"
    return "insufficient"


def _counts(outcomes):
    result = {key: 0 for key in ("win", "loss", "tie", "insufficient")}
    for value in outcomes:
        result[value if value in result else "insufficient"] += 1
    total = len(outcomes)
    result["total"] = total
    result["win_rate"] = round(result["win"] / total, 4) if total else 0.0
    result["loss_rate"] = round(result["loss"] / total, 4) if total else 0.0
    result["win_or_tie_rate"] = round(
        (result["win"] + result["tie"]) / total, 4) if total else 0.0
    result["insufficient_rate"] = round(
        result["insufficient"] / total, 4) if total else 0.0
    return result


def _percentile(values, quantile):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def _threshold_failures(counts, rule, location):
    failures = []
    if counts["total"] < rule["min_cases_per_family"]:
        failures.append({**location, "code": "insufficient_cases",
                         "actual": counts["total"],
                         "required": rule["min_cases_per_family"]})
        return failures
    checks = (
        ("loss_rate", "max_loss_rate", lambda actual, limit: actual > limit),
        ("win_or_tie_rate", "min_win_or_tie_rate",
         lambda actual, limit: actual < limit),
        ("insufficient_rate", "max_insufficient_rate",
         lambda actual, limit: actual > limit),
        ("win_rate", "min_win_rate", lambda actual, limit: actual < limit),
    )
    for actual_key, policy_key, predicate in checks:
        if policy_key not in rule:
            continue
        if predicate(counts[actual_key], rule[policy_key]):
            failures.append({**location, "code": policy_key,
                             "actual": counts[actual_key],
                             "required": rule[policy_key]})
    return failures


def _quality_matrix(results, families, channels, opponent_kind=None):
    matrix = {}
    for family in families:
        family_cases = [row for row in results
                        if row.get("family") == family and
                        (opponent_kind is None or
                         row.get("opponent_kind") == opponent_kind)]
        matrix[family] = {"cases": len(family_cases), "channels": {}}
        for channel in channels[family]:
            dimensions = ("__overall__",) + CHANNEL_DIMENSIONS[channel]
            channel_rows = {}
            for dimension in dimensions:
                outcomes = []
                for case in family_cases:
                    report = (case.get("channels") or {}).get(channel)
                    winner = None
                    if report:
                        if dimension == "__overall__":
                            winner = report.get("overall_winner")
                        else:
                            winner = ((report.get("dimensions") or {}).get(
                                dimension) or {}).get("winner")
                    outcomes.append(_candidate_outcome(
                        winner, case.get("candidate_side")))
                channel_rows[dimension] = _counts(outcomes)
            matrix[family]["channels"][channel] = channel_rows
    return matrix


def _gate_matrix(matrix, rule, scope):
    failures = []
    for family, family_row in matrix.items():
        for channel, dimensions in family_row["channels"].items():
            for dimension, counts in dimensions.items():
                failures.extend(_threshold_failures(
                    counts, rule, {"scope": scope, "family": family,
                                   "channel": channel,
                                   "dimension": dimension}))
    return failures


def _human_matrix(results, families):
    matrix = {}
    for family in families:
        outcomes = []
        for case in results:
            if case.get("family") != family:
                continue
            # Missing labels are evidence gaps, not invisible cases.  This
            # makes max_insufficient_rate meaningful and prevents a curated
            # labeled subset from hiding the unlabeled remainder.
            outcomes.append(_candidate_outcome(
                case.get("human_winner"), case.get("candidate_side")))
        matrix[family] = _counts(outcomes)
    return matrix


def _efficiency_matrix(results, families, rules):
    matrix = {}
    for metric, rule in rules.items():
        matrix[metric] = {}
        for family in families:
            ratios, candidate_values = [], []
            missing = 0
            valid_pairs = 0
            zero_baseline_regressions = 0
            for case in results:
                if case.get("family") != family:
                    continue
                kinds = rule.get("opponent_kinds")
                if kinds and case.get("opponent_kind") not in kinds:
                    continue
                side = case.get("candidate_side")
                opponent = "right" if side == "left" else "left" \
                    if side == "right" else None
                side_metrics = case.get("side_metrics") or {}
                candidate = _number((side_metrics.get(side) or {}).get(metric))
                baseline = _number((side_metrics.get(opponent) or {}).get(metric))
                if candidate is None or baseline is None or \
                        candidate < 0 or baseline < 0:
                    missing += 1
                    continue
                valid_pairs += 1
                candidate_values.append(candidate)
                if baseline == 0:
                    if candidate == 0:
                        ratios.append(1.0)
                    else:
                        zero_baseline_regressions += 1
                    continue
                ratios.append(candidate / baseline)
            matrix[metric][family] = {
                "pairs": valid_pairs, "missing_or_invalid_pairs": missing,
                "zero_baseline_regressions": zero_baseline_regressions,
                "p50_ratio": (round(_percentile(ratios, .5), 4)
                              if ratios else None),
                "p95_ratio": (round(_percentile(ratios, .95), 4)
                              if ratios else None),
                "max_candidate_value": (round(max(candidate_values), 4)
                                        if candidate_values else None),
            }
    return matrix


def evaluate_release(results, policy):
    """Return a transparent pass/fail report with every failed invariant."""
    normalized = normalize_policy(policy)
    if not isinstance(results, list):
        raise ValueError("benchmark results must be an array")
    families = normalized["required_families"]
    channels = normalized["required_channels_by_family"]
    failures = []

    invalid_rows = [index + 1 for index, row in enumerate(results)
                    if not isinstance(row, dict)]
    if invalid_rows:
        failures.append({"scope": "manifest", "code": "invalid_result_rows",
                         "rows": invalid_rows})
    valid_results = [row for row in results if isinstance(row, dict)]
    case_ids = [str(row.get("case_id") or "") for row in valid_results]
    missing_case_ids = [index + 1 for index, row in enumerate(valid_results)
                        if not str(row.get("case_id") or "").strip()]
    if missing_case_ids:
        failures.append({"scope": "manifest", "code": "case_id_missing",
                         "rows": missing_case_ids})
    duplicates = sorted({value for value in case_ids
                         if value and case_ids.count(value) > 1})
    if duplicates:
        failures.append({"scope": "manifest", "code": "duplicate_case_ids",
                         "case_ids": duplicates})
    invalid_sides = [str(row.get("case_id") or "unnamed")
                     for row in valid_results
                     if row.get("candidate_side") not in SIDES]
    if invalid_sides:
        failures.append({"scope": "manifest", "code": "candidate_side_missing",
                         "case_ids": invalid_sides})
    invalid_families = [
        {"case_id": str(row.get("case_id") or "unnamed"),
         "family": row.get("family")}
        for row in valid_results
        if row.get("family") not in editorial_contracts.FAMILIES]
    if invalid_families:
        failures.append({"scope": "manifest", "code": "unknown_family",
                         "cases": invalid_families})
    opponent_identity_required = bool(normalized["quality_by_opponent"]) or any(
        rule.get("opponent_kinds")
        for rule in normalized["efficiency"].values())
    if opponent_identity_required:
        missing_opponents = [
            str(row.get("case_id") or "unnamed") for row in valid_results
            if not str(row.get("opponent_kind") or "").strip()]
        if missing_opponents:
            failures.append({"scope": "manifest",
                             "code": "opponent_kind_missing",
                             "case_ids": missing_opponents})

    quality = _quality_matrix(valid_results, families, channels)
    failures.extend(_gate_matrix(quality, normalized["quality"], "quality"))

    quality_by_opponent = {}
    for opponent, rule in normalized["quality_by_opponent"].items():
        matrix = _quality_matrix(valid_results, families, channels, opponent)
        quality_by_opponent[opponent] = matrix
        failures.extend(_gate_matrix(
            matrix, rule, f"quality_by_opponent:{opponent}"))

    human = None
    if normalized.get("human_labels"):
        human = _human_matrix(valid_results, families)
        for family, counts in human.items():
            failures.extend(_threshold_failures(
                counts, normalized["human_labels"],
                {"scope": "human_labels", "family": family}))

    efficiency = _efficiency_matrix(
        valid_results, families, normalized["efficiency"])
    for metric, family_rows in efficiency.items():
        rule = normalized["efficiency"][metric]
        for family, row in family_rows.items():
            location = {"scope": "efficiency", "family": family,
                        "metric": metric}
            if row["pairs"] < rule["min_pairs_per_family"]:
                failures.append({**location, "code": "insufficient_metric_pairs",
                                 "actual": row["pairs"],
                                 "required": rule["min_pairs_per_family"]})
            if row["missing_or_invalid_pairs"]:
                failures.append({
                    **location, "code": "missing_metric_pairs",
                    "actual": row["missing_or_invalid_pairs"],
                    "required": 0})
            if row["pairs"] < rule["min_pairs_per_family"]:
                continue
            if row["zero_baseline_regressions"]:
                failures.append({
                    **location, "code": "regression_from_zero_baseline",
                    "actual": row["zero_baseline_regressions"],
                    "required": 0})
            for actual_key, policy_key in (
                    ("p50_ratio", "max_p50_ratio"),
                    ("p95_ratio", "max_p95_ratio"),
                    ("max_candidate_value", "max_candidate_value")):
                if policy_key in rule and (row[actual_key] is None or
                                           row[actual_key] > rule[policy_key]):
                    failures.append({**location, "code": policy_key,
                                     "actual": row[actual_key],
                                     "required": rule[policy_key]})

    return {
        "gate_version": GATE_VERSION,
        "policy_name": normalized["name"],
        "status": "pass" if not failures else "fail",
        "release_allowed": not failures,
        "failures": failures,
        "quality": quality,
        "quality_by_opponent": quality_by_opponent,
        "human_labels": human,
        "efficiency": efficiency,
        "policy": normalized,
    }
