"""Queue ownership and admission helpers for Studio/MCP jobs."""

import os

from psycopg2.extras import Json

from video_services import harness_route


def execution_policy():
    mode = os.getenv("EXECUTION_POLICY_MODE", "legacy").strip().lower()
    return mode if mode in {"legacy", "redesign"} else "legacy"


def enqueue(cur, project_id, user_id, job_type, payload):
    stamped = dict(payload or {})
    stamped.setdefault("execution_policy", execution_policy())
    # Harness v2 routing (off by default): a chat turn or MCP call is stamped
    # once here, by the rule the worker's db.enqueue_job shares
    # (harness_route.stamp); a stamp already there is kept and a one-request
    # override (``harness_request``) is never stored.
    harness_route.stamp(cur, user_id, project_id, job_type, stamped)
    if job_type == "preview" and stamped.get("source") == "user_edit" \
            and not stamped.get("force") and stamped.get("edl_version"):
        # The player needs a watchable file, not its own lower-resolution
        # encode alongside the editor's approval render (#74). Share the
        # worker's transaction lock so either arrival order has one owner.
        version = int(stamped["edl_version"])
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (int(project_id), version))
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'preview'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, str(version)))
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        # The player can retry an editor's exact version between a failed
        # proof and its recovery. The running First cut still owns the reply.
        cur.execute("""SELECT parent.id AS parent_id, child.payload AS render_payload
                         FROM video_jobs child JOIN video_jobs parent ON
                              parent.project_id = child.project_id
                              AND child.payload->>'agent_job_id' = parent.id::text
                        WHERE child.project_id = %s AND child.type IN ('preview','preview_check')
                          AND child.payload->>'edl_version' = %s
                          AND parent.state = 'running' AND parent.payload->>'first_cut' = 'true'
                        ORDER BY child.id DESC LIMIT 1""", (project_id, str(version)))
        owner = cur.fetchone()
        if owner:
            stamped.update(source="agent_preview", agent_job_id=owner["parent_id"],
                           first_film=True, quality="approval", audio_model_review=False)
            if (owner["render_payload"] or {}).get("first_cut_original"):
                stamped["first_cut_original"] = True
    cur.execute(
        """INSERT INTO video_jobs (project_id, user_id, type, payload)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (project_id, int(user_id), job_type, Json(stamped)),
    )
    return cur.fetchone()["id"]


# The work a user STARTS that spends Valmera's models: a chat turn and a
# shorts plan. An MCP tool call ('mcp_tool') is a client's own synchronous
# editor call (render_preview, look_at, keep_segments ...): routes/mcp.py
# queues every one without consulting MAX_CONCURRENT_JOBS_PER_USER and the
# worker serializes them per project, so the cap never limits them.
STARTED_JOB_TYPES = ("agent_turn", "shorts_plan")
ORCHESTRATION_JOB_TYPES = STARTED_JOB_TYPES + ("mcp_tool",)


def running_orchestration_jobs(cur, user_id, *, tool_calls=True):
    """Count model-spend jobs, not child media jobs sharing their request.

    tool_calls=False counts only the chat turns and shorts plans the cap
    admits. Pass it where an account's own parallel MCP editors must not
    starve the caller: 22-23 Oct 2026, the owner's editors' render_preview /
    look_at / keep_segments calls held all three slots, and the shorts plan
    of the next podcast was refused while nothing it competes with ran."""
    types = list(ORCHESTRATION_JOB_TYPES if tool_calls else STARTED_JOB_TYPES)
    cur.execute(
        """SELECT COUNT(*) AS n FROM video_jobs
           WHERE user_id = %s AND state IN ('queued','running')
             AND type = ANY(%s)""",
        (int(user_id), types),
    )
    return (cur.fetchone() or {}).get("n") or 0


def running_started_jobs(cur, user_id):
    """The chat turns and shorts plans an account has in flight: what a new
    shorts plan competes with. Never the account's MCP tool calls."""
    return running_orchestration_jobs(cur, user_id, tool_calls=False)
