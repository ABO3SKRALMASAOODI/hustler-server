"""
planner.py — Flask blueprint for the Requirements Gatherer (Planner) agent.

Endpoints:
  POST /auth/planner/start          — Start planner for a job
  GET  /auth/planner/<job_id>/status — Poll planner state
  POST /auth/planner/<job_id>/answer — Submit user answer/decision
  POST /auth/planner/<job_id>/quit   — Quit the planner
  GET  /auth/planner/<job_id>/spec   — Get the final approved spec
"""

from flask import Blueprint, request, jsonify
from routes.auth import token_required
import json
import os
import subprocess
import threading
import time

planner_bp = Blueprint("planner", __name__)

OUTPUTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "outputs"))
ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "engine"))


def _get_workspace(job_id):
    return os.path.join(OUTPUTS_DIR, job_id)


def _verify_job_ownership(job_id, user_id):
    workspace = _get_workspace(job_id)
    meta_path = os.path.join(workspace, "meta.json")
    if not os.path.exists(meta_path):
        return False
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        meta_uid = str(meta.get("user_id", "")).strip()
        req_uid  = str(user_id).strip()
        print(f"[planner] ownership: meta={repr(meta_uid)} req={repr(req_uid)} match={meta_uid == req_uid}")
        return meta_uid == req_uid
    except Exception as e:
        print(f"[planner] ownership check error: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
#  POST /auth/planner/start
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/start", methods=["POST"])
@token_required
def start_planner(user_id):
    data = request.get_json() or {}
    job_id = data.get("job_id")
    message = data.get("message", "").strip()

    if not job_id or not message:
        return jsonify({"error": "job_id and message required"}), 400

    
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found or unauthorized"}), 404

    workspace = _get_workspace(job_id)

    # Check if planner is already running
    state_path = os.path.join(workspace, "planner_state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                st = json.load(f)
            current = st.get("state", "")
            if current in ("thinking", "waiting_questions", "waiting_spec", "waiting_edit"):
                return jsonify({"error": "Planner is already running", "state": current}), 409
        except Exception:
            pass

    # Clean up previous planner files
    for fname in ["planner_state.json", "planner_messages.jsonl", "planner_answer.json",
                   "planner_quit.json", "planner_spec.json"]:
        p = os.path.join(workspace, fname)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # Resolve model from meta.json
    model_arg = None
    meta_path = os.path.join(workspace, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            hb_model = meta.get("model", "hb-6")
            model_map = {
                "hb-6":     "claude-haiku-4-5-20251001",
                "hb-6-pro": "claude-sonnet-4-6",
                "hb-7":     "claude-opus-4-6",
            }
            model_arg = model_map.get(hb_model, "claude-sonnet-4-6")
        except Exception:
            pass

    # Launch planner in background thread
    def run_planner():
        try:
            cmd = [
                "python", os.path.join(ENGINE_DIR, "planner_runner.py"),
                "--workspace", workspace,
                "--message", message,
            ]
            if model_arg:
                cmd.extend(["--model", model_arg])

            print(f"[planner_route] Starting planner for job {job_id}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
                cwd=ENGINE_DIR,
            )
            print(f"[planner_route] Finished with code {result.returncode}")
            if result.stdout:
                print(f"[planner_route] stdout (last 500): {result.stdout[-500:]}")
            if result.stderr:
                print(f"[planner_route] stderr (last 500): {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            print(f"[planner_route] Planner timed out for job {job_id}")
            try:
                with open(os.path.join(workspace, "planner_state.json"), "w") as f:
                    json.dump({"state": "error", "error": "Planner timed out"}, f)
            except Exception:
                pass
        except Exception as e:
            print(f"[planner_route] Error: {e}")
            try:
                with open(os.path.join(workspace, "planner_state.json"), "w") as f:
                    json.dump({"state": "error", "error": str(e)}, f)
            except Exception:
                pass

    thread = threading.Thread(target=run_planner, daemon=True)
    thread.start()

    return jsonify({"ok": True, "state": "thinking"})


# ══════════════════════════════════════════════════════════════════════════════
#  GET /auth/planner/<job_id>/status
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/<job_id>/status", methods=["GET"])
@token_required
def planner_status(user_id, job_id):
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found"}), 404

    workspace = _get_workspace(job_id)
    state_path = os.path.join(workspace, "planner_state.json")

    if not os.path.exists(state_path):
        return jsonify({"state": "idle"})

    try:
        with open(state_path) as f:
            state_data = json.load(f)
        return jsonify(state_data)
    except Exception as e:
        return jsonify({"state": "error", "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  POST /auth/planner/<job_id>/answer
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/<job_id>/answer", methods=["POST"])
@token_required
def planner_answer(user_id, job_id):
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found"}), 404

    workspace = _get_workspace(job_id)
    data = request.get_json() or {}

    answer_path = os.path.join(workspace, "planner_answer.json")
    with open(answer_path, "w") as f:
        json.dump(data, f)

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
#  POST /auth/planner/<job_id>/quit
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/<job_id>/quit", methods=["POST"])
@token_required
def planner_quit(user_id, job_id):
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found"}), 404

    workspace = _get_workspace(job_id)

    # Signal the runner to quit
    quit_path = os.path.join(workspace, "planner_quit.json")
    with open(quit_path, "w") as f:
        json.dump({"ts": time.time()}, f)

    # Give runner a moment, then clean up
    time.sleep(1)
    for fname in ["planner_state.json", "planner_messages.jsonl",
                   "planner_answer.json", "planner_spec.json"]:
        p = os.path.join(workspace, fname)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
#  GET /auth/planner/<job_id>/spec
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/<job_id>/spec", methods=["GET"])
@token_required
def planner_spec(user_id, job_id):
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found"}), 404

    workspace = _get_workspace(job_id)
    spec_path = os.path.join(workspace, "planner_spec.json")

    if not os.path.exists(spec_path):
        return jsonify({"error": "No approved spec found"}), 404

    try:
        with open(spec_path) as f:
            spec_data = json.load(f)
        return jsonify(spec_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500