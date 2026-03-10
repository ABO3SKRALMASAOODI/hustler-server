"""
node_modules cleanup manager.

Rule:
- After a build completes, schedule node_modules deletion 30 min later.
- If the user sends a new message to the same job before the timer fires,
  cancel the pending deletion (npm install will run again as part of the build).
- This means:
    * Active jobs: node_modules stays alive between quick follow-ups.
    * Idle jobs: node_modules deleted after 30 min, saving ~200MB disk each.
    * Next-day edits: npm install runs again (~45-90s), which is the accepted trade-off.
"""

import threading
import shutil
import os
import time

# job_id -> threading.Timer
_timers: dict = {}
_lock = threading.Lock()

CLEANUP_DELAY_SECONDS = 30 * 60  # 30 minutes


def schedule_cleanup(job_folder: str, job_id: str):
    """
    Schedule node_modules deletion 30 min from now.
    If a timer already exists for this job, reset it (extend by another 30 min).
    Call this right after a successful build completes.
    """
    with _lock:
        _cancel_existing(job_id)

        def _do_cleanup():
            node_modules = os.path.join(job_folder, "node_modules")
            if os.path.isdir(node_modules):
                try:
                    shutil.rmtree(node_modules, ignore_errors=True)
                    print(f"[cleanup] node_modules deleted for job {job_id} after inactivity")
                except Exception as e:
                    print(f"[cleanup] failed to delete node_modules for {job_id}: {e}")
            with _lock:
                _timers.pop(job_id, None)

        timer = threading.Timer(CLEANUP_DELAY_SECONDS, _do_cleanup)
        timer.daemon = True
        timer.start()
        _timers[job_id] = timer
        print(f"[cleanup] scheduled node_modules deletion for job {job_id} in {CLEANUP_DELAY_SECONDS//60} min")


def cancel_cleanup(job_id: str):
    """
    Cancel a pending cleanup for this job.
    Call this when the user sends a new message to the job — we want to
    keep node_modules alive so the follow-up build is fast.
    """
    with _lock:
        cancelled = _cancel_existing(job_id)
        if cancelled:
            print(f"[cleanup] cancelled pending node_modules deletion for job {job_id} (new message incoming)")


def _cancel_existing(job_id: str) -> bool:
    """Internal — cancel timer if exists. Must be called under _lock."""
    timer = _timers.pop(job_id, None)
    if timer:
        timer.cancel()
        return True
    return False