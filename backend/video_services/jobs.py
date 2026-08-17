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


def running_orchestration_jobs(cur, user_id):
    """Count model-spend jobs, not child media jobs sharing their request."""
    cur.execute(
        """SELECT COUNT(*) AS n FROM video_jobs
           WHERE user_id = %s AND state IN ('queued','running')
             AND type IN ('agent_turn', 'shorts_plan', 'mcp_tool')""",
        (int(user_id),),
    )
    return cur.fetchone()["n"]
