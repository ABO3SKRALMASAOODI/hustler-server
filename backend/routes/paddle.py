from flask import Blueprint, request, jsonify
import requests
import os
import jwt
import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

import offers

paddle_bp = Blueprint('paddle', __name__)


def get_offers_db():
    """A short-lived connection for offer bookkeeping.

    Deliberately NOT models.get_db(): that one is cached on flask.g for the
    whole request and shared with the caller, and offers.py rolls back on its
    own errors — which on a shared connection would discard somebody else's
    work. A private connection makes the offer path unable to affect anything
    around it.
    """
    return psycopg2.connect(os.environ['DATABASE_URL'],
                            cursor_factory=RealDictCursor)

# ── Plan definitions ──────────────────────────────────────────────────────────

PLANS_LIVE = {
    # ── The two live plans (round 45) ───────────────────────────────────
    # Both carry a 3-DAY FREE TRIAL, configured on the Paddle PRICE itself
    # (trial_period: 3 days, requires_payment_method) — not in this code.
    # Paddle therefore creates the subscription immediately, charges nothing
    # until day 3, and fires subscription.created right away; the webhook
    # grants credits on that event, so a trialling user is a paying user as
    # far as the app is concerned and simply stops being one if they cancel.
    #
    # The pricing page sells ONE product at TWO volumes:
    #   'ai'     Creator $30/mo, $300/yr — 1,500 credits
    #   'ai_pro' Pro     $50/mo, $500/yr — 3,000 credits
    #
    # REBASED FOR MARGIN (round 48). 1 credit is ~$0.01 of real model spend, so
    # the old 2,400/4,000 grants were $24 and $40 of cost against $30 and $50 of
    # revenue — a 20% margin at full price, and NEGATIVE for anyone on the
    # annual price or an intro discount. At 1,500/3,000 the cost is $15 and $30:
    # a 50% margin on Creator and 40% on Pro, which is the deliberate volume
    # break that makes Pro worth moving to (60 credits per dollar vs 50).
    #
    # NOTE the annual prices are unchanged and are the thin ones: $300/yr is
    # $25/mo for $15 of cost (40%), $500/yr is $41.67/mo for $30 (28%). Raise
    # the annual price, not the credits, if that needs fixing.
    #
    # Change the number here and PLAN_CREDITS in paddle_webhook.py and
    # PLAN_MONTHLY_LIMITS in credits.py together — three places, one truth.
    'ai':     {'price_id': 'pri_01kyde25cwqf7t2bk1ekky2pyp', 'yearly_price_id': 'pri_01kyde25n7rxrhajg5xvxxka7y', 'monthly_credits': 1500},
    'ai_pro': {'price_id': 'pri_01kye15m5262nbs7hjmazrej7j', 'yearly_price_id': 'pri_01kye15mdacm7wzqp740g3rvy4', 'monthly_credits': 3000},
    # ── MCP: off the pricing page, kept so the one live subscription resolves.
    # monthly_credits 0 is deliberate — that customer supplies their own model
    # through their own MCP client, so the pool (which meters OUR model spend)
    # must not be topped up.
    'mcp':   {'price_id': 'pri_01kyde24w5s63hgzh7wzn4zwnt', 'yearly_price_id': 'pri_01kyde254pd3z24zqd8mzav861', 'monthly_credits': 0},
    # ── Retired tiers, kept so grandfathered subscribers keep working ────
    'plus':  {'price_id': 'pri_01jxj6smtjkfsf22hdr4swyr9j', 'yearly_price_id': 'pri_01kkekq1hcvzvyhh3ffk3nk291', 'monthly_credits': 800},
    'pro':   {'price_id': 'pri_01kk4k4y8c3ygxd620vcxg6ph1', 'yearly_price_id': 'pri_01kkeksjv9pf2nc1gphj67m8ae', 'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4k83cwpmf1jsctgdvhm0n6', 'yearly_price_id': 'pri_01kkektygjg89gywskyj1dycx2', 'monthly_credits': 5000},
    'titan': {'price_id': 'pri_01kkekbegh2q5x3kxn28afbw5d', 'yearly_price_id': 'pri_01kkekf5ksjq5dqbfpxakf1g23', 'monthly_credits': 10000},
    'ace':   {'price_id': 'pri_01kkekgt4zv65t59yw7ybz8w01', 'yearly_price_id': 'pri_01kkekj0am5yfqxx933c6d4tck', 'monthly_credits': 30000},
}

PLANS_SANDBOX = {
    'plus':  {'price_id': 'pri_01jw8722trngfyz12kq158vrz7', 'yearly_price_id': 'SANDBOX_PLUS_YEARLY_TODO',  'monthly_credits': 800},
    'pro':   {'price_id': 'pri_01kk4wvnbxb7nbh426bnk62xa2', 'yearly_price_id': 'SANDBOX_PRO_YEARLY_TODO',   'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4wwr07ce0xp8x4kvdgt8kg', 'yearly_price_id': 'SANDBOX_ULTRA_YEARLY_TODO', 'monthly_credits': 5000},
    'titan': {'price_id': 'SANDBOX_TITAN_MONTHLY_TODO',      'yearly_price_id': 'SANDBOX_TITAN_YEARLY_TODO', 'monthly_credits': 10000},
    'ace':   {'price_id': 'SANDBOX_ACE_MONTHLY_TODO',        'yearly_price_id': 'SANDBOX_ACE_YEARLY_TODO',   'monthly_credits': 30000},
}

PLANS = PLANS_SANDBOX if os.environ.get('PADDLE_MODE') == 'sandbox' else PLANS_LIVE

# Only these tiers can be NEWLY purchased or switched to. plus/pro/ultra/titan/
# ace are retired from the product but stay in PLANS (and PLAN_CREDITS in the
# webhook) so grandfathered subscribers keep working — they must NOT be
# reachable via a hand-crafted checkout/change-plan call that mints their live
# price IDs.
# 'mcp' is deliberately NOT here: the MCP server does not exist yet, so a buyer
# would pay, correctly receive 0 credits (they bring their own model) and have
# nothing to connect to. The Paddle product, prices and 3-day trial are already
# live and the plan stays in PLANS, so the one existing MCP subscription keeps
# renewing and resolving — this only blocks NEW checkouts, including
# hand-crafted ones that bypass the pricing page. Add 'mcp' back the day the
# server ships.
PURCHASABLE_PLANS = {'ai', 'ai_pro'}


def get_paddle_base():
    is_sandbox = os.environ.get('PADDLE_MODE') == 'sandbox'
    return "https://sandbox-api.paddle.com" if is_sandbox else "https://api.paddle.com"


def paddle_headers():
    return {
        "Authorization": f"Bearer {os.environ['PADDLE_API_KEY']}",
        "Content-Type": "application/json"
    }


def decode_token(auth_header):
    if not auth_header:
        return None, None
    token = auth_header.split(" ")[1]
    payload = jwt.decode(token, os.environ['SECRET_KEY'], algorithms=["HS256"])
    return payload.get('sub'), payload.get('email')


@paddle_bp.route('/paddle/checkout-config', methods=['POST'])
def checkout_config():
    """What the browser needs to open an INLINE Paddle checkout itself.

    Why the browser creates the transaction and not us: Paddle.js can only
    render inline for a transaction that is already `ready`, and a transaction
    we create server-side is `draft` — it has no customer and no address, and
    we cannot invent either. Faced with a draft, Paddle.js does not error; it
    silently redirects to its hosted page to collect those details, which is
    exactly the "nice page, then the old checkout two seconds later" bounce.
    Opening with `items` lets Paddle create the transaction AND collect the
    address inside our own frame.

    The price id and the email are returned from HERE rather than hardcoded in
    the bundle so the client can never pick a price we did not authorise, and
    the email is the JWT's, not whatever the page felt like sending.
    """
    try:
        user_id, user_email = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    data = request.json or {}
    plan = data.get('plan', 'ai')
    billing = 'yearly' if data.get('billing') == 'yearly' else 'monthly'
    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    if plan not in PURCHASABLE_PLANS:
        return jsonify({"error": "That plan is no longer available."}), 400

    price_id = (PLANS[plan]['yearly_price_id'] if billing == 'yearly'
                else PLANS[plan]['price_id'])
    out = {"price_id": price_id, "email": user_email,
           "user_id": str(user_id), "plan": plan, "billing": billing}

    # The intro discount is decided HERE, by the server, from the user's own
    # offer row — never from a flag the browser sends. The client cannot ask
    # for a discount it has not been granted, and cannot keep one past its
    # expiry, because the only thing it ever receives is an id we chose to put
    # in this response.
    #
    # Monthly only: the discount covers the first billing period, which on an
    # annual price would be a whole year of credits sold below cost. Paddle
    # also enforces this via the discount's restrict_to (offers.py), so the two
    # cannot drift apart.
    if billing == 'monthly':
        conn = get_offers_db()
        try:
            offer = offers.live_offer(conn, user_id)
            did = offers.discount_id() if offer else None
            if offer and did:
                out["discount_id"] = did
                out["percent_off"] = offer["percent_off"]
                out["offer_seconds_remaining"] = offers.seconds_left(offer)
                offers.mark_served(conn, user_id, offer["kind"])
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return jsonify(out)


# ── Create checkout session ───────────────────────────────────────────────────

@paddle_bp.route('/paddle/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        user_id, user_email = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    data = request.json or {}
    plan = data.get('plan', 'plus')
    billing = data.get('billing', 'monthly')  # 'monthly' or 'yearly'

    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    if plan not in PURCHASABLE_PLANS:
        return jsonify({"error": "That plan is no longer available."}), 400

    # Pick the right price ID based on billing interval
    if billing == 'yearly':
        price_id = PLANS[plan]['yearly_price_id']
    else:
        price_id = PLANS[plan]['price_id']

    body = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "customer": {"email": user_email} if user_email else {},
        "custom_data": {"user_id": user_id, "plan": plan, "billing": billing},
        "collection_mode": "automatic",
        "checkout": {"success_url": "https://valmera.io/purchase-success"}
    }

    # The intro discount, on the HOSTED fallback path. Eligibility is the
    # server's call from the user's own offer row — `use_promo` from the client
    # is not consulted at all, because a discount a browser can ask for is a
    # discount anyone can take. Monthly only, for the reason in offers.py.
    if billing == 'monthly':
        conn = None
        try:
            conn = get_offers_db()
            offer = offers.live_offer(conn, user_id)
            did = offers.discount_id() if offer else None
            if offer and did:
                body["discount_id"] = did
                offers.mark_served(conn, user_id, offer["kind"])
                print(f"🎉 {offer['percent_off']}% offer applied for user "
                      f"{user_id} ({offer['kind']})")
        except Exception as e:
            print(f"⚠️ Offer check failed: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    print(f'🎯 Checkout: plan={plan}, billing={billing}')

    response = requests.post(
        f"{get_paddle_base()}/transactions",
        headers=paddle_headers(),
        json=body
    )
    print("🔁 Paddle API Response:", response.text)

    if response.status_code != 201:
        return jsonify({"error": "Failed to create checkout session", "details": response.text}), 500

    resp_data = response.json()
    checkout_url = resp_data["data"]["checkout"]["url"]
    # The transaction id is what lets the frontend open Paddle.js INLINE, on
    # our own dark page, instead of sending the user to Paddle's hosted white
    # two-column page. checkout_url is still returned so any older client (and
    # the fallback path when Paddle.js fails to load) keeps working.
    return jsonify({"checkout_url": checkout_url,
                    "txn_id": resp_data["data"]["id"],
                    "plan": plan, "billing": billing})


# ── Check promo eligibility ──────────────────────────────────────────────────

@paddle_bp.route('/billing/offer', methods=['GET'])
def billing_offer():
    """This account's live discount, if it has one.

    Also MINTS the welcome offer as a side effect. A new account should get its
    24 hours from the moment it exists, and there are three doors into the
    product (email verification, Google OAuth, and simply landing on the
    pricing page); minting here as well means none of them can leave someone
    without the offer everyone else got. The UNIQUE (user_id, kind) constraint
    makes the repeat mints free.

    Always 200 with a body — the pricing page renders at list price when
    `active` is false, and an offer lookup must never be the reason someone
    cannot see the plans.
    """
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"active": False}), 200
    if not user_id:
        return jsonify({"active": False}), 200

    conn = None
    try:
        conn = get_offers_db()
        # Only for accounts that have never subscribed: a paying customer does
        # not need an acquisition discount, and a trialling one already used
        # their moment.
        with conn.cursor() as cur:
            cur.execute("SELECT is_subscribed FROM users WHERE id = %s",
                        (int(user_id),))
            row = cur.fetchone() or {}
        if not row.get("is_subscribed"):
            offers.mint(conn, user_id, offers.WELCOME)
        return jsonify(offers.public(conn, user_id)), 200
    except Exception as e:
        print(f"⚠️ offer lookup failed: {e}")
        return jsonify({"active": False}), 200
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# Kept under its old name so an older cached frontend bundle keeps working; the
# body is the new shape plus the one field that page read.
@paddle_bp.route('/paddle/promo-status', methods=['GET'])
def promo_status():
    resp = billing_offer()
    body = resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
    body = dict(body or {})
    body["eligible"] = bool(body.get("active"))
    return jsonify(body), 200


# ── Upgrade / downgrade ───────────────────────────────────────────────────────

@paddle_bp.route('/paddle/change-plan', methods=['POST'])
def change_plan():
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    data = request.json or {}
    new_plan = data.get('plan')
    billing = data.get('billing', 'monthly')

    if new_plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    if new_plan not in PURCHASABLE_PLANS:
        return jsonify({"error": "That plan is no longer available."}), 400

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription"}), 400

    # Get current subscription to find item id
    sub_res = requests.get(
        f"{get_paddle_base()}/subscriptions/{subscription_id}",
        headers=paddle_headers()
    )
    if sub_res.status_code != 200:
        return jsonify({"error": "Could not fetch subscription"}), 500

    # Pick the right price ID
    if billing == 'yearly':
        new_price_id = PLANS[new_plan]['yearly_price_id']
    else:
        new_price_id = PLANS[new_plan]['price_id']

    body = {
        "items": [{"price_id": new_price_id, "quantity": 1}],
        # Keep the subscription's custom_data.plan in sync with the new price so
        # it isn't misleading (the webhook now grants off the price, but other
        # tooling reads this field).
        "custom_data": {"user_id": user_id, "plan": new_plan, "billing": billing},
        "proration_billing_mode": "do_not_bill"
    }
    res = requests.patch(
        f"{get_paddle_base()}/subscriptions/{subscription_id}",
        headers=paddle_headers(),
        json=body
    )
    if res.status_code not in (200, 202):
        return jsonify({"error": "Failed to change plan", "details": res.text}), 500

    return jsonify({"message": f"Plan will change to {new_plan} at next billing cycle."})


# ── Cancel subscription ───────────────────────────────────────────────────────

def _plan_from_price(price_id):
    for name, cfg in PLANS.items():
        if price_id in (cfg.get('price_id'), cfg.get('yearly_price_id')):
            return name
    return None


@paddle_bp.route('/paddle/subscription-state', methods=['GET'])
def subscription_state():
    """The REAL state of the subscription, read from Paddle.

    The users table cannot answer this. Cancelling uses
    effective_from=next_billing_period, so Paddle records a SCHEDULED change
    and the subscription stays active until the period ends — nothing in our
    database moves, and a UI driven off `plan`/`is_subscribed` alone shows the
    identical screen after you cancel. That is exactly the bug this endpoint
    exists to fix.

    Degrades to the DB view when Paddle is unreachable, so the page still
    renders something truthful rather than erroring.
    """
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT plan, is_subscribed, subscription_id "
                        "FROM users WHERE id = %s", (int(user_id),))
            row = cur.fetchone() or {}
    finally:
        conn.close()

    out = {
        "plan": row.get("plan") or "free",
        "is_subscribed": bool(row.get("is_subscribed")),
        "status": None, "scheduled_cancel_at": None,
        "ends_at": None, "trialing": False, "source": "db",
    }
    sub_id = row.get("subscription_id")
    if not sub_id:
        return jsonify(out)

    try:
        r = requests.get(f"{get_paddle_base()}/subscriptions/{sub_id}",
                         headers=paddle_headers(), timeout=12)
        if r.status_code != 200:
            return jsonify(out)
        d = r.json().get("data") or {}
    except Exception as e:
        print(f"⚠️ subscription-state lookup failed: {e}")
        return jsonify(out)

    sched = d.get("scheduled_change") or {}
    items = d.get("items") or [{}]
    price_id = ((items[0].get("price") or {}).get("id"))
    out.update({
        "status": d.get("status"),
        "trialing": d.get("status") == "trialing",
        "scheduled_cancel_at": (sched.get("effective_at")
                                if sched.get("action") == "cancel" else None),
        "ends_at": (d.get("current_billing_period") or {}).get("ends_at"),
        "plan": _plan_from_price(price_id) or out["plan"],
        # Paddle is authoritative: `canceled` there means gone, whatever the
        # users row still says (the row only moves when the webhook lands).
        "is_subscribed": d.get("status") in ("active", "trialing", "past_due"),
        "source": "paddle",
    })
    return jsonify(out)


@paddle_bp.route('/paddle/resume-subscription', methods=['POST'])
def resume_subscription():
    """Undo a scheduled cancellation — the counterpart to cancel.

    Without this, "cancel" was a one-way door inside the billing period: the
    plan was still live and still being paid for, but the only way back was to
    let it lapse and buy again.
    """
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No subscription found"}), 400

    res = requests.patch(
        f"{get_paddle_base()}/subscriptions/{subscription_id}",
        headers=paddle_headers(),
        json={"scheduled_change": None})
    if res.status_code not in (200, 204):
        return jsonify({"error": "Could not resume the subscription",
                        "details": res.text[:300]}), 500
    return jsonify({"message": "Your subscription will continue as normal."})


@paddle_bp.route('/paddle/cancel-offer', methods=['GET'])
def cancel_offer():
    """What the "are you sure?" screen should say before it cancels anything.

    Two different screens, and which one a user gets is a fact about their
    account, not a guess:

      * they have never redeemed a discount -> offer 50% off their first
        charge to stay.
      * they already have one (they started this very trial on the welcome
        offer) -> NO second discount. Stacking two 50%s on one subscription is
        the double-discount this whole feature has to avoid, so they get the
        honest screen instead: what they keep, and until when.

    Returns 200 with a body in both cases. The frontend renders the confirm
    screen either way; the offer is the only part that varies.
    """
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    # subscription_state is a view; on its error paths it returns (body, code).
    try:
        raw = subscription_state()
        state = (raw[0] if isinstance(raw, tuple) else raw).get_json() or {}
    except Exception as e:
        print(f"⚠️ cancel-offer state lookup failed: {e}")
        state = {}
    out = {"offer": {"active": False},
           "trialing": bool(state.get("trialing")),
           "plan": state.get("plan"),
           "ends_at": state.get("scheduled_cancel_at") or state.get("ends_at"),
           "already_discounted": False}

    conn = None
    try:
        conn = get_offers_db()
        if offers.has_ever_used(conn, user_id):
            out["already_discounted"] = True
            return jsonify(out), 200
        # Only mint the save offer for someone who actually still has a
        # subscription to save.
        if state.get("is_subscribed"):
            offers.mint(conn, user_id, offers.SAVE)
            out["offer"] = offers.public(conn, user_id)
    except Exception as e:
        print(f"⚠️ cancel-offer lookup failed: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return jsonify(out), 200


@paddle_bp.route('/paddle/accept-offer', methods=['POST'])
def accept_offer():
    """Take the stay-offer instead of cancelling.

    The user is mid-subscription, so there is no checkout to attach a discount
    to — it goes onto the SUBSCRIPTION, effective immediately, which makes the
    charge at the end of their trial the discounted one. Also clears any
    scheduled cancellation, because someone who accepts an offer to stay
    plainly means to stay.
    """
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription"}), 400

    conn = None
    try:
        conn = get_offers_db()
        # Re-check server-side. The button is only rendered for eligible users,
        # but eligibility is not the button's to decide.
        if offers.has_ever_used(conn, user_id):
            return jsonify({"error": "You've already used a discount on this "
                                     "account."}), 400
        offer = offers.mint(conn, user_id, offers.SAVE) or \
            offers.live_offer(conn, user_id)
        if not offer:
            return jsonify({"error": "That offer isn't available right "
                                     "now."}), 400
        ok, err = offers.apply_to_subscription(subscription_id)
        if not ok:
            return jsonify({"error": err}), 502
        offers.mark_used(conn, user_id)
    except Exception as e:
        print(f"⚠️ accept-offer failed: {e}")
        return jsonify({"error": "We couldn't apply that just now — please "
                                 "try again."}), 500
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Undo a scheduled cancel if one is already pending. Best-effort: the
    # discount is applied either way, and reporting failure here would tell the
    # user nothing happened when in fact it did.
    try:
        requests.patch(f"{get_paddle_base()}/subscriptions/{subscription_id}",
                       headers=paddle_headers(),
                       json={"scheduled_change": None}, timeout=12)
    except Exception as e:
        print(f"⚠️ accept-offer resume failed: {e}")

    return jsonify({"message": f"Done — {offers.PERCENT_OFF}% off your next "
                               "payment, and your plan continues as normal."})


@paddle_bp.route('/paddle/cancel-subscription', methods=['POST'])
def cancel_subscription():
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription found"}), 400

    res = requests.post(
        f"{get_paddle_base()}/subscriptions/{subscription_id}/cancel",
        headers=paddle_headers(),
        json={"effective_from": "next_billing_period"}
    )
    if res.status_code not in (200, 204):
        return jsonify({"error": "Failed to cancel", "details": res.text}), 500

    return jsonify({"message": "Subscription will cancel at end of billing period."})