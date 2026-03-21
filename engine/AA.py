import argparse, os, json, time, traceback, subprocess, re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from Agent5 import create_generator
from File_state import FileState
from dotenv import load_dotenv
load_dotenv()

try:
    from credits import tokens_to_credits
except ImportError:
    def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, model="hb-6-pro"):
        pricing = {
            "hb-6":     {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
            "hb-6-pro": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
            "hb-7":     {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
        }
        p = pricing.get(model, pricing["hb-6-pro"])
        cost_dollars = (
            (input_tokens       * p["input"]) +
            (output_tokens      * p["output"]) +
            (cache_write_tokens * p["cache_write"]) +
            (cache_read_tokens  * p["cache_read"])
        ) / 1_000_000
        return round(cost_dollars / 0.01, 2)


ANTHROPIC_TO_HB = {
    "claude-haiku-4-5-20251001": "hb-6",
    "claude-sonnet-4-6":        "hb-6-pro",
    "claude-opus-4-6":          "hb-7",
}


def write_state(workspace, state, extra=None):
    data = {"state": state, "updated_at": time.time()}
    if extra:
        data.update(extra)
    with open(os.path.join(workspace, "state.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def append_message(workspace, role, text, token_breakdown=None, credits=None, attachments=None):
    entry = {"role": role, "text": text, "ts": time.time()}
    if attachments:
        entry["attachments"] = attachments
    if token_breakdown:
        entry["token_breakdown"] = token_breakdown
        entry["tokens_used"]     = sum(token_breakdown.values())
        entry["credits_used"]    = credits
    with open(os.path.join(workspace, "messages.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(workspace):
    path = os.path.join(workspace, "messages.jsonl")
    if not os.path.exists(path):
        return []
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return messages


def _save_build_output(workspace, success, stdout, stderr, phase):
    data = {
        "build_success": success,
        "build_phase":   phase,
        "build_stdout":  (stdout or "")[-3000:],
        "build_stderr":  (stderr or "")[-3000:],
        "build_ts":      time.time(),
    }
    state_path = os.path.join(workspace, "state.json")
    existing = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.update(data)
    with open(state_path, "w") as f:
        json.dump(existing, f)

    build_output_path = os.path.join(workspace, "build_output.json")
    with open(build_output_path, "w") as f:
        json.dump(data, f)


# ══════════════════════════════════════════════════════════════════════════════
#  PRE-BUILD: Verify image imports exist on disk
# ══════════════════════════════════════════════════════════════════════════════

def verify_image_imports(workspace):
    """
    Scan all .tsx/.jsx/.ts/.js files for image imports.
    If an imported image file doesn't exist on disk, replace the import
    with a comment and set the variable to undefined.
    Returns a list of files that were patched.
    """
    src_dir = os.path.join(workspace, "src")
    if not os.path.isdir(src_dir):
        return []

    patched_files = []

    # Pattern matches: import someName from '../assets/something.jpg'
    import_pattern = re.compile(
        r"""^(import\s+(\w+)\s+from\s+['"])(\.\.?/[^'"]*\.(jpg|jpeg|png|webp|gif|svg))(['"];?\s*)$""",
        re.MULTILINE
    )

    for root, dirs, filenames in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in {"node_modules", "dist", ".git"}]
        for fname in filenames:
            if not fname.endswith(('.tsx', '.jsx', '.ts', '.js')):
                continue

            filepath = os.path.join(root, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue

            original = content
            imports_to_remove = []

            for match in import_pattern.finditer(content):
                var_name = match.group(2)
                rel_path = match.group(3)

                # Resolve to absolute path
                file_dir = os.path.dirname(filepath)
                abs_image_path = os.path.normpath(os.path.join(file_dir, rel_path))

                if not os.path.isfile(abs_image_path):
                    print(f"[verify_images] MISSING: {rel_path} (imported in {fname} as '{var_name}')")
                    imports_to_remove.append({
                        "full_match": match.group(0),
                        "var_name": var_name,
                        "rel_path": rel_path,
                    })

            if not imports_to_remove:
                continue

            for imp in imports_to_remove:
                content = content.replace(
                    imp["full_match"],
                    f"// [auto-removed] missing image: {imp['rel_path']}\n"
                    f"const {imp['var_name']} = undefined;\n"
                )

            if content != original:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                patched_files.append(fname)
                print(f"[verify_images] Patched {fname}: removed {len(imports_to_remove)} broken image import(s)")

    return patched_files


def build_project(workspace):
    try:
        # ── Pre-build: patch any broken image imports ─────────────────
        patched = verify_image_imports(workspace)
        if patched:
            print(f"[build] Pre-build: patched {len(patched)} file(s) with missing image imports")

        install = subprocess.run(
            ["npm", "install", "--include=dev", "--legacy-peer-deps"],
            cwd=workspace, capture_output=True, text=True, timeout=300
        )
        print(f"[build] npm install returncode={install.returncode}")
        print(f"[build] npm install stdout={install.stdout[-500:] if install.stdout else ''}")
        print(f"[build] npm install stderr={install.stderr[-500:] if install.stderr else ''}")
        if install.returncode != 0:
            _save_build_output(workspace, False, install.stdout, install.stderr, "npm_install")
            print(f"[build] npm install failed")
            return False

        result = subprocess.run(
            ["./node_modules/.bin/vite", "build"], cwd=workspace,
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            _save_build_output(workspace, False, result.stdout, result.stderr, "vite_build")
            print(f"[build] vite build failed:\n{result.stderr}")
            return False

        _save_build_output(workspace, True, result.stdout, result.stderr, "vite_build")

        port_file = os.path.join(workspace, "preview_port.txt")
        if os.path.exists(port_file):
            os.remove(port_file)

        print(f"[build] success — preview served via Flask /auth/preview/ route")

        nm_path = os.path.join(workspace, "node_modules")
        if os.path.isdir(nm_path):
            import shutil
            shutil.rmtree(nm_path, ignore_errors=True)
            print(f"[build] node_modules deleted to save disk")

        return True
    except Exception as e:
        _save_build_output(workspace, False, "", str(e), "exception")
        print(f"[build] exception: {e}")
        return False


def save_deduction(workspace, token_breakdown, credits_used):
    path     = os.path.join(workspace, "deduct_credits.json")
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)

    existing.append({
        "input_tokens":       token_breakdown.get("input", 0),
        "output_tokens":      token_breakdown.get("output", 0),
        "cache_write_tokens": token_breakdown.get("cache_write", 0),
        "cache_read_tokens":  token_breakdown.get("cache_read", 0),
        "tokens_used":        sum(token_breakdown.values()),
        "credits_used":       credits_used,
        "ts":                 time.time(),
    })

    with open(path, "w") as f:
        json.dump(existing, f)


def _heartbeat_db(workspace):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        meta_path = os.path.join(workspace, "meta.json")
        if not os.path.exists(meta_path):
            return
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return
        job_id = os.path.basename(workspace)
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET updated_at = NOW() WHERE job_id = %s",
                (job_id,)
            )
            conn.commit()
        conn.close()
    except Exception:
        pass


def write_progress(workspace, entry):
    path = os.path.join(workspace, "progress.json")
    existing = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = []

    entry["ts"] = time.time()
    existing.append(entry)

    if len(existing) > 50:
        existing = existing[-50:]

    with open(path, "w") as f:
        json.dump(existing, f)

    _heartbeat_db(workspace)


def clear_progress(workspace):
    path = os.path.join(workspace, "progress.json")
    if os.path.exists(path):
        os.remove(path)


TOOL_ACTIONS = {
    "write_file":          "writing",
    "edit_file":           "editing",
    "read_file":           "reading",
    "files_list":          "scanning",
    "run_install_command": "installing",
    "generate_image":      "generating image",
    "edit_image":          "editing image",
    "delete_file":         "deleting",
    "rename_file":         "renaming",
    "search_files":        "searching",
    "request_backend":     "requesting backend",
    "request_stripe":      "requesting stripe",
    "request_ai":          "requesting ai",
    "read_console_logs":   "reading console logs",
    "read_package_json":   "checking dependencies",
}


def _guess_lang(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    lang_map = {
        "js": "javascript", "jsx": "javascript", "ts": "typescript", "tsx": "typescript",
        "css": "css", "html": "html", "json": "json", "md": "markdown",
        "py": "python", "sh": "bash",
    }
    return lang_map.get(ext, "plaintext")


def make_hooks(workspace):
    file_count = {"written": 0, "read": 0}

    def on_thinking(turn, detail):
        write_progress(workspace, {"action": "thinking", "detail": detail})

    def on_tool_start(name, args):
        action    = TOOL_ACTIONS.get(name, "processing")
        file_path = args.get("path", None) if isinstance(args, dict) else None

        entry = {"action": action}
        if file_path:
            entry["file"] = file_path

        if name == "request_backend":
            reason = args.get("reason", "") if isinstance(args, dict) else ""
            entry["detail"] = f"Requesting backend: {reason[:200]}"
        elif name == "request_stripe":
            reason = args.get("reason", "") if isinstance(args, dict) else ""
            entry["detail"] = f"Requesting Stripe: {reason[:200]}"
        elif name == "request_ai":
            reason = args.get("reason", "") if isinstance(args, dict) else ""
            entry["detail"] = f"Setting up AI integration: {reason[:200]}"
        elif name == "run_install_command":
            entry["detail"] = args.get("command", "")[:200]
        elif name == "generate_image":
            target_path = args.get("target_path", "image")
            model_name  = args.get("model", "flux.schnell")
            w = args.get("width", 1024)
            h = args.get("height", 768)
            entry["detail"] = f"Generating image: {target_path} ({w}x{h}, {model_name})"
            entry["file"]   = target_path
        elif name == "edit_image":
            target_path    = args.get("target_path", "image")
            prompt_preview = args.get("prompt", "")[:160]
            entry["detail"] = f"Editing image → {target_path}: {prompt_preview}..."
            entry["file"]   = target_path
        elif name == "delete_file":
            del_path = args.get("path", "")
            entry["detail"] = f"Deleting {del_path}"
            entry["file"]   = del_path
        elif name == "rename_file":
            orig = args.get("original_path", "")
            new  = args.get("new_path", "")
            entry["detail"] = f"Renaming {orig} → {new}"
            entry["file"]   = new
        elif name == "search_files":
            query      = args.get("query", "")[:160]
            search_dir = args.get("search_dir", "src")
            entry["detail"] = f"Searching for '{query}' in {search_dir}"
        elif name == "read_console_logs":
            entry["detail"] = "Reading runtime console logs..."
        elif name == "read_package_json":
            entry["detail"] = "Checking installed dependencies..."
        elif file_path:
            entry["detail"] = f"{action.capitalize()} {file_path}"
        else:
            entry["detail"] = f"{action.capitalize()} project files..."

        if name == "write_file" and isinstance(args, dict):
            content = args.get("content", "")
            lines   = content.split("\n")
            if len(lines) > 60:
                lines = lines[-60:]
            entry["code"]  = "\n".join(lines)
            entry["lang"]  = _guess_lang(file_path or "")
            file_count["written"] += 1
        elif name == "edit_file" and isinstance(args, dict):
            new_str = args.get("new_str", "")
            lines   = new_str.split("\n")
            if len(lines) > 60:
                lines = lines[-60:]
            entry["code"]  = "\n".join(lines)
            entry["lang"]  = _guess_lang(file_path or "")
            file_count["written"] += 1

        entry["files_written"] = file_count["written"]
        write_progress(workspace, entry)

    def on_tool_end(name, args, result):
        pass

    def on_text(text):
        import re
        cleaned = text.strip()
        if len(cleaned) < 8:
            return
        cleaned = re.sub(r'^[─═\-\*]{3,}\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^#{1,4}\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\[.?\]\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\n{2,}', '\n', cleaned).strip()
        if len(cleaned) < 8:
            return
        snippet = cleaned[:300] + ("..." if len(cleaned) > 150 else "")
        write_progress(workspace, {"action": "planning", "detail": snippet})

    def on_rate_limit(attempt, delay):
        write_progress(workspace, {"action": "waiting", "detail": f"Rate limited, retrying in {delay}s..."})

    return {
        "on_thinking":   on_thinking,
        "on_tool_start": on_tool_start,
        "on_tool_end":   on_tool_end,
        "on_text":       on_text,
        "on_rate_limit": on_rate_limit,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--message",   default=None)
    parser.add_argument("--model",     default=None)
    args = parser.parse_args()

    WORKSPACE = args.workspace
    os.chdir(WORKSPACE)

    anthropic_model = args.model
    if not anthropic_model:
        meta_path = os.path.join(WORKSPACE, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                hb_model = meta.get("model", "hb-6")
                model_map = {
                    "hb-6":     "claude-haiku-4-5-20251001",
                    "hb-6-pro": "claude-sonnet-4-6",
                    "hb-7":     "claude-opus-4-6",
                }
                anthropic_model = model_map.get(hb_model, "claude-haiku-4-5-20251001")
            except Exception:
                anthropic_model = "claude-haiku-4-5-20251001"
        else:
            anthropic_model = "claude-haiku-4-5-20251001"

    hb_model = ANTHROPIC_TO_HB.get(anthropic_model, "hb-6-pro")
    print(f"[AA] Using model: {anthropic_model} (HB: {hb_model})")

    try:
        write_state(WORKSPACE, "running")
        clear_progress(WORKSPACE)

        # Clear stale console logs from previous builds
        console_log_path = os.path.join(WORKSPACE, "console_logs.json")
        if os.path.exists(console_log_path):
            try:
                os.remove(console_log_path)
            except Exception:
                pass

        write_progress(WORKSPACE, {
            "action": "starting",
            "detail": "Setting up your project...",
        })

        files_list      = FileState(False)
        supabase_config = None
        stripe_config   = None
        ai_config       = None

        meta_path_sb = os.path.join(WORKSPACE, "meta.json")
        if os.path.exists(meta_path_sb):
            try:
                with open(meta_path_sb) as f:
                    meta_sb = json.load(f)

                # ── Supabase ──────────────────────────────────────────
                if meta_sb.get("supabase_enabled"):
                    job_id = os.path.basename(WORKSPACE)
                    supabase_config = {
                        "url":              meta_sb.get("supabase_url", ""),
                        "anon_key":         meta_sb.get("supabase_anon_key", ""),
                        "service_role_key": meta_sb.get("supabase_service_role", os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")),
                        "project_ref":      meta_sb.get("supabase_project_ref", os.getenv("SUPABASE_PROJECT_REF", "")),
                        "preview_url":      f"https://entrepreneur-bot-backend.onrender.com/auth/preview-raw/{job_id}/",
                    }
                    print(f"[AA] Supabase enabled — project ref: {supabase_config['project_ref']}")

                # ── Stripe ────────────────────────────────────────────
                if meta_sb.get("stripe_enabled"):
                    job_id_str = os.path.basename(WORKSPACE)
                    stripe_config = {
                        "publishable_key": meta_sb.get("stripe_publishable_key", ""),
                        "proxy_url":       f"https://entrepreneur-bot-backend.onrender.com/stripe/job/{job_id_str}",
                    }
                    print(f"[AA] Stripe enabled — publishable key present: {bool(stripe_config['publishable_key'])}")

            except Exception as e:
                print(f"[AA] Error reading meta.json: {e}")

        # ── AI proxy — always available ───────────────────────────────
        app_token    = ""
        job_id_str   = os.path.basename(WORKSPACE)
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            import jwt as pyjwt

            db_url = os.getenv("DATABASE_URL")
            if db_url:
                db_conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
                with db_conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id, app_token FROM jobs WHERE job_id = %s",
                        (job_id_str,)
                    )
                    job_row = cur.fetchone()

                if job_row:
                    if job_row["app_token"]:
                        app_token = job_row["app_token"]
                        print(f"[AA] Reusing existing app token for job {job_id_str}")
                    else:
                        secret_key = os.getenv("SECRET_KEY", "supersecretkey")
                        app_token  = pyjwt.encode(
                            {
                                "scope":  "ai_proxy",
                                "job_id": job_id_str,
                                "sub":    str(job_row["user_id"]),
                            },
                            secret_key,
                            algorithm="HS256"
                        )
                        with db_conn.cursor() as cur:
                            cur.execute(
                                "UPDATE jobs SET app_token = %s WHERE job_id = %s",
                                (app_token, job_id_str)
                            )
                        db_conn.commit()
                        print(f"[AA] Generated new app token for job {job_id_str}")

                db_conn.close()
        except Exception as e:
            print(f"[AA] Warning: could not generate app token: {e}")

        ai_config = {
            "proxy_url": "https://entrepreneur-bot-backend.onrender.com/auth/ai/proxy",
            "app_token": app_token,
        }
        print(f"[AA] AI proxy always available — app token present: {bool(app_token)}")

        generator = create_generator(
            files_list_state = files_list,
            model            = anthropic_model,
            supabase_config  = supabase_config,
            workspace        = WORKSPACE,
            stripe_config    = stripe_config,
            ai_config        = ai_config,
        )

        write_progress(WORKSPACE, {
            "action": "thinking",
            "detail": "Reading project structure...",
        })

        history = load_history(WORKSPACE)
        for entry in history:
            role = entry.get("role")
            text = entry.get("text", "")
            if role in ("user", "assistant") and text:
                generator.messages.append({"role": role, "content": text})

        if args.message:
            user_message = args.message.strip()
        else:
            prompt_file = os.path.join(WORKSPACE, "prompt.txt")
            if not os.path.exists(prompt_file):
                raise Exception("prompt.txt not found and no --message provided")
            with open(prompt_file, "r", encoding="utf-8") as f:
                user_message = f.read().strip()

        if not user_message:
            raise Exception("Empty user message")

        # ── Load attachments ──────────────────────────────────────────
        attachments_path = os.path.join(WORKSPACE, "attachments.json")
        attachments = []
        if os.path.exists(attachments_path):
            try:
                with open(attachments_path) as f:
                    attachments = json.load(f)
                os.remove(attachments_path)
                print(f"[AA] Found {len(attachments)} attachment(s)")
            except Exception as e:
                print(f"[AA] Error reading attachments: {e}")
                attachments = []

        if attachments:
            import base64
            content_blocks = []

            for att in attachments:
                att_path   = os.path.join(WORKSPACE, att["path"])
                media_type = att.get("media_type", "")

                if media_type.startswith("image/") and media_type != "image/svg+xml":
                    try:
                        with open(att_path, "rb") as img_f:
                            img_data = base64.b64encode(img_f.read()).decode("utf-8")
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type":       "base64",
                                "media_type": media_type,
                                "data":       img_data,
                            }
                        })
                        print(f"[AA] Attached image: {att['filename']} ({media_type})")
                    except Exception as e:
                        print(f"[AA] Failed to read image {att_path}: {e}")

                elif media_type == "application/pdf":
                    try:
                        with open(att_path, "rb") as pdf_f:
                            pdf_data = base64.b64encode(pdf_f.read()).decode("utf-8")
                        content_blocks.append({
                            "type": "document",
                            "source": {
                                "type":       "base64",
                                "media_type": "application/pdf",
                                "data":       pdf_data,
                            }
                        })
                        print(f"[AA] Attached PDF: {att['filename']}")
                    except Exception as e:
                        print(f"[AA] Failed to read PDF {att_path}: {e}")

                else:
                    try:
                        with open(att_path, "r", encoding="utf-8") as txt_f:
                            text_content = txt_f.read()
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Attached file: {att['filename']}]\n{text_content}"
                        })
                        print(f"[AA] Attached text file: {att['filename']}")
                    except Exception as e:
                        print(f"[AA] Failed to read text file {att_path}: {e}")

            content_blocks.append({"type": "text", "text": user_message})
            chat_input = content_blocks
        else:
            chat_input = user_message

        user_attachments = None
        if attachments:
            user_attachments = [{"name": a["filename"], "type": a["media_type"]} for a in attachments]
        append_message(WORKSPACE, "user", user_message, attachments=user_attachments)

        write_progress(WORKSPACE, {
            "action": "thinking",
            "detail": "Planning your application...",
        })

        hooks = make_hooks(WORKSPACE)
        generator.on_thinking   = hooks["on_thinking"]
        generator.on_tool_start = hooks["on_tool_start"]
        generator.on_tool_end   = hooks["on_tool_end"]
        generator.on_text       = hooks["on_text"]
        generator.on_rate_limit = hooks["on_rate_limit"]

        output, token_breakdown, code_changed = generator.chat(chat_input)

        print(f"[AA] code_changed={code_changed} — {'will build' if code_changed else 'skipping build (text-only reply)'}")

        credits_used = tokens_to_credits(
            input_tokens       = token_breakdown["input"],
            output_tokens      = token_breakdown["output"],
            cache_write_tokens = token_breakdown["cache_write"],
            cache_read_tokens  = token_breakdown["cache_read"],
            model              = hb_model,
        )

        print(f"[credits] model={hb_model} breakdown={token_breakdown} → {credits_used} credits")

        append_message(WORKSPACE, "assistant", output,
                       token_breakdown=token_breakdown, credits=credits_used)

        save_deduction(WORKSPACE, token_breakdown, credits_used)

        partial_path = os.path.join(WORKSPACE, "partial_deduction.json")
        if os.path.exists(partial_path):
            os.remove(partial_path)

        build_ok = False
        if code_changed:
            write_progress(WORKSPACE, {
                "action": "building",
                "detail": "Installing dependencies & compiling...",
            })
            build_ok = build_project(WORKSPACE)

        clear_progress(WORKSPACE)

        write_state(WORKSPACE, "completed", {
            "build_ok":        build_ok,
            "code_changed":    code_changed,
            "token_breakdown": token_breakdown,
            "credits_used":    credits_used,
        })

    except Exception as e:
        clear_progress(WORKSPACE)
        write_state(WORKSPACE, "failed", {
            "error":     str(e),
            "traceback": traceback.format_exc(),
        })


if __name__ == "__main__":
    main()