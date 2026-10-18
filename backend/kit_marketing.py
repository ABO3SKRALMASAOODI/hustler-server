"""Daily Kit broadcasts, explicit audience permission, and durable deduplication.

The caller holds the existing newsletter advisory lock. Provider errors never
fall back to Brevo, and no schema is created during a request or scheduler tick.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
import hashlib
import hmac
import os
import time
from psycopg2.extras import execute_values

from kit_client import KitClient, KitError, check_free_account

DUBAI = ZoneInfo("Asia/Dubai")
DISCLOSURE = "Email me daily Valmera editing tips and invitations to return. I can unsubscribe at any time."
SUPPRESSED = {"cancelled", "bounced", "complained"}


def enabled():
    return os.getenv("MARKETING_EMAIL_PROVIDER", "brevo").lower() == "kit"


def config():
    values = {key: os.getenv(key, "").strip() for key in (
        "KIT_ACCOUNT_ID", "KIT_AUDIENCE_TAG_ID", "KIT_EMAIL_TEMPLATE_ID", "KIT_FROM_EMAIL")}
    if not all(values.values()):
        raise KitError("kit_setup_incomplete")
    for key in ("KIT_ACCOUNT_ID", "KIT_AUDIENCE_TAG_ID", "KIT_EMAIL_TEMPLATE_ID"):
        if not values[key].isdigit():
            raise KitError("kit_setup_incomplete")
    return values


def query(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def write(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def audience(conn):
    from routes.newsletter import BASE_FILTER
    return query(conn, f"""SELECT u.id, lower(u.email) AS email, p.kit_subscriber_id, p.kit_state
        FROM users u JOIN newsletter_permissions p ON p.email=lower(u.email)
        WHERE {BASE_FILTER} AND p.revoked_at IS NULL
        ORDER BY u.id""")


def save_links(conn, links):
    if not links:
        return
    with conn.cursor() as cur:
        execute_values(cur, """UPDATE newsletter_permissions p SET kit_subscriber_id=v.sid,
            kit_state='active',synced_at=NOW(),updated_at=NOW()
            FROM (VALUES %s) AS v(email,sid)
            WHERE p.email=v.email AND p.revoked_at IS NULL""", links, page_size=500)
    conn.commit()


def suppress_local(conn, email, state):
    with conn.cursor() as cur:
        cur.execute("""UPDATE newsletter_permissions SET revoked_at=COALESCE(revoked_at,NOW()),
                       kit_state=COALESCE(%s,kit_state), updated_at=NOW() WHERE email=%s""", (state, email))
        cur.execute("UPDATE users SET unsubscribed_at=COALESCE(unsubscribed_at,NOW()) WHERE lower(email)=%s", (email,))
    conn.commit()


def unsubscribe_remote(conn, email):
    if not enabled():
        return
    rows = query(conn, "SELECT kit_subscriber_id FROM newsletter_permissions WHERE email=%s", (email.lower(),))
    if rows and rows[0]["kit_subscriber_id"]:
        try:
            KitClient().unsubscribe(rows[0]["kit_subscriber_id"])
            write(conn, "UPDATE newsletter_permissions SET kit_state='cancelled',updated_at=NOW() WHERE email=%s", (email.lower(),))
        except KitError:
            # The local opt-out is durable and the next sync retries it before
            # any new campaign. Never turn a successful local opt-out into 500.
            pass


def sync_audience(conn, client, cfg, *, dry_run=False):
    account = client.account()
    limit = check_free_account(account, expected_id=cfg["KIT_ACCOUNT_ID"], sender=cfg["KIT_FROM_EMAIL"])
    remote = client.subscribers()
    if not dry_run:
        pending_optouts = query(conn, """SELECT p.email,p.kit_subscriber_id FROM newsletter_permissions p
            LEFT JOIN users u ON lower(u.email)=p.email
            WHERE p.kit_subscriber_id IS NOT NULL AND p.kit_state='active'
              AND (p.revoked_at IS NOT NULL OR u.unsubscribed_at IS NOT NULL OR u.id IS NULL)""")
        for row in pending_optouts:
            client.unsubscribe(row["kit_subscriber_id"])
            write(conn, "UPDATE newsletter_permissions SET kit_state='cancelled',updated_at=NOW() WHERE email=%s", (row["email"],))
    by_email = {str(row["email_address"]).strip().lower(): row for row in remote}
    candidates = {row["email"]: row for row in audience(conn)}
    new_count = sum(email not in by_email for email in candidates)
    if sum(row.get("state") == "active" for row in remote) + new_count > limit:
        raise KitError("kit_free_subscriber_limit")
    members = {int(row["id"]): row for row in client.tag_members(cfg["KIT_AUDIENCE_TAG_ID"])}
    eligible = {}
    links = []
    for email, local in candidates.items():
        sub = by_email.get(email)
        if sub and sub.get("state") in SUPPRESSED:
            if not dry_run:
                suppress_local(conn, email, sub["state"])
            continue
        if sub and sub.get("state") != "active":
            continue  # Never confirm an unconfirmed subscriber for them.
        if not sub and not dry_run:
            sub = client.subscribe(email)
            if sub.get("state") != "active":
                continue
        if sub:
            eligible[int(sub["id"])] = email
            if not dry_run:
                if local.get("kit_subscriber_id") != sub["id"] or local.get("kit_state") != "active":
                    links.append((email, int(sub["id"])))
                if int(sub["id"]) not in members:
                    client.tag(cfg["KIT_AUDIENCE_TAG_ID"], sub["id"])
    if dry_run:
        return {"eligible": len(eligible) + new_count, "imports_needed": new_count,
                "free_subscriber_limit": limit}
    save_links(conn, links)
    # Local opt-outs and removed accounts cannot remain in the managed audience.
    for sid, sub in members.items():
        if sid not in eligible:
            client.untag(cfg["KIT_AUDIENCE_TAG_ID"], sid)
            email = str(sub["email_address"]).strip().lower()
            local = query(conn, "SELECT unsubscribed_at FROM users WHERE lower(email)=%s", (email,))
            if local and local[0]["unsubscribed_at"] and sub.get("state") == "active":
                client.unsubscribe(sid)
    # Kit list APIs are eventually consistent. Fail closed until the tag
    # reflects the exact audience, rather than accidentally send to stale members.
    actual = {int(row["id"]) for row in client.tag_members(cfg["KIT_AUDIENCE_TAG_ID"])
              if row.get("state") == "active"}
    if actual != set(eligible):
        raise KitError("kit_audience_sync_pending")
    # Recheck local consent after potentially slow imports.
    if set(eligible.values()) != {row["email"] for row in audience(conn)}:
        # Suppressed/unconfirmed remote contacts are excluded legitimately.
        current = {row["email"] for row in audience(conn)}
        if not set(eligible.values()).issubset(current):
            raise KitError("kit_audience_changed")
    return {"eligible": len(eligible), "imports_needed": new_count,
            "free_subscriber_limit": limit}


def daily_content(day, templates):
    from routes.newsletter_content import WEEKLY_ORDER, CONTENT_VERSION, render_tokens
    keys = [key for key in WEEKLY_ORDER if templates[key]["enabled"]]
    if not keys:
        raise KitError("kit_no_enabled_topics")
    key = keys[(day - datetime(2026, 10, 18).date()).days % len(keys)]
    template = templates[key]
    cta = "https://valmera.io/studio?" + urlencode({
        "utm_source": "valmera", "utm_medium": "email", "utm_campaign": f"daily-{day}-{key}",
        "utm_content": CONTENT_VERSION, "utm_term": "kit",
    })
    # Kit's selected template supplies its own unsubscribe, postal address and
    # required branding. Content contains no recipient-specific login tokens.
    body = render_tokens(template["body_html"], cta_url=cta,
                         unsub_url="{{ unsubscribe_url }}", html=True)
    body += ('<p style="color:#aaa;font:13px/1.6 Arial,sans-serif">AI editing requires a subscription. '
             '<a href="https://valmera.io/subscribe?utm_source=valmera&amp;utm_medium=email&amp;utm_campaign=plan_details">View plans</a>.</p>')
    body = ('<table role="presentation" width="100%" bgcolor="#0a0a0a" style="background:#0a0a0a;color:#eee">'
            '<tr><td style="padding:28px 24px">' + body + '</td></tr></table>')
    return key, {"subject": template["subject"], "content": body,
                 "preview_text": template.get("preheader", "")}


def run(conn, settings, *, force=False, dry_run=False, client=None, now=None):
    """Run under the shared newsletter lock; force never bypasses daily dedupe."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started = time.monotonic()
    if not settings.get("master_enabled"):
        return {"skipped": "disabled", "provider": "kit"}
    if not dry_run and os.getenv("KIT_DAILY_ENABLED") != "1":
        return {"skipped": "kit_activation_pending", "provider": "kit"}
    local_now = now.astimezone(DUBAI)
    local_send_hour = (int(settings.get("send_hour_utc", 15)) + 4) % 24
    if not force and not dry_run and local_now.hour < local_send_hour:
        return {"skipped": "before_send_hour", "provider": "kit"}
    day = local_now.date()
    try:
        cfg = config()
        client = client or KitClient()
        rows = query(conn, "SELECT * FROM kit_daily_runs WHERE day=%s", (day,))
        previous = rows[0] if rows else None
        if previous and previous["state"] in {"submitted", "scheduled", "sending", "completed", "aborted"}:
            if not dry_run and previous.get("broadcast_id"):
                remote = client.broadcast(previous["broadcast_id"])
                state = remote.get("status", previous["state"])
                if state in {"scheduled", "sending", "completed", "aborted"}:
                    write(conn, "UPDATE kit_daily_runs SET state=%s,updated_at=NOW() WHERE day=%s", (state, day))
            return {"skipped": "already_scheduled_today", "provider": "kit",
                    "broadcast_id": previous.get("broadcast_id")}
        if previous and previous["state"] in {"creating", "uncertain"}:
            matches = [b for b in client.broadcasts() if b.get("description") == previous["description"]]
            if len(matches) == 1 and not dry_run:
                write(conn, "UPDATE kit_daily_runs SET broadcast_id=%s,state='submitted',error_code=NULL,updated_at=NOW() WHERE day=%s",
                      (matches[0]["id"], day))
            # A missing item can be eventual consistency, never permission to resend.
            return {"skipped": "reconciling_prior_attempt", "provider": "kit"}
        synced = sync_audience(conn, client, cfg, dry_run=dry_run)
        from routes.newsletter import get_all_templates
        topic, content = daily_content(day, get_all_templates(conn))
        result = {"provider": "kit", "dry_run": dry_run, "day": str(day), "topic": topic, **synced}
        if dry_run or not synced["eligible"]:
            return {**result, **({"skipped": "no_consented_recipients"} if not synced["eligible"] else {})}
        description = f"valmera-daily-{day}"
        write(conn, """INSERT INTO kit_daily_runs(day,topic,description,state,recipient_count)
              VALUES (%s,%s,%s,'creating',%s) ON CONFLICT(day) DO UPDATE
              SET state='creating',recipient_count=EXCLUDED.recipient_count,error_code=NULL,updated_at=NOW()""",
              (day, topic, description, synced["eligible"]))
        try:
            broadcast = client.create_broadcast(
                description=description, **content, tag_id=cfg["KIT_AUDIENCE_TAG_ID"],
                template_id=cfg["KIT_EMAIL_TEMPLATE_ID"], sender=cfg["KIT_FROM_EMAIL"],
                # Large first-time imports can take minutes. Schedule relative
                # to the completed sync, not the beginning of this tick.
                send_at=(now + timedelta(seconds=time.monotonic() - started, minutes=2)).isoformat(),
            )
        except KitError as exc:
            write(conn, "UPDATE kit_daily_runs SET state=%s,error_code=%s,updated_at=NOW() WHERE day=%s",
                  ("uncertain" if exc.uncertain else "prepared", exc.code, day))
            raise
        write(conn, "UPDATE kit_daily_runs SET broadcast_id=%s,state='submitted',updated_at=NOW() WHERE day=%s", (broadcast["id"], day))
        return {**result, "status": "submitted", "broadcast_id": broadcast["id"]}
    except KitError as exc:
        return {"skipped": exc.code, "provider": "kit", "retryable": not exc.uncertain}


def valid_signature(raw, header, secret, now=None):
    if not secret or len(raw) > 1_000_000:
        return False
    parts = [part.strip() for part in (header or "").split(",")]
    timestamps = [part[2:] for part in parts if part.startswith("t=")]
    if len(timestamps) != 1:
        return False
    try:
        stamp = int(timestamps[0])
    except ValueError:
        return False
    if abs((now or time.time()) - stamp) > 300:
        return False
    expected = hmac.new(secret.encode(), (str(stamp) + ".").encode() + raw, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(part[3:], expected) for part in parts if part.startswith("v1="))
