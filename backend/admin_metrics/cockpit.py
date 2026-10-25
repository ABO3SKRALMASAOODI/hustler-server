"""The Health board: every paying or about-to-pay account on one screen.

Strategy 1025 §4 (with the red team's corrections C2.9): instead of clicking
each subscriber, the owner reads one strip of numbers, one table with a row
per relevant account, flags with a rule and a next action, the podcast
factory's render use, R2 by population and the trial spend.

Read-only, from existing tables. Computed in the background and shared through
app_kv (cache.shared_report) like the other heavy reports, so the page answers
within 3 s. The alerts (cockpit_alerts) and the daily digest read the same
compute(), so the email and the page can never disagree.
"""
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

import billing
from admin_metrics import db, defs, infra_cost, subscriber_record, subscriptions

# Internal accounts (strategy 1025: owner, agents, tests, the Cloudflare
# account owner, and two ambiguous accounts with no activity). The customer()
# population already drops the admin and ADMIN_EXCLUDE_EMAILS; these ids are
# excluded on top so the board matches the analysis whatever the env holds.
INTERNAL_IDS = tuple(sorted({22, 28, 29, 45, 46, 49, 53, 60, 61, 62, 68, 108,
                             398, 1058, 2330} | {
    int(x) for x in os.getenv("COCKPIT_EXTRA_INTERNAL_IDS", "").split(",")
    if x.strip().isdigit()}))
OWNER_ID = infra_cost.OWNER_ID
# Plans the Studio editor refuses: whatever the gate itself says
# (routes.video.MCP_ONLY_PLANS; D1 moves Plus in or out of it).
_MCP_ONLY_FALLBACK = ("mcp_connect", "plus_2026")


def mcp_only_plans():
    try:
        from routes.video import MCP_ONLY_PLANS
        return tuple(MCP_ONLY_PLANS)
    except Exception:
        return _MCP_ONLY_FALLBACK
# First cut / trial events (track B). Read here, never written.
KEEP_KINDS = ("free_result_keep_clicked", "first_cut_keep_clicked",
              "trial_keep_clicked")
DELIVERED_KINDS = ("free_result_delivered", "first_cut_delivered",
                   "trial_result_delivered")
RESULT_KINDS = ("download_triggered", "mcp_export_delivered")
EVENT_KINDS = RESULT_KINDS + KEEP_KINDS + DELIVERED_KINDS + (
    "upload_started", "upload_landed", "checkout_stage", "mcp_only_refused",
    "trial_gate_shown", "first_prompt_sent", "first_prompt_blocked",
    "cancel_feedback")
# D2 (approved 25 Oct 2026): the free trial runs on Claude models through the
# Anthropic API, funded by about $200 of credit, with a global monthly cap.
TRIAL_BUDGET_USD = float(os.getenv("TRIAL_BUDGET_USD", "200"))
TRIAL_SINCE = os.getenv("TRIAL_SINCE", "2026-10-25")

# ── Service errors a customer saw, by class ──────────────────────────────
ERROR_TEXT = re.compile(
    r"executor|service-side|processing limits|reach the editing model|"
    r"went wrong on my end|ffmpeg|container|recovery passes|could not analy[sz]e|"
    r"couldn't analy[sz]e|couldn't render|black-frame|processing service|"
    r"rendering service|editing server|interrupted (mid-request )?on our side|"
    r"on our side", re.I)
ERROR_CLASSES = (
    ("executor_missing", re.compile(
        r"no remote executor|modal or cloudflare is required|"
        r"processing service could not analy", re.I)),
    ("shard_busy", re.compile(r"shard is busy|stayed busy|capacity|"
                              r"no container instance", re.I)),
    ("version_skew", re.compile(r"source version does not match|readiness "
                                r"mismatch|different code|version", re.I)),
    ("deploy_kill", re.compile(r"deploy in progress|new release|"
                               r"interrupted|shutting down|lost the executor",
                               re.I)),
    ("oom", re.compile(r"out-of-memory|exit -9|processing limits", re.I)),
    ("model_unreachable", re.compile(r"editing model|credits remaining|"
                                     r"insufficient_quota|http 429", re.I)),
)
ERROR_CLASS_LABELS = {
    "executor_missing": "Executor missing", "shard_busy": "Shard busy",
    "version_skew": "Version skew", "deploy_kill": "Deploy kill",
    "oom": "Out of memory", "model_unreachable": "Model unreachable",
    "other": "Other"}

# ── Flags: rule, action, severity (§4.3) ─────────────────────────────────
RED, AMBER, WATCH = "red", "amber", "watch"
FLAGS = {
    "CHARGED_NO_USE": (RED, "Renewal charged in the last 35 days, with 0 "
                       "edits since it or in the 14 days before it",
                       "Refund or pause"),
    "CHARGE_SOON_NO_USE": (RED, "Next charge within 5 days (by date) and 0 "
                           "edits in 30 days",
                           "Pause or refund before the charge"),
    "PAST_DUE_DORMANT": (RED, "Past due and inactive 14+ days",
                         "Cancel the retry"),
    "ERROR_LOOP": (RED, "2+ service errors in 24 h, or one job type failing "
                   "3 times in 7 days", "Fix, then credit the account"),
    "RENEWAL_OFF_STILL_ACTIVE": (AMBER, "Renewal off and active in the last "
                                 "7 days", "Save: they are still using it"),
    "PAID_NO_RESULT_24H": (AMBER, "Paid more than 24 h ago and 0 finished "
                           "results", "Finish their first video"),
    "UNFINISHED_CHARGED": (AMBER, "Credits charged on a partial or blocked "
                           "request in the last 14 days",
                           "Refund the credits"),
    "WRONG_PLAN": (AMBER, "MCP-only plan with access and no result, plus an "
                   "mcp_only refusal or no MCP connection within 1 h of "
                   "paying for it", "Switch plans"),
    "STUCK_SETUP": (AMBER, "MCP connected with no upload after 24 h, or an "
                    "upload that never landed", "Hand-hold the setup"),
    "REPEAT_ASK": (AMBER, "The same instruction 3+ times in one session",
                   "Agent miss: review the chat"),
    "QUICK_CANCEL": (AMBER, "Renewal off within 60 min of paying",
                     "Read the chat"),
    "LONG_SOURCE": (WATCH, "Longest source over 30 min", "Watch it"),
}
SEVERITY_RANK = {RED: 0, AMBER: 1, WATCH: 2}
HEALTH_RANK = {"red": 0, "amber": 1, "green": 2, "ended": 3}

_STOP = set("""the and for you can this that with please make add put more
less from into your have just like want also then all are but not out its
it's video clip clips one two use edit edits change again now there here what
how why when need some them they very really still okay alright could would
should little bit try""".split())
# What a request is about, across the languages customers write in. Three
# asks about the same thing inside 24 hours means the agent missed it
# (1799 asked for quieter audio 8 times in English and Czech on 8-9 Sep).
INTENTS = {
    "audio": re.compile(r"\b(audio|music|volume|sound|loud\w*|quiet\w*|mute"
                        r"|hudb\w*|zvuk\w*|zesla\w*|ztlum\w*|lautst\w*"
                        r"|musique|m[uú]sica|volumen)\b", re.I),
    "silence": re.compile(r"(silen\w*|\bpauses?\b|\bums?\b|\buhs?\b|"
                          r"filler|dead space)", re.I),
    "captions": re.compile(r"(caption|subtitle|titulk\w*|legend\w*|"
                           r"untertitel)", re.I),
    "aspect": re.compile(r"(vertical|horizontal|9:16|16:9|portrait|"
                         r"landscape|aspect)", re.I),
}
REPEAT_WINDOW = timedelta(hours=24)


def _utc(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


def _iso(v):
    return defs.iso(_utc(v)) if v else None


def _minutes(a, b):
    return round((b - a).total_seconds() / 60.0, 1)


def error_class(text):
    for key, rx in ERROR_CLASSES:
        if rx.search(text or ""):
            return key
    return "other"


def _stem(word):
    for suffix in ("ing", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def _tokens(text):
    words = re.findall(r"[a-z\u00c0-\u017f][a-z\u00c0-\u017f']{2,}",
                       (text or "").lower())
    return {_stem(w) for w in words if w not in _STOP}


def _max_in_window(times):
    times = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(times):
        while t - times[j] > REPEAT_WINDOW:
            j += 1
        best = max(best, i - j + 1)
    return best


def repeat_count(messages):
    """Largest number of times one instruction was given in one session
    within 24 hours. `messages` are (created_at, text) for one session.

    Two asks are the same when they are about the same thing (INTENTS) or
    when their content words overlap (stemmed Jaccard >= 0.5, 2+ shared)."""
    best = 0
    for rx in INTENTS.values():
        best = max(best, _max_in_window(
            [t for t, text in messages if rx.search(text or "")]))
    groups = []
    for t, text in messages:
        tok = _tokens(text)
        if len(tok) < 2:
            continue
        for g in groups:
            shared = len(tok & g["tok"])
            if shared >= 2 and shared / len(tok | g["tok"]) >= 0.5:
                g["times"].append(t)
                break
        else:
            groups.append({"tok": tok, "times": [t]})
    for g in groups:
        best = max(best, _max_in_window(g["times"]))
    return best


# ── Raw facts (a dozen bounded queries over ~50 accounts) ────────────────

def _accounts(cur, now):
    cur.execute(f"""
        SELECT u.id, u.email, u.created_at, u.last_seen_at, u.plan,
               u.billing_plan, u.billing_period, u.billing_status,
               u.is_subscribed, u.subscription_id, u.payment_failed_at,
               u.payment_failed_reason, u.payment_failed_count
          FROM users u
         WHERE {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))
           AND (EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id
                          AND {defs.success('p')})
                OR u.billing_status IN ('past_due', 'paused')
                OR EXISTS (SELECT 1 FROM client_events ce
                            WHERE ce.user_id = u.id
                              AND ce.kind = 'checkout_stage'
                              AND ce.created_at > %(d30)s)
                OR EXISTS (SELECT 1 FROM client_events ce
                            WHERE ce.user_id = u.id
                              AND ce.kind = ANY(%(keep)s)
                              AND ce.created_at > %(d7)s))
         ORDER BY u.id""",
                {"internal": list(INTERNAL_IDS), "keep": list(KEEP_KINDS),
                 "d30": now - timedelta(days=30),
                 "d7": now - timedelta(days=7)})
    return [dict(r) for r in cur.fetchall()]


def _by_user(rows, key="user_id"):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def _facts(cur, ids, sids):
    f = {}
    cur.execute("""SELECT user_id, subscription_id, plan, status,
                          amount_cents, currency, error_code,
                          COALESCE(occurred_at, created_at) AS at
                     FROM payments WHERE user_id = ANY(%s)
                    ORDER BY COALESCE(occurred_at, created_at)""", (ids,))
    f["payments"] = _by_user([dict(r) for r in cur.fetchall()])
    sids = set(sids) | {p["subscription_id"] for rows in f["payments"].values()
                        for p in rows if p.get("subscription_id")}
    cur.execute("""SELECT id, user_id, project_id, type, state, created_at,
                          updated_at, LEFT(error, 300) AS error,
                          COALESCE(payload->>'root_agent_job_id',
                                   result->>'root_agent_job_id',
                                   id::text) AS root,
                          result->>'outcome' AS outcome,
                          COALESCE(NULLIF(result->>'credits_charged', '')
                                   ::numeric, 0) AS credits,
                          CASE WHEN type = 'mcp_tool'
                                AND payload->>'tool' = 'download_url'
                               THEN result->'download_receipt'->>'render_type'
                          END AS receipt
                     FROM video_jobs
                    WHERE user_id = ANY(%s)
                      AND type IN ('agent_turn', 'mcp_tool', 'final', 'index',
                                   'preview', 'preview_check', 'shorts_plan',
                                   'filmstrip')""", (ids,))
    f["jobs"] = _by_user([dict(r) for r in cur.fetchall()])
    cur.execute("""SELECT user_id, project_id, kind, created_at,
                          CASE WHEN kind IN ('trial_gate_shown',
                                             'first_prompt_blocked',
                                             'cancel_feedback',
                                             'mcp_only_refused')
                               THEN detail END AS detail
                     FROM client_events
                    WHERE user_id = ANY(%s) AND kind = ANY(%s)""",
                (ids, list(EVENT_KINDS)))
    f["events"] = _by_user([dict(r) for r in cur.fetchall()])
    cur.execute("""SELECT p.user_id, p.id AS project_id, m.session_id, m.role,
                          LEFT(m.content, 400) AS content, m.created_at,
                          m.meta->>'kind' AS kind, m.meta->>'error' AS error,
                          NULLIF(m.meta->>'job', '') AS job
                     FROM projects p
                     JOIN chat_messages m ON m.session_id = p.chat_session_id
                    WHERE p.user_id = ANY(%s)
                    ORDER BY m.id""", (ids,))
    f["messages"] = _by_user([dict(r) for r in cur.fetchall()])
    cur.execute("""SELECT g.user_id, g.created_at, g.last_used_at, g.calls,
                          g.revoked_at, c.client_name
                     FROM mcp_oauth_grants g
                     LEFT JOIN mcp_oauth_clients c USING (client_id)
                    WHERE g.user_id = ANY(%s)""", (ids,))
    f["grants"] = _by_user([dict(r) for r in cur.fetchall()])
    cur.execute("""SELECT p.user_id, p.id, p.title, p.created_at,
                          (SELECT max(a.duration_s) FROM assets a
                            WHERE a.project_id = p.id AND a.kind = 'original')
                              AS source_s
                     FROM projects p
                    WHERE p.user_id = ANY(%s) AND p.parent_project_id IS NULL
                    ORDER BY p.id""", (ids,))
    f["projects"] = _by_user([dict(r) for r in cur.fetchall()])
    f["snapshots"], f["history"] = {}, defaultdict(list)
    if sids and db.has_table(cur, "admin_subscription_snapshots"):
        cur.execute("SELECT * FROM admin_subscription_snapshots "
                    "WHERE subscription_id = ANY(%s)", (sorted(sids),))
        f["snapshots"] = {r["subscription_id"]: dict(r)
                          for r in cur.fetchall()}
    if sids and db.has_table(cur, "admin_subscription_history"):
        cur.execute("""SELECT subscription_id, provider_updated_at, status,
                              scheduled_action, next_billed_at, event_id
                         FROM admin_subscription_history
                        WHERE subscription_id = ANY(%s)
                        ORDER BY provider_updated_at""", (sorted(sids),))
        for r in cur.fetchall():
            f["history"][r["subscription_id"]].append(dict(r))
    return f


# ── One account's row ────────────────────────────────────────────────────

def _client(grants):
    names = sorted({(g.get("client_name") or "AI app") for g in grants
                    if not g.get("revoked_at")})
    return ("MCP via " + ", ".join(names)) if names else "Studio"


def _price(plan, period):
    prices = billing.PLAN_PRICES_USD.get(plan) or {}
    value = prices.get("yearly" if period == "yearly" else "monthly")
    return float(value) if value is not None else None


def _first_renewal_off(history, sid, paid_at):
    """When the customer first turned renewal off on this subscription.

    A scheduled cancel, or an immediate cancellation (a refund-style end).
    Paddle ending a subscription after its card retries ran out is not the
    customer turning renewal off."""
    previous = None
    for h in history.get(sid) or []:
        at = _utc(h["provider_updated_at"])
        if at >= paid_at - timedelta(minutes=1):
            if h.get("scheduled_action") == "cancel":
                return at
            if h.get("status") == "canceled" and (previous or {}).get(
                    "status") not in ("past_due", "paused"):
                return at
        previous = h
    return None


def build_row(acct, f, journey, now):
    uid = acct["id"]
    pays = f["payments"].get(uid, [])
    ok = [p for p in pays if p["status"] in ("completed", "paid")
          and (p["amount_cents"] or 0) > 0]
    refunds = [p for p in pays if (p["amount_cents"] or 0) < 0]
    charges = [_utc(p["at"]) for p in ok]
    first_paid = charges[0] if charges else None
    last_charge = charges[-1] if charges else None
    prev_charge = charges[-2] if len(charges) > 1 else None
    lifetime = sum(p["amount_cents"] for p in ok if p["currency"] == "USD")
    jobs = f["jobs"].get(uid, [])
    events = f["events"].get(uid, [])
    msgs = f["messages"].get(uid, [])
    grants = f["grants"].get(uid, [])
    projects = f["projects"].get(uid, [])
    ev = defaultdict(list)
    for e in events:
        ev[e["kind"]].append(e)
    edits = sorted(_utc(j["updated_at"]) for j in jobs
                   if j["type"] in ("agent_turn", "mcp_tool")
                   and j["state"] == "done")
    mcp_user = bool(grants)
    # A finished result: a Studio download, or a final export's link handed
    # to the AI app (MCP download_url receipts; before receipts existed, a
    # done final for an MCP account).
    receipts = [_utc(j["updated_at"]) for j in jobs
                if j.get("receipt") == "final_export" and j["state"] == "done"]
    if mcp_user and not receipts:
        receipts = [_utc(j["updated_at"]) for j in jobs
                    if j["type"] == "final" and j["state"] == "done"]
    results = sorted([_utc(e["created_at"]) for e in ev["download_triggered"]]
                     + receipts)
    delivered = sorted(_utc(e["created_at"]) for k in DELIVERED_KINDS
                       for e in ev[k])
    d7, d1 = now - timedelta(days=7), now - timedelta(days=1)

    # Subscription state (Paddle snapshot via the shared journey summary).
    sid = acct.get("subscription_id") or next(
        (p["subscription_id"] for p in reversed(ok) if p["subscription_id"]),
        None)
    snap = f["snapshots"].get(sid) or {}
    state = journey.get("renewal_state") or "none"
    plan = (acct.get("billing_plan") if acct.get("billing_plan") not in
            (None, "free") else None) or (ok[-1]["plan"] if ok else None) \
        or acct.get("plan")
    period = acct.get("billing_period") or "monthly"
    price = _price(plan, period)
    has_access = state in ("renews", "cancelled", "past_due", "trialing",
                           "pausing")
    next_charge = _utc(snap.get("next_billed_at")) if state == "renews" \
        else None
    access_end = _utc(journey.get("plan_ends_at")) if state == "cancelled" \
        else None
    # Paddle keeps a past-due subscription's next retry in next_billed_at.
    next_retry = _utc(snap.get("next_billed_at")) if state == "past_due" \
        else None
    first_sid = next((p["subscription_id"] for p in ok
                      if p["subscription_id"]), None)
    off_at = (_first_renewal_off(f["history"], first_sid, first_paid)
              if first_sid and first_paid else None)
    off_min = _minutes(first_paid, off_at) if off_at else None

    # Studio requests (one prompt through its chained segments).
    req = defaultdict(list)
    for j in jobs:
        if j["type"] == "agent_turn":
            req[j["root"]].append(j)
    requests = []
    for segs in req.values():
        segs.sort(key=lambda j: j["id"])
        start = min(_utc(j["created_at"]) for j in segs)
        end = max(_utc(j["updated_at"]) for j in segs)
        requests.append({"start": start, "outcome": segs[-1]["outcome"],
                         "wait_min": _minutes(start, end),
                         "credits": float(sum(j["credits"] or 0
                                              for j in segs))})
    req7 = [r for r in requests if r["start"] >= d7]
    fulfilled7 = sum(1 for r in req7 if r["outcome"] == "fulfilled")
    waits7 = sorted(r["wait_min"] for r in req7)
    unfinished = [r for r in requests
                  if r["outcome"] in ("partial", "blocked")
                  and r["start"] >= now - timedelta(days=14)
                  and r["credits"] > 0]
    unfinished_credits = round(sum(r["credits"] for r in unfinished), 1)

    # MCP use.
    mcp_jobs = [j for j in jobs if j["type"] == "mcp_tool"]
    mcp7 = sum(1 for j in mcp_jobs if _utc(j["created_at"]) >= d7)
    mcp_only = mcp_only_plans()
    mcp_paid = next((_utc(p["at"]) for p in ok
                     if p["plan"] in mcp_only), None)
    hour = timedelta(hours=1)
    mcp_first_hour = (sum(1 for j in mcp_jobs if mcp_paid - hour <=
                          _utc(j["created_at"]) <= mcp_paid + hour)
                      + sum(1 for g in grants if _utc(g["created_at"])
                            <= mcp_paid + hour)) if mcp_paid else 0
    refusals = len(ev["mcp_only_refused"])

    # Friction.
    errors = []
    job_err = {str(j["id"]): j.get("error") for j in jobs}
    for m in msgs:
        if m["role"] != "assistant":
            continue
        if m.get("error") in ("job_failed", "job_died") or (
                m.get("kind") is None and ERROR_TEXT.search(m["content"] or "")
                and not m["content"].startswith("The reviewed frames")):
            errors.append((_utc(m["created_at"]),
                           error_class((job_err.get(m.get("job") or "") or "")
                                       + " " + (m["content"] or ""))))
    errors.sort()
    err7 = [e for e in errors if e[0] >= d7]
    err24 = [e for e in errors if e[0] >= d1]
    failed7 = defaultdict(int)
    for j in jobs:
        if j["state"] == "failed" and _utc(j["updated_at"]) >= d7:
            failed7[j["type"]] += 1
    canned = sum(1 for m in msgs if m["role"] == "assistant" and first_paid
                 and _utc(m["created_at"]) > first_paid
                 and m.get("kind") in ("concierge", "canned",
                                       "subscription_required",
                                       "subscription_plans"))
    sessions = defaultdict(list)
    for m in msgs:
        if m["role"] == "user":
            sessions[m["session_id"]].append((_utc(m["created_at"]),
                                              m["content"]))
    repeats = max((repeat_count(v) for v in sessions.values()), default=0)

    # Activity.
    seen = [_utc(acct.get("last_seen_at"))]
    seen += [_utc(m["created_at"]) for m in msgs[-1:]]
    seen += [_utc(max((j["created_at"] for j in jobs), default=None))]
    seen += [_utc(g.get("last_used_at")) for g in grants]
    last_active = max((s for s in seen if s), default=None)

    # Making.
    first_project = projects[0] if projects else None
    first_prompt = next((m["content"] for m in msgs if m["role"] == "user"),
                        None)
    blocked = next((((e.get("detail") or {}).get("prompt")
                     or (e.get("detail") or {}).get("text"))
                    for e in ev["first_prompt_blocked"] + ev["trial_gate_shown"]
                    if (e.get("detail") or {}).get("prompt")
                    or (e.get("detail") or {}).get("text")), None)
    longest = max((float(p["source_s"] or 0) for p in projects), default=0.0)
    uploads_started = ev["upload_started"]
    landed = ev["upload_landed"]

    # Flags.
    flags = []
    if last_charge and prev_charge and last_charge >= now - timedelta(days=35) \
            and not any(e > last_charge - timedelta(days=14) for e in edits):
        flags.append("CHARGED_NO_USE")
    if next_charge and next_charge >= now and \
            (next_charge.date() - now.date()).days <= 5 and \
            not any(e >= now - timedelta(days=30) for e in edits):
        flags.append("CHARGE_SOON_NO_USE")
    if state == "past_due" and (not last_active or
                                last_active < now - timedelta(days=14)):
        flags.append("PAST_DUE_DORMANT")
    if len(err24) >= 2 or any(n >= 3 for n in failed7.values()):
        flags.append("ERROR_LOOP")
    if state == "cancelled" and last_active and last_active >= d7:
        flags.append("RENEWAL_OFF_STILL_ACTIVE")
    if first_paid and first_paid < d1 and not results:
        flags.append("PAID_NO_RESULT_24H")
    if unfinished_credits > 0:
        flags.append("UNFINISHED_CHARGED")
    if plan in mcp_only and mcp_paid and has_access and not results \
            and (refusals or mcp_first_hour == 0):
        flags.append("WRONG_PLAN")
    setup_stuck = (mcp_user and not landed and not results and any(
        _utc(g["created_at"]) < d1 for g in grants)) or (
        uploads_started and not landed)
    if has_access and setup_stuck:
        flags.append("STUCK_SETUP")
    if repeats >= 3:
        flags.append("REPEAT_ASK")
    if off_min is not None and off_min <= 60:
        flags.append("QUICK_CANCEL")
    if longest > 30 * 60:
        flags.append("LONG_SOURCE")
    flags.sort(key=lambda k: SEVERITY_RANK[FLAGS[k][0]])
    top = FLAGS[flags[0]][0] if flags else None
    if top == RED:
        health = "red"
    elif not has_access:
        health = "ended"
    elif top == AMBER:
        health = "amber"
    else:
        health = "green"
    due = next_charge or access_end or next_retry
    actions = []
    for k in flags:
        a = FLAGS[k][2]
        if k == "RENEWAL_OFF_STILL_ACTIVE" and access_end:
            a += f" ({max(0, (access_end - now).days)} days left)"
        if k == "CHARGE_SOON_NO_USE" and next_charge:
            a += f" ({next_charge.strftime('%d %b')})"
        if k == "PAST_DUE_DORMANT" and next_retry:
            a += f" (next retry {next_retry.strftime('%d %b')})"
        if a not in actions:
            actions.append(a)
    return {
        "id": uid, "email": acct["email"], "client": _client(grants),
        "health": health, "flags": flags,
        "next_action": " · ".join(actions[:2]) if actions else None,
        "project_id": (first_project or {}).get("id"),
        "latest_project_id": projects[-1]["id"] if projects else None,
        "plan": plan, "price_usd": price, "period": period,
        "first_paid_at": _iso(first_paid), "charges": len(ok),
        "lifetime_usd": defs.usd(lifetime),
        "refunded_usd": defs.usd(-sum(p["amount_cents"] for p in refunds)),
        "renewal": {"state": state,
                    "off_minutes_after_paying": off_min,
                    "next_charge_at": _iso(next_charge),
                    "next_charge_usd": price if next_charge else None,
                    "access_ends_at": _iso(access_end),
                    "days_left": ((due - now).days if due else None)},
        "billing": {"past_due_since": _iso(acct.get("payment_failed_at"))
                    if state == "past_due" else None,
                    "retries": int(acct.get("payment_failed_count") or 0)
                    if state == "past_due" else 0,
                    "next_retry_at": _iso(next_retry),
                    "card_error": next((p["error_code"] for p in
                                        reversed(pays) if p["error_code"]),
                                       None)},
        "result_before_paying": bool(first_paid and (
            any(r < first_paid for r in results)
            or any(d < first_paid for d in delivered))) if first_paid
        else None,
        "results": {"total": len(results),
                    "last_7d": sum(1 for r in results if r >= d7),
                    "hours_to_first": (round((min(r for r in results
                                                  if r >= first_paid)
                                              - first_paid).total_seconds()
                                             / 3600.0, 1)
                                       if first_paid and any(
                                           r >= first_paid for r in results)
                                       else None)},
        "requests": {"last_7d": len(req7),
                     "fulfilled_pct": defs.pct(fulfilled7, len(req7), 0),
                     "p90_wait_min": (waits7[min(len(waits7) - 1,
                                                 int(len(waits7) * 0.9))]
                                      if waits7 else None),
                     "unfinished_credits_30d": unfinished_credits},
        "mcp": {"calls_7d": mcp7,
                "exports_7d": sum(1 for r in receipts if r >= d7),
                "mcp_only_refusals": refusals,
                "calls_first_hour": mcp_first_hour},
        "friction": {"service_errors_7d": len(err7),
                     "service_errors_total": len(errors),
                     "error_classes_7d": sorted({c for _, c in err7}),
                     "failed_jobs_7d": sum(failed7.values()),
                     "canned_after_paying": canned,
                     "repeat_ask": repeats},
        "last_active_at": _iso(last_active),
        "making": {"title": (first_project or {}).get("title"),
                   "first_prompt": " ".join(((blocked or first_prompt)
                                             or "").split())[:80] or None,
                   "prompt_was_blocked": bool(blocked),
                   "longest_source_min": round(longest / 60.0, 1)
                   if longest else None},
        "_sort_due": (due - now).total_seconds() if due else 1e12,
        "_first_paid": first_paid, "_off_at": off_at, "_results": results,
        "_edits": edits, "_errors": errors, "_last_charge": last_charge,
    }


# ── The top strip ────────────────────────────────────────────────────────

def _committed_at(history, plans, at):
    """Committed MRR (renewal on, list price) as Paddle stood at `at`."""
    total = 0.0
    subs = set()
    for sid, rows in history.items():
        state = None
        for h in rows:
            if _utc(h["provider_updated_at"]) <= at:
                state = h
        if state and subscriber_record.renews(state) \
                and sid in plans:
            total += billing.monthly_value(*plans[sid])
            subs.add(sid)
    return round(total, 2), subs


def strip(cur, rows, f, now, infra):
    d7 = now - timedelta(days=7)
    split = subscriptions.paddle_split(cur)
    plans, owners, renewing = {}, {}, set()
    if db.has_table(cur, "admin_subscription_snapshots"):
        cur.execute(subscriptions.SPLIT_SQL.format(
            success=defs.success("p")))
        for r in cur.fetchall():
            r = dict(r)
            plans[r["subscription_id"]] = subscriptions.plan_of(r)
            owners[r["subscription_id"]] = r.get("user_id")
            if subscriptions.snapshot_state(r)[0] == "renews":
                renewing.add(r["subscription_id"])
    by_id = {r["id"]: r for r in rows}

    def engaged(subs, at):
        total = 0.0
        for sid in subs:
            row = by_id.get(owners.get(sid))
            if row and any(at - timedelta(days=14) <= r <= at
                           for r in row["_results"]):
                total += billing.monthly_value(*plans[sid])
        return round(total, 2)

    hist = f["history_all"]
    committed_now, subs_now = _committed_at(hist, plans, now)
    committed_then, subs_then = _committed_at(hist, plans, d7)
    if split.get("available"):
        # Now: Paddle's stored snapshots (what the Revenue split shows).
        committed_now = split.get("renewing_mrr_usd", committed_now)
        subs_now = renewing

    def payers_between(a, b):
        return [r for r in rows if r["_first_paid"] and a <= r["_first_paid"]
                < b]

    def offs_between(a, b):
        return [r for r in rows if r["_off_at"] and a <= r["_off_at"] < b]

    new7, newp = payers_between(d7, now), payers_between(d7 - timedelta(7), d7)
    off7, offp = offs_between(d7, now), offs_between(d7 - timedelta(7), d7)
    off_minutes = [r["renewal"]["off_minutes_after_paying"] for r in off7
                   if r["renewal"]["off_minutes_after_paying"] is not None]
    entitled = [r for r in rows if r["renewal"]["state"] in
                ("renews", "cancelled", "past_due")]
    no_result = [r for r in entitled if r["_last_charge"] and not any(
        x >= r["_last_charge"] for x in r["_results"])]
    payers = [r for r in rows if r["_first_paid"]]
    seen_cohort = signup_results(cur, now)
    errs = customer_errors(cur, now)
    first_cut = trial_spend(cur, now)
    return {
        "committed_mrr": {"value": committed_now, "previous": committed_then,
                          "subscriptions": len(subs_now)},
        "engaged_committed_mrr": {"value": engaged(subs_now, now),
                                  "previous": engaged(subs_then, d7)},
        "paddle_active_mrr": {"value": split.get("paddle_mrr_usd"),
                              "subscriptions": split.get("paddle_active")},
        "new_payers_7d": {"value": len(new7), "previous": len(newp),
                          "ids": [r["id"] for r in new7]},
        "renewal_offs_7d": {"value": len(off7), "previous": len(offp),
                            "median_minutes_after_paying":
                                round(median(off_minutes), 0)
                                if off_minutes else None,
                            "ids": [r["id"] for r in off7]},
        "signups_saw_result": seen_cohort,
        "paid_no_result": {"value": len(no_result), "of": len(entitled),
                           "lifetime": sum(1 for r in payers
                                           if not r["_results"]),
                           "lifetime_of": len(payers),
                           "ids": [r["id"] for r in no_result]},
        "service_errors_24h": errs,
        "infra": {"yesterday_usd": infra.get("yesterday_usd"),
                  "previous": _week_ago(infra),
                  "cycle_projected_usd": (infra.get("cycle") or {})
                  .get("projected_usd"),
                  "cycle_to_date_usd": (infra.get("cycle") or {})
                  .get("to_date_usd"),
                  "owner_share_7d_pct": infra.get("last_7d_owner_share_pct")},
        "first_cut": first_cut,
    }


def _week_ago(infra):
    daily = infra.get("daily") or []
    return daily[-7]["usd"] if len(daily) >= 7 else None


def signup_results(cur, now):
    """Signups (last 7 d vs the 7 d before) who saw a finished result of
    their own within 15 minutes: a First-cut/trial delivery, a done final or
    a download."""
    cur.execute(f"""
        WITH s AS (
          SELECT u.id, u.created_at AT TIME ZONE 'UTC' AS at
            FROM users u
           WHERE {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))
             AND u.created_at >= %(start)s)
        SELECT s.id, s.at,
               (SELECT min(x.t) FROM (
                  SELECT ce.created_at AS t FROM client_events ce
                   WHERE ce.user_id = s.id AND ce.kind = ANY(%(kinds)s)
                  UNION ALL
                  SELECT vj.updated_at FROM video_jobs vj
                   WHERE vj.user_id = s.id AND vj.type = 'final'
                     AND vj.state = 'done') x) AS first_result
          FROM s""",
                {"internal": list(INTERNAL_IDS),
                 "start": (now - timedelta(days=14)).replace(tzinfo=None),
                 "kinds": list(DELIVERED_KINDS + RESULT_KINDS)})
    cur_n = prev_n = cur_saw = prev_saw = 0
    mins = []
    for r in cur.fetchall():
        at = _utc(r["at"])
        fr = _utc(r["first_result"])
        quick = bool(fr and fr - at <= timedelta(minutes=15))
        if at >= now - timedelta(days=7):
            cur_n += 1
            cur_saw += quick
            if quick:
                mins.append(_minutes(at, fr))
        else:
            prev_n += 1
            prev_saw += quick
    return {"value_pct": defs.pct(cur_saw, cur_n), "saw": cur_saw,
            "signups": cur_n, "previous_pct": defs.pct(prev_saw, prev_n),
            "median_minutes": round(median(mins), 1) if mins else None}


def customer_errors(cur, now):
    """Service errors shown to customers in the last 24 h (and the same 24 h
    a week ago), by class."""
    cur.execute(f"""
        SELECT m.created_at, LEFT(m.content, 300) AS content,
               m.meta->>'error' AS error, m.meta->>'kind' AS kind,
               LEFT(j.error, 300) AS job_error, p.user_id,
               {defs.paying('u')} AS paying
          FROM chat_messages m
          JOIN projects p ON p.chat_session_id = m.session_id
          JOIN users u ON u.id = p.user_id
          LEFT JOIN video_jobs j ON j.id::text = m.meta->>'job'
         WHERE m.role = 'assistant'
           AND (m.created_at >= %(a)s OR (m.created_at >= %(b)s
                                          AND m.created_at < %(c)s))
           AND {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))""",
                {"a": (now - timedelta(days=1)).replace(tzinfo=None),
                 "b": (now - timedelta(days=8)).replace(tzinfo=None),
                 "c": (now - timedelta(days=7)).replace(tzinfo=None),
                 "internal": list(INTERNAL_IDS)})
    by = defaultdict(int)
    prev = 0
    paying_users = set()
    for r in cur.fetchall():
        is_err = r["error"] in ("job_failed", "job_died") or (
            r["kind"] is None and ERROR_TEXT.search(r["content"] or ""))
        if not is_err:
            continue
        if _utc(r["created_at"]) >= now - timedelta(days=1):
            by[error_class((r["job_error"] or "") + " " +
                           (r["content"] or ""))] += 1
            if r["paying"]:
                paying_users.add(r["user_id"])
        else:
            prev += 1
    return {"value": sum(by.values()), "previous": prev,
            "by_class": [{"key": k, "label": ERROR_CLASS_LABELS[k], "n": n}
                         for k, n in sorted(by.items(), key=lambda x: -x[1])],
            "paying_customers": sorted(paying_users)}


def trial_spend(cur, now):
    """The free trial / First cut (D2): Claude-model spend by never-paid
    accounts since TRIAL_SINCE, against the Anthropic credit. Concierge
    replies to free users run on other models and are not trial spend."""
    from routes.admin_video import _row_cost
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute(f"""
        SELECT COALESCE(SUM({_row_cost('lc')}) FILTER (
                   WHERE lc.created_at >= %(day)s), 0) AS today,
               COALESCE(SUM({_row_cost('lc')}) FILTER (
                   WHERE lc.created_at >= %(month)s), 0) AS month,
               COALESCE(SUM({_row_cost('lc')}), 0) AS total,
               COUNT(DISTINCT p.user_id) FILTER (
                   WHERE lc.created_at >= %(month)s) AS users_month
          FROM llm_calls lc JOIN projects p ON p.id = lc.project_id
          JOIN users u ON u.id = p.user_id
         WHERE lc.created_at >= %(since)s
           AND LOWER(COALESCE(lc.model, '')) LIKE '%%claude%%'
           AND {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))
           AND NOT EXISTS (SELECT 1 FROM payments sp
                            WHERE sp.user_id = u.id AND {defs.success('sp')}
                              AND COALESCE(sp.occurred_at, sp.created_at)
                                  <= lc.created_at)
        """, {"day": day, "month": month, "since": TRIAL_SINCE,
              "internal": list(INTERNAL_IDS)})
    r = cur.fetchone() or {}
    cur.execute("SELECT key, value FROM app_kv WHERE key = ANY(%s)",
                (["free_result", "trial_budget", "llm_balance",
                  "fallback_canary", "llm_credit"],))
    kv = {}
    for row in cur.fetchall():
        try:
            import json
            kv[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            kv[row["key"]] = row["value"]
    flag = kv.get("trial_budget") or kv.get("free_result") or {}
    on = bool(isinstance(flag, dict) and flag.get("on"))
    total = float(r.get("total") or 0)
    return {"live": on, "today_usd": round(float(r.get("today") or 0), 2),
            "month_usd": round(float(r.get("month") or 0), 2),
            "budget_usd": TRIAL_BUDGET_USD,
            "used_pct": defs.pct(total, TRIAL_BUDGET_USD),
            "monthly_cap_usd": (flag.get("monthly_usd_cap")
                                if isinstance(flag, dict) else None),
            "users_month": int(r.get("users_month") or 0),
            "llm_balance": kv.get("llm_balance"),
            "llm_credit": llm_credit(cur, kv.get("llm_credit")),
            "fallback_canary": kv.get("fallback_canary")}


# Which wallet a model's calls are paid from.
PROVIDER_MODELS = {"anthropic": "claude%%", "openai": "gpt-%%",
                   "xai": "grok-%%", "deepseek": "deepseek%%"}


def llm_credit(cur, loaded):
    """R5: spend against the credit loaded on each provider.

    app_kv 'llm_credit' = {"anthropic": {"loaded_usd": 200,
    "loaded_at": "2026-10-25T00:00:00Z"}, ...}, set by the owner when a
    wallet is topped up. Spend is every llm_calls row on that provider's
    models since then (the owner's too: it is the same wallet)."""
    from routes.admin_video import _row_cost
    if not isinstance(loaded, dict):
        return []
    out = []
    for provider, entry in sorted(loaded.items()):
        pattern = PROVIDER_MODELS.get(provider)
        if not pattern or not isinstance(entry, dict):
            continue
        try:
            amount = float(entry.get("loaded_usd"))
            since = _utc(entry.get("loaded_at"))
        except (TypeError, ValueError):
            continue
        if since is None or amount <= 0:
            continue
        cur.execute(f"""SELECT COALESCE(SUM({_row_cost('lc')}), 0) AS usd
                          FROM llm_calls lc
                         WHERE lc.created_at >= %s
                           AND LOWER(COALESCE(lc.model, '')) LIKE '{pattern}'
                        """, (since,))
        spent = float((cur.fetchone() or {}).get("usd") or 0)
        out.append({"provider": provider, "loaded_usd": amount,
                    "loaded_at": defs.iso(since), "spent_usd": round(spent, 2),
                    "remaining_usd": round(amount - spent, 2),
                    "used_pct": defs.pct(spent, amount)})
    return out


# ── The owner's podcast factory ──────────────────────────────────────────

def factory(cur, now, infra):
    cur.execute("""
        SELECT COUNT(DISTINCT project_id) FILTER (
                   WHERE type = 'final' AND state = 'done') AS shorts,
               COUNT(*) FILTER (WHERE type = 'preview' AND state = 'done'
                   AND COALESCE(payload->>'quality', '') <> 'draft')
                   AS approval_previews,
               COUNT(*) FILTER (WHERE type = 'preview' AND state = 'done'
                   AND payload->>'quality' = 'draft') AS drafts,
               COUNT(*) FILTER (WHERE type = 'final' AND state = 'done')
                   AS finals,
               COUNT(*) FILTER (WHERE type = 'preview_check'
                   AND state = 'done') AS proofs,
               COUNT(*) FILTER (WHERE type = 'mcp_tool') AS mcp_calls
          FROM video_jobs
         WHERE user_id = %s AND created_at >= %s""",
                (OWNER_ID, now - timedelta(days=7)))
    r = dict(cur.fetchone() or {})
    shorts = int(r.get("shorts") or 0)

    def per(n):
        return round(float(n or 0) / shorts, 2) if shorts else None
    owner_usd = None
    daily = infra.get("daily") or []
    if daily:
        owner_usd = round(sum(d.get("owner_usd") or 0 for d in daily[-7:]), 2)
    return {"days": 7, "shorts": shorts,
            "approval_renders_per_short": per(r.get("approval_previews")),
            "full_renders_per_short": per((r.get("approval_previews") or 0)
                                          + (r.get("finals") or 0)),
            "finals_per_short": per(r.get("finals")),
            "drafts_per_short": per(r.get("drafts")),
            "proofs_per_short": per(r.get("proofs")),
            "mcp_calls_per_short": per(r.get("mcp_calls")),
            "owner_infra_usd": owner_usd,
            "usd_per_short": (round(owner_usd / shorts, 3)
                              if owner_usd is not None and shorts else None)}


# ── Everything ───────────────────────────────────────────────────────────

def compute(cur, now=None):
    now = _utc(now) or datetime.now(timezone.utc)
    accounts = _accounts(cur, now)
    ids = [a["id"] for a in accounts]
    journeys = subscriptions.for_customers(cur, accounts) if accounts else {}
    sids = {a["subscription_id"] for a in accounts if a.get("subscription_id")}
    f = _facts(cur, ids, sids)
    # History for every subscription (committed MRR 7 days ago).
    f["history_all"] = defaultdict(list)
    if db.has_table(cur, "admin_subscription_history"):
        cur.execute("""SELECT subscription_id, provider_updated_at, status,
                              scheduled_action, next_billed_at
                         FROM admin_subscription_history
                        ORDER BY provider_updated_at""")
        for r in cur.fetchall():
            f["history_all"][r["subscription_id"]].append(dict(r))
    rows = [build_row(a, f, journeys.get(a["id"]) or {}, now)
            for a in accounts]
    rows.sort(key=lambda r: (HEALTH_RANK[r["health"]],
                             min((SEVERITY_RANK[FLAGS[k][0]]
                                  for k in r["flags"]), default=9),
                             r["_sort_due"], -r["id"]))
    infra = infra_cost.report(cur, now)
    top = strip(cur, rows, f, now, infra)
    counts = {k: [r["id"] for r in rows if k in r["flags"]] for k in FLAGS}
    for r in rows:
        for k in [k for k in r if k.startswith("_")]:
            r.pop(k)
    return {
        "as_of": defs.iso(now),
        "strip": top,
        "flags": [{"key": k, "severity": FLAGS[k][0], "rule": FLAGS[k][1],
                   "action": FLAGS[k][2], "ids": counts[k],
                   "count": len(counts[k])} for k in FLAGS],
        "rows": rows,
        "health_counts": {h: sum(1 for r in rows if r["health"] == h)
                          for h in HEALTH_RANK},
        "factory": factory(cur, now, infra),
        "r2": infra.get("r2"),
        "infra": {k: v for k, v in infra.items() if k != "r2"},
        "internal_ids_excluded": list(INTERNAL_IDS),
    }
