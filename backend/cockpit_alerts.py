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
  * alerts are only what needs the owner NOW (29 Oct 2026): a P0 ticket the
    Fixer did not pick up within 30 minutes, or a model provider out of
    credit; never payments, renewals, refunds or spend;
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


# ── What needs the owner NOW ─────────────────────────────────────────────
# Owner, 29 Oct 2026: alerts only for things that need attention now, "a
# customer blocked and not yet auto-handled". Every customer failure is
# filed as a ticket within a minute (ops_loop.intake) and the Fixer starts on
# P0s from the Mac; the owner is emailed only when that did not happen, or
# when only the owner can unblock customers (a provider out of credit).
# Payments, renewals, refunds, infrastructure spend and trial budgets are
# never alerts (spend is in the digest; billing advice is nowhere).

def _customer_sql(alias="u"):
    return (f"{defs.customer(alias)} AND NOT ({alias}.id = ANY(%(internal)s))")


def event_alerts(rcur, since, now):
    """(dedupe key, section, line html) for what needs the owner now."""
    out = []
    try:
        from ops_loop import intake
        for t in intake.unhandled(rcur, now):
            out.append((f"unhandled:{t['id']}:"
                        f"{t['opened_at']:%Y%m%dT%H%M}",
                        "Customer blocked, not handled yet",
                        f"<b>P0 #{t['id']}</b> {_e(t['title'])} · "
                        f"{_e(t['users'])} customer"
                        f"{'s' if t['users'] != 1 else ''} · open "
                        f"{t['waiting_min']} min and the Fixer has not "
                        f"started (the Mac may be asleep or the P0 watch "
                        f"off) · <a href=\"{ADMIN_URL}?tab=tickets&ticket="
                        f"{t['id']}\" style=\"color:#f87171\">ticket</a>"))
    except Exception as e:
        print(f"⚠️ [cockpit] unhandled-P0 check failed: "
              f"{type(e).__name__}: {e}", flush=True)
    # Only the owner can pay a provider: say so once a day while it lasts.
    p = {"since": since, "internal": INTERNAL}
    rcur.execute("""
        SELECT count(*) AS n, count(DISTINCT j.user_id) AS users,
               max(LEFT(j.error, 200)) AS error
          FROM video_jobs j
         WHERE j.state = 'failed' AND j.updated_at >= %(since)s
           AND NOT (j.user_id = ANY(%(internal)s))
           AND (j.error ILIKE '%%insufficient_quota%%'
                OR j.error ILIKE '%%no credits remaining%%'
                OR j.error ILIKE '%%credit balance is too low%%')""", p)
    r = rcur.fetchone() or {}
    if r.get("n"):
        out.append((f"llmquota:{now:%Y%m%d}",
                    "A model provider is out of credit (only you can fix it)",
                    f"{r['n']} customer job(s) of {r['users']} customer(s) "
                    f"failed: {_e(r['error'])}"))
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
        # A newline after each item keeps the plain-text part one per line.
        items = "".join(f'<li style="margin:0 0 6px">{line}</li>\n'
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
            f'#f87171">Open the admin</a></p>{foot}</div></div>')


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
# One clear email a day, answering four questions in plain words (owner,
# 29 Oct 2026); everything else was cut (money, funnel, urgent accounts,
# trial spend, podcast factory: the admin still has them).

def day_window(now):
    """Yesterday in the admin timezone: (day, start, end)."""
    day = ranges.local_today(now) - timedelta(days=1)
    return day, ranges.local_midnight(day), ranges.local_midnight(
        day + timedelta(days=1))


EVIDENCE_LINK_S = 6 * 24 * 3600     # R2 presigned URLs live at most 7 days


def _evidence_link(key):
    """A long-lived picture link for the email (the admin inbox may open it
    days later)."""
    try:
        import storage
        if not key or not storage.is_configured():
            return None
        return storage.presign_get(key, expires=EVIDENCE_LINK_S)
    except Exception:
        return None


def digest_facts(rcur, now, presign=_evidence_link):
    """Everything the digest says, for yesterday (admin timezone). Never
    raises: the digest goes out with whatever parts could be read."""
    from ops_loop import views
    day, start, end = day_window(now)
    facts = views.awareness(rcur, start, end, now)
    facts["day"] = day.isoformat()
    try:
        proof = views.digest_facts(rcur, start, end, presign=presign) or {}
        facts["thumbnails"] = proof.get("thumbnails") or []
    except Exception as e:
        facts["thumbnails"] = []
        facts.setdefault("errors", {})["thumbnails"] = \
            f"{type(e).__name__}: {e}"[:200]
    return facts


def _pct(v):
    return f"{v:.0f}%" if isinstance(v, (int, float)) else "—"


def _link(url, text):
    return (f'<a href="{html.escape(url, quote=True)}" style="color:#f87171">'
            f'{_e(text)}</a>')


def _ticket_line(t):
    state = {"fixed": "fixed", "fixing": "being fixed", "open": "open",
             "wont_fix": "won't fix"}.get(t.get("status"), t.get("status"))
    commits = " ".join(_link(c["url"], c["sha"]) for c in t.get("commits")
                       or [])
    return (f"{_link(t['url'], '#' + str(t['id']))} "
            f"<b>[{_e(t['severity'])}]</b> {_e(t['title'])} · "
            f"{_e(t['failures'])} failure{'s' if t['failures'] != 1 else ''}"
            f", {_e(t['users'])} customer{'s' if t['users'] != 1 else ''} · "
            f"{_e(state)}" + (f" ({commits})" if commits else ""))


def digest_sections(f):
    """[(title, [line html])] for the four questions, in plain words."""
    res, res7 = f.get("results"), f.get("results_7d")
    fail = f.get("failures") or {}
    ppl, ppl7 = f.get("people") or {}, f.get("people_7d") or {}
    infra = f.get("infra") or {}
    # 1. Results.
    if res is None:
        good = ["The outcome loop is not live (migration 041)."]
    elif not res["judged"]:
        good = ["No sessions were judged yesterday"
                + (f" · last 7 days {_pct(res7['rate_pct'])} good of "
                   f"{res7['judged']}" if res7 and res7["judged"] else "")
                + "."]
    else:
        good = [f"<b>{_pct(res['rate_pct'])} good</b>: {res['good']} of "
                f"{res['judged']} sessions judged delivered what was asked"
                + (f" · {res['poor']} poor" if res["poor"] else "")
                + (f" · {res['not_delivered']} not delivered"
                   if res["not_delivered"] else "")
                + (f" · average {res['avg_score']}/10"
                   if res.get("avg_score") is not None else ""),
                f"Last 7 days: {_pct((res7 or {}).get('rate_pct'))} good of "
                f"{(res7 or {}).get('judged', 0)} sessions."]
    thumbs = f.get("thumbnails") or []
    if thumbs:
        good.append("".join(
            f'<a href="{ADMIN_URL}?tab=sessions" style="display:inline-block;'
            f'margin:0 8px 8px 0;text-decoration:none;vertical-align:top;'
            f'width:140px"><img src="{html.escape(p["url"], quote=True)}" '
            f'width="140" alt="{_e(p.get("caption"))}" style="display:block;'
            f'width:140px;border-radius:8px;border:1px solid #26262e"><span '
            f'style="display:block;color:#9a9aa5;font-size:11px;'
            f'line-height:1.35;margin-top:4px">{_e(p["verdict"])} · '
            f'{_e(p["score"])}/10 · {_e((p.get("caption") or "")[:90])}'
            f'</span></a>' for p in thumbs[:4]))
    # 2. Failures and what was fixed automatically.
    if not fail:
        failed = ["Failures could not be read."]
    else:
        failed = [f"<b>{fail['total']} failure"
                  f"{'s' if fail['total'] != 1 else ''}</b> hit "
                  f"{fail['customers']} customer"
                  f"{'s' if fail['customers'] != 1 else ''}"
                  + (", each filed as a ticket automatically:"
                     if fail["total"] else " · nothing failed.")]
        if fail.get("intake_stale"):
            failed.insert(0, "<b>The automatic failure intake is not "
                          "running</b> (last pass "
                          f"{_e(fail.get('intake_last_at') or 'never')}): "
                          "failures may be missing below.")
        failed += [_ticket_line(t) for t in fail["by_ticket"][:8]]
        if len(fail["by_ticket"]) > 8:
            failed.append(f"… and {len(fail['by_ticket']) - 8} more causes "
                          f"({_link(ADMIN_URL + '?tab=tickets', 'Tickets')})")
        for t in fail["fixed"]:
            commits = " ".join(_link(c["url"], c["sha"])
                               for c in t.get("commits") or [])
            failed.append(f"Fixed automatically: "
                          f"{_link(t['url'], '#' + str(t['id']))} "
                          f"{_e(t['title'])} · {commits} · guarded by "
                          f"{_e(t.get('regression_check'))}")
        for u in fail.get("unhandled") or []:
            failed.append(f"<b>Waiting for a person</b>: "
                          f"{_link(u['url'], '#' + str(u['id']))} "
                          f"{_e(u['title'])} ({u['waiting_min']} min)")
        failed.append(f"Tickets now: {fail['open']} open, {fail['fixing']} "
                      f"being fixed"
                      + (f" ({fail['p0_active']} P0)" if fail["p0_active"]
                         else "") + ".")
    # 3. People.
    people = [f"<b>{ppl.get('signups', '—')} signups</b> · "
              f"{ppl.get('active', '—')} active customers (sent a request, "
              f"uploaded, ran an edit or an MCP call)",
              f"Last 7 days: {ppl7.get('signups', '—')} signups · "
              f"{ppl7.get('active', '—')} active customers."]
    # 4. Infrastructure.
    q = infra.get("queue") or {}
    boxes = infra.get("boxes")
    costs = infra.get("costs") or {}
    box_lines = []
    for b in boxes or []:
        load = (f"load {b['load1']}/{b['cpus']} CPUs" if b.get("load1")
                is not None and b.get("cpus") else "load —")
        box_lines.append(
            f"Box {_e(b['name'])}: {load} · memory "
            f"{_pct(b.get('mem_used_pct'))} · disk "
            f"{_pct(b.get('disk_used_pct'))} · "
            f"{_e(b.get('running_jobs'))} jobs running"
            + (" · <b>not reporting</b> (last " + _e(b['reported_at']) + ")"
               if b.get("stale") else ""))
    if not box_lines:
        box_lines = ["Render box: not reporting health yet."]
    wait = q.get("p90_wait_s_24h")
    infra_lines = box_lines + [
        f"Queue now: {q.get('queued', '—')} waiting, {q.get('running', '—')}"
        f" running"
        + (f", oldest waiting {q['oldest_queued_min']} min"
           if q.get("oldest_queued_min") else "")
        + f" · last 24 h: {q.get('jobs_24h', '—')} jobs, "
        f"{q.get('failed_24h', '—')} failed"
        + (f", 90% started within {wait:.0f} s" if wait is not None else ""),
        f"Costs: ${_e(costs.get('yesterday_usd'))} yesterday · "
        f"${_e(costs.get('cycle_to_date_usd'))} this cycle, projected "
        f"${_e(costs.get('cycle_projected_usd'))} (Cloudflare executors + "
        f"R2, from our own ledger)"]
    return [("Did customers get good results?", good),
            ("What failed, and what was fixed automatically", failed),
            ("Signups and active customers", people),
            ("Infrastructure", infra_lines)]


def digest_email(facts):
    res = facts.get("results") or {}
    fail = facts.get("failures") or {}
    ppl = facts.get("people") or {}
    rate = res.get("rate_pct")
    subject = (f"Valmera daily · {facts['day']} · "
               + (f"{rate:.0f}% good of {res['judged']} judged · "
                  if rate is not None else "no sessions judged · ")
               + f"{fail.get('total', 0)} failure"
               f"{'s' if fail.get('total', 0) != 1 else ''} · "
               f"{ppl.get('signups', 0)} signup"
               f"{'s' if ppl.get('signups', 0) != 1 else ''}")
    return subject, _shell(f"Yesterday at Valmera ({facts['day']})",
                           digest_sections(facts),
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
                out["digest"] = maybe_digest(r, w, now, send)
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


def maybe_digest(r, w, now, send):
    if not digest_due(now):
        return False
    key = f"cockpit_digest:{ranges.local_today(now).isoformat()}"
    if not claim(w, key):
        return False
    try:
        subject, body = digest_email(digest_facts(r, now))
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
    items = event_alerts(rcur, now - timedelta(days=1), now)
    facts = digest_facts(rcur, now, presign=None)
    subject, body = digest_email(facts)
    return {"alerts": [{"key": k, "section": s, "line": l}
                       for k, s, l in items], "digest_subject": subject,
            "digest_html": body}


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
