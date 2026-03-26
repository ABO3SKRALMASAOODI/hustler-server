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

CF_HEADERS = lambda: {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}


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

    try:
        subprocess.run(
            ["npm", "install", "-g", "wrangler"],
            capture_output=True, text=True, timeout=120
        )
        return True
    except Exception as e:
        print(f"[deploy] Failed to install wrangler: {e}")
        return False


def _get_zone_id():
    """Fetch the Cloudflare zone ID for thehustlerbot.com."""
    import requests as _requests
    resp = _requests.get(
        "https://api.cloudflare.com/client/v4/zones?name=valmera.io",
        headers=CF_HEADERS(), timeout=15,
    )
    if resp.status_code == 200:
        zones = resp.json().get("result", [])
        if zones:
            return zones[0]["id"]
    return None


def _delete_custom_domain(cf_project_name, custom_domain, zone_id=None):
    """
    Remove a custom domain from:
      1. The Cloudflare Pages project (domains list)
      2. The DNS zone (CNAME record)
    Returns (success, error_message)
    """
    import requests as _requests

    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return False, "Cloudflare credentials not configured"

    errors = []

    # ── Step 1: Remove from Pages project domains ──────────────────────────
    if cf_project_name:
        try:
            # List domains on the project
            list_resp = _requests.get(
                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{cf_project_name}/domains",
                headers=CF_HEADERS(), timeout=15,
            )
            if list_resp.status_code == 200:
                domains = list_resp.json().get("result", [])
                for d in domains:
                    if d.get("name") == custom_domain:
                        domain_id = d.get("id")
                        if domain_id:
                            del_resp = _requests.delete(
                                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{cf_project_name}/domains/{domain_id}",
                                headers=CF_HEADERS(), timeout=15,
                            )
                            if del_resp.status_code not in (200, 204):
                                errors.append(f"Pages domain delete failed: {del_resp.status_code}")
                            else:
                                print(f"[deploy] Removed {custom_domain} from Pages project {cf_project_name}")
                        break
        except Exception as e:
            errors.append(f"Pages domain removal error: {e}")

    # ── Step 2: Remove CNAME from DNS zone ────────────────────────────────
    try:
        if not zone_id:
            zone_id = _get_zone_id()

        if zone_id:
            # Extract subdomain name (e.g. "myapp" from "myapp.thehustlerbot.com")
            subdomain_name = custom_domain.replace(".valmera.io", "")

            dns_resp = _requests.get(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=CNAME&name={custom_domain}",
                headers=CF_HEADERS(), timeout=15,
            )
            if dns_resp.status_code == 200:
                records = dns_resp.json().get("result", [])
                for record in records:
                    rec_id = record.get("id")
                    if rec_id:
                        del_resp = _requests.delete(
                            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rec_id}",
                            headers=CF_HEADERS(), timeout=15,
                        )
                        if del_resp.status_code not in (200, 204):
                            errors.append(f"DNS CNAME delete failed: {del_resp.status_code}")
                        else:
                            print(f"[deploy] Removed DNS CNAME for {custom_domain}")
    except Exception as e:
        errors.append(f"DNS removal error: {e}")

    if errors:
        print(f"[deploy] Domain cleanup warnings: {errors}")

    return True, None  # Best-effort — don't fail the whole deploy over cleanup


# ── DELETE /deploy/<job_id>/domain ─────────────────────────────────────────────
@deploy_bp.route('/deploy/<job_id>/domain', methods=['DELETE'])
@token_required
def delete_domain(user_id, job_id):
    """
    Release the custom domain for a job:
    - Removes CNAME from Cloudflare DNS
    - Removes custom domain from Pages project
    - Clears publish_name and published_url from DB
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, publish_name, cf_project_name, published_url FROM jobs WHERE job_id = %s",
                (job_id,)
            )
            row = cur.fetchone()
            if not row or str(row['user_id']) != str(user_id):
                return jsonify({"error": "Not authorized"}), 403

            publish_name   = row.get('publish_name')
            cf_project_name = row.get('cf_project_name')

            if not publish_name:
                return jsonify({"error": "No domain to release"}), 400

            custom_domain = f"{publish_name}.valmera.io"

    finally:
        conn.close()

    # Delete from Cloudflare
    _delete_custom_domain(cf_project_name, custom_domain)

    # Clear from DB
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE jobs
                   SET published_url = NULL, publish_name = NULL, updated_at = NOW()
                   WHERE job_id = %s""",
                (job_id,)
            )
            conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True}), 200


@deploy_bp.route('/deploy/<job_id>', methods=['POST'])
@token_required
def publish_project(user_id, job_id):
    """
    Publish a project's dist/ folder to Cloudflare Pages.
    Creates the Pages project if it doesn't exist, then deploys.
    If the job already has a different domain (publish_name), the old one is
    deleted from Cloudflare DNS + Pages before the new one is created.
    Returns the live URL.
    """
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return jsonify({"error": "Deployment not configured. Contact support."}), 500

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    dist_dir   = os.path.join(job_folder, "dist")

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
            project_title       = row.get('title', job_id)
            existing_publish_name = row.get('publish_name')
            existing_cf_project  = row.get('cf_project_name')
    finally:
        conn.close()

    if not _ensure_wrangler():
        return jsonify({"error": "Deployment tool not available. Try again later."}), 500

    import re
    import requests as _requests

    data        = request.get_json(silent=True) or {}
    custom_name = (data.get("name", "") or "").strip().lower()
    update_only = data.get("update_only", False)   # True = redeploy same domain, no name change

    # ── Resolve chosen subdomain ──────────────────────────────────────────
    if update_only and existing_publish_name:
        # Just redeploy — keep existing domain
        chosen_subdomain = existing_publish_name
    elif custom_name:
        custom_name = re.sub(r'[^a-z0-9-]', '', custom_name)
        custom_name = re.sub(r'-+', '-', custom_name).strip('-')
        chosen_subdomain = custom_name if len(custom_name) >= 3 else None
    else:
        chosen_subdomain = None

    if not chosen_subdomain:
        slug = project_title.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s]+', '-', slug)
        slug = re.sub(r'-+', '-', slug).strip('-')
        chosen_subdomain = slug[:40] if slug else f"app-{job_id[:8]}"

    # ── Domain-change flow: clean up old domain first ─────────────────────
    domain_changed = (
        existing_publish_name
        and chosen_subdomain != existing_publish_name
        and not update_only
    )

    if domain_changed:
        old_domain = f"{existing_publish_name}.valmera.io"
        print(f"[deploy] Domain change: {existing_publish_name} → {chosen_subdomain}. Cleaning up old domain...")
        zone_id = _get_zone_id()
        _delete_custom_domain(existing_cf_project, old_domain, zone_id)

        # Clear old publish_name in DB immediately so it's freed
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET publish_name = NULL, published_url = NULL, updated_at = NOW() WHERE job_id = %s",
                    (job_id,)
                )
                conn.commit()
        finally:
            conn.close()

    # ── Check subdomain uniqueness ────────────────────────────────────────
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

    # Reuse existing CF project if same domain / update_only
    if existing_cf_project and not domain_changed:
        cf_project_name = existing_cf_project
        print(f"[deploy] Re-publishing to existing project: {cf_project_name}")
    else:
        cf_project_name = f"hb-{chosen_subdomain}"

    # ── Check if project exists in our CF account ─────────────────────────
    def _project_exists_and_is_ours(name):
        resp = _requests.get(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{name}",
            headers=CF_HEADERS(), timeout=10,
        )
        return resp.status_code == 200

    env = os.environ.copy()
    env["CLOUDFLARE_ACCOUNT_ID"] = CF_ACCOUNT_ID
    env["CLOUDFLARE_API_TOKEN"]  = CF_API_TOKEN

    def _wrangler_deploy(project_name):
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
                if "already being used" in create_err or "already exists" in create_err:
                    return False, r.stdout, "COLLISION:" + create.stderr
                return False, r.stdout, create.stderr

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

        if not success:
            error_text = (stdout + stderr).lower()
            if "collision" in error_text or "already" in error_text:
                original_name   = cf_project_name
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

        # Extract the pages.dev URL from wrangler output
        live_url       = None
        combined_output = stdout + stderr

        for line in combined_output.split("\n"):
            if "deployment complete" in line.lower() or "take a peek" in line.lower():
                deploy_match = re.findall(r'https://[a-zA-Z0-9\-]+\.pages\.dev', line)
                if deploy_match:
                    live_url = deploy_match[-1]
                    break

        if not live_url:
            for line in combined_output.split("\n"):
                if "available at" in line.lower():
                    avail_match = re.findall(r'https://[a-zA-Z0-9\-]+\.pages\.dev', line)
                    if avail_match:
                        live_url = avail_match[-1]
                        break

        if not live_url:
            live_url = f"https://{cf_project_name}.pages.dev"

        print(f"[deploy] Published successfully: {live_url} (project: {cf_project_name})")

        # ── Add custom domain ─────────────────────────────────────────────
        chosen_name   = chosen_subdomain
        custom_domain = f"{chosen_name}.valmera.io"
        branded_url   = f"https://{custom_domain}"
        pages_dev_host = live_url.replace("https://", "") if live_url else f"{cf_project_name}.pages.dev"

        try:
            zone_id = _get_zone_id()

            if zone_id:
                # Check if CNAME already exists for this name
                dns_check = _requests.get(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=CNAME&name={custom_domain}",
                    headers=CF_HEADERS(), timeout=15,
                )
                existing_cnames = dns_check.json().get("result", []) if dns_check.status_code == 200 else []

                if not existing_cnames:
                    cname_resp = _requests.post(
                        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                        headers=CF_HEADERS(),
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
                print("[deploy] Could not find zone ID for thehustlerbot.com")

            # Add custom domain to Pages project
            cf_api_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/pages/projects/{cf_project_name}/domains"

            check_resp       = _requests.get(cf_api_url, headers=CF_HEADERS(), timeout=15)
            existing_domains = []
            if check_resp.status_code == 200:
                existing_domains = [d.get("name", "") for d in check_resp.json().get("result", [])]

            if custom_domain not in existing_domains:
                add_resp = _requests.post(
                    cf_api_url,
                    headers=CF_HEADERS(),
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

        final_url = branded_url

        # Store in DB
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
            "url":          final_url,
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
                "SELECT published_url, publish_name FROM jobs WHERE job_id = %s AND user_id = %s",
                (job_id, int(user_id))
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404
            return jsonify({
                "published_url": row.get("published_url"),
                "publish_name":  row.get("publish_name"),
                "is_published":  bool(row.get("published_url")),
            }), 200
    finally:
        conn.close()