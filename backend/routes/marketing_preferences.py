"""Opt-in and suppression apply to marketing, never account-service messages."""
import os

from flask import Blueprint, request, jsonify

from routes.auth import token_required
from routes.newsletter import admin_required, get_db, get_settings
import kit_marketing as marketing

marketing_bp = Blueprint("marketing_preferences", __name__)


@marketing_bp.route("/preferences", methods=["GET", "PUT"])
@token_required
def preferences(user_id):
    conn = get_db()
    try:
        rows = marketing.query(conn, """SELECT lower(u.email) AS email,u.unsubscribed_at,
            p.consented_at,p.revoked_at,p.kit_state FROM users u
            LEFT JOIN newsletter_permissions p ON p.email=lower(u.email)
            WHERE u.id=%s AND u.is_verified=1""", (user_id,))
        if not rows:
            return jsonify(error="Account not found."), 404
        row = rows[0]
        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify(error="Choose whether to receive daily emails."), 400
            value = data.get("enabled")
            if type(value) is not bool:
                return jsonify(error="Choose whether to receive daily emails."), 400
            if value:
                if data.get("disclosure") != marketing.DISCLOSURE:
                    return jsonify(error="Please review the email preference and try again."), 400
                if row.get("kit_state") in marketing.SUPPRESSED:
                    # Never silently reactivate an address suppressed by Kit.
                    return jsonify(error="Please contact support to rejoin the email list."), 409
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO newsletter_permissions(email,consented_at,source,disclosure)
                        VALUES (%s,NOW(),'account-preferences',%s) ON CONFLICT(email) DO UPDATE
                        SET consented_at=NOW(),source='account-preferences',disclosure=EXCLUDED.disclosure,
                            revoked_at=NULL,updated_at=NOW()""", (row["email"], marketing.DISCLOSURE))
                    cur.execute("UPDATE users SET unsubscribed_at=NULL WHERE id=%s", (user_id,))
                conn.commit()
            else:
                marketing.suppress_local(conn, row["email"], None)
                marketing.unsubscribe_remote(conn, row["email"])
            return jsonify(enabled=value, disclosure=marketing.DISCLOSURE)
        active = bool(row["consented_at"] and not row["revoked_at"] and not row["unsubscribed_at"])
        return jsonify(enabled=active, disclosure=marketing.DISCLOSURE)
    finally:
        conn.close()


@marketing_bp.get("/kit-status")
@admin_required
def status():
    conn = get_db()
    try:
        runs = marketing.query(conn, "SELECT day,topic,state,broadcast_id,recipient_count,error_code,updated_at FROM kit_daily_runs ORDER BY day DESC LIMIT 14")
        counts = marketing.query(conn, """SELECT COUNT(*) FILTER(WHERE revoked_at IS NULL) AS permission_count,
            COUNT(*) FILTER(WHERE revoked_at IS NOT NULL) AS opted_out,
            COUNT(*) FILTER(WHERE revoked_at IS NULL AND kit_state='active') AS synced
            FROM newsletter_permissions""")[0]
        counts["eligible"] = len(marketing.audience(conn))
        settings = get_settings(conn)
        return jsonify(provider="kit" if marketing.enabled() else "brevo",
            enabled=marketing.enabled() and os.getenv("KIT_DAILY_ENABLED") == "1" and settings["master_enabled"],
            master_enabled=settings["master_enabled"],
            activated=os.getenv("KIT_DAILY_ENABLED") == "1",
            send_hour_utc=settings["send_hour_utc"], counts=counts,
            runs=[{k: str(v) if k in ("day", "updated_at") else v for k, v in row.items()} for row in runs])
    finally:
        conn.close()


@marketing_bp.post("/kit-webhook")
def webhook():
    raw = request.get_data(cache=True)
    if not marketing.valid_signature(raw, request.headers.get("X-Kit-Signature"), os.getenv("KIT_WEBHOOK_SECRET")):
        return jsonify(error="Invalid signature."), 401
    data = request.get_json(silent=True) or {}
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or len(events) > 1000:
        return jsonify(error="Invalid events."), 400
    conn = get_db()
    try:
        for event in events:
            if not isinstance(event, dict):
                return jsonify(error="Invalid event."), 400
            if event.get("type") not in {"subscriber.unsubscribed", "subscriber.bounced", "subscriber.complained"}:
                continue
            payload = event.get("data")
            sub = payload.get("subscriber") if isinstance(payload, dict) else None
            if not isinstance(sub, dict):
                return jsonify(error="Invalid subscriber event."), 400
            email = str(sub.get("email_address", "")).strip().lower()
            if not event.get("id") or not email or sub.get("state") not in marketing.SUPPRESSED:
                return jsonify(error="Invalid subscriber event."), 400
            with conn.cursor() as cur:
                cur.execute("INSERT INTO kit_webhook_events(id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (event["id"],))
                if cur.fetchone():
                    cur.execute("""UPDATE newsletter_permissions SET revoked_at=COALESCE(revoked_at,NOW()),
                        kit_state=%s,updated_at=NOW() WHERE email=%s""", (sub["state"], email))
                    cur.execute("UPDATE users SET unsubscribed_at=COALESCE(unsubscribed_at,NOW()) WHERE lower(email)=%s", (email,))
            conn.commit()
        return ("", 204)
    finally:
        conn.close()
