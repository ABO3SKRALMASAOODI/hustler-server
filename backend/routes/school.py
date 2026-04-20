"""
School tutor blueprint — vertical AI demo.

Exposes:
  POST /school/chat            JSON: { messages, context }  → streamed/plain reply
  POST /school/extract         multipart: file              → { text, pages }

No auth required (demo endpoint). Calls Anthropic directly using the
backend's ANTHROPIC_API_KEY. Rate limited per-IP via a simple in-memory
counter so it can't be abused while it's live.
"""
from flask import Blueprint, request, jsonify, current_app
import os
import io
import time
import threading
import anthropic

school_bp = Blueprint('school', __name__, url_prefix='/school')

MODEL              = "claude-opus-4-7"
MAX_TOKENS         = 1400
MAX_CONTEXT_CHARS  = 120_000   # cap what we embed in the system prompt
MAX_FILE_BYTES     = 8 * 1024 * 1024  # 8 MB
RATE_WINDOW_SEC    = 60
RATE_LIMIT_MSGS    = 40

_rate_lock = threading.Lock()
_rate_hits = {}   # ip -> [timestamps]


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(ip, []) if now - t < RATE_WINDOW_SEC]
        if len(hits) >= RATE_LIMIT_MSGS:
            _rate_hits[ip] = hits
            return True
        hits.append(now)
        _rate_hits[ip] = hits
    return False


def _extract_pdf_text(data: bytes) -> tuple[str, int]:
    """Best-effort PDF text extraction. Tries pypdf, then PyPDF2."""
    try:
        from pypdf import PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader
        except Exception:
            return "", 0
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n\n".join(pages), len(reader.pages)
    except Exception:
        return "", 0


def _build_system_prompt(ctx: dict) -> str:
    subjects = ctx.get("subjects") or []
    schedule = ctx.get("schedule") or {}
    now_iso  = ctx.get("now") or ""
    weekday  = ctx.get("weekday") or ""
    local_time = ctx.get("local_time") or ""
    student_name = (ctx.get("student_name") or "the student").strip()
    grade = (ctx.get("grade") or "university").strip()

    lines = []
    lines.append(
        "You are Valmera Tutor, a patient, one-on-one AI tutor for a school "
        "or university student. The student is in a classroom setting where "
        "they were too shy or rushed to ask follow-up questions, so they come "
        "to you to actually understand the material."
    )
    lines.append("")
    lines.append("How to teach:")
    lines.append(
        "- Default to short, crystal-clear explanations. Build up from the "
        "basics. Use simple words first, then introduce the textbook terms."
    )
    lines.append(
        "- When a student says things like 'I don't understand', 'I'm lost', "
        "'explain again', 'can you repeat' — slow down, break it into steps, "
        "use a concrete example, and end by asking one small check-in "
        "question to confirm they followed."
    )
    lines.append(
        "- Use analogies and tiny worked examples. Prefer showing over telling."
    )
    lines.append(
        "- If the student mentions a current class / time, ground your reply "
        "in what they are supposed to be learning right now based on the "
        "schedule and syllabus below. Example: 'It's Tuesday 11:00 — you're "
        "in Physics right now, and the syllabus has you on Motion, so…'"
    )
    lines.append(
        "- Never shame the student for not getting it. Never say 'as I said "
        "before'. They did not get it — that's why they're here."
    )
    lines.append(
        "- Stay on curriculum. If they ask something off-topic, answer briefly "
        "then steer back."
    )
    lines.append(
        "- Keep answers under ~200 words unless the student asks for more "
        "depth. Use short paragraphs, bullet points, or a numbered walkthrough "
        "when it helps."
    )
    lines.append("")
    lines.append(f"Student: {student_name} ({grade}).")
    if now_iso or weekday or local_time:
        lines.append(
            f"Current time: {weekday} {local_time} (ISO {now_iso}). "
            "Use this to figure out which class is happening right now from "
            "the schedule."
        )
    lines.append("")

    if schedule and (schedule.get("text") or "").strip():
        lines.append("=== WEEKLY SCHEDULE ===")
        lines.append(schedule["text"].strip()[:8000])
        lines.append("=== END SCHEDULE ===")
        lines.append("")

    if subjects:
        lines.append("=== SUBJECTS & SYLLABUSES ===")
        for s in subjects:
            name = s.get("name") or "Subject"
            text = (s.get("text") or "").strip()
            if not text:
                lines.append(f"[{name}] — (no syllabus uploaded yet)")
                continue
            # Truncate each subject so the overall system prompt stays bounded.
            snippet = text[:20_000]
            lines.append(f"--- {name} ---")
            lines.append(snippet)
        lines.append("=== END SUBJECTS ===")

    prompt = "\n".join(lines)
    if len(prompt) > MAX_CONTEXT_CHARS:
        prompt = prompt[:MAX_CONTEXT_CHARS] + "\n\n[context truncated]"
    return prompt


@school_bp.route('/chat', methods=['POST', 'OPTIONS'])
def school_chat():
    if request.method == 'OPTIONS':
        return ('', 204)

    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
          .split(",")[0].strip() or "anon")
    if _rate_limited(ip):
        return jsonify({"error": "Rate limit exceeded. Slow down a bit."}), 429

    data = request.get_json(silent=True) or {}
    messages = data.get("messages") or []
    context  = data.get("context")  or {}

    clean = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        clean.append({"role": role, "content": str(content)[:8000]})

    if not clean:
        return jsonify({"error": "messages required"}), 400

    system_prompt = _build_system_prompt(context)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Server is missing ANTHROPIC_API_KEY"}), 500

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model       = MODEL,
            max_tokens  = MAX_TOKENS,
            system      = system_prompt,
            messages    = clean,
            temperature = 0.6,
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return jsonify({
            "content":       text,
            "input_tokens":  getattr(resp.usage, "input_tokens", None),
            "output_tokens": getattr(resp.usage, "output_tokens", None),
        }), 200
    except anthropic.APIError as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@school_bp.route('/extract', methods=['POST', 'OPTIONS'])
def school_extract():
    """Accepts a single file upload. Returns plain text extracted from it."""
    if request.method == 'OPTIONS':
        return ('', 204)

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400

    raw = f.read()
    if not raw:
        return jsonify({"error": "empty file"}), 400
    if len(raw) > MAX_FILE_BYTES:
        return jsonify({"error": "file too large (max 8MB)"}), 413

    filename = (f.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

    text = ""
    pages = 0

    if ext == "pdf":
        text, pages = _extract_pdf_text(raw)
    else:
        # treat everything else as text (txt/md/csv/json/etc)
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""

    text = (text or "").strip()
    if not text:
        return jsonify({"error": "could not extract text from this file"}), 422

    # Hard cap to keep the browser / system prompt sane
    if len(text) > 60_000:
        text = text[:60_000] + "\n\n[...truncated...]"

    return jsonify({
        "text":     text,
        "pages":    pages,
        "filename": f.filename,
        "chars":    len(text),
    }), 200
