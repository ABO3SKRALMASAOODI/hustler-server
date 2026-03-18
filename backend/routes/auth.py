from flask import Blueprint, request, jsonify, current_app, send_from_directory
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random
from routes.verify_email import send_code_to_email
import uuid, subprocess, os, signal
import shutil, json, time, base64
from credits import (
    count_running_jobs,
    check_and_reserve, deduct_credits, get_balance,
    get_job_credits, refresh_daily_credits, tokens_to_credits,
    is_model_allowed, get_anthropic_model, PLAN_MODELS
)

auth_bp = Blueprint('auth', __name__)

# ------------------------------------------------------------------ #
#  DB + Auth helpers                                                   #
# ------------------------------------------------------------------ #

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


# ------------------------------------------------------------------ #
#  Path constants                                                      #
# ------------------------------------------------------------------ #

PROJECT_ROOT      = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUTS_DIR       = os.path.join(PROJECT_ROOT, "outputs")
ENGINE_SCRIPT     = os.path.join(PROJECT_ROOT, "engine", "AA.py")
TEMPLATE_SCAFFOLD = os.path.join(PROJECT_ROOT, "engine", "templates", "vite-react")

# Max upload: 10MB per file, max 5 files per message
MAX_UPLOAD_SIZE       = 10 * 1024 * 1024
MAX_UPLOAD_FILES      = 5
ALLOWED_UPLOAD_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
    'pdf',
    'txt', 'md', 'csv',
}


# ------------------------------------------------------------------ #
#  Template definitions                                                #
# ------------------------------------------------------------------ #

TEMPLATE_JOB_IDS = {
    "6a1def90": "SaaS Landing Page",
    "ea5cb482": "Developer Portfolio",
    "70d042cf": "E-commerce Product Page",
    "515567f6": "Analytics Dashboard",
    "0fa66551": "Waitlist / Coming Soon",
    "4338edbb": "Restaurant / Menu",
}


# ------------------------------------------------------------------ #
#  Preview URL helper                                                  #
# ------------------------------------------------------------------ #

def _get_preview_url(job_id, job_folder):
    from flask import request as flask_request

    port_file = os.path.join(job_folder, "preview_port.txt")
    if os.path.exists(port_file):
        try:
            os.remove(port_file)
        except OSError:
            pass

    dist_dir = os.path.join(job_folder, "dist")
    if os.path.isdir(dist_dir):
        base = flask_request.host_url.rstrip("/")
        return f"{base}/auth/preview/{job_id}/"

    return None


# ------------------------------------------------------------------ #
#  Subprocess kill helper                                              #
# ------------------------------------------------------------------ #

def _kill_job_process(job_folder):
    """Kill the AA.py subprocess. Uses pkill as primary strategy."""
    state_path = os.path.join(job_folder, "state.json")
    if not os.path.exists(state_path):
        return

    try:
        with open(state_path) as f:
            state_data = json.load(f)
        pid = state_data.get("pid")

        try:
            result = subprocess.run(
                ["pkill", "-9", "-f", "--", f"--workspace {job_folder}"],
                capture_output=True, text=True, timeout=10
            )
            print(f"[cancel] pkill by workspace: returncode={result.returncode} stderr={result.stderr.strip()}")
        except Exception as e:
            print(f"[cancel] pkill by workspace failed: {e}")

        if not pid:
            print(f"[cancel] No PID in state.json, relying on pkill")
            return

        pid = int(pid)

        try:
            os.kill(pid, signal.SIGKILL)
            print(f"[cancel] Sent SIGKILL to PID {pid}")
        except ProcessLookupError:
            print(f"[cancel] PID {pid} already dead")
        except PermissionError as e:
            print(f"[cancel] Permission denied on PID {pid}: {e}")
        except OSError as e:
            print(f"[cancel] OSError killing PID {pid}: {e}")

    except Exception as e:
        print(f"[cancel] Error killing process: {e}")


# ------------------------------------------------------------------ #
#  Helper: get user plan from DB                                       #
# ------------------------------------------------------------------ #

def _get_user_plan(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT plan FROM users WHERE id = %s", (int(user_id),))
            row = cur.fetchone()
        return (row.get("plan") or "free") if row else "free"
    finally:
        conn.close()


# ------------------------------------------------------------------ #
#  Upload helper                                                       #
# ------------------------------------------------------------------ #

def _save_uploads_to_job(job_folder, files):
    uploads_dir = os.path.join(job_folder, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    manifest = []
    for f in files:
        if not f or not f.filename:
            continue

        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            continue

        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        if size > MAX_UPLOAD_SIZE:
            continue

        safe_name = f"{uuid.uuid4().hex[:8]}_{f.filename}"
        save_path = os.path.join(uploads_dir, safe_name)
        f.save(save_path)

        mime_map = {
            'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'gif': 'image/gif', 'webp': 'image/webp', 'svg': 'image/svg+xml',
            'pdf': 'application/pdf',
            'txt': 'text/plain', 'md': 'text/plain', 'csv': 'text/csv',
        }
        media_type = mime_map.get(ext, 'application/octet-stream')

        manifest.append({
            "filename":   f.filename,
            "path":       os.path.join("uploads", safe_name),
            "media_type": media_type,
            "size":       size,
        })

    return manifest


# ------------------------------------------------------------------ #
#  Credits endpoints                                                   #
# ------------------------------------------------------------------ #

@auth_bp.route('/credits', methods=['GET'])
@token_required
def get_user_credits(user_id):
    conn = get_db()
    try:
        info = get_balance(conn, int(user_id))
        return jsonify(info), 200
    finally:
        conn.close()


@auth_bp.route('/job/<job_id>/credits', methods=['GET'])
@token_required
def get_job_credit_breakdown(user_id, job_id):
    conn = get_db()
    try:
        turns = get_job_credits(conn, job_id)
        return jsonify({"turns": turns}), 200
    finally:
        conn.close()


# ------------------------------------------------------------------ #
#  Model access info                                                   #
# ------------------------------------------------------------------ #

@auth_bp.route('/models', methods=['GET'])
@token_required
def get_available_models(user_id):
    plan    = _get_user_plan(user_id)
    allowed = PLAN_MODELS.get(plan, PLAN_MODELS["free"])

    models = [
        {
            "id":          "hb-6",
            "name":        "HB-6",
            "description": "Fast & efficient for everyday tasks",
            "engine":      "claude-haiku-4-5-20251001",
            "locked":      "hb-6" not in allowed,
            "min_plan":    "free",
        },
        {
            "id":          "hb-6-pro",
            "name":        "HB-6 Pro",
            "description": "Powerful for complex apps, uses more credits",
            "engine":      "claude-sonnet-4-6",
            "locked":      "hb-6-pro" not in allowed,
            "min_plan":    "plus",
        },
        {
            "id":          "hb-7",
            "name":        "HB-7",
            "description": "Advanced reasoning for complex tasks, highest credit usage",
            "engine":      "claude-opus-4-6",
            "locked":      "hb-7" not in allowed,
            "min_plan":    "ultra",
        },
    ]

    return jsonify({"models": models, "plan": plan}), 200


# ------------------------------------------------------------------ #
#  Subscription                                                        #
# ------------------------------------------------------------------ #

@auth_bp.route('/status/subscription', methods=['GET'])
@token_required
def check_subscription(user_id):
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_subscribed, subscription_id, plan FROM users WHERE id = %s", (user_id,))
    row    = cursor.fetchone()
    cursor.close(); conn.close()
    return jsonify({
        'is_subscribed':   bool(row['is_subscribed']) if row else False,
        'subscription_id': row['subscription_id']     if row else None,
        'plan':            row['plan'] if row else 'free',
    })


# ------------------------------------------------------------------ #
#  Register                                                            #
# ------------------------------------------------------------------ #

@auth_bp.route('/register', methods=['POST'])
def register():
    data     = request.get_json()
    email    = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM users
        WHERE email = %s AND is_verified = 0 AND created_at < NOW() - INTERVAL '5 minute'
    """, (email,))
    conn.commit()

    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user      = cursor.fetchone()
    hashed_pw = generate_password_hash(password)

    if user:
        if user['is_verified'] == 0:
            cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed_pw, email))
            conn.commit()
            code = str(random.randint(100000, 999999))
            cursor.execute("""
                INSERT INTO email_codes (email, code)
                VALUES (%s, %s)
                ON CONFLICT (email) DO UPDATE SET code = EXCLUDED.code
            """, (email, code))
            conn.commit()
            send_code_to_email(email, code)
            cursor.close(); conn.close()
            return jsonify({'message': 'Verification code re-sent. Please verify your email.'}), 200
        else:
            cursor.close(); conn.close()
            return jsonify({'error': 'User already exists'}), 409

    cursor.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, hashed_pw))
    conn.commit()
    code = str(random.randint(100000, 999999))
    cursor.execute("""
        INSERT INTO email_codes (email, code)
        VALUES (%s, %s)
        ON CONFLICT (email) DO UPDATE SET code = EXCLUDED.code
    """, (email, code))
    conn.commit()
    send_code_to_email(email, code)
    cursor.close(); conn.close()
    return jsonify({'message': 'User registered. Verification code sent.'}), 201


# ------------------------------------------------------------------ #
#  Login                                                               #
# ------------------------------------------------------------------ #

@auth_bp.route('/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = data.get('email')
    password = data.get('password')

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    cursor.close(); conn.close()

    if not user or user['is_verified'] == 0:
        return jsonify({'error': 'User not found. Please register.'}), 404
    if not check_password_hash(user['password'], password):
        return jsonify({'error': 'Incorrect password'}), 401

    token = jwt.encode({
        'sub':   str(user['id']),
        'email': user['email'],
        'exp':   datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, current_app.config['SECRET_KEY'], algorithm='HS256')
    plan = user.get('plan', 'free') or 'free'
    return jsonify({'token': token, 'plan': plan}), 200


# ------------------------------------------------------------------ #
#  Password reset                                                      #
# ------------------------------------------------------------------ #

@auth_bp.route('/send-reset-code', methods=['POST'])
def send_reset_code():
    data  = request.get_json()
    email = data.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s AND is_verified = 1", (email,))
    if not cursor.fetchone():
        cursor.close(); conn.close()
        return jsonify({'error': 'User not found or not verified'}), 404
    code       = str(random.randint(100000, 999999))
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()
    cursor.execute("""
        INSERT INTO password_reset_codes (email, code, expires_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET code = EXCLUDED.code, expires_at = EXCLUDED.expires_at
    """, (email, code, expires_at))
    conn.commit()
    cursor.close(); conn.close()
    send_code_to_email(email, code)
    return jsonify({'message': 'Reset code sent to your email'}), 200


@auth_bp.route('/verify-reset-code', methods=['POST'])
def verify_reset_code():
    data  = request.get_json()
    email = data.get('email')
    code  = data.get('code')

    if not email or not code:
        return jsonify({'error': 'Email and code are required'}), 400

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, expires_at FROM password_reset_codes WHERE email = %s",
        (email,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return jsonify({'error': 'No code found'}), 404

    if str(row['code']).strip() != str(code).strip():
        return jsonify({'error': 'Incorrect code'}), 400

    expires_at = row['expires_at']
    if isinstance(expires_at, str):
        expires_at = datetime.datetime.fromisoformat(expires_at)

    if expires_at < datetime.datetime.utcnow():
        return jsonify({'error': 'Code expired'}), 400

    return jsonify({'message': 'Code verified'}), 200


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json()
    email        = data.get('email')
    new_password = data.get('password')
    if not email or not new_password:
        return jsonify({'error': 'Email and new password are required'}), 400
    hashed_pw = generate_password_hash(new_password)
    conn      = get_db()
    cursor    = conn.cursor()
    cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed_pw, email))
    cursor.execute("DELETE FROM password_reset_codes WHERE email = %s", (email,))
    conn.commit()
    cursor.close(); conn.close()
    return jsonify({'message': 'Password updated successfully'}), 200


# ------------------------------------------------------------------ #
#  Internal helper — deduct credits after job completes               #
# ------------------------------------------------------------------ #

def _process_credits_deduction(job_id, job_folder, user_id):
    lock_path = os.path.join(job_folder, "credits_processed.lock")

    if os.path.exists(lock_path):
        return

    deduct_file  = os.path.join(job_folder, "deduct_credits.json")
    partial_file = os.path.join(job_folder, "partial_deduction.json")

    target_file = None
    if os.path.exists(deduct_file):
        target_file = deduct_file
    elif os.path.exists(partial_file):
        target_file = partial_file

    if not target_file:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET state = 'completed', updated_at = NOW() WHERE job_id = %s AND state = 'running'",
                    (job_id,)
                )
                conn.commit()
        finally:
            conn.close()
        try:
            with open(lock_path, "w") as f:
                json.dump({"processed_at": time.time(), "source": "none"}, f)
        except Exception:
            pass
        return

    with open(target_file) as f:
        entries = json.load(f)

    if not entries:
        try:
            with open(lock_path, "w") as f:
                json.dump({"processed_at": time.time(), "source": "empty"}, f)
        except Exception:
            pass
        return

    meta_path = os.path.join(job_folder, "meta.json")
    model     = "hb-6-pro"
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            model = meta.get("model", "hb-6-pro")
        except Exception:
            pass

    conn = get_db()
    try:
        for i, entry in enumerate(entries):
            entry_model = entry.get("model", model)
            deduct_credits(
                conn,
                user_id            = int(user_id),
                job_id             = job_id,
                turn               = i + 1,
                tokens_used        = int(entry.get("tokens_used", 0)),
                input_tokens       = int(entry.get("input_tokens", 0)),
                output_tokens      = int(entry.get("output_tokens", 0)),
                cache_write_tokens = int(entry.get("cache_write_tokens", 0)),
                cache_read_tokens  = int(entry.get("cache_read_tokens", 0)),
                model              = entry_model,
            )

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET state = 'completed', updated_at = NOW() WHERE job_id = %s",
                (job_id,)
            )
            conn.commit()

        for f_path in [deduct_file, partial_file]:
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass

        try:
            with open(lock_path, "w") as f:
                json.dump({"processed_at": time.time(), "source": os.path.basename(target_file)}, f)
        except Exception:
            pass

    finally:
        conn.close()


@auth_bp.route('/job/<job_id>/title', methods=['PATCH'])
@token_required
def update_job_title(user_id, job_id):
    data  = request.get_json() or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET title = %s, updated_at = NOW() WHERE job_id = %s AND user_id = %s",
                (title[:50], job_id, int(user_id))
            )
            conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------------ #
#  Smart project title generation                                      #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/title', methods=['POST'])
@token_required
def generate_job_title(user_id):
    import anthropic as _anthropic
    data   = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"title": "New Project"}), 200

    try:
        client = _anthropic.Anthropic()
        resp   = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 30,
            messages   = [{
                "role":    "user",
                "content": (
                    f"Give a short 3-5 word project title for this app idea. "
                    f"Return ONLY the title, no punctuation, no quotes, no explanation.\n\n"
                    f"App idea: {prompt[:300]}"
                ),
            }],
        )
        title = resp.content[0].text.strip().strip('"').strip("'")
        title = title[:50] if title else prompt[:40]
    except Exception:
        title = prompt[:40]

    return jsonify({"title": title}), 200


# ------------------------------------------------------------------ #
#  File upload endpoint                                                #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/upload', methods=['POST'])
@token_required
def upload_files(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    if len(files) > MAX_UPLOAD_FILES:
        return jsonify({"error": f"Maximum {MAX_UPLOAD_FILES} files per upload"}), 400

    manifest = _save_uploads_to_job(job_folder, files)

    if not manifest:
        return jsonify({"error": "No valid files uploaded"}), 400

    return jsonify({"files": manifest}), 200


# ------------------------------------------------------------------ #
#  Generate — initial project                                          #
# ------------------------------------------------------------------ #

@auth_bp.route('/generate', methods=['POST'])
@token_required
def generate(user_id):
    if request.content_type and 'multipart/form-data' in request.content_type:
        prompt         = request.form.get("prompt", "")
        title          = request.form.get("title", "").strip() or (prompt[:40] if prompt else "New Project")
        model          = request.form.get("model", "hb-6")
        uploaded_files = request.files.getlist("files")
    else:
        data           = request.get_json() or {}
        prompt         = data.get("prompt")
        title          = data.get("title", "").strip() or (prompt[:40] if prompt else "New Project")
        model          = data.get("model", "hb-6")
        uploaded_files = []

    if not prompt:
        return jsonify({"error": "Prompt required"}), 400

    plan = _get_user_plan(user_id)
    if not is_model_allowed(plan, model):
        return jsonify({"error": f"Your {plan} plan doesn't include access to this model. Please upgrade."}), 403

    conn = get_db()
    try:
        if not check_and_reserve(conn, int(user_id)):
            return jsonify({"error": "Not enough credits. Please subscribe or wait for your daily refresh."}), 402
        running = count_running_jobs(conn, int(user_id))
        if running >= 3:
            return jsonify({"error": "You already have 3 projects building. Wait for one to finish before starting another."}), 429
    finally:
        conn.close()

    job_id     = str(uuid.uuid4())[:8]
    job_folder = os.path.join(OUTPUTS_DIR, job_id)

    while os.path.exists(job_folder):
        job_id     = str(uuid.uuid4())[:8]
        job_folder = os.path.join(OUTPUTS_DIR, job_id)

    os.makedirs(job_folder, exist_ok=True)

    shutil.copytree(
        TEMPLATE_SCAFFOLD, job_folder,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('node_modules', '.git')
    )

    with open(os.path.join(job_folder, "prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)

    attachments = []
    if uploaded_files:
        attachments = _save_uploads_to_job(job_folder, uploaded_files)

    with open(os.path.join(job_folder, "meta.json"), "w") as f:
        json.dump({"user_id": user_id, "model": model}, f)

    if attachments:
        with open(os.path.join(job_folder, "attachments.json"), "w") as f:
            json.dump(attachments, f)

    with open(os.path.join(job_folder, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"state": "running", "created_at": time.time()}, f)

    anthropic_model = get_anthropic_model(model)

    proc = subprocess.Popen(
        ["python3", ENGINE_SCRIPT, "--workspace", job_folder, "--model", anthropic_model],
        cwd=job_folder,
        preexec_fn=os.setsid
    )

    with open(os.path.join(job_folder, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"state": "running", "created_at": time.time(), "pid": proc.pid}, f)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (job_id, user_id, title, state)
                VALUES (%s, %s, %s, 'running')
                ON CONFLICT (job_id) DO NOTHING
                """,
                (job_id, int(user_id), title)
            )
            conn.commit()
    finally:
        conn.close()

    return jsonify({"job_id": job_id}), 200


# ------------------------------------------------------------------ #
#  Generation — follow-up turns                                        #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/message', methods=['POST'])
@token_required
def job_message(user_id, job_id):
    if request.content_type and 'multipart/form-data' in request.content_type:
        message        = (request.form.get("message", "") or "").strip()
        model          = request.form.get("model", None)
        uploaded_files = request.files.getlist("files")
    else:
        data           = request.get_json() or {}
        message        = data.get("message", "").strip()
        model          = data.get("model", None)
        uploaded_files = []

    if not message:
        return jsonify({"error": "message required"}), 400

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    attachments = []
    if uploaded_files:
        attachments = _save_uploads_to_job(job_folder, uploaded_files)
        if attachments:
            with open(os.path.join(job_folder, "attachments.json"), "w") as f:
                json.dump(attachments, f)

    meta_path     = os.path.join(job_folder, "meta.json")
    current_model = "hb-6"
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            current_model = meta.get("model", "hb-6")
        except Exception:
            pass

    if model:
        plan = _get_user_plan(user_id)
        if not is_model_allowed(plan, model):
            return jsonify({"error": f"Your {plan} plan doesn't include access to this model. Please upgrade."}), 403
        try:
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            meta["model"] = model
            with open(meta_path, "w") as f:
                json.dump(meta, f)
            current_model = model
        except Exception:
            pass

    conn = get_db()
    try:
        if not check_and_reserve(conn, int(user_id)):
            return jsonify({"error": "Not enough credits. Please subscribe or wait for your daily refresh."}), 402
        running = count_running_jobs(conn, int(user_id))
        if running >= 3:
            return jsonify({"error": "You already have 3 projects building. Wait for one to finish before starting another."}), 429
    finally:
        conn.close()

    try:
        from cleanup_manager import cancel_cleanup
        cancel_cleanup(job_id)
    except ImportError:
        pass

    state_path = os.path.join(job_folder, "state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state_data = json.load(f)
        if state_data.get("state") == "running":
            return jsonify({"error": "Job is still running"}), 409

    lock_path = os.path.join(job_folder, "credits_processed.lock")
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except Exception:
            pass

    cancelled_path = os.path.join(job_folder, "cancelled.lock")
    if os.path.exists(cancelled_path):
        try:
            os.remove(cancelled_path)
        except Exception:
            pass

    messages_path = os.path.join(job_folder, "messages.jsonl")
    if os.path.exists(messages_path):
        try:
            lines = []
            with open(messages_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(line)
            if lines:
                try:
                    last_msg = json.loads(lines[-1])
                    if last_msg.get("role") == "assistant":
                        if state_data.get("state") == "failed" and state_data.get("error") == "Cancelled by user":
                            lines.pop()
                            with open(messages_path, "w", encoding="utf-8") as f:
                                for line in lines:
                                    f.write(line + "\n")
                            print(f"[job_message] Removed stale assistant message after cancel for {job_id}")
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            print(f"[job_message] Error cleaning messages: {e}")

    anthropic_model = get_anthropic_model(current_model)

    proc = subprocess.Popen(
        ["python3", ENGINE_SCRIPT, "--workspace", job_folder,
         "--message", message, "--model", anthropic_model],
        cwd=job_folder,
        preexec_fn=os.setsid
    )

    with open(state_path, "w") as f:
        json.dump({"state": "running", "updated_at": time.time(), "pid": proc.pid}, f)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET state = 'running', updated_at = NOW() WHERE job_id = %s",
                (job_id,)
            )
            conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "running"}), 200


# ------------------------------------------------------------------ #
#  Job status + messages                                               #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/status', methods=['GET'])
@token_required
def job_status(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    state_path = os.path.join(job_folder, "state.json")
    state_data = {}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state_data = json.load(f)

    cancelled_path = os.path.join(job_folder, "cancelled.lock")
    if os.path.exists(cancelled_path):
        state_data["state"] = "failed"

    if state_data.get("state") == "completed":
        _process_credits_deduction(job_id, job_folder, user_id)

    messages      = []
    messages_path = os.path.join(job_folder, "messages.jsonl")
    if os.path.exists(messages_path):
        with open(messages_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    preview_url = _get_preview_url(job_id, job_folder)

    if preview_url:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET preview_url = %s, updated_at = NOW() WHERE job_id = %s",
                    (preview_url, job_id)
                )
                conn.commit()
        finally:
            conn.close()

    conn = get_db()
    try:
        credits_info = get_balance(conn, int(user_id))
    finally:
        conn.close()

    progress      = []
    progress_path = os.path.join(job_folder, "progress.json")
    if os.path.exists(progress_path):
        try:
            with open(progress_path) as f:
                progress = json.load(f)
        except (json.JSONDecodeError, IOError):
            progress = []

    meta_path = os.path.join(job_folder, "meta.json")
    job_model = "hb-6"
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            job_model = meta.get("model", "hb-6")
        except Exception:
            pass

    published_url = None
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT published_url FROM jobs WHERE job_id = %s", (job_id,))
            pub_row = cur.fetchone()
            if pub_row:
                published_url = pub_row.get("published_url")
    finally:
        conn.close()

    backend_requested  = False
    backend_req_path   = os.path.join(job_folder, "backend_requested.json")
    if os.path.exists(backend_req_path):
        backend_requested = True

    return jsonify({
        "job_id":            job_id,
        "state":             state_data.get("state", "unknown"),
        "build_ok":          state_data.get("build_ok", False),
        "code_changed":      state_data.get("code_changed", False),
        "error":             state_data.get("error"),
        "messages":          messages,
        "preview_url":       preview_url,
        "credits_balance":   credits_info["balance"],
        "progress":          progress,
        "model":             job_model,
        "plan":              credits_info.get("plan", "free"),
        "published_url":     published_url,
        "backend_requested": backend_requested,
    }), 200


# ------------------------------------------------------------------ #
#  Jobs list                                                           #
# ------------------------------------------------------------------ #

@auth_bp.route('/jobs', methods=['GET'])
@token_required
def list_jobs(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, title, state, preview_url, created_at
                FROM jobs
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (int(user_id),)
            )
            rows = cur.fetchall()
        return jsonify({"jobs": [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ------------------------------------------------------------------ #
#  Template cloning                                                    #
# ------------------------------------------------------------------ #

@auth_bp.route('/template/clone', methods=['POST'])
@token_required
def clone_template(user_id):
    data        = request.get_json() or {}
    template_id = data.get("template_id", "").strip()

    if template_id not in TEMPLATE_JOB_IDS:
        return jsonify({"error": "Invalid template"}), 400

    template_folder = os.path.join(OUTPUTS_DIR, template_id)
    if not os.path.isdir(template_folder):
        return jsonify({"error": "Template not found on disk"}), 404

    new_job_id = str(uuid.uuid4())[:8]
    new_folder = os.path.join(OUTPUTS_DIR, new_job_id)
    while os.path.exists(new_folder):
        new_job_id = str(uuid.uuid4())[:8]
        new_folder = os.path.join(OUTPUTS_DIR, new_job_id)

    shutil.copytree(
        template_folder, new_folder,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('node_modules', '.git', '__pycache__',
                                       'deduct_credits.json', 'meta.json',
                                       'preview_port.txt')
    )

    with open(os.path.join(new_folder, "meta.json"), "w") as f:
        json.dump({"user_id": user_id, "cloned_from": template_id, "model": "hb-6"}, f)

    with open(os.path.join(new_folder, "state.json"), "w") as f:
        json.dump({"state": "completed", "cloned_from": template_id, "updated_at": time.time()}, f)

    messages_path = os.path.join(new_folder, "messages.jsonl")
    if os.path.exists(messages_path):
        os.remove(messages_path)

    dist_dir    = os.path.join(new_folder, "dist")
    preview_url = f"/auth/preview/{new_job_id}/" if os.path.isdir(dist_dir) else None
    title       = TEMPLATE_JOB_IDS[template_id]

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (job_id, user_id, title, state, preview_url)
                VALUES (%s, %s, %s, 'completed', %s)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (new_job_id, int(user_id), title, preview_url)
            )
            conn.commit()
    finally:
        conn.close()

    return jsonify({
        "job_id":      new_job_id,
        "title":       title,
        "preview_url": preview_url,
        "state":       "completed",
    }), 200


@auth_bp.route('/templates', methods=['GET'])
def list_templates():
    templates = []
    for job_id, title in TEMPLATE_JOB_IDS.items():
        templates.append({
            "job_id":      job_id,
            "title":       title,
            "preview_url": f"/auth/preview/{job_id}/",
        })
    return jsonify({"templates": templates}), 200


# ------------------------------------------------------------------ #
#  Source files — for the code viewer                                  #
# ------------------------------------------------------------------ #

INTERNAL_FILES = {
    "state.json", "messages.jsonl", "meta.json", "prompt.txt",
    "preview_port.txt", "deduct_credits.json", "Files_list.txt",
    "progress.json", "attachments.json", "credits_processed.lock",
    "cancelled.lock", "backend_requested.json", "backend_approved.json",
    "backend_denied.json", "partial_deduction.json",
    "console_logs.json", "build_output.json",   # diagnostic files
}

SKIP_DIRS = {"node_modules", "dist", ".git", "__pycache__", "uploads"}

DIR_ORDER = {"src": 0, "public": 1}


def _collect_project_files(job_folder):
    files = []
    seen  = set()

    def _add(abs_path, rel_path):
        if rel_path in seen:
            return
        seen.add(rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            files.append({"path": rel_path, "content": content})
        except Exception:
            pass

    for root, dirs, filenames in os.walk(job_folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in filenames:
            if filename in INTERNAL_FILES:
                continue
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, job_folder)

            parts = rel_path.replace("\\", "/").split("/")
            if len(parts) == 1:
                allowed_root = {
                    "package.json", "package-lock.json",
                    "vite.config.ts", "vite.config.js",
                    "tailwind.config.ts", "tailwind.config.js",
                    "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json",
                    "postcss.config.js", "postcss.config.cjs",
                    "eslint.config.js", "eslint.config.ts",
                    ".eslintrc", ".eslintrc.js", ".eslintrc.json",
                    ".prettierrc", ".prettierrc.js", ".prettierrc.json",
                    ".env.example", "index.html", "README.md",
                }
                if filename not in allowed_root:
                    continue

            _add(abs_path, rel_path)

    def _sort_key(f):
        p   = f["path"].replace("\\", "/")
        top = p.split("/")[0]
        return (DIR_ORDER.get(top, 2), p)

    files.sort(key=_sort_key)
    return files


@auth_bp.route('/job/<job_id>/files', methods=['GET'])
@token_required
def get_job_files(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    files = _collect_project_files(job_folder)
    return jsonify({"files": files}), 200


# ------------------------------------------------------------------ #
#  ZIP download                                                        #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/download', methods=['GET'])
@token_required
def download_job_zip(user_id, job_id):
    import io
    import zipfile
    from flask import Response

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM jobs WHERE job_id = %s AND user_id = %s",
                        (job_id, int(user_id)))
            row = cur.fetchone()
    finally:
        conn.close()

    project_title = (row["title"] if row else job_id).strip()
    safe_title    = "".join(c if c.isalnum() or c in "-_ " else "" for c in project_title)
    safe_title    = safe_title.strip().replace(" ", "-") or job_id
    zip_filename  = f"{safe_title}.zip"

    files = _collect_project_files(job_folder)

    has_readme     = any(f["path"].lower() == "readme.md" for f in files)
    readme_content = None
    if not has_readme:
        readme_content = f"""# {project_title}

Generated by [The Hustler Bot](https://thehustlerbot.com) — AI-powered app builder.

## Getting Started

### Prerequisites
- [Node.js](https://nodejs.org/) v18 or higher
- npm (comes with Node.js)

### Setup

```bash
# 1. Install dependencies
npm install

# 2. Start the development server
npm run dev
```

Then open [http://localhost:5173](http://localhost:5173) in your browser.

### Build for production

```bash
npm run build
```

The production build will be in the `dist/` folder.

---
*Built with React + Vite + Tailwind CSS*
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            zf.writestr(f"{safe_title}/{file['path']}", file["content"])
        if readme_content:
            zf.writestr(f"{safe_title}/README.md", readme_content)

    buf.seek(0)

    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
            "Content-Length":      str(len(buf.getvalue())),
        }
    )


# ------------------------------------------------------------------ #
#  Preview serving                                                     #
# ------------------------------------------------------------------ #

@auth_bp.route('/preview/<job_id>/', defaults={'filename': ''})
@auth_bp.route('/preview/<job_id>/<path:filename>')
def serve_preview(job_id, filename):
    from flask import make_response

    dist_dir = os.path.join(OUTPUTS_DIR, job_id, "dist")
    if not os.path.isdir(dist_dir):
        return jsonify({"error": "Preview not ready yet"}), 404

    if filename:
        file_path = os.path.join(dist_dir, filename)
        if os.path.isfile(file_path):
            return send_from_directory(dist_dir, filename)

    from flask import request as flask_request
    base    = flask_request.host_url.rstrip("/")
    wrapper = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Preview</title>
<style>
  * {{ margin: 0; padding: 0; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; }}
  iframe {{ width: 100%; height: 100%; border: none; }}
</style>
</head><body>
<iframe src="{base}/auth/preview-raw/{job_id}/" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
</body></html>"""

    resp = make_response(wrapper)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers.pop("X-Frame-Options", None)
    return resp


@auth_bp.route('/preview-raw/<job_id>/', defaults={'filename': 'index.html'})
@auth_bp.route('/preview-raw/<job_id>/<path:filename>')
def serve_preview_raw(job_id, filename):
    from flask import make_response

    dist_dir = os.path.join(OUTPUTS_DIR, job_id, "dist")
    if not os.path.isdir(dist_dir):
        return jsonify({"error": "Preview not ready yet"}), 404

    if filename and filename != "index.html":
        file_path = os.path.join(dist_dir, filename)
        if os.path.isfile(file_path):
            return send_from_directory(dist_dir, filename)

    index_path = os.path.join(dist_dir, "index.html")
    if not os.path.isfile(index_path):
        return jsonify({"error": "Preview not ready yet"}), 404

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    asset_base = f"/auth/preview/{job_id}/"
    html = html.replace('src="./assets/',    f'src="{asset_base}assets/')
    html = html.replace("src='./assets/",    f"src='{asset_base}assets/")
    html = html.replace('href="./assets/',   f'href="{asset_base}assets/')
    html = html.replace("href='./assets/",   f"href='{asset_base}assets/")
    html = html.replace('href="./favicon',   f'href="{asset_base}favicon')
    html = html.replace('href="./placeholder', f'href="{asset_base}placeholder')

    from flask import request as flask_request
    base     = flask_request.host_url.rstrip("/")
    boot_fix = f"""<script>
history.replaceState(null, '', '/');
(function() {{
  var _endpoint = "{base}/auth/preview/{job_id}/console-log";
  var _buf = [];
  var _flush = function() {{
    if (!_buf.length) return;
    var logs = _buf.splice(0);
    try {{ fetch(_endpoint, {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{logs:logs}}), keepalive:true}}); }} catch(e) {{}}
  }};
  var _capture = function(level) {{
    return function() {{
      var msg = Array.from(arguments).map(function(a) {{
        try {{ return typeof a === "object" ? JSON.stringify(a) : String(a); }} catch(e) {{ return String(a); }}
      }}).join(" ");
      _buf.push({{level:level, msg:msg, ts:Date.now()}});
      if (_buf.length >= 5) _flush();
    }};
  }};
  console.error = _capture("error");
  console.warn  = _capture("warn");
  window.addEventListener("error", function(e) {{
    _buf.push({{level:"error", msg: e.message + " (" + e.filename + ":" + e.lineno + ")", ts:Date.now()}});
    _flush();
  }});
  window.addEventListener("unhandledrejection", function(e) {{
    _buf.push({{level:"error", msg:"Unhandled promise rejection: " + String(e.reason), ts:Date.now()}});
    _flush();
  }});
  setInterval(_flush, 3000);
}})();
</script>
"""
    html = html.replace("<head>", "<head>\n" + boot_fix, 1)

    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers.pop("X-Frame-Options", None)
    return resp


# ------------------------------------------------------------------ #
#  Supabase email confirmation callback                                #
# ------------------------------------------------------------------ #

@auth_bp.route('/supabase-callback/<job_id>')
def supabase_auth_callback(job_id):
    from flask import make_response

    html = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Email Verified</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100vh;
    background: #ffffff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    padding: 24px;
  }
  .card {
    max-width: 400px;
    width: 100%;
    background: #ffffff;
    border: 1px solid #e5e5e5;
    border-radius: 16px;
    padding: 48px 40px 40px;
    text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.06);
  }
  .icon-wrap {
    width: 64px; height: 64px; border-radius: 50%;
    background: #f0fdf4; border: 1px solid #bbf7d0;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 24px;
  }
  .checkmark {
    width: 28px; height: 28px; stroke: #16a34a;
    stroke-width: 2.5; fill: none;
    stroke-linecap: round; stroke-linejoin: round;
  }
  h1 { font-size: 1.375rem; font-weight: 700; color: #111111; margin-bottom: 10px; }
  p { font-size: 0.9rem; color: #666666; line-height: 1.6; margin-bottom: 32px; }
  .btn {
    display: inline-block; padding: 12px 28px;
    background: #111111; color: #ffffff;
    font-size: 0.875rem; font-weight: 600;
    text-decoration: none; border-radius: 8px;
    transition: background 0.15s ease;
  }
  .btn:hover { background: #333333; }
  .divider { width: 40px; height: 2px; background: #e5e5e5; margin: 28px auto 0; border-radius: 2px; }
  .footer { margin-top: 20px; font-size: 0.75rem; color: #aaaaaa; }
</style>
</head>
<body>
  <div class="card">
    <div class="icon-wrap">
      <svg class="checkmark" viewBox="0 0 24 24">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
    </div>
    <h1>Email Verified</h1>
    <p>Your email address has been confirmed.<br>You can now sign in to your account.</p>
    <a href="https://thehustlerbot.com/login" class="btn">Go to Sign In</a>
    <div class="divider"></div>
    <p class="footer">The Hustler Bot &nbsp;·&nbsp; You can close this tab after signing in.</p>
  </div>
</body>
</html>"""

    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# ------------------------------------------------------------------ #
#  Backend ready / denied signals                                      #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/backend-ready', methods=['POST'])
@token_required
def backend_ready_signal(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    req_path = os.path.join(job_folder, "backend_requested.json")
    if os.path.exists(req_path):
        os.remove(req_path)

    approved_path = os.path.join(job_folder, "backend_approved.json")
    with open(approved_path, "w") as f:
        json.dump({"approved": True, "ts": time.time()}, f)

    return jsonify({"ok": True}), 200


@auth_bp.route('/job/<job_id>/backend-denied', methods=['POST'])
@token_required
def backend_denied_signal(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    req_path = os.path.join(job_folder, "backend_requested.json")
    if os.path.exists(req_path):
        os.remove(req_path)

    denied_path = os.path.join(job_folder, "backend_denied.json")
    with open(denied_path, "w") as f:
        json.dump({"denied": True, "ts": time.time()}, f)

    return jsonify({"ok": True}), 200


# ------------------------------------------------------------------ #
#  Console log receiver — captures runtime errors from preview iframe  #
# ------------------------------------------------------------------ #

@auth_bp.route('/preview/<job_id>/console-log', methods=['POST'])
def preview_console_log(job_id):
    """Receives runtime console errors POSTed from the previewed app."""
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return '', 204

    try:
        data = request.get_json(silent=True) or {}
        logs = data.get("logs", [])
        if not logs:
            return '', 204

        log_path = os.path.join(job_folder, "console_logs.json")
        existing = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        existing.extend(logs)
        existing = existing[-100:]   # Keep last 100 entries only

        with open(log_path, "w") as f:
            json.dump(existing, f)
    except Exception:
        pass

    return '', 204


# ------------------------------------------------------------------ #
#  Health check                                                        #
# ------------------------------------------------------------------ #

@auth_bp.route('/generate-test', methods=['GET'])
def generate_test():
    return jsonify({"message": "Backend is ready for generation"})


# ------------------------------------------------------------------ #
#  Cancel job                                                          #
# ------------------------------------------------------------------ #

@auth_bp.route('/job/<job_id>/cancel', methods=['POST'])
@token_required
def cancel_job(user_id, job_id):
    try:
        job_folder = os.path.join(OUTPUTS_DIR, job_id)

        if os.path.isdir(job_folder):
            state_path = os.path.join(job_folder, "state.json")
            saved_pid  = None
            if os.path.exists(state_path):
                try:
                    with open(state_path) as f:
                        old_state = json.load(f)
                    saved_pid = old_state.get("pid")
                except Exception:
                    pass

            cancelled_path = os.path.join(job_folder, "cancelled.lock")
            with open(cancelled_path, "w") as f:
                json.dump({"cancelled_at": time.time(), "user_id": user_id}, f)

            _kill_job_process(job_folder)

            time.sleep(1)

            partial_file = os.path.join(job_folder, "partial_deduction.json")
            deduct_file  = os.path.join(job_folder, "deduct_credits.json")

            target_file = None
            if os.path.exists(deduct_file):
                target_file = deduct_file
            elif os.path.exists(partial_file):
                target_file = partial_file

            if target_file:
                try:
                    with open(target_file) as f:
                        entries = json.load(f)

                    meta_path = os.path.join(job_folder, "meta.json")
                    model     = "hb-6-pro"
                    if os.path.exists(meta_path):
                        try:
                            with open(meta_path) as f:
                                meta = json.load(f)
                            model = meta.get("model", "hb-6-pro")
                        except Exception:
                            pass

                    conn = get_db()
                    try:
                        for i, entry in enumerate(entries):
                            entry_model = entry.get("model", model)
                            deduct_credits(
                                conn,
                                user_id            = int(user_id),
                                job_id             = job_id,
                                turn               = i + 1,
                                tokens_used        = int(entry.get("tokens_used", 0)),
                                input_tokens       = int(entry.get("input_tokens", 0)),
                                output_tokens      = int(entry.get("output_tokens", 0)),
                                cache_write_tokens = int(entry.get("cache_write_tokens", 0)),
                                cache_read_tokens  = int(entry.get("cache_read_tokens", 0)),
                                model              = entry_model,
                            )
                    finally:
                        conn.close()

                    for f_path in [deduct_file, partial_file]:
                        if os.path.exists(f_path):
                            try:
                                os.remove(f_path)
                            except Exception:
                                pass

                    lock_path = os.path.join(job_folder, "credits_processed.lock")
                    try:
                        with open(lock_path, "w") as f:
                            json.dump({"processed_at": time.time(), "source": "cancel"}, f)
                    except Exception:
                        pass

                except Exception as e:
                    print(f"[cancel] partial deduction error: {e}")

            with open(state_path, "w") as f:
                json.dump({
                    "state":      "failed",
                    "error":      "Cancelled by user",
                    "updated_at": time.time()
                }, f)

            progress_path = os.path.join(job_folder, "progress.json")
            if os.path.exists(progress_path):
                try:
                    os.remove(progress_path)
                except Exception:
                    pass

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET state = 'failed', updated_at = NOW() WHERE job_id = %s AND user_id = %s",
                    (job_id, int(user_id))
                )
                conn.commit()
        finally:
            conn.close()

        return jsonify({"status": "cancelled"})
    except Exception as e:
        print(f"[cancel] Error: {e}")
        return jsonify({"error": str(e)}), 500