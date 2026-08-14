"""Shared successful-job commit path for local and request-based workers.

An agent turn can now execute on Cloud Run while the Render dispatcher merely
waits on HTTP. The process that actually ran the stateful turn must own the
credit charge and terminal job write: if Render redeploys mid-request, the
Cloud Run request continues and still commits exactly once.
"""

import config
import db as dbx


def finalize_success(worker_db, job, result, lease_claim):
    """Charge (best effort) and atomically fence the terminal job write.

    Returns ``True`` when this execution lease committed the result and
    ``False`` when a reaper/replacement already superseded it.
    """
    if not isinstance(result, dict):
        return worker_db.run(
            dbx.finish_job, job["id"], "done", None, result, lease_claim)

    if job["type"] in ("agent_turn", "shorts_plan") \
            and result.get("billable", True):
        try:
            # A shorts_plan now scouts and seeds LOCKED story cuts; it does
            # not render or creatively edit them.  New workers report
            # rendered_clips=0, so selection is charged only for its model
            # turn.  Keep the old clips fallback for jobs completed by an
            # older worker during a rolling deploy.
            rendered = result.get("rendered_clips")
            clip_count = (rendered if rendered is not None
                          else result.get("clips"))
            extra = (config.SHORTS_CLIP_CREDITS * int(clip_count or 0)
                     if job["type"] == "shorts_plan" else 0.0)
            charged = worker_db.run(
                dbx.charge_turn_credits, job["user_id"], job["id"], extra)
            result["credits_charged"] = charged
        except Exception as exc:
            # Billing must never erase a finished edit. charge_turn_credits is
            # idempotent by job id, so a later reconciliation can repair it.
            print(f"[job {job['id']}] credit charge failed: {exc}", flush=True)
    elif job["type"] == "agent_turn":
        result["credits_charged"] = 0.0
        print(f"[job {job['id']}] not charged — the turn produced nothing "
              f"usable ({result.get('truncated') and 'truncated'})",
              flush=True)

    committed = worker_db.run(
        dbx.finish_job, job["id"], "done", None, result, lease_claim)
    if committed and job["type"] == "final":
        try:
            asset_id = (result.get("render_asset_id")
                        or result.get("asset_id"))
            worker_db.run(
                dbx.record_client_event, job["user_id"], job["project_id"],
                "export_render_done",
                {"job_id": job["id"],
                 "version": (job.get("payload") or {}).get("edl_version"),
                 "duration_s": result.get("duration_s")},
                asset_id)
        except Exception as exc:
            print(f"[job {job['id']}] export event dropped: {exc}",
                  flush=True)
    return committed
