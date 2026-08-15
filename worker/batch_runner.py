"""Run one final/index as a Cloud Run Job and own its terminal DB state."""

import base64
import json
import os
import sys
import threading
import time
import traceback

import config
import compute_cost
import db as dbx
import failure_policy
import http_server
import indexer
import job_completion
import renderer


RUNNERS = {"index": indexer.run_index_job, "final": renderer.run_render_job}


def _decode_job(raw):
    padding = "=" * (-len(raw) % 4)
    return json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))


def _notify_failure(worker_db, job, error):
    note = {
        "final": ("The final export failed ({err}). Press Download to try "
                  "again."),
        "index": ("I couldn't analyze that video ({err}). Try uploading it "
                  "again, or a different format like mp4."),
    }.get(job.get("type"))
    if job.get("type") == "final" and \
            not failure_policy.decision_for(error, "final").retryable:
        note = ("This edit did not pass the export safety check ({err}). The "
                "timeline needs to be repaired before exporting; pressing "
                "Download again on this same version will not fix it.")
    if not note:
        return
    try:
        project = worker_db.run(dbx.get_project, job["project_id"])
        if project and project.get("chat_session_id"):
            worker_db.run(
                dbx.add_message, project["chat_session_id"], "assistant",
                note.format(err=str(error)[:160]),
                {"error": "job_failed", "job": job["id"],
                 "executor": "cloud_run_job"})
    except Exception as exc:
        print(f"[batch {job.get('id')}] failure note failed: {exc}",
              flush=True)


def run(job):
    config.require_core()
    if config.WORKER_ROLE != "batch_executor":
        raise SystemExit("batch_runner requires WORKER_ROLE=batch_executor")
    runner = RUNNERS.get(job.get("type"))
    if not runner:
        raise SystemExit(f"batch executor refuses {job.get('type')!r}")

    job_id = job["id"]
    lease_claim = job.get("total_claims")
    db = http_server._LeasedDb(job_id, lease_claim)
    dbx.track_job(job_id)
    threading.Thread(target=dbx.heartbeat_forever, daemon=True,
                     name="batch-heartbeat").start()
    t0 = time.monotonic()
    try:
        if not db.run(dbx.lease_is_current, job_id, lease_claim):
            print(f"[batch {job_id}] claim {lease_claim} was superseded "
                  "before startup; exiting without compute", flush=True)
            return 0
        print(f"[batch {job_id}] start {job['type']} project="
              f"{job.get('project_id')} claim={lease_claim}", flush=True)
        result = runner(db, job)
        total = round(time.monotonic() - t0, 2)
        if isinstance(result, dict):
            timings = result.setdefault("timings", {})
            timings["queue_wait_s"] = job.get("_queue_wait_s")
            timings["total_s"] = total
            timings["execution_mode"] = "cloud_run_job"
            compute_cost.annotate_batch(timings, total)
        if not job_completion.finalize_success(db, job, result, lease_claim):
            raise dbx.JobLeaseLost(
                f"job {job_id} claim {lease_claim} was superseded at commit")
        print(f"[batch {job_id}] done in {total}s", flush=True)
        return 0
    except Exception as error:
        traceback.print_exc()
        decision = failure_policy.decision_for(error, job.get("type"))
        if decision.retryable and int(job.get("attempts") or 0) \
                < decision.max_attempts:
            changed = db.run(dbx.requeue_job, job_id, error, lease_claim)
            print(f"[batch {job_id}] {'requeued' if changed else 'stale'} "
                  f"after {decision.kind}: {error}", flush=True)
        else:
            result = {
                "failure": decision.payload(error),
                "timings": {"total_s": round(time.monotonic() - t0, 2),
                            "execution_mode": "cloud_run_job"},
            }
            compute_cost.annotate_batch(
                result["timings"], result["timings"]["total_s"])
            changed = db.run(dbx.finish_job, job_id, "failed", error, result,
                             lease_claim)
            if changed:
                db.run(dbx.bump_metric, "job_failed")
                _notify_failure(db, job, error)
            print(f"[batch {job_id}] terminal={bool(changed)} "
                  f"kind={decision.kind}: {error}", flush=True)
        # Application state is authoritative and Cloud Run platform retries
        # are disabled. Exit zero so its UI does not imply another execution.
        return 0
    finally:
        dbx.untrack_job(job_id)
        db.reset()


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else os.getenv("VALMERA_BATCH_JOB", "")
    if not raw:
        raise SystemExit("missing encoded job payload")
    raise SystemExit(run(_decode_job(raw)))


if __name__ == "__main__":
    main()
