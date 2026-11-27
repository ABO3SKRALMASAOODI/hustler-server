"""Send a verified account's volume-editing inquiry to Valmera support."""

import os
from uuid import UUID

import psycopg2
from psycopg2.extras import Json
from flask import Blueprint, current_app, jsonify, request

import brevo_delivery
from routes.auth import get_db, token_required

enterprise_bp = Blueprint("enterprise", __name__)
SUPPORT_EMAIL = "support@valmera.io"
EVENT_KIND = "enterprise_inquiry"
# Serialize admission to this inbox across workers, then release the database
# lock before contacting the mail provider.
INBOX_LOCK = 1731496023


def uuid4(value):
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if parsed.version == 4 and str(parsed) == value else None


def form_text(values, key, limit):
    value = values.get(key)
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned if len(cleaned) <= limit and "\x00" not in cleaned else ""


def reserve(user_id, reference, session_id):
    conn = get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT email FROM users WHERE id=%s AND is_verified=1", (user_id,))
            user = cur.fetchone()
            if not user or not user["email"]:
                return "unverified", None

            cur.execute("SELECT pg_advisory_xact_lock(%s)", (INBOX_LOCK,))
            cur.execute(
                """SELECT detail->>'state' AS state
                   FROM client_events
                   WHERE user_id=%s AND kind=%s AND detail->>'reference'=%s
                   ORDER BY id DESC LIMIT 1""",
                (user_id, EVENT_KIND, reference),
            )
            previous = cur.fetchone()
            if previous:
                return previous["state"], None

            cur.execute(
                """SELECT count(*) FILTER (WHERE user_id=%s) AS account,
                          count(*) AS total
                   FROM client_events
                   WHERE kind=%s AND created_at >= now() - interval '24 hours'""",
                (user_id, EVENT_KIND),
            )
            recent = cur.fetchone()
            if recent["account"] >= 3 or recent["total"] >= 20:
                return "limited", None

            detail = {"state": "pending", "reference": reference}
            if session_id:
                detail["session_id"] = session_id
            cur.execute(
                """INSERT INTO client_events (user_id, kind, detail)
                   VALUES (%s, %s, %s) RETURNING id""",
                (user_id, EVENT_KIND, Json(detail)),
            )
            return "reserved", (cur.fetchone()["id"], user["email"])
    finally:
        conn.close()


def finish(event_id, state):
    conn = get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE client_events
                   SET detail = detail || jsonb_build_object('state', %s::text)
                   WHERE id=%s AND kind=%s AND detail->>'state'='pending'""",
                (state, event_id, EVENT_KIND),
            )
    finally:
        conn.close()


def form_response(state, status):
    response = jsonify({"state": state})
    response.headers["Cache-Control"] = "no-store"
    return response, status


@enterprise_bp.post("/enterprise/inquiry")
@token_required
def submit_inquiry(user_id):
    request.max_content_length = 16384
    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return form_response("invalid", 400)

    reference = uuid4(values.get("reference"))
    company = form_text(values, "company", 120)
    volume = form_text(values, "volume", 160)
    message = form_text(values, "message", 2000)
    if not all((reference, company, volume, message)):
        return form_response("invalid", 400)

    session = values.get("session_id")
    session_id = session if isinstance(session, str) and session.startswith("s_") and uuid4(session[2:]) else None
    try:
        state, reservation = reserve(user_id, reference, session_id)
    except psycopg2.Error:
        current_app.logger.warning("Could not record an Enterprise inquiry")
        return form_response("unavailable", 503)

    if state != "reserved":
        return form_response(state, {"accepted": 200, "pending": 202, "unverified": 403, "limited": 429}.get(state, 503))

    event_id, reply_email = reservation
    payload = {
        "sender": {"name": "Valmera", "email": os.getenv("FROM_EMAIL") or SUPPORT_EMAIL},
        "to": [{"email": SUPPORT_EMAIL}],
        "replyTo": {"email": reply_email},
        "subject": "Valmera Enterprise quote request",
        "textContent": f"Company or team: {company}\nExpected editing volume: {volume}\n\n{message}\n\nReply to: {reply_email}\nInquiry reference: {reference}",
        "tags": ["enterprise-inquiry", f"enterprise-{reference}"],
        "headers": {"IdempotencyKey": reference},
    }
    # A repeated browser submission uses the same reference. An uncertain
    # provider result is never resubmitted automatically.
    sent = brevo_delivery.send_email(payload, category="critical", logger=current_app.logger)
    state = "accepted" if sent else "unavailable"
    try:
        finish(event_id, state)
    except psycopg2.Error:
        current_app.logger.warning("Could not update an Enterprise inquiry's mail status")
    return form_response(state, 200 if sent else 503)
