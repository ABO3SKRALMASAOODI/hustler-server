"""Dispatcher -> executor client (round 38).

When WORKER_ROLE=worker AND REMOTE_EXECUTOR_URL is set, the dispatcher ships
index/preview/final jobs to a stateless Cloud Run executor instead of encoding
them on its own (cheap, network-bound) box. These wrappers have the SAME
`(worker_db, job)` signature as the local runners in indexer/renderer, so
main.RUNNERS can swap one for the other with nothing else changing — the
dispatcher keeps claiming, heart-beating, retrying, reaping and credit-charging
exactly as before. The heavy CPU happens on the executor; this call just waits
on the HTTP response (I/O wait, so a dispatcher thread costs almost nothing).

The executor reads the job's real state from the shared Postgres, so the body
we POST is only what the runner needs to identify the work — never asset bytes.
"""

import threading

import requests

import config
import version


class RemoteExecutorError(RuntimeError):
    pass


# The last version skew observed against the executor, or "" when the two
# services agree (or when we have not been able to ask). Written by
# check_executor_version, read by _run_remote so that a job which fails on a
# stale executor SAYS SO in the error the admin job list shows. Round 55's
# customer got "the render is the wrong length" three times; so did we, and it
# was the only thing either of us had.
_skew_note = ""
_skew_lock = threading.Lock()


def executor_health(timeout=20):
    """GET /health on the executor. Returns the parsed body, or raises."""
    if not config.REMOTE_EXECUTOR_URL:
        raise RemoteExecutorError("REMOTE_EXECUTOR_URL is not set")
    resp = requests.get(f"{config.REMOTE_EXECUTOR_URL}/health", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


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


def _job_payload(job):
    """A JSON-safe subset of the claimed job row. The runners read only these
    fields; created_at (a datetime) is used by the dispatcher's process_one for
    queue-wait timing, not by the runner, so it is deliberately omitted."""
    return {
        "id": job["id"],
        "type": job["type"],
        "project_id": job["project_id"],
        "user_id": job.get("user_id"),
        "attempts": job.get("attempts"),
        "payload": job.get("payload") or {},
    }


def _run_remote(job):
    if not config.REMOTE_EXECUTOR_URL:
        raise RemoteExecutorError("REMOTE_EXECUTOR_URL is not set")
    url = f"{config.REMOTE_EXECUTOR_URL}/run"
    headers = {"Content-Type": "application/json"}
    if config.REMOTE_EXECUTOR_SECRET:
        headers["Authorization"] = f"Bearer {config.REMOTE_EXECUTOR_SECRET}"
    try:
        # Per-kind: a preview is a user staring at a spinner, a final is an
        # hour-long export nobody wants refused at minute 25. One number for
        # both was sized for the short one.
        resp = requests.post(url, json={"job": _job_payload(job)},
                             headers=headers,
                             timeout=config.executor_timeout_for(
                                 job.get("type")))
    except requests.RequestException as e:
        # A transport failure (timeout, connection reset, cold-start slowness)
        # raises so process_one requeues within the media attempt budget — a
        # re-run is safe (renders are deterministic and cache-deduped).
        raise RemoteExecutorError(f"executor call failed: {e}") from e
    if resp.status_code != 200:
        body = (resp.text or "")[:500]
        raise RemoteExecutorError(
            f"executor returned {resp.status_code}: {body}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RemoteExecutorError(
            f"executor returned non-JSON: {(resp.text or '')[:300]}") from e
    if data.get("error"):
        # The runner itself raised on the executor (e.g. "EDL version not
        # found"). Surface it as an error so the dispatcher's normal failure
        # path — requeue then reaper note — runs, identical to a local raise.
        #
        # And ASK WHOSE CODE FAILED, right here, while the instance that just
        # ran the job is still warm. A stale executor produces errors that read
        # as ordinary defects — "the render is the wrong length", "EDL shape
        # invalid: kind should be one of ..." — and are nothing of the kind:
        # they are this dispatcher handing work to a build that predates the
        # fix. Both times that happened the missing sentence was this one, and
        # both times it cost a day to reconstruct from the database. It is one
        # cheap GET on a failure path, so it never touches a healthy render.
        msg = str(data["error"])[:500]
        skew = check_executor_version(quiet=True)
        if skew:
            msg = f"{msg} [{skew}]"
        raise RemoteExecutorError(msg)
    return data.get("result")


def capture_available():
    """Is there an executor to run web captures on? (round 61)

    Chromium is baked into the same image the executor runs, so this is purely
    "is the executor configured". When it is not, the caller falls back to
    recording locally — which is what shipped before and is correct on a
    single-box deployment; it is only the LARGE dispatcher-plus-executor
    deployment where the browser has to move.
    """
    return bool(config.REMOTE_EXECUTOR_URL)


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


def frames_available():
    """Is there an executor to decode a stored original on? (round 62)

    Same contract as capture_available: purely "is the executor configured".
    With no executor there is only one box, and the local decode is what
    shipped before — correct for that deployment, fatal only beside a
    dispatcher whose job is to stay light.
    """
    return bool(config.REMOTE_EXECUTOR_URL)


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
    return bool(config.REMOTE_EXECUTOR_URL)


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
    return bool(config.REMOTE_EXECUTOR_URL)


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
    return bool(config.REMOTE_EXECUTOR_URL)


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
    return bool(config.REMOTE_EXECUTOR_URL)


def run_clean_remote(project_id, payload, user_id=None):
    """Repaint erase regions (and/or run the cursor pass) on the executor.
    Returns inpaint.run_clean_job's dict — clean_video stats plus the
    before/after ink measurements and object sizes; on success both cleaned
    objects are already uploaded. Synchronous, no job row."""
    return _run_remote({"id": None, "type": "clean",
                        "project_id": project_id, "user_id": user_id,
                        "attempts": 0, "payload": payload})


def run_render_remote(worker_db, job):      # signature matches run_render_job
    return _run_remote(job)


def run_index_remote(worker_db, job):       # signature matches run_index_job
    return _run_remote(job)
