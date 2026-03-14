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
    import requests as _requests
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
        cf_project_name = f"{slug}-{short_id}" if slug else f"app-{job_id}"

    # Check if this project name is already ours (from a previous publish of this job)
    # or if it's taken by someone else — if so, append a suffix
    cf_headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }

    def _project_exists_and_is_ours(name):
        """Check if a Cloudflare Pages project exists and belongs to our account."""
        resp = _requests.get(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{name}",
            headers=cf_headers, timeout=10,
        )
        if resp.status_code == 200:
            return True  # Exists in our account — safe to redeploy
        return False  # Doesn't exist or error

    def _try_deploy_name(name):
        """Try to deploy with this name. Returns True if wrangler succeeds or project exists in our account."""
        if _project_exists_and_is_ours(name):
            return True
        # Name not in our account — it might be globally taken. We'll try and see.
        return None  # Unknown, let wrangler try

    # If the name already exists in our account, use it directly
    # Otherwise try the name, and if wrangler fails due to collision, add suffix
    if not _project_exists_and_is_ours(cf_project_name):
        # Might be a new name or a collision — we'll handle after wrangler attempt
        pass

    env = os.environ.copy()
    env["CLOUDFLARE_ACCOUNT_ID"] = CF_ACCOUNT_ID
    env["CLOUDFLARE_API_TOKEN"] = CF_API_TOKEN

    def _wrangler_deploy(project_name):
        """Run wrangler deploy. Auto-creates project if needed. Returns (success, stdout, stderr)."""
        r = subprocess.run(
            [
                "npx", "wrangler", "pages", "deploy", dist_dir,
                "--project-name", project_name,
                "--branch", "main",
                "--commit-dirty=true",
            ],
            capture_output=True, text=True, timeout=120,
            env=env, cwd=job_folder,
        )

        if r.returncode == 0:
            return True, r.stdout, r.stderr

        error_text = (r.stdout + r.stderr).lower()

        # Project doesn't exist in our account — create it and retry
        if "not found" in error_text or "8000007" in error_text:
            print(f"[deploy] Project '{project_name}' not found, creating...")
            create = subprocess.run(
                [
                    "npx", "wrangler", "pages", "project", "create",
                    project_name, "--production-branch", "main",
                ],
                capture_output=True, text=True, timeout=60,
                env=env, cwd=job_folder, input="",
            )
            print(f"[deploy] Create: {create.stdout[-200:]}{create.stderr[-200:]}")

            if create.returncode != 0:
                create_err = (create.stdout + create.stderr).lower()
                # If create fails because name is globally taken, signal collision
                if "already being used" in create_err or "already exists" in create_err:
                    return False, r.stdout, "COLLISION:" + create.stderr
                return False, r.stdout, create.stderr

            # Retry deploy after creating
            r2 = subprocess.run(
                [
                    "npx", "wrangler", "pages", "deploy", dist_dir,
                    "--project-name", project_name,
                    "--branch", "main",
                    "--commit-dirty=true",
                ],
                capture_output=True, text=True, timeout=120,
                env=env, cwd=job_folder,
            )
            return r2.returncode == 0, r2.stdout, r2.stderr

        return False, r.stdout, r.stderr

    try:
        print(f"[deploy] Publishing {job_id} to Cloudflare Pages as {cf_project_name}...")

        success, stdout, stderr = _wrangler_deploy(cf_project_name)
        print(f"[deploy] wrangler stdout: {stdout[-500:]}")
        print(f"[deploy] wrangler stderr: {stderr[-500:]}")

        # If failed due to name collision, retry with suffix
        if not success:
            error_text = (stdout + stderr).lower()
            if "collision" in error_text or "already" in error_text:
                original_name = cf_project_name
                cf_project_name = f"{cf_project_name}-{job_id[:4]}"
                print(f"[deploy] Name collision on '{original_name}', retrying as '{cf_project_name}'...")

                success, stdout, stderr = _wrangler_deploy(cf_project_name)
                print(f"[deploy] Retry stdout: {stdout[-500:]}")
                print(f"[deploy] Retry stderr: {stderr[-500:]}")

            if not success:
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

        # ── Add custom domain: name.thehustlerbot.com ────────────────
        # Use the user's original chosen name (before any collision suffix)
        chosen_name = (data.get("name", "") or "").strip().lower()
        chosen_name = re.sub(r'[^a-z0-9-]', '', chosen_name)
        chosen_name = re.sub(r'-+', '-', chosen_name).strip('-')
        if len(chosen_name) < 3:
            # Fallback to project name
            chosen_name = cf_project_name

        custom_domain = f"{chosen_name}.thehustlerbot.com"
        branded_url = f"https://{custom_domain}"

        try:
            # Add custom domain to the Cloudflare Pages project
            cf_api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{cf_project_name}/domains"

            # Check if domain already added
            check_resp = _requests.get(cf_api_url, headers=cf_headers, timeout=15)
            existing_domains = []
            if check_resp.status_code == 200:
                existing_domains = [d.get("name", "") for d in check_resp.json().get("result", [])]

            if custom_domain not in existing_domains:
                add_resp = _requests.post(
                    cf_api_url,
                    headers=cf_headers,
                    json={"name": custom_domain},
                    timeout=15,
                )
                if add_resp.status_code in (200, 201):
                    print(f"[deploy] Custom domain added: {custom_domain}")
                else:
                    print(f"[deploy] Custom domain failed: {add_resp.status_code} {add_resp.text[:200]}")
                    # Fall back to pages.dev URL if custom domain fails
                    branded_url = live_url
            else:
                print(f"[deploy] Custom domain already exists: {custom_domain}")

        except Exception as e:
            print(f"[deploy] Custom domain error: {e}")
            branded_url = live_url

        # Use the branded URL as the primary URL
        final_url = branded_url

        # Store the published URL in the database
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE jobs
                       SET published_url = %s, updated_at = NOW()
                       WHERE job_id = %s""",
                    (final_url, job_id)
                )
                conn.commit()
        finally:
            conn.close()

        return jsonify({
            "url": final_url,
            "pages_dev_url": live_url,
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