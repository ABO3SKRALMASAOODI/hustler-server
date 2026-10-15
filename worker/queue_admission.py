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
        # The account share is work-conserving: it binds only while another
        # account has work of this family waiting. A lone account may grow
        # to the fleet less a small reserve (so a newcomer starts at once);
        # the moment someone else queues, new claims for the busy account
        # stop at its share until the others are served. (Oct 2026: a
        # 14-file upload ran two analyses at a time on an otherwise idle
        # 8-shard batch lane, so its last file waited 41 minutes.)
        account_live = f"""(SELECT COUNT(*) FROM video_jobs admission_live
                  WHERE admission_live.user_id = video_jobs.user_id
                    AND {live})"""
        clauses.append(f"""AND (video_jobs.type <> ALL(%s) OR (
            (SELECT COUNT(*) FROM video_jobs admission_live
             WHERE {live}) < %s
            AND ({account_live} < %s
                 OR ({account_live} < %s AND NOT EXISTS (
                   SELECT 1 FROM video_jobs admission_wait
                   WHERE admission_wait.type = ANY(%s)
                     AND admission_wait.state = 'queued'
                     AND admission_wait.user_id IS DISTINCT FROM
                         video_jobs.user_id
                     AND admission_wait.attempts < %s)))))""")
        params.extend([list(family), list(family), config.STALE_AFTER_S,
                       fleet_limit, list(family), config.STALE_AFTER_S,
                       account_limit, list(family), config.STALE_AFTER_S,
                       lone_account_limit(fleet_limit, account_limit),
                       list(family), _family_attempts(family)])
    return "\n".join(clauses), params


def lone_account_limit(fleet_limit, account_limit):
    """Ceiling for an account while no other account waits in the family."""
    reserve = max(1, int(fleet_limit) // 8)
    return max(int(account_limit), int(fleet_limit) - reserve)


def _family_attempts(family):
    """Attempts below which a queued row of this family is still claimable."""
    if "mcp_tool" in family:
        return config.MAX_ATTEMPTS_MCP
    if "agent_turn" in family:
        return config.MAX_ATTEMPTS_AGENT
    return config.MAX_ATTEMPTS_MEDIA


def lock_claim(cur):
    # A busy claim returns to the regular lane poll; it consumes no attempt.
    cur.execute("SELECT pg_try_advisory_xact_lock(861231, 1) AS acquired")
    row = cur.fetchone()
    return bool(row and row["acquired"])
