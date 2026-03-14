"""
Deploy route — publish a project's dist/ folder to Cloudflare Pages.

Each project gets its own Cloudflare Pages project named 'hb-<job_id>'.
The live URL will be: https://hb-<job_id>.pages.dev

Uses Wrangler CLI for deployment (officially recommended by Cloudflare).
"""

from flask import Blueprint, request, jsonify, current_app
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
import os
import subprocess
import json
import time

deploy_bp = Blueprint('deploy', __name__)


def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[len('Bearer '):]
        if not token:
            return jsonify({'error': 'Token is missing!'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            user_id = data['sub']
        except Exception:
            return jsonify({'error': 'Token is invalid!'}), 401
        return f(user_id=user_id, *args, **kwargs)
    return decorated


# ── Path constants ────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUTS_DIR  = os.path.join(PROJECT_ROOT, "outputs")

# Cloudflare credentials from env
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN  = os.environ.get("CLOUDFLARE_API_TOKEN", "")


def _ensure_wrangler():
    """Make sure wrangler is installed globally. Only runs once."""
    try:
        result = subprocess.run(
            ["npx", "wrangler", "--version"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Install wrangler globally
    try:
        subprocess.run(
            ["npm", "install", "-g", "wrangler"],
            capture_output=True, text=True, timeout=120
        )
        return True
    except Exception as e:
        print(f"[deploy] Failed to install wrangler: {e}")
        return False


@deploy_bp.route('/deploy/<job_id>', methods=['POST'])
@token_required
def publish_project(user_id, job_id):
    """
    Publish a project's dist/ folder to Cloudflare Pages.
    Creates the Pages project if it doesn't exist, then deploys.
    Returns the live URL.
    """
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return jsonify({"error": "Deployment not configured. Contact support."}), 500

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    dist_dir = os.path.join(job_folder, "dist")

    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    if not os.path.isdir(dist_dir):
        return jsonify({"error": "Project hasn't been built yet. Build it first, then publish."}), 400

    # Verify the user owns this job
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, title FROM jobs WHERE job_id = %s",
                (job_id,)
            )
            row = cur.fetchone()
            if not row or str(row['user_id']) != str(user_id):
                return jsonify({"error": "Not authorized"}), 403
            project_title = row.get('title', job_id)
    finally:
        conn.close()

    # Ensure wrangler is available
    if not _ensure_wrangler():
        return jsonify({"error": "Deployment tool not available. Try again later."}), 500

    # Use custom name from request if provided, otherwise generate from title
    import re
    data = request.get_json(silent=True) or {}
    custom_name = (data.get("name", "") or "").strip().lower()

    if custom_name:
        # Sanitize user-provided name
        custom_name = re.sub(r'[^a-z0-9-]', '', custom_name)
        custom_name = re.sub(r'-+', '-', custom_name).strip('-')
        if len(custom_name) >= 3:
            cf_project_name = custom_name
        else:
            cf_project_name = None
    else:
        cf_project_name = None

    # Fallback: generate from project title
    if not cf_project_name:
        slug = project_title.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s]+', '-', slug)
        slug = re.sub(r'-+', '-', slug).strip('-')
        slug = slug[:40]
        short_id = job_id[:4]
        cf_project_name = f"{slug}-{short_id}" if slug else f"hb-{job_id}"

    env = os.environ.copy()
    env["CLOUDFLARE_ACCOUNT_ID"] = CF_ACCOUNT_ID
    env["CLOUDFLARE_API_TOKEN"] = CF_API_TOKEN

    try:
        # Deploy using wrangler — it auto-creates the project if it doesn't exist
        print(f"[deploy] Publishing {job_id} to Cloudflare Pages as {cf_project_name}...")

        result = subprocess.run(
            [
                "npx", "wrangler", "pages", "deploy", dist_dir,
                "--project-name", cf_project_name,
                "--branch", "main",
                "--commit-dirty=true",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=job_folder,
        )

        stdout = result.stdout
        stderr = result.stderr
        print(f"[deploy] wrangler stdout: {stdout[-500:]}")
        print(f"[deploy] wrangler stderr: {stderr[-500:]}")

        if result.returncode != 0:
            # Check if it's a "project doesn't exist" error — create it first
            if "could not find" in stderr.lower() or "not found" in stderr.lower():
                print(f"[deploy] Project doesn't exist, creating {cf_project_name}...")
                create_result = subprocess.run(
                    [
                        "npx", "wrangler", "pages", "project", "create",
                        cf_project_name, "--production-branch", "main",
                    ],
                    capture_output=True, text=True, timeout=60,
                    env=env, cwd=job_folder,
                    input="",  # Avoid interactive prompts
                )
                print(f"[deploy] Create stdout: {create_result.stdout[-300:]}")
                print(f"[deploy] Create stderr: {create_result.stderr[-300:]}")

                # Retry deploy
                result = subprocess.run(
                    [
                        "npx", "wrangler", "pages", "deploy", dist_dir,
                        "--project-name", cf_project_name,
                        "--branch", "main",
                        "--commit-dirty=true",
                    ],
                    capture_output=True, text=True, timeout=120,
                    env=env, cwd=job_folder,
                )
                stdout = result.stdout
                stderr = result.stderr
                print(f"[deploy] Retry stdout: {stdout[-500:]}")
                print(f"[deploy] Retry stderr: {stderr[-500:]}")

                if result.returncode != 0:
                    return jsonify({
                        "error": "Deployment failed",
                        "details": stderr[-300:]
                    }), 500

            else:
                return jsonify({
                    "error": "Deployment failed",
                    "details": stderr[-300:]
                }), 500

        # Extract the live URL from wrangler output
        # Wrangler prints something like: "✨ Deployment complete! Take a peek over at https://xxx.hb-xxxx.pages.dev"
        live_url = None
        for line in (stdout + stderr).split("\n"):
            if "pages.dev" in line:
                import re
                urls = re.findall(r'https://[a-zA-Z0-9\-]+\.pages\.dev', line)
                if urls:
                    live_url = urls[-1]  # Take the last one (production URL)
                    break

        if not live_url:
            # Fallback: construct the URL
            live_url = f"https://{cf_project_name}.pages.dev"

        print(f"[deploy] Published successfully: {live_url}")

        # Store the published URL in the database
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE jobs
                       SET published_url = %s, updated_at = NOW()
                       WHERE job_id = %s""",
                    (live_url, job_id)
                )
                conn.commit()
        finally:
            conn.close()

        return jsonify({
            "url": live_url,
            "project_name": cf_project_name,
        }), 200

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Deployment timed out. Try again."}), 504
    except Exception as e:
        print(f"[deploy] Error: {e}")
        return jsonify({"error": f"Deployment failed: {str(e)}"}), 500


@deploy_bp.route('/deploy/<job_id>/status', methods=['GET'])
@token_required
def get_deploy_status(user_id, job_id):
    """Get the published URL for a job, if any."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT published_url FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404
            return jsonify({
                "published_url": row.get("published_url"),
                "is_published": bool(row.get("published_url")),
            }), 200
    finally:
        conn.close()