"""Stateless request-based executor (round 38; agent isolation round 104).

Runs with WORKER_ROLE=executor for compute or WORKER_ROLE=agent_executor for
isolated agent turns. Deploy either as a scale-to-zero Cloud Run service:

    gcloud run deploy valmera-executor \
        --source worker/ --region us-central1 \
        --cpu 8 --memory 16Gi --concurrency 1 \
        --min-instances 0 --max-instances 5 --timeout 3600 \
        --set-env-vars WORKER_ROLE=executor,REMOTE_EXECUTOR_SECRET=...,DATABASE_URL=...,<S3+model envs>

Each HTTP request runs ONE job to completion on its own instance, so there is
no INDEX_SLOTS=1 serialization and nothing is billed while idle. It writes
progress + results to the same Postgres the dispatcher polls, so the studio's
job-status UI needs no change. It uses only the Python stdlib for HTTP — no new
image dependency — because Cloud Run just needs a process listening on $PORT.

Routes:
    GET  /health  -> 200 "ok"        (Cloud Run startup/health probe)
    POST /run     -> {"result": ...} | {"error": "..."}   (Bearer-authed)
"""

import hmac
import json
import os
import shutil
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import compute_cost
import db as dbx
import frameserve
import failure_policy
import indexer
import inpaint
import job_completion
import matte
import renderer
import screenmatch
import stems
import tracker
import version
import webrecord
from schemas import EDLValidationError
from storage import WorkdirTooSmall

# Only compute runners are exposed by the heavy executor. The separate
# agent_executor role replaces this whole map with agent_turn only.
#
# `capture` is the exception that proves the rule (round 61): it is not a
# queued job at all, it is one TOOL CALL inside an agent turn — but the thing
# it does is launch a full Chromium at 1080x1920, which is compute, and doing
# that on the dispatcher OOM-killed a real customer's turn on the first
# production use of the feature. The turn still runs there and blocks on this
# call; only the browser moved. See webrecord.run_capture_job.
COMPUTE_RUNNERS = {
    "index": indexer.run_index_job,
    "preview": renderer.run_render_job,
    "final": renderer.run_render_job,
    "capture": webrecord.run_capture_job,
    # Same shape as capture (round 62): one tool call inside an agent turn,
    # moved here because decoding a user's 4K original for six jpegs is
    # compute, and doing it on the dispatcher killed job 1452's turn.
    "frames": frameserve.run_frames_job,
    # Round 63: optical-flow tracking of a screen quad through the takeover
    # window. Decodes the whole window of a user ORIGINAL — the exact job
    # class that OOM-killed the dispatcher four separate times.
    "track": tracker.run_track_job,
    # Round 64: the text-behind matte. Reads only the 540p proxy, but runs a
    # person-segmentation forward pass per budgeted frame — model compute,
    # which belongs on this box, never beside agent turns.
    "matte": matte.run_matte_job,
    # Round 65d: the takeover's guided content-lock. SIFT on 2048px frames
    # of a user original — tried on the dispatcher for exactly one live run,
    # which it OOM-killed (job 1513).
    "smatch": screenmatch.run_smatch_job,
    # Round 67: the erase/repaint pass — a full decode + re-encode of a user
    # ORIGINAL inside an agent turn, the heaviest member of the OOM class and
    # the last one that still ran on the dispatcher. Job 1557 (Jul 31 2026)
    # died minutes after a customer's erase ran there.
    "clean": inpaint.run_clean_job,
    # Round 97: music/voice stem separation — a torch forward pass over the
    # whole soundtrack. Model compute by definition; the dependency only
    # exists in this image (see /health features and stems.available).
    "stems": stems.run_stems_job,
}

# A separate Cloud Run service uses the same authenticated HTTP contract but
# exposes only agent_turn. It scales independently from ffmpeg and cannot be
# tricked into accepting a render job merely by changing the request body.
if config.WORKER_ROLE == "agent_executor":
    import agent_loop
    RUNNERS = {"agent_turn": agent_loop.run_agent_job}
else:
    RUNNERS = COMPUTE_RUNNERS


class _LeasedDb:
    """Bind every executor progress write to one monotonic queue claim.

    Runners deliberately share the ordinary Db interface. Keeping the binding
    here means every present and future executor runner gets fencing without
    relying on each call site to remember an extra argument.
    """

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


def _authorized(headers):
    """Constant-time bearer check. If no secret is configured the endpoint is
    open — allowed only for local testing; production MUST set the secret on
    both services (config warns on boot)."""
    if not config.REMOTE_EXECUTOR_SECRET:
        return True
    got = headers.get("Authorization", "")
    prefix = "Bearer "
    if not got.startswith(prefix):
        return False
    return hmac.compare_digest(got[len(prefix):], config.REMOTE_EXECUTOR_SECRET)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # we do our own structured logging in do_POST

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz", ""):
            # WHAT CODE IS THIS. Unauthenticated on purpose: it is the probe
            # Cloud Run itself calls, it leaks nothing (a hash of public
            # source and four integers), and the whole point is that anyone
            # holding a terminal can answer "is the render service current?"
            # in one curl instead of a day of database forensics. Best effort
            # — a health endpoint that can 500 is not a health endpoint.
            body = {"status": "ok", "role": config.WORKER_ROLE}
            try:
                body.update(version.version_report())
            except Exception as e:
                body["version_error"] = str(e)[:200]
            # WHICH PROVIDER IS THIS. The code fingerprint above cannot see a
            # vendor mismatch — the executor ran the RIGHT code against the
            # WRONG base URL for five days (see llm.config_report), failing
            # every index greeting and every visual caption without one loud
            # symptom. Same contract as the fingerprint: best effort, no keys,
            # never a gate.
            try:
                import llm as _llm
                body["llm"] = _llm.config_report()
            except Exception as e:
                body["llm_error"] = str(e)[:200]
            # HOW BIG A VIDEO CAN THIS INSTANCE ACTUALLY STAGE. On Cloud Run
            # TMP_DIR is an in-memory filesystem sized by `--memory`, so this
            # is the number that decides whether the upload cap is real — and
            # it lived nowhere: the only way to discover it was to send a job
            # big enough to get the container OOM-killed, which produces no
            # error at all. Same reasoning as the code fingerprint above: make
            # the answer one curl instead of an incident.
            try:
                import storage as _storage
                free = _storage.free_workdir_bytes()
                total = shutil.disk_usage(config.TMP_DIR).total
                gb = 1024 ** 3
                body["workdir"] = {
                    "path": config.TMP_DIR,
                    # tmpfs => staging costs RAM and the memory limit binds;
                    # anything else => it is real disk. The deploy doc assumed
                    # tmpfs unconditionally and the live service disagreed.
                    "fstype": _storage.workdir_fstype(),
                    "free_gb": round((free or 0) / gb, 1),
                    "fs_total_gb": round(total / gb, 1),
                    "mem_available_gb": round(
                        (_storage._cgroup_memory_available() or 0) / gb, 1),
                    # The largest source this instance could stage right now,
                    # given the artifacts a job writes beside it.
                    "max_source_gb": round(
                        (free or 0) / gb / config.WORKDIR_HEADROOM, 1),
                }
            except Exception as e:
                body["workdir_error"] = str(e)[:200]
            self._send(200, body)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/run":
            return self._send(404, {"error": "not found"})
        if not _authorized(self.headers):
            return self._send(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": f"bad request body: {e}"})
        job = payload.get("job") or {}
        jtype = job.get("type")
        runner = RUNNERS.get(jtype)
        if not runner:
            return self._send(400, {"error": f"unsupported job type: {jtype}"})

        job_id = job.get("id")
        t0 = time.monotonic()
        print(f"[executor] start {jtype} job={job_id} "
              f"project={job.get('project_id')}", flush=True)
        lease_claim = job.get("total_claims")
        db = _LeasedDb(job_id, lease_claim)
        if job_id is not None:
            dbx.track_job(job_id)
        try:
            if lease_claim is not None and not db.run(
                    dbx.lease_is_current, job_id, lease_claim):
                raise dbx.JobLeaseLost(
                    f"job {job_id} execution lease {lease_claim} is no longer current")
            result = runner(db, job)
            dt = round(time.monotonic() - t0, 2)
            completed = False
            # The process that spent the compute owns the result.  This used
            # to be agent-only, leaving a completed preview/final dependent on
            # the Render waiter's HTTP connection.  A Render deploy after the
            # encode therefore bought the same Cloud Run work again.
            if job_id is not None:
                if isinstance(result, dict):
                    timings = result.setdefault("timings", {})
                    timings["queue_wait_s"] = job.get("_queue_wait_s")
                    timings["total_s"] = dt
                    compute_cost.annotate_request(
                        timings, dt, config.WORKER_ROLE,
                        os.getenv("K_SERVICE", ""))
                completed = job_completion.finalize_success(
                    db, job, result, lease_claim)
                if completed is False:
                    raise dbx.JobLeaseLost(
                        f"job {job_id} execution lease {lease_claim} was "
                        "superseded before executor completion")
            print(f"[executor] done {jtype} job={job_id} in {dt}s", flush=True)
            try:
                self._send(200, {"result": result,
                                 "job_completed": bool(completed)})
            except (BrokenPipeError, ConnectionResetError):
                if not completed:
                    raise
                # The Render waiter disappeared after the executor had
                # already committed the fenced result. That is exactly why
                # completion ownership lives here; it is success, not a retry.
                print(f"[executor] dispatcher disconnected after committed "
                      f"job={job_id}; result is safe", flush=True)
        except Exception as e:
            traceback.print_exc()
            dt = round(time.monotonic() - t0, 2)
            print(f"[executor] FAILED {jtype} job={job_id} after {dt}s: {e}",
                  flush=True)
            # 200 + {"error"} so the dispatcher parses the real message and runs
            # its normal requeue/reaper path — same as a local runner raising.
            decision = failure_policy.classify(e, jtype)
            failure_timings = {"total_s": dt}
            compute_cost.annotate_request(
                failure_timings, dt, config.WORKER_ROLE,
                os.getenv("K_SERVICE", ""))
            self._send(200, {
                "error": str(e),
                "retryable": decision.retryable,
                "failure": decision.payload(e),
                "timings": failure_timings,
                "lease_lost": isinstance(e, dbx.JobLeaseLost),
            })
        finally:
            if job_id is not None:
                dbx.untrack_job(job_id)
            db.reset()   # close this request's connection (no pooling here)


def serve():
    config.require_core()
    os.makedirs(config.TMP_DIR, exist_ok=True)
    if not config.REMOTE_EXECUTOR_SECRET:
        print("[executor] WARNING: REMOTE_EXECUTOR_SECRET is unset — /run is "
              "OPEN. Set it on this service and the dispatcher.", flush=True)
    port = config.EXECUTOR_PORT
    # The dispatcher normally supplies the heartbeat while awaiting HTTP. The
    # agent executor also beats locally so a Render redeploy or network reset
    # cannot let the reaper kill a still-running turn whose result this request
    # is responsible for committing.
    threading.Thread(target=dbx.heartbeat_forever, daemon=True,
                     name="executor-heartbeat").start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"valmera-{config.WORKER_ROLE.replace('_', '-')} listening on :{port} "
          f"(code={version.code_version()} "
          f"whisper={config.WHISPER_MODEL}/{config.WHISPER_DEVICE})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    serve()
