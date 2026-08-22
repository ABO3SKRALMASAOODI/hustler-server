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

import psycopg2

import compute_cost
import config
import db as dbx
import failure_policy
import io_telemetry
import job_completion
import resource_usage


_heartbeat_started = False
_heartbeat_lock = threading.Lock()
_container_ready_at = time.monotonic()
_container_input_count = 0
_container_input_lock = threading.Lock()


def _input_observation(now):
    global _container_input_count
    with _container_input_lock:
        _container_input_count += 1
        sequence = _container_input_count
    return {
        "container_input_sequence": sequence,
        "container_first_input": sequence == 1,
        # This is the observable Python-image readiness portion of a cold
        # start. Provider queue/cold-boot latency remains visible to the
        # caller's durable launch timing and must not be guessed here.
        "cold_start_observed_s": (
            round(max(0.0, now - _container_ready_at), 3)
            if sequence == 1 else 0.0),
    }


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


def _confirm_provider_ownership(db, job):
    """Wait boundedly for the spawn caller to publish this exact call.

    Modal returns a provider call id only after accepting the input, so the
    Postgres handoff necessarily follows by a few milliseconds. A dispatcher
    crash in that gap used to leave an unobservable paid render. The executor
    now waits for the exact id before expensive work; a genuinely missing or
    superseded handoff exits without touching user media.
    """
    job_id = job.get("id")
    claim = job.get("total_claims")
    call_id = job.get("provider_call_id")
    provider = os.getenv("EXECUTOR_PROVIDER", "cloud_run")
    if job_id is None or claim is None or not call_id \
            or provider not in {"modal", "cloudflare"}:
        return
    deadline = time.monotonic() + config.REMOTE_HANDOFF_CONFIRM_S
    while True:
        try:
            status = db.run(
                dbx.confirm_remote_execution_ownership,
                job_id, claim, provider, call_id)
        except (psycopg2.OperationalError,
                psycopg2.InterfaceError) as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise dbx.RemoteExecutionUnconfirmed(
                    f"job {job_id} provider call {call_id} ownership could "
                    "not be confirmed before the bounded handoff deadline"
                ) from exc
            time.sleep(min(0.5, remaining))
            continue
        if status == "owned":
            return
        if status == "unavailable":
            print(f"[executor] remote ownership ledger is not installed for "
                  f"job {job_id}; using migration-compatible lease fencing",
                  flush=True)
            return
        if isinstance(status, str) and status.startswith("superseded"):
            diagnostic = status.partition(":")[2]
            suffix = f" [{diagnostic}]" if diagnostic else ""
            raise dbx.JobLeaseLost(
                f"job {job_id} provider call {call_id} was superseded before "
                f"executor start{suffix}")
        if time.monotonic() >= deadline:
            raise dbx.RemoteExecutionUnconfirmed(
                f"job {job_id} provider call {call_id} ownership was not "
                "recorded before the bounded handoff deadline")
        time.sleep(0.1)


def _deployment_identity(job):
    """Best-effort shared-source and provider-adapter fingerprints.

    A canary result is not comparable when Modal and Cloudflare ran different
    renderer bytes. Diagnostics must never fail a user's render, so an
    unreadable fingerprint is recorded as ``unknown`` and the rollout gate
    fails closed later instead of withholding the completed artifact.
    """
    try:
        import version
        code_version = version.code_version()
    except Exception:
        code_version = "unknown"
    adapter_version = str(job.get("provider_adapter_version") or "unknown")
    return {
        "executor_code_version": code_version,
        "executor_adapter_version": adapter_version,
    }


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
    try:
        provider_start_s = max(
            0.0, time.time() - float(job.get("dispatch_submitted_at")))
    except (TypeError, ValueError):
        provider_start_s = None
    input_observation = _input_observation(t0)
    io_token = io_telemetry.begin()
    io_finished = False
    resource_start = resource_usage.snapshot()
    try:
        memory_sampler = resource_usage.MemorySampler()
    except Exception:
        # Observability never becomes a new reason a customer render can fail.
        memory_sampler = None

    def measured_resources():
        nonlocal io_finished
        measured = resource_usage.usage_since(resource_start)
        try:
            sampled_peak = memory_sampler.finish() if memory_sampler else None
        except Exception:
            sampled_peak = None
        if sampled_peak is not None:
            measured["container_memory_sampled_peak_mib"] = sampled_peak
        if not io_finished:
            measured.update(io_telemetry.finish(io_token))
            io_finished = True
        return measured

    print(f"[executor] start {jtype} job={job_id} "
          f"project={job.get('project_id')} provider="
          f"{os.getenv('EXECUTOR_PROVIDER', 'cloud_run')}", flush=True)
    db = LeasedDb(job_id, lease_claim)
    if job_id is not None:
        dbx.track_job(job_id)
    try:
        _confirm_provider_ownership(db, job)
        if lease_claim is not None and not db.run(
                dbx.lease_is_current, job_id, lease_claim):
            raise dbx.JobLeaseLost(
                f"job {job_id} execution lease {lease_claim} is no longer current")
        if job_id is not None and lease_claim is not None:
            try:
                db.run(dbx.mark_remote_execution_running,
                       job_id, lease_claim,
                       os.getenv("EXECUTOR_PROVIDER", "cloud_run"),
                       job.get("provider_call_id"))
            except Exception as exc:
                # The queue lease remains the source of truth. A handoff
                # telemetry write must never become a new render failure.
                print(f"[executor] could not mark remote start for job "
                      f"{job_id}: {str(exc)[:160]}", flush=True)
        result = runner(db, job)
        dt = round(time.monotonic() - t0, 2)
        execution_timings = {"total_s": dt}
        execution_timings.update(measured_resources())
        execution_timings.update(input_observation)
        execution_timings.update(_deployment_identity(job))
        execution_timings.update({
            "execution_class": config.execution_class_for(jtype),
            "execution_policy": config.execution_policy_for(job),
            "cache_hit": bool(
                isinstance(result, dict) and (
                    result.get("cached") or result.get("cache_hit")
                    or any(str(key).endswith("cache_hit_s")
                           for key in (result.get("timings") or {})))),
        })
        if provider_start_s is not None:
            execution_timings["provider_start_s"] = round(
                provider_start_s, 3)
        requested_provider = str(((job.get("payload") or {}).get(
            "execution_provider") or "")).strip().lower()
        actual_provider = os.getenv("EXECUTOR_PROVIDER", "cloud_run")
        if requested_provider:
            execution_timings["dispatch_provider"] = requested_provider
            execution_timings["provider_fallback"] = (
                requested_provider != actual_provider)
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
            try:
                db.run(dbx.finish_remote_execution, job_id, lease_claim,
                       "done", None,
                       os.getenv("EXECUTOR_PROVIDER", "cloud_run"),
                       job.get("provider_call_id"))
            except Exception as exc:
                print(f"[executor] could not close remote handoff for job "
                      f"{job_id}: {str(exc)[:160]}", flush=True)
        print("[resources] " + json.dumps({
            "type": jtype, "job_id": job_id, "ok": True,
            **execution_timings,
        }, sort_keys=True, separators=(",", ":")), flush=True)
        print(f"[executor] done {jtype} job={job_id} in {dt}s", flush=True)
        return {"result": result, "job_completed": bool(completed),
                "execution": execution_timings}
    except Exception as exc:
        traceback.print_exc()
        dt = round(time.monotonic() - t0, 2)
        print(f"[executor] FAILED {jtype} job={job_id} after {dt}s: {exc}",
              flush=True)
        decision = failure_policy.classify(exc, jtype)
        if job_id is not None and lease_claim is not None:
            try:
                db.run(dbx.finish_remote_execution, job_id, lease_claim,
                       "failed", exc,
                       os.getenv("EXECUTOR_PROVIDER", "cloud_run"),
                       job.get("provider_call_id"))
            except Exception as ledger_exc:
                print(f"[executor] could not record remote failure for job "
                      f"{job_id}: {str(ledger_exc)[:160]}", flush=True)
        failure_timings = {"total_s": dt}
        failure_timings.update(
            dict(getattr(exc, "runner_timings", {}) or {}))
        failure_timings.update(measured_resources())
        failure_timings.update(input_observation)
        failure_timings.update(_deployment_identity(job))
        failure_timings.update({
            "execution_class": config.execution_class_for(jtype),
            "execution_policy": config.execution_policy_for(job),
            "cache_hit": False,
        })
        if provider_start_s is not None:
            failure_timings["provider_start_s"] = round(
                provider_start_s, 3)
        requested_provider = str(((job.get("payload") or {}).get(
            "execution_provider") or "")).strip().lower()
        actual_provider = os.getenv("EXECUTOR_PROVIDER", "cloud_run")
        if requested_provider:
            failure_timings["dispatch_provider"] = requested_provider
            failure_timings["provider_fallback"] = (
                requested_provider != actual_provider)
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
        if not io_finished:
            try:
                io_telemetry.finish(io_token)
            except Exception:
                pass
        if job_id is not None:
            dbx.untrack_job(job_id)
        db.reset()
