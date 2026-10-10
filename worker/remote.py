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
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import psycopg2
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


class CloudflareCapacityBusy(CloudflareLaunchUnavailable):
    """No Cloudflare call was accepted because its selected shard was busy.

    This is distinct from an ambiguous launch. Queue-backed callers may
    safely defer the unchanged job and obtain a new fenced claim identity.
    """


class CloudflareRolloutPending(CloudflareLaunchUnavailable):
    """A source/role readiness gate proved that no compute was accepted.

    A deploy is in progress. Queue-backed calls wait within their existing
    lease for CLOUDFLARE_ROLLOUT_WAIT_S. Once that bounded window ends the
    outcome is an explicit, retryable "deploy in progress": nothing ran, so
    the caller (or one bounded media retry) may safely try again.
    """

    failure_kind = "provider_rollout_pending"
    retryable = True
    max_attempts = 1
    agent_repairable = False


class CloudflareCapacityUnavailable(CloudflareLaunchUnavailable):
    """Cloudflare could not provide a container before any /run was sent.

    The Containers runtime answers "there is no container instance that can
    be provided to this durable object" (or rate-limits container starts)
    after its own ~30-s instance search, and a startup it abandoned before
    /run ends as provider_start_abandoned. Either way the Durable Object
    stored no accepted call, so the same claim may start under another
    Durable Object identity, on another suitable lane, or on the fenced
    fallback provider. When every route is refused, the final instance says
    so plainly and is retryable: nothing ran and nothing changed.
    """

    failure_kind = "provider_capacity_unavailable"
    retryable = True
    max_attempts = 1
    agent_repairable = False


class _CloudflareRefused(Exception):
    """A reconnect found the call's own pre-/run refusal tombstone."""

    def __init__(self, body):
        super().__init__(str((body or {}).get("error") or
                             "Cloudflare refused the call before /run"))
        self.body = body if isinstance(body, dict) else {}


class CloudflareTerminalFailure(RemoteExecutorError):
    """A named Cloudflare call ended; an alternate provider is now safe."""


# Messages the Containers runtime and SDK use when no container could be
# started for a Durable Object. The adapter also flags these structurally
# (capacity_unavailable); the text keeps an older adapter classifiable.
_CAPACITY_REFUSAL_TEXT = (
    "there is no container instance",
    "requesting too many containers per second",
    "throttling the container service",
)


def _capacity_refusal_text(text):
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _CAPACITY_REFUSAL_TEXT)


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
    job_type = str(job.get("type") or "")
    # Synchronous child types are an additive safe subset.  Do not make a
    # newly shipped child route depend on Render's older explicit queue-type
    # allowlist being edited in the same rollout: that stale env value is how
    # frames silently remained Modal-primary after the code was deployed.
    if job_type not in config.CLOUDFLARE_EXECUTOR_TYPES and \
            job_type not in config.CLOUDFLARE_SYNCHRONOUS_TYPES:
        return False
    synchronous = job.get("id") is None
    if synchronous and job_type not in config.CLOUDFLARE_SYNCHRONOUS_TYPES:
        return False
    percent = config.CLOUDFLARE_EXECUTOR_PERCENT
    if percent <= 0:
        return False
    if percent < 100:
        stable = (f"cloudflare:{job.get('id')}:{job.get('project_id')}:"
                  f"{job_type}:" + (json.dumps(
                      job.get("payload") or {}, sort_keys=True,
                      separators=(",", ":"), default=str)
                      if synchronous else ""))
        bucket = int(hashlib.sha256(
            stable.encode("utf-8")).hexdigest()[:8], 16)
        if bucket % 100 >= percent:
            return False
    # Orchestration containers operate on database state and small proxies;
    # they do not stage the project's original media. A 14-GB podcast must
    # not force its otherwise-light MCP/agent control loop back to Modal.
    if str(job.get("type") or "") in {
            "agent_turn", "mcp_tool", "shorts_plan"}:
        return True
    # A synchronous frame look has no queue row from which to probe a shape.
    # It is bounded to twelve stills, runs in the 12-GiB interactive profile,
    # and a provider-capacity terminal can safely fall back to Modal.
    if synchronous:
        return True
    shape = (job.get("_execution_shape") or
             (job.get("payload") or {}).get("execution_shape") or {})
    if not shape:
        return False
    try:
        staged_bytes = int(shape.get("total_bytes") or 0)
        original_bytes = int(shape.get("original_bytes") or 0)
        # Render sources above this threshold are range-read by the renderer.
        # Counting their full size rejected a 3.2-GB original plus 1.4 GB of
        # inserts even though only the inserts needed local disk (2166).
        if shape.get('shape_version') == 2:
            staged_bytes = int(shape['staged_bytes'])
        elif job_type in {"preview", "preview_check", "final"} \
                and config.CLOUDFLARE_STREAM_SOURCE_MIN_BYTES > 0 \
                and original_bytes >= config.CLOUDFLARE_STREAM_SOURCE_MIN_BYTES:
            staged_bytes = max(0, staged_bytes - original_bytes)
        elif job_type == "filmstrip" and shape.get("proxy_bytes"):
            # Filmstrips stage the completed proxy, never its unused original.
            staged_bytes = max(0, staged_bytes - original_bytes) + int(shape["proxy_bytes"])
        byte_limit = (config.CLOUDFLARE_MAX_INDEX_INPUT_BYTES
                      if job_type == "index" else config.CLOUDFLARE_MAX_INPUT_BYTES)
        # Indexing downloads one source. Unknown/zero metadata cannot prove
        # it will fit; unlike a streamed render, zero is not a valid shape.
        bytes_ok = (0 < staged_bytes <= byte_limit if job_type == "index"
                    else 0 <= staged_bytes <= byte_limit)
        duration_limit = float(config.CLOUDFLARE_MAX_SOURCE_DURATION_S)
        duration_ok = duration_limit <= 0 or (
            float(shape.get("max_duration_s") or 0) <= duration_limit)
        return bytes_ok and duration_ok
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
            shape = worker_db.run(
                dbx.project_execution_shape, job.get("project_id"),
                (job.get("payload") or {}).get("asset_id"),
                job.get("type"), job.get("payload") or {}, True)
            # PostgreSQL NUMERIC values arrive as Decimal.  Normalize the
            # in-memory copy before the first provider request, exactly as the
            # JSONB persistence path does.  Otherwise attempt one fails in the
            # HTTP JSON encoder while a retry mysteriously succeeds after
            # reloading the already-normalized payload from PostgreSQL.
            job["_execution_shape"] = dbx._json_safe(shape or {})
        except Exception as exc:
            # Unknown capacity must not bypass the container limit. Only a
            # configured alternate provider can accept this job instead.
            print(f"[dispatcher] Cloudflare shape probe failed for job "
                  f"{job.get('id')}: {str(exc)[:160]}; checking configured alternatives",
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
    if job_type in {"mcp_tool", "preview", "preview_check"}:
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
    if config.CLOUDFLARE_EXECUTOR_ENABLED \
            and config.CLOUDFLARE_EXECUTOR_URL \
            and (job_type is None
                 or job_type in config.CLOUDFLARE_EXECUTOR_TYPES
                 or job_type in config.CLOUDFLARE_SYNCHRONOUS_TYPES):
        return _cloudflare_preflight(timeout=timeout)
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
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED \
            and not (config.CLOUDFLARE_EXECUTOR_ENABLED and
                     config.CLOUDFLARE_EXECUTOR_URL):
        return
    now = time.monotonic()
    with _warm_lock:
        if now - _warm_last < _WARM_COOLDOWN_S:
            return
        _warm_last = now
    try:
        if config.CLOUDFLARE_EXECUTOR_ENABLED \
                and config.CLOUDFLARE_EXECUTOR_URL:
            _cloudflare_preflight(timeout=10)
            return
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
    # Cloudflare reports both the deployment commit and the shared worker
    # source fingerprint. The dispatcher runs from that shared source tree,
    # so compare source-to-source; a metadata-only deploy commit is not skew.
    remote_v = str(theirs.get("source_version") or
                   theirs.get("code_version") or "unknown")
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
            and "agent_turn" in config.MODAL_EXECUTOR_TYPES) and not (
            config.CLOUDFLARE_EXECUTOR_ENABLED
            and config.CLOUDFLARE_EXECUTOR_URL
            and "agent_turn" in config.CLOUDFLARE_EXECUTOR_TYPES):
        return ""
    mine = version.code_version()
    try:
        theirs = executor_health(job_type="agent_turn")
    except Exception as e:
        if not quiet:
            print(f"[dispatcher] agent executor version check failed: "
                  f"{str(e)[:200]}", flush=True)
        return ""
    remote_v = str(theirs.get("source_version") or
                   theirs.get("code_version") or "unknown")
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
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED \
            and not (config.CLOUDFLARE_EXECUTOR_ENABLED and
                     config.CLOUDFLARE_EXECUTOR_URL):
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
        # 2000 = what the job row keeps (db.py); ffmpeg's cause is at the end.
        msg = error_text.excerpt(data["error"], 2000)
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


def _cloudflare_refusal_settled(status, row, now=None):
    """Whether a `refused` record proves this ledger row's launch is orphaned.

    The record names no launch the guardian can match, so it counts only
    when it was written after this row's launch was recorded (a relaunch of
    the same identity refreshes submitted_at) and has stood for the attach
    grace: an attached dispatcher reads its own refusal within one 2-s poll,
    closes the row and moves on, so a refusal still open after that has no
    dispatcher left. Anything unparseable keeps the old wait-for-deadline.
    """
    try:
        refused_at = datetime.fromisoformat(
            str(status.get("updatedAt") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if refused_at.tzinfo is None:
        refused_at = refused_at.replace(tzinfo=timezone.utc)
    submitted = row.get("submitted_at")
    if not isinstance(submitted, datetime):
        return False
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return refused_at >= submitted and (now - refused_at).total_seconds() \
        >= config.REMOTE_GUARDIAN_ATTACH_GRACE_S


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
            lane = row.get("function_name") or \
                _cloudflare_lane(row.get("type"))
            status = _cloudflare_status(row["call_id"], lane, timeout=10)
            state = status.get("status")
            if state == "unknown":
                # A lost /run response is not proof of life, so it earns no
                # heartbeat; a live executor keeps beating on its own. Fence
                # and fail the call once PostgreSQL shows it is dead.
                data = _abandon_if_dead(row["call_id"], lane, job, worker_db)
                if data is None:
                    return {"status": "running", "job": job}
            elif state in {"submitted", "starting", "running", "stopping"}:
                worker_db.run(
                    dbx.heartbeat_remote_execution, job_id, claim)
                return {"status": "running", "job": job}
            elif state == "missing":
                return {"status": "unknown", "job": job}
            elif state == "refused" and not _cloudflare_refusal_settled(
                    status, row):
                # A pre-/run refusal that an attached dispatcher (polling
                # every 2 s) has not had time to close and route elsewhere,
                # or an older launch's refusal that a relaunch of the same
                # identity is replacing. Neither is an orphan yet.
                return {"status": "unknown", "job": job}
            else:
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
                      "failed", exc, provider, row.get("call_id"))
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
        closed = worker_db.run(
            dbx.finish_remote_execution, job_id, claim,
            "failed", exc, provider, row.get("call_id"))
        # A rollout-abandoned `starting` state is positive proof that the
        # executor never sent /run. The attached dispatcher normally switches
        # providers itself; if the orphan guardian observes the terminal first,
        # queue the exact job for Modal without consuming MCP's one real
        # attempt. The call-scoped SQL fence loses harmlessly if the attached
        # dispatcher already replaced this ledger with its Modal call.
        if decision.kind == "provider_start_abandoned" \
                and provider == "cloudflare" \
                and config.CLOUDFLARE_MODAL_FALLBACK \
                and config.MODAL_EXECUTOR_ENABLED \
                and str(job.get("type") or "") in \
                config.MODAL_EXECUTOR_TYPES:
            if not closed:
                return {"status": "superseded", "job": job, "error": exc}
            queued = worker_db.run(
                dbx.requeue_provider_fallback, job_id, claim,
                "cloudflare", row.get("call_id"), "modal", exc)
            return {
                "status": ("provider_fallback_queued" if queued
                           else "superseded"),
                "job": job, "error": exc,
            }
        if failure_policy.defer_prerequisite(
                worker_db, job, exc, decision, claim):
            return {"status": "requeued", "job": job, "error": exc}
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
                      terminal, current.get("error"), provider,
                      row.get("call_id"))
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
            persisted = _record_remote_execution_with_retry(
                ledger, job, "modal", call_id, name, execution_timeout_s)
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
                               job.get("total_claims"), "failed", exc,
                               "modal", call_id)
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
                           job.get("total_claims"), "failed", exc,
                           "modal", call_id)
            except Exception:
                pass
            finally:
                ledger.reset()
        raise
    if remote_handoff:
        ledger = dbx.Db()
        try:
            ledger.run(dbx.finish_remote_execution, job["id"],
                       job.get("total_claims"), "done", None,
                       "modal", call_id)
        except Exception:
            pass
        finally:
            ledger.reset()
    return result


def _cloudflare_lane(job_type):
    if job_type in {"preview", "preview_check", "filmstrip", "frames",
                    "mcp_media"}:
        return "interactive"
    if job_type in {"agent_turn", "mcp_tool", "shorts_plan"}:
        return {"agent_turn": "agent", "mcp_tool": "mcp",
                "shorts_plan": "shorts"}[job_type]
    return "batch"


def _cloudflare_lane_for(job):
    """The lane this dispatch uses: its home lane unless it failed over."""
    return job.get("_cloudflare_lane") or _cloudflare_lane(job.get("type"))


def _cloudflare_failover_lanes(job):
    """Other lanes that can run this exact job after a capacity refusal.

    Only strict capability matches are listed. The batch image is the
    interactive image built with FULL_COMPUTE=1 (a superset) under the same
    `executor` role, so media work moves interactive -> batch. Never the
    reverse: interactive lacks the YouTube PO-token provider, segmentation
    models and Demucs that fetch/search/matte/stems need. A watch_video
    (`__media__`) is media work that both executor lanes run directly; every
    other MCP tool, Studio turn and Shorts plan needs its own role's image.
    """
    job_type = job.get("type")
    if job_type in {"frames", "mcp_media", "preview", "preview_check",
                    "filmstrip"}:
        return ("batch",)
    if job_type == "mcp_tool" \
            and (job.get("payload") or {}).get("tool") == "__media__":
        return ("interactive", "batch")
    return ()


def _record_remote_execution_with_retry(ledger, job, provider, call_id,
                                        function_name, timeout_s):
    """Durably publish one provider call identity, boundedly.

    Modal has accepted its call by this point; Cloudflare's deterministic call
    identity is reserved just before submission. The id is immutable in both
    cases, so retrying this database upsert survives a recovery without
    submitting duplicate paid compute. The executor remains idle behind the
    matching ownership fence until this succeeds or the bounded deadline.
    """
    started = time.monotonic()
    deadline = started + config.REMOTE_HANDOFF_PERSIST_S
    failures = 0
    while True:
        try:
            persisted = ledger.run(
                dbx.record_remote_execution, job["id"],
                job.get("total_claims"), provider, call_id, function_name,
                timeout_s, {
                    "job_type": job.get("type"),
                    "execution_policy": config.execution_policy_for(job),
                    "queue_wait_s": job.get("_queue_wait_s"),
                })
            if failures:
                elapsed = time.monotonic() - started
                print(f"[dispatcher] {provider} call {call_id} handoff "
                      f"recovered after {failures} database failures "
                      f"({elapsed:.1f}s)", flush=True)
            return persisted
        except (psycopg2.OperationalError,
                psycopg2.InterfaceError) as exc:
            failures += 1
            ledger.reset()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            if failures == 1:
                print(f"[dispatcher] {provider} call {call_id} database "
                      "handoff failed and will retry the same call identity "
                      f"boundedly ({str(exc)[:160]})", flush=True)
            time.sleep(min(1.0, remaining))


def _cloudflare_alternate_safe(job):
    payload = job.get("payload") or {}
    return (job.get("type") in {"preview", "preview_check", "filmstrip", "mcp_media"}
            or (job.get("type") == "mcp_tool"
                and payload.get("tool") == "review_audio"
                and payload.get("mutation") is False))


def _cloudflare_call_id(job):
    if job.get("id") is None:
        # Scratch frame keys are consumed and deleted by the caller, so two
        # later identical looks must not reuse a week-old terminal envelope.
        # The nonce lives on this in-memory handoff: an ambiguous HTTP result
        # reconnects the same named call, while a genuinely new look is new.
        nonce = job.setdefault("_cloudflare_sync_nonce", uuid.uuid4().hex)
        raw = f"{job.get('type')}:sync:{nonce}"
    else:
        raw = f"{job.get('type')}:{job.get('id')}:{job.get('total_claims')}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    job_type = str(job.get("type") or "job")
    admission_slot = job.get('_cloudflare_admission_slot', 0)
    # A capacity refusal proves the pinned Durable Object could not get a
    # container, so any job may spread to an alternate identity: there is no
    # warm process (or project cache) left on the refused shard to preserve.
    if admission_slot in (1, 2) and (
            _cloudflare_alternate_safe(job)
            or job.get("_cloudflare_capacity_spread")):
        try:
            alt_project = max(0, int(job.get("project_id")))
        except (TypeError, ValueError):
            alt_project = 0
        return f"cf-alt{admission_slot}-{job_type}-p{alt_project}-{digest}"
    group = str((job.get("payload") or {}).get("render_group") or "")
    if job_type in {"final", "preview", "preview_check"} and re.fullmatch(r"[0-9]+-[01]", group):
        return f"cf-render-g{group}-{digest}"
    if job_type in {"mcp_tool", "preview", "preview_check"}:
        # Cloudflare recognizes this stable prefix and sends every call for a
        # project to the same MCP shard. ToolContext intentionally caches
        # search handles (notably stock ids) between calls; call-id sharding
        # sent search_stock and add_stock_media to different processes and
        # made 129 valid ids look unknown. Preview jobs likewise need a
        # stable project key so successive revisions reuse resident media.
        try:
            project_id = max(0, int(job.get("project_id")))
        except (TypeError, ValueError):
            project_id = 0
        prefix = "mcp" if job_type == "mcp_tool" else "preview"
        return f"cf-{prefix}-p{project_id}-{digest}"
    return f"cf-{job_type[:18]}-{digest}"


def _interpret_cloudflare_terminal(data, job):
    """Mark a confirmed terminal envelope as safe for selective fallback."""
    try:
        return _interpret_executor_data(data, job)
    except Exception as exc:
        # A failed startup can end in the router before Python ever runs and
        # closes its ledger. Release only this positively terminal identity,
        # including when provider fallback is disabled; otherwise requeueing
        # leaves the retry fenced out until the old provider deadline.
        if isinstance(data, dict) and data.get("error") \
                and job.get("id") is not None \
                and job.get("total_claims") is not None:
            ledger = dbx.Db()
            try:
                ledger.run(dbx.finish_remote_execution, job["id"],
                           job["total_claims"], "failed", exc,
                           "cloudflare", _cloudflare_call_id(job))
            except Exception as close_error:
                # The guardian observes queued leases too, so a failed
                # bookkeeping write can recover without launching twice.
                print(f"[dispatcher] terminal Cloudflare ledger close for "
                      f"{job['id']} deferred ({type(close_error).__name__})",
                      flush=True)
            finally:
                ledger.reset()
        terminal = CloudflareTerminalFailure(str(exc))
        for attr in ("failure_kind", "retryable", "max_attempts",
                     "agent_repairable", "executor_timings", "stderr_tail"):
            if hasattr(exc, attr):
                setattr(terminal, attr, getattr(exc, attr))
        raise terminal from exc


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
        remote_source = str(body.get("source_version") or "unknown")
        local_source = version.code_version()
        if remote_source != "unknown" and local_source != "unknown" and \
                remote_source != local_source:
            # No call has been reserved. Wait for this rollout when there is
            # no configured alternate; failing ordinary jobs here makes
            # every deployment consume the customer's retry allowance.
            raise CloudflareRolloutPending(
                "Cloudflare source version does not match the dispatcher "
                f"({remote_source} != {local_source})")
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


def _reconcile_completed_cloudflare_call(call_id, lane):
    """Release only a named call whose exact execution durably succeeded.

    The provider's HTTP handler can disappear after Python commits success.
    That must not occupy a warm shard for the remainder of its six-hour
    lease. This acknowledgement neither reruns work nor stops a container.
    A missing proof, database outage or provider refusal leaves it reserved.
    """
    if not isinstance(call_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{8,96}", call_id):
        return False
    probe = dbx.Db()
    try:
        completed = probe.run(dbx.completed_remote_call,
                              "cloudflare", call_id, lane)
        if not isinstance(completed, dict):
            return False
        response = requests.post(
            f"{config.CLOUDFLARE_EXECUTOR_URL}/calls/{lane}/{call_id}/complete",
            json={"job": {key: completed[key] for key in
                          ("id", "type", "project_id", "total_claims")},
                  "envelope": {"result": dbx._json_safe(completed["result"]),
                               "job_completed": True}},
            headers=_cloudflare_headers(), timeout=10)
        return response.status_code == 200
    except Exception as exc:
        print(f"[dispatcher] Cloudflare completion acknowledgement for "
              f"{call_id} deferred ({type(exc).__name__})", flush=True)
        return False
    finally:
        probe.reset()


def _cloudflare_liveness_row(conn, job_id, stale_s):
    """Read-only liveness evidence for the queue claim behind one call.

    The executor process heartbeats both rows every HEARTBEAT_EVERY_S while
    it works; the dispatcher no longer refreshes them for an `unknown` call.
    """
    if not dbx.remote_executions_table_ready(conn):
        return None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.state AS job_state, j.total_claims AS job_claims,
                   (j.heartbeat_at IS NULL OR j.heartbeat_at
                      < NOW() - make_interval(secs => %s)) AS heartbeat_stale,
                   r.total_claims AS remote_claims,
                   r.provider AS remote_provider,
                   r.call_id AS remote_call_id, r.state AS remote_state,
                   r.error AS remote_error,
                   (r.last_observed_at IS NULL OR r.last_observed_at
                      < NOW() - make_interval(secs => %s)) AS observed_stale
              FROM video_jobs j
              LEFT JOIN remote_executions r ON r.job_id = j.id
             WHERE j.id = %s""", (stale_s, stale_s, job_id))
        row = cur.fetchone()
        return dict(row) if row else {"job_state": "missing"}


_STALE_HEARTBEAT_REASON = "no executor heartbeat for "


def _short_reason_detail(text, limit=120):
    """One printable line, keeping both the error's identity and its tail."""
    flat = " ".join(re.sub(r"[^\x20-\x7e]", " ", str(text or "")).split())
    if len(flat) <= limit:
        return flat
    head = limit // 3
    return f"{flat[:head]} ... {flat[-(limit - head - 5):]}"


def _cloudflare_dead_call_reason(row, job, call_id):
    """Why this call's executor provably cannot still own the claim, or None.

    Durable success is never abandoned (the completion acknowledgement
    releases it with its real result), and absent evidence means alive.
    """
    if not isinstance(row, dict):
        return None
    state = row.get("job_state")
    if state == "missing":
        return "its queue job no longer exists"
    claim = job.get("total_claims")
    if row.get("job_claims") != claim:
        return "its queue lease moved to a newer claim"
    if state == "done":
        return None
    if state != "running":
        return f"its queue job is {state}"
    if row.get("remote_provider") is not None:
        if not (row.get("remote_claims") == claim
                and row.get("remote_provider") == "cloudflare"
                and str(row.get("remote_call_id")) == str(call_id)):
            return "another provider call owns its claim"
        if row.get("remote_state") in {"failed", "cancelled"}:
            # Carry the executor's own cause: a mutation abandoned as
            # "outcome unknown" otherwise loses why it actually failed.
            detail = _short_reason_detail(row.get("remote_error"))
            return (f"its executor recorded {row['remote_state']}"
                    + (f": {detail}" if detail else ""))
    if row.get("heartbeat_stale") is True \
            and row.get("observed_stale") is not False:
        return (_STALE_HEARTBEAT_REASON
                + f"{int(config.CLOUDFLARE_DEAD_CALL_STALE_S)}s")
    return None


# (call id, claim) -> [first, last] monotonic times of stale-heartbeat
# readings. A fresh reading, a terminal outcome or a gap longer than
# _STALE_SIGHTING_MAX_GAP_S between readings forgets it. Dispatcher threads
# and the orphan guardian of one process share it; each process confirms on
# its own readings.
_STALE_SIGHTINGS = {}
_STALE_SIGHTINGS_LOCK = threading.Lock()
# The orphan guardian reads every REMOTE_GUARDIAN_INTERVAL_S (15 s) and an
# attached dispatcher about every 30 s, so a longer silence means this
# process stopped observing the call and cannot vouch for what happened.
_STALE_SIGHTING_MAX_GAP_S = 120.0


def _stale_heartbeat_confirmed(call_id, claim, now=None):
    """True when this stale reading confirms an earlier one.

    The first stale reading only starts the clock. Abandonment needs another
    stale reading CLOUDFLARE_DEAD_CALL_CONFIRM_S later, so an executor whose
    heartbeat thread is still reconnecting after a database outage has time
    to beat again (which forgets the sighting) before anything is destroyed.
    """
    now = time.monotonic() if now is None else now
    key = (str(call_id), claim)
    with _STALE_SIGHTINGS_LOCK:
        for other, (_first, last) in list(_STALE_SIGHTINGS.items()):
            if now - last > _STALE_SIGHTING_MAX_GAP_S:
                del _STALE_SIGHTINGS[other]
        seen = _STALE_SIGHTINGS.get(key)
        if seen is None:
            _STALE_SIGHTINGS[key] = [now, now]
            return False
        seen[1] = now
        return now - seen[0] >= config.CLOUDFLARE_DEAD_CALL_CONFIRM_S


def _forget_stale_sighting(call_id, claim):
    with _STALE_SIGHTINGS_LOCK:
        _STALE_SIGHTINGS.pop((str(call_id), claim), None)


def _abandon_cloudflare_call(call_id, lane, job, reason):
    """Ask the shard to fence and fail one dead call; its envelope or None."""
    try:
        response = requests.post(
            f"{config.CLOUDFLARE_EXECUTOR_URL}/calls/{lane}/{call_id}/abandon",
            json={"job": {key: job.get(key) for key in
                          ("id", "type", "project_id", "total_claims")},
                  "reason": reason},
            headers=_cloudflare_headers(), timeout=60)
        body = response.json() if response.status_code == 200 else {}
    except Exception as exc:
        # The shard may still finish the fence; the next status read sees it.
        print(f"[dispatcher] Cloudflare abandonment of {call_id} deferred "
              f"({type(exc).__name__})", flush=True)
        return None
    if not isinstance(body, dict):
        return None
    envelope = body.get("envelope")
    if body.get("status") in {"done", "failed"} and isinstance(envelope, dict):
        return envelope
    return None


def _abandon_if_dead(call_id, lane, job, worker_db=None):
    """Release one `unknown` queue-backed call whose executor is dead.

    Studio agent turns are left to their lease: the reaper's bounded death
    resume (a fresh pass over the same request) needs the queue row to stay
    running, and a terminal "outcome unknown" here would bypass it.
    TODO(cloudflare-dead-agent-turns): a dead `unknown` Studio turn still
    holds its agent shard until its 21,600-s lease. Abandon it here too and
    call the reaper's death resume (dbx.enqueue_agent_continuation, bounded
    by death_resume_count) directly instead of waiting for the lease.
    """
    if job.get("id") is None or job.get("total_claims") is None \
            or job.get("type") == "agent_turn":
        return None
    probe = worker_db or dbx.Db()
    try:
        row = probe.run(_cloudflare_liveness_row, job["id"],
                        config.CLOUDFLARE_DEAD_CALL_STALE_S)
    except Exception as exc:
        print(f"[dispatcher] Cloudflare liveness check for {call_id} "
              f"deferred ({type(exc).__name__})", flush=True)
        return None
    finally:
        if worker_db is None:
            probe.reset()
    claim = job.get("total_claims")
    reason = _cloudflare_dead_call_reason(row, job, call_id)
    if not reason:
        _forget_stale_sighting(call_id, claim)
        return None
    if reason.startswith(_STALE_HEARTBEAT_REASON) \
            and not _stale_heartbeat_confirmed(call_id, claim):
        print(f"[dispatcher] Cloudflare call {call_id} for job {job['id']} "
              f"shows {reason}; confirming before abandoning it", flush=True)
        return None
    print(f"[dispatcher] Cloudflare call {call_id} for job {job['id']} is "
          f"dead ({reason}); asking its shard to fence and fail it",
          flush=True)
    envelope = _abandon_cloudflare_call(call_id, lane, job, reason)
    if envelope is not None:
        _forget_stale_sighting(call_id, claim)
    return envelope


def _release_dead_cloudflare_owner(call_id, lane):
    """A busy shard's owner may be a dead `unknown` call; release it."""
    if not isinstance(call_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{8,96}", call_id):
        return False
    try:
        status = _cloudflare_status(call_id, lane, timeout=10)
    except Exception:
        return False
    owner = status.get("job") if isinstance(status.get("job"), dict) else {}
    if status.get("status") != "unknown" or not owner.get("type") \
            or not all(isinstance(owner.get(key), int) for key in
                       ("id", "project_id", "total_claims")):
        return False
    return _abandon_if_dead(call_id, lane, owner) is not None


# Check an `unknown` call's liveness about every 30 s of 2-s status polls.
_LIVENESS_EVERY_POLLS = 15


def _refusal_for_launch(status, launch_id):
    """The refusal body when this status is our own launch's refusal.

    A call id can be refused, then launched again (a rollout wait reuses its
    identity). Only a refusal stamped with this request's launch nonce proves
    this request was refused; an older one says nothing about a request that
    may still be on its way, so it counts as "not observable yet".
    """
    if status.get("status") != "refused" or not launch_id \
            or status.get("launchId") != launch_id:
        return None
    return status.get("envelope") or status


def _recover_cloudflare_result(call_id, lane, job, deadline):
    """Reconnect a named Container call without launching another instance."""
    launch_id = job.get("_cloudflare_launch_id")
    last = None
    unknown_polls = 0
    while time.monotonic() < deadline:
        try:
            status = _cloudflare_status(call_id, lane, timeout=10)
            state = status.get("status")
            if state == "unknown":
                # Not proof of life: only the executor's own heartbeat keeps
                # this claim fresh now. Once PostgreSQL proves the executor
                # dead, the shard fences the container and fails the call.
                unknown_polls += 1
                if (unknown_polls - 1) % _LIVENESS_EVERY_POLLS == 0:
                    abandoned = _abandon_if_dead(call_id, lane, job)
                    if abandoned is not None:
                        return abandoned
            elif state in {"submitted", "starting", "running"}:
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
            elif state == "refused":
                # The Durable Object's own record that this launch's startup
                # failed before /run (no container instance, rollout skew).
                # Its handler wrote it, so this request is no longer in
                # flight: the launch is proven unaccepted.
                refusal = _refusal_for_launch(status, launch_id)
                if refusal is not None:
                    raise _CloudflareRefused(refusal)
                last = RemoteExecutorError(
                    f"Cloudflare call {call_id} is not observable yet")
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
                        _reconcile_completed_cloudflare_call(call_id, lane)
                        return {"result": current.get("result"),
                                "job_completed": True}
                    if current and current.get("state") == "failed":
                        return {"error": current.get("error") or
                                "Cloudflare executor failed",
                                "retryable": False}
                finally:
                    probe.reset()
        except _CloudflareRefused:
            raise
        except Exception as exc:
            last = exc
        time.sleep(2)
    # The provider lease starts just before this local deadline. One final
    # status read lets an expired execution become a terminal envelope and
    # preserves the actual provider error instead of reporting ``: None``.
    try:
        status = _cloudflare_status(call_id, lane, timeout=10)
        state = status.get("status")
        refusal = _refusal_for_launch(status, launch_id)
        if refusal is not None:
            raise _CloudflareRefused(refusal)
        if state in {"done", "failed"}:
            envelope = status.get("envelope")
            if isinstance(envelope, dict):
                return envelope
            last = RemoteExecutorError(
                f"Cloudflare call {call_id} ended without an envelope")
        elif status.get("error"):
            last = RemoteExecutorError(
                f"Cloudflare call {call_id} remained {state}: "
                f"{status.get('error')}")
        elif state:
            last = RemoteExecutorError(
                f"Cloudflare call {call_id} remained {state} through its "
                "executor deadline")
    except _CloudflareRefused:
        raise
    except Exception as exc:
        last = exc
    raise RemoteExecutorError(
        f"Cloudflare call {call_id} could not be recovered: {last}") from last


def _refused_before_run(job, call_id, lane, status_code, body):
    """Close one provably unaccepted launch and raise its exact kind.

    Reached from the launch response itself, or from a reconnect that found
    the Durable Object's own refusal tombstone. Either way no /run was sent.
    """
    queue_backed = job.get("id") is not None
    if queue_backed:
        ledger = dbx.Db()
        try:
            ledger.run(dbx.finish_remote_execution, job["id"],
                       job.get("total_claims"), "cancelled",
                       body.get("error") or "Cloudflare launch refused",
                       "cloudflare", call_id)
        except Exception:
            pass
        finally:
            ledger.reset()
        dbx.unmark_remote_owned(job["id"])
    launch_error = str(body.get("error") or
                       "Cloudflare launch refused before /run")
    if status_code == 429 and "shard is busy" in launch_error.lower():
        owner = body.get("active_call_id")
        # Free the shard for this claim's next admission attempt
        # when its owner already succeeded or provably died.
        if not _reconcile_completed_cloudflare_call(owner, lane) \
                and queue_backed:
            _release_dead_cloudflare_owner(owner, lane)
        raise CloudflareCapacityBusy(launch_error)
    if body.get("capacity_unavailable") is True \
            or _capacity_refusal_text(launch_error):
        # The Containers runtime searched for an instance and found none.
        # Nothing was accepted; the caller spreads, fails over, or reports.
        raise CloudflareCapacityUnavailable(launch_error)
    if status_code == 503 and launch_error.startswith((
            "container readiness mismatch ",
            "container readiness failed:",
            "Cloudflare container image is not ready",
            "Error: Internal error hitting the containers service",
            "Error: Container sidecar is shutting down")):
        # A retiring sidecar can reject startAndWaitForPorts.
        # Only this 503 + no-acceptance proof is safe to wait on;
        # an ambiguous /run failure must reconnect instead.
        raise CloudflareRolloutPending(launch_error)
    raise CloudflareLaunchUnavailable(launch_error)


def _run_cloudflare(job):
    queue_backed = job.get("id") is not None
    if (queue_backed and job.get("total_claims") is None) or (
            not queue_backed and str(job.get("type") or "") not in
            config.CLOUDFLARE_SYNCHRONOUS_TYPES):
        raise CloudflareLaunchUnavailable(
            "Cloudflare does not accept this unfenced synchronous call")
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
    lane = _cloudflare_lane_for(job)
    timeout_s = config.cloudflare_timeout_for(job.get("type")) + 60
    if queue_backed and dbx.mark_remote_owned(job["id"]) is False:
        raise CloudflareLaunchUnavailable(
            "dispatcher shutdown began before Cloudflare submission")
    if queue_backed:
        ledger = dbx.Db()
        try:
            _record_remote_execution_with_retry(
                ledger, job, "cloudflare", call_id, lane, timeout_s)
        except Exception as exc:
            print(f"[dispatcher] Cloudflare call {call_id} ledger write "
                  f"failed ({str(exc)[:160]}); deterministic call id still "
                  "prevents a second Container instance", flush=True)
        finally:
            ledger.reset()

    deadline = time.monotonic() + timeout_s
    # Names this one launch request, so a reconnect can tell its own refusal
    # from an older refusal of the same call id.
    launch_id = uuid.uuid4().hex
    job["_cloudflare_launch_id"] = launch_id

    def recover():
        try:
            return _recover_cloudflare_result(call_id, lane, job, deadline)
        except _CloudflareRefused as refused:
            # The reconnect found this call's own refusal: its startup
            # failed before /run, exactly like an immediate 503.
            _refused_before_run(job, call_id, lane, 503, refused.body)

    try:
        response = requests.post(
            f"{config.CLOUDFLARE_EXECUTOR_URL}/calls/{lane}/{call_id}",
            json={"job": _job_payload(job), "timeout_s": timeout_s,
                  "launch_id": launch_id},
            headers=_cloudflare_headers(),
            # The named call remains recoverable after this observation
            # request ends. Reconnect early enough to detect a rollout-
            # abandoned `starting` state instead of waiting a full agent/MCP
            # execution envelope before asking the Durable Object again.
            timeout=max(1, min(config.CLOUDFLARE_START_OBSERVATION_S,
                               deadline - time.monotonic())))
    except requests.RequestException:
        if queue_backed:
            dbx.remote_launch_recorded(job["id"])
        data = recover()
    else:
        if queue_backed:
            dbx.remote_launch_recorded(job["id"])
        if response.status_code == 404:
            if queue_backed:
                ledger = dbx.Db()
                try:
                    ledger.run(dbx.finish_remote_execution, job["id"],
                               job.get("total_claims"), "cancelled",
                               "Cloudflare route was not deployed",
                               "cloudflare", call_id)
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
            if not isinstance(response_body, dict):
                response_body = {}
            if response_body.get("safe_to_fallback"):
                _refused_before_run(job, call_id, lane,
                                    response.status_code, response_body)
            # The Worker may have lost its side of an already-running
            # container request. Reconnect to the deterministic call before
            # considering any physical retry.
            return _interpret_cloudflare_terminal(recover(), job)
        try:
            data = response.json()
        except ValueError:
            # A proxy can replace a successful response with a non-JSON
            # body after /run was accepted. Recover the same named call;
            # never fail the edit or replay a mutation from this alone.
            data = recover()
    return _interpret_cloudflare_terminal(data, job)


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


def _deploy_in_progress(job, cause):
    """The explicit retryable outcome after a bounded rollout wait."""
    kind = str(job.get("type") or "job")
    error = CloudflareRolloutPending(
        f"Deploy in progress: Valmera's executor is switching to a new "
        f"release, so this {kind} was not started and nothing changed. "
        f"It is safe to retry shortly. ({cause})")
    if job.get("id") is not None:
        # Same bound as any transient failure: media get one automatic
        # retry; an MCP tool or Studio turn hands the retry to its caller.
        base = {"agent_turn": config.MAX_ATTEMPTS_AGENT,
                "mcp_tool": config.MAX_ATTEMPTS_MCP}.get(
                    kind, config.MAX_ATTEMPTS_MEDIA)
        error.max_attempts = max(1, min(int(base), 2))
    return error


def _capacity_route(job, retry):
    """(lane, admission slot) for the retry-th launch after a refusal.

    Retry 1 spreads to another Durable Object of the same lane (its project
    or call-id shard is pinned). Retry 2 fails over to the next suitable lane,
    whose container class has its own instance pool. Later retries alternate
    between them, cycling identities, until the bounded budget ends.
    """
    home = _cloudflare_lane(job.get("type"))
    lanes = (home,) + tuple(_cloudflare_failover_lanes(job))
    if retry == 1 or len(lanes) == 1:
        return home, retry % 3
    if retry % 2 == 0:
        other = lanes[1 + ((retry // 2 - 1) % (len(lanes) - 1))]
        return other, ((retry // 2 - 1) // (len(lanes) - 1)) % 3
    return home, (retry // 2 + 1) % 3


def _capacity_delay(retry):
    """Jittered exponential backoff; the first spread is near-immediate.

    Each refusal already includes the runtime's own ~30-s instance search, so
    a fresh identity is tried at once, then 2, 4, 8, 16, 20 s (+/-50%).
    """
    if retry <= 1:
        return random.uniform(0.25, 1.0)
    return min(20.0, 2.0 * (2 ** (retry - 2))) * random.uniform(0.5, 1.5)


def _capacity_exhausted(job, state):
    """The honest final outcome once every capacity route was refused."""
    kind = str(job.get("type") or "job")
    lanes = []
    for lane, _slot in state["routes"]:
        if lane not in lanes:
            lanes.append(lane)
    elapsed = time.monotonic() - state["started"]
    cause = _short_reason_detail(state["last"], 160)
    error = CloudflareCapacityUnavailable(
        f"Cloudflare could not provide a container for this {kind}: every "
        f"launch was refused before anything ran ({state['attempts']} "
        f"attempt(s) on the {' and '.join(lanes)} "
        f"lane{'s' if len(lanes) > 1 else ''} over {elapsed:.0f}s; last: "
        f"{cause}). Nothing ran and nothing changed; it is safe to retry in "
        "a minute.")
    if job.get("id") is not None:
        # Same bound as a deploy-in-progress refusal: media get one
        # automatic retry; MCP tools and Studio turns hand it to the caller.
        base = {"agent_turn": config.MAX_ATTEMPTS_AGENT,
                "mcp_tool": config.MAX_ATTEMPTS_MCP}.get(
                    kind, config.MAX_ATTEMPTS_MEDIA)
        error.max_attempts = max(1, min(int(base), 2))
    return error


def _retry_after_capacity_refusal(job, exc, state, queued, window_end):
    """Prepare the next fenced launch route, or raise the final outcome.

    Every refusal handled here is proven pre-/run: the Durable Object kept
    no accepted call and the ledger row is closed, so a new identity can
    never double-execute. Queue-backed waits re-check the lease.
    """
    now = time.monotonic()
    if state is None:
        state = {"started": now, "attempts": 0,
                 "routes": [(_cloudflare_lane_for(job),
                             job.get("_cloudflare_admission_slot", 0))],
                 "deadline": window_end}
    state["attempts"] += 1
    state["last"] = exc
    retry = state["attempts"]
    refused = (_cloudflare_lane_for(job),
               job.get("_cloudflare_admission_slot", 0))
    # A busy shard may already have moved this claim to an alternate slot;
    # never spend a retry on the identity that was just refused.
    state["route"] = state.get("route", 0) + 1
    lane, slot = _capacity_route(job, state["route"])
    if (lane, slot) == refused:
        state["route"] += 1
        lane, slot = _capacity_route(job, state["route"])
    delay = _capacity_delay(retry)
    if retry > config.CLOUDFLARE_CAPACITY_RETRIES \
            or now + delay >= state["deadline"]:
        raise _capacity_exhausted(job, state) from exc
    print(f"[dispatcher] Cloudflare could not provide a container for "
          f"{job.get('type')} {job.get('id')} on "
          f"{_cloudflare_lane_for(job)} ({_short_reason_detail(exc, 120)}); "
          f"nothing ran, retrying on {lane} slot {slot} in {delay:.1f}s",
          flush=True)
    time.sleep(delay)
    if queued:
        probe = dbx.Db()
        try:
            if not probe.run(dbx.lease_is_current, job["id"],
                             job.get("total_claims")):
                raise dbx.JobLeaseLost(
                    "job lease changed during capacity retry")
        finally:
            probe.reset()
        job["_cloudflare_capacity_spread"] = True
    else:
        # An id-less child's identity is its nonce; a fresh one is a new,
        # never-accepted call on (usually) another shard.
        job.pop("_cloudflare_sync_nonce", None)
    if lane == _cloudflare_lane(job.get("type")):
        job.pop("_cloudflare_lane", None)
    else:
        job["_cloudflare_lane"] = lane
    job["_cloudflare_admission_slot"] = slot
    state["routes"].append((lane, slot))
    return state


def _run_cloudflare_with_capacity_wait(job):
    """Wait only after a provider proves this fenced call was not accepted.

    Ambiguous launches still reconnect in _run_cloudflare. Never repeat them
    and never wait after a terminal compute failure. A paid synchronous
    caller does not wait on a busy shard; it retries a few times on fresh,
    never-accepted identities (other shards) with short jittered backoff.
    When Cloudflare cannot provide a container at all, the same claim moves
    across Durable Objects and suitable lanes (_retry_after_capacity_refusal)
    within a bounded window. Dispatcher heartbeats continue during admission.
    """
    queued = job.get("id") is not None
    wait_s = config.CLOUDFLARE_BUSY_WAIT_S if queued else 0
    rollout_wait_s = config.CLOUDFLARE_ROLLOUT_WAIT_S if queued else 0
    modal_alternate = bool(
        config.CLOUDFLARE_MODAL_FALLBACK and config.MODAL_EXECUTOR_ENABLED
        and job.get("type") in config.MODAL_EXECUTOR_TYPES)
    if modal_alternate:
        wait_s = 0  # A configured alternate can serve it immediately.
        rollout_wait_s = 0
    started = time.monotonic()
    deadline = started + wait_s
    rollout_deadline = started + rollout_wait_s
    delay = 2.0
    sync_busy_retries = 0
    capacity = None
    # No new launch starts after this point. It is measured from the first
    # launch and never exceeds the job's own provider budget; an id-less
    # child also stays inside the margin its parent MCP lease reserves above
    # the child's lease, so a late successful route cannot outlive the
    # parent that waits for it.
    capacity_window = min(config.CLOUDFLARE_CAPACITY_WAIT_S,
                          config.cloudflare_timeout_for(job.get("type")))
    if not queued:
        capacity_window = min(capacity_window,
                              config.CLOUDFLARE_MCP_CHILD_MARGIN_S - 60)
    capacity_end = started + max(0.0, capacity_window)
    while True:
        try:
            return _run_cloudflare(job)
        except CloudflareCapacityUnavailable as exc:
            capacity = _retry_after_capacity_refusal(
                job, exc, capacity, queued, capacity_end)
            continue
        except CloudflareTerminalFailure as exc:
            # A startup Cloudflare abandoned before /run is the same "no
            # container could be started" outcome, already terminal under
            # this identity; the next route uses a new one.
            if getattr(exc, "failure_kind", None) != \
                    "provider_start_abandoned":
                raise
            if queued:
                dbx.unmark_remote_owned(job["id"])
            capacity = _retry_after_capacity_refusal(
                job, exc, capacity, queued, capacity_end)
            continue
        except (CloudflareCapacityBusy, CloudflareRolloutPending) as exc:
            rollout = isinstance(exc, CloudflareRolloutPending)
            # Only an explicit pre-launch busy refusal permits a different
            # slot. Accepted/ambiguous calls keep their identity and lease.
            # Preserve project affinity on the first try, but don't make one
            # occupied shard strand a short preview while the pool is idle.
            slot = job.get('_cloudflare_admission_slot', 0)
            if not rollout and slot < 2 and _cloudflare_alternate_safe(job):
                job['_cloudflare_admission_slot'] = slot + 1
                continue
            if not rollout and not queued and not modal_alternate \
                    and sync_busy_retries < config.CLOUDFLARE_SYNC_BUSY_RETRIES:
                # The 429 stored nothing under the refused id, so it was never
                # accepted. A new nonce is a new identity on another shard.
                sync_busy_retries += 1
                job.pop("_cloudflare_sync_nonce", None)
                time.sleep(random.uniform(0.5, 1.5)
                           * (2 ** (sync_busy_retries - 1)))
                continue
            remaining = (rollout_deadline if rollout else deadline) - time.monotonic()
            if remaining <= 0:
                if rollout:
                    raise _deploy_in_progress(job, exc) from exc
                raise
            if rollout:
                # Give staged containers time to retire, rather than waking
                # the old image continuously during its rollout grace.
                delay = max(delay, 15.0)
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 60.0 if rollout else 20.0)
            probe = dbx.Db()
            try:
                if not probe.run(dbx.lease_is_current, job["id"],
                                 job.get("total_claims")):
                    raise dbx.JobLeaseLost("job lease changed during admission wait")
            finally:
                probe.reset()


def _run_remote(job, url_override=None, modal_function=None):
    provider = desired_execution_provider(job) if url_override is None \
        else "cloud_run"
    if provider == "cloudflare":
        try:
            return _run_cloudflare_with_capacity_wait(job)
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
        except CloudflareTerminalFailure as exc:
            # Capacity/budget failures are explicitly about this provider and
            # can be solved by a differently sized/billed alternate even
            # though replaying the same provider is non-retryable. Every other
            # kind must also carry the executor's retryable=true decision.
            # Without that second gate, an MCP precondition such as "wait for
            # indexing" was classified unknown/non-retryable, then pointlessly
            # replayed on Modal after Cloudflare had already given the correct
            # deterministic answer.
            # Never "provider_capacity_unavailable": this job's own capacity
            # refusal is a CloudflareLaunchUnavailable (handled above), so a
            # terminal envelope of that kind is always a NESTED child's
            # refusal reported by a parent that already ran (an MCP tool or
            # Studio turn, possibly after it changed the project). Replaying
            # that parent elsewhere would run it twice.
            provider_switch_kinds = {
                "executor_capacity", "provider_budget_exhausted",
                "provider_start_abandoned", "executor_memory",
            }
            retryable_fallback_kinds = {
                "transient_infrastructure", "stalled_io", "media_command",
                "unknown",
            }
            kind = str(getattr(exc, "failure_kind", "unknown"))
            fallback_allowed = kind in provider_switch_kinds or (
                bool(getattr(exc, "retryable", True))
                and kind in retryable_fallback_kinds)
            if not (config.CLOUDFLARE_MODAL_FALLBACK
                    and config.MODAL_EXECUTOR_ENABLED
                    and str(job.get("type") or "") in
                    config.MODAL_EXECUTOR_TYPES
                    and fallback_allowed):
                raise
            # An id-less synchronous call has no queue lease to replace. The
            # named Durable Object is already terminal, which is the complete
            # proof needed before its one bounded Modal fallback.
            if job.get("id") is None:
                print(f"[dispatcher] synchronous Cloudflare call ended with "
                      f"{kind}; retrying once on Modal", flush=True)
                return _run_modal(job, modal_function)
            # The Durable Object has a terminal envelope. Confirm the queue
            # lease is still ours before replacing its terminal provider
            # ledger with a Modal call on the same immutable claim.
            probe = dbx.Db()
            try:
                current = probe.run(dbx.get_job, job.get("id"))
                if not current or current.get("state") != "running" or \
                        current.get("total_claims") != job.get("total_claims"):
                    raise exc

                # The executor normally closes this exact ledger identity
                # before returning its terminal envelope. Do it again from
                # the dispatcher and verify the terminal fence before Modal
                # is allowed to reserve the same queue claim. This covers a
                # database restart during the executor's best-effort close;
                # without it Modal could be accepted but then correctly
                # rejected by the still-active Cloudflare ownership row.
                call_id = _cloudflare_call_id(job)
                closed = probe.run(
                    dbx.finish_remote_execution, job["id"],
                    job.get("total_claims"), "failed", exc,
                    "cloudflare", call_id)
                existing = probe.run(dbx.get_remote_execution, job["id"])
                if not closed and existing is None:
                    # The original pre-launch ledger write can itself have
                    # coincided with a database recovery. The named Durable
                    # Object is now proven terminal, so materialize and close
                    # that exact identity; no second compute is authorized by
                    # this bookkeeping repair.
                    recorded = _record_remote_execution_with_retry(
                        probe, job, "cloudflare", call_id,
                        _cloudflare_lane_for(job),
                        config.cloudflare_timeout_for(job.get("type")) + 60)
                    if recorded:
                        closed = probe.run(
                            dbx.finish_remote_execution, job["id"],
                            job.get("total_claims"), "failed", exc,
                            "cloudflare", call_id)
                        existing = probe.run(
                            dbx.get_remote_execution, job["id"])
                terminal_identity = bool(existing) and \
                    existing.get("total_claims") == job.get("total_claims") \
                    and existing.get("provider") == "cloudflare" \
                    and str(existing.get("call_id")) == call_id \
                    and existing.get("state") in {"failed", "cancelled"}
                if not (closed or terminal_identity):
                    raise exc
            finally:
                probe.reset()
            print(f"[dispatcher] Cloudflare call ended with {kind}; "
                  "retrying the same fenced lease once on Modal", flush=True)
            return _run_modal(job, modal_function)
    if provider == "local":
        raise RemoteExecutorError(
            f"no remote executor is configured for {job.get('type')}")
    if provider == "modal":
        try:
            return _run_modal(job, modal_function)
        except ModalLaunchUnavailable as exc:
            # Submission was rejected before a Modal call id existed. A job
            # stamped before the Cloudflare cutover can therefore move safely
            # to Cloudflare under the same queue lease. Once either provider
            # has an active ledger row this replacement fails closed.
            if job.get("id") is not None \
                    and config.CLOUDFLARE_EXECUTOR_ENABLED \
                    and config.CLOUDFLARE_EXECUTOR_URL:
                probe = dbx.Db()
                try:
                    shape = (job.get("_execution_shape") or
                             (job.get("payload") or {}).get(
                                 "execution_shape") or {})
                    if not shape and str(job.get("type") or "") not in {
                            "agent_turn", "mcp_tool", "shorts_plan"}:
                        shape = dbx._json_safe(probe.run(
                            dbx.project_execution_shape,
                            job.get("project_id"),
                            (job.get("payload") or {}).get("asset_id")) or {})
                        job["_execution_shape"] = shape
                    replaced = None
                    if _cloudflare_selected(job):
                        replaced = probe.run(
                            dbx.replace_execution_provider_before_launch,
                            job["id"], job.get("total_claims"), "modal",
                            "cloudflare", shape)
                finally:
                    probe.reset()
                if replaced == "cloudflare":
                    payload = dict(job.get("payload") or {})
                    payload["execution_provider"] = "cloudflare"
                    job["payload"] = payload
                    print(f"[dispatcher] {exc}; moving the unlaunched "
                          "Modal-stamped lease to Cloudflare", flush=True)
                    return _run_cloudflare(job)
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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "capture" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 {"fetch", "search"}.issubset(
                     config.CLOUDFLARE_SYNCHRONOUS_TYPES)))


def fetch_bytes_available():
    """Whether any configured alternate has not failed a real-byte probe.

    Unknown is allowed during rollout; a diagnostic outage never disables a
    capability.  Once every configured provider has an explicit failed byte
    verdict, however, making the user wait through the same known wall is not
    resilience. A successful post-proxy probe turns this back on by itself.
    """
    import ytaccess

    states = []
    if config.CLOUDFLARE_EXECUTOR_ENABLED \
            and config.CLOUDFLARE_EXECUTOR_URL:
        states.append(ytaccess.provider_youtube_ok("cloudflare"))
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

    Cloudflare, Cloud Run and the optional Modal rollback use the same
    stateless runner but leave the internet through different networks. An
    explicit YouTube access wall advances to the next configured provider; a
    content verdict such as private/removed does not.
    Transport failure also earns the other provider, because no successful
    response means the caller has no usable storage key (a possible orphan is
    reclaimed with ordinary scratch/fetched-object lifecycle cleanup).
    """
    providers = []
    cloudflare_primary = _cloudflare_selected(job)
    if cloudflare_primary:
        # Same admission policy as every other id-less child: a provably
        # unaccepted busy shard retries on a fresh identity before failing.
        providers.append(("cloudflare",
                          lambda: _run_cloudflare_with_capacity_wait(job)))
    if config.MODAL_EXECUTOR_ENABLED and (
            not cloudflare_primary or config.CLOUDFLARE_MODAL_FALLBACK):
        providers.append(("modal", lambda: _run_modal(
            job, function_override="egress")))
    # Keep the old endpoint only as an explicitly enabled rollback; never pay
    # its latency before the selected provider.
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


def stock_acquire_available():
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "stock_acquire" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


def run_stock_acquire_remote(project_id, payload, user_id=None):
    """Acquire/probe/review stock bytes in the idempotent egress lane."""
    return _run_across_media_egress({
        "id": None, "type": "stock_acquire", "project_id": project_id,
        "user_id": user_id, "attempts": 0, "payload": payload})


def frames_available():
    """Is there an executor to decode a stored original on? (round 62)

    Same contract as capture_available: purely "is the executor configured".
    With no executor there is only one box, and the local decode is what
    shipped before — correct for that deployment, fatal only beside a
    dispatcher whose job is to stay light.
    """
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "frames" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "track" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "matte" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "smatch" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    return bool(config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "clean" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


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
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED \
            and not (config.CLOUDFLARE_EXECUTOR_ENABLED and
                     config.CLOUDFLARE_EXECUTOR_URL):
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
    if not config.REMOTE_EXECUTOR_URL and not config.MODAL_EXECUTOR_ENABLED \
            and not (config.CLOUDFLARE_EXECUTOR_ENABLED and
                     config.CLOUDFLARE_EXECUTOR_URL):
        import stems
        return stems.run_stems_job(None, job)
    return _run_remote(job)


def run_render_remote(worker_db, job):      # signature matches run_render_job
    provider = desired_execution_provider(job)
    if job.get("type") == "final" and provider not in {"cloudflare",
                                                        "modal"}:
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
    return bool(config.MODAL_EXECUTOR_ENABLED or config.REMOTE_EXECUTOR_URL or
                (config.CLOUDFLARE_EXECUTOR_ENABLED and
                 config.CLOUDFLARE_EXECUTOR_URL and
                 "mcp_media" in config.CLOUDFLARE_SYNCHRONOUS_TYPES))


def run_mcp_media_remote(project_id, payload, user_id=None):
    """Run only the resolved MCP video encode on remote media compute.

    The dispatcher resolves/waits for a timeline preview first, then sends a
    stateless id-less call. Cloudflare is primary; the optional legacy Modal
    adapter remains usable only when Cloudflare is not selected.
    """
    if (payload or {}).get("tool") != "__media__":
        raise ValueError("MCP media offload accepts __media__ only")
    if config.WORKER_ROLE in {"executor", "batch_executor"}:
        # A media executor is the encode's destination. Sending it on again
        # recursed through every interactive shard (Aug 30-Oct 10 2026)
        # until Cloudflare refused a container; mcp_media.prepare encodes
        # locally there, so reaching this line is a bug, never a fallback.
        raise RemoteExecutorError(
            "a media executor must encode MCP media itself, not re-dispatch it")
    job = {"id": None, "type": "mcp_media", "project_id": project_id,
           "user_id": user_id, "attempts": 0, "payload": payload}
    if _cloudflare_selected(job):
        try:
            return _run_remote(job)
        except CloudflareLaunchUnavailable:
            # Nothing ran. The fenced fallback runs the same resolved encode
            # as its legacy mcp_tool shape, only where operators allow it.
            if not (config.CLOUDFLARE_MODAL_FALLBACK
                    and config.MODAL_EXECUTOR_ENABLED
                    and "mcp_tool" in config.MODAL_EXECUTOR_TYPES):
                raise
            print("[dispatcher] Cloudflare could not start the MCP media "
                  "encode; running it once on Modal", flush=True)
            return _run_modal(dict(job, type="mcp_tool"),
                              function_override="preview")
    if config.MODAL_EXECUTOR_ENABLED:
        legacy = dict(job, type="mcp_tool")
        return _run_modal(legacy, function_override="preview")
    if config.REMOTE_EXECUTOR_URL:
        return _run_cloud(dict(job, type="mcp_tool"))
    raise RemoteExecutorError("no MCP media executor is configured")


def run_index_remote(worker_db, job):       # signature matches run_index_job
    provider = desired_execution_provider(job)
    if provider not in {"cloudflare", "modal"}:
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
        # An encode the kernel OOM-killed (executor_memory) is the same
        # verdict as a source that needs more room: the identical graph fits
        # only on the larger lane.
        is_capacity = getattr(error, "failure_kind", "") in (
            "executor_capacity", "executor_memory")
        definitely_missing = isinstance(error, RemoteServiceUnavailable)
        provider = desired_execution_provider(job)
        modal_capacity = (config.MODAL_EXECUTOR_ENABLED and is_capacity and (
            provider == "modal" or (
                provider == "cloudflare"
                and config.CLOUDFLARE_MODAL_FALLBACK)))
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
    # The immutable execution_provider stamp owns this decision.  Calling
    # Modal directly here made the queue log say Cloudflare while every MCP
    # turn still ran on Modal and silently consumed fallback budget.
    return _run_remote(job)


def run_shorts_remote(worker_db, job):      # signature matches shorts runner
    """Run story planning independently from Studio and MCP capacity."""
    return _run_remote(job)


def run_probe_remote(payload=None):
    """Launch the real-byte egress probe without using dispatcher network."""
    job = {"id": None, "type": "ytprobe", "project_id": None,
           "user_id": None, "attempts": 0, "payload": payload or {}}
    if _cloudflare_selected(job):
        return _run_remote(job)
    if config.MODAL_EXECUTOR_ENABLED:
        return _run_modal(job, function_override="probe")
    if config.REMOTE_EXECUTOR_URL:
        return _run_cloud(job)
    raise RemoteExecutorError("no remote egress probe is configured")
