"""Shared successful-job commit path for local and request-based workers.

An agent turn can now execute on Cloud Run while the Render dispatcher merely
waits on HTTP. The process that actually ran the stateful turn must own the
credit charge and terminal job write: if Render redeploys mid-request, the
Cloud Run request continues and still commits exactly once.
"""

import config
import db as dbx


def _result_is_billable(job, result):
    """Resolve rolling-deploy results without making absence mean "charge".

    Current agent workers always send an explicit boolean.  A legacy worker
    can omit it on an early/blocked return (notably ``awaiting_user``), so an
    agent result without the field is billable only when it also contains the
    affirmative terminal facts of a useful reply.  This retains billing for a
    read-only Q&A while defaulting ambiguous/no-change results to the user's
    favour.  Shorts keeps its historical default for rolling deploys.
    """
    payload = job.get("payload") or {}
    if payload.get("operator_repair") is True:
        # Production recovery jobs correct work the product previously
        # delivered incorrectly or failed to finish.  They are created only
        # by the internal operator path; charging the customer a second time
        # would turn our reliability incident into their bill.
        return False
    if job["type"] != "agent_turn":
        return bool(result.get("billable", True))
    if "billable" in result:
        # The worker contract is a JSON boolean.  Do not let a malformed
        # truthy string such as "false" turn a safe default into a debit.
        return result.get("billable") is True
    return (result.get("status") == "replied"
            and result.get("outcome") in {"fulfilled", "partial"})


def _qualifies_subscribe_gate(job, result):
    """Whether this committed result consumed the account's free real edit."""
    if (job.get("payload") or {}).get("operator_repair") is True:
        return False
    if job["type"] == "agent_turn":
        return (result.get("status") == "replied"
                and result.get("outcome") in {"fulfilled", "partial"}
                and result.get("edl_changed") is True)
    if job["type"] == "shorts_plan":
        rendered = result.get("rendered_clips")
        clips = rendered if rendered is not None else result.get("clips")
        try:
            return int(clips or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


def finalize_success(worker_db, job, result, lease_claim):
    """Charge (best effort) and atomically fence the terminal job write.

    Returns ``True`` when this execution lease committed the result and
    ``False`` when a reaper/replacement already superseded it.
    """
    if not isinstance(result, dict):
        return worker_db.run(
            dbx.finish_job, job["id"], "done", None, result, lease_claim)

    accounted = job["type"] in ("agent_turn", "shorts_plan")
    billable = accounted and _result_is_billable(job, result)
    qualifies = accounted and _qualifies_subscribe_gate(job, result)
    rendered = result.get("rendered_clips")
    clip_count = rendered if rendered is not None else result.get("clips")
    try:
        clip_count = max(0, int(clip_count or 0))
    except (TypeError, ValueError):
        clip_count = 0
    extra = (config.SHORTS_CLIP_CREDITS * clip_count
             if job["type"] == "shorts_plan" else 0.0)
    if job["type"] == "agent_turn" and not billable:
        result["credits_charged"] = 0.0
        print(f"[job {job['id']}] not charged — the turn produced nothing "
              f"usable ({result.get('truncated') and 'truncated'})",
              flush=True)

    if accounted:
        payload = job.get("payload") or {}
        try:
            accounting_job_id = int(
                payload.get("root_agent_job_id") or job["id"])
        except (TypeError, ValueError):
            accounting_job_id = int(job["id"])
        terminal = worker_db.run(
            dbx.finish_accounted_job, job["id"], result, lease_claim,
            job["user_id"], billable, extra, qualifies, accounting_job_id)
        committed = bool((terminal or {}).get("committed"))
        if committed and (terminal or {}).get("charged") is not None:
            result["credits_charged"] = terminal["charged"]
        if (terminal or {}).get("billing_error"):
            print(f"[job {job['id']}] credit charge failed: "
                  f"{terminal['billing_error']}", flush=True)
        if (terminal or {}).get("qualification_error"):
            print(f"[job {job['id']}] subscribe qualification dropped: "
                  f"{terminal['qualification_error']}", flush=True)
    else:
        committed = worker_db.run(
            dbx.finish_job, job["id"], "done", None, result, lease_claim)
    if not committed:
        return False
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
