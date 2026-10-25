"""Backend side of the Claude-funded trial (owner decision D2, 25 Oct 2026).

The worker (worker/claude_trial.py) grants, meters and stops the trial. This
module is what the backend needs from the same tables (migration 040):

* ``account_status(cur, user_id)`` — the ONE per-account running total
  (``trial_budgets``), for track B's export / second-request gate and the
  Studio's "keep going" card. Read-only, fails closed (no row, no table or
  any error reads as "no trial budget left").
* ``pool_status(cur)`` / ``summary(conn)`` — the pool against its $200, for
  the cockpit line.
* ``send_due()`` + ``start_scheduler(app)`` — drains ``trial_alerts`` (pool
  at 50/80/95 %, Anthropic billing/auth refusals) to the admin address
  through the existing transactional sender (founder_alert, Brevo). Never a
  customer. Threshold rows are also (re)derived here from the ledger, so a
  worker that crashed between metering and queueing cannot hide one.
"""

import datetime
import os
import threading

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:                                      # pragma: no cover
    BackgroundScheduler = None

POOL_USD_DEFAULT = float(os.getenv("CLAUDE_TRIAL_POOL_USD", "200"))
ALERT_PCTS = (50, 80, 95)
MAX_BATCH = 10
STALE_CLAIM_MINUTES = 15


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def tables_ready(cur):
    cur.execute("SELECT to_regclass('public.trial_budgets') AS a, "
                "to_regclass('public.trial_budget_days') AS b")
    row = cur.fetchone()
    return bool(_v(row, "a", 0) and _v(row, "b", 1))


def account_status(cur, user_id):
    """{'granted', 'cap_usd', 'spent_usd', 'remaining_usd', 'exhausted'}.

    The shared contract with track B: remaining = cap_usd - spent_usd on
    trial_budgets. Any failure reads as not granted / nothing remaining."""
    empty = {"granted": False, "cap_usd": 0.0, "spent_usd": 0.0,
             "remaining_usd": 0.0, "exhausted": False}
    try:
        cur.execute("SAVEPOINT claude_trial_status")
        if not tables_ready(cur):
            cur.execute("RELEASE SAVEPOINT claude_trial_status")
            return empty
        cur.execute("""SELECT cap_usd, spent_usd, exhausted_at
                         FROM trial_budgets WHERE user_id = %s""",
                    (int(user_id),))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT claude_trial_status")
    except Exception as exc:                             # pragma: no cover
        try:
            cur.execute("ROLLBACK TO SAVEPOINT claude_trial_status")
            cur.execute("RELEASE SAVEPOINT claude_trial_status")
        except Exception:
            pass
        print(f"[claude_trial] status lookup failed for {user_id}: {exc}",
              flush=True)
        return empty
    if not row:
        return empty
    cap = float(_v(row, "cap_usd", 0) or 0)
    spent = float(_v(row, "spent_usd", 1) or 0)
    return {"granted": True, "cap_usd": round(cap, 4),
            "spent_usd": round(spent, 6),
            "remaining_usd": round(max(0.0, cap - spent), 6),
            "exhausted": bool(_v(row, "exhausted_at", 2)) or spent >= cap - 0.005}


def _cycle(cur):
    """The Anthropic credit cycle the pool belongs to (claude_credit.py): the
    credit has no rollover, so the pool and its alerts restart each cycle."""
    import claude_credit
    conn = getattr(cur, "connection", None)
    cfg = None
    if conn is not None:
        try:
            cur.execute("SAVEPOINT claude_trial_cycle")
            cfg = claude_credit.read_config(conn)
            cur.execute("RELEASE SAVEPOINT claude_trial_cycle")
        except Exception:
            try:
                cur.execute("ROLLBACK TO SAVEPOINT claude_trial_cycle")
                cur.execute("RELEASE SAVEPOINT claude_trial_cycle")
            except Exception:
                pass
            cfg = None
    return claude_credit.cycle_window(cfg or claude_credit.parse_config(None))


def pool_status(cur, pool_usd=None):
    pool_usd = float(pool_usd or POOL_USD_DEFAULT)
    start, end = _cycle(cur)
    cur.execute("""SELECT
          COALESCE(SUM(spent_usd) FILTER (
              WHERE day >= %(start)s AND day < %(end)s), 0) AS spent,
          COALESCE(SUM(spent_usd) FILTER (
              WHERE day >= date_trunc('month', (NOW() AT TIME ZONE 'UTC'))::date), 0) AS month,
          COALESCE(SUM(spent_usd) FILTER (
              WHERE day = (NOW() AT TIME ZONE 'UTC')::date), 0) AS today,
          COALESCE(SUM(grants) FILTER (
              WHERE day >= %(start)s AND day < %(end)s), 0) AS grants,
          COALESCE(SUM(grants) FILTER (
              WHERE day = (NOW() AT TIME ZONE 'UTC')::date), 0) AS grants_today
        FROM trial_budget_days""", {"start": start.date(), "end": end.date()})
    row = cur.fetchone()
    spent = float(_v(row, "spent", 0) or 0)
    return {"pool_usd": pool_usd, "spent_usd": round(spent, 4),
            "cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
            "pct": round(100.0 * spent / pool_usd, 1) if pool_usd else None,
            "month_usd": round(float(_v(row, "month", 1) or 0), 4),
            "today_usd": round(float(_v(row, "today", 2) or 0), 4),
            "grants": int(_v(row, "grants", 3) or 0),
            "grants_today": int(_v(row, "grants_today", 4) or 0)}


def summary(conn, pool_usd=None):
    """The cockpit line: pool, accounts, average spend against the $1."""
    with conn.cursor() as cur:
        if not tables_ready(cur):
            return {"ready": False}
        out = pool_status(cur, pool_usd)
        cur.execute("""SELECT COUNT(*) AS accounts,
                              COUNT(*) FILTER (WHERE exhausted_at IS NOT NULL)
                                  AS exhausted,
                              COALESCE(AVG(spent_usd), 0) AS avg_spent,
                              COALESCE(AVG(cap_usd), 0) AS avg_cap
                         FROM trial_budgets""")
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM trial_alerts "
                    "WHERE sent_at IS NULL")
        pending = int(_v(cur.fetchone(), "n", 0) or 0)
    avg = float(_v(row, "avg_spent", 2) or 0)
    out.update({"ready": True, "accounts": int(_v(row, "accounts", 0) or 0),
                "exhausted": int(_v(row, "exhausted", 1) or 0),
                "avg_spent_usd": round(avg, 4),
                "avg_cap_usd": round(float(_v(row, "avg_cap", 3) or 0), 4),
                "accounts_left_at_avg": (
                    int(max(0.0, out["pool_usd"] - out["spent_usd"]) / avg)
                    if avg > 0 else None),
                "alerts_pending": pending})
    return out


# ------------------------------------------------------------------ #
#  Admin alerts                                                        #
# ------------------------------------------------------------------ #

def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from database_config import preferred_database_url
    return psycopg2.connect(preferred_database_url(),
                            cursor_factory=RealDictCursor, connect_timeout=5)


def queue_threshold_alerts(cur, pool_usd=None):
    """Insert any crossed 50/80/95 % row the worker has not queued."""
    status = pool_status(cur, pool_usd)
    queued = 0
    for mark in ALERT_PCTS:
        if status["pct"] is not None and status["pct"] >= mark:
            cur.execute("""INSERT INTO trial_alerts (dedupe_key, kind, subject,
                                                     html_content)
                           VALUES (%s, 'pool_threshold', %s, %s)
                           ON CONFLICT (dedupe_key) DO NOTHING""",
                        (f"pool_{mark}_of_{int(round(status['pool_usd']))}"
                         f"_{status['cycle_end'][:10]}",
                         f"Claude trial pool at {mark}%: "
                         f"${status['spent_usd']:.2f} of "
                         f"${status['pool_usd']:.0f}",
                         f"<p>The Claude-funded trial has spent "
                         f"<b>${status['spent_usd']:.2f}</b> of its "
                         f"${status['pool_usd']:.0f} pool "
                         f"({status['pct']:.1f}%), "
                         f"{status['grants']} accounts granted.</p>"))
            queued += cur.rowcount
    return queued


def send_due(limit=MAX_BATCH, sender=None):
    """Claim and send pending alert rows. Returns {'due', 'sent'}."""
    if sender is None:
        from founder_alert import send_founder_alert_now as sender
    try:
        conn = _connect()
    except Exception as exc:
        print(f"[claude_trial] alert database unavailable: {exc}", flush=True)
        return {"due": 0, "sent": 0, "error": str(exc)[:200]}
    sent = due = 0
    try:
        with conn.cursor() as cur:
            if not tables_ready(cur):
                conn.rollback()
                return {"due": 0, "sent": 0, "ready": False}
            cur.execute("SELECT to_regclass('public.trial_alerts') AS t")
            if not _v(cur.fetchone(), "t", 0):
                conn.rollback()
                return {"due": 0, "sent": 0, "ready": False}
            queue_threshold_alerts(cur)
            cur.execute(f"""
                UPDATE trial_alerts
                   SET status = 'sending', attempt_count = attempt_count + 1,
                       last_attempt_at = NOW()
                 WHERE id IN (
                       SELECT id FROM trial_alerts
                        WHERE sent_at IS NULL AND attempt_count < 8
                          AND (status IN ('pending', 'failed')
                               OR (status = 'sending' AND last_attempt_at
                                   < NOW() - INTERVAL '{STALE_CLAIM_MINUTES} minutes'))
                        ORDER BY created_at LIMIT %s
                        FOR UPDATE SKIP LOCKED)
             RETURNING id, subject, html_content""", (int(limit),))
            rows = cur.fetchall()
        conn.commit()
        due = len(rows)
        for row in rows:
            ok = False
            error = None
            try:
                ok = bool(sender(_v(row, "subject", 1), _v(row, "html_content", 2)))
            except Exception as exc:                     # pragma: no cover
                error = str(exc)[:300]
            with conn.cursor() as cur:
                if ok:
                    cur.execute("UPDATE trial_alerts SET status = 'sent', "
                                "sent_at = NOW() WHERE id = %s",
                                (_v(row, "id", 0),))
                    sent += 1
                else:
                    cur.execute("UPDATE trial_alerts SET status = 'failed', "
                                "last_error = %s WHERE id = %s",
                                (error or "sender returned false",
                                 _v(row, "id", 0)))
            conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[claude_trial] alert drain failed: {exc}", flush=True)
        return {"due": due, "sent": sent, "error": str(exc)[:200]}
    finally:
        conn.close()
    return {"due": due, "sent": sent}


_scheduler = None
_scheduler_lock = threading.Lock()


def start_scheduler(app):
    """Every 2 minutes; row claims make N gunicorn schedulers safe."""
    global _scheduler
    if os.getenv("CLAUDE_TRIAL_ALERTS_ENABLED", "1") not in ("1", "true"):
        return
    url = str(app.config.get("DATABASE_URL") or "")
    if not url or "//stub/" in url:          # tests and CI stub the database
        return
    if BackgroundScheduler is None:
        app.logger.warning("apscheduler missing — trial alerts not started")
        return
    with _scheduler_lock:
        if _scheduler is not None:
            return

        def job():
            try:
                result = send_due()
                if result.get("due") or result.get("error"):
                    app.logger.info("claude trial alerts: %s", result)
            except Exception as exc:                     # pragma: no cover
                app.logger.error("claude trial alert drain failed: %s", exc)

        scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
        scheduler.add_job(
            job, "interval", minutes=2, id="claude_trial_alerts",
            replace_existing=True,
            next_run_time=(datetime.datetime.now(datetime.timezone.utc)
                           + datetime.timedelta(minutes=2)))
        scheduler.start()
        _scheduler = scheduler
