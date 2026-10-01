"""Bound one account's compute without rejecting its durable queued work.

Limits cover dispatcher-owned jobs, not the number of connected MCP clients.
The claim transaction takes one short advisory lock before counting running
jobs, so concurrent dispatchers cannot all admit against the same free slot.
No lock survives the database commit or wraps a remote execution.
"""
import config


def policies():
    if not config._CLOUDFLARE_REMOTE_EXEC:
        return ()
    return (
        (("mcp_tool",), 20, 6),
        (("agent_turn",), 5, 2),
        (("shorts_plan",), 8, 2),
        (("index", "final"), 8, 2),
        (("preview", "preview_check", "filmstrip"), 20, 6),
    )


def claim_filter(types, has_remote_ledger):
    clauses, params = [], []
    for family, fleet_limit, account_limit in policies():
        if not set(types).intersection(family):
            continue
        remote_live = "" if not has_remote_ledger else """
            OR EXISTS (SELECT 1 FROM remote_executions admission_remote
                WHERE admission_remote.job_id = admission_live.id
                  AND admission_remote.total_claims = admission_live.total_claims
                  AND admission_remote.state IN ('submitted', 'running')
                  AND admission_remote.deadline_at > NOW())"""
        live = f"""admission_live.type = ANY(%s)
            AND admission_live.id <> video_jobs.id
            AND admission_live.state = 'running'
            AND (admission_live.heartbeat_at >= NOW()
                 - make_interval(secs => %s) {remote_live})"""
        clauses.append(f"""AND (video_jobs.type <> ALL(%s) OR (
            (SELECT COUNT(*) FROM video_jobs admission_live
             WHERE {live}) < %s
            AND (SELECT COUNT(*) FROM video_jobs admission_live
                 WHERE admission_live.user_id = video_jobs.user_id
                   AND {live}) < %s))""")
        params.extend([list(family), list(family), config.STALE_AFTER_S,
                       fleet_limit, list(family), config.STALE_AFTER_S,
                       account_limit])
    return "\n".join(clauses), params


def lock_claim(cur):
    # A busy claim returns to the regular lane poll; it consumes no attempt.
    cur.execute("SELECT pg_try_advisory_xact_lock(861231, 1) AS acquired")
    row = cur.fetchone()
    return bool(row and row["acquired"])
