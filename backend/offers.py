"""Historical offer bookkeeping. New discounts and emails are retired.

Keep redemption stamps for already-paid Paddle webhook events. No live
checkout, signup, cancellation, or scheduler path may mint a new offer.
"""

import datetime
import os
import time

import requests

PERCENT_OFF = int(os.getenv("OFFER_PERCENT_OFF", "50"))
OFFER_HOURS = int(os.getenv("OFFER_HOURS", "24"))

# WELCOME is retired as a mint (round 49) and kept only so rows already in
# production read back — see the module docstring. Nothing should create one.
WELCOME = "welcome"
WINBACK = "winback"
SAVE = "save"
KINDS = (WELCOME, WINBACK, SAVE)

# Human labels, for the email and the admin.
KIND_LABELS = {WELCOME: "Welcome offer", WINBACK: "One-time offer",
               SAVE: "Stay offer"}

# The Paddle discount's code. Visible to anyone who reaches a checkout with it
# applied, which is fine: it is the same discount we broadcast by email, it
# only ever covers the first billing period, and Paddle's `restrict_to` keeps
# it off the annual prices.
DISCOUNT_CODE = os.getenv("PADDLE_DISCOUNT_CODE", "VALMERA50")

_schema = {"ok": False, "checked_at": 0.0}
_discount = {"id": None, "checked_at": 0.0}
_RECHECK_SECONDS = 60


# ── schema ──────────────────────────────────────────────────────────────────

def ensure_schema(conn):
    """Idempotent DDL. Returns False (and stays quiet) if it cannot run, so a
    permissions problem degrades to "nobody has an offer" rather than 500ing
    the pricing page."""
    if _schema["ok"]:
        return True
    if time.time() - _schema["checked_at"] < _RECHECK_SECONDS:
        return False
    _schema["checked_at"] = time.time()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_offers (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL,
                kind         TEXT NOT NULL,
                percent_off  INTEGER NOT NULL DEFAULT 50,
                created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at   TIMESTAMP NOT NULL,
                emailed_at   TIMESTAMP,
                served_at    TIMESTAMP,
                used_at      TIMESTAMP,
                UNIQUE (user_id, kind)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_offers_user "
                    "ON user_offers(user_id)")
        conn.commit()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] schema not ready: {e}", flush=True)
        return False
    _schema["ok"] = True
    return True


def _rollback(conn):
    """A failed statement poisons psycopg2's connection, and get_db() hands the
    SAME object to everything else in the request. Roll back so an offer
    hiccup cannot break an unrelated query."""
    try:
        conn.rollback()
    except Exception:
        pass


def _get(row, key, index):
    """Works with RealDictCursor rows and plain tuples alike."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (KeyError, TypeError, IndexError):
            return None


# ── minting and reading ─────────────────────────────────────────────────────

def has_ever_used(conn, user_id):
    """True once this account has actually redeemed a discount. The single
    guard against handing the same person 50% twice."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM user_offers "
                    "WHERE user_id = %s AND used_at IS NOT NULL LIMIT 1",
                    (int(user_id),))
        found = cur.fetchone() is not None
        cur.close()
        return found
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] used-check failed for {user_id}: {e}", flush=True)
        # Fail CLOSED: an unknown history must not mint a second discount.
        return True


def mint(conn, user_id, kind, hours=None):
    """Introductory and retention discounts were retired on 2026-09-08."""
    return None


def _row(conn, user_id, kind):
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, kind, percent_off, created_at, expires_at,
                              emailed_at, served_at, used_at
                       FROM user_offers WHERE user_id = %s AND kind = %s""",
                    (int(user_id), kind))
        row = cur.fetchone()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] read failed for {user_id}/{kind}: {e}", flush=True)
        return None
    return _as_dict(row)


def _as_dict(row):
    if row is None:
        return None
    return {
        "id": _get(row, "id", 0),
        "kind": _get(row, "kind", 1),
        "percent_off": int(_get(row, "percent_off", 2) or PERCENT_OFF),
        "created_at": _get(row, "created_at", 3),
        "expires_at": _get(row, "expires_at", 4),
        "emailed_at": _get(row, "emailed_at", 5),
        "served_at": _get(row, "served_at", 6),
        "used_at": _get(row, "used_at", 7),
    }


def live_offer(conn, user_id):
    """Unused historical offers cannot be redeemed after retirement."""
    return None


def seconds_left(offer):
    """Computed on the SERVER. The countdown must not depend on the visitor's
    clock — a device an hour fast would show an offer as expired that is not,
    and one an hour slow would keep showing it after checkout stopped honouring
    it."""
    if not offer or not offer.get("expires_at"):
        return 0
    exp = offer["expires_at"]
    if exp.tzinfo is not None:
        exp = exp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return max(0, int((exp - datetime.datetime.utcnow()).total_seconds()))


def public(conn, user_id):
    """What the frontend gets. Always a dict, never an error — the pricing page
    renders at list price when `active` is false."""
    offer = live_offer(conn, user_id)
    if not offer:
        return {"active": False}
    return {
        "active": True,
        "kind": offer["kind"],
        "percent_off": offer["percent_off"],
        "seconds_remaining": seconds_left(offer),
        "expires_at": offer["expires_at"].isoformat() + "Z",
        "label": KIND_LABELS.get(offer["kind"], "Offer"),
    }


def mark_served(conn, user_id, kind):
    """Records that a checkout was opened with this discount attached. Not the
    same as used — plenty of checkouts are abandoned — but it is what tells the
    save-offer path that this person already had their discount in hand."""
    _stamp(conn, user_id, "served_at", kind)


def mark_used(conn, user_id):
    """Paddle confirmed a discount on this account's subscription. Burns every
    outstanding offer, because the guarantee is one per ACCOUNT, not one per
    kind."""
    if not ensure_schema(conn):
        return
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_offers SET used_at = COALESCE(used_at, NOW()) "
                    "WHERE user_id = %s AND used_at IS NULL", (int(user_id),))
        conn.commit()
        cur.close()
        print(f"[offers] user {user_id} redeemed their discount", flush=True)
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] mark_used failed for {user_id}: {e}", flush=True)


def mark_emailed(conn, user_id, kind):
    _stamp(conn, user_id, "emailed_at", kind)


def _stamp(conn, user_id, column, kind):
    if column not in ("emailed_at", "served_at"):        # never interpolated
        return                                           # from user input
    if not ensure_schema(conn):
        return
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_offers SET " + column + " = NOW() "
                    "WHERE user_id = %s AND kind = %s", (int(user_id), kind))
        conn.commit()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] {column} stamp failed for {user_id}: {e}", flush=True)


# ── segment predicates (for the newsletter engine) ──────────────────────────

def table_ready(conn):
    """True once user_offers exists. to_regclass is checked rather than the
    error caught, because in Postgres a failed statement poisons the whole
    transaction — a missing table would take an unrelated query down with it."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.user_offers') AS t")
        row = cur.fetchone()
        cur.close()
        return bool(_get(row, "t", 0))
    except Exception:                                       # pragma: no cover
        _rollback(conn)
        return False


def sql_no_live_offer(conn, alias="u"):
    """SQL predicate: this user is NOT already holding an unused, unexpired
    offer. Degrades to TRUE before the table exists, so a campaign written
    against it does not break on a fresh deploy."""
    if not table_ready(conn):
        return "TRUE"
    return ("NOT EXISTS (SELECT 1 FROM user_offers o WHERE o.user_id = "
            + alias + ".id AND o.used_at IS NULL AND o.expires_at > NOW())")


def sql_never_used(conn, alias="u"):
    """SQL predicate: this user has never redeemed a discount."""
    if not table_ready(conn):
        return "TRUE"
    return ("NOT EXISTS (SELECT 1 FROM user_offers o WHERE o.user_id = "
            + alias + ".id AND o.used_at IS NOT NULL)")


# ── the email ───────────────────────────────────────────────────────────────

def send_offer_email(conn, user_id, email, kind=WELCOME):
    """Retired campaign; never send even when an old template is enabled."""
    return False


def _fill(text, ctx):
    """The two tokens this email adds on top of the shared set. Kept out of
    newsletter_content.render_tokens because they only mean anything here."""
    return (text or "").replace("{{OFFER_PERCENT}}", str(ctx["percent"])) \
                       .replace("{{OFFER_HOURS}}", str(ctx["hours"]))


# ── Paddle ──────────────────────────────────────────────────────────────────

def _paddle_base():
    return ("https://sandbox-api.paddle.com"
            if os.environ.get("PADDLE_MODE") == "sandbox"
            else "https://api.paddle.com")


def _headers():
    key = os.environ.get("PADDLE_API_KEY")
    if not key:
        return None
    return {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


# Which plans a 50% intro may be applied to. NOT every purchasable plan:
# Frontier is deliberately absent. It is priced at a 50% margin (5,000 credits
# at USD_PER_CREDIT is $25 of model spend against $50), so half off is a month
# sold at exactly cost — and the model it promises is the expensive one. The
# discount is an acquisition tool for the entry tiers; the top tier is bought
# by people who already know what they want.
DISCOUNTABLE_PLANS = ("ai", "ai_pro")


def _monthly_price_ids():
    """The prices the discount may be applied to: the entry tiers, monthly only.

    Enforced at PADDLE via restrict_to, not just in our UI, because a discount
    that our checkout declines to attach is still a discount someone can type
    the code into. Two independent reasons a price is excluded:

      * ANNUAL — `recur: false` discounts the first billing PERIOD, which on a
        yearly price is twelve months of credits for half of one year's money.
      * FRONTIER — see DISCOUNTABLE_PLANS above.
    """
    try:
        from routes.paddle import PLANS, PURCHASABLE_PLANS
    except Exception:                                       # pragma: no cover
        return []
    plans = [p for p in DISCOUNTABLE_PLANS if p in PURCHASABLE_PLANS]
    return [PLANS[p]["price_id"] for p in plans
            if PLANS.get(p, {}).get("price_id")]


def discount_id():
    """Do not return cached IDs, env overrides, or recreate retired codes."""
    return None


def _find_discount(headers):
    return None


def _create_discount(headers):
    return None


def apply_to_subscription(subscription_id):
    return False, "This offer is no longer available."
