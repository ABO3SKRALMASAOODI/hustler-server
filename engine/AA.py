import argparse, os, json, time, traceback, subprocess
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
    def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens):
        cost_dollars = (
            (input_tokens       * 3.00) +
            (output_tokens      * 15.00) +
            (cache_write_tokens * 3.75) +
            (cache_read_tokens  * 0.30)
        ) / 1_000_000
        # No markup — match credits.py MARKUP=1.0
        return round(cost_dollars / 0.01, 2)


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_state(workspace, state, extra=None):
    data = {"state": state, "updated_at": time.time()}
    if extra:
        data.update(extra)
    with open(os.path.join(workspace, "state.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def append_message(workspace, role, text, token_breakdown=None, credits=None):
    entry = {"role": role, "text": text, "ts": time.time()}
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


def build_project(workspace):
    """
    Run npm install + vite build, then delete node_modules to save disk.
    """
    try:
        install = subprocess.run(
            ["npm", "install", "--include=dev", "--legacy-peer-deps"],
            cwd=workspace, capture_output=True, text=True, timeout=300
        )
        print(f"[build] npm install returncode={install.returncode}")
        print(f"[build] npm install stdout={install.stdout[-500:] if install.stdout else ''}")
        print(f"[build] npm install stderr={install.stderr[-500:] if install.stderr else ''}")
        if install.returncode != 0:
            print(f"[build] npm install failed")
            return False

        result = subprocess.run(
            ["./node_modules/.bin/vite", "build"], cwd=workspace,
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"[build] vite build failed:\n{result.stderr}")
            return False

        port_file = os.path.join(workspace, "preview_port.txt")
        if os.path.exists(port_file):
            os.remove(port_file)

        print(f"[build] success — preview served via Flask /auth/preview/ route")

        # ── Delete node_modules immediately to save disk (~200MB per job) ──
        nm_path = os.path.join(workspace, "node_modules")
        if os.path.isdir(nm_path):
            import shutil
            shutil.rmtree(nm_path, ignore_errors=True)
            print(f"[build] node_modules deleted to save disk")

        return True
    except Exception as e:
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


# ── Progress tracking ─────────────────────────────────────────────────────────

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


def clear_progress(workspace):
    path = os.path.join(workspace, "progress.json")
    if os.path.exists(path):
        os.remove(path)


# ── Hook factories ────────────────────────────────────────────────────────────

TOOL_ACTIONS = {
    "write_file":          "writing",
    "edit_file":           "editing",
    "read_file":           "reading",
    "files_list":          "scanning",
    "run_install_command": "installing",
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
        action = TOOL_ACTIONS.get(name, "processing")
        file_path = args.get("path", None) if isinstance(args, dict) else None

        entry = {"action": action}
        if file_path:
            entry["file"] = file_path

        if name == "run_install_command":
            entry["detail"] = args.get("command", "")[:80]
        elif file_path:
            entry["detail"] = f"{action.capitalize()} {file_path}"
        else:
            entry["detail"] = f"{action.capitalize()} project files..."

        if name == "write_file" and isinstance(args, dict):
            content = args.get("content", "")
            lines = content.split("\n")
            if len(lines) > 60:
                lines = lines[-60:]
            entry["code"] = "\n".join(lines)
            entry["lang"] = _guess_lang(file_path or "")
            file_count["written"] += 1
        elif name == "edit_file" and isinstance(args, dict):
            new_str = args.get("new_str", "")
            lines = new_str.split("\n")
            if len(lines) > 60:
                lines = lines[-60:]
            entry["code"] = "\n".join(lines)
            entry["lang"] = _guess_lang(file_path or "")
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
        snippet = cleaned[:150] + ("..." if len(cleaned) > 150 else "")
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--message",   default=None)
    args = parser.parse_args()

    WORKSPACE = args.workspace
    os.chdir(WORKSPACE)

    try:
        write_state(WORKSPACE, "running")
        clear_progress(WORKSPACE)

        write_progress(WORKSPACE, {
            "action": "starting",
            "detail": "Setting up your project...",
        })

        files_list = FileState(False)
        generator  = create_generator(files_list=files_list)

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

        append_message(WORKSPACE, "user", user_message)

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

        output, token_breakdown, code_changed = generator.chat(user_message)

        print(f"[AA] code_changed={code_changed} — {'will build' if code_changed else 'skipping build (text-only reply)'}")

        credits_used = tokens_to_credits(
            input_tokens       = token_breakdown["input"],
            output_tokens      = token_breakdown["output"],
            cache_write_tokens = token_breakdown["cache_write"],
            cache_read_tokens  = token_breakdown["cache_read"],
        )

        print(f"[credits] breakdown={token_breakdown} → {credits_used} credits")

        append_message(WORKSPACE, "assistant", output,
                       token_breakdown=token_breakdown, credits=credits_used)

        save_deduction(WORKSPACE, token_breakdown, credits_used)

        build_ok = False
        if code_changed:
            write_progress(WORKSPACE, {
                "action": "building",
                "detail": "Installing dependencies & compiling...",
            })
            build_ok = build_project(WORKSPACE)
            # node_modules already deleted inside build_project()

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