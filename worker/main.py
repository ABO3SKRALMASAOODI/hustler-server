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
    try:
        print(f"[job {job_id}] start {job['type']} project={job['project_id']} "
              f"attempt={job['attempts']}", flush=True)
        result = RUNNERS[job["type"]](worker_db, job)
        worker_db.run(dbx.finish_job, job_id, "done", None, result)
        print(f"[job {job_id}] done", flush=True)
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
    finally:
        dbx.untrack_job(job_id)


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


def reaper():
    worker_db = dbx.Db()
    while True:
        time.sleep(60)
        try:
            n = worker_db.run(dbx.fail_exhausted_jobs)
            if n:
                print(f"[reaper] failed {n} exhausted stale job(s)", flush=True)
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
