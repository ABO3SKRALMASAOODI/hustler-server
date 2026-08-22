"""Dispatcher -> executor client (round 38).

When WORKER_ROLE=worker and remote siblings are configured, the dispatcher
ships media/index work to the heavy Cloud Run executor and agent turns to the
smaller request-based agent service. These wrappers have the SAME
`(worker_db, job)` signature as the local runners in indexer/renderer, so
main.RUNNERS can swap one for the other with nothing else changing — the
dispatcher keeps claiming, heart-beating, retrying, reaping and credit-charging
exactly as before. The heavy CPU happens on the executor; this call just waits
on the HTTP response (I/O wait, so a dispatcher thread costs almost nothing).

The executor reads the job's real state from the shared Postgres, so the body
we POST is only what the runner needs to identify the work — never asset bytes.
"""

import hashlib
import json
import threading
import time
from datetime import datetime, timezone

import requests

import config
import db as dbx
import error_text
import failure_policy
import version


class RemoteExecutorError(RuntimeError):
    pass


class RemoteServiceUnavailable(RemoteExecutorError):
    """A derived sibling definitely does not exist; heavy fallback is safe."""


class BatchUnavailable(RuntimeError):
    """The launcher definitely did not start a Job; request fallback is safe."""


class RemoteBatchDetached(RuntimeError):
    """A Job may be running without this dispatcher; leave its DB lease alone."""


class ModalLaunchUnavailable(RemoteExecutorError):
    """Modal rejected the submission before returning a durable call id."""


class CloudflareLaunchUnavailable(RemoteExecutorError):
    """Cloudflare proved no call was accepted, so Modal fallback is safe."""


# The last version skew observed against the executor, or "" when the two
# services agree (or when we have not been able to ask). Written by
# check_executor_version, read by _run_remote so that a job which fails on a
# stale executor SAYS SO in the error the admin job list shows. Round 55's
# customer got "the render is the wrong length" three times; so did we, and it
# was the only thing either of us had.
_skew_note = ""
_skew_lock = threading.Lock()

# Capability/version probes are advisory and several callers ask the same
# question in one turn.  A cold /health request is still a billable Cloud Run
# request, so cache successful answers per service. Failures are never cached.
_health_cache = {}
_health_lock = threading.Lock()
_HEALTH_CACHE_S = 300.0

# Modal's newly-created FunctionCall can briefly answer 0 outputs / 0
# unfinished inputs before the accepted input is visible to every control
# plane replica.  The SDK exposes that observation as OutputExpiredError —
# the same exception used for a genuinely expired old output.  Treating the
# first observation as terminal races the durable ledger: the guardian marks
# the exact call failed, then its container starts and correctly refuses the
# now-terminal ownership row.  Keep the ambiguity bounded; a real missing
# call fails after one minute rather than becoming an indefinite job.
_MODAL_OUTPUT_VISIBILITY_GRACE_S = 60.0

_modal_functions = {}
_modal_lock = threading.Lock()


def _modal_function(name):
    """Hydrate and cache a deployed Modal Function handle lazily."""
    with _modal_lock:
        function = _modal_functions.get(name)
        if function is not None:
            return function
        try:
            import modal
            function = modal.Function.from_name(
                config.MODAL_EXECUTOR_APP, name,
                environment_name=config.MODAL_EXECUTOR_ENVIRONMENT or None)
        except Exception as exc:
            raise ModalLaunchUnavailable(
                f"Modal function lookup failed for {name}: {exc}") from exc
        _modal_functions[name] = function
        return function


def _modal_selected(job):
    if not config.MODAL_EXECUTOR_ENABLED:
        return False
    jtype = str(job.get("type") or "")
    if jtype not in config.MODAL_EXECUTOR_TYPES:
        return False
    # The percentage is a legacy rollout mechanism. A redesign-stamped job
    # has already crossed the atomic ownership switch and must never drift
    # back to Cloud Run because of a bucket assignment.
    if config.execution_policy_for(job) == "redesign":
        return True
    percent = config.MODAL_EXECUTOR_PERCENT
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    stable = f"{job.get('id')}:{job.get('project_id')}:{jtype}"
    bucket = int(hashlib.sha256(stable.encode("utf-8")).hexdigest()[:8], 16)
    return bucket % 100 < percent


def _cloudflare_selected(job):
    if not config.CLOUDFLARE_EXECUTOR_ENABLED \
            or not config.CLOUDFLARE_EXECUTOR_URL:
        return False
    if job.get("id") is None:
        return False
    if str(job.get("type") or "") not in config.CLOUDFLARE_EXECUTOR_TYPES:
        return False
    percent = config.CLOUDFLARE_EXECUTOR_PERCENT
    if percent <= 0:
        return False
    if percent < 100:
        stable = (f"cloudflare:{job.get('id')}:{job.get('project_id')}:"
                  f"{job.get('type')}")
        bucket = int(hashlib.sha256(
            stable.encode("utf-8")).hexdigest()[:8], 16)
        if bucket % 100 >= percent:
            return False
    shape = job.get("_execution_shape") or {}
    if not shape:
        return False
    try:
        return (int(shape.get("total_bytes") or 0)
                <= config.CLOUDFLARE_MAX_INPUT_BYTES
                and float(shape.get("max_duration_s") or 0)
                <= config.CLOUDFLARE_MAX_SOURCE_DURATION_S)
    except (TypeError, ValueError):
        return False


def desired_execution_provider(job):
    """Return the immutable provider choice for one queue-backed job."""
    stamped = str(((job.get("payload") or {}).get(
        "execution_provider") or "")).strip().lower()
    if stamped in {"cloudflare", "modal", "cloud_run", "local"}:
        return stamped
    if _cloudflare_selected(job):
        return "cloudflare"
    if _modal_selected(job):
        return "modal"
    return "cloud_run" if _executor_url(job.get("type")) else "local"


def stamp_execution_provider(worker_db, job):
    """Fence rollout changes from moving an already-claimed job."""
    if job.get("id") is None:
        return desired_execution_provider(job)
    stamped = str(((job.get("payload") or {}).get(
        "execution_provider") or "")).strip().lower()
    if not stamped and config.CLOUDFLARE_EXECUTOR_ENABLED \
            and str(job.get("type") or "") in \
            config.CLOUDFLARE_EXECUTOR_TYPES:
        try:
            job["_execution_shape"] = worker_db.run(
                dbx.project_execution_shape, job.get("project_id"),
                (job.get("payload") or {}).get("asset_id"))
        except Exception as exc:
            # Fail closed to the proven Modal owner when capacity cannot be
            # established. Provider optimization never takes the product down.
            print(f"[dispatcher] Cloudflare shape probe failed for job "
                  f"{job.get('id')}: {str(exc)[:160]}; keeping Modal",
                  flush=True)
    provider = desired_execution_provider(job)
    persisted = worker_db.run(
        dbx.stamp_execution_provider, job["id"], job.get("total_claims"),
        provider, job.get("_execution_shape") or {})
    if persisted:
        payload = dict(job.get("payload") or {})
        payload["execution_provider"] = persisted
        if job.get("_execution_shape"):
            payload["execution_shape"] = job["_execution_shape"]
        job["payload"] = payload
        return persisted
    return provider


def _modal_eu_selected(job):
    """Stable, retry-safe regional canary for byte-heavy Modal calls."""
    percent = config.MODAL_EU_PERCENT
    if percent <= 0:
        return False
    if str(job.get("type") or "") not in config.MODAL_EU_TYPES:
        return False
    if percent >= 100:
        return True
    identity = job.get("id")
    if identity is None:
        # Synchronous MCP/frame calls have no row id. Their canonical payload
        # makes separate calls sample independently while an identical retry
        # remains in the same region.
        identity = json.dumps(job.get("payload") or {}, sort_keys=True,
                              separators=(",", ":"), default=str)
    stable = (f"eu:{identity}:{job.get('project_id')}:"
              f"{job.get('type')}")
    bucket = int(hashlib.sha256(stable.encode("utf-8")).hexdigest()[:8], 16)
    return bucket % 100 < percent


def _modal_function_name(job_type, override=None):
    if override:
        return override
    if job_type in ("preview", "preview_check", "filmstrip"):
        return "preview"
    if job_type == "final":
        return "final"
    if job_type == "index":
        return "index"
    if job_type == "agent_turn":
        return "agent"
    if job_type == "mcp_tool":
        return "mcp"
    if job_type == "shorts_plan":
        return "shorts"
    if job_type == "ytprobe":
        return "probe"
    if job_type == "frames":
        return "light"
    if job_type == "capture":
        return "heavy"
    if job_type in ("fetch", "search", "stock_acquire"):
        return "egress"
    return "heavy"


def _modal_health(timeout=20):
    try:
        call = _modal_function("health").spawn()
    except Exception as exc:
        if isinstance(exc, ModalLaunchUnavailable):
            raise
        raise ModalLaunchUnavailable(f"Modal health launch failed: {exc}") \
            from exc
    return call.get(timeout=timeout)


def executor_health(timeout=20, job_type=None):
    """GET /health on the executor. Returns the parsed body, or raises."""
    # A generic capability/version probe uses the preview sibling. It runs the
    # same application image as the 32-GiB fallback, but costs a quarter of the
    # vCPU allocation to cold-start. Heavy capacity is tested by real heavy
    # work, never by a diagnostic ping.
    if config.MODAL_EXECUTOR_ENABLED and (
            job_type is None or job_type in config.MODAL_EXECUTOR_TYPES):
        key = "modal:health"
        now = time.monotonic()
        with _health_lock:
            cached = _health_cache.get(key)
            if cached and now - cached[0] < _HEALTH_CACHE_S:
                return cached[1]
            # Keep the lock through the cold launch.  Two dispatcher boot
            # threads used to rent duplicate health containers at once.
            body = _modal_health(timeout=timeout)
            _health_cache[key] = (now, body)
        return body
    url = _executor_url("preview" if job_type is None else job_type)
    if not url:
        raise RemoteExecutorError("remote executor URL is not set")
    now = time.monotonic()
    with _health_lock:
        cached = _health_cache.get(url)
        if cached and now - cached[0] < _HEALTH_CACHE_S:
            return cached[1]
    resp = requests.get(f"{url}/health", timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    with _health_lock:
        _health_cache[url] = (now, body)
    return body


def _executor_url(job_type=None):
    """Choose a right-sized service without ever losing the heavy fallback."""
    if job_type == "agent_turn" and config.REMOTE_AGENT_EXECUTOR_URL:
        return config.REMOTE_AGENT_EXECUTOR_URL
    if job_type in ("preview", "preview_check") \
            and config.REMOTE_EXECUTOR_PREVIEW_URL:
        return config.REMOTE_EXECUTOR_PREVIEW_URL
    if job_type in ("final", "index") \
            and config.REMOTE_EXECUTOR_BATCH_URL:
        return config.REMOTE_EXECUTOR_BATCH_URL
    return config.REMOTE_EXECUTOR_URL


# Pre-warm throttle: one boot per cooldown window is all a session needs
# (Cloud Run keeps an idle instance around well past this), and an active
# chat must not turn every agent turn into a health request.
_WARM_COOLDOWN_S = 300.0
_warm_last = 0.0
_warm_lock = threading.Lock()


def warm_executor():
    """Fire-and-forget executor boot ahead of the first render (round 98).

    min-instances stays 0 — the $600/mo always-warm instance stays not
    bought (see DEPLOY_EXECUTOR.md). This instead boots an instance ONLY
    when a user is actively editing: the agent loop calls it at turn start,
    the boot overlaps the model's own planning seconds, and by the time
    render_preview enqueues, the cold start is already paid. Every failure
    is swallowed: the worst case is exactly the old behavior."""
    global _warm_last
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED:
        return
    now = time.monotonic()
    with _warm_lock:
        if now - _warm_last < _WARM_COOLDOWN_S:
            return
        _warm_last = now
    try:
        if config.MODAL_EXECUTOR_ENABLED \
                and "preview" in config.MODAL_EXECUTOR_TYPES:
            # A no-op input boots the actual 2-core preview image while the
            # model plans. It is intentionally not awaited.
            _modal_function("preview").spawn({"type": "__warm"})
            return
        url = _executor_url("preview")
        resp = requests.get(f"{url}/health", timeout=45)
        resp.raise_for_status()
    except Exception:
        pass


def check_executor_version(quiet=False):
    """Compare the executor's code with ours and remember the answer.

    NEVER BLOCKS ANYTHING. It does not gate dispatch, delay a job or fail a
    render — a skewed executor still serves most work correctly, and refusing
    to use it would take the whole product down to prevent a subset of edits
    from being wrong. That is the round-53 mistake exactly: a version check
    whose only move was "no" hid every finished export on the platform. This
    one's only move is "say so".

    Returns the skew note ("" when the two agree, or when the executor could
    not be reached — an unreachable executor is a different problem with its
    own loud failure path, and guessing skew from a timeout would be a lie).
    """
    global _skew_note
    mine = version.code_version()
    try:
        theirs = executor_health()
    except Exception as e:
        if not quiet:
            print(f"[dispatcher] executor version check failed: "
                  f"{str(e)[:200]}", flush=True)
        return ""
    remote_v = str(theirs.get("code_version") or "unknown")
    note = ""
    if mine != "unknown" and remote_v != "unknown" and mine != remote_v:
        note = (f"the render executor is running DIFFERENT code than this "
                f"dispatcher (executor {remote_v}, dispatcher {mine}) — "
                f"redeploy it: see worker/DEPLOY_EXECUTOR.md")
        if not quiet:
            print(f"[dispatcher] *** VERSION SKEW *** {note}\n"
                  f"[dispatcher]     executor reports: {theirs}", flush=True)
    elif not quiet:
        print(f"[dispatcher] executor code={remote_v} (matches dispatcher)",
              flush=True)
    with _skew_lock:
        _skew_note = note
    return note


def check_agent_executor_version(quiet=False):
    """Report agent-service skew without conflating it with render skew."""
    if not config.REMOTE_AGENT_EXECUTOR_URL and not (
            config.MODAL_EXECUTOR_ENABLED
            and "agent_turn" in config.MODAL_EXECUTOR_TYPES):
        return ""
    mine = version.code_version()
    try:
        theirs = executor_health(job_type="agent_turn")
    except Exception as e:
        if not quiet:
            print(f"[dispatcher] agent executor version check failed: "
                  f"{str(e)[:200]}", flush=True)
        return ""
    remote_v = str(theirs.get("code_version") or "unknown")
    if mine != "unknown" and remote_v != "unknown" and mine != remote_v:
        note = (f"the agent executor is running DIFFERENT code than this "
                f"dispatcher (executor {remote_v}, dispatcher {mine})")
        if not quiet:
            print(f"[dispatcher] *** AGENT VERSION SKEW *** {note}",
                  flush=True)
        return note
    if not quiet:
        print(f"[dispatcher] agent executor code={remote_v} "
              "(matches dispatcher)", flush=True)
    return ""


def executor_supports(feature, timeout=8):
    """Does the executor advertise `feature` in /health's `features` list?

    True with no executor configured (renders run THIS process's code, which
    by definition supports whatever it can validate). False when the executor
    answers and the feature is missing — the one case a WRITE should refuse,
    because the render service genuinely cannot draw what would be stored.
    None when the executor cannot be reached: unknown is not "no" (the
    round-53 rule — a diagnostic outage must never take a feature down), so
    callers treat None as permission plus a louder failure elsewhere.
    """
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED:
        return True
    try:
        body = executor_health(timeout=timeout)
    except Exception:
        return None
    return feature in (body.get("features") or [])


def _job_payload(job):
    """A JSON-safe subset of the claimed job row. The runners read only these
    fields. Queue latency is computed once by the dispatcher and carried as a
    number, avoiding datetime/clock skew across providers."""
    return {
        "id": job["id"],
        "type": job["type"],
        "project_id": job["project_id"],
        "user_id": job.get("user_id"),
        "attempts": job.get("attempts"),
        # Monotonic execution lease. Unlike attempts, this is never refunded
        # on a dispatcher deploy, so an orphan and its replacement cannot
        # present the same identity to progress/result writes.
        "total_claims": job.get("total_claims"),
        "payload": job.get("payload") or {},
        # Private transport metadata, ignored by ordinary runners. It lets the
        # request-based agent owner preserve the queue timing even though a
        # datetime is deliberately not serialized into this body.
        "_queue_wait_s": job.get("_queue_wait_s"),
        # Wall-clock handoff boundary used by the remote executor to measure
        # provider scheduling + image/container cold start. Monotonic clocks
        # cannot be compared across hosts.
        "dispatch_submitted_at": job.get("_dispatch_submitted_at"),
    }


def _launch_batch_and_wait(worker_db, job):
    """Start one durable Cloud Run Job, detach shutdown ownership, poll DB."""
    launcher = config.REMOTE_BATCH_LAUNCHER_URL
    if not launcher or not config.REMOTE_BATCH_JOB_NAME:
        raise BatchUnavailable("batch launcher is not configured")
    job_id = job["id"]
    claim = job.get("total_claims")
    if claim is None:
        raise BatchUnavailable("job has no monotonic execution claim")

    reserved = worker_db.run(dbx.reserve_batch_launch, job_id, claim)
    if reserved:
        headers = {"Content-Type": "application/json"}
        if config.REMOTE_EXECUTOR_SECRET:
            headers["Authorization"] = f"Bearer {config.REMOTE_EXECUTOR_SECRET}"
        try:
            job.setdefault("_dispatch_submitted_at", time.time())
            response = requests.post(
                f"{launcher}/launch", json={"job": _job_payload(job)},
                headers=headers, timeout=30)
        except requests.RequestException as exc:
            # We cannot distinguish "never reached the launcher" from "the
            # launch succeeded and its response was lost". The durable mark
            # prevents an expensive duplicate; the stale reaper recovers if
            # no Job ever appears.
            dbx.untrack_job(job_id)
            raise RemoteBatchDetached(
                f"batch launch response was ambiguous: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 404:
            worker_db.run(dbx.clear_batch_launch, job_id, claim)
            raise BatchUnavailable("launcher is not deployed yet")
        if data.get("safe_to_fallback"):
            worker_db.run(dbx.clear_batch_launch, job_id, claim)
            raise BatchUnavailable(str(data.get("error") or "launch refused"))
        if response.status_code not in (200, 202) or not data.get("launched"):
            dbx.untrack_job(job_id)
            raise RemoteBatchDetached(
                f"batch launcher returned {response.status_code}: "
                f"{(response.text or '')[:400]}")
        worker_db.run(dbx.record_batch_launch, job_id, claim,
                      data.get("operation") or "accepted")

    # Ownership has crossed the launch boundary. A Render SIGTERM must not
    # refund/requeue this row while the independently-running Job is healthy.
    dbx.untrack_job(job_id)
    deadline = time.monotonic() + config.executor_timeout_for(job["type"]) + 300
    while time.monotonic() < deadline:
        current = worker_db.run(dbx.get_job, job_id)
        if not current:
            raise RemoteBatchDetached(f"batch job row {job_id} disappeared")
        if current["state"] == "done":
            result = current.get("result")
            if not isinstance(result, dict):
                result = {"result": result}
            result["_remote_job_completed"] = True
            result["_remote_job_terminal_state"] = "done"
            return result
        if current["state"] == "failed":
            return {"_remote_job_completed": True,
                    "_remote_job_terminal_state": "failed",
                    "_remote_job_error": current.get("error")}
        if current["state"] == "queued":
            return {"_remote_job_completed": True,
                    "_remote_job_terminal_state": "requeued"}
        time.sleep(config.BATCH_POLL_INTERVAL_S)
    raise RemoteBatchDetached(
        f"batch execution for job {job_id} outlived the dispatcher poll window")


def _interpret_executor_data(data, job):
    """Turn either provider's established envelope into runner semantics."""
    if not isinstance(data, dict):
        raise RemoteExecutorError(
            f"executor returned an invalid response: {type(data).__name__}")
    if data.get("error"):
        msg = error_text.excerpt(data["error"], 500)
        skew = (check_agent_executor_version(quiet=True)
                if job.get("type") == "agent_turn"
                else check_executor_version(quiet=True))
        if skew:
            msg = f"{msg} [{skew}]"
        failure = data.get("failure") or {}
        if data.get("lease_lost"):
            err = dbx.JobLeaseLost(msg)
            err.executor_timings = data.get("timings") or {}
            raise failure_policy.attach(
                err, failure_policy.classify(err, job.get("type")), failure)
        if data.get("retryable") is False:
            err = dbx.PermanentJobError(msg)
            err.executor_timings = data.get("timings") or {}
            raise failure_policy.attach(
                err, failure_policy.classify(err, job.get("type")), failure)
        err = RemoteExecutorError(msg)
        err.executor_timings = data.get("timings") or {}
        raise failure_policy.attach(
            err, failure_policy.classify(err, job.get("type")), failure)
    result = data.get("result")
    if isinstance(result, dict) and isinstance(data.get("execution"), dict):
        # Queued jobs already persist these fields in result.timings. Direct
        # frame/egress/tool calls have no job row, so carry the same evidence
        # back to their orchestrator rather than leaving it only in logs.
        result.setdefault("execution", data["execution"])
    if data.get("job_completed") and isinstance(result, dict):
        result["_remote_job_completed"] = True
    return result


def _recover_modal_result(call_id, job, deadline):
    """Reconnect to a durable call after a transient SDK transport failure."""
    import modal
    last = None
    output_missing_since = None
    while time.monotonic() < deadline:
        try:
            call = modal.FunctionCall.from_id(call_id)
            return call.get(timeout=min(30, max(1, deadline - time.monotonic())))
        except TimeoutError as exc:
            last = exc
        except Exception as exc:
            if _modal_output_expired(exc):
                now = time.monotonic()
                output_missing_since = output_missing_since or now
                if now - output_missing_since >= \
                        _MODAL_OUTPUT_VISIBILITY_GRACE_S:
                    raise RemoteExecutorError(
                        f"Modal call {call_id} remained invisible for "
                        f"{_MODAL_OUTPUT_VISIBILITY_GRACE_S:.0f}s") from exc
                last = exc
                time.sleep(2)
            elif not _modal_transport_error(exc):
                raise RemoteExecutorError(
                    f"Modal call {call_id} failed: {exc}") from exc
            else:
                last = exc
                time.sleep(2)
        if job.get("id") is not None:
            probe = dbx.Db()
            try:
                current = probe.run(dbx.get_job, job["id"])
                if current and current.get("state") == "done":
                    return {"result": current.get("result"),
                            "job_completed": True}
            except Exception:
                pass
            finally:
                probe.reset()
    raise RemoteExecutorError(
        f"Modal call {call_id} could not be recovered: {last}") from last


def _modal_transport_error(exc):
    """True only when asking the same durable call again can help.

    Function failures and timeouts are terminal results. Retrying ``get`` for
    an hour cannot change them; it only delays the user's repair path. Modal's
    connection/service failures are different: the paid input may still be
    running, so reconnect to its call id instead of launching a duplicate.
    """
    try:
        from modal import exception as modal_exc
        transient = (
            modal_exc.ConnectionError,
            modal_exc.InternalError,
            modal_exc.ServiceError,
        )
    except Exception:
        transient = ()
    return isinstance(exc, (ConnectionError, OSError) + transient)


def _modal_output_expired(exc):
    """Whether Modal has not exposed an output/unfinished input for a call."""
    try:
        from modal import exception as modal_exc
        return isinstance(exc, modal_exc.OutputExpiredError)
    except Exception:
        return False


def _modal_visibility_grace_active(row, now=None):
    """Bound the ambiguous just-spawned OutputExpiredError window."""
    submitted = row.get("submitted_at")
    if not isinstance(submitted, datetime):
        return False
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return 0 <= (now - submitted).total_seconds() \
        < _MODAL_OUTPUT_VISIBILITY_GRACE_S


def reconcile_remote_execution(worker_db, row):
    """Observe one durable provider call after its dispatcher disappeared.

    A timeout from ``get`` is positive evidence that Modal still owns the
    call, so it earns a database heartbeat. A terminal function error follows
    the same retry policy as an attached dispatcher. Transport uncertainty is
    deliberately left alone until the persisted provider deadline; guessing
    "dead" there is how duplicate paid renders are born.
    """
    provider = row.get("provider")
    if provider not in {"modal", "cloudflare"}:
        return {"status": "unsupported_provider", "row": row}
    job_id = row["job_id"]
    claim = row["total_claims"]
    job = {
        "id": job_id,
        "type": row.get("type"),
        "project_id": row.get("project_id"),
        "user_id": row.get("user_id"),
        "attempts": int(row.get("attempts") or 0),
        "total_claims": claim,
        "payload": row.get("payload") or {},
    }
    try:
        if provider == "modal":
            import modal
            call = modal.FunctionCall.from_id(row["call_id"])
            data = call.get(timeout=0.1)
        else:
            status = _cloudflare_status(
                row["call_id"], row.get("function_name") or
                _cloudflare_lane(row.get("type")), timeout=10)
            state = status.get("status")
            if state in {"submitted", "starting", "running", "unknown"}:
                worker_db.run(
                    dbx.heartbeat_remote_execution, job_id, claim)
                return {"status": "running", "job": job}
            if state == "missing":
                return {"status": "unknown", "job": job}
            data = status.get("envelope")
            if not isinstance(data, dict):
                raise RemoteExecutorError(
                    f"Cloudflare call {row['call_id']} ended without an "
                    "executor envelope")
    except TimeoutError:
        worker_db.run(dbx.heartbeat_remote_execution, job_id, claim)
        return {"status": "running", "job": job}
    except Exception as exc:
        if (provider == "modal" and _modal_output_expired(exc)
                and _modal_visibility_grace_active(row)):
            # No positive liveness proof yet, so do not heartbeat.  The
            # submitted ledger + provider deadline protects the queue claim;
            # the next guardian pass either observes the live call or, after
            # the bounded grace, follows ordinary terminal failure policy.
            return {"status": "visibility_pending", "job": job,
                    "error": exc}
        if ((provider == "modal" and _modal_transport_error(exc))
                or (provider == "cloudflare"
                    and isinstance(exc, requests.RequestException))):
            return {"status": "unknown", "job": job, "error": exc}
        decision = failure_policy.decision_for(exc, job.get("type"))
        worker_db.run(dbx.finish_remote_execution, job_id, claim,
                      "failed", exc)
        if decision.retryable and job["attempts"] < decision.max_attempts:
            requeued = worker_db.run(
                dbx.requeue_job, job_id, exc, claim)
            return {"status": "requeued" if requeued else "superseded",
                    "job": job, "error": exc}
        failure_result = {"failure": decision.payload(exc)}
        finished = worker_db.run(
            dbx.finish_job, job_id, "failed", exc, failure_result, claim)
        return {"status": "failed" if finished is not False else "superseded",
                "job": job, "error": exc}

    try:
        _interpret_executor_data(data, job)
    except Exception as exc:
        decision = failure_policy.decision_for(exc, job.get("type"))
        worker_db.run(dbx.finish_remote_execution, job_id, claim,
                      "failed", exc)
        if decision.retryable and job["attempts"] < decision.max_attempts:
            requeued = worker_db.run(dbx.requeue_job, job_id, exc, claim)
            return {"status": "requeued" if requeued else "superseded",
                    "job": job, "error": exc}
        failure_result = {"failure": decision.payload(exc)}
        finished = worker_db.run(
            dbx.finish_job, job_id, "failed", exc, failure_result, claim)
        return {"status": "failed" if finished is not False else "superseded",
                "job": job, "error": exc}

    current = worker_db.run(dbx.get_job, job_id)
    if current and current.get("state") in {"done", "failed"}:
        terminal = current["state"]
        worker_db.run(dbx.finish_remote_execution, job_id, claim,
                      terminal, current.get("error"))
        return {"status": terminal, "job": job,
                "error": (RuntimeError(current.get("error"))
                          if terminal == "failed" else None)}

    # execute() commits the queue result before returning the FunctionCall.
    # If a provider says complete but the queue has not observed that commit,
    # keep the lease protected and surface the contradiction in logs; never
    # manufacture a second render or a success row without billing semantics.
    worker_db.run(dbx.heartbeat_remote_execution, job_id, claim)
    return {"status": "completion_pending", "job": job}


def _run_modal(job, function_override=None):
    base_name = _modal_function_name(job.get("type"), function_override)
    requested_name = (f"{base_name}_eu"
                      if base_name in {"preview", "batch", "final", "index",
                                       "light"}
                      and _modal_eu_selected(job) else base_name)
    candidates = [requested_name]
    if requested_name.endswith("_eu"):
        candidates.append(base_name)
    # A new dispatcher may become healthy before the Modal workflow finishes.
    # The old batch function is an identical safe launch target for index work.
    if base_name == "index" and "batch" not in candidates:
        candidates.append("batch")
    last = None
    for name in candidates:
        try:
            function = _modal_function(name)
            break
        except ModalLaunchUnavailable as exc:
            last = exc
    else:
        raise last or ModalLaunchUnavailable(
            f"no Modal function available for {base_name}")
    if name != requested_name:
        print(f"[dispatcher] Modal function {requested_name} unavailable; "
              f"using {name} before launch", flush=True)
    elif name.endswith("_eu"):
        print(f"[dispatcher] Modal EU canary type={job.get('type')} "
              f"job={job.get('id')} project={job.get('project_id')} "
              f"function={name}", flush=True)
    execution_timeout_s = max(
        config.executor_timeout_for(job.get("type")),
        config.modal_timeout_for(job.get("type"))) + 60
    remote_handoff = job.get("id") is not None
    if remote_handoff:
        # Reserve ownership immediately before the launch request. This closes
        # the narrow race where Render can SIGTERM between Modal accepting a
        # call and this thread recording that acceptance. A rejected launch
        # restores ordinary local ownership below.
        if dbx.mark_remote_owned(job["id"]) is False:
            raise ModalLaunchUnavailable(
                "dispatcher shutdown began before Modal submission")
    try:
        job.setdefault("_dispatch_submitted_at", time.time())
        call = function.spawn(_job_payload(job))
    except Exception as exc:
        if remote_handoff:
            dbx.unmark_remote_owned(job["id"])
        raise ModalLaunchUnavailable(
            f"Modal rejected {name} before launch: {exc}") from exc
    call_id = call.object_id
    reconnect_call_id = None
    if remote_handoff:
        ledger = dbx.Db()
        try:
            persisted = ledger.run(
                dbx.record_remote_execution, job["id"],
                job.get("total_claims"), "modal", call_id, name,
                execution_timeout_s, {
                    "job_type": job.get("type"),
                    "execution_policy": config.execution_policy_for(job),
                    "queue_wait_s": job.get("_queue_wait_s"),
                })
            if not persisted:
                existing = ledger.run(
                    dbx.get_remote_execution, job["id"])
                if existing and int(existing.get("total_claims") or -1) \
                        == int(job.get("total_claims") or -2) \
                        and existing.get("provider") == "modal" \
                        and existing.get("state") in {"submitted", "running"}:
                    reconnect_call_id = str(existing["call_id"])
                    print(f"[dispatcher] Modal claim already belongs to "
                          f"{reconnect_call_id}; reconnecting it instead of "
                          f"the duplicate accepted call {call_id}", flush=True)
                else:
                    print(f"[dispatcher] Modal call {call_id} launched but "
                          "the durable remote ledger is not installed yet; "
                          "remaining attached to preserve single execution",
                          flush=True)
        except Exception as exc:
            # Submission already happened. Never launch a duplicate merely
            # because the observability write failed; stay attached to this
            # exact FunctionCall and let its fenced executor own completion.
            print(f"[dispatcher] Modal call {call_id} launched; remote "
                  f"ledger write failed ({str(exc)[:160]}), staying attached",
                  flush=True)
        finally:
            ledger.reset()
            dbx.remote_launch_recorded(job["id"])
    if reconnect_call_id and reconnect_call_id != call_id:
        # The newly accepted input carries its own provider id and will fail
        # the executor-side ownership handshake before expensive work. Follow
        # the already-recorded physical call that actually owns this claim.
        import modal
        call = modal.FunctionCall.from_id(reconnect_call_id)
        call_id = reconnect_call_id
    # Once a durable call id exists, timing out early and returning to the
    # queue would run the same paid render twice. Stay attached through the
    # provider's full function limit; inner ffmpeg/stall deadlines still make
    # genuinely bad previews fail much earlier.
    deadline = time.monotonic() + execution_timeout_s
    try:
        data = call.get(timeout=max(1, deadline - time.monotonic()))
    except TimeoutError as exc:
        raise RemoteExecutorError(
            f"Modal {name} call {call_id} exceeded its dispatcher deadline") \
            from exc
    except Exception as exc:
        # The call id proves submission happened. Never fall back and buy a
        # duplicate render. Reconnect only for a transport failure; a remote
        # function failure is already terminal and must reach repair/reaper
        # policy immediately rather than being polled for the next hour.
        if not (_modal_transport_error(exc)
                or _modal_output_expired(exc)):
            if remote_handoff:
                ledger = dbx.Db()
                try:
                    ledger.run(dbx.finish_remote_execution, job["id"],
                               job.get("total_claims"), "failed", exc)
                except Exception:
                    pass
                finally:
                    ledger.reset()
            raise RemoteExecutorError(
                f"Modal {name} call {call_id} failed: {exc}") from exc
        data = _recover_modal_result(call_id, job, deadline)
    try:
        result = _interpret_executor_data(data, job)
    except Exception as exc:
        if remote_handoff:
            ledger = dbx.Db()
            try:
                ledger.run(dbx.finish_remote_execution, job["id"],
                           job.get("total_claims"), "failed", exc)
            except Exception:
                pass
            finally:
                ledger.reset()
        raise
    if remote_handoff:
        ledger = dbx.Db()
        try:
            ledger.run(dbx.finish_remote_execution, job["id"],
                       job.get("total_claims"), "done")
        except Exception:
            pass
        finally:
            ledger.reset()
    return result


def _cloudflare_lane(job_type):
    return "interactive" if job_type in {
        "preview", "preview_check", "filmstrip"} else "batch"


def _cloudflare_call_id(job):
    raw = f"{job.get('type')}:{job.get('id')}:{job.get('total_claims')}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"cf-{str(job.get('type') or 'job')[:18]}-{digest}"


def _cloudflare_headers():
    headers = {"Content-Type": "application/json"}
    if config.REMOTE_EXECUTOR_SECRET:
        headers["Authorization"] = f"Bearer {config.REMOTE_EXECUTOR_SECRET}"
    return headers


def _cloudflare_preflight(timeout=10):
    """Authenticated positive readiness cache for the Container router.

    The first eligible job validates provider identity and the shared secret.
    Repeating that cross-region request on every proof reel adds latency but
    no safety: a missing route is still proven by the named POST's 404. Only
    successes are cached, and the URL is part of the key.
    """
    key = f"cloudflare:{config.CLOUDFLARE_EXECUTOR_URL}"
    now = time.monotonic()
    with _health_lock:
        cached = _health_cache.get(key)
        if cached and now - cached[0] < _HEALTH_CACHE_S:
            return cached[1]
        response = requests.get(
            f"{config.CLOUDFLARE_EXECUTOR_URL}/health",
            headers=_cloudflare_headers(), timeout=timeout)
        if response.status_code != 200:
            raise CloudflareLaunchUnavailable(
                f"Cloudflare preflight returned {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise CloudflareLaunchUnavailable(
                "Cloudflare preflight returned non-JSON") from exc
        if body.get("provider") != "cloudflare":
            raise CloudflareLaunchUnavailable(
                "Cloudflare preflight reached the wrong service")
        _health_cache[key] = (now, body)
        return body


def _cloudflare_status(call_id, lane, timeout=10):
    response = requests.get(
        f"{config.CLOUDFLARE_EXECUTOR_URL}/calls/{lane}/{call_id}",
        headers=_cloudflare_headers(), timeout=timeout)
    if response.status_code == 404:
        return {"status": "missing"}
    response.raise_for_status()
    return response.json()


def _recover_cloudflare_result(call_id, lane, job, deadline):
    """Reconnect a named Container call without launching another instance."""
    last = None
    while time.monotonic() < deadline:
        try:
            status = _cloudflare_status(call_id, lane, timeout=10)
            state = status.get("status")
            if state in {"submitted", "starting", "running", "unknown"}:
                if job.get("id") is not None:
                    probe = dbx.Db()
                    try:
                        probe.run(dbx.heartbeat_remote_execution, job["id"],
                                  job.get("total_claims"))
                    except Exception:
                        pass
                    finally:
                        probe.reset()
            elif state in {"done", "failed"}:
                envelope = status.get("envelope")
                if isinstance(envelope, dict):
                    return envelope
                raise RemoteExecutorError(
                    f"Cloudflare call {call_id} ended without an envelope")
            elif state == "missing":
                # This function is entered only after a launch POST became
                # ambiguous. Even repeated missing reads cannot prove a lost
                # request will not arrive later, so they NEVER authorize a
                # second provider. The fenced lease expires only at the same
                # outer deadline as the original call.
                last = RemoteExecutorError(
                    f"Cloudflare call {call_id} is not observable yet")
            if job.get("id") is not None:
                probe = dbx.Db()
                try:
                    current = probe.run(dbx.get_job, job["id"])
                    if current and current.get("state") == "done":
                        return {"result": current.get("result"),
                                "job_completed": True}
                    if current and current.get("state") == "failed":
                        return {"error": current.get("error") or
                                "Cloudflare executor failed",
                                "retryable": False}
                finally:
                    probe.reset()
        except Exception as exc:
            last = exc
        time.sleep(2)
    raise RemoteExecutorError(
        f"Cloudflare call {call_id} could not be recovered: {last}") from last


def _run_cloudflare(job):
    if job.get("id") is None or job.get("total_claims") is None:
        raise CloudflareLaunchUnavailable(
            "Cloudflare canary accepts queue-backed jobs only")
    # Include the first authenticated readiness request in user-observed
    # provider startup. Successful readiness is cached, but its first cold
    # network trip is still real latency and must not disappear from the gate.
    job["_dispatch_submitted_at"] = time.time()
    try:
        _cloudflare_preflight(timeout=10)
    except requests.RequestException as exc:
        # No launch request was sent, so Modal fallback is unambiguous.
        raise CloudflareLaunchUnavailable(
            f"Cloudflare preflight failed before launch: {exc}") from exc

    call_id = _cloudflare_call_id(job)
    lane = _cloudflare_lane(job.get("type"))
    timeout_s = config.executor_timeout_for(job.get("type")) + 60
    if dbx.mark_remote_owned(job["id"]) is False:
        raise CloudflareLaunchUnavailable(
            "dispatcher shutdown began before Cloudflare submission")
    ledger = dbx.Db()
    try:
        ledger.run(dbx.record_remote_execution, job["id"],
                   job.get("total_claims"), "cloudflare", call_id, lane,
                   timeout_s, {"job_type": job.get("type"),
                               "execution_policy":
                                   config.execution_policy_for(job),
                               "queue_wait_s": job.get("_queue_wait_s")})
    except Exception as exc:
        print(f"[dispatcher] Cloudflare call {call_id} ledger write failed "
              f"({str(exc)[:160]}); deterministic call id still prevents a "
              "second Container instance", flush=True)
    finally:
        ledger.reset()

    deadline = time.monotonic() + timeout_s
    try:
        response = requests.post(
            f"{config.CLOUDFLARE_EXECUTOR_URL}/calls/{lane}/{call_id}",
            json={"job": _job_payload(job), "timeout_s": timeout_s},
            headers=_cloudflare_headers(),
            timeout=max(1, deadline - time.monotonic()))
    except requests.RequestException:
        dbx.remote_launch_recorded(job["id"])
        data = _recover_cloudflare_result(call_id, lane, job, deadline)
    else:
        dbx.remote_launch_recorded(job["id"])
        if response.status_code == 404:
            ledger = dbx.Db()
            try:
                ledger.run(dbx.finish_remote_execution, job["id"],
                           job.get("total_claims"), "cancelled",
                           "Cloudflare route was not deployed")
            except Exception:
                pass
            finally:
                ledger.reset()
            dbx.unmark_remote_owned(job["id"])
            raise CloudflareLaunchUnavailable(
                "Cloudflare Container route is not deployed")
        if response.status_code != 200:
            try:
                response_body = response.json()
            except ValueError:
                response_body = {}
            if response_body.get("safe_to_fallback"):
                ledger = dbx.Db()
                try:
                    ledger.run(dbx.finish_remote_execution, job["id"],
                               job.get("total_claims"), "cancelled",
                               response_body.get("error") or
                               "Cloudflare launch refused")
                except Exception:
                    pass
                finally:
                    ledger.reset()
                dbx.unmark_remote_owned(job["id"])
                raise CloudflareLaunchUnavailable(
                    str(response_body.get("error") or
                        "Cloudflare launch refused before /run"))
            # The Worker may have lost its side of an already-running
            # container request. Reconnect to the deterministic call before
            # considering any physical retry.
            return _interpret_executor_data(
                _recover_cloudflare_result(call_id, lane, job, deadline),
                job)
        try:
            data = response.json()
        except ValueError as exc:
            raise RemoteExecutorError(
                "Cloudflare call returned non-JSON") from exc
    return _interpret_executor_data(data, job)


def _run_cloud(job, url_override=None):
    url_base = url_override or _executor_url(job.get("type"))
    if not url_base:
        raise RemoteExecutorError("REMOTE_EXECUTOR_URL is not set")
    url = f"{url_base}/run"
    headers = {"Content-Type": "application/json"}
    if config.REMOTE_EXECUTOR_SECRET:
        headers["Authorization"] = f"Bearer {config.REMOTE_EXECUTOR_SECRET}"
    try:
        # Per-kind: a preview is a user staring at a spinner, a final is an
        # hour-long export nobody wants refused at minute 25. One number for
        # both was sized for the short one.
        job.setdefault("_dispatch_submitted_at", time.time())
        resp = requests.post(url, json={"job": _job_payload(job)},
                             headers=headers,
                             timeout=config.executor_timeout_for(
                                 job.get("type")))
    except requests.RequestException as e:
        # A transport failure (timeout, connection reset, cold-start slowness)
        # raises so process_one requeues within the media attempt budget — a
        # re-run is safe (renders are deterministic and cache-deduped).
        raise RemoteExecutorError(f"executor call failed: {e}") from e
    if resp.status_code == 404 \
            and url_base != config.REMOTE_EXECUTOR_URL:
        raise RemoteServiceUnavailable(
            f"derived executor service is not deployed: {url_base}")
    if resp.status_code != 200:
        body = (resp.text or "")[:500]
        raise RemoteExecutorError(
            f"executor returned {resp.status_code}: {body}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RemoteExecutorError(
            f"executor returned non-JSON: {(resp.text or '')[:300]}") from e
    return _interpret_executor_data(data, job)


def _run_remote(job, url_override=None, modal_function=None):
    provider = desired_execution_provider(job) if url_override is None \
        else "cloud_run"
    if provider == "cloudflare":
        try:
            return _run_cloudflare(job)
        except CloudflareLaunchUnavailable as exc:
            if not (config.CLOUDFLARE_MODAL_FALLBACK
                    and config.MODAL_EXECUTOR_ENABLED
                    and str(job.get("type") or "") in
                    config.MODAL_EXECUTOR_TYPES):
                raise
            print(f"[dispatcher] {exc}; launching the same fenced job on "
                  "Modal before any Cloudflare call was accepted",
                  flush=True)
            return _run_modal(job, modal_function)
    if provider == "local":
        raise RemoteExecutorError(
            f"no remote executor is configured for {job.get('type')}")
    if provider == "modal":
        try:
            return _run_modal(job, modal_function)
        except ModalLaunchUnavailable as exc:
            if (config.execution_policy_for(job) == "redesign"
                    or not config.MODAL_CLOUD_RUN_FALLBACK):
                raise
            print(f"[dispatcher] {exc}; using Cloud Run launch fallback",
                  flush=True)
    return _run_cloud(job, url_override=url_override)


def capture_available():
    """Is there an executor to run web captures on? (round 61)

    Chromium is baked into the same image the executor runs, so this is purely
    "is the executor configured". When it is not, the caller falls back to
    recording locally — which is what shipped before and is correct on a
    single-box deployment; it is only the LARGE dispatcher-plus-executor
    deployment where the browser has to move.
    """
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_capture_remote(project_id, payload, user_id=None):
    """Record a web page on the executor and return record()'s dict, with
    `storage_key` in place of `path` (the bytes never come back here).

    Not a queued job: this is called synchronously from inside an agent turn,
    so there is no row to claim and no id. _run_remote only reads the fields
    below, and the executor's runner only reads project_id and payload.
    """
    return _run_remote({"id": None, "type": "capture",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def fetch_available():
    """Whether a different executor egress can acquire a blocked URL."""
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def fetch_bytes_available():
    """Whether any configured alternate has not failed a real-byte probe.

    Unknown is allowed during rollout; a diagnostic outage never disables a
    capability.  Once every configured provider has an explicit failed byte
    verdict, however, making the user wait through the same known wall is not
    resilience. A successful post-proxy probe turns this back on by itself.
    """
    import ytaccess

    states = []
    if config.REMOTE_EXECUTOR_URL:
        states.append(ytaccess.provider_youtube_ok("cloud_run"))
    if config.MODAL_EXECUTOR_ENABLED:
        states.append(ytaccess.provider_youtube_ok("modal"))
    if not states:
        return False
    return True if any(s is True for s in states) else any(s is None
                                                            for s in states)


def _run_across_media_egress(job):
    """Run a safe stateless media operation through independent providers.

    Cloud Run and Modal use the same stateless runner but leave the internet
    through different networks. An explicit YouTube access wall advances to
    the next provider; a content verdict such as private/removed does not.
    Transport failure also earns the other provider, because no successful
    response means the caller has no usable storage key (a possible orphan is
    reclaimed with ordinary scratch/fetched-object lifecycle cleanup).
    """
    providers = []
    if config.MODAL_EXECUTOR_ENABLED:
        providers.append(("modal", lambda: _run_modal(
            job, function_override="egress")))
    # Modal is production. Keep the old endpoint only as an explicitly
    # enabled rollback after Modal has failed; never pay its latency before a
    # healthy Modal fetch (and never touch it when fallback is disabled).
    if config.REMOTE_EXECUTOR_URL \
            and config.execution_policy_for(job) != "redesign" and (
            not config.MODAL_EXECUTOR_ENABLED
            or config.MODAL_CLOUD_RUN_FALLBACK):
        providers.append(("cloud_run", lambda: _run_cloud(job)))
    if not providers:
        raise RemoteExecutorError("no alternate media-fetch executor is set")

    last_result, errors = None, []
    for name, invoke in providers:
        try:
            result = invoke()
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:220]}")
            continue
        if not isinstance(result, dict):
            errors.append(f"{name}: invalid fetch response")
            continue
        result["fetch_provider"] = name
        if result.get("ok"):
            return result
        last_result = result
        if not result.get("access_blocked"):
            return result
    if last_result is not None:
        if errors:
            last_result["provider_errors"] = errors
        return last_result
    raise RemoteExecutorError("; ".join(errors) or
                              "all alternate media executors failed")


def run_fetch_remote(project_id, payload, user_id=None):
    """Fetch and store media through the first egress that can reach it."""
    return _run_across_media_egress({
        "id": None, "type": "fetch", "project_id": project_id,
        "user_id": user_id, "attempts": 0, "payload": payload})


def run_search_remote(project_id, payload, user_id=None):
    """Discover named YouTube media through alternate egress providers."""
    return _run_across_media_egress({
        "id": None, "type": "search", "project_id": project_id,
        "user_id": user_id, "attempts": 0, "payload": payload})


def run_stock_acquire_remote(project_id, payload, user_id=None):
    """Acquire/probe/review stock bytes in the idempotent egress lane."""
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable(
            "Modal is required for stock-media acquisition")
    return _run_modal({"id": None, "type": "stock_acquire",
                       "project_id": project_id, "user_id": user_id,
                       "attempts": 0, "payload": payload},
                      function_override="egress")


def frames_available():
    """Is there an executor to decode a stored original on? (round 62)

    Same contract as capture_available: purely "is the executor configured".
    With no executor there is only one box, and the local decode is what
    shipped before — correct for that deployment, fatal only beside a
    dispatcher whose job is to stay light.
    """
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_frames_remote(project_id, payload, user_id=None):
    """Extract stills from a stored object on the executor. Returns
    frameserve.run_frames_job's dict: per-time storage keys (None where a
    seek failed), errors, and the probed duration. Synchronous, no job row —
    the round-61 capture shape."""
    return _run_remote({"id": None, "type": "frames",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def track_available():
    """Is there an executor to run quad tracking on? (round 63)

    Same contract as frames_available. Tracking decodes the WHOLE takeover
    window of what is usually a user's 4K original — the job class that has
    OOM-killed the dispatcher four times — so with no executor the caller
    keeps the static pin rather than attempting it locally."""
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_track_remote(project_id, payload, user_id=None):
    """Track a screen quad through a window of a stored object on the
    executor. Returns tracker.run_track_job's dict: {"quads", "quality"}.
    Synchronous, no job row — the round-61 capture shape."""
    return _run_remote({"id": None, "type": "track",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def matte_available():
    """Is there an executor to build the text-behind matte on? (round 64)

    Same contract as frames_available. The matte reads only the 540p proxy,
    but the person model's forward passes are CPU compute the dispatcher
    cannot afford beside agent turns — with no executor the caller builds the
    photometric mask locally, which is exactly what shipped before."""
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_matte_remote(project_id, payload, user_id=None):
    """Build the text-behind mask on the executor. Returns
    matte.measure_and_build's stats dict; on ok=True the mask is already at
    payload['out_key'] in storage. Synchronous, no job row — the round-61
    capture shape."""
    return _run_remote({"id": None, "type": "matte",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def smatch_available():
    """Is there an executor to run the takeover's guided content-lock on?
    (round 65d) Same contract as track_available — SIFT on 2048px frames of
    a user original OOM-killed the dispatcher the one time it ran there, so
    with no executor the caller refines on its small local frames only."""
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_smatch_remote(project_id, payload, user_id=None):
    """Match the takeover content against the filmed glass on the executor,
    guided by a vision read. Returns screenmatch.run_smatch_job's dict:
    {"match": {...} | None}. Synchronous, no job row."""
    return _run_remote({"id": None, "type": "smatch",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def clean_available():
    """Is there an executor to run the erase/repaint pass on? (round 67)
    Same contract as track_available: the clean pass decodes AND re-encodes
    every frame of a user original inside an agent turn — the heaviest member
    of the job class that has OOM-killed the dispatcher repeatedly — so with
    an executor configured it never runs locally, and a remote failure is an
    honest refusal, never a local retry."""
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED)


def run_clean_remote(project_id, payload, user_id=None):
    """Repaint erase regions (and/or run the cursor pass) on the executor.
    Returns inpaint.run_clean_job's dict — clean_video stats plus the
    before/after ink measurements and object sizes; on success both cleaned
    objects are already uploaded. Synchronous, no job row."""
    return _run_remote({"id": None, "type": "clean",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def stems_available():
    """Is there a build that can separate stems? (round 97)

    Unlike the pure "is the executor configured" gates above, the demucs
    dependency exists only where it was baked into the image — so the honest
    answer comes from /health's features (executor_supports), or from this
    process's own import when there is no executor at all. None (executor
    unreachable) follows the round-53 rule: unknown is not "no"."""
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED:
        import stems
        return stems.available()
    return executor_supports("stems")


def run_stems_remote(project_id, payload, user_id=None):
    """Separate a stored source's audio into vocals/accompaniment on the
    executor (or locally when none is configured). Returns
    stems.run_stems_job's dict; both stems are already uploaded on ok.
    Synchronous, no job row — the round-61 capture shape."""
    job = {"id": None, "type": "stems", "project_id": project_id,
           "user_id": user_id, "attempts": 0, "payload": payload}
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED:
        import stems
        return stems.run_stems_job(None, job)
    return _run_remote(job)


def run_render_remote(worker_db, job):      # signature matches run_render_job
    if job.get("type") == "final" and not _modal_selected(job):
        try:
            return _launch_batch_and_wait(worker_db, job)
        except BatchUnavailable as exc:
            print(f"[dispatcher] batch final unavailable ({exc}); using "
                  "request executor for this job", flush=True)
    return _run_request_with_capacity_fallback(job)


def run_filmstrip_remote(worker_db, job):
    """Run timeline-art decoding on a scale-to-zero provider.

    Filmstrip jobs are queue-backed and therefore use the same durable Modal
    launch/fenced completion contract as renders. This intentionally bypasses
    percentage selection and Cloud Run fallback: a 4K asset filmstrip is the
    workload that OOM-killed the 512-MiB dispatcher, and Google is no longer a
    production execution target.
    """
    provider = desired_execution_provider(job)
    if provider == "cloudflare":
        return _run_remote(job)
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable(
            "Modal or Cloudflare is required for filmstrip compute")
    # The 2-core/2-GiB preview reservation is enough for two single-threaded
    # asset seeks while costing far less than the generic 8-GiB light lane.
    return _run_modal(job, function_override="preview")


def mcp_media_available():
    """Whether video bytes can be encoded away from the dispatcher."""
    return bool(config.MODAL_EXECUTOR_ENABLED)


def run_mcp_media_remote(project_id, payload, user_id=None):
    """Run only the resolved MCP video encode on Modal.

    The dispatcher resolves/waits for a timeline preview first, then sends a
    stateless id-less call. Modal is consequently billed for the encode, not
    for idling while another Modal function renders the preview source.
    """
    if (payload or {}).get("tool") != "__media__":
        raise ValueError("Modal MCP offload accepts __media__ only")
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable("Modal is required for MCP media encode")
    return _run_modal({"id": None, "type": "mcp_tool",
                       "project_id": project_id, "user_id": user_id,
                       "attempts": 0, "payload": payload},
                      function_override="preview")


def run_index_remote(worker_db, job):       # signature matches run_index_job
    if not _modal_selected(job):
        try:
            return _launch_batch_and_wait(worker_db, job)
        except BatchUnavailable as exc:
            print(f"[dispatcher] batch index unavailable ({exc}); using "
                  "request executor for this job", flush=True)
    return _run_request_with_capacity_fallback(job)


def _run_request_with_capacity_fallback(job):
    """Use the fast 16-GiB lane, preserving 32-GiB upload compatibility."""
    primary = _executor_url(job.get("type"))
    try:
        return _run_remote(job)
    except Exception as error:
        is_capacity = getattr(error, "failure_kind", "") == \
            "executor_capacity"
        definitely_missing = isinstance(error, RemoteServiceUnavailable)
        modal_capacity = (desired_execution_provider(job)
                          in {"modal", "cloudflare"}
                          and config.MODAL_EXECUTOR_ENABLED and is_capacity)
        cloud_sibling_fallback = primary \
            and primary != config.REMOTE_EXECUTOR_URL \
            and (is_capacity or definitely_missing)
        if modal_capacity or cloud_sibling_fallback:
            why = "source needs 32 GiB" if is_capacity else \
                "right-sized service is not deployed"
            print(f"[dispatcher] {job.get('type')} {why}; using heavy "
                  "request executor once", flush=True)
            if modal_capacity:
                return _run_modal(job, function_override="heavy")
            return _run_remote(job, url_override=config.REMOTE_EXECUTOR_URL)
        raise


def run_agent_remote(worker_db, job):       # signature matches run_agent_job
    remote_job = dict(job)
    created = job.get("created_at")
    if created is not None:
        try:
            remote_job["_queue_wait_s"] = round(max(
                0.0, (datetime.now(timezone.utc) - created).total_seconds()), 2)
        except (TypeError, ValueError):
            remote_job["_queue_wait_s"] = None
    return _run_remote(remote_job)


def run_mcp_remote(worker_db, job):         # signature matches run_mcp_job
    """Run external MCP orchestration in its own scale-to-zero pool.

    The queued job and monotonic lease make the launch durable. Any ffmpeg,
    acquisition, vision or generation requested by the tool is launched by
    that orchestrator onto the appropriate child function; none runs on the
    Render dispatcher.
    """
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable("Modal is required for MCP orchestration")
    return _run_modal(job, function_override="mcp")


def run_shorts_remote(worker_db, job):      # signature matches shorts runner
    """Run story planning independently from Studio and MCP capacity."""
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable(
            "Modal is required for Shorts orchestration")
    return _run_modal(job, function_override="shorts")


def run_probe_remote(payload=None):
    """Launch the real-byte egress probe without using dispatcher network."""
    if not config.MODAL_EXECUTOR_ENABLED:
        raise ModalLaunchUnavailable("Modal is required for remote probing")
    return _run_modal({"id": None, "type": "ytprobe", "project_id": None,
                       "user_id": None, "attempts": 0,
                       "payload": payload or {}},
                      function_override="probe")
