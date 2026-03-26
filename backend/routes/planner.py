"""
planner.py — Flask blueprint for the Requirements Gatherer (Planner) agent.

Key changes from v1:
  - Runs planner_runner.run_planner() directly in a thread (not subprocess)
  - New state: "waiting_reply" for text-only responses (greetings, clarifications)
  - POST /auth/planner/<job_id>/continue — resume after text-only responses
  - Model respects meta.json (user's model selector choice)

Endpoints:
  POST /auth/planner/start          — Start planner for a job
  GET  /auth/planner/<job_id>/status — Poll planner state
  POST /auth/planner/<job_id>/answer — Submit user answer/decision
  POST /auth/planner/<job_id>/continue — Continue conversation (for text replies)
  POST /auth/planner/<job_id>/quit   — Quit the planner
  GET  /auth/planner/<job_id>/spec   — Get the final approved spec
"""

from flask import Blueprint, request, jsonify
from routes.auth import token_required
import json
import os
import sys
import threading
import time

planner_bp = Blueprint("planner", __name__)

OUTPUTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "outputs"))
ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "engine"))

# Add engine to path so we can import planner_runner directly
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)


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
        return meta_uid == req_uid
    except Exception as e:
        print(f"[planner] ownership check error: {e}")
        return False


def _resolve_model(workspace):
    """Read model from meta.json and return anthropic model string."""
    MODEL_MAP = {
        "V6":     "claude-haiku-4-5-20251001",
        "V6-pro": "claude-sonnet-4-6",
        "V7":     "claude-opus-4-6",
    }
    meta_path = os.path.join(workspace, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            V_model = meta.get("model", "V6")
            return MODEL_MAP.get(V_model, "claude-haiku-4-5-20251001")
        except Exception:
            pass
    return "claude-haiku-4-5-20251001"


def _start_planner_thread(workspace, message, model_arg=None):
    """Launch planner in a daemon thread. No subprocess, no timeout."""
    from planner_runner import run_planner

    def _run():
        try:
            run_planner(workspace, message, model_arg)
        except Exception as e:
            print(f"[planner_route] Thread error: {e}")
            try:
                import traceback
                with open(os.path.join(workspace, "planner_state.json"), "w") as f:
                    json.dump({
                        "state": "error",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }, f)
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


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

    model_arg = _resolve_model(workspace)

    _start_planner_thread(workspace, message, model_arg)

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
#  POST /auth/planner/<job_id>/continue — Resume after text-only response
# ══════════════════════════════════════════════════════════════════════════════

@planner_bp.route("/auth/planner/<job_id>/continue", methods=["POST"])
@token_required
def planner_continue(user_id, job_id):
    """
    Continue the planner conversation after a text-only response.
    The planner thread has exited after producing a text reply,
    so we need to start a new thread with the follow-up message.
    It will reload conversation history from planner_messages.jsonl.
    """
    if not _verify_job_ownership(job_id, user_id):
        return jsonify({"error": "Job not found"}), 404

    data = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "message required"}), 400

    workspace = _get_workspace(job_id)

    # Verify planner is in a resumable state
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

    model_arg = _resolve_model(workspace)

    # Start new thread — planner_runner will reload history from planner_messages.jsonl
    _start_planner_thread(workspace, message, model_arg)

    return jsonify({"ok": True, "state": "thinking"})


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