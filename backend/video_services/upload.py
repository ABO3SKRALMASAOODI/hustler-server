"""Upload/tray metadata and bounded recovery of unstarted analysis.

Media transfer remains direct-to-object-storage; these helpers deliberately
accept only database metadata and never open, probe, or decode media bytes.
"""

from video_services.jobs import enqueue
from video_services.project_state import index_job_state


def completed_upload_index(cur, project_id, user_id, asset_id, *, can_retry):
    """Reuse the index receipt, or retry a proven pre-execution rollout refusal.

    The caller holds the project row lock used by upload completion and Studio
    recovery. Preserve the failed job as evidence and create at most two new
    attempts per six hours, sharing Studio's per-asset recovery budget. Never
    replay an ambiguous execution or a real media/validation failure here.
    """
    cur.execute("""SELECT id, state, error, payload, result FROM video_jobs
                   WHERE project_id = %s AND type = 'index'
                     AND COALESCE(payload->>'proxy_only', '') <> 'true'
                     AND payload->>'asset_id' = %s
                   ORDER BY id DESC LIMIT 1""", (project_id, str(asset_id)))
    job = cur.fetchone()
    if not job:
        return {"index_job_id": None}
    receipt = {"index_job_id": job["id"], "index_job_state": job["state"]}
    if job["state"] != "failed":
        return receipt
    receipt["index_error"] = job.get("error")
    failure = (job.get("result") or {}).get("failure") or {}
    if not can_retry or failure.get("kind") != "provider_rollout_pending":
        return receipt
    state = index_job_state(cur, project_id, asset_id)
    if state["active"] or state["recent_count"] >= 3:
        return receipt
    old_payload = job.get("payload") or {}
    # Recompute provider admission and reset its deferral counters for the new
    # job, while keeping the original's source/proxy and policy contract.
    payload = {key: old_payload[key] for key in (
        "asset_id", "client_proxy_key", "dedup_src", "reindex",
        "execution_policy") if key in old_payload}
    payload.update(asset_id=asset_id, retry_of=job["id"])
    job_id = enqueue(cur, project_id, user_id, "index", payload)
    return {"index_job_id": job_id, "index_job_state": "queued",
            "index_restarted": True}


def clean_dimension(value, low, high):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def serialize_asset(asset):
    return {
        "id": asset["id"], "kind": asset["kind"],
        "storage_key": asset["storage_key"], "bytes": asset["bytes"],
        "duration_s": asset["duration_s"], "width": asset["width"],
        "height": asset["height"], "fps": asset["fps"],
        "sha256": asset["sha256"], "meta": asset.get("meta") or {},
        "created_at": (asset["created_at"].isoformat()
                       if asset.get("created_at") else None),
    }
