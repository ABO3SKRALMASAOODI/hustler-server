"""
Super Agents blueprint — CRUD, chat, skills, schedules, integrations, logs, memory.

All routes prefixed with /agents (registered in app.py).
"""

from flask import Blueprint, request, jsonify, current_app
from routes.auth import token_required, get_db
import uuid
import json
import os
import sys
import threading
import time
import traceback

super_agents_bp = Blueprint("super_agents", __name__)

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

# In-memory state for running agent chats (thread_id -> state dict)
_chat_states = {}
_chat_lock = threading.Lock()


def _gen_id():
    return uuid.uuid4().hex[:8]


def _verify_agent_ownership(agent_id, user_id):
    """Check that the agent belongs to the user."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM super_agents WHERE agent_id = %s",
                (agent_id,)
            )
            row = cur.fetchone()
            if not row:
                return False
            return str(row["user_id"]) == str(user_id)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
#  CRUD
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/create", methods=["POST"])
@token_required
def create_agent(user_id):
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    agent_id = _gen_id()
    description = data.get("description", "")
    system_prompt = data.get("instructions", "")
    model = data.get("model", "V6")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO super_agents
                   (agent_id, user_id, name, description, system_prompt, model, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'draft')
                   RETURNING id, agent_id, name, description, model, status, created_at""",
                (agent_id, user_id, name, description, system_prompt, model)
            )
            agent = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "agent": dict(agent)}), 201


@super_agents_bp.route("/list", methods=["GET"])
@token_required
def list_agents(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT agent_id, name, description, model, status, created_at, updated_at
                   FROM super_agents
                   WHERE user_id = %s
                   ORDER BY updated_at DESC""",
                (user_id,)
            )
            agents = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"agents": [dict(a) for a in agents]})


@super_agents_bp.route("/<agent_id>", methods=["GET"])
@token_required
def get_agent(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM super_agents WHERE agent_id = %s",
                (agent_id,)
            )
            agent = cur.fetchone()
    finally:
        conn.close()

    if not agent:
        return jsonify({"error": "Agent not found"}), 404

    result = dict(agent)
    # Don't expose internal id
    result.pop("id", None)
    return jsonify({"agent": result})


@super_agents_bp.route("/<agent_id>", methods=["PUT"])
@token_required
def update_agent(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    allowed_fields = {"name", "description", "system_prompt", "model", "config"}
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    # Also accept "instructions" as alias for system_prompt
    if "instructions" in data and "system_prompt" not in updates:
        updates["system_prompt"] = data["instructions"]

    set_clauses = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values())
    # Serialize config as JSON if present
    for i, k in enumerate(updates):
        if k == "config" and isinstance(values[i], dict):
            values[i] = json.dumps(values[i])

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE super_agents SET {set_clauses}, updated_at = NOW() WHERE agent_id = %s",
                values + [agent_id]
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@super_agents_bp.route("/<agent_id>", methods=["DELETE"])
@token_required
def delete_agent(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM super_agents WHERE agent_id = %s", (agent_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@super_agents_bp.route("/<agent_id>/activate", methods=["POST"])
@token_required
def activate_agent(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE super_agents SET status = 'active', updated_at = NOW() WHERE agent_id = %s",
                (agent_id,)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "status": "active"})


@super_agents_bp.route("/<agent_id>/pause", methods=["POST"])
@token_required
def pause_agent(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE super_agents SET status = 'paused', updated_at = NOW() WHERE agent_id = %s",
                (agent_id,)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "status": "paused"})


# ══════════════════════════════════════════════════════════════════════
#  CHAT (thread + poll pattern)
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/<agent_id>/chat", methods=["POST"])
@token_required
def chat_send(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    message = data.get("message", "").strip()
    thread_id = data.get("thread_id")

    if not message:
        return jsonify({"error": "Message is required"}), 400

    # Check credits
    from credits import check_and_reserve
    conn = get_db()
    try:
        if not check_and_reserve(conn, int(user_id)):
            return jsonify({"error": "Insufficient credits"}), 402
    finally:
        conn.close()

    # Create or find thread
    conn = get_db()
    try:
        if not thread_id:
            thread_id = _gen_id()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_threads (thread_id, agent_id, channel, title)
                       VALUES (%s, %s, 'web', %s)""",
                    (thread_id, agent_id, message[:100])
                )
            conn.commit()
        else:
            # Verify thread belongs to this agent
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM agent_threads WHERE thread_id = %s AND agent_id = %s",
                    (thread_id, agent_id)
                )
                if not cur.fetchone():
                    return jsonify({"error": "Thread not found"}), 404
    finally:
        conn.close()

    # Start processing in background thread
    db_url = current_app.config["DATABASE_URL"]

    with _chat_lock:
        _chat_states[thread_id] = {
            "state": "thinking",
            "response": None,
            "error": None,
        }

    def _run_chat():
        try:
            from super_agent.runner import SuperAgentRunner
            runner = SuperAgentRunner(agent_id, thread_id, db_url)
            result = runner.run(message, trigger_type="chat")

            with _chat_lock:
                _chat_states[thread_id] = {
                    "state": "done",
                    "response": result["text"],
                    "credits_used": result["credits_used"],
                    "tokens": result["tokens"],
                    "error": None,
                }
        except Exception as e:
            print(f"[super_agent] Chat error for {agent_id}/{thread_id}: {e}")
            traceback.print_exc()
            with _chat_lock:
                _chat_states[thread_id] = {
                    "state": "error",
                    "response": None,
                    "error": str(e)[:500],
                }

    thread = threading.Thread(target=_run_chat, daemon=True)
    thread.start()

    return jsonify({
        "ok": True,
        "thread_id": thread_id,
        "state": "thinking",
    })


@super_agents_bp.route("/<agent_id>/chat/status", methods=["GET"])
@token_required
def chat_status(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    thread_id = request.args.get("thread_id")
    if not thread_id:
        return jsonify({"error": "thread_id query param required"}), 400

    with _chat_lock:
        state = _chat_states.get(thread_id)

    if not state:
        return jsonify({"state": "idle"})

    result = {"state": state["state"]}
    if state["state"] == "done":
        result["response"] = state["response"]
        result["credits_used"] = state.get("credits_used", 0)
        result["tokens"] = state.get("tokens", {})
        # Clean up after delivery
        with _chat_lock:
            _chat_states.pop(thread_id, None)
    elif state["state"] == "error":
        result["error"] = state["error"]
        with _chat_lock:
            _chat_states.pop(thread_id, None)

    return jsonify(result)


@super_agents_bp.route("/<agent_id>/threads", methods=["GET"])
@token_required
def list_threads(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT thread_id, channel, title, created_at, updated_at
                   FROM agent_threads
                   WHERE agent_id = %s
                   ORDER BY updated_at DESC
                   LIMIT 50""",
                (agent_id,)
            )
            threads = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"threads": [dict(t) for t in threads]})


@super_agents_bp.route("/<agent_id>/threads/<thread_id>", methods=["GET"])
@token_required
def get_thread_messages(user_id, agent_id, thread_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT role, content, created_at
                   FROM agent_messages
                   WHERE thread_id = %s
                   ORDER BY created_at ASC""",
                (thread_id,)
            )
            messages = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"messages": [dict(m) for m in messages]})


@super_agents_bp.route("/<agent_id>/threads/<thread_id>", methods=["DELETE"])
@token_required
def delete_thread(user_id, agent_id, thread_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_threads WHERE thread_id = %s AND agent_id = %s",
                (thread_id, agent_id)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════
#  SKILLS
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/skills/catalog", methods=["GET"])
@token_required
def skills_catalog(user_id):
    from super_agent.skills import get_catalog_info
    return jsonify({"skills": get_catalog_info()})


@super_agents_bp.route("/<agent_id>/skills", methods=["GET"])
@token_required
def list_agent_skills(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, skill_type, config, enabled, created_at FROM agent_skills WHERE agent_id = %s",
                (agent_id,)
            )
            skills = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"skills": [dict(s) for s in skills]})


@super_agents_bp.route("/<agent_id>/skills", methods=["POST"])
@token_required
def add_skill(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    skill_type = data.get("skill_type", "").strip()
    skill_config = data.get("config", {})

    from super_agent.skills import get_skill_class
    if not get_skill_class(skill_type):
        return jsonify({"error": f"Unknown skill type: {skill_type}"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_skills (agent_id, skill_type, config, enabled)
                   VALUES (%s, %s, %s, TRUE)
                   RETURNING id, skill_type, config, enabled""",
                (agent_id, skill_type,
                 json.dumps(skill_config) if isinstance(skill_config, dict) else skill_config)
            )
            skill = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "skill": dict(skill)}), 201


@super_agents_bp.route("/<agent_id>/skills/<int:skill_id>", methods=["PUT"])
@token_required
def update_skill(user_id, agent_id, skill_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    conn = get_db()
    try:
        updates = []
        values = []
        if "config" in data:
            updates.append("config = %s")
            cfg = data["config"]
            values.append(json.dumps(cfg) if isinstance(cfg, dict) else cfg)
        if "enabled" in data:
            updates.append("enabled = %s")
            values.append(bool(data["enabled"]))

        if not updates:
            return jsonify({"error": "Nothing to update"}), 400

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE agent_skills SET {', '.join(updates)} WHERE id = %s AND agent_id = %s",
                values + [skill_id, agent_id]
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@super_agents_bp.route("/<agent_id>/skills/<int:skill_id>", methods=["DELETE"])
@token_required
def remove_skill(user_id, agent_id, skill_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_skills WHERE id = %s AND agent_id = %s",
                (skill_id, agent_id)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════
#  SCHEDULES
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/<agent_id>/schedules", methods=["GET"])
@token_required
def list_schedules(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, cron_expression, task_prompt, timezone,
                          enabled, last_run_at, next_run_at, created_at
                   FROM agent_schedules
                   WHERE agent_id = %s
                   ORDER BY created_at DESC""",
                (agent_id,)
            )
            schedules = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"schedules": [dict(s) for s in schedules]})


@super_agents_bp.route("/<agent_id>/schedules", methods=["POST"])
@token_required
def create_schedule(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    cron_expr = data.get("cron_expression", "").strip()
    task_prompt = data.get("task_prompt", "").strip()

    if not cron_expr or not task_prompt:
        return jsonify({"error": "cron_expression and task_prompt are required"}), 400

    name = data.get("name", "")
    timezone = data.get("timezone", "UTC")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_schedules
                   (agent_id, name, cron_expression, task_prompt, timezone, enabled)
                   VALUES (%s, %s, %s, %s, %s, TRUE)
                   RETURNING id, name, cron_expression, task_prompt, timezone, enabled, created_at""",
                (agent_id, name, cron_expr, task_prompt, timezone)
            )
            schedule = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "schedule": dict(schedule)}), 201


@super_agents_bp.route("/<agent_id>/schedules/<int:schedule_id>", methods=["PUT"])
@token_required
def update_schedule(user_id, agent_id, schedule_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    allowed = {"name", "cron_expression", "task_prompt", "timezone", "enabled"}
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    set_clauses = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values())

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE agent_schedules SET {set_clauses} WHERE id = %s AND agent_id = %s",
                values + [schedule_id, agent_id]
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@super_agents_bp.route("/<agent_id>/schedules/<int:schedule_id>", methods=["DELETE"])
@token_required
def delete_schedule(user_id, agent_id, schedule_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_schedules WHERE id = %s AND agent_id = %s",
                (schedule_id, agent_id)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════
#  INTEGRATIONS
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/<agent_id>/integrations", methods=["GET"])
@token_required
def list_integrations(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, platform, status, created_at
                   FROM agent_integrations
                   WHERE agent_id = %s""",
                (agent_id,)
            )
            integrations = cur.fetchall()
    finally:
        conn.close()

    # Don't expose config (contains secrets)
    return jsonify({"integrations": [dict(i) for i in integrations]})


@super_agents_bp.route("/<agent_id>/integrations", methods=["POST"])
@token_required
def add_integration(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    data = request.get_json() or {}
    platform = data.get("platform", "").strip().lower()

    if platform not in ("whatsapp", "telegram", "slack"):
        return jsonify({"error": "Platform must be whatsapp, telegram, or slack"}), 400

    config = data.get("config", {})
    webhook_secret = _gen_id() + _gen_id()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_integrations
                   (agent_id, platform, config, webhook_secret, status)
                   VALUES (%s, %s, %s, %s, 'pending')
                   ON CONFLICT (agent_id, platform)
                   DO UPDATE SET config = EXCLUDED.config,
                                 webhook_secret = EXCLUDED.webhook_secret,
                                 status = 'pending'
                   RETURNING id, platform, status, created_at""",
                (agent_id, platform,
                 json.dumps(config) if isinstance(config, dict) else config,
                 webhook_secret)
            )
            integration = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    result = dict(integration)
    result["webhook_secret"] = webhook_secret
    return jsonify({"ok": True, "integration": result}), 201


@super_agents_bp.route("/<agent_id>/integrations/<platform>", methods=["DELETE"])
@token_required
def remove_integration(user_id, agent_id, platform):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_integrations WHERE agent_id = %s AND platform = %s",
                (agent_id, platform)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════
#  LOGS
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/<agent_id>/logs", methods=["GET"])
@token_required
def list_logs(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, trigger_type, trigger_source, status,
                          input_summary, output_summary, tokens_used,
                          credits_used, error, started_at, completed_at, duration_ms
                   FROM agent_logs
                   WHERE agent_id = %s
                   ORDER BY started_at DESC
                   LIMIT %s OFFSET %s""",
                (agent_id, limit, offset)
            )
            logs = cur.fetchall()
    finally:
        conn.close()

    return jsonify({"logs": [dict(l) for l in logs]})


# ══════════════════════════════════════════════════════════════════════
#  MEMORY
# ══════════════════════════════════════════════════════════════════════

@super_agents_bp.route("/<agent_id>/memory", methods=["GET"])
@token_required
def list_memories(user_id, agent_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    db_url = current_app.config["DATABASE_URL"]
    from super_agent.memory_manager import MemoryManager
    mm = MemoryManager(agent_id, db_url)
    memories = mm.list_all()

    return jsonify({"memories": [dict(m) for m in memories]})


@super_agents_bp.route("/<agent_id>/memory/<int:memory_id>", methods=["DELETE"])
@token_required
def delete_memory(user_id, agent_id, memory_id):
    if not _verify_agent_ownership(agent_id, user_id):
        return jsonify({"error": "Agent not found"}), 404

    db_url = current_app.config["DATABASE_URL"]
    from super_agent.memory_manager import MemoryManager
    mm = MemoryManager(agent_id, db_url)
    mm.delete(memory_id)

    return jsonify({"ok": True})
