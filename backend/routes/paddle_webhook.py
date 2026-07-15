import hashlib
import hmac
import os
import time

from flask import Blueprint, request
from models import get_db, update_user_subscription_status
from datetime import datetime

paddle_webhook = Blueprint('paddle_webhook', __name__)

PLAN_CREDITS = {
    'plus':  800,
    'pro':   2400,
    'ultra': 5000,
    'titan': 10000,
    'ace':   30000,
    'free':  0,
}

# The plan/credits granted MUST be derived from the price the user actually
# paid, never from client-supplied custom_data.plan. Paddle.js lets a visitor
# on the approved domain open an inline checkout for the $20 plus price while
# attaching customData {plan:'ace'}; the resulting webhook is genuinely signed,
# so signature verification is no defense — only pricing the grant off the real
# price_id is. This reverse map (built from the same PLANS the checkout uses)
# covers both monthly and yearly price IDs, including the retired tiers'
# grandfathered prices so their renewals still resolve correctly.
try:
    from routes.paddle import PLANS as _PADDLE_PLANS
except Exception:                       # pragma: no cover - import safety
    _PADDLE_PLANS = {}
PRICE_TO_PLAN = {}
for _name, _cfg in (_PADDLE_PLANS or {}).items():
    for _k in ('price_id', 'yearly_price_id'):
        _pid = (_cfg or {}).get(_k)
        if _pid:
            PRICE_TO_PLAN[_pid] = _name


def _plan_from_data(data):
    """Authoritative plan from the PAID price id in the event's line items.
    Returns the plan name or None if no known price is present."""
    for it in (data.get('items') or []):
        price = it.get('price') or {}
        pid = price.get('id') or it.get('price_id')
        if pid and pid in PRICE_TO_PLAN:
            return PRICE_TO_PLAN[pid]
    return None


def _user_id_by_subscription(subscription_id):
    """Adjustment/refund events carry no custom_data — find the user via the
    subscription id we stored at activation."""
    if not subscription_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE subscription_id = %s LIMIT 1",
                (subscription_id,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def _stored_subscription_id(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT subscription_id FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None

# Paddle signs every webhook (Paddle-Signature: "ts=...;h1=...", where h1 is
# HMAC-SHA256 of "ts:raw_body" with the endpoint's secret key from
# Paddle > Developer tools > Notifications). Without verification anyone who
# reads the URL can grant themselves any plan. Enforced when
# PADDLE_WEBHOOK_SECRET is set; until then requests pass with a loud warning
# so payments don't break before the env var is configured.
PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET", "")


def _clawback_monthly_credits(user_id):
    """Zero the monthly credit pool and recompute the balance so a canceled or
    refunded user can't keep spending the credits they no longer paid for.
    update_user_subscription_status only clears the LIMIT, not the live pool —
    without this the monthly credits survive until the next daily refresh
    silently re-adds them into the balance."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET credits_monthly = 0,
            credits_balance = COALESCE(credits_daily, 0)
                            + COALESCE(credits_bonus, 0)
        WHERE id = %s
    """, (user_id,))
    conn.commit()
    cur.close()


def _verify_paddle_signature(req):
    header = req.headers.get("Paddle-Signature", "")
    parts = dict(p.split("=", 1) for p in header.split(";") if "=" in p)
    ts, h1 = parts.get("ts"), parts.get("h1")
    if not ts or not h1:
        return False
    try:
        if abs(time.time() - int(ts)) > 300:   # stale/replayed event
            return False
    except ValueError:
        return False
    signed = f"{ts}:".encode() + req.get_data()
    expected = hmac.new(PADDLE_WEBHOOK_SECRET.encode(), signed,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


@paddle_webhook.route('/webhook/paddle', methods=['POST'])
def handle_webhook():
    if PADDLE_WEBHOOK_SECRET:
        if not _verify_paddle_signature(request):
            print("⛔ Paddle webhook rejected: bad or missing signature")
            return 'Invalid signature', 403
    else:
        print("⚠️  PADDLE_WEBHOOK_SECRET is not set — webhook signature "
              "NOT verified. Set it in the Render env ASAP.")
    payload = request.get_json(force=True)
    print("🔔 Webhook received:", payload.get('event_type'))

    event_type = payload.get('event_type')
    data = payload.get('data', {})

    # Paddle BILLING event names (not the Classic *_refunded/_failed alerts,
    # which never fire on this integration). Grants activate the plan; refunds
    # arrive as adjustment.* with action='refund'; cancellations arrive as
    # subscription.canceled at period end; failed charges (past_due) are grace
    # (Paddle retries) and downgrade only when the sub is ultimately canceled.
    GRANT_EVENTS = ('transaction.completed', 'transaction.paid',
                    'subscription.created', 'subscription.updated',
                    'subscription.activated')
    REFUND_EVENTS = ('adjustment.created', 'adjustment.updated')
    if event_type not in GRANT_EVENTS + REFUND_EVENTS + (
            'subscription.canceled', 'subscription.past_due',
            'transaction.payment_failed'):
        return 'OK', 200

    custom_data = data.get('custom_data') or {}
    subscription_id = data.get('subscription_id') or data.get('id')

    if event_type in GRANT_EVENTS:
        user_id = custom_data.get('user_id')
        if not user_id:
            return 'OK', 200
        # Plan/credits come from the PAID price, never from custom_data.plan.
        plan = _plan_from_data(data)
        if not plan:
            print("⛔ Grant event with no known price id — granting 0 credits")
            plan = 'free'
        billing = custom_data.get('billing', 'monthly')
        monthly_credits = PLAN_CREDITS.get(plan, 0)
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(
                    expiry_date_str.replace("Z", "+00:00"))
            except Exception as e:
                print(f"⚠️ Date parse error: {e}")
        update_user_subscription_status(
            user_id, True, expiry_date, subscription_id, plan, monthly_credits)
        print(f"✅ User {user_id} on plan {plan} ({billing}) activated (from price). Credits: {monthly_credits}")

    elif event_type in REFUND_EVENTS:
        if (data.get('action') or '').lower() != 'refund':
            return 'OK', 200            # credit/chargeback adjustments ignored
        user_id = _user_id_by_subscription(subscription_id)
        if not user_id:
            return 'OK', 200
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        _clawback_monthly_credits(user_id)
        print(f"⚠️ User {user_id} refunded — reverted to free + credits clawed back")

    elif event_type == 'subscription.canceled':
        user_id = custom_data.get('user_id') or \
            _user_id_by_subscription(subscription_id)
        if not user_id:
            return 'OK', 200
        # Only downgrade if this cancellation is for the user's CURRENT
        # subscription — a stale canceled event for an old, already-replaced
        # subscription must not wipe the pool they just paid for on a new one.
        stored = _stored_subscription_id(user_id)
        if stored and subscription_id and stored != subscription_id:
            print(f"↩︎ Stale cancel for {subscription_id} (user {user_id} now on {stored}) — ignored")
            return 'OK', 200
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        _clawback_monthly_credits(user_id)
        print(f"⚠️ User {user_id} canceled — reverted to free + credits clawed back")

    else:
        # subscription.past_due / transaction.payment_failed: dunning grace,
        # Paddle retries the charge; no downgrade until it truly cancels.
        print(f"ℹ️ Dunning event {event_type} for sub {subscription_id} — no change (grace)")

    return 'OK', 200