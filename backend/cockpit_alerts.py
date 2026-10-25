"""Owner alerts and the 08:00 daily digest (strategy 1025 §4.4, §4.5).

Everything here goes to ONE address: the admin inbox (ADMIN_TO, the address
the admin dashboard is gated to). There is no recipient parameter anywhere in
this module and nothing reads a customer's address as a destination: these
emails are the product reporting to its owner, never mail to a customer.

How it runs:
  * a BackgroundScheduler tick every 5 minutes in each gunicorn worker; a
    PostgreSQL advisory lock makes exactly one of them scan;
  * each alert has a dedupe key claimed in app_kv (`cockpit_alert:<key>`)
    BEFORE it is sent, so restarts, overlapping workers and retries never
    send the same alert twice; a failed send releases its claims for the
    next tick;
  * one tick sends at most ONE email that lists every new alert, and at most
    MAX_ALERT_EMAILS_PER_DAY a day, so alerts never eat the Brevo reserve
    that sign-in codes and receipts share;
  * the digest is claimed per Dubai day (`cockpit_digest:<date>`) and sent by
    the first tick at or after 08:00 +04:00 (04:00 UTC): cron-safe, a worker
    asleep at 08:00 sends it on its next tick, and it is never sent twice.

Reads run on a read-only connection; the only writes are app_kv claims.
"""
import html
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from admin_metrics import defs, ranges
from admin_metrics.cockpit import INTERNAL_IDS

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:                                      # pragma: no cover
    BackgroundScheduler = None

ADMIN_TO = defs.ADMIN_EMAIL          # thevalmera@gmail.com, never a customer
ADMIN_URL = "https://valmera.io/admin"
TICK_LOCK_ID = 7_710_251_025
TICK_MINUTES = 5
MAX_ALERT_EMAILS_PER_DAY = int(os.getenv("COCKPIT_MAX_ALERT_EMAILS_PER_DAY",
                                         "24"))
DIGEST_HOUR_LOCAL = 8
INFRA_ALERT_USD_PER_DAY = float(os.getenv("COCKPIT_INFRA_ALERT_USD", "3"))
TRIAL_THRESHOLDS = (50, 80, 95)
FIRST_LOOKBACK = timedelta(minutes=30)   # a fresh install never replays history
INTERNAL = list(INTERNAL_IDS)


def enabled():
    return os.getenv("COCKPIT_ALERTS_ENABLED", "1") in ("1", "true", "True")


def _e(v):
    return html.escape(str(v), quote=True) if v not in (None, "") else "—"


# ── app_kv claims ────────────────────────────────────────────────────────

def claim(wcur, key, value=None):
    """True when this process now owns `key` (first writer wins)."""
    wcur.execute("""INSERT INTO app_kv (key, value, updated_at)
                    VALUES (%s, %s, NOW()) ON CONFLICT (key) DO NOTHING
                    RETURNING key""",
                 (key, json.dumps(value or {"at": _now_iso()})))
    return wcur.fetchone() is not None


def release(wcur, key):
    wcur.execute("DELETE FROM app_kv WHERE key = %s", (key,))


def _kv(rcur, key):
    rcur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
    row = rcur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _put(wcur, key, value):
    wcur.execute("""INSERT INTO app_kv (key, value, updated_at)
                    VALUES (%s, %s, NOW()) ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()""",
                 (key, json.dumps(value, default=str)))


# ── What is new since the last tick ──────────────────────────────────────

def _customer_sql(alias="u"):
    return (f"{defs.customer(alias)} AND NOT ({alias}.id = ANY(%(internal)s))")


def event_alerts(rcur, since, now):
    """Alerts that are events: (dedupe key, section, line html)."""
    p = {"since": since, "since_naive": since.replace(tzinfo=None),
         "internal": INTERNAL, "now": now}
    out = []
    # Renewals and one-time charges (first payments on a subscription are
    # announced by the paid-subscriber email, which now carries the first
    # prompt and "result before paying").
    rcur.execute(f"""
        SELECT p.transaction_id, p.amount_cents, p.currency, p.plan, p.status,
               u.id, u.email,
               EXISTS (SELECT 1 FROM payments e
                        WHERE e.subscription_id = p.subscription_id
                          AND e.transaction_id <> p.transaction_id
                          AND {defs.success('e')}
                          AND COALESCE(e.occurred_at, e.created_at)
                              < COALESCE(p.occurred_at, p.created_at))
                   AS renewal
          FROM payments p JOIN users u ON u.id = p.user_id
         WHERE p.updated_at >= %(since_naive)s AND {_customer_sql()}
           AND (({defs.success('p')}) OR p.amount_cents < 0
                OR p.status = 'chargeback_warning')""", p)
    for r in rcur.fetchall():
        money = f"{r['currency']} {abs(r['amount_cents']) / 100:.2f}"
        if r["amount_cents"] < 0 or r["status"] == "chargeback_warning":
            out.append((f"adj:{r['transaction_id']}", "Money returned",
                        f"<b>{_e(r['status'])}</b> {money} · {_e(r['email'])}"
                        f" (#{r['id']})"))
        elif r["renewal"] or not r["plan"]:
            out.append((f"pay:{r['transaction_id']}", "Payments",
                        f"{'Renewal' if r['renewal'] else 'Payment'} {money}"
                        f" · {_e(r['email'])} (#{r['id']}) · "
                        f"{_e(r['plan'])}"))
    # Renewal turned off (a provider event, scheduled cancel).
    rcur.execute(f"""
        SELECT DISTINCT ON (h.subscription_id) h.subscription_id,
               h.provider_updated_at, u.id, u.email,
               (SELECT min(COALESCE(f.occurred_at, f.created_at))
                  FROM payments f WHERE f.subscription_id = h.subscription_id
                   AND {defs.success('f')}) AS paid_at,
               (SELECT count(*) FROM video_jobs j WHERE j.user_id = u.id
                   AND j.type IN ('agent_turn', 'mcp_tool')
                   AND j.state = 'done') AS edits,
               (SELECT count(*) FROM client_events c WHERE c.user_id = u.id
                   AND c.kind IN ('download_triggered',
                                  'mcp_export_delivered')) AS results,
               (SELECT LEFT(j.error, 160) FROM video_jobs j
                 WHERE j.user_id = u.id AND j.state = 'failed'
                 ORDER BY j.updated_at DESC LIMIT 1) AS last_error
          FROM admin_subscription_history h
          JOIN (SELECT DISTINCT subscription_id, user_id FROM payments
                 WHERE subscription_id IS NOT NULL) m
            ON m.subscription_id = h.subscription_id
          JOIN users u ON u.id = m.user_id
         WHERE h.scheduled_action = 'cancel' AND h.event_id IS NOT NULL
           AND h.provider_updated_at >= %(since)s AND {_customer_sql()}
         ORDER BY h.subscription_id, h.provider_updated_at""", p)
    for r in rcur.fetchall():
        after = ""
        if r["paid_at"]:
            mins = (r["provider_updated_at"].replace(tzinfo=None)
                    - r["paid_at"]).total_seconds() / 60
            after = f" · {mins:.0f} min after paying"
        out.append((f"off:{r['subscription_id']}:"
                    f"{r['provider_updated_at']:%Y%m%d}", "Renewal turned off",
                    f"{_e(r['email'])} (#{r['id']}){after} · edits "
                    f"{r['edits']} · results {r['results']}"
                    + (f" · last error: {_e(r['last_error'])}"
                       if r["last_error"] else "")))
    # A paying customer was shown a service error.
    rcur.execute(f"""
        SELECT m.id, LEFT(m.content, 180) AS content, u.id AS uid, u.email,
               p.id AS project_id
          FROM chat_messages m
          JOIN projects p ON p.chat_session_id = m.session_id
          JOIN users u ON u.id = p.user_id
         WHERE m.role = 'assistant' AND m.created_at >= %(since_naive)s
           AND m.meta->>'error' IN ('job_failed', 'job_died')
           AND {defs.paying('u')} AND {_customer_sql()}""", p)
    for r in rcur.fetchall():
        out.append((f"err:{r['id']}", "Paying customer saw a service error",
                    f"{_e(r['email'])} (#{r['uid']}) · project "
                    f"{r['project_id']}: {_e(r['content'])}"))
    # Model account out of credit, executor missing, deploy-killed jobs.
    rcur.execute(f"""
        SELECT j.id, j.type, j.user_id, LEFT(j.error, 200) AS error, u.email
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.state = 'failed' AND j.updated_at >= %(since)s
           AND j.user_id <> 60
           AND (j.error ILIKE '%%insufficient_quota%%'
                OR j.error ILIKE '%%no credits remaining%%'
                OR j.error ILIKE '%%no remote executor is configured%%'
                OR j.error ILIKE '%%modal or cloudflare is required%%'
                OR j.error ILIKE '%%deploy in progress%%')""", p)
    for r in rcur.fetchall():
        text = (r["error"] or "").lower()
        section = ("LLM provider out of credit"
                   if "quota" in text or "credits remaining" in text else
                   "Deploy killed a user job" if "deploy" in text else
                   "No executor for a user job")
        out.append((f"job:{r['id']}", section,
                    f"{_e(r['type'])} job {r['id']} · {_e(r['email'])} "
                    f"(#{r['user_id']}): {_e(r['error'])}"))
    # Worker-raised alarms (R2 routing, R5 LLM balance) parked in app_kv.
    for key, section in (("llm_balance", "LLM provider balance"),
                         ("executor_routing_alarm", "Executor routing")):
        v = _kv(rcur, key) or {}
        at = v.get("alarm_at") if isinstance(v, dict) else None
        if at and str(at) >= since.isoformat():
            out.append((f"{key}:{at}", section, _e(v.get("alarm"))))
    return out


def daily_alerts(board, now):
    """Alerts read off the Health board (once a day per account/threshold)."""
    out = []
    rows = {r["id"]: r for r in board.get("rows") or []}
    for r in rows.values():
        ren = r.get("renewal") or {}
        days = ren.get("days_left")
        if "CHARGE_SOON_NO_USE" in r["flags"] and days is not None \
                and days <= 3:
            out.append((f"chargesoon:{r['id']}:{ren.get('next_charge_at')}",
                        "Charge soon, no use",
                        f"{_e(r['email'])} (#{r['id']}) is charged "
                        f"${_e(ren.get('next_charge_usd'))} on "
                        f"{_e((ren.get('next_charge_at') or '')[:10])} with 0 "
                        "edits in 30 days: pause or refund first"))
        retry = (r.get("billing") or {}).get("next_retry_at")
        if "PAST_DUE_DORMANT" in r["flags"] and retry and \
                datetime.fromisoformat(retry.replace("Z", "+00:00")) \
                <= now + timedelta(days=1):
            out.append((f"pastdue:{r['id']}:{retry[:10]}",
                        "Past-due retry on a dormant account tomorrow",
                        f"{_e(r['email'])} (#{r['id']}): Paddle retries the "
                        f"card on {_e(retry[:16].replace('T', ' '))} UTC for "
                        "someone inactive 14+ days: cancel the retry"))
    infra = (board.get("strip") or {}).get("infra") or {}
    y = infra.get("yesterday_usd")
    if y is not None and y > INFRA_ALERT_USD_PER_DAY:
        out.append((f"infra:{(now - timedelta(days=1)):%Y%m%d}",
                    "Infrastructure over budget",
                    f"${y:.2f} yesterday (alert above "
                    f"${INFRA_ALERT_USD_PER_DAY:.2f}/day); cycle projected "
                    f"${_e(infra.get('cycle_projected_usd'))}, owner share "
                    f"{_e(infra.get('owner_share_7d_pct'))}%"))
    trial = (board.get("strip") or {}).get("first_cut") or {}
    for w in trial.get("llm_credit") or []:
        for t in (80, 95):
            if (w.get("used_pct") or 0) >= t:
                out.append((f"llmcredit:{w['provider']}:{t}:"
                            f"{w.get('loaded_at')}",
                            f"LLM credit {t}% used",
                            f"{_e(w['provider'])}: ${_e(w.get('spent_usd'))} "
                            f"of ${_e(w.get('loaded_usd'))} spent since "
                            f"{_e((w.get('loaded_at') or '')[:10])}; "
                            f"${_e(w.get('remaining_usd'))} left"))
    used = trial.get("used_pct") or 0
    for t in TRIAL_THRESHOLDS:
        if used >= t:
            out.append((f"trial:{t}:{now:%Y%m}", f"Trial budget {t}% used",
                        f"{used}% of ${trial.get('budget_usd')} used; "
                        f"${trial.get('month_usd')} this month"))
    return out


# ── Email ────────────────────────────────────────────────────────────────

def _send(subject, body_html):
    """Send to the admin inbox only. Returns True when Brevo accepted it."""
    import brevo_delivery
    from routes.newsletter_content import plain_text
    payload = {
        "sender": {"name": os.getenv("FROM_NAME", "Valmera"),
                   "email": os.getenv("FROM_EMAIL", "support@valmera.io")},
        "to": [{"email": ADMIN_TO}],
        "subject": subject[:180],
        "htmlContent": body_html,
        "textContent": plain_text(body_html),
    }
    return bool(brevo_delivery.send_email(payload, category="critical"))


def _shell(title, sections, footer=None):
    parts = []
    for name, lines in sections:
        if not lines:
            continue
        items = "".join(f'<li style="margin:0 0 6px">{line}</li>'
                        for line in lines)
        parts.append(f'<h2 style="margin:18px 0 8px;font-size:14px;'
                     f'color:#fff">{_e(name)}</h2><ul style="margin:0;'
                     f'padding-left:18px;color:#d6d6dc;font-size:13px;'
                     f'line-height:1.5">{items}</ul>')
    foot = (f'<p style="margin:18px 0 0;color:#6b6b75;font-size:12px">'
            f'{footer}</p>') if footer else ""
    return (f'<div style="background:#0b0b0f;padding:24px;font-family:'
            f'-apple-system,Segoe UI,Roboto,sans-serif"><div style="max-width:'
            f'640px;margin:0 auto;background:#141419;border:1px solid #26262e;'
            f'border-radius:14px;padding:22px 24px"><h1 style="margin:0 0 6px;'
            f'color:#fff;font-size:18px">{_e(title)}</h1>{"".join(parts)}'
            f'<p style="margin:18px 0 0"><a href="{ADMIN_URL}" style="color:'
            f'#f87171">Open the Health board</a></p>{foot}</div></div>')


def alert_email(items):
    sections = {}
    for _key, section, line in items:
        sections.setdefault(section, []).append(line)
    n = len(items)
    first = items[0][1] if items else ""
    subject = (f"Valmera alert: {first}" if n == 1 else
               f"Valmera: {n} alerts ({first} …)")
    return subject, _shell("Needs you now", list(sections.items()),
                           "Sent to the admin inbox only, never to a "
                           "customer. Each alert is sent once.")


# ── The digest ───────────────────────────────────────────────────────────

def funnel_yesterday(rcur, now):
    day = ranges.local_today(now) - timedelta(days=1)
    start, end = ranges.local_midnight(day), ranges.local_midnight(
        day + timedelta(days=1))
    p = {"s": start, "e": end, "sn": start.replace(tzinfo=None),
         "en": end.replace(tzinfo=None), "internal": INTERNAL}
    rcur.execute(f"""
        WITH s AS (SELECT u.id FROM users u WHERE {_customer_sql()}
                     AND u.created_at >= %(sn)s AND u.created_at < %(en)s),
        ev AS (SELECT ce.user_id, ce.kind, ce.created_at, ce.detail
                 FROM client_events ce
                WHERE ce.created_at >= %(s)s AND ce.created_at < %(e)s
                  AND NOT (ce.user_id = ANY(%(internal)s)))
        SELECT (SELECT count(*) FROM s) AS signups,
               (SELECT count(DISTINCT user_id) FROM ev
                 WHERE kind = 'upload_landed') AS uploaders,
               (SELECT count(DISTINCT user_id) FROM ev
                 WHERE kind IN ('free_result_delivered',
                                'first_cut_delivered')) AS first_cuts,
               (SELECT count(DISTINCT user_id) FROM ev
                 WHERE kind IN ('free_result_watched', 'first_cut_watched')
                   AND COALESCE(NULLIF(detail->>'pct', '')::float, 0) >= 50)
                   AS watched_half,
               (SELECT count(DISTINCT user_id) FROM ev
                 WHERE kind IN ('free_result_keep_clicked',
                                'first_cut_keep_clicked')) AS keep_clicks,
               (SELECT count(DISTINCT user_id) FROM ev
                 WHERE kind = 'checkout_stage'
                   AND detail->>'stage' = 'opened') AS checkouts,
               (SELECT COALESCE(sum(p.amount_cents), 0) FROM payments p
                  JOIN users u ON u.id = p.user_id
                 WHERE {defs.success('p')} AND p.currency = 'USD'
                   AND COALESCE(p.occurred_at, p.created_at) >= %(sn)s
                   AND COALESCE(p.occurred_at, p.created_at) < %(en)s
                   AND {_customer_sql()}) AS cash_cents""", p)
    out = dict(rcur.fetchone() or {})
    out["day"] = day.isoformat()
    out["start"], out["end"] = start, end
    return out


def digest_email(board, funnel, now):
    s = board.get("strip") or {}
    rows = board.get("rows") or []
    start, end = funnel["start"], funnel["end"]

    def within(iso):
        if not iso:
            return False
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return start <= at < end
    payers = [r for r in rows if within(r.get("first_paid_at"))]
    with_result = sum(1 for r in payers if r.get("result_before_paying"))
    offs = (s.get("renewal_offs_7d") or {}).get("ids") or []
    errs = s.get("service_errors_24h") or {}
    infra = s.get("infra") or {}
    trial = s.get("first_cut") or {}
    fac = board.get("factory") or {}
    urgent = [r for r in rows if r["health"] == "red"] + \
        [r for r in rows if r["health"] == "amber"]
    funnel_line = (
        f"{funnel['signups']} signups → {funnel['uploaders']} uploaded → "
        f"{funnel['first_cuts']} First cuts → {funnel['watched_half']} "
        f"watched >50% → {funnel['keep_clicks']} Keep it → "
        f"{funnel['checkouts']} opened checkout → {len(payers)} paid "
        f"({with_result} saw a result first)")
    sections = [
        ("Funnel, " + funnel["day"], [funnel_line,
                                      f"Renewal turned off in the last 7 "
                                      f"days: {len(offs)}"]),
        ("Money", [f"Committed MRR ${_e((s.get('committed_mrr') or {}).get('value'))}"
                   f" · engaged ${_e((s.get('engaged_committed_mrr') or {}).get('value'))}"
                   f" · Paddle-active ${_e((s.get('paddle_active_mrr') or {}).get('value'))}",
                   f"Cash yesterday ${funnel['cash_cents'] / 100:.2f}"]),
        ("Most urgent accounts", [
            f"<b>{_e(r['email'])}</b> (#{r['id']}) · "
            f"{_e(', '.join(r['flags'][:3]))} → {_e(r.get('next_action'))}"
            for r in urgent[:5]]),
        ("Service errors shown to customers (24 h)", [
            ", ".join(f"{c['label']} {c['n']}" for c in errs.get("by_class")
                      or []) or "None"]),
        ("Infrastructure", [
            f"${_e(infra.get('yesterday_usd'))} yesterday · cycle "
            f"projected ${_e(infra.get('cycle_projected_usd'))} · owner "
            f"{_e(infra.get('owner_share_7d_pct'))}% of the last 7 days"]),
        ("Free trial spend", [
            f"${_e(trial.get('month_usd'))} this month · "
            f"{_e(trial.get('used_pct'))}% of ${_e(trial.get('budget_usd'))}"
            + ("" if trial.get("live") else " · not live yet")]),
        ("Podcast factory (7 days)", [
            f"{_e(fac.get('shorts'))} shorts · "
            f"{_e(fac.get('approval_renders_per_short'))} approval renders "
            f"and {_e(fac.get('full_renders_per_short'))} full renders per "
            f"short · ${_e(fac.get('usd_per_short'))} per short"]),
    ]
    subject = (f"Valmera daily · {funnel['day']} · MRR "
               f"${(s.get('committed_mrr') or {}).get('value')} · "
               f"{len(payers)} new payer{'s' if len(payers) != 1 else ''}")
    return subject, _shell(f"Yesterday at Valmera ({funnel['day']})",
                           sections,
                           "The daily digest, 08:00 Dubai time. Admin inbox "
                           "only.")


# ── The tick ─────────────────────────────────────────────────────────────

def _connect(read_only):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from database_config import preferred_database_url
    opts = "-c statement_timeout=90000 -c lock_timeout=2000"
    if read_only:
        opts += " -c default_transaction_read_only=on"
    conn = psycopg2.connect(preferred_database_url(),
                            cursor_factory=RealDictCursor, connect_timeout=10,
                            options=opts)
    conn.autocommit = True
    return conn


def _count_key(now):
    return f"cockpit_alert_count:{now:%Y-%m-%d}"


def run_tick(now=None, send=_send, rconn=None, wconn=None):
    """One scan: claim new alerts and send them in one email; send the
    digest when it is due. Returns what it did."""
    now = now or datetime.now(timezone.utc)
    own = rconn is None
    rconn = rconn or _connect(True)
    wconn = wconn or _connect(False)
    out = {"alerts": 0, "sent": False, "digest": False}
    try:
        with wconn.cursor() as w:
            w.execute("SELECT pg_try_advisory_lock(%s) AS got",
                      (TICK_LOCK_ID,))
            if not w.fetchone()["got"]:
                return {"skipped": "another worker is scanning"}
        try:
            with rconn.cursor() as r, wconn.cursor() as w:
                mark = _kv(r, "cockpit_alert_watermark") or {}
                since = (datetime.fromisoformat(mark["at"])
                         if isinstance(mark, dict) and mark.get("at")
                         else now - FIRST_LOOKBACK)
                items = event_alerts(r, since - timedelta(minutes=2), now)
                board = None
                hourly = _kv(r, "cockpit_alert_hourly") or {}
                if not isinstance(hourly, dict) or hourly.get("hour") != \
                        now.strftime("%Y%m%d%H"):
                    from admin_metrics import cockpit
                    board = cockpit.compute(r, now)
                    items += daily_alerts(board, now)
                    _put(w, "cockpit_alert_hourly",
                         {"hour": now.strftime("%Y%m%d%H")})
                claimed = [i for i in items
                           if claim(w, "cockpit_alert:" + i[0][:180])]
                out["alerts"] = len(claimed)
                count = _kv(r, _count_key(now)) or {"n": 0}
                if claimed and int(count.get("n", 0)) < \
                        MAX_ALERT_EMAILS_PER_DAY:
                    subject, body = alert_email(claimed)
                    if send(subject, body):
                        out["sent"] = True
                        _put(w, _count_key(now),
                             {"n": int(count.get("n", 0)) + 1})
                    else:
                        for i in claimed:
                            release(w, "cockpit_alert:" + i[0][:180])
                _put(w, "cockpit_alert_watermark", {"at": now.isoformat()})
                out["digest"] = maybe_digest(r, w, now, board, send)
        finally:
            with wconn.cursor() as w:
                w.execute("SELECT pg_advisory_unlock(%s)", (TICK_LOCK_ID,))
    finally:
        if own:
            for c in (rconn, wconn):
                try:
                    c.close()
                except Exception:
                    pass
    return out


def digest_due(now):
    local = now.astimezone(ranges.TZ)
    return local.hour >= DIGEST_HOUR_LOCAL


def maybe_digest(r, w, now, board, send):
    if not digest_due(now):
        return False
    key = f"cockpit_digest:{ranges.local_today(now).isoformat()}"
    if not claim(w, key):
        return False
    try:
        if board is None:
            from admin_metrics import cockpit
            board = cockpit.compute(r, now)
        subject, body = digest_email(board, funnel_yesterday(r, now), now)
        if send(subject, body):
            return True
    except Exception as e:
        print(f"⚠️ [cockpit] digest failed: {type(e).__name__}: {e}",
              flush=True)
    release(w, key)
    return False


def preview(rcur, now=None):
    """What the next tick would contain (no claims, no email): for tests and
    the operator."""
    now = now or datetime.now(timezone.utc)
    from admin_metrics import cockpit
    board = cockpit.compute(rcur, now)
    items = event_alerts(rcur, now - timedelta(days=1), now) + \
        daily_alerts(board, now)
    subject, _ = digest_email(board, funnel_yesterday(rcur, now), now)
    return {"alerts": [{"key": k, "section": s, "line": l}
                       for k, s, l in items], "digest_subject": subject}


_scheduler = None
_lock = threading.Lock()


def start_scheduler(app):
    """Every 5 minutes in each worker; the advisory lock picks one."""
    global _scheduler
    url = str(app.config.get("DATABASE_URL") or "")
    if not enabled() or not url or "stub" in url or \
            os.getenv("SKIP_DB_INIT") == "1":
        return
    if BackgroundScheduler is None:
        app.logger.warning("apscheduler missing: cockpit alerts not started")
        return
    with _lock:
        if _scheduler is not None:
            return

        def job():
            try:
                with app.app_context():
                    result = run_tick()
                if result.get("alerts") or result.get("digest"):
                    app.logger.info("cockpit alerts: %s", result)
            except Exception as e:                      # never kill the thread
                app.logger.error("cockpit alert tick failed: %s", e)

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(job, "interval", minutes=TICK_MINUTES,
                      id="cockpit_alerts", replace_existing=True,
                      next_run_time=datetime.now(timezone.utc)
                      + timedelta(minutes=3))
        sched.start()
        _scheduler = sched
        app.logger.info("cockpit alert scheduler started")
