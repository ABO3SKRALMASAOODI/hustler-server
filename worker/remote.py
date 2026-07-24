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

import requests

import config


class RemoteExecutorError(RuntimeError):
    pass


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
        resp = requests.post(url, json={"job": _job_payload(job)},
                             headers=headers,
                             timeout=config.REMOTE_EXECUTOR_TIMEOUT_S)
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
        raise RemoteExecutorError(str(data["error"])[:500])
    return data.get("result")


def run_render_remote(worker_db, job):      # signature matches run_render_job
    return _run_remote(job)


def run_index_remote(worker_db, job):       # signature matches run_index_job
    return _run_remote(job)
