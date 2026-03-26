"""
Supabase management routes for The Hustler Bot — Phase 2.

Each generated app gets its OWN Supabase project via the Management API.
When a user clicks "Enable Backend", we:
1. Immediately return 202 so the frontend doesn't time out
2. Provision in a background thread:
   - Create a new Supabase project
   - Wait for it to become active
   - Configure SMTP (Brevo) so emails come from support@thehustlerbot.com
   - Disable email confirmation for smooth dev experience
   - Store the project's URL + anon key + service_role key in the job's meta.json and DB
3. Frontend polls /supabase/job/<id>/backend-status until supabase_enabled=True
"""

from flask import Blueprint, request, jsonify, current_app
from routes.auth import token_required, get_db
import os
import time
import json
import threading
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


# ── Background provisioner ────────────────────────────────────────────────────

def _provision_supabase(app, job_id, user_id, project_name):
    """
    Runs in a daemon thread. Creates and configures the Supabase project,
    then writes the result back to the DB and meta.json.

    Uses app.app_context() so Flask globals (current_app, g) work correctly
    outside a request.
    """
    with app.app_context():
        import uuid as _uuid

        db_password = DB_PASSWORD_PREFIX + _uuid.uuid4().hex[:16]

        print(f"[supabase] Creating project '{project_name}' for job {job_id}...")

        # ── Step 1: Create the Supabase project ──────────────────────────
        try:
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
        except Exception as e:
            print(f"[supabase] Create request failed: {e}")
            _mark_failed(job_id)
            return

        if create_resp.status_code >= 400:
            print(f"[supabase] Failed to create project: {create_resp.text[:500]}")
            _mark_failed(job_id)
            return

        project_data = create_resp.json()
        project_ref  = project_data.get("id", "")
        project_url  = f"https://{project_ref}.supabase.co"

        print(f"[supabase] Project created: {project_ref} — waiting for it to become active...")

        # ── Step 2: Wait for project to become active ─────────────────────
        max_wait      = 180  # seconds — bumped up slightly for safety
        poll_interval = 5
        elapsed       = 0
        became_active = False

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                status_resp = http_requests.get(
                    f"{SUPABASE_API}/projects/{project_ref}",
                    headers=_mgmt_headers(),
                    timeout=15,
                )
                if status_resp.status_code < 400:
                    status = status_resp.json().get("status", "")
                    print(f"[supabase] Project {project_ref} status: {status} ({elapsed}s)")
                    if status == "ACTIVE_HEALTHY":
                        became_active = True
                        break
                else:
                    print(f"[supabase] Status check failed: {status_resp.text[:200]}")
            except Exception as e:
                print(f"[supabase] Status poll error: {e}")

        if not became_active:
            print(f"[supabase] Project {project_ref} never became active after {max_wait}s")
            _mark_failed(job_id)
            return

        # ── Step 3: Get the API keys ──────────────────────────────────────
        anon_key         = ""
        service_role_key = ""

        try:
            keys_resp = http_requests.get(
                f"{SUPABASE_API}/projects/{project_ref}/api-keys",
                headers=_mgmt_headers(),
                timeout=15,
            )
            if keys_resp.status_code < 400:
                for key in keys_resp.json():
                    if key.get("name") == "anon":
                        anon_key = key.get("api_key", "")
                    elif key.get("name") == "service_role":
                        service_role_key = key.get("api_key", "")
                print(f"[supabase] Got API keys for {project_ref}")
            else:
                print(f"[supabase] Failed to get API keys: {keys_resp.text[:300]}")
        except Exception as e:
            print(f"[supabase] Key fetch error: {e}")

        # ── Step 4: Configure auth, SMTP, and redirects ───────────────────
        brevo_smtp_key = os.getenv("BREVO_SMTP_KEY", "")
        callback_url   = f"https://entrepreneur-bot-backend.onrender.com/auth/supabase-callback/{job_id}"

        confirmation_template = (
            '<div style="max-width:520px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
            'background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e5e5;">'
            '<div style="padding:32px 32px 24px;text-align:center;border-bottom:1px solid #f0f0f0;">'
            '<h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#111111;">Verify Your Email</h1>'
            '<p style="margin:0;font-size:14px;color:#888888;">Please confirm your email to continue</p></div>'
            '<div style="padding:32px;">'
            '<p style="font-size:15px;color:#444444;line-height:1.6;margin:0 0 24px;">Thanks for signing up! '
            'Click the button below to verify your email address and activate your account.</p>'
            '<div style="text-align:center;margin:28px 0;">'
            '<a href="{{ .ConfirmationURL }}" style="display:inline-block;padding:14px 36px;background:#111111;'
            'color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;border-radius:8px;">Verify Email Address</a></div>'
            '<p style="font-size:13px;color:#999999;line-height:1.5;margin:24px 0 0;text-align:center;">'
            'If you didn\'t create this account, you can safely ignore this email.</p></div>'
            '<div style="padding:16px 32px;background:#fafafa;border-top:1px solid #f0f0f0;text-align:center;">'
            '<p style="margin:0;font-size:11px;color:#bbbbbb;">This is an automated message. Please do not reply.</p>'
            '</div></div>'
        )

        # PATCH 1 — site_url and redirect allowlist
        try:
            url_resp = http_requests.patch(
                f"{SUPABASE_API}/projects/{project_ref}/config/auth",
                headers=_mgmt_headers(),
                json={
                    "site_url": callback_url,
                    "uri_allow_list": (
                        f"{callback_url},"
                        "https://entrepreneur-bot-backend.onrender.com/**,"
                        "https://valmera.io/**"
                    ),
                    "mailer_autoconfirm": False,
                },
                timeout=15,
            )
            print(f"[supabase] URL config — status {url_resp.status_code} — {url_resp.text[:300]}")
        except Exception as e:
            print(f"[supabase] URL config error: {e}")

        # PATCH 2 — SMTP and email template
        if brevo_smtp_key:
            try:
                smtp_resp = http_requests.patch(
                    f"{SUPABASE_API}/projects/{project_ref}/config/auth",
                    headers=_mgmt_headers(),
                    json={
                        "smtp_admin_email":                        "support@thehustlerbot.com",
                        "smtp_host":                               "smtp-relay.brevo.com",
                        "smtp_port":                               "587",
                        "smtp_user":                               "8dc5e6001@smtp-brevo.com",
                        "smtp_pass":                               brevo_smtp_key,
                        "smtp_sender_name":                        "The Hustler Bot",
                        "mailer_subjects_confirmation":            "Verify your email",
                        "mailer_templates_confirmation_content":   confirmation_template,
                    },
                    timeout=15,
                )
                print(f"[supabase] SMTP config — status {smtp_resp.status_code} — {smtp_resp.text[:300]}")
            except Exception as e:
                print(f"[supabase] SMTP config error: {e}")

        # ── Step 5: Store credentials in DB and meta.json ─────────────────
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
        except Exception as e:
            print(f"[supabase] DB write error: {e}")
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


def _mark_failed(job_id):
    """Write a sentinel so the frontend can stop polling on hard failure."""
    meta_path = os.path.join(OUTPUTS_DIR, job_id, "meta.json")
    try:
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["supabase_provisioning_failed"] = True
        with open(meta_path, "w") as f:
            json.dump(meta, f)
    except Exception as e:
        print(f"[supabase] _mark_failed error: {e}")


# ── Provision a new Supabase project for a job ───────────────────────────────

@supabase_bp.route('/job/<job_id>/enable-backend', methods=['POST'])
@token_required
def enable_backend(user_id, job_id):
    """
    Kick off Supabase provisioning in a background thread and return 202
    immediately so the frontend never hits Render's 30-second request timeout.

    The frontend should then poll GET /supabase/job/<job_id>/backend-status
    until supabase_enabled=True (or provisioning_failed=True).
    """
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    if not access_token:
        return jsonify({"error": "Supabase Management API not configured."}), 500

    # Check job belongs to user
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

            # Already enabled — return existing credentials immediately
            if row.get("supabase_enabled") and row.get("supabase_project_ref"):
                cur.execute(
                    "SELECT supabase_url, supabase_anon_key FROM jobs WHERE job_id = %s",
                    (job_id,)
                )
                existing = cur.fetchone()
                return jsonify({
                    "enabled":         True,
                    "supabase_url":    existing.get("supabase_url"),
                    "anon_key":        existing.get("supabase_anon_key"),
                    "already_existed": True,
                }), 200
    finally:
        conn.close()

    # Check if provisioning is already in-flight (meta.json sentinel)
    meta_path = os.path.join(OUTPUTS_DIR, job_id, "meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            if meta.get("supabase_provisioning"):
                return jsonify({"provisioning": True}), 202
    except Exception:
        pass

    # Get job title for the Supabase project name
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM jobs WHERE job_id = %s", (job_id,))
            title_row = cur.fetchone()
            title = (title_row.get("title", "") if title_row else "") or "app"
            project_name = f"{title[:30].strip()}-{job_id}"
    finally:
        conn.close()

    # Write in-flight sentinel to meta.json so duplicate clicks are ignored
    try:
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["supabase_provisioning"] = True
        with open(meta_path, "w") as f:
            json.dump(meta, f)
    except Exception as e:
        print(f"[supabase] Could not write provisioning sentinel: {e}")

    # Grab the Flask app instance before leaving the request context
    app = current_app._get_current_object()

    thread = threading.Thread(
        target=_provision_supabase,
        args=(app, job_id, user_id, project_name),
        daemon=True,
    )
    thread.start()

    return jsonify({"provisioning": True}), 202


# ── Check backend status ─────────────────────────────────────────────────────

@supabase_bp.route('/job/<job_id>/backend-status', methods=['GET'])
@token_required
def backend_status(user_id, job_id):
    """
    Poll this endpoint after calling enable-backend.
    Returns:
      - supabase_enabled=True once provisioning is done
      - provisioning=True while still in-flight
      - provisioning_failed=True on hard failure
    """
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

    # Check meta.json for in-flight / failed sentinels
    provisioning        = False
    provisioning_failed = False
    meta_path = os.path.join(OUTPUTS_DIR, job_id, "meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            provisioning        = bool(meta.get("supabase_provisioning") and not meta.get("supabase_enabled"))
            provisioning_failed = bool(meta.get("supabase_provisioning_failed"))
    except Exception:
        pass

    return jsonify({
        "supabase_enabled":    bool(row.get("supabase_enabled")),
        "supabase_url":        row.get("supabase_url"),
        "anon_key":            row.get("supabase_anon_key"),
        "project_ref":         row.get("supabase_project_ref"),
        "provisioning":        provisioning,
        "provisioning_failed": provisioning_failed,
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