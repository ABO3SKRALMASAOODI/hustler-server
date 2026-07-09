"""Valmera media+agent worker.

Deployed as a Render Background Worker from worker/Dockerfile. Polls the
video_jobs table in Postgres (FOR UPDATE SKIP LOCKED) with two lanes:

  media lane  — index | preview | final   (CPU-heavy, ffmpeg/whisper)
  agent lane  — agent_turn                (IO-bound LLM loops)

Separate lanes mean an agent turn that enqueues a preview and waits for it
can never deadlock the worker. Heartbeats keep long jobs claimable-safe;
stale jobs are retried up to the attempt limit, then failed by the reaper.
"""

import os
import threading
import time
import traceback
from datetime import datetime, timezone

import agent_loop
import config
import db as dbx
import indexer
import renderer

MEDIA_TYPES = ("index", "preview", "final")
AGENT_TYPES = ("agent_turn",)

RUNNERS = {
    "index": indexer.run_index_job,
    "preview": renderer.run_render_job,
    "final": renderer.run_render_job,
    "agent_turn": agent_loop.run_agent_job,
}


def process_one(worker_db, job):
    job_id = job["id"]
    dbx.track_job(job_id)
    t0 = time.monotonic()
    queue_wait = None
    if job.get("created_at"):
        queue_wait = round(max(0.0, (
            datetime.now(timezone.utc) - job["created_at"]).total_seconds()), 2)
    try:
        print(f"[job {job_id}] start {job['type']} project={job['project_id']} "
              f"attempt={job['attempts']} queue_wait={queue_wait}s", flush=True)
        result = RUNNERS[job["type"]](worker_db, job)
        total = round(time.monotonic() - t0, 2)
        if isinstance(result, dict):
            timings = result.setdefault("timings", {})
            timings["queue_wait_s"] = queue_wait
            timings["total_s"] = total
        if job["type"] == "agent_turn" and isinstance(result, dict):
            try:
                charged = worker_db.run(dbx.charge_turn_credits,
                                        job["user_id"], job_id)
                result["credits_charged"] = charged
            except Exception as ce:
                # Billing must never fail a finished edit.
                print(f"[job {job_id}] credit charge failed: {ce}",
                      flush=True)
        worker_db.run(dbx.finish_job, job_id, "done", None, result)
        print(f"[job {job_id}] done in {total}s "
              f"(queue {queue_wait}s) timings="
              f"{(result or {}).get('timings') if isinstance(result, dict) else None}",
              flush=True)
    except Exception as e:
        traceback.print_exc()
        max_attempts = (config.MAX_ATTEMPTS_AGENT
                        if job["type"] in AGENT_TYPES
                        else config.MAX_ATTEMPTS_MEDIA)
        if job["attempts"] < max_attempts:
            worker_db.run(dbx.requeue_job, job_id, e)
            print(f"[job {job_id}] requeued after error: {e}", flush=True)
        else:
            worker_db.run(dbx.finish_job, job_id, "failed", e, None)
            print(f"[job {job_id}] FAILED: {e}", flush=True)
            _notify_failure(worker_db, job, e)
    finally:
        dbx.untrack_job(job_id)


FAIL_NOTES = {
    # agent_turn posts its own apology inside run_agent_job — not repeated.
    "final": "The final export failed ({err}). Hit Export to try again.",
    "index": ("I couldn't analyze that video ({err}). Try uploading it "
              "again, or a different format like mp4."),
}


def _notify_failure(worker_db, job, err):
    note = FAIL_NOTES.get(job["type"])
    if not note:
        return
    try:
        project = worker_db.run(dbx.get_project, job["project_id"])
        if project and project.get("chat_session_id"):
            worker_db.run(dbx.add_message, project["chat_session_id"],
                          "assistant", note.format(err=str(err)[:160]),
                          {"error": "job_failed", "job": job["id"]})
    except Exception as e2:
        print(f"[notify] {e2}", flush=True)


def lane(name, types, max_attempts):
    worker_db = dbx.Db()
    while True:
        try:
            job = worker_db.run(dbx.claim_job, types, max_attempts)
            if job:
                process_one(worker_db, job)
                continue
        except Exception as e:
            print(f"[{name}] poll error: {e}", flush=True)
            worker_db.reset()
        time.sleep(config.POLL_INTERVAL_S)


REAPER_NOTES = {
    "agent_turn": ("I lost my connection while working on that request — "
                   "nothing further was changed. Please send it again."),
    "final": ("The final export was interrupted before it finished. "
              "Hit Export again to restart it."),
}


def reaper():
    """Every turn and render must terminate VISIBLY: when a stale job's
    retries are exhausted, tell the user in chat instead of leaving the UI
    on 'Editing…' forever."""
    worker_db = dbx.Db()
    while True:
        time.sleep(60)
        try:
            rows = worker_db.run(dbx.fail_exhausted_jobs) or []
            for row in rows:
                print(f"[reaper] failed exhausted job {row['id']} "
                      f"({row['type']})", flush=True)
                note = REAPER_NOTES.get(row["type"])
                if not note:
                    continue
                try:
                    project = worker_db.run(dbx.get_project, row["project_id"])
                    if project and project.get("chat_session_id"):
                        worker_db.run(dbx.add_message,
                                      project["chat_session_id"],
                                      "assistant", note,
                                      {"error": "job_died", "job": row["id"]})
                except Exception as e:
                    print(f"[reaper] notify failed: {e}", flush=True)
        except Exception as e:
            print(f"[reaper] {e}", flush=True)
            worker_db.reset()


def main():
    config.require_core()
    os.makedirs(config.TMP_DIR, exist_ok=True)
    print(f"valmera-worker starting: media_slots={config.MEDIA_SLOTS} "
          f"agent_slots={config.AGENT_SLOTS} whisper={config.WHISPER_MODEL}/"
          f"{config.WHISPER_DEVICE} agent_model={config.AGENT_MODEL} "
          f"vision={config.VISION_MODEL or 'off'}", flush=True)

    threads = [
        threading.Thread(target=dbx.heartbeat_forever, daemon=True,
                         name="heartbeat"),
        threading.Thread(target=reaper, daemon=True, name="reaper"),
    ]
    for i in range(config.MEDIA_SLOTS):
        threads.append(threading.Thread(
            target=lane, args=(f"media{i}", MEDIA_TYPES,
                               config.MAX_ATTEMPTS_MEDIA),
            daemon=True, name=f"media{i}"))
    for i in range(config.AGENT_SLOTS):
        threads.append(threading.Thread(
            target=lane, args=(f"agent{i}", AGENT_TYPES,
                               config.MAX_ATTEMPTS_AGENT),
            daemon=True, name=f"agent{i}"))
    for t in threads:
        t.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
