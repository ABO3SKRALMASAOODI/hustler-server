"""wait(handle, max_s<=100): settles any handle a tool returned
(HARNESS_V2.md 2.7). The finished envelope, or plain progress."""
import time

import agent_tools
import db as dbx

from harness2 import envelope
from harness2 import render_tool

MAX_WAIT_S = 100.0


def wait(ctx, handle, max_s=60):
    try:
        max_s = max(0.0, min(MAX_WAIT_S, float(max_s or 0)))
    except (TypeError, ValueError):
        max_s = 60.0
    job_id = render_tool.job_of(handle)
    if job_id is None:
        return envelope.failed("wait", f"{handle!r} is not a handle",
                               "a handle a tool returned, like 'job:4512'")
    job = ctx.db.run(dbx.get_job, job_id)
    if not job:
        return envelope.failed("wait", f"there is no job {job_id}",
                               "timeline() shows the current state")
    if job.get("type") == "preview":
        version = (job.get("payload") or {}).get("edl_version")
        return render_tool.settle(ctx, job_id, version, max_s)
    deadline = time.monotonic() + max_s
    while job.get("state") not in ("done", "failed") \
            and time.monotonic() < deadline:
        agent_tools._touch_job_heartbeat(ctx)
        time.sleep(min(3.0, max(0.2, deadline - time.monotonic())))
        job = ctx.db.run(dbx.get_job, job_id) or job
    env = envelope.Envelope(tool="wait")
    state = job.get("state")
    if state == "done":
        env.body = (f"job {job_id} ({job.get('type')}) finished: "
                    + str(job.get("result") or "")[:1500])
        env.applied = 1
    elif state == "failed":
        env.failed = True
        env.skip(0, f"job {job_id} ({job.get('type')}) failed: "
                    f"{str(job.get('error') or '')[:300]}",
                 "start it again; nothing that was saved is lost")
    else:
        progress = job.get("progress")
        env.running = {"handle": f"job:{job_id}", "what": job.get("type"),
                       "progress": (f"{progress}%" if progress is not None
                                    else state)}
    return env
