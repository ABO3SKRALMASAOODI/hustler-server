"""Reporting-only mirror of signed subscription events and Paddle GETs.

No entitlements, credits, payments, provider mutations or customer emails.
The caller owns the transaction. A failed report write never rolls back
billing work, and an old webhook cannot overwrite a newer provider snapshot.
"""
import logging


def record_snapshot(conn, data):
    sid = data.get("id") or ""
    if not sid.startswith("sub_") or not data.get("updated_at") or not data.get("status"):
        return False
    change = data.get("scheduled_change") or {}
    period = data.get("current_billing_period") or {}
    cur, savepoint = None, False
    try:
        cur = conn.cursor()
        cur.execute("SAVEPOINT subscription_report")
        savepoint = True
        cur.execute("""
            INSERT INTO admin_subscription_snapshots
                (subscription_id, status, started_at, canceled_at, next_billed_at,
                 period_ends_at, scheduled_action, scheduled_effective_at,
                 provider_updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (subscription_id) DO UPDATE SET
                status = EXCLUDED.status, started_at = EXCLUDED.started_at,
                canceled_at = EXCLUDED.canceled_at,
                next_billed_at = EXCLUDED.next_billed_at,
                period_ends_at = EXCLUDED.period_ends_at,
                scheduled_action = EXCLUDED.scheduled_action,
                scheduled_effective_at = EXCLUDED.scheduled_effective_at,
                provider_updated_at = EXCLUDED.provider_updated_at,
                observed_at = now()
            WHERE EXCLUDED.provider_updated_at >=
                  admin_subscription_snapshots.provider_updated_at
        """, (sid, data["status"], data.get("started_at"), data.get("canceled_at"),
              data.get("next_billed_at"), period.get("ends_at"), change.get("action"),
              change.get("effective_at"), data["updated_at"]))
        cur.execute("RELEASE SAVEPOINT subscription_report")
        return True
    except Exception:
        if savepoint:
            cur.execute("ROLLBACK TO SAVEPOINT subscription_report")
            cur.execute("RELEASE SAVEPOINT subscription_report")
        logging.getLogger(__name__).warning("Subscription report snapshot unavailable")
        return False
    finally:
        if cur is not None and hasattr(cur, "close"):
            cur.close()
