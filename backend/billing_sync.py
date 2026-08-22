"""Paddle is the source of truth, and this is what goes and asks it.

ROUND 59. Webhooks are the fast path and they are not a record. They get
dropped, they arrive out of order, they fire while a deploy is restarting, and
when one is lost NOTHING ever revisits the account — the wrong state simply
becomes permanent. Two live examples on Jul 29 2026, on a customer base of six
subscriptions:

  * user 205 — Paddle: `past_due`, card refused for `not_enough_balance`.
    Us: `converted`, subscribed, holding 2,000 credits.
  * user 60  — Paddle: `canceled` twelve hours earlier.
    Us: still subscribed.

Neither was a bug in the webhook handler's logic (well, 205 was — see
billing.py). Both were states nothing was ever going to re-check. So this
module makes the DB match Paddle on a schedule and on demand, and it is the
answer to "the admin and Paddle disagree" as a CLASS, not one at a time.

WHAT IT WILL AND WILL NOT DO. It never invents a downgrade from silence: a
network error, a timeout or a missing API key leaves the account exactly as it
was and is reported as `unreachable`, because "Paddle did not answer" is not
evidence a customer stopped paying. It acts only on an answer.

THE DAILY TICK also does the thing you cannot do from a webhook — keep trying.
Paddle's own dunning retries a refused card up to 7 times over 30 days
(Retain), and there is no public API to force an attempt, so what recovers the
money is the CUSTOMER: one email a day, capped, carrying a real Paddle
update-payment-method checkout link for their subscription. The decline this
was built for was `not_enough_balance` — a card that will work fine tomorrow,
if anyone tells them.
"""

import datetime
import os
import threading

import requests

import billing
import trial_state

try:                                    # pragma: no cover - optional dep
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:                       # pragma: no cover
    BackgroundScheduler = None


# Its own lock id — the newsletter's (918273645) is held for the length of a
# send run, and sharing it would make each job silently skip the other's day.
TICK_LOCK_ID = 918273646

SYNC_TIMEOUT = 20

# Bounds the tick. Every account past this waits for the next hour rather than
# holding the lock and the Paddle rate limit open indefinitely. Logged when it
# bites — a silent cap reads as "everything is in sync" when it is not.
MAX_SUBS_PER_TICK = 300


def _base():
    return ("https://sandbox-api.paddle.com"
            if os.environ.get("PADDLE_MODE") == "sandbox"
            else "https://api.paddle.com")


def _headers():
    key = os.environ.get("PADDLE_API_KEY")
    if not key:
        return None
    return {"Authorization": f"Bearer {key}"}


# ── Reading Paddle ───────────────────────────────────────────────────────────

def fetch_subscription(subscription_id):
    """(data, error). `error` is None only when Paddle actually answered."""
    h = _headers()
    if not h:
        return None, "no_api_key"
    try:
        r = requests.get(f"{_base()}/subscriptions/{subscription_id}",
                         headers=h, timeout=SYNC_TIMEOUT)
    except requests.RequestException as e:
        return None, f"unreachable: {e}"
    if r.status_code == 404:
        return None, "not_found"
    if r.status_code != 200:
        return None, f"http_{r.status_code}"
    return (r.json().get("data") or {}), None


def fetch_transactions(subscription_id, limit=30):
    h = _headers()
    if not h:
        return []
    try:
        r = requests.get(f"{_base()}/transactions",
                         params={"subscription_id": subscription_id,
                                 "per_page": limit},
                         headers=h, timeout=SYNC_TIMEOUT)
        if r.status_code != 200:
            return []
        return r.json().get("data") or []
    except requests.RequestException:
        return []


def update_payment_method_link(subscription_id):
    """A real Paddle checkout the customer can use to fix their card.

    `GET /subscriptions/{id}/update-payment-method-transaction` mints (or
    returns) a transaction for exactly this purpose. Paddle has NO endpoint to
    force a retry — its dunning engine owns the attempts — so this link is the
    only lever we actually hold over a refused payment, which is why the daily
    email is built around it rather than around a "we tried again" claim we
    could not make truthfully.

    Returns (checkout_url, transaction_id). Either may be None: the hosted
    checkout URL only exists when a default payment link is set in Paddle
    (Checkout > Settings), and the frontend can open the transaction id with
    Paddle.js regardless.
    """
    h = _headers()
    if not h or not subscription_id:
        return None, None
    try:
        r = requests.get(
            f"{_base()}/subscriptions/{subscription_id}"
            f"/update-payment-method-transaction",
            headers=h, timeout=SYNC_TIMEOUT)
        if r.status_code != 200:
            print(f"⚠️ [billing_sync] update-payment-method for "
                  f"{subscription_id}: HTTP {r.status_code} "
                  f"{(r.text or '')[:200]}", flush=True)
            return None, None
        d = r.json().get("data") or {}
        return ((d.get("checkout") or {}).get("url"), d.get("id"))
    except requests.RequestException as e:
        print(f"⚠️ [billing_sync] update-payment-method for "
              f"{subscription_id} unreachable: {e}", flush=True)
        return None, None


def _price_id_from_items(data):
    for it in (data.get("items") or []):
        pid = (it.get("price") or {}).get("id") or it.get("price_id")
        if pid:
            return pid
    return None


def _plan_from_items(data):
    try:
        from routes.paddle_webhook import PRICE_TO_PLAN
    except Exception:
        return None
    pid = _price_id_from_items(data)
    if pid and pid in PRICE_TO_PLAN:
        return PRICE_TO_PLAN[pid]
    return None


def _billing_period(data):
    """'monthly' or 'yearly', from the subscription's own billing cycle."""
    cycle = data.get("billing_cycle") or {}
    interval = (cycle.get("interval") or "month").lower()
    freq = cycle.get("frequency") or 1
    if interval == "year" or (interval == "month" and freq == 12):
        return "yearly"
    return "monthly"


# ── Reconciling one account ──────────────────────────────────────────────────

def reconcile_user(conn, row, fetch_all_transactions=False):
    """Make one user's billing columns match Paddle. Returns a report dict.

    `row` needs id, email, subscription_id, plan, is_subscribed,
    billing_status, credits_balance, credits_monthly, trial_status.

    Every write goes through `conn` and the local helpers at the bottom of this
    file rather than models.update_user_subscription_status — that one calls
    models.get_db(), which reads flask.g, and the hourly tick runs on a
    scheduler thread with no request and no app context. It would raise there
    on the first account it tried to fix, which is the one place this has to
    work unattended.
    """
    user_id = row["id"]
    sub_id = row.get("subscription_id")
    report = {"user_id": user_id, "email": row.get("email"),
              "subscription_id": sub_id, "changes": []}

    if not sub_id:
        return report

    data, err = fetch_subscription(sub_id)
    if err:
        # NEVER downgrade on silence. A timeout is not a cancellation, and the
        # one thing worse than an admin that disagrees with Paddle is one that
        # cuts off paying customers whenever Paddle has a bad minute.
        report["error"] = err
        if err == "not_found":
            # A definite 404 is an ANSWER, not silence — this Paddle account
            # has never heard of the subscription we are billing against. Three
            # live `plus` accounts are in exactly this state (sandbox-era ids
            # against a live key), each holding 800 credits.
            #
            # It is still not acted on. "Paddle does not recognise this id"
            # could equally mean the id is stale and the customer is fine, and
            # a downgrade is the one mistake here that costs a real person
            # access they paid for. So it is RECORDED and surfaced in the admin
            # contradiction list, where a human decides — which is the whole
            # difference between this and the state it replaced, where nothing
            # showed the disagreement at all.
            billing.set_status(conn, user_id, "not_in_paddle")
            report["changes"].append(
                "flagged: Paddle does not recognise this subscription id")
        return report

    status = (data.get("status") or "").lower()
    plan = _plan_from_items(data) or row.get("plan")
    period = _billing_period(data)
    report.update({"paddle_status": status, "plan": plan, "period": period})

    # Backfill the money ledger. Bounded on purpose: once an account has a
    # recorded payment and a healthy status, webhooks keep it current and this
    # call is pure cost. Problem accounts and never-paid ones get it every tick,
    # which is exactly where the truth is in question.
    if (fetch_all_transactions
            or status in billing.FAILING_STATUSES
            or not billing.subscription_has_paid(conn, sub_id)):
        for txn in fetch_transactions(sub_id):
            billing.record_transaction(conn, user_id, txn)

    was = (row.get("billing_status") or "")
    if was != status:
        report["changes"].append(f"billing_status {was or 'unknown'} → {status}")

    # ── Canceled: gone, and no longer past_due ──────────────────────────────
    if status == "canceled":
        if row.get("is_subscribed"):
            trial_state.record_cancel(conn, user_id, sub_id)
            _downgrade(conn, user_id)
            report["changes"].append("downgraded (Paddle says canceled)")
        billing.clear_billing(conn, user_id)
        return report

    # ── Refused: the card did not work ──────────────────────────────────────
    if status in billing.FAILING_STATUSES:
        reason = _latest_decline_reason(conn, sub_id)
        outcome = billing.record_failure(conn, user_id, sub_id, plan, reason,
                                         status=status)
        trial_state.record_payment_failure(conn, user_id, sub_id)
        report["failure"] = outcome
        if outcome.get("action") == "lifted" and row.get("is_subscribed"):
            report["changes"].append(f"credits lifted ({reason or 'declined'})")
        elif outcome.get("action") == "grace":
            # A customer with a payment history keeps everything while Paddle
            # retries — but not forever. Once the grace runs out the pool goes
            # the same way as anyone else's, because at that point the money
            # genuinely has not arrived.
            if _grace_expired(conn, user_id):
                billing.lift_paid_credits(conn, user_id)
                report["changes"].append(
                    f"grace expired after {billing.PAID_GRACE_DAYS}d "
                    f"— credits lifted")
        return report

    # ── Trialing ────────────────────────────────────────────────────────────
    if status == "trialing":
        billing.set_status(conn, user_id, status, plan, period)
        trial_state.sync_from_subscription(conn, user_id, plan, sub_id, data)
        clamped = _clamp_trial_credits(conn, user_id, plan)
        if clamped is not None:
            report["changes"].append(
                f"trial credits clamped {clamped[0]:.0f} → {clamped[1]:.0f}")
        return report

    # ── Active ──────────────────────────────────────────────────────────────
    if status == "active":
        billing.set_status(conn, user_id, status, plan, period)
        paid = billing.subscription_has_paid(conn, sub_id, user_id)
        if paid:
            billing.record_recovery(conn, user_id, plan)
            trial_state.record_paid_conversion(conn, user_id, sub_id)
        else:
            # Active with no money recorded. Usually a charge in flight
            # (seconds old); if it stays this way the next tick's transaction
            # backfill will have found the failure and this becomes past_due.
            report["note"] = "active but no payment recorded yet"
        if paid and not row.get("is_subscribed"):
            # Paddle says active AND the ledger shows money — restore the plan.
            # Gated on `paid` on purpose: "active but nothing collected yet" is
            # the exact state that produced the round-59 bug, and re-granting
            # a full pool from it would rebuild it here.
            _activate(conn, user_id, plan, sub_id,
                      _parse(data.get("next_billed_at")),
                      price_id=_price_id_from_items(data))
            report["changes"].append("re-subscribed (Paddle says active, "
                                     "payment on record)")
    return report


def _parse(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _downgrade(conn, user_id):
    """Back to free, credits clawed back. The connection-local twin of
    models.update_user_subscription_status(..., False, ...) plus
    paddle_webhook._clawback_monthly_credits — both of which read flask.g and
    so cannot be called from the scheduler thread."""
    cur = conn.cursor()
    cur.execute("""UPDATE users
                      SET is_subscribed         = 0,
                          subscription_expiry   = NULL,
                          subscription_id       = NULL,
                          plan                  = 'free',
                          credits_monthly_limit = 0,
                          credits_monthly       = 0,
                          credits_balance       = COALESCE(credits_daily, 0)
                                                + COALESCE(credits_bonus, 0)
                    WHERE id = %s""", (user_id,))
    conn.commit()
    cur.close()


def _activate(conn, user_id, plan, subscription_id, expiry, price_id=None):
    """Restore a paid plan in full. Mirrors the `is_subscribed and not
    preserve` branch of models.update_user_subscription_status, including the
    20-a-day subscriber top-up."""
    from routes.paddle_webhook import credits_for_price, SUB_DAILY_CREDITS
    monthly = credits_for_price(price_id, plan)
    cur = conn.cursor()
    cur.execute("""UPDATE users
                      SET is_subscribed         = 1,
                          subscription_expiry   = %s,
                          subscription_id       = %s,
                          plan                  = %s,
                          credits_monthly_limit = %s,
                          credits_monthly       = %s,
                          credits_daily         = %s,
                          credits_balance       = %s + COALESCE(credits_bonus, 0) + %s
                    WHERE id = %s""",
                (expiry, subscription_id, plan, monthly, monthly,
                 SUB_DAILY_CREDITS, SUB_DAILY_CREDITS, monthly, user_id))
    conn.commit()
    cur.close()


def _latest_decline_reason(conn, subscription_id):
    cur = conn.cursor()
    cur.execute("""SELECT error_code FROM payments
                    WHERE subscription_id = %s AND error_code IS NOT NULL
                    ORDER BY COALESCE(occurred_at, created_at) DESC LIMIT 1""",
                (subscription_id,))
    row = cur.fetchone()
    cur.close()
    return billing._scalar(row)


def _grace_expired(conn, user_id):
    cur = conn.cursor()
    cur.execute("""SELECT payment_failed_at < NOW() - (%s || ' days')::interval
                     FROM users WHERE id = %s""",
                (billing.PAID_GRACE_DAYS, user_id))
    row = cur.fetchone()
    cur.close()
    return bool(billing._scalar(row))


def _clamp_trial_credits(conn, user_id, plan):
    """A trial may not hold more than its allowance. Returns (before, after).

    THIS IS A REAL LEAK, not a theoretical one. On Jul 29 2026 two live
    trialling accounts held 3,924.80 and 2,000.00 credits against allowances of
    400 and 200 — ten times the cap, or roughly $19.60 and $10.00 of model
    spend available to accounts that had paid nothing and had already scheduled
    a cancellation.

    The cause is the interaction of two correct-looking things: the grant is a
    SET (which is what makes Paddle's repeated events idempotent), and a repeat
    event for a running trial passes preserve_credits=True — which updates
    `credits_monthly_limit` but deliberately leaves `credits_monthly` alone. So
    if any single event ever granted the full pool (a transaction.completed
    arriving before subscription.created does exactly that), the limit is
    corrected afterwards and the POOL never is. The webhook comment calls that
    race self-healing; it heals the limit, not the balance.
    """
    import credits as credits_mod
    allowance = credits_mod.trial_allowance(plan)
    cur = conn.cursor()
    cur.execute("""SELECT credits_monthly, credits_balance,
                          credits_monthly_limit
                     FROM users WHERE id = %s""", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        return None
    monthly = float((row["credits_monthly"] if isinstance(row, dict)
                     else row[0]) or 0)
    balance = float((row["credits_balance"] if isinstance(row, dict)
                     else row[1]) or 0)
    try:
        if isinstance(row, dict):
            stored = int(row.get("credits_monthly_limit") or 0)
        else:
            stored = int(row[2] or 0) if len(row) > 2 else 0
    except (TypeError, ValueError, IndexError):
        stored = 0
    # A live trial sold on the previous price keeps that allowance. Never
    # shrink it to the new shopfront's 10% slice.
    if stored > allowance:
        allowance = stored
    if allowance <= 0 or monthly <= allowance:
        cur.close()
        return None
    # Clamp the pool, and take the same amount off the balance rather than
    # recomputing it — the user may legitimately have spent some of the trial
    # already, and recomputing would hand those credits back.
    over = monthly - allowance
    cur.execute("""UPDATE users
                      SET credits_monthly       = %s,
                          credits_monthly_limit = %s,
                          credits_balance       = GREATEST(0, credits_balance - %s)
                    WHERE id = %s""",
                (allowance, allowance, over, user_id))
    conn.commit()
    cur.close()
    return (balance, max(0.0, balance - over))


# ── Reconciling everybody ────────────────────────────────────────────────────

def reconcile_all(conn, full=False):
    """Sync every account that has a subscription. Returns a report."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, subscription_id, plan, is_subscribed,
               billing_status, credits_balance, credits_monthly, trial_status
          FROM users
         WHERE subscription_id IS NOT NULL
            OR is_subscribed = 1
            OR billing_status IS NOT NULL
         ORDER BY id
         LIMIT %s
    """, (MAX_SUBS_PER_TICK + 1,))
    rows = [_as_dict(r, cur) for r in cur.fetchall()]
    cur.close()

    truncated = len(rows) > MAX_SUBS_PER_TICK
    if truncated:
        rows = rows[:MAX_SUBS_PER_TICK]
        print(f"⚠️ [billing_sync] more than {MAX_SUBS_PER_TICK} subscriptions "
              f"— syncing the first {MAX_SUBS_PER_TICK} this tick", flush=True)

    reports, changed, errors = [], 0, 0
    for row in rows:
        try:
            rep = reconcile_user(conn, row, fetch_all_transactions=full)
        except Exception as e:                              # pragma: no cover
            billing._safe_rollback(conn)
            print(f"⚠️ [billing_sync] user {row.get('id')} failed: {e}",
                  flush=True)
            rep = {"user_id": row.get("id"), "email": row.get("email"),
                   "error": str(e)}
        if rep.get("error"):
            errors += 1
        if rep.get("changes"):
            changed += 1
        reports.append(rep)
    return {"checked": len(rows), "changed": changed, "errors": errors,
            "truncated": truncated, "reports": reports}


def _as_dict(row, cur):
    if isinstance(row, dict):
        return dict(row)
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ── The daily nudge ──────────────────────────────────────────────────────────

def _frontend():
    return os.getenv("FRONTEND_URL", "https://valmera.io").rstrip("/")


def _dunning_html(name, plan, reason, link):
    """The decline email. Design language of the round-49 lifecycle mail:
    #0b0b0b surfaces, #1e1e1e hairlines, a WHITE pill CTA.

    Deliberately NOT sent through routes/newsletter.py: that path attaches a
    List-Unsubscribe header and respects the marketing opt-out. This is a
    transactional service message about money the customer owes on a
    subscription they asked for — suppressing it because they unsubscribed from
    product news would mean silently letting their account lapse.
    """
    from billing import decline_message
    said = decline_message(reason)
    label = trial_state.plan_label(plan)
    cta = link or f"{_frontend()}/account"
    return f"""
<div style="background:#000;padding:32px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#0b0b0b;border:1px solid #1e1e1e;border-radius:14px;padding:32px;">
    <div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#8a8a8a;margin-bottom:18px;">Valmera · Billing</div>
    <h1 style="color:#fff;font-size:22px;line-height:1.3;margin:0 0 14px;">Your payment didn't go through</h1>
    <p style="color:#c9c9c9;font-size:15px;line-height:1.6;margin:0 0 14px;">
      {said} We couldn't collect for your {label} plan, so your editing credits are on hold.
    </p>
    <p style="color:#c9c9c9;font-size:15px;line-height:1.6;margin:0 0 22px;">
      Your bank will be tried again automatically over the next few days — but the fastest fix is to update your card. It takes about thirty seconds, and everything picks up exactly where you left it.
    </p>
    <a href="{cta}" style="display:inline-block;background:#fff;color:#000;text-decoration:none;font-weight:600;font-size:15px;padding:13px 26px;border-radius:999px;">Update payment method</a>
    <p style="color:#6e6e6e;font-size:13px;line-height:1.6;margin:24px 0 0;border-top:1px solid #1e1e1e;padding-top:18px;">
      Didn't mean to subscribe? Ignore this and the subscription cancels itself — you won't be charged.
    </p>
  </div>
</div>"""


def send_dunning_email(conn, user_id, email, plan, reason, subscription_id):
    """One decline email. Returns True if Brevo accepted it."""
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key or not email:
        return False
    link, _txn = update_payment_method_link(subscription_id)
    payload = {
        "sender": {"name": os.getenv("FROM_NAME", "Valmera"),
                   "email": os.getenv("FROM_EMAIL", "support@valmera.io")},
        "to": [{"email": email}],
        "subject": "Your Valmera payment didn't go through",
        "htmlContent": _dunning_html(email, plan, reason, link),
    }
    try:
        res = requests.post("https://api.brevo.com/v3/smtp/email",
                            json=payload,
                            headers={"accept": "application/json",
                                     "api-key": api_key,
                                     "content-type": "application/json"},
                            timeout=15)
    except requests.RequestException as e:
        print(f"⚠️ [dunning] send to {email} failed (network): {e}", flush=True)
        return False
    if res.status_code != 201:
        print(f"⚠️ [dunning] send to {email} failed: HTTP {res.status_code} "
              f"{(res.text or '')[:300]}", flush=True)
        return False
    cur = conn.cursor()
    cur.execute("""UPDATE users
                      SET dunning_emailed_at  = NOW(),
                          dunning_email_count = dunning_email_count + 1
                    WHERE id = %s""", (user_id,))
    conn.commit()
    cur.close()
    print(f"📧 [dunning] emailed {email} ({reason or 'declined'})", flush=True)
    return True


def run_dunning(conn):
    """Email every past_due account, at most once a day, up to the cap.

    The 20-hour window rather than 24: the tick runs hourly and a strict
    24-hour test would drift a full hour later every day until the mail
    arrived at 3am.
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, email, billing_plan, plan, payment_failed_reason,
               subscription_id
          FROM users
         WHERE billing_status = ANY(%s)
           AND payment_failed_at IS NOT NULL
           AND dunning_email_count < %s
           AND (dunning_emailed_at IS NULL
                OR dunning_emailed_at < NOW() - INTERVAL '20 hours')
    """, (list(billing.FAILING_STATUSES), billing.MAX_DUNNING_EMAILS))
    rows = [_as_dict(r, cur) for r in cur.fetchall()]
    cur.close()
    sent = 0
    for r in rows:
        if send_dunning_email(conn, r["id"], r["email"],
                              r.get("billing_plan") or r.get("plan"),
                              r.get("payment_failed_reason"),
                              r.get("subscription_id")):
            sent += 1
    return {"eligible": len(rows), "sent": sent}


# ── The tick ─────────────────────────────────────────────────────────────────

def run_billing_tick(conn=None, dry_run=False):
    """Reconcile with Paddle, then nudge whoever owes money.

    Advisory-locked because all three gunicorn workers run a scheduler. Unlike
    the newsletter tick this is NOT once-a-day-gated — reconciling is cheap,
    idempotent and the whole point is that the admin stops being stale. The
    once-a-day part is inside run_dunning, where it belongs.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    own = conn is None
    if own:
        # Billing's scheduler lock is session-scoped.  Ordinary request and
        # worker traffic may use Render's transaction-pooled PgBouncer URL,
        # but this single hourly connection must remain direct until the lock
        # is released.
        conn = psycopg2.connect(
            os.environ.get("DIRECT_DATABASE_URL")
            or os.environ["DATABASE_URL"],
                                cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (TICK_LOCK_ID,))
        got = billing._scalar(cur.fetchone())
        cur.close()
        if not got:
            return {"skipped": "another worker holds the lock"}
        try:
            if not billing.columns_ready(conn):
                return {"skipped": "migration 012 not applied"}
            result = reconcile_all(conn)
            if not dry_run:
                result["dunning"] = run_dunning(conn)
            return result
        finally:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (TICK_LOCK_ID,))
            conn.commit()
            cur.close()
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


_scheduler = None
_lock = threading.Lock()


def start_billing_scheduler(app):
    """Hourly reconcile. Called once per gunicorn worker; the advisory lock
    makes the extra callers no-ops."""
    global _scheduler
    if os.getenv("BILLING_SYNC_ENABLED", "1") not in ("1", "true", "True"):
        app.logger.info("billing sync disabled by env")
        return
    if BackgroundScheduler is None:
        app.logger.warning("apscheduler missing — billing sync not started")
        return
    with _lock:
        if _scheduler is not None:
            return

        def job():
            try:
                out = run_billing_tick()
                if out.get("changed") or out.get("errors"):
                    app.logger.info("billing sync: %s", out)
            except Exception as e:      # never let the scheduler thread die
                app.logger.error("billing sync tick failed: %s", e)

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(job, "interval", hours=1,
                      id="billing_sync", replace_existing=True,
                      next_run_time=datetime.datetime.utcnow()
                      + datetime.timedelta(minutes=2))
        sched.start()
        _scheduler = sched
        app.logger.info("billing sync scheduler started")
