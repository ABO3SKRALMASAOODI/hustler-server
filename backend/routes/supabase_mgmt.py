"""
Supabase management routes for The Hustler Bot.

Phase 1 (Free tier): All generated apps share ONE Supabase project.
Each job gets its own set of tables prefixed with the job_id.
The shared project URL + anon key are injected into generated code.
The service_role key stays on the backend for admin operations (creating tables, RLS).

Phase 2 (Pro tier): Each job gets its own Supabase project via Management API.
Switch by setting SUPABASE_MODE=per_project in env vars.
"""

from flask import Blueprint, request, jsonify, current_app
from routes.auth import token_required, get_db
import os
import requests as http_requests

supabase_bp = Blueprint('supabase', __name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _get_supabase_config():
    """Return the shared Supabase project config from env vars."""
    return {
        "url":              os.getenv("SUPABASE_URL", ""),
        "anon_key":         os.getenv("SUPABASE_ANON_KEY", ""),
        "service_role_key": os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    }


def _supabase_headers():
    """Headers for Supabase REST API calls using service_role key."""
    config = _get_supabase_config()
    return {
        "apikey":        config["service_role_key"],
        "Authorization": f"Bearer {config['service_role_key']}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


# ── Provision Supabase for a job ──────────────────────────────────────────────

@supabase_bp.route('/job/<job_id>/enable-backend', methods=['POST'])
@token_required
def enable_backend(user_id, job_id):
    """
    Enable Supabase backend for a job.
    In Phase 1 (shared mode), this just stores the shared credentials in the
    job's meta.json and DB so the AI agent knows Supabase is available.
    """
    config = _get_supabase_config()
    if not config["url"] or not config["anon_key"]:
        return jsonify({"error": "Supabase is not configured on the server."}), 500

    # Update the jobs table with Supabase info
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Check job belongs to user
            cur.execute(
                "SELECT job_id FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            if not cur.fetchone():
                return jsonify({"error": "Job not found"}), 404

            cur.execute(
                """
                UPDATE jobs
                SET supabase_enabled = TRUE,
                    supabase_url     = %s,
                    supabase_anon_key = %s,
                    updated_at       = NOW()
                WHERE job_id = %s
                """,
                (config["url"], config["anon_key"], job_id)
            )
            conn.commit()
    finally:
        conn.close()

    # Also update the job's meta.json so AA.py knows Supabase is enabled
    import json
    outputs_dir = os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        "outputs"
    )
    meta_path = os.path.join(outputs_dir, job_id, "meta.json")
    try:
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["supabase_enabled"] = True
        meta["supabase_url"]     = config["url"]
        meta["supabase_anon_key"] = config["anon_key"]
        with open(meta_path, "w") as f:
            json.dump(meta, f)
    except Exception as e:
        print(f"[supabase] Warning: couldn't update meta.json: {e}")

    return jsonify({
        "enabled":      True,
        "supabase_url": config["url"],
        "anon_key":     config["anon_key"],
    }), 200


# ── Check backend status ─────────────────────────────────────────────────────

@supabase_bp.route('/job/<job_id>/backend-status', methods=['GET'])
@token_required
def backend_status(user_id, job_id):
    """Check if Supabase is enabled for this job."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT supabase_enabled, supabase_url, supabase_anon_key
                FROM jobs WHERE job_id = %s AND user_id = %s
                """,
                (job_id, int(user_id))
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "supabase_enabled": bool(row.get("supabase_enabled")),
        "supabase_url":     row.get("supabase_url"),
        "anon_key":         row.get("supabase_anon_key"),
    }), 200


# ── Execute SQL (used by AI agent tools via backend) ─────────────────────────

@supabase_bp.route('/job/<job_id>/sql', methods=['POST'])
@token_required
def execute_sql(user_id, job_id):
    data = request.get_json() or {}
    sql  = data.get("sql", "").strip()

    if not sql:
        return jsonify({"error": "SQL query required"}), 400

    sql_lower = sql.lower().strip()
    blocked = ["drop database", "drop schema public", "pg_terminate_backend"]
    for b in blocked:
        if b in sql_lower:
            return jsonify({"error": f"Blocked operation: {b}"}), 403

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT supabase_enabled FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404
            if not row.get("supabase_enabled"):
                return jsonify({"error": "Backend not enabled for this job"}), 400
    finally:
        conn.close()

    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    project_ref = os.getenv("SUPABASE_PROJECT_REF", "")

    if not access_token or not project_ref:
        return jsonify({"error": "Supabase Management API not configured"}), 500

    try:
        resp = http_requests.post(
            f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"query": sql},
            timeout=30,
        )
        if resp.status_code < 400:
            return jsonify({"result": resp.json()}), 200
        else:
            return jsonify({"error": "SQL execution failed", "detail": resp.text[:500]}), resp.status_code
    except Exception as e:
        return jsonify({"error": f"SQL execution error: {str(e)}"}), 500
# ── List tables in the Supabase project ──────────────────────────────────────
@supabase_bp.route('/job/<job_id>/tables', methods=['GET'])
@token_required
def list_tables(user_id, job_id):
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    project_ref = os.getenv("SUPABASE_PROJECT_REF", "")

    if not access_token or not project_ref:
        return jsonify({"error": "Supabase not configured"}), 500

    try:
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        resp = http_requests.post(
            f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"query": sql},
            timeout=15,
        )
        if resp.status_code < 400:
            return jsonify({"tables": resp.json()}), 200
        else:
            return jsonify({"tables": [], "note": "Could not fetch tables"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500