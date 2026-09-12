import hashlib
import hmac
import os
import time

import jwt
import requests
from flask import Blueprint, request
from models import get_db, update_user_subscription_status
from datetime import datetime

import billing
import credits as credits_mod
import offers
import paid_subscription_alert
import trial_state
from plan_catalog import PLAN_MONTHLY_CREDITS

paddle_webhook = Blueprint('paddle_webhook', __name__)


# Subscriber daily top-up, and the 0 that replaces it during a trial. Named
# here so the two callers below cannot disagree about what "no top-up" means.
SUB_DAILY_CREDITS = 20
TRIAL_DAILY_CREDITS = 0


def _trial_aware_grant(user_id, plan, subscription_id, event_type, data,
                       payment_grant=False):
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
    A zero-dollar opening transaction is never a payment grant; only a positive
    transaction's first paid/completed transition can release the full pool.
    """
    full = credits_for_price(_price_id_from_data(data), plan)
    status = (data.get('status') or '').lower()
    if not event_type.startswith('subscription.'):
        status = ''             # not the subscription's status — see above
    if payment_grant:
        # A specific transaction crossed into paid/completed for the first
        # time. This, not subscription status, is the one event that refreshes
        # the pool for a purchase or renewal.
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
    # Paddle can emit active subscription.created/updated before the charge
    # completes, and can repeat those events throughout a billing period.
    # Update entitlement facts and limits but never refill spent credits until
    # a concrete transaction crosses into a paid state.
    return full, SUB_DAILY_CREDITS, True, 'subscription facts (credits unchanged)'

# Compatibility name used throughout the webhook and existing tests. The
# values themselves live in plan_catalog beside the Paddle prices that buy them.
PLAN_CREDITS = PLAN_MONTHLY_CREDITS

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
PRICE_CREDITS = {}
for _name, _cfg in (_PADDLE_PLANS or {}).items():
    _credits = (_cfg or {}).get('monthly_credits', 0)
    for _k in ('price_id', 'yearly_price_id'):
        _pid = (_cfg or {}).get(_k)
        if _pid:
            PRICE_TO_PLAN[_pid] = _name
            PRICE_CREDITS[_pid] = _credits
    for _legacy_id, _legacy_credits in ((_cfg or {}).get('legacy_prices') or {}).items():
        if _legacy_id:
            PRICE_TO_PLAN[_legacy_id] = _name
            PRICE_CREDITS[_legacy_id] = _legacy_credits


def _price_id_from_data(data):
    for it in (data.get('items') or []):
        price = it.get('price') or {}
        pid = price.get('id') or it.get('price_id')
        if pid:
            return pid
    return None


def credits_for_price(price_id, plan):
    """Credits this paid price actually bought.

    Shopfront prices use PLAN_CREDITS[plan]. A grandfathered trial or
    subscription on a previous price keeps the grant that price sold —
    otherwise converting a $30 trial would land 1,000 credits."""
    if price_id and price_id in PRICE_CREDITS:
        return PRICE_CREDITS[price_id]
    return PLAN_CREDITS.get(plan, 0)


def _plan_from_data(data):
    """Authoritative plan from the PAID price id in the event's line items.
    Returns the plan name or None if no known price is present."""
    pid = _price_id_from_data(data)
    if pid and pid in PRICE_TO_PLAN:
        return PRICE_TO_PLAN[pid]
    return None


def _has_recurring_items(data):
    """Whether this transaction describes a recurring price.

    Paddle's interim ``paid`` event may not have ``subscription_id`` yet. Its
    item billing cycle still tells us that a completed event without that id
    is malformed rather than a legitimate one-time charge.
    """
    for item in (data.get('items') or []):
        price = item.get('price') or {}
        if price.get('billing_cycle'):
            return True
    return False


_PADDLE_BASE = ("https://sandbox-api.paddle.com"
                if os.environ.get('PADDLE_MODE') == 'sandbox'
                else "https://api.paddle.com")


class _PayerIdentityUnavailable(RuntimeError):
    """Paddle could not prove which local account owns this payment."""


def _user_id_by_customer_email(customer_id):
    """The account that owns the email Paddle billed, or None.

    One Paddle API call on a rare event (an activation), which is cheap next
    to letting a forged custom_data.user_id decide who gets a paid plan.
    A provider/network failure is distinct from a verified customer whose
    email has no local account. The caller retries the former and refuses to
    trust browser-controlled ``custom_data.user_id`` for either case.
    """
    if not customer_id:
        return None
    try:
        r = requests.get(
            f"{_PADDLE_BASE}/customers/{customer_id}",
            headers={"Authorization": f"Bearer {os.environ['PADDLE_API_KEY']}"},
            # Paddle expects a webhook response inside five seconds. Leave
            # room for the local transaction and return 503 for provider
            # retry instead of occupying the entire delivery deadline here.
            timeout=3)
        if r.status_code != 200:
            raise _PayerIdentityUnavailable(
                f"customer lookup returned HTTP {r.status_code}")
        email = ((r.json().get('data') or {}).get('email') or '').strip()
        if not email:
            raise _PayerIdentityUnavailable(
                "customer lookup returned no email")
    except Exception as e:
        print(f"⚠️ customer lookup failed for {customer_id}: {e}")
        if isinstance(e, _PayerIdentityUnavailable):
            raise
        raise _PayerIdentityUnavailable(
            "customer lookup was unavailable") from e
    # NEVER close this connection here. get_db() caches one per REQUEST on
    # flask.g and hands the same object to every caller, so closing it here
    # killed the connection that update_user_subscription_status then tried to
    # use — the whole activation 500'd and Paddle retried forever. The app
    # teardown closes it after the complete request instead.
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


def _verified_payer_user_id(data, subscription_id):
    """Resolve a billing event without trusting browser custom data."""
    stored = _user_id_by_subscription(subscription_id)
    if stored:
        return stored
    return _user_id_by_customer_email(data.get('customer_id'))


def _retire_upgrade_source(user_id, new_subscription_id, paid_plan, data):
    """Schedule the old contract to end after a signed upgrade checkout.

    The browser can carry this token but cannot forge it. We act only on the
    final paid transaction and only when its verified payer and paid price
    match the server-issued upgrade intent. Abandoned checkouts therefore
    never disturb the current subscription.
    """
    token = (data.get('custom_data') or {}).get('upgrade_token')
    if not token:
        return True
    try:
        intent = jwt.decode(
            token, os.environ['SECRET_KEY'], algorithms=['HS256'],
            options={'require': ['exp', 'iat', 'sub']})
    except Exception as error:
        print(f"⛔ Invalid subscription-upgrade token: {error}", flush=True)
        return True
    if (intent.get('purpose') != 'subscription_upgrade'
            or str(intent.get('sub')) != str(user_id)
            or intent.get('to_plan') != paid_plan):
        print("⛔ Upgrade token did not match the verified payment", flush=True)
        return True

    old_subscription_id = intent.get('from_subscription_id')
    if (not old_subscription_id
            or old_subscription_id == new_subscription_id):
        return True
    endpoint = f"{_PADDLE_BASE}/subscriptions/{old_subscription_id}"
    headers = {
        "Authorization": f"Bearer {os.environ['PADDLE_API_KEY']}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            f"{endpoint}/cancel", headers=headers,
            json={"effective_from": "next_billing_period"}, timeout=2)
        if response.status_code in (200, 204, 404):
            print(f"✅ Upgrade retired old subscription "
                  f"{old_subscription_id}", flush=True)
            return True

        # Paddle can answer with a conflict when cancellation was already
        # scheduled by this webhook's earlier delivery. Confirm that state so
        # an idempotent retry does not become a permanent retry loop.
        current = requests.get(endpoint, headers=headers, timeout=2)
        old = (current.json().get('data') or {}) if current.status_code == 200 else {}
        scheduled = old.get('scheduled_change') or {}
        if (old.get('status') == 'canceled'
                or scheduled.get('action') == 'cancel'):
            return True
        print(f"⚠️ Could not retire upgraded subscription "
              f"{old_subscription_id}: HTTP {response.status_code}",
              flush=True)
        return False
    except requests.RequestException as error:
        print(f"⚠️ Could not retire upgraded subscription "
              f"{old_subscription_id}: {error}", flush=True)
        return False

# Paddle signs every webhook (Paddle-Signature: "ts=...;h1=...", where h1 is
# HMAC-SHA256 of "ts:raw_body" with the endpoint's secret key from
# Paddle > Developer tools > Notifications). Without verification anyone who
# reads the URL can grant themselves any plan. A missing secret is an operator
# outage, never permission to trust an unsigned request: fail closed before
# parsing or touching account state and let Paddle retry after configuration is
# repaired.
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
    parts = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parts.append((key.strip(), value.strip()))
    ts = next((value for key, value in parts if key == "ts"), None)
    # Paddle documents at least one h1 and may send several while rotating a
    # destination secret.  Accept any matching signature instead of silently
    # discarding all but the last one through a dict conversion.
    signatures = [value for key, value in parts if key == "h1" and value]
    if not ts or not signatures:
        return False
    try:
        if abs(time.time() - int(ts)) > 300:   # stale/replayed event
            return False
    except ValueError:
        return False
    signed = f"{ts}:".encode() + req.get_data()
    expected = hmac.new(PADDLE_WEBHOOK_SECRET.encode(), signed,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature)
               for signature in signatures)


@paddle_webhook.route('/webhook/paddle', methods=['POST'])
def handle_webhook():
    if not PADDLE_WEBHOOK_SECRET:
        print("⛔ Paddle webhook unavailable: PADDLE_WEBHOOK_SECRET is not "
              "configured")
        return 'Webhook signing is not configured', 503
    if not _verify_paddle_signature(request):
        print("⛔ Paddle webhook rejected: bad or missing signature")
        return 'Invalid signature', 403
    payload = request.get_json(force=True)
    print("🔔 Webhook received:", payload.get('event_type'))

    event_type = payload.get('event_type')
    data = payload.get('data', {})

    # Paddle BILLING event names (not the Classic *_refunded/_failed alerts,
    # which never fire on this integration). Grants activate the plan; refunds
    # arrive as adjustment.* with action='refund'; cancellations arrive as
    # subscription.canceled at period end.
    #
    # ROUND 59 — A FAILED CHARGE IS NO LONGER JUST A PRINTED LINE. These used
    # to fall through to an `else` that logged "no change (grace)" and did
    # nothing at all, so a trial whose card was refused KEPT the full plan
    # Paddle's preceding `active` event had just granted it. They now go to
    # billing.record_failure, which lifts the pool for an account that has
    # never paid a cent and grants real, bounded grace to one that has.
    GRANT_EVENTS = ('transaction.completed', 'transaction.paid',
                    'subscription.created', 'subscription.updated',
                    'subscription.activated')
    REFUND_EVENTS = ('adjustment.created', 'adjustment.updated')
    DUNNING_EVENTS = ('subscription.past_due', 'subscription.paused',
                      'transaction.payment_failed', 'transaction.past_due')
    if event_type not in GRANT_EVENTS + REFUND_EVENTS + DUNNING_EVENTS + (
            'subscription.canceled',):
        return 'OK', 200

    custom_data = data.get('custom_data') or {}
    # A subscription object's id identifies the subscription; a transaction or
    # adjustment must carry subscription_id explicitly. Falling back to a
    # transaction id made a standalone charge look like a recurring contract.
    subscription_id = (data.get('id')
                       if event_type.startswith('subscription.')
                       else data.get('subscription_id'))

    # `transaction.paid` is an intentionally brief state while Paddle is
    # still processing a successful payment. For automatically collected
    # recurring transactions, Paddle documents that subscription_id may not
    # exist until the following `transaction.completed` event. A 503 here
    # only creates a retry storm using the same incomplete payload. Do not
    # consume the paid transition; completed (or the hourly reconciler) will
    # durably record it and grant the entitlement once it can be linked.
    if event_type == 'transaction.paid' and not subscription_id:
        print("ℹ️ Deferring unlinked transaction.paid until completed")
        return 'OK', 200

    # ── Identity, resolved ONCE for every branch ────────────────────────────
    # custom_data arrives from Paddle.js (the browser creates the transaction
    # so it can render inline), so user_id is no longer server-set and must
    # never be trusted as identity. An already-stored subscription is the
    # cheapest authoritative path for renewals/refunds. Otherwise the BUYER'S
    # EMAIL from Paddle's API must resolve the account. A provider outage gets
    # a 503 so Paddle retries; it does not turn attacker-controlled metadata
    # into authority.
    #
    # It is resolved here rather than inside each branch because it costs a
    # Paddle API round trip, and round 59 added branches that all need it.
    try:
        user_id = _verified_payer_user_id(data, subscription_id)
    except _PayerIdentityUnavailable:
        return 'Payer identity temporarily unavailable', 503
    claimed = custom_data.get('user_id')
    if user_id and claimed and str(claimed) != str(user_id):
        print(f"⛔ custom_data claimed user {claimed} but the paying "
              f"customer is user {user_id} — using the payer")
    if not user_id:
        print("⛔ Paddle event did not resolve to a verified local account; "
              "browser custom_data was ignored")
        return 'Payer identity could not be verified', 503

    # A completed one-time charge is revenue but not a subscription grant.
    # Record it without trying to invent recurring entitlement. Conversely, a
    # recurring price that somehow reaches completed without a subscription id
    # is malformed and must remain retryable.
    if event_type == 'transaction.completed' and not subscription_id:
        if _has_recurring_items(data):
            print("⛔ Completed recurring transaction has no subscription_id")
            return 'Subscription identity is missing', 503
        receipt = billing.record_transaction(
            get_db(), user_id, data, report_transition=True)
        if not receipt or not receipt.get("recorded"):
            return 'Payment ledger temporarily unavailable', 503
        return 'OK', 200

    # A subscriber grant must be attributable to both a recurring contract and
    # a server-known Paddle price. Retrying is safer than acknowledging a paid
    # event whose entitlement this release cannot represent correctly.
    grant_plan = None
    if event_type in GRANT_EVENTS:
        if (event_type.startswith('transaction.') and
                not subscription_id):
            print("⛔ Paid grant event has no subscription_id")
            return 'Subscription identity is missing', 503
        grant_plan = _plan_from_data(data)
        if not grant_plan:
            print("⛔ Grant event has no server-known Paddle price id")
            return 'Billing price is not recognized', 503

    # Every recognized transaction Paddle mentions goes in the ledger,
    # successful or not, BEFORE any branch decides what it means.
    # `payments.amount_cents` is the
    # only thing on this system that is revenue: a trial opens with a genuine,
    # signed, `completed` transaction whose grand_total is $0.00, so counting
    # completed transactions has never once been counting money.
    payment_record = None
    if event_type.startswith('transaction.'):
        payment_record = billing.record_transaction(
            get_db(), user_id, data, report_transition=True, commit=False)
        if not payment_record or not payment_record.get("recorded"):
            # Do not acknowledge a payment event whose durable row was not
            # written. Paddle will retry, and no entitlement mutation has run.
            return 'Payment ledger temporarily unavailable', 503

    # Paddle emits a completed zero-dollar transaction when opening a trial.
    # It proves neither payment nor the subscription's current lifecycle state.
    # Persist it as accounting evidence, but let the signed subscription event
    # establish the trial allowance and daily-credit policy. Otherwise event
    # reordering could briefly install paid daily credits before the trial row
    # exists, and a missing subscription delivery could leave that state stuck.
    if (event_type in ('transaction.completed', 'transaction.paid')
            and int(payment_record.get('amount_cents') or 0) <= 0):
        get_db().commit()
        print(f"ℹ️ {event_type} carried no payment; ledgered without "
              "entitlement mutation")
        return 'OK', 200

    if not user_id:
        return 'OK', 200

    # The subscription's OWN status, which only subscription.* events carry —
    # transaction.completed's data.status is the TRANSACTION's ('completed').
    sub_status = ((data.get('status') or '').lower()
                  if event_type.startswith('subscription.') else '')

    # ── THE GUARD THAT WAS MISSING ─────────────────────────────────────────
    # Paddle keeps sending subscription.updated while a subscription sits in
    # dunning, and every one of those events carries the paid price id. They
    # are in GRANT_EVENTS, so without this they walk straight into the grant
    # below and hand the full monthly pool to an account whose card was just
    # refused — re-granting it after every lift, forever.
    if sub_status in billing.FAILING_STATUSES:
        _handle_failure(user_id, subscription_id, data, event_type, sub_status)
        return 'OK', 200

    if event_type in DUNNING_EVENTS:
        _handle_failure(user_id, subscription_id, data, event_type)
        return 'OK', 200

    if event_type in GRANT_EVENTS:
        # Plan/credits come from the PAID price, never from custom_data.plan.
        plan = grant_plan
        # NB: a local named `billing` lived here (the monthly/yearly label) and
        # would now shadow the billing module imported at the top of the file.
        period = custom_data.get('billing', 'monthly')
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(
                    expiry_date_str.replace("Z", "+00:00"))
            except Exception as e:
                print(f"⚠️ Date parse error: {e}")
        grant, daily, preserve, why = _trial_aware_grant(
            user_id, plan, subscription_id, event_type, data,
            payment_grant=bool(
                payment_record and payment_record.get("newly_paid")))
        update_user_subscription_status(
            user_id, True, expiry_date, subscription_id, plan, grant,
            daily_credits=daily, preserve_credits=preserve)
        print(f"✅ User {user_id} on plan {plan} ({period}) activated "
              f"(from price). Credits: {grant} ({why})")

        # ── MONEY ────────────────────────────────────────────────────────
        # A transaction that carries a real amount is the ONLY thing that
        # proves the card worked. It is what promotes a trial to 'converted',
        # and what clears a decline when a Paddle retry finally lands.
        if event_type in ('transaction.completed', 'transaction.paid'):
            cents, _cur = billing.transaction_amount(data)
            if cents > 0:
                billing.record_recovery(get_db(), user_id, plan)
                billing.set_status(
                    get_db(), user_id, 'active', plan, period)
                trial_state.record_paid_conversion(
                    get_db(), user_id, data.get('subscription_id'))
                print(f"💰 User {user_id} paid {cents / 100:.2f} on {plan}")
                if (event_type == 'transaction.completed'
                        and not _retire_upgrade_source(
                            user_id, subscription_id, plan, data)):
                    # The paid plan is already safe and idempotent. Ask Paddle
                    # to retry only so the old contract cannot be left renewing
                    # after a transient cancellation API failure.
                    return 'Old subscription cancellation unavailable', 503
                # The no-trial shopfront removed the old "trial started"
                # founder email. Queue its honest replacement only after real
                # money lands. The queue is unique by subscription AND
                # transaction, so webhook retries and future renewals cannot
                # spam the founder. The outbox itself accepts only Paddle's
                # final transaction.completed event, not the preceding paid
                # lifecycle event. It fails soft
                # and sends off-request; billing never waits on Brevo.
                if event_type == 'transaction.completed':
                    paid_subscription_alert.enqueue_and_kick(
                        get_db(), user_id, plan, data, event_type)
        elif sub_status:
            billing.set_status(get_db(), user_id, sub_status, plan)

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
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        _clawback_monthly_credits(user_id)
        billing.clear_billing(get_db(), user_id)
        print(f"⚠️ User {user_id} refunded — reverted to free + credits clawed back")

    elif event_type == 'subscription.canceled':
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
        billing.clear_billing(get_db(), user_id)
        print(f"⚠️ User {user_id} canceled — reverted to free + credits clawed back")

    return 'OK', 200


def _handle_failure(user_id, subscription_id, data, event_type,
                    sub_status=None):
    """A charge was refused. Record it, and take back what was not paid for.

    THIS IS THE BRANCH THAT USED TO PRINT "no change (grace)" AND RETURN.
    A trial ending in a refused card left an account marked converted, holding
    the full monthly pool, on a plan nobody had paid for — permanently, because
    nothing downstream ever revisited it.

    What "grace" means now depends on a fact we can finally check: whether this
    account has ever collected a payment. See billing.record_failure. Both
    outcomes mark the account past_due, which is what lights the studio banner,
    the admin badge and the daily nudge.
    """
    # A transaction event with no subscription is a one-off, not a renewal.
    # `subscription_id` falls back to data['id'] at the top of the handler, so
    # without this a failed standalone charge would be looked up in `payments`
    # under its own transaction id, find nothing, and lift a subscriber's pool
    # over a payment that had nothing to do with their subscription.
    if (event_type.startswith('transaction.')
            and not data.get('subscription_id')):
        # record_transaction joined this request transaction with commit=False
        # so a later entitlement update could commit atomically. There is no
        # entitlement update for a standalone failure, but its payment ledger
        # row is still durable evidence and must be committed explicitly.
        get_db().commit()
        print(f"ℹ️ {event_type} with no subscription for user {user_id} "
              f"— recorded, no entitlement change")
        return

    reason = billing.payment_error_code(data)
    plan = _plan_from_data(data)
    # A subscription.paused at the end of Paddle's retry window is a harder
    # state than past_due — it means recovery has stopped being attempted.
    status = sub_status or ('paused' if event_type == 'subscription.paused'
                            else 'past_due')
    outcome = billing.record_failure(get_db(), user_id, subscription_id, plan,
                                     reason, status=status)
    # A trial whose conversion charge was refused is NOT a conversion. This is
    # the exact row the admin was reading as 'converted' next to a Paddle
    # dashboard that said the payment failed.
    trial_state.record_payment_failure(get_db(), user_id, subscription_id)
    print(f"💳 {event_type} for user {user_id} / sub {subscription_id}: "
          f"{reason or 'declined'} — {outcome.get('action')}")
