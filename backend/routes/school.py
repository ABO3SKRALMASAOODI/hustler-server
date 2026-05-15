from flask import Blueprint, request, jsonify
import os
import anthropic

school_bp = Blueprint('school', __name__)
print("✅ school.py (Mentora tutor) is active")

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500

TUTOR_PERSONA = """
You are Mentora, a kind and patient private tutor for a university student. The student is talking to you in the moments right after class — they did not understand something in class but were too embarrassed to keep asking the teacher to repeat it. They nodded along even though they were lost.

Your job, in priority order:
1. Make the student feel safe. Never make them feel stupid. Use warm phrasing like "great question", "let's go slow", "no rush at all".
2. Use the student's CURRENT TIME + WEEKLY SCHEDULE + per-subject SYLLABUSES below to figure out which class they likely just came from and what topic they are on right now. Anchor your reply there. State your guess out loud before explaining — for example: "Looks like you just came out of Linear Algebra at 11 a.m. — you're on eigenvalues this week, right? Want me to walk through it from the top?"
3. Explain in simple, concrete steps with small worked examples and concrete analogies the student can picture.
4. Break replies into 3–5 short bullets, not walls of text. Never dump a textbook chapter.
5. Quote from the textbook when it's useful, but always restate the idea in plain language right after the quote.
6. End every real explanation with ONE tiny check-for-understanding question, so the student can self-test before moving on.

Tone: friendly, warm, slightly informal — like a smart older friend who happens to tutor. Avoid academic jargon unless you immediately explain it in everyday words.

Format: Markdown. Short paragraphs. Bullets. Use **bold** to highlight the one key term you want them to remember. Avoid emojis unless the student uses them first.
""".strip()


SUPPORTED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def _trim(text: str, limit: int) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[...truncated, original was {len(text)} characters]"


def _data_url_to_image_block(data_url, media_type):
    """Parse data:image/...;base64,... → Anthropic image content block."""
    if not data_url or not isinstance(data_url, str):
        return None
    try:
        if data_url.startswith("data:"):
            header, b64 = data_url.split(",", 1)
            if not media_type:
                meta = header[5:]  # strip "data:"
                media_type = meta.split(";")[0]
        else:
            b64 = data_url
        if not media_type:
            media_type = "image/jpeg"
        media_type = str(media_type).lower()
        if media_type not in SUPPORTED_IMAGE_TYPES:
            return None
        # Hard cap on base64 size to avoid blowing the request body
        if len(b64) > 7_000_000:
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }
    except Exception:
        return None


def _build_system(subjects, schedule, now_iso, student_meta,
                  schedule_is_image=False, image_subject_names=None):
    image_subject_names = image_subject_names or []
    parts = [TUTOR_PERSONA, ""]

    parts.append("# Student profile")
    if student_meta:
        for k, v in student_meta.items():
            if v:
                parts.append(f"- **{k}**: {v}")
    else:
        parts.append("(no profile uploaded — assume a university student)")
    parts.append("")

    parts.append("# Current time")
    parts.append(now_iso or "(unknown — ask the student what time it is if you need it)")
    parts.append("")

    parts.append("# Student's weekly schedule")
    if schedule:
        parts.append("```")
        parts.append(_trim(schedule, 8000))
        parts.append("```")
    elif schedule_is_image:
        parts.append("(uploaded as an image — see the attached schedule image in this message; read the times and subject names directly from it)")
    else:
        parts.append("(not uploaded yet — if the student says 'I just came out of class' and you don't know which one, ask them which subject before guessing)")
    parts.append("")

    parts.append("# Student's subjects, syllabuses, and textbook material")
    if subjects or image_subject_names:
        for name, content in subjects.items():
            parts.append(f"## {name}")
            if content:
                parts.append("```")
                parts.append(_trim(content, 12000))
                parts.append("```")
            else:
                parts.append("(file not uploaded yet)")
            parts.append("")
        for name in image_subject_names:
            if name in subjects:
                continue
            parts.append(f"## {name}")
            parts.append("(uploaded as an image — see the attached syllabus image for this subject; read its contents directly)")
            parts.append("")
    else:
        parts.append("(no subject files uploaded yet)")

    return "\n".join(parts)


@school_bp.route('/school/chat', methods=['POST'])
def school_chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    subjects = data.get("subjects") or {}
    subjects_images = data.get("subjects_images") or {}
    schedule = data.get("schedule") or ""
    schedule_image = data.get("schedule_image")
    now_iso = data.get("now_iso") or ""
    student_meta = data.get("student") or {}

    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages required"}), 400

    clean = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            clean.append({
                "role": m["role"],
                "content": str(m["content"])[:8000],
            })
    if not clean:
        return jsonify({"error": "no valid messages"}), 400

    if not os.getenv("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not configured on server"}), 500

    subjects_clean = {}
    if isinstance(subjects, dict):
        for k, v in subjects.items():
            if v:
                subjects_clean[str(k)[:120]] = str(v)

    # Build image attachments for the most recent user turn (Claude Vision).
    image_attachments = []  # list of (label, image_block)
    image_subject_names = []
    if isinstance(schedule_image, dict) and schedule_image.get("dataUrl"):
        block = _data_url_to_image_block(schedule_image.get("dataUrl"), schedule_image.get("mediaType"))
        if block:
            image_attachments.append(("[Attached: the student's weekly class schedule (image)]", block))
    if isinstance(subjects_images, dict):
        for name, img in subjects_images.items():
            if not isinstance(img, dict):
                continue
            block = _data_url_to_image_block(img.get("dataUrl"), img.get("mediaType"))
            if block:
                label_name = str(name)[:120]
                image_subject_names.append(label_name)
                image_attachments.append((f"[Attached syllabus image for: {label_name}]", block))

    if image_attachments:
        # Attach to the most recent user message — model sees them as fresh context for the question.
        for i in range(len(clean) - 1, -1, -1):
            if clean[i]["role"] == "user":
                user_text = clean[i]["content"]
                blocks = []
                for label, img_block in image_attachments:
                    blocks.append({"type": "text", "text": label})
                    blocks.append(img_block)
                blocks.append({"type": "text", "text": user_text})
                clean[i] = {"role": "user", "content": blocks}
                break

    system_prompt = _build_system(
        subjects_clean,
        str(schedule),
        str(now_iso),
        student_meta if isinstance(student_meta, dict) else {},
        schedule_is_image=bool(isinstance(schedule_image, dict) and schedule_image.get("dataUrl")),
        image_subject_names=image_subject_names,
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=clean,
            temperature=0.7,
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return jsonify({
            "content": text,
            "model": CLAUDE_MODEL,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }), 200
    except anthropic.APIError as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@school_bp.route('/school/health', methods=['GET'])
def school_health():
    return jsonify({
        "ok": True,
        "model": CLAUDE_MODEL,
        "anthropic_key_present": bool(os.getenv("ANTHROPIC_API_KEY")),
    }), 200
