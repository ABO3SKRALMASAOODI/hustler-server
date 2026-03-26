from flask import Blueprint, request, jsonify, current_app
import requests
import base64
import os
import jwt
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor

github_bp = Blueprint('github', __name__)

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", "Ov23liUC5tA7pNQbfiWo")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "09bb3bfeb91a83b34200e642ba0a980232d5d7c0")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUTS_DIR  = os.path.join(PROJECT_ROOT, "outputs")

INTERNAL_FILES = {
    "state.json", "messages.jsonl", "meta.json", "prompt.txt",
    "preview_port.txt", "deduct_credits.json", "Files_list.txt",
    "progress.json",
}
SKIP_DIRS = {"node_modules", "dist", ".git", "__pycache__"}


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
            data    = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            user_id = data['sub']
        except Exception:
            return jsonify({'error': 'Token is invalid!'}), 401
        return f(user_id=user_id, *args, **kwargs)
    return decorated


def _collect_project_files(job_folder):
    """Collect all source files for the project."""
    files = []
    seen  = set()

    for root, dirs, filenames in os.walk(job_folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in filenames:
            if filename in INTERNAL_FILES:
                continue
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, job_folder).replace("\\", "/")

            if rel_path in seen:
                continue
            seen.add(rel_path)

            parts = rel_path.split("/")
            if len(parts) == 1:
                allowed_root = {
                    "package.json", "package-lock.json",
                    "vite.config.ts", "vite.config.js",
                    "tailwind.config.ts", "tailwind.config.js",
                    "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json",
                    "postcss.config.js", "postcss.config.cjs",
                    "eslint.config.js", "index.html", "README.md",
                }
                if filename not in allowed_root:
                    continue

            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception:
                pass

    return files


# ── Exchange OAuth code for GitHub access token ───────────────────────────────

@github_bp.route('/github/token', methods=['POST'])
@token_required
def exchange_github_token(user_id):
    data = request.get_json() or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "code required"}), 400

    resp = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        json={
            "client_id":     GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code":          code,
        },
        timeout=15,
    )

    if resp.status_code != 200:
        return jsonify({"error": "GitHub token exchange failed"}), 502

    gh_data = resp.json()
    access_token = gh_data.get("access_token")
    if not access_token:
        error = gh_data.get("error_description", "Unknown error")
        return jsonify({"error": f"GitHub denied: {error}"}), 400

    return jsonify({"access_token": access_token}), 200


# ── Push project to GitHub ────────────────────────────────────────────────────

@github_bp.route('/github/push/<job_id>', methods=['POST'])
@token_required
def push_to_github(user_id, job_id):
    data         = request.get_json() or {}
    access_token = data.get("access_token", "").strip()
    if not access_token:
        return jsonify({"error": "access_token required"}), 400

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    # Get project title from DB
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM jobs WHERE job_id = %s AND user_id = %s",
                        (job_id, int(user_id)))
            row = cur.fetchone()
    finally:
        conn.close()

    project_title = (row["title"] if row else job_id).strip()
    safe_name     = "".join(c if c.isalnum() or c == "-" else "-" for c in project_title.replace(" ", "-"))
    safe_name     = safe_name.strip("-").lower() or job_id
    repo_name     = f"hustlerbot-{safe_name}"

    gh_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get GitHub username
    user_resp = requests.get("https://api.github.com/user", headers=gh_headers, timeout=10)
    if user_resp.status_code != 200:
        return jsonify({"error": "Invalid GitHub token"}), 401
    gh_username = user_resp.json().get("login")

    # Create repo (handle name conflicts by appending job_id)
    create_resp = requests.post(
        "https://api.github.com/user/repos",
        headers=gh_headers,
        json={
            "name":        repo_name,
            "description": f"Built with The Hustler Bot — https://valmera.io",
            "private":     False,
            "auto_init":   False,
        },
        timeout=15,
    )

    if create_resp.status_code == 422:
        # Repo already exists — append job_id to make unique
        repo_name   = f"hustlerbot-{safe_name}-{job_id}"
        create_resp = requests.post(
            "https://api.github.com/user/repos",
            headers=gh_headers,
            json={
                "name":        repo_name,
                "description": f"Built with The Hustler Bot — https://valmera.io",
                "private":     False,
                "auto_init":   False,
            },
            timeout=15,
        )

    if create_resp.status_code not in (200, 201):
        err = create_resp.json().get("message", "Failed to create repo")
        return jsonify({"error": f"GitHub: {err}"}), 502

    # Collect files and push each one
    files = _collect_project_files(job_folder)

    # Add README if missing
    has_readme = any(f["path"].lower() == "readme.md" for f in files)
    if not has_readme:
        files.append({
            "path": "README.md",
            "content": f"""# {project_title}

Built with [valmera](https://valmera.io) — AI-powered app builder.

## Getting Started

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

## Build for production

```bash
npm run build
```
""",
        })

    failed_files = []
    for file in files:
        encoded = base64.b64encode(file["content"].encode("utf-8")).decode("utf-8")
        push_resp = requests.put(
            f"https://api.github.com/repos/{gh_username}/{repo_name}/contents/{file['path']}",
            headers=gh_headers,
            json={
                "message": f"Add {file['path']}",
                "content": encoded,
            },
            timeout=15,
        )
        if push_resp.status_code not in (200, 201):
            failed_files.append(file["path"])

    repo_url = f"https://github.com/{gh_username}/{repo_name}"

    return jsonify({
        "repo_url":     repo_url,
        "repo_name":    repo_name,
        "files_pushed": len(files) - len(failed_files),
        "failed_files": failed_files,
    }), 200