"""Provider-neutral executor runtime.

Cloud Run's HTTP server and Modal's durable Functions both call this module.
Keeping the job execution and fenced terminal write in one place makes a
provider cutover a transport change, not a second rendering implementation.
"""

import json
import os
import threading
import time
import traceback

import compute_cost
import config
import db as dbx
import failure_policy
import job_completion
import resource_usage


_heartbeat_started = False
_heartbeat_lock = threading.Lock()


class LeasedDb:
    """Bind every executor progress write to one monotonic queue claim."""

    def __init__(self, job_id, total_claims):
        self._db = dbx.Db()
        self._job_id = job_id
        self._total_claims = total_claims
        self._lost = threading.Event()

    def run(self, fn, *args, **kwargs):
        is_progress = fn is dbx.set_progress and args \
            and args[0] == self._job_id and self._total_claims is not None
        if is_progress:
            kwargs["total_claims"] = self._total_claims
        out = self._db.run(fn, *args, **kwargs)
        if is_progress and out is False:
            self._lost.set()
        return out

    def cancelled(self):
        return self._lost.is_set()

    def reset(self):
        self._db.reset()


def ensure_heartbeat():
    """Start one heartbeat thread per container, including Modal containers."""
    global _heartbeat_started
    with _heartbeat_lock:
        if _heartbeat_started:
            return
        threading.Thread(target=dbx.heartbeat_forever, daemon=True,
                         name="executor-heartbeat").start()
        _heartbeat_started = True


def execute(job, runners):
    """Execute one job and return the established executor JSON envelope."""
    jtype = job.get("type")
    runner = runners.get(jtype)
    if not runner:
        return {"error": f"unsupported job type: {jtype}",
                "retryable": False}

    os.makedirs(config.TMP_DIR, exist_ok=True)

    job_id = job.get("id")
    lease_claim = job.get("total_claims")
    t0 = time.monotonic()
    resource_start = resource_usage.snapshot()
    print(f"[executor] start {jtype} job={job_id} "
          f"project={job.get('project_id')} provider="
          f"{os.getenv('EXECUTOR_PROVIDER', 'cloud_run')}", flush=True)
    db = LeasedDb(job_id, lease_claim)
    if job_id is not None:
        dbx.track_job(job_id)
    try:
        if lease_claim is not None and not db.run(
                dbx.lease_is_current, job_id, lease_claim):
            raise dbx.JobLeaseLost(
                f"job {job_id} execution lease {lease_claim} is no longer current")
        result = runner(db, job)
        dt = round(time.monotonic() - t0, 2)
        execution_timings = {"total_s": dt}
        execution_timings.update(resource_usage.usage_since(resource_start))
        compute_cost.annotate_request(
            execution_timings, dt, config.WORKER_ROLE,
            os.getenv("K_SERVICE", ""))
        completed = False
        if job_id is not None:
            if isinstance(result, dict):
                timings = result.setdefault("timings", {})
                timings["queue_wait_s"] = job.get("_queue_wait_s")
                timings.update(execution_timings)
            completed = job_completion.finalize_success(
                db, job, result, lease_claim)
            if completed is False:
                raise dbx.JobLeaseLost(
                    f"job {job_id} execution lease {lease_claim} was "
                    "superseded before executor completion")
        print("[resources] " + json.dumps({
            "type": jtype, "job_id": job_id, "ok": True,
            **execution_timings,
        }, sort_keys=True, separators=(",", ":")), flush=True)
        print(f"[executor] done {jtype} job={job_id} in {dt}s", flush=True)
        return {"result": result, "job_completed": bool(completed)}
    except Exception as exc:
        traceback.print_exc()
        dt = round(time.monotonic() - t0, 2)
        print(f"[executor] FAILED {jtype} job={job_id} after {dt}s: {exc}",
              flush=True)
        decision = failure_policy.classify(exc, jtype)
        failure_timings = {"total_s": dt}
        failure_timings.update(resource_usage.usage_since(resource_start))
        compute_cost.annotate_request(
            failure_timings, dt, config.WORKER_ROLE,
            os.getenv("K_SERVICE", ""))
        print("[resources] " + json.dumps({
            "type": jtype, "job_id": job_id, "ok": False,
            **failure_timings,
        }, sort_keys=True, separators=(",", ":")), flush=True)
        return {
            "error": str(exc),
            "retryable": decision.retryable,
            "failure": decision.payload(exc),
            "timings": failure_timings,
            "lease_lost": isinstance(exc, dbx.JobLeaseLost),
        }
    finally:
        if job_id is not None:
            dbx.untrack_job(job_id)
        db.reset()
