"""Fail-closed production gate for Cloudflare Containers rollout stages.

The gate consumes only aggregate job telemetry. It never reads chat content,
EDLs, transcripts, media paths or user identity. Cloudflare is allowed to
advance only when like-for-like input cohorts prove reliability, artifact
integrity, end-to-end speed and gross cost against Modal.
"""

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone


DEFAULT_TYPES = ("preview_check", "filmstrip", "index")
PROVIDERS = ("cloudflare", "modal")
CLOUDFLARE_MONTHLY_BASE_USD = 5.0
HAZARD_KINDS = {
    "executor_capacity", "lease_lost", "provider_budget_exhausted",
    "worker_died", "deadline_exceeded", "duplicate_execution",
    "remote_ownership_unconfirmed",
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _percentile(values, quantile):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    at = (len(values) - 1) * quantile
    lo, hi = int(at), min(len(values) - 1, int(at) + 1)
    return values[lo] + (values[hi] - values[lo]) * (at - lo)


def _band(value, limits, labels):
    if value is None:
        return "missing"
    for limit, label in zip(limits, labels):
        if value <= limit:
            return label
    return labels[-1].replace("<=", ">")


def _cohort(sample):
    return (
        sample["type"],
        _band(sample["input_bytes"],
              (256 * 1024 ** 2, 1024 ** 3, 4 * 1024 ** 3),
              ("<=256MiB", "<=1GiB", "<=4GiB")),
        _band(sample["duration_s"], (300, 1800, 3600),
              ("<=5m", "<=30m", "<=60m")),
        "cache" if sample["cache_hit"] else "uncached",
        "cold" if sample["cold"] else "warm",
    )


def _failure_kind(row, result):
    failure = result.get("failure") or {}
    kind = str(failure.get("kind") or failure.get("failure_kind") or "")
    if kind:
        return kind
    text = str(row.get("error") or "").lower()
    if "lease" in text and "lost" in text:
        return "lease_lost"
    if "out of memory" in text or "oom" in text or "capacity" in text:
        return "executor_capacity"
    if "deadline" in text or "timed out" in text or "timeout" in text:
        return "deadline_exceeded"
    if "duplicate" in text:
        return "duplicate_execution"
    return "other" if row.get("state") == "failed" else ""


def _artifact_complete(job_type, result):
    if job_type == "index":
        return bool(result.get("sha256"))
    if job_type in {"preview", "final"}:
        return bool(result.get("render_asset_id") and
                    _number(result.get("duration_s")) is not None)
    if job_type == "preview_check":
        return bool(result.get("render_asset_id") and
                    isinstance(result.get("changed_ranges"), list))
    if job_type == "filmstrip":
        return bool(result.get("available") is True and result.get("key"))
    return bool(result)


def sample_from_row(row):
    payload = row.get("payload") or {}
    result = row.get("result") or {}
    timings = result.get("timings") or {}
    shape = payload.get("execution_shape") or {}
    provider = str(timings.get("compute_provider") or "").lower()
    dispatch = str(timings.get("dispatch_provider") or
                   payload.get("execution_provider") or "").lower()
    total = _number(timings.get("total_s"))
    queue = _number(timings.get("queue_wait_s"))
    start = _number(timings.get("provider_start_s"))
    end_to_end = (queue + start + total
                  if None not in (queue, start, total) else None)
    state = str(row.get("state") or "")
    failure_kind = _failure_kind(row, result)
    return {
        "type": str(row.get("type") or ""),
        "provider": provider,
        "dispatch_provider": dispatch,
        "provider_fallback": bool(timings.get("provider_fallback")),
        "state": state,
        "success": state == "done",
        "failure_kind": failure_kind,
        "hazard": failure_kind in HAZARD_KINDS,
        "artifact_complete": (
            _artifact_complete(str(row.get("type") or ""), result)
            if state == "done" else True),
        "input_bytes": _number(shape.get("total_bytes")),
        "duration_s": _number(shape.get("max_duration_s")),
        "cache_hit": bool(timings.get("cache_hit")),
        "cold": bool(timings.get("container_first_input")),
        "runner_s": total,
        "end_to_end_s": end_to_end,
        "cost_usd": _number(
            timings.get("gross_compute_usd_with_tail_ceiling")),
        "code_version": str(
            timings.get("executor_code_version") or "").strip(),
        "adapter_version": str(
            timings.get("executor_adapter_version") or "").strip(),
    }


def _provider_summary(rows):
    successes = [row for row in rows if row["success"]]
    costs = [row["cost_usd"] for row in rows
             if row["cost_usd"] is not None]
    return {
        "jobs": len(rows),
        "successes": len(successes),
        "failures": len(rows) - len(successes),
        "failure_rate": round((len(rows) - len(successes)) / len(rows), 4)
                        if rows else None,
        "p50_end_to_end_s": _percentile(
            [row["end_to_end_s"] for row in successes
             if row["end_to_end_s"] is not None], .5),
        "p95_end_to_end_s": _percentile(
            [row["end_to_end_s"] for row in successes
             if row["end_to_end_s"] is not None], .95),
        "p95_runner_s": _percentile(
            [row["runner_s"] for row in successes
             if row["runner_s"] is not None], .95),
        "gross_cost_per_success_usd": (
            sum(costs) / len(successes) if len(costs) == len(rows)
            and successes else None),
        "code_versions": sorted({row["code_version"] for row in rows
                                 if row.get("code_version")}),
        "adapter_versions": sorted({row["adapter_version"] for row in rows
                                    if row.get("adapter_version")}),
    }


def _ratio(candidate, baseline):
    if candidate is None or baseline is None:
        return None
    if baseline == 0:
        return 1.0 if candidate == 0 else math.inf
    return candidate / baseline


def _cost_with_base(summary, provider, fixed_fee_per_success):
    variable = summary.get("gross_cost_per_success_usd")
    fixed = fixed_fee_per_success if provider == "cloudflare" else 0.0
    summary["fixed_base_fee_per_success_usd"] = fixed
    summary["cost_per_success_with_base_usd"] = (
        variable + fixed if variable is not None else None)
    return summary


def evaluate(samples, *, job_types=DEFAULT_TYPES, min_samples=20,
             min_cohort_samples=3, max_slowdown=1.05,
             expected_percent=None, observation_hours=1.0,
             cloudflare_monthly_base_usd=CLOUDFLARE_MONTHLY_BASE_USD):
    """Return a sanitized pass/fail report for one rollout observation."""
    job_types = tuple(dict.fromkeys(job_types))
    failures = []
    valid = [row for row in samples if row.get("type") in job_types]
    observed_cloudflare_successes = sum(
        1 for row in valid
        if row.get("provider") == "cloudflare" and row.get("success"))
    monthly_scale = 30 * 24 / max(.01, float(observation_hours))
    projected_monthly_cloudflare_successes = (
        observed_cloudflare_successes * monthly_scale)
    fixed_fee_per_success = (
        max(0.0, float(cloudflare_monthly_base_usd)) /
        projected_monthly_cloudflare_successes
        if projected_monthly_cloudflare_successes > 0 else None)

    invalid_provider = [row for row in valid
                        if row.get("provider") not in PROVIDERS]
    if invalid_provider:
        failures.append({"code": "missing_or_invalid_provider_telemetry",
                         "count": len(invalid_provider)})
    valid = [row for row in valid if row.get("provider") in PROVIDERS]

    missing_identity = [row for row in valid
                        if not row.get("code_version")
                        or row.get("code_version") == "unknown"
                        or not row.get("adapter_version")
                        or row.get("adapter_version") == "unknown"]
    if missing_identity:
        failures.append({"code": "missing_deployment_identity",
                         "count": len(missing_identity)})

    fallbacks = [row for row in valid
                 if row.get("dispatch_provider") == "cloudflare"
                 and row.get("provider") != "cloudflare"]
    if fallbacks:
        failures.append({"code": "cloudflare_prelaunch_fallback",
                         "count": len(fallbacks)})
    hazards = [row for row in valid
               if row.get("provider") == "cloudflare" and row.get("hazard")]
    if hazards:
        failures.append({"code": "cloudflare_hazard", "count": len(hazards),
                         "kinds": sorted({row["failure_kind"]
                                          for row in hazards})})
    incomplete = [row for row in valid
                  if row.get("provider") == "cloudflare"
                  and row.get("success") and not row.get("artifact_complete")]
    if incomplete:
        failures.append({"code": "incomplete_cloudflare_artifact",
                         "count": len(incomplete)})

    by_type = {}
    for job_type in job_types:
        typed = [row for row in valid if row["type"] == job_type]
        providers = {provider: [row for row in typed
                                if row["provider"] == provider]
                     for provider in PROVIDERS}
        summaries = {
            provider: _cost_with_base(
                _provider_summary(rows), provider, fixed_fee_per_success)
            for provider, rows in providers.items()}
        by_type[job_type] = {"providers": summaries, "cohorts": {}}
        for provider in PROVIDERS:
            if len(providers[provider]) < min_samples:
                failures.append({"code": "insufficient_provider_samples",
                                 "type": job_type, "provider": provider,
                                 "actual": len(providers[provider]),
                                 "required": min_samples})
            versions = summaries[provider]["adapter_versions"]
            if len(versions) > 1:
                failures.append({
                    "code": "mixed_provider_adapter_versions",
                    "type": job_type, "provider": provider,
                    "count": len(versions)})
        cloudflare_codes = set(summaries["cloudflare"]["code_versions"])
        modal_codes = set(summaries["modal"]["code_versions"])
        if cloudflare_codes and modal_codes and cloudflare_codes != modal_codes:
            failures.append({"code": "executor_code_version_mismatch",
                             "type": job_type,
                             "cloudflare_count": len(cloudflare_codes),
                             "modal_count": len(modal_codes)})
        cloudflare = providers["cloudflare"]
        if cloudflare and not any(row["cold"] for row in cloudflare):
            failures.append({"code": "missing_cold_sample", "type": job_type})
        if cloudflare and not any(not row["cold"] for row in cloudflare):
            failures.append({"code": "missing_warm_sample", "type": job_type})

        cohort_keys = sorted({_cohort(row) for row in cloudflare})
        for key in cohort_keys:
            candidate = [row for row in cloudflare if _cohort(row) == key]
            baseline = [row for row in providers["modal"]
                        if _cohort(row) == key]
            label = "|".join(key[1:])
            csum = _cost_with_base(
                _provider_summary(candidate), "cloudflare",
                fixed_fee_per_success)
            bsum = _cost_with_base(
                _provider_summary(baseline), "modal",
                fixed_fee_per_success)
            metrics = {
                "cloudflare": csum,
                "modal": bsum,
                "p50_end_to_end_ratio": _ratio(
                    csum["p50_end_to_end_s"], bsum["p50_end_to_end_s"]),
                "p95_end_to_end_ratio": _ratio(
                    csum["p95_end_to_end_s"], bsum["p95_end_to_end_s"]),
                "p95_runner_ratio": _ratio(
                    csum["p95_runner_s"], bsum["p95_runner_s"]),
                "cost_per_success_ratio": _ratio(
                    csum["cost_per_success_with_base_usd"],
                    bsum["cost_per_success_with_base_usd"]),
            }
            by_type[job_type]["cohorts"][label] = metrics
            if len(candidate) < min_cohort_samples or \
                    len(baseline) < min_cohort_samples:
                failures.append({
                    "code": "insufficient_matched_cohort", "type": job_type,
                    "cohort": label, "cloudflare": len(candidate),
                    "modal": len(baseline), "required": min_cohort_samples})
                continue
            if csum["failure_rate"] > bsum["failure_rate"]:
                failures.append({"code": "failure_rate_regression",
                                 "type": job_type, "cohort": label,
                                 "cloudflare": csum["failure_rate"],
                                 "modal": bsum["failure_rate"]})
            for name in ("p50_end_to_end_ratio", "p95_end_to_end_ratio",
                         "p95_runner_ratio"):
                value = metrics[name]
                if value is None or value > max_slowdown:
                    failures.append({"code": name, "type": job_type,
                                     "cohort": label, "actual": value,
                                     "required_max": max_slowdown})
            cost_ratio = metrics["cost_per_success_ratio"]
            if cost_ratio is None or cost_ratio >= 1.0:
                failures.append({"code": "cost_not_lower",
                                 "type": job_type, "cohort": label,
                                 "actual_ratio": cost_ratio,
                                 "required_max_exclusive": 1.0})

    rollout = None
    if expected_percent is not None:
        eligible = [row for row in samples if row.get("type") in job_types
                    and row.get("input_bytes") is not None
                    and row["input_bytes"] <= 4 * 1024 ** 3
                    and row.get("duration_s") is not None
                    and row["duration_s"] <= 3600]
        attempted = [row for row in eligible
                     if row.get("dispatch_provider") == "cloudflare"]
        actual = 100 * len(attempted) / len(eligible) if eligible else None
        p = max(0.0, min(1.0, float(expected_percent) / 100))
        tolerance = max(5.0, 300 * math.sqrt(
            p * (1 - p) / max(1, len(eligible))))
        rollout = {"eligible_jobs": len(eligible),
                   "cloudflare_attempts": len(attempted),
                   "actual_percent": actual,
                   "expected_percent": float(expected_percent),
                   "tolerance_percentage_points": round(tolerance, 2)}
        if actual is None or abs(actual - float(expected_percent)) > tolerance:
            failures.append({"code": "rollout_share_mismatch",
                             **rollout})

    return {
        "gate_version": 1,
        "status": "pass" if not failures else "fail",
        "advance_allowed": not failures,
        "sample_count": len(samples),
        "failures": failures,
        "types": by_type,
        "rollout": rollout,
        "policy": {"job_types": list(job_types),
                   "min_samples": min_samples,
                   "min_cohort_samples": min_cohort_samples,
                   "max_slowdown": max_slowdown,
                   "observation_hours": observation_hours,
                   "cloudflare_monthly_base_usd":
                       cloudflare_monthly_base_usd,
                   "projected_monthly_cloudflare_successes":
                       projected_monthly_cloudflare_successes,
                   "fixed_base_fee_per_cloudflare_success_usd":
                       fixed_fee_per_success},
    }


def apply_remote_health(report, health):
    """Add durable-ownership invariants to an existing sanitized report."""
    report["remote_ownership"] = health
    checks = (
        ("ledger_present", False, "remote_ledger_missing"),
        ("expired_active", True, "expired_active_remote_execution"),
        ("terminal_job_active", True, "terminal_job_with_active_execution"),
        ("duplicate_call_ids", True, "duplicate_remote_call_id"),
    )
    for key, numeric, code in checks:
        value = health.get(key)
        failed = (int(value or 0) > 0) if numeric else not bool(value)
        if failed:
            report["failures"].append({"code": code, "actual": value})
    report["advance_allowed"] = not report["failures"]
    report["status"] = "pass" if report["advance_allowed"] else "fail"
    return report


def _load_rows(conn, since, until, job_types):
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("""SELECT type, state, payload, result, error
                       FROM video_jobs
                       WHERE updated_at >= %s AND updated_at < %s
                         AND type = ANY(%s)
                         AND state IN ('done', 'failed')
                       ORDER BY updated_at""",
                    (since, until, list(job_types)))
        return cur.fetchall()


def _load_remote_health(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.remote_executions') AS name")
        if not (cur.fetchone() or {}).get("name"):
            return {"ledger_present": False, "expired_active": None,
                    "terminal_job_active": None,
                    "duplicate_call_ids": None}
        cur.execute("""SELECT
            COUNT(*) FILTER (
              WHERE r.state IN ('submitted','running')
                AND r.deadline_at <= NOW()) AS expired_active,
            COUNT(*) FILTER (
              WHERE r.state IN ('submitted','running')
                AND j.state IN ('done','failed')) AS terminal_job_active
          FROM remote_executions r
          JOIN video_jobs j ON j.id = r.job_id""")
        health = dict(cur.fetchone() or {})
        cur.execute("""SELECT COUNT(*) AS n FROM (
            SELECT provider, call_id FROM remote_executions
            GROUP BY provider, call_id HAVING COUNT(*) > 1
        ) duplicates""")
        health["duplicate_call_ids"] = int(
            (cur.fetchone() or {}).get("n") or 0)
        health["ledger_present"] = True
        return health


def _parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--until")
    parser.add_argument("--types", default=",".join(DEFAULT_TYPES))
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--min-cohort-samples", type=int, default=3)
    parser.add_argument("--max-slowdown", type=float, default=1.05)
    parser.add_argument("--expected-percent", type=float)
    parser.add_argument("--database-env", default="DATABASE_URL")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    until = _parse_time(args.until) if args.until else datetime.now(timezone.utc)
    since = until - timedelta(hours=max(.01, args.hours))
    job_types = tuple(value.strip() for value in args.types.split(",")
                      if value.strip())
    dsn = os.getenv(args.database_env, "")
    if not dsn:
        parser.error(f"environment variable {args.database_env} is empty")
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor,
                            connect_timeout=8)
    try:
        rows = _load_rows(conn, since, until, job_types)
        remote_health = _load_remote_health(conn)
        conn.rollback()
    finally:
        conn.close()
    samples = [sample_from_row(row) for row in rows]
    report = evaluate(
        samples, job_types=job_types, min_samples=max(1, args.min_samples),
        min_cohort_samples=max(1, args.min_cohort_samples),
        max_slowdown=args.max_slowdown,
        expected_percent=args.expected_percent,
        observation_hours=max(.01, args.hours))
    apply_remote_health(report, remote_health)
    report["window"] = {"start": since.isoformat(),
                        "end": until.isoformat()}
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    print(json.dumps({"status": report["status"],
                      "advance_allowed": report["advance_allowed"],
                      "sample_count": report["sample_count"],
                      "failure_codes": sorted({row["code"]
                                               for row in report["failures"]})},
                     sort_keys=True))
    return 0 if report["advance_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
