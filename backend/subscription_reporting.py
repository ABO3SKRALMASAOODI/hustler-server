"""Reporting-only mirror of signed subscription events and Paddle GETs.

No entitlements, credits, payments, provider mutations or customer emails.
The caller owns the transaction. A failed report write never rolls back
billing work, and an old webhook cannot overwrite a newer provider snapshot.
"""
import logging


def record_snapshot(conn, data, event=None):
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
        record_history(conn, data, event)
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


def record_history(conn, data, event=None):
    """Append provider versions; a GET never invents a cancellation event.

    Separate savepoint keeps snapshot reporting working during deployment.
    Deduplication by provider version handles retries, overlapping lifecycle
    events and hourly GETs. Signed/retrieved events enrich existing GET rows.
    """
    change, period, event = (data.get("scheduled_change") or {},
                              data.get("current_billing_period") or {}, event or {})
    cur = conn.cursor()
    try:
        cur.execute("SAVEPOINT subscription_history")
        cur.execute("""
            INSERT INTO admin_subscription_history
                (subscription_id, provider_updated_at, status, started_at,
                 canceled_at, next_billed_at, period_ends_at, scheduled_action,
                 scheduled_effective_at, event_id, event_type, occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (subscription_id, provider_updated_at) DO UPDATE SET
                event_id = COALESCE(admin_subscription_history.event_id, EXCLUDED.event_id),
                event_type = COALESCE(admin_subscription_history.event_type, EXCLUDED.event_type),
                occurred_at = COALESCE(admin_subscription_history.occurred_at, EXCLUDED.occurred_at)
        """, (data["id"], data["updated_at"], data["status"], data.get("started_at"),
              data.get("canceled_at"), data.get("next_billed_at"), period.get("ends_at"),
              change.get("action"), change.get("effective_at"), event.get("event_id"),
              event.get("event_type"), event.get("occurred_at")))
        cur.execute("RELEASE SAVEPOINT subscription_history")
        return True
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT subscription_history")
        cur.execute("RELEASE SAVEPOINT subscription_history")
        logging.getLogger(__name__).warning("Subscription history unavailable")
        return False
    finally:
        cur.close()
