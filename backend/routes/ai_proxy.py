from flask import Blueprint, request, jsonify
from routes.auth import token_required, get_db
from credits import tokens_to_credits, get_balance
import os, json, time
import anthropic
import jwt as pyjwt

ai_proxy_bp = Blueprint('ai_proxy', __name__)

PROXY_MODEL     = "claude-haiku-4-5-20251001"
PROXY_HB_MODEL  = "hb-6"
MAX_TOKENS      = 1000
APP_TOKEN_SCOPE = "ai_proxy"


def _deduct_proxy_credits(conn, user_id: int, credits_used: float):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT credits_daily, credits_monthly FROM users WHERE id = %s FOR UPDATE",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return

        daily   = float(row["credits_daily"]   or 0)
        monthly = float(row["credits_monthly"] or 0)

        remaining = credits_used
        if daily >= remaining:
            daily    -= remaining
            remaining = 0
        else:
            remaining -= daily
            daily      = 0
            monthly    = max(0, monthly - remaining)

        cur.execute(
            """UPDATE users
               SET credits_daily   = %s,
                   credits_monthly = %s,
                   credits_balance = %s + %s
               WHERE id = %s""",
            (daily, monthly, daily, monthly, user_id)
        )
        conn.commit()


def _resolve_user_from_token(token: str, secret_key: str):
    """
    Accepts two token types:
    1. Full user JWT (scope = normal login) — direct user access
    2. App token (scope = ai_proxy) — scoped to a job, charges job owner

    Returns user_id or None.
    """
    try:
        data = pyjwt.decode(token, secret_key, algorithms=["HS256"])

        # App token — scoped to ai_proxy only
        if data.get("scope") == APP_TOKEN_SCOPE:
            job_id = data.get("job_id")
            if not job_id:
                return None
            # Look up job owner
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id, app_token FROM jobs WHERE job_id = %s",
                        (job_id,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    # Verify the token matches what we stored
                    if row["app_token"] != token:
                        return None
                    return int(row["user_id"])
            finally:
                conn.close()

        # Normal user JWT
        return int(data["sub"])

    except Exception:
        return None


@ai_proxy_bp.route('/auth/ai/proxy', methods=['POST'])
def ai_proxy():
    """
    Accepts both full user JWTs and scoped app tokens.
    Credits always charged to the job owner.
    """
    from flask import current_app

    # ── Extract token ─────────────────────────────────────────────────
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]

    if not token:
        return jsonify({"error": "Authorization token required"}), 401

    secret_key = current_app.config["SECRET_KEY"]
    user_id    = _resolve_user_from_token(token, secret_key)

    if not user_id:
        return jsonify({"error": "Invalid or expired token"}), 401

    # ── Check balance ─────────────────────────────────────────────────
    conn = get_db()
    try:
        info = get_balance(conn, user_id)
        if info["balance"] < 0.01:
            return jsonify({"error": "Not enough credits"}), 402
    finally:
        conn.close()

    # ── Validate request ──────────────────────────────────────────────
    data        = request.get_json() or {}
    messages    = data.get("messages", [])
    system      = data.get("system", None)
    max_tokens  = min(int(data.get("max_tokens", MAX_TOKENS)), MAX_TOKENS)
    temperature = float(data.get("temperature", 1.0))

    if not messages:
        return jsonify({"error": "messages required"}), 400

    clean_messages = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            clean_messages.append({
                "role":    m["role"],
                "content": str(m["content"])[:8000],
            })

    if not clean_messages:
        return jsonify({"error": "No valid messages"}), 400

    # ── Call Anthropic ────────────────────────────────────────────────
    try:
        client = anthropic.Anthropic()
        kwargs = dict(
            model       = PROXY_MODEL,
            max_tokens  = max_tokens,
            messages    = clean_messages,
            temperature = temperature,
        )
        if system:
            kwargs["system"] = str(system)[:2000]

        resp = client.messages.create(**kwargs)

        input_tokens  = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        credits_used  = tokens_to_credits(
            input_tokens       = input_tokens,
            output_tokens      = output_tokens,
            cache_write_tokens = 0,
            cache_read_tokens  = 0,
            model              = PROXY_HB_MODEL,
        )

        conn = get_db()
        try:
            _deduct_proxy_credits(conn, user_id, credits_used)
        finally:
            conn.close()

        text = "".join(
            block.text for block in resp.content if hasattr(block, "text")
        )

        return jsonify({
            "content":       text,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "credits_used":  credits_used,
        }), 200

    except anthropic.APIError as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500