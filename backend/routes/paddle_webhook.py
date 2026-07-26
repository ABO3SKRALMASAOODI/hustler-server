import hashlib
import hmac
import os
import time

import requests
from flask import Blueprint, request
from models import get_db, update_user_subscription_status
from datetime import datetime

import credits as credits_mod
import offers
import trial_state

paddle_webhook = Blueprint('paddle_webhook', __name__)


# Subscriber daily top-up, and the 0 that replaces it during a trial. Named
# here so the two callers below cannot disagree about what "no top-up" means.
SUB_DAILY_CREDITS = 20
TRIAL_DAILY_CREDITS = 0


def _trial_aware_grant(user_id, plan, subscription_id, event_type, data):
    """How many credits this grant event should leave the user holding.

    Returns (credits, daily_top_up, preserve_existing_balance, reason).

    A TRIAL gets credits.trial_allowance(plan) — 10% of the plan — and no daily
    top-up. Paddle creates the subscription at checkout and charges nothing for
    three days, so before this a trialling account could spend the ENTIRE
    monthly grant (up to $50 of model spend on Frontier) and then cancel having
    paid nothing. Conversion is what releases the rest.

    Two facts have to be read carefully to get this right:

      * The subscription's status only appears on subscription.* events.
        transaction.completed carries the TRANSACTION's status ('completed'),
        which reads as "not trialing" and would hand a trialling user the full
        pool. So a recorded trial counts as trialing too.
      * The grant is a SET. That is what makes Paddle's repeated events
        idempotent, and it is also what would refill a half-spent trial every
        time the subscription is touched — so a repeat event for a trial that
        is already running preserves the balance instead.

    The order the webhook runs in matters and is load-bearing: the grant
    happens BEFORE trial_state.sync_from_subscription records the trial. So on
    the very first subscription.created the recorded-trial check is False, the
    trial is granted fresh (not preserved), and every event after it preserves.
    That also self-heals the race where a transaction event arrives first and
    wrongly grants the full pool: the subscription.created that follows sets it
    back down to the trial slice, seconds later.
    """
    full = PLAN_CREDITS.get(plan, 0)
    status = (data.get('status') or '').lower()
    if not event_type.startswith('subscription.'):
        status = ''             # not the subscription's status — see above
    if status == 'active':
        # Paddle charged. Release the plan in full; this is also the event that
        # ends a trial, so the pool must be reset rather than preserved.
        return full, SUB_DAILY_CREDITS, False, 'paid'
    try:
        already = trial_state.is_recorded_trial(get_db(), user_id,
                                                subscription_id)
    except Exception as e:                                  # pragma: no cover
        print(f"⚠️ [trial] grant check failed for {user_id}: {e}", flush=True)
        already = False
    if status == 'trialing' or already:
        allowance = credits_mod.trial_allowance(plan)
        if already:
            return allowance, TRIAL_DAILY_CREDITS, True, 'trial (unchanged)'
        return allowance, TRIAL_DAILY_CREDITS, False, 'trial allowance'
    return full, SUB_DAILY_CREDITS, False, 'full plan'

PLAN_CREDITS = {
    # The three live tiers. Keep in step with PLANS in paddle.py (the checkout)
    # and PLAN_MONTHLY_LIMITS in credits.py (the denominator the studio shows).
    # Round 49: credits burn at TWICE the model's real cost
    # (credits.USD_PER_CREDIT = $0.005), so the cost column below is half what
    # the credit count suggests. See the margin table in paddle.py.
    'ai':     2000,     # Creator  $30  -> $10 of model cost, 67% margin
    'ai_pro': 4000,     # Pro      $50  -> $20 of model cost, 60% margin
    'ai_max': 10000,    # Frontier $100 -> $50 of model cost, 50% margin
    # 'mcp' grants 0 ON PURPOSE. Credits meter OUR model spend, and on the
    # MCP plan the customer's own key pays for the model — topping up a pool
    # they never draw from would be meaningless, and metering their key as
    # ours would overcharge them. Keep at 0 unless MCP starts using our LLM.
    'mcp':   0,
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


_PADDLE_BASE = ("https://sandbox-api.paddle.com"
                if os.environ.get('PADDLE_MODE') == 'sandbox'
                else "https://api.paddle.com")


def _user_id_by_customer_email(customer_id):
    """The account that owns the email Paddle billed, or None.

    One Paddle API call on a rare event (an activation), which is cheap next
    to letting a forged custom_data.user_id decide who gets a paid plan.
    Returns None on ANY failure so a Paddle hiccup degrades to the previous
    behaviour (trust custom_data) rather than silently dropping a real
    customer's activation.
    """
    if not customer_id:
        return None
    try:
        r = requests.get(
            f"{_PADDLE_BASE}/customers/{customer_id}",
            headers={"Authorization": f"Bearer {os.environ['PADDLE_API_KEY']}"},
            timeout=10)
        if r.status_code != 200:
            return None
        email = ((r.json().get('data') or {}).get('email') or '').strip()
        if not email:
            return None
    except Exception as e:
        print(f"⚠️ customer lookup failed for {customer_id}: {e}")
        return None
    # NEVER close this connection. get_db() caches one per REQUEST on flask.g
    # and hands the same object to every caller, so closing it here killed the
    # connection that update_user_subscription_status then tried to use — the
    # whole activation 500'd and Paddle retried forever. _user_id_by_subscription
    # right below has always followed the same rule.
    cur = get_db().cursor()
    cur.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                (email,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


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


def _record_discount_use(user_id, data):
    """Mark the account's intro offer redeemed if this event carries a discount.

    Both shapes are checked because both occur: a SUBSCRIPTION object carries
    `discount: {id, starts_at, ends_at}`, while a TRANSACTION carries
    `discount_id` at the top level. Never raises — an offer that stays
    un-burned is a bookkeeping wrinkle; an exception here is a failed
    activation and a Paddle retry loop.
    """
    try:
        discount = data.get('discount') or {}
        did = discount.get('id') if isinstance(discount, dict) else None
        did = did or data.get('discount_id')
        if not did:
            return
        offers.mark_used(get_db(), user_id)
    except Exception as e:
        print(f"⚠️ [offers] could not record discount use for {user_id}: {e}")


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
        # custom_data now arrives from Paddle.js (the browser creates the
        # transaction so it can render inline), so user_id is no longer
        # server-set and must not be trusted on its own. The BUYER'S EMAIL is
        # the authoritative identity: resolve the account from Paddle's
        # customer record and prefer it whenever the two disagree. Without
        # this, editing customData in devtools would activate a plan on
        # someone else's account.
        user_id = _user_id_by_customer_email(data.get('customer_id'))
        claimed = custom_data.get('user_id')
        if user_id and claimed and str(claimed) != str(user_id):
            print(f"⛔ custom_data claimed user {claimed} but the paying "
                  f"customer is user {user_id} — granting to the payer")
        if not user_id:
            user_id = claimed          # unknown email (first purchase) -> fall back
        if not user_id:
            return 'OK', 200
        # Plan/credits come from the PAID price, never from custom_data.plan.
        plan = _plan_from_data(data)
        if not plan:
            print("⛔ Grant event with no known price id — granting 0 credits")
            plan = 'free'
        billing = custom_data.get('billing', 'monthly')
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(
                    expiry_date_str.replace("Z", "+00:00"))
            except Exception as e:
                print(f"⚠️ Date parse error: {e}")
        grant, daily, preserve, why = _trial_aware_grant(
            user_id, plan, subscription_id, event_type, data)
        update_user_subscription_status(
            user_id, True, expiry_date, subscription_id, plan, grant,
            daily_credits=daily, preserve_credits=preserve)
        print(f"✅ User {user_id} on plan {plan} ({billing}) activated "
              f"(from price). Credits: {grant} ({why})")
        # Trial bookkeeping + the founder alert. Only subscription.* events
        # carry the subscription's own status; transaction.completed's
        # data.status is the TRANSACTION's ('completed'), which would read as
        # "not trialing" and quietly lose the signal. It never raises — a
        # missing badge must not cost anyone their plan.
        if event_type.startswith('subscription.'):
            trial_state.sync_from_subscription(
                get_db(), user_id, plan, subscription_id, data)
        # Burn the intro offer the moment Paddle confirms a discount is on this
        # subscription. Paddle is the only authority for this: we hand a
        # discount id to a checkout, but plenty of checkouts are abandoned and
        # some are completed without it. Marking on "we offered" instead of "it
        # was taken" would quietly deny people a discount they never received.
        _record_discount_use(user_id, data)

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
        # Recorded before the downgrade, because the downgrade is what makes
        # this event indistinguishable from any other cancellation afterwards.
        # A no-op unless this user was actually mid-trial.
        trial_state.record_cancel(get_db(), user_id, subscription_id)
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        _clawback_monthly_credits(user_id)
        print(f"⚠️ User {user_id} canceled — reverted to free + credits clawed back")

    else:
        # subscription.past_due / transaction.payment_failed: dunning grace,
        # Paddle retries the charge; no downgrade until it truly cancels.
        print(f"ℹ️ Dunning event {event_type} for sub {subscription_id} — no change (grace)")

    return 'OK', 200