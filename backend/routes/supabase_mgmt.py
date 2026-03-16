"""
Supabase management routes for The Hustler Bot — Phase 2.

Each generated app gets its OWN Supabase project via the Management API.
When a user clicks "Enable Backend", we:
1. Create a new Supabase project
2. Wait for it to become active
3. Configure SMTP (Brevo) so emails come from support@thehustlerbot.com
4. Disable email confirmation for smooth dev experience
5. Store the project's URL + anon key + service_role key in the job's meta.json and DB
"""

from flask import Blueprint, request, jsonify, current_app
from routes.auth import token_required, get_db
import os
import time
import json
import requests as http_requests

supabase_bp = Blueprint('supabase', __name__)

# ── Config ────────────────────────────────────────────────────────────────────

SUPABASE_API       = "https://api.supabase.com/v1"
ORG_ID             = os.getenv("SUPABASE_ORG_ID", "kpkxuyxtclwllsfcqhdn")
DEFAULT_REGION     = os.getenv("SUPABASE_REGION", "us-west-1")
DB_PASSWORD_PREFIX = "hb-db-"  # prefix for generated DB passwords

OUTPUTS_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    "outputs"
)


def _mgmt_headers():
    """Headers for Supabase Management API."""
    token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


# ── Provision a new Supabase project for a job ───────────────────────────────

@supabase_bp.route('/job/<job_id>/enable-backend', methods=['POST'])
@token_required
def enable_backend(user_id, job_id):
    """
    Create a new Supabase project for this job.
    Returns the project URL and anon key once ready.
    """
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    if not access_token:
        return jsonify({"error": "Supabase Management API not configured."}), 500

    # Check job belongs to user and isn't already enabled
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_id, supabase_enabled, supabase_project_ref FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404

            # If already enabled, return existing credentials
            if row.get("supabase_enabled") and row.get("supabase_project_ref"):
                cur.execute(
                    "SELECT supabase_url, supabase_anon_key FROM jobs WHERE job_id = %s",
                    (job_id,)
                )
                existing = cur.fetchone()
                return jsonify({
                    "enabled":      True,
                    "supabase_url": existing.get("supabase_url"),
                    "anon_key":     existing.get("supabase_anon_key"),
                    "already_existed": True,
                }), 200
    finally:
        conn.close()

    # ── Step 1: Create the Supabase project ──────────────────────────────
    import uuid
    db_password = DB_PASSWORD_PREFIX + uuid.uuid4().hex[:16]

    # Get job title for project name
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM jobs WHERE job_id = %s", (job_id,))
            title_row = cur.fetchone()
            project_name = (title_row.get("title", "") if title_row else "") or f"app-{job_id}"
            # Clean the name
            project_name = project_name[:40].strip() or f"app-{job_id}"
    finally:
        conn.close()

    print(f"[supabase] Creating project '{project_name}' for job {job_id}...")

    create_resp = http_requests.post(
        f"{SUPABASE_API}/projects",
        headers=_mgmt_headers(),
        json={
            "organization_id": ORG_ID,
            "name":            project_name,
            "db_pass":         db_password,
            "region":          DEFAULT_REGION,
            "plan":            "free",
        },
        timeout=30,
    )

    if create_resp.status_code >= 400:
        error_detail = create_resp.text[:500]
        print(f"[supabase] Failed to create project: {error_detail}")
        return jsonify({"error": f"Failed to create backend: {error_detail}"}), 500

    project_data = create_resp.json()
    project_ref  = project_data.get("id", "")
    project_url  = f"https://{project_ref}.supabase.co"

    print(f"[supabase] Project created: {project_ref} — waiting for it to become active...")

    # ── Step 2: Wait for project to become active ────────────────────────
    max_wait    = 120  # seconds
    poll_interval = 5
    elapsed     = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status_resp = http_requests.get(
            f"{SUPABASE_API}/projects/{project_ref}",
            headers=_mgmt_headers(),
            timeout=15,
        )

        if status_resp.status_code < 400:
            status = status_resp.json().get("status", "")
            print(f"[supabase] Project {project_ref} status: {status} ({elapsed}s)")
            if status == "ACTIVE_HEALTHY":
                break
        else:
            print(f"[supabase] Status check failed: {status_resp.text[:200]}")

    # ── Step 3: Get the API keys ─────────────────────────────────────────
    keys_resp = http_requests.get(
        f"{SUPABASE_API}/projects/{project_ref}/api-keys",
        headers=_mgmt_headers(),
        timeout=15,
    )

    anon_key         = ""
    service_role_key = ""

    if keys_resp.status_code < 400:
        for key in keys_resp.json():
            if key.get("name") == "anon":
                anon_key = key.get("api_key", "")
            elif key.get("name") == "service_role":
                service_role_key = key.get("api_key", "")
        print(f"[supabase] Got API keys for {project_ref}")
    else:
        print(f"[supabase] Failed to get API keys: {keys_resp.text[:300]}")

    
    # ── Step 4: Configure auth, SMTP, and redirects in one call ──────────
    brevo_smtp_key = os.getenv("BREVO_SMTP_KEY", "")
    preview_url = f"https://entrepreneur-bot-backend.onrender.com/auth/preview-raw/{job_id}/"

    auth_config = {
        "mailer_autoconfirm": False,
        "site_url": preview_url,
        "uri_allow_list": "https://entrepreneur-bot-backend.onrender.com/**,https://thehustlerbot.com/**",
        "mailer_subjects_confirmation": "Verify your email",
        "mailer_templates_confirmation_content": '<div style="max-width:520px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e5e5;"><div style="padding:32px 32px 24px;text-align:center;border-bottom:1px solid #f0f0f0;"><h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#111111;">Verify Your Email</h1><p style="margin:0;font-size:14px;color:#888888;">Please confirm your email to continue</p></div><div style="padding:32px;"><p style="font-size:15px;color:#444444;line-height:1.6;margin:0 0 24px;">Thanks for signing up! Click the button below to verify your email address and activate your account.</p><div style="text-align:center;margin:28px 0;"><a href="{{ .ConfirmationURL }}" style="display:inline-block;padding:14px 36px;background:#111111;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;border-radius:8px;">Verify Email Address</a></div><p style="font-size:13px;color:#999999;line-height:1.5;margin:24px 0 0;text-align:center;">If you didn\'t create this account, you can safely ignore this email.</p></div><div style="padding:16px 32px;background:#fafafa;border-top:1px solid #f0f0f0;text-align:center;"><p style="margin:0;font-size:11px;color:#bbbbbb;">This is an automated message. Please do not reply.</p></div></div>',
    }

    if brevo_smtp_key:
        auth_config.update({
            "smtp_admin_email": "support@thehustlerbot.com",
            "smtp_host": "smtp-relay.brevo.com",
            "smtp_port": "587",
            "smtp_user": "8dc5e6001@smtp-brevo.com",
            "smtp_pass": brevo_smtp_key,
            "smtp_sender_name": "The Hustler Bot",
        })

    try:
        config_resp = http_requests.patch(
            f"{SUPABASE_API}/projects/{project_ref}/config/auth",
            headers=_mgmt_headers(),
            json=auth_config,
            timeout=15,
        )
        if config_resp.status_code < 400:
            print(f"[supabase] Auth + SMTP + redirects configured for {project_ref}")
        else:
            print(f"[supabase] Warning: config failed: {config_resp.text[:200]}")
    except Exception as e:
        print(f"[supabase] Warning: config error: {e}")

    # ── Step 7: Store credentials in DB and meta.json ────────────────────
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET supabase_enabled     = TRUE,
                    supabase_url         = %s,
                    supabase_anon_key    = %s,
                    supabase_project_ref = %s,
                    updated_at           = NOW()
                WHERE job_id = %s
                """,
                (project_url, anon_key, project_ref, job_id)
            )
            conn.commit()
    finally:
        conn.close()

    # Update meta.json
    meta_path = os.path.join(OUTPUTS_DIR, job_id, "meta.json")
    try:
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["supabase_enabled"]      = True
        meta["supabase_url"]          = project_url
        meta["supabase_anon_key"]     = anon_key
        meta["supabase_service_role"] = service_role_key
        meta["supabase_project_ref"]  = project_ref
        with open(meta_path, "w") as f:
            json.dump(meta, f)
    except Exception as e:
        print(f"[supabase] Warning: couldn't update meta.json: {e}")

    print(f"[supabase] ✓ Project {project_ref} ready for job {job_id}")

    return jsonify({
        "enabled":      True,
        "supabase_url": project_url,
        "anon_key":     anon_key,
        "project_ref":  project_ref,
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
                SELECT supabase_enabled, supabase_url, supabase_anon_key, supabase_project_ref
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
        "project_ref":      row.get("supabase_project_ref"),
    }), 200


# ── Execute SQL on a job's Supabase project ──────────────────────────────────

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

    # Get the job's project ref
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT supabase_enabled, supabase_project_ref FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404
            if not row.get("supabase_enabled"):
                return jsonify({"error": "Backend not enabled for this job"}), 400
            project_ref = row.get("supabase_project_ref")
            if not project_ref:
                return jsonify({"error": "No Supabase project found for this job"}), 400
    finally:
        conn.close()

    try:
        resp = http_requests.post(
            f"{SUPABASE_API}/projects/{project_ref}/database/query",
            headers=_mgmt_headers(),
            json={"query": sql},
            timeout=30,
        )
        if resp.status_code < 400:
            return jsonify({"result": resp.json()}), 200
        else:
            return jsonify({"error": "SQL execution failed", "detail": resp.text[:500]}), resp.status_code
    except Exception as e:
        return jsonify({"error": f"SQL execution error: {str(e)}"}), 500


# ── List tables ──────────────────────────────────────────────────────────────

@supabase_bp.route('/job/<job_id>/tables', methods=['GET'])
@token_required
def list_tables(user_id, job_id):
    # Get the job's project ref
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT supabase_project_ref FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row or not row.get("supabase_project_ref"):
                return jsonify({"tables": [], "note": "No backend project"}), 200
            project_ref = row["supabase_project_ref"]
    finally:
        conn.close()

    try:
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        resp = http_requests.post(
            f"{SUPABASE_API}/projects/{project_ref}/database/query",
            headers=_mgmt_headers(),
            json={"query": sql},
            timeout=15,
        )
        if resp.status_code < 400:
            return jsonify({"tables": resp.json()}), 200
        else:
            return jsonify({"tables": [], "note": "Could not fetch tables"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500