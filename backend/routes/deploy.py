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
                "SELECT user_id, title, publish_name, cf_project_name FROM jobs WHERE job_id = %s",
                (job_id,)
            )
            row = cur.fetchone()
            if not row or str(row['user_id']) != str(user_id):
                return jsonify({"error": "Not authorized"}), 403
            project_title = row.get('title', job_id)
            existing_publish_name = row.get('publish_name')
            existing_cf_project = row.get('cf_project_name')
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
        custom_name = re.sub(r'[^a-z0-9-]', '', custom_name)
        custom_name = re.sub(r'-+', '-', custom_name).strip('-')
        if len(custom_name) >= 3:
            chosen_subdomain = custom_name
        else:
            chosen_subdomain = None
    else:
        chosen_subdomain = None

    if not chosen_subdomain:
        slug = project_title.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s]+', '-', slug)
        slug = re.sub(r'-+', '-', slug).strip('-')
        chosen_subdomain = slug[:40] if slug else f"app-{job_id[:8]}"

    # Check subdomain uniqueness — allow if it's the same job re-publishing
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_id FROM jobs WHERE publish_name = %s AND job_id != %s",
                (chosen_subdomain, job_id)
            )
            conflict = cur.fetchone()
            if conflict:
                return jsonify({"error": f"The name '{chosen_subdomain}' is already taken. Choose a different name."}), 409
    finally:
        conn.close()

    # If re-publishing, reuse the existing Cloudflare project name
    if existing_cf_project:
        cf_project_name = existing_cf_project
        print(f"[deploy] Re-publishing to existing project: {cf_project_name}")
    else:
        cf_project_name = chosen_subdomain

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

        # Extract the pages.dev URL from wrangler output (for fallback only)
        # Wrangler prints: "✨ Deployment complete! Take a peek over at https://abc123.projectname-xyz.pages.dev"
        # NOTE: The pages.dev URL may have a suffix (e.g., counter-cq0) but the actual
        # Cloudflare project name is what we passed to wrangler (e.g., counter).
        # Do NOT update cf_project_name from the URL.
        import re
        live_url = None
        combined_output = stdout + stderr

        # Get the pages.dev URL from deployment complete line
        for line in combined_output.split("\n"):
            if "deployment complete" in line.lower() or "take a peek" in line.lower():
                deploy_match = re.findall(r'https://[a-zA-Z0-9\-]+\.pages\.dev', line)
                if deploy_match:
                    live_url = deploy_match[-1]
                    break

        # Fallback: try the "available at" line
        if not live_url:
            for line in combined_output.split("\n"):
                if "available at" in line.lower():
                    avail_match = re.findall(r'https://[a-zA-Z0-9\-]+\.pages\.dev', line)
                    if avail_match:
                        live_url = avail_match[-1]
                        break

        if not live_url:
            live_url = f"https://{cf_project_name}.pages.dev"

        # cf_project_name stays as-is (what we passed to wrangler)
        print(f"[deploy] Published successfully: {live_url} (project: {cf_project_name})")

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

        # Get the pages.dev host for this project (for CNAME target)
        pages_dev_host = live_url.replace("https://", "") if live_url else f"{cf_project_name}.pages.dev"

        try:
            # Step 1: Create CNAME DNS record so the subdomain resolves
            zone_resp = _requests.get(
                f"https://api.cloudflare.com/client/v4/zones?name=thehustlerbot.com",
                headers=cf_headers, timeout=15,
            )
            zone_id = None
            if zone_resp.status_code == 200:
                zones = zone_resp.json().get("result", [])
                if zones:
                    zone_id = zones[0]["id"]

            if zone_id:
                # Check if CNAME already exists
                dns_check = _requests.get(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=CNAME&name={custom_domain}",
                    headers=cf_headers, timeout=15,
                )
                existing_cnames = dns_check.json().get("result", []) if dns_check.status_code == 200 else []

                if not existing_cnames:
                    cname_resp = _requests.post(
                        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                        headers=cf_headers,
                        json={
                            "type": "CNAME",
                            "name": chosen_name,
                            "content": pages_dev_host,
                            "ttl": 1,
                            "proxied": False,
                        },
                        timeout=15,
                    )
                    if cname_resp.status_code in (200, 201):
                        print(f"[deploy] DNS CNAME created: {chosen_name} → {pages_dev_host}")
                    else:
                        print(f"[deploy] DNS CNAME failed: {cname_resp.status_code} {cname_resp.text[:200]}")
                else:
                    print(f"[deploy] DNS CNAME already exists for {custom_domain}")
            else:
                print(f"[deploy] Could not find zone ID for thehustlerbot.com")

            # Step 2: Add custom domain to the Cloudflare Pages project
            cf_api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{cf_project_name}/domains"

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
                    branded_url = live_url
            else:
                print(f"[deploy] Custom domain already exists: {custom_domain}")

        except Exception as e:
            print(f"[deploy] Custom domain error: {e}")
            branded_url = live_url

        # Use the branded URL as the primary URL
        final_url = branded_url

        # Store the published URL, publish name, and CF project name in the database
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE jobs
                       SET published_url = %s, publish_name = %s, cf_project_name = %s, updated_at = NOW()
                       WHERE job_id = %s""",
                    (final_url, chosen_subdomain, cf_project_name, job_id)
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