#!/usr/bin/env python3
"""Emit a privacy-safe, read-only subscriber/MCP reliability snapshot.

Only aggregate counts and timestamps leave this process. The command never
prints connection details, user identifiers, project identifiers, messages,
or raw provider errors.
"""

import argparse
from collections import Counter
from datetime import datetime
import json
import os

import psycopg2


def subscriber_failure_category(error):
    text = str(error or "").lower()
    if "_metric" in text or "unboundlocalerror" in text:
        return "agent_metric_closure"
    if "shard is busy" in text or "capacity busy" in text:
        return "cloudflare_capacity_busy"
    if "changed-section piece" in text or (
            "duration" in text and ("mismatch" in text or "expected" in text)):
        return "preview_duration_mismatch"
    if "max() arg is an empty sequence" in text:
        return "empty_sequence_max"
    if "could not be recovered" in text or (
            "cloudflare" in text and "recover" in text):
        return "cloudflare_unrecovered"
    return "other"


def mcp_failure_category(error):
    text = str(error or "").lower()
    if "shard is busy" in text or "capacity busy" in text:
        return "cloudflare_capacity_busy"
    if "modal" in text and any(
            word in text for word in ("billing", "limit", "credit", "payment")):
        return "modal_billing_or_limit"
    if "readiness mismatch" in text:
        return "executor_version_mismatch"
    if "no container instance" in text:
        return "cloudflare_container_unavailable"
    if "hasn't finished analyzing" in text or "has not finished analyzing" in text:
        return "project_not_indexed"
    if "executor" in text and any(
            word in text for word in ("timeout", "unavailable")):
        return "executor_timeout_or_unavailable"
    return "other"


def _count_map(rows, *keys):
    output = {}
    for row in rows:
        cursor = output
        for key in row[:len(keys) - 1]:
            cursor = cursor.setdefault(str(key), {})
        cursor[str(row[len(keys) - 1])] = int(row[len(keys)])
    return output


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def build_snapshot(conn, days=7):
    days = max(1, min(30, int(days)))
    interval = f"{days} days"
    result = {"window_days": days}

    with conn.cursor() as cur:
        cur.execute("SELECT NOW()")
        result["generated_at"] = _iso(cur.fetchone()[0])

        cur.execute("""
            WITH paid_users AS (
              SELECT DISTINCT user_id FROM payments
               WHERE status = 'completed' AND amount_cents > 0
            )
            SELECT j.type, j.state, COUNT(*)
              FROM video_jobs j JOIN paid_users p ON p.user_id = j.user_id
             WHERE j.created_at >= NOW() - %s::interval
             GROUP BY j.type, j.state ORDER BY j.type, j.state
        """, (interval,))
        result["subscriber_jobs"] = _count_map(cur.fetchall(), "type", "state")

        cur.execute("""
            WITH paid_users AS (
              SELECT DISTINCT user_id FROM payments
               WHERE status = 'completed' AND amount_cents > 0
            )
            SELECT j.error
              FROM video_jobs j JOIN paid_users p ON p.user_id = j.user_id
             WHERE j.state = 'failed'
               AND j.created_at >= NOW() - %s::interval
        """, (interval,))
        result["subscriber_failure_categories"] = dict(sorted(Counter(
            subscriber_failure_category(row[0])
            for row in cur.fetchall()).items()))

        cur.execute("""
            WITH paid_users AS (
              SELECT DISTINCT user_id FROM payments
               WHERE status = 'completed' AND amount_cents > 0
            ), turns AS (
              SELECT j.*,
                     COALESCE(NULLIF(
                       j.payload->>'root_agent_job_id', '')::bigint,
                       j.id) AS root_id
                FROM video_jobs j
                JOIN paid_users p ON p.user_id = j.user_id
               WHERE j.type = 'agent_turn'
                 AND j.created_at >= NOW() - %s::interval
            ), roots AS (
              SELECT root_id, COUNT(*) AS slices,
                     BOOL_OR(state = 'failed') AS failed,
                     BOOL_OR(state = 'done'
                       AND result->>'status' = 'replied') AS replied,
                     BOOL_OR(state = 'done'
                       AND result->>'status' = 'continued') AS continued,
                     BOOL_OR(state = 'done'
                       AND result->>'status' IS NULL) AS no_status,
                     MAX(COALESCE(NULLIF(
                       payload->>'continuation_sequence', '')::int, 0))
                       AS max_sequence,
                     MAX(updated_at) AS last_updated
                FROM turns GROUP BY root_id
            )
            SELECT CASE
                     WHEN replied THEN 'replied'
                     WHEN failed THEN 'failed_without_replied_slice'
                     WHEN continued THEN 'continued_without_terminal_slice'
                     WHEN no_status THEN 'steered_without_status'
                     ELSE 'other'
                   END AS outcome,
                   COUNT(*), MAX(slices), MAX(max_sequence),
                   ROUND(MAX(EXTRACT(EPOCH FROM
                     (NOW() - last_updated)) / 60))::int
              FROM roots GROUP BY outcome ORDER BY outcome
        """, (interval,))
        result["subscriber_logical_turns"] = [{
            "outcome": outcome, "count": int(count),
            "max_slices": int(max_slices or 0),
            "max_sequence": int(max_sequence or 0),
            "oldest_updated_minutes": int(age or 0),
        } for outcome, count, max_slices, max_sequence, age
            in cur.fetchall()]

        cur.execute("""
            WITH paid_users AS (
              SELECT DISTINCT user_id FROM payments
               WHERE status = 'completed' AND amount_cents > 0
            ), turns AS (
              SELECT j.*,
                     COALESCE(NULLIF(
                       j.payload->>'root_agent_job_id', '')::bigint,
                       j.id) AS root_id
                FROM video_jobs j
                JOIN paid_users p ON p.user_id = j.user_id
               WHERE j.type = 'agent_turn'
                 AND j.created_at >= NOW() - %s::interval
            ), failed_roots AS (
              SELECT root_id
                FROM turns GROUP BY root_id
              HAVING BOOL_OR(state = 'failed')
                 AND NOT BOOL_OR(state = 'done'
                   AND result->>'status' = 'replied')
            ), prompts AS (
              SELECT f.root_id,
                     NULLIF(r.payload->>'message_id', '')::bigint AS message_id
                FROM failed_roots f JOIN video_jobs r ON r.id = f.root_id
            ), windows AS (
              SELECT p.root_id, u.session_id, u.id AS prompt_id,
                     (SELECT MIN(n.id) FROM chat_messages n
                       WHERE n.session_id = u.session_id
                         AND n.role = 'user' AND n.id > u.id) AS next_user_id
                FROM prompts p LEFT JOIN chat_messages u ON u.id = p.message_id
            ), scored AS (
              SELECT w.*,
                     EXISTS (
                       SELECT 1 FROM chat_messages a
                        WHERE a.session_id = w.session_id
                          AND a.role = 'assistant' AND a.id > w.prompt_id
                          AND (w.next_user_id IS NULL
                               OR a.id < w.next_user_id)) AS has_reply
                FROM windows w
            )
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE prompt_id IS NULL),
                   COUNT(*) FILTER (WHERE has_reply),
                   COUNT(*) FILTER (
                     WHERE prompt_id IS NOT NULL AND NOT has_reply),
                   COUNT(*) FILTER (
                     WHERE prompt_id IS NOT NULL AND NOT has_reply
                       AND next_user_id IS NULL)
              FROM scored
        """, (interval,))
        total, unlinked, replied, missing, abandoned = cur.fetchone()
        result["failed_turn_reply_coverage"] = {
            "failed_logical_turns": int(total or 0),
            "missing_prompt_link": int(unlinked or 0),
            "assistant_reply_before_next_request": int(replied or 0),
            "no_assistant_reply_before_next_request": int(missing or 0),
            "no_reply_and_no_later_request": int(abandoned or 0),
        }

        cur.execute("""
            SELECT state, COUNT(*)
              FROM video_jobs
             WHERE type = 'mcp_tool'
               AND created_at >= NOW() - %s::interval
             GROUP BY state ORDER BY state
        """, (interval,))
        result["mcp_jobs"] = {
            str(state): int(count) for state, count in cur.fetchall()}

        cur.execute("""
            SELECT error
              FROM video_jobs
             WHERE type = 'mcp_tool' AND state = 'failed'
               AND created_at >= NOW() - %s::interval
        """, (interval,))
        result["mcp_failure_categories"] = dict(sorted(Counter(
            mcp_failure_category(row[0]) for row in cur.fetchall()).items()))

        cur.execute("""
            SELECT
              COUNT(*) FILTER (
                WHERE result->>'text' ~* '^\\s*REJECTED:'),
              COUNT(*) FILTER (
                WHERE COALESCE((result->>'is_error')::boolean, false))
              FROM video_jobs
             WHERE type = 'mcp_tool' AND state = 'done'
               AND created_at >= NOW() - %s::interval
        """, (interval,))
        refused, structured = cur.fetchone()
        result["mcp_done_refusals"] = int(refused or 0)
        result["mcp_done_structured_errors"] = int(structured or 0)

        cur.execute("""
            WITH stats AS (
              SELECT payload->>'tool' AS tool,
                     COUNT(*) AS total,
                     COUNT(*) FILTER (WHERE state = 'done') AS done,
                     COUNT(*) FILTER (WHERE state = 'failed') AS failed,
                     COUNT(*) FILTER (
                       WHERE state = 'done'
                         AND result->>'text' ~* '^\\s*REJECTED:') AS refused
                FROM video_jobs
               WHERE type = 'mcp_tool'
                 AND created_at >= NOW() - %s::interval
               GROUP BY payload->>'tool'
            )
            SELECT tool, total, done, failed, refused
              FROM stats
             WHERE failed > 0 OR refused > 0
             ORDER BY failed + refused DESC, tool
        """, (interval,))
        result["mcp_problem_tools"] = [{
            "tool": tool or "unknown",
            "total": int(total), "done": int(done),
            "failed": int(failed), "refused": int(refused),
            "successful": int(done) - int(refused),
        } for tool, total, done, failed, refused in cur.fetchall()]

        cur.execute("""
            SELECT type, state, COUNT(*),
                   ROUND(EXTRACT(EPOCH FROM
                     (NOW() - MIN(updated_at))) / 60)::int
              FROM video_jobs
             WHERE state IN ('queued', 'running')
             GROUP BY type, state ORDER BY state, type
        """)
        result["open_jobs"] = [{
            "type": job_type, "state": state, "count": int(count),
            "oldest_updated_minutes": int(age or 0),
        } for job_type, state, count, age in cur.fetchall()]

        cur.execute("""
            WITH paid_users AS (
              SELECT DISTINCT user_id FROM payments
               WHERE status = 'completed' AND amount_cents > 0
            ), paid_sessions AS (
              SELECT DISTINCT p.chat_session_id
                FROM projects p JOIN paid_users u ON u.user_id = p.user_id
               WHERE p.chat_session_id IS NOT NULL
            )
            SELECT COUNT(*) FILTER (WHERE m.meta->>'feedback' = 'up'),
                   COUNT(*) FILTER (WHERE m.meta->>'feedback' = 'down')
              FROM chat_messages m
              JOIN paid_sessions s ON s.chat_session_id = m.session_id
             WHERE m.created_at >= NOW() - %s::interval
        """, (interval,))
        up, down = cur.fetchone()
        result["subscriber_feedback"] = {
            "up": int(up or 0), "down": int(down or 0)}

        cur.execute("SELECT to_regclass('public.remote_executions')")
        if cur.fetchone()[0]:
            cur.execute("""
                SELECT provider, state, COUNT(*),
                       MAX(COALESCE(completed_at, last_observed_at,
                                    started_at, submitted_at))
                  FROM remote_executions
                 WHERE COALESCE(completed_at, last_observed_at,
                                started_at, submitted_at)
                       >= NOW() - %s::interval
                 GROUP BY provider, state ORDER BY provider, state
            """, (interval,))
            result["remote_executions"] = [{
                "provider": provider, "state": state, "count": int(count),
                "latest_at": _iso(latest),
            } for provider, state, count, latest in cur.fetchall()]

            cur.execute("""
                SELECT COUNT(*),
                       ROUND(MAX(EXTRACT(EPOCH FROM
                         (NOW() - COALESCE(r.last_observed_at, r.started_at,
                                           r.submitted_at))) / 60))::int
                  FROM remote_executions r
                  JOIN video_jobs j
                    ON j.id = r.job_id
                   AND j.total_claims = r.total_claims
                 WHERE r.state IN ('submitted', 'running')
                   AND j.state IN ('done', 'failed')
            """)
            count, age = cur.fetchone()
            result["remote_terminal_contradictions"] = {
                "count": int(count or 0),
                "oldest_minutes": int(age or 0),
            }
        else:
            result["remote_executions"] = []
            result["remote_terminal_contradictions"] = {
                "count": 0, "oldest_minutes": 0}

        cur.execute("SELECT MAX(created_at), MAX(updated_at) FROM video_jobs")
        created, updated = cur.fetchone()
        result["latest_job_created_at"] = _iso(created)
        result["latest_job_updated_at"] = _iso(updated)

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="comparison window from 1 to 30 days (default: 7)")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL is required")

    conn = psycopg2.connect(
        dsn, connect_timeout=15,
        options="-c default_transaction_read_only=on")
    try:
        print(json.dumps(build_snapshot(conn, args.days), indent=2,
                         sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
