"""Queue ownership and admission helpers for Studio/MCP jobs."""

import os

from psycopg2.extras import Json


def execution_policy():
    mode = os.getenv("EXECUTION_POLICY_MODE", "legacy").strip().lower()
    return mode if mode in {"legacy", "redesign"} else "legacy"


def enqueue(cur, project_id, user_id, job_type, payload):
    stamped = dict(payload or {})
    stamped.setdefault("execution_policy", execution_policy())
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
