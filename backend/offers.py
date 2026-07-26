"""The 50%-off offer: who has one, how long it lives, and how it reaches Paddle.

Round 48. Three moments now carry the same offer:

  welcome   a new account gets one the moment it exists, live for 24 hours.
            The countdown on the pricing page and the email both read from
            here, so they cannot disagree.
  winback   a ONE-OFF broadcast to everyone who has never subscribed. Same
            discount, same 24 hours, sent by the newsletter engine.
  save      a trialling user who presses cancel is offered it instead of being
            let go silently.

ONE 50% PER ACCOUNT, EVER
-------------------------
This is the whole safety property, and it is enforced in exactly two places so
it cannot be reasoned about wrongly:

  * `UNIQUE (user_id, kind)` — a user can never hold two of the same kind, so
    every mint path is idempotent by construction rather than by care.
  * `mint()` refuses outright once ANY of that user's offers has been used.

The second clause is what stops the double-discount the save offer would
otherwise create: someone who started their trial on the welcome discount is
already paying half price, and handing them another 50% at the cancel screen
would stack two discounts on one subscription. They get the honest version of
that screen instead — what they keep, and when it ends.

WHY THE DISCOUNT IS MONTHLY-ONLY
--------------------------------
`recur: false`, so it applies to the first billing period and nothing after.
On a monthly plan that is one $15 or $25 month — a real acquisition cost
against a plan that carries a 50% margin at list price. On an ANNUAL plan the
same flag would discount a whole year: $150 for 12 months of credits that cost
us $180. So the Paddle discount is `restrict_to` the two monthly prices, which
means the rule is enforced by Paddle itself and not only by our UI.

FAILURE POSTURE
---------------
Everything here fails to "no offer". A missing table, an unreachable Paddle, a
discount that will not resolve — each of those returns None and the pricing
page renders at full price with no countdown. That is the honest-off contract
used throughout this codebase: never show someone a struck-through price we
cannot actually charge them.

SCHEMA (self-provisioned, additive, idempotent — same as newsletter.py):

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
    );
"""

import datetime
import os
import time

import requests

PERCENT_OFF = int(os.getenv("OFFER_PERCENT_OFF", "50"))
OFFER_HOURS = int(os.getenv("OFFER_HOURS", "24"))

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
    """Create this user's offer of `kind`, or return the existing one.

    Returns the offer dict, or None when the user is not eligible (already
    redeemed a discount) or the table is unavailable. Never raises — every
    caller is on a path where the offer is a bonus, not the point.
    """
    if kind not in KINDS:
        return None
    if not ensure_schema(conn):
        return None
    if has_ever_used(conn, user_id):
        return None
    hours = OFFER_HOURS if hours is None else hours
    try:
        cur = conn.cursor()
        # ON CONFLICT DO NOTHING, then read: the UNIQUE constraint is what makes
        # this exactly-once across the several places that mint a welcome offer
        # (email verification, OAuth callback, and the pricing page itself), no
        # matter which one the user reaches first or how many times.
        cur.execute("""
            INSERT INTO user_offers (user_id, kind, percent_off, expires_at)
            VALUES (%s, %s, %s, NOW() + (%s || ' hours')::interval)
            ON CONFLICT (user_id, kind) DO NOTHING
        """, (int(user_id), kind, PERCENT_OFF, str(int(hours))))
        conn.commit()
        cur.close()
    except Exception as e:
        _rollback(conn)
        print(f"[offers] mint {kind} failed for {user_id}: {e}", flush=True)
        return None
    return _row(conn, user_id, kind)


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
    """This user's usable offer, or None.

    Usable means: not redeemed, not expired, and backed by a discount Paddle
    will actually honour. That last clause is the important one — showing a
    struck-through price we cannot charge is worse than showing no offer.
    """
    if not user_id or not ensure_schema(conn):
        return None
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, kind, percent_off, created_at, expires_at,
                              emailed_at, served_at, used_at
                       FROM user_offers
                       WHERE user_id = %s AND used_at IS NULL
                         AND expires_at > NOW()
                       ORDER BY expires_at DESC LIMIT 1""", (int(user_id),))
        row = cur.fetchone()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _rollback(conn)
        print(f"[offers] live lookup failed for {user_id}: {e}", flush=True)
        return None
    offer = _as_dict(row)
    if not offer:
        return None
    if not discount_id():
        return None
    return offer


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
    """Send the offer email for an already-minted offer, at most once.

    Imported lazily: the newsletter module owns Brevo, the branded shell and
    the unsubscribe token, and it needs a Flask app context that this module
    should not assume it has. Returns True only if Brevo accepted it.
    """
    if not email:
        return False
    offer = _row(conn, user_id, kind)
    if not offer or offer.get("emailed_at") or offer.get("used_at"):
        return False
    if seconds_left(offer) <= 0:
        return False
    try:
        from routes.newsletter import (_send_one, _unsub_url, get_db as _nldb,
                                       get_template)
        from routes.newsletter_content import wrap_email, render_tokens
    except Exception as e:                                  # pragma: no cover
        print(f"[offers] email module unavailable: {e}", flush=True)
        return False
    try:
        # The template lives with every other lifecycle email so the admin can
        # edit the copy without a deploy.
        nl = _nldb()
        try:
            tmpl = get_template(nl, "offer_50")
        finally:
            nl.close()
        if not tmpl.get("enabled"):
            return False
        unsub = _unsub_url(email)
        ctx = {"percent": offer["percent_off"],
               "hours": max(1, round(seconds_left(offer) / 3600))}
        body = _fill(render_tokens(tmpl["body_html"],
                                   cta_url="https://valmera.io/subscribe",
                                   unsub_url=unsub), ctx)
        subject = _fill(render_tokens(tmpl["subject"]), ctx)
        pre = _fill(render_tokens(tmpl.get("preheader", ""),
                                  unsub_url=unsub), ctx)
        ok = _send_one(email, subject, wrap_email(body, unsub, preheader=pre),
                       unsub)
    except Exception as e:
        print(f"[offers] offer email to {email} failed: {e}", flush=True)
        return False
    if ok:
        mark_emailed(conn, user_id, kind)
    return ok


def grant_welcome(conn, user_id, email):
    """Mint a new account's 24-hour offer and email it. Idempotent, silent on
    failure, and safe to call from every signup path there is.

    Called at the moment an account becomes real — email verification and the
    Google callback — because that is when the clock should start and when the
    email is worth sending. The UNIQUE constraint makes the second and third
    caller free, and nothing here can fail a signup: an account created without
    an offer is a missed discount, an exception on this line is a user who
    cannot get in.
    """
    try:
        offer = mint(conn, user_id, WELCOME)
        if offer:
            send_offer_email(conn, user_id, email, WELCOME)
    except Exception as e:                                  # pragma: no cover
        print(f"[offers] welcome grant failed for {user_id}: {e}", flush=True)


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


def _monthly_price_ids():
    """The prices the discount may be applied to: monthly only.

    Enforced at PADDLE, not just in our UI, because `recur: false` on an annual
    price would discount twelve months of credits — which cost more than the
    discounted year brings in.
    """
    try:
        from routes.paddle import PLANS, PURCHASABLE_PLANS
    except Exception:                                       # pragma: no cover
        return []
    return [PLANS[p]["price_id"] for p in sorted(PURCHASABLE_PLANS)
            if PLANS.get(p, {}).get("price_id")]


def discount_id():
    """The Paddle discount id to attach at checkout, or None.

    Resolution order: an explicit env override, then a lookup by code, then
    creation. Cached for the life of the process once found; re-tried at most
    once a minute while it is not, so an outage does not turn into a request
    storm. None means the whole offer surface goes quiet — see the module
    docstring on why that is the right failure.
    """
    override = os.getenv("PADDLE_DISCOUNT_ID", "").strip()
    if override:
        return override
    if _discount["id"]:
        return _discount["id"]
    if time.time() - _discount["checked_at"] < _RECHECK_SECONDS:
        return None
    _discount["checked_at"] = time.time()
    headers = _headers()
    if not headers:
        return None
    found = _find_discount(headers)
    if not found:
        # Create, then look ONE more time. Paddle rejects a duplicate code, so
        # a transient lookup failure followed by a create that legitimately
        # 409s would otherwise leave the offer permanently off with a perfectly
        # good discount sitting in the account.
        found = _create_discount(headers) or _find_discount(headers)
    _discount["id"] = found
    return found


def _find_discount(headers):
    try:
        # No status filter: it is another way for the lookup to miss a discount
        # that does exist, and a code collision on create is the expensive
        # failure here.
        r = requests.get(f"{_paddle_base()}/discounts",
                         params={"code": DISCOUNT_CODE},
                         headers=headers, timeout=12)
        if r.status_code != 200:
            print(f"[offers] discount lookup HTTP {r.status_code}: "
                  f"{r.text[:200]}", flush=True)
            return None
        for d in (r.json().get("data") or []):
            if (d.get("code") or "").upper() == DISCOUNT_CODE.upper():
                print(f"[offers] using existing Paddle discount {d.get('id')}",
                      flush=True)
                return d.get("id")
    except Exception as e:
        print(f"[offers] discount lookup failed: {e}", flush=True)
    return None


def _create_discount(headers):
    body = {
        "description": f"Valmera {PERCENT_OFF}% intro offer",
        "type": "percentage",
        "amount": str(PERCENT_OFF),
        # First billing period only. On a plan with a 3-day trial that is the
        # first real charge — which is exactly the moment the offer is for.
        "recur": False,
        "code": DISCOUNT_CODE,
        "enabled_for_checkout": True,
    }
    prices = _monthly_price_ids()
    if prices:
        body["restrict_to"] = prices
    try:
        r = requests.post(f"{_paddle_base()}/discounts", json=body,
                          headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            print(f"[offers] discount create HTTP {r.status_code}: "
                  f"{r.text[:300]}", flush=True)
            return None
        did = (r.json().get("data") or {}).get("id")
        print(f"[offers] created Paddle discount {did} "
              f"({PERCENT_OFF}% first period, monthly prices only)", flush=True)
        return did
    except Exception as e:
        print(f"[offers] discount create failed: {e}", flush=True)
        return None


def apply_to_subscription(subscription_id):
    """Attach the discount to an EXISTING subscription — the save offer.

    A trialling user has already checked out, so there is no checkout left to
    attach a discount to; it has to go onto the subscription itself, effective
    immediately, which means the charge at the end of their trial is the
    discounted one. Returns (ok, error_text_for_a_human).
    """
    did = discount_id()
    headers = _headers()
    if not did or not headers:
        return False, "That offer isn't available right now."
    try:
        r = requests.patch(
            f"{_paddle_base()}/subscriptions/{subscription_id}",
            json={"discount": {"id": did, "effective_from": "immediately"}},
            headers=headers, timeout=15)
    except Exception as e:
        print(f"[offers] apply to {subscription_id} failed: {e}", flush=True)
        return False, "We couldn't apply that just now — please try again."
    if r.status_code not in (200, 202):
        print(f"[offers] apply HTTP {r.status_code}: {r.text[:300]}",
              flush=True)
        return False, "We couldn't apply that just now — please try again."
    return True, None
