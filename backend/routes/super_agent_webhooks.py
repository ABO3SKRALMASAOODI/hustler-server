"""
Incoming webhook endpoints for WhatsApp, Telegram, and Slack.

These are PUBLIC endpoints (no JWT auth) — verified by platform-specific signatures.
They receive incoming messages from messaging platforms and route them to the
correct super agent for processing.
"""

from flask import Blueprint, request, jsonify, Response, current_app
import json
import sys
import os
import threading
import traceback
import logging

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

log = logging.getLogger("super_agent_webhooks")

super_agent_webhooks_bp = Blueprint("super_agent_webhooks", __name__)


def _get_db():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(current_app.config["DATABASE_URL"], cursor_factory=RealDictCursor)


def _find_agent_by_integration(platform, identifier_key, identifier_value):
    """
    Find the agent linked to a platform integration by a config field.
    Returns (agent_id, user_id, integration_config) or (None, None, None).
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.agent_id, a.user_id, i.config
                FROM agent_integrations i
                JOIN super_agents a ON a.agent_id = i.agent_id
                WHERE i.platform = %s AND a.status = 'active'
            """, (platform,))
            rows = cur.fetchall()

        for row in rows:
            config = row["config"]
            if isinstance(config, str):
                config = json.loads(config)
            if config.get(identifier_key) == identifier_value:
                return row["agent_id"], row["user_id"], config

        return None, None, None
    finally:
        conn.close()


def _process_incoming_message(agent_id, user_id, channel, channel_id, text, db_url, reply_fn):
    """
    Process an incoming message from any platform in a background thread.
    reply_fn(response_text) is called to send the reply back.
    """
    def _run():
        try:
            # Check credits
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
            try:
                from credits import check_and_reserve
                if not check_and_reserve(conn, int(user_id)):
                    reply_fn("Sorry, the agent owner has run out of credits. Please try again later.")
                    return
            finally:
                conn.close()

            # Find or create thread for this channel+contact
            thread_id = _get_or_create_thread(agent_id, channel, channel_id, text, db_url)

            # Run the agent
            from super_agent.runner import SuperAgentRunner
            runner = SuperAgentRunner(agent_id, thread_id, db_url)
            result = runner.run(text, trigger_type="webhook", trigger_source=f"{channel}:{channel_id}")

            # Send reply
            if result.get("text"):
                reply_fn(result["text"])

        except Exception as e:
            log.error(f"Webhook message processing error: {e}")
            traceback.print_exc()
            try:
                reply_fn("Sorry, I encountered an error processing your message. Please try again.")
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _get_or_create_thread(agent_id, channel, channel_id, first_message, db_url):
    """Get existing thread or create new one for this channel contact."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import uuid

    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            # Look for existing thread
            cur.execute(
                """SELECT thread_id FROM agent_threads
                   WHERE agent_id = %s AND channel = %s AND channel_id = %s
                   ORDER BY updated_at DESC LIMIT 1""",
                (agent_id, channel, channel_id)
            )
            row = cur.fetchone()
            if row:
                return row["thread_id"]

            # Create new thread
            thread_id = uuid.uuid4().hex[:8]
            cur.execute(
                """INSERT INTO agent_threads (thread_id, agent_id, channel, channel_id, title)
                   VALUES (%s, %s, %s, %s, %s)""",
                (thread_id, agent_id, channel, channel_id, first_message[:100])
            )
            conn.commit()
            return thread_id
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
#  WHATSAPP WEBHOOKS
# ══════════════════════════════════════════════════════════════════════

@super_agent_webhooks_bp.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta webhook verification (hub.challenge)."""
    from super_agent.channels.whatsapp import WhatsAppChannel
    challenge = WhatsAppChannel.verify_webhook(request.args)
    if challenge:
        return Response(challenge, status=200, content_type="text/plain")
    return "Verification failed", 403


@super_agent_webhooks_bp.route("/webhooks/whatsapp", methods=["POST"])
def whatsapp_incoming():
    """Receive incoming WhatsApp messages."""
    payload = request.get_json(silent=True) or {}

    from super_agent.channels.whatsapp import WhatsAppChannel
    messages = WhatsAppChannel.parse_webhook(payload)

    db_url = current_app.config["DATABASE_URL"]

    for msg in messages:
        phone_number_id = msg.get("phone_number_id", "")
        from_number = msg.get("from_number", "")
        text = msg.get("text", "")

        if not text or not phone_number_id:
            continue

        # Find agent by phone_number_id
        agent_id, user_id, config = _find_agent_by_integration(
            "whatsapp", "phone_number_id", phone_number_id
        )
        if not agent_id:
            log.warning(f"No agent found for WhatsApp phone_number_id: {phone_number_id}")
            continue

        access_token = config.get("access_token") or os.getenv("WHATSAPP_ACCESS_TOKEN")

        def make_reply_fn(pnid, to, token):
            def reply(response_text):
                WhatsAppChannel.send_message(pnid, to, response_text, access_token=token)
            return reply

        _process_incoming_message(
            agent_id, user_id, "whatsapp", from_number, text, db_url,
            make_reply_fn(phone_number_id, from_number, access_token)
        )

    return jsonify({"ok": True}), 200


# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM WEBHOOKS
# ══════════════════════════════════════════════════════════════════════

@super_agent_webhooks_bp.route("/webhooks/telegram/<webhook_token>", methods=["POST"])
def telegram_incoming(webhook_token):
    """Receive incoming Telegram updates."""
    update = request.get_json(silent=True) or {}

    from super_agent.channels.telegram import TelegramChannel
    parsed = TelegramChannel.parse_update(update)

    if not parsed.get("text") or not parsed.get("chat_id"):
        return jsonify({"ok": True}), 200

    # Find agent by webhook token hash
    agent_id, user_id, config = _find_agent_by_integration(
        "telegram", "webhook_token", webhook_token
    )
    if not agent_id:
        log.warning(f"No agent found for Telegram webhook token: {webhook_token[:8]}...")
        return jsonify({"ok": True}), 200

    bot_token = config.get("bot_token", "")
    chat_id = parsed["chat_id"]
    text = parsed["text"]
    db_url = current_app.config["DATABASE_URL"]

    def reply(response_text):
        TelegramChannel.send_message(bot_token, chat_id, response_text)

    _process_incoming_message(
        agent_id, user_id, "telegram", str(chat_id), text, db_url, reply
    )

    return jsonify({"ok": True}), 200


# ══════════════════════════════════════════════════════════════════════
#  SLACK WEBHOOKS
# ══════════════════════════════════════════════════════════════════════

@super_agent_webhooks_bp.route("/webhooks/slack", methods=["POST"])
def slack_incoming():
    """Receive incoming Slack events."""
    # Handle Slack URL verification challenge
    if request.content_type == "application/json":
        payload = request.get_json(silent=True) or {}
    else:
        payload = {}

    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge")}), 200

    # Verify signature
    from super_agent.channels.slack import SlackChannel
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not SlackChannel.verify_signature(request.get_data(), timestamp, signature):
        return "Invalid signature", 403

    # Parse event
    parsed = SlackChannel.parse_event(payload)
    if not parsed.get("text") or not parsed.get("channel_id"):
        return jsonify({"ok": True}), 200

    # Ignore bot messages (already filtered in parse_event, but double check)
    event = payload.get("event", {})
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return jsonify({"ok": True}), 200

    # Find agent — we need to match by the Slack team_id or channel
    team_id = payload.get("team_id", "")
    agent_id, user_id, config = _find_agent_by_integration(
        "slack", "team_id", team_id
    )
    if not agent_id:
        log.warning(f"No agent found for Slack team: {team_id}")
        return jsonify({"ok": True}), 200

    bot_token = config.get("bot_token", "")
    channel_id = parsed["channel_id"]
    text = parsed["text"]
    thread_ts = parsed.get("thread_ts") or parsed.get("ts")
    db_url = current_app.config["DATABASE_URL"]

    def reply(response_text):
        SlackChannel.send_message(bot_token, channel_id, response_text, thread_ts=thread_ts)

    _process_incoming_message(
        agent_id, user_id, "slack", channel_id, text, db_url, reply
    )

    return jsonify({"ok": True}), 200
