"""Server-side $1 warning, calibrated to an owner-confirmed provider balance.

Anthropic inference credentials cannot read the Console wallet. This is an
explicit estimate of that balance minus subsequent metered Valmera spend, never
the trial budget or a claim to see top-ups, grant expiry or outside API traffic.
It has no authority over admission, models, customer credits or provider billing.
"""
import html
import json
import math
import uuid
from datetime import datetime, timezone

KEY = "anthropic_wallet"
STATE_KEY = "anthropic_wallet_monitor"
THRESHOLD = 1.0
KIND = "anthropic_wallet_low"


def _row(row, key):
    return row.get(key) if isinstance(row, dict) else row[0] if row else None


def _read(cur, key):
    cur.execute("SELECT value FROM app_kv WHERE key=%s", (key,))
    raw = _row(cur.fetchone(), "value")
    return json.loads(raw) if isinstance(raw, str) else raw or {}


def _put(cur, key, value):
    cur.execute("""INSERT INTO app_kv (key,value,updated_at) VALUES (%s,%s,NOW())
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()""",
                (key, json.dumps(value, sort_keys=True)))


def _spend(cur):
    # Lifetime counter: a new trial/monthly cycle must not replenish a wallet.
    cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                   AS usd FROM trial_budget_days""")
    customers = float(_row(cur.fetchone(), "usd"))
    # trial_budget_days is the customers' line; company spend draws on the same
    # wallet from spend_ledger (migration 043).
    cur.execute("SELECT to_regclass('public.spend_ledger') AS t")
    if not _row(cur.fetchone(), "t"):
        return customers
    cur.execute("""SELECT COALESCE(SUM(usd),0) AS usd FROM spend_ledger
                   WHERE provider='anthropic' AND lane<>'customer'""")
    return customers + float(_row(cur.fetchone(), "usd"))


def estimate(config, spend):
    if not config:
        return None
    if not math.isfinite(spend) or spend < config["meter_baseline_usd"] - .000001:
        return None  # Counter loss is unknown, never a funded or empty wallet.
    return round(config["confirmed_balance_usd"] - max(0, spend-config["meter_baseline_usd"]), 6)


def status(conn):
    from founder_alert import founder_email
    with conn.cursor() as cur:
        config = _read(cur, KEY)
        remaining = estimate(config, _spend(cur)) if config else None
        monitor = _read(cur, STATE_KEY)
    return {"configured": bool(config), "source": "confirmed_balance_minus_valmera_spend",
            "estimated": True, "estimated_remaining_usd": remaining,
            "threshold_usd": THRESHOLD, "recipient": founder_email(),
            "confirmed_balance_usd": config.get("confirmed_balance_usd"),
            "confirmed_at": config.get("confirmed_at"),
            "last_checked_at": monitor.get("checked_at"),
            "needs_resync": bool(config and remaining is None),
            "note": "Update the confirmed balance after recharging or receiving new credits. "
                    "This estimate excludes spending outside Valmera and credit expiry."}


def confirm_balance(conn, balance):
    if type(balance) not in (int, float) or not math.isfinite(balance) or not 0 <= balance <= 1000000:
        raise ValueError("balance_usd must be a finite, nonnegative dollar amount")
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('anthropic_wallet_monitor'))")
        old = _read(cur, KEY)
        spend = _spend(cur)
        # A low-balance resync must not send another identical warning. A
        # confirmed balance above $1 rearms the next depletion episode.
        episode = old.get("episode")
        if not episode or balance > THRESHOLD:
            episode = uuid.uuid4().hex
        value = {"confirmed_balance_usd": float(balance), "meter_baseline_usd": spend,
                 "confirmed_at": datetime.now(timezone.utc).isoformat(), "episode": episode}
        _put(cur, KEY, value)
        if balance > THRESHOLD:
            # Preserve an audit record, but stop retrying an obsolete warning.
            cur.execute("""UPDATE trial_alerts SET status='failed',attempt_count=8,
                           last_error='Superseded by a new confirmed balance'
                           WHERE kind=%s AND sent_at IS NULL AND status IN ('pending','failed')""", (KIND,))
    return value


def scan(conn, now=None):
    """Atomic outbox insertion; safe across schedulers, restarts and retries."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('anthropic_wallet_monitor')) AS locked")
        if not _row(cur.fetchone(), "locked"):
            return 0
        config = _read(cur, KEY)
        if not config:
            return 0
        monitor = _read(cur, STATE_KEY)
        if monitor.get("checked_at") and (now-datetime.fromisoformat(monitor["checked_at"])).total_seconds() < 90:
            return 0
        remaining = estimate(config, _spend(cur))
        _put(cur, STATE_KEY, {"checked_at": now.isoformat(), "estimated_remaining_usd": remaining})
        if remaining is None or remaining > THRESHOLD:
            return 0
        subject = f"Valmera: Anthropic credit estimated at ${max(remaining,0):.2f} — $1 warning"
        body = (f"<h2>Anthropic credit: estimated ${max(remaining,0):.2f} remaining</h2>"
                f"<p>Your $1 warning has been reached. The last confirmed balance was "
                f"${config['confirmed_balance_usd']:.2f} at {html.escape(config['confirmed_at'])}.</p>"
                "<p>This is a server-side estimate using subsequent Valmera API spending. "
                "It cannot see other organization spending, new credits or credit expiry. "
                "It does not disable paid customers.</p>"
                '<p><a href="https://platform.claude.com/settings/billing">Check Anthropic billing</a> · '
                '<a href="https://valmera.io/admin">Update the confirmed balance in Valmera Admin</a></p>')
        cur.execute("""INSERT INTO trial_alerts(dedupe_key,kind,subject,html_content)
                       VALUES (%s,%s,%s,%s) ON CONFLICT(dedupe_key) DO NOTHING""",
                    (f"wallet_low:{config['episode']}", KIND, subject, body))
        return cur.rowcount


def tick():
    from claude_trial import _connect
    conn = _connect()
    try:
        count = scan(conn)
        conn.commit()
        return count
    finally:
        conn.close()
