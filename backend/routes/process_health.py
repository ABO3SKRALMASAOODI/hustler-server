"""
process_health.py — Detects dead agent processes and cleans up job state.

Import and call check_job_health(job_folder, job_id, user_id) from your
status endpoint. It returns the (possibly corrected) state string.

This fixes the bug where AA.py crashes without updating state.json,
leaving the frontend polling "running" forever.
"""

import os
import json
import time
import signal


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = just check, don't actually send
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive
        return True
    except OSError:
        return False


def check_job_health(job_folder: str, job_id: str, user_id=None) -> dict:
    """
    Check if a job that claims to be "running" actually has a live process.

    Returns the state_data dict, possibly mutated to "failed" if the process
    is dead but state.json still says "running".

    Call this from your /job/<job_id>/status endpoint BEFORE returning state
    to the frontend.
    """
    state_path = os.path.join(job_folder, "state.json")
    if not os.path.exists(state_path):
        return {"state": "unknown"}

    try:
        with open(state_path) as f:
            state_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[health] Could not read state.json for {job_id}: {e}")
        return {"state": "unknown", "error": str(e)}

    # Only check running jobs
    if state_data.get("state") != "running":
        return state_data

    pid = state_data.get("pid")

    # ── Case 1: No PID recorded ──────────────────────────────────────────
    if not pid:
        # Check how long it's been "running" without a PID
        created_at = state_data.get("created_at") or state_data.get("updated_at")
        if created_at and (time.time() - created_at) > 60:
            print(f"[health] Job {job_id} has no PID and has been 'running' for >{int(time.time() - created_at)}s — marking failed")
            _mark_dead(state_path, state_data, "Process never started (no PID recorded)")
        return state_data

    # ── Case 2: PID recorded — check if it's alive ───────────────────────
    pid = int(pid)
    if _is_pid_alive(pid):
        # Process is still running — also check for staleness
        # (process alive but stuck for >30 minutes is suspicious)
        updated_at = state_data.get("updated_at") or state_data.get("created_at")
        if updated_at and (time.time() - updated_at) > 1800:
            print(f"[health] WARNING: Job {job_id} PID {pid} alive but no update in {int(time.time() - updated_at)}s — may be stuck")
            # Don't kill it yet — just log the warning. The user can cancel manually.
        return state_data

    # ── Case 3: PID is dead but state says running ────────────────────────
    print(f"[health] Job {job_id} PID {pid} is DEAD but state.json says 'running' — marking failed")

    # Check for crash_log.json which our patched AAgent.py writes
    crash_log_path = os.path.join(job_folder, "crash_log.json")
    crash_error = "Agent process died unexpectedly"
    if os.path.exists(crash_log_path):
        try:
            with open(crash_log_path) as f:
                crash_data = json.load(f)
            crash_error = crash_data.get("error", crash_error)
            last_tool = crash_data.get("last_tool", "unknown")
            print(f"[health] Crash log found — error: {crash_error[:200]}, last_tool: {last_tool}")
        except Exception:
            pass

    _mark_dead(state_path, state_data, crash_error)
    return state_data


def _mark_dead(state_path: str, state_data: dict, error_msg: str):
    """Update state.json to reflect that the job has failed."""
    state_data["state"] = "failed"
    state_data["error"] = error_msg[:500]
    state_data["updated_at"] = time.time()
    state_data["recovered_by"] = "health_check"
    try:
        with open(state_path, "w") as f:
            json.dump(state_data, f)
    except Exception as e:
        print(f"[health] Could not write failed state: {e}")