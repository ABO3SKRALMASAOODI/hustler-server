"""Needs your attention: a prioritised list of things the owner can act on.

Customers only. Each item is a plain sentence that links to where it can be
fixed (a project's jobs, a customer, the revenue page).
"""
from admin_metrics import defs, health, money, projects

TYPES = ("failed_job", "stuck_job", "upload_failed", "payment_failing",
         "billing_mismatch", "message_no_edit")

# What the customer got back instead of an edit, in words (the same names
# the Health page uses: "Concierge reply", "Subscription notice").
REPLY_WORDS = {"subscription_required": "the subscription notice",
               "concierge": "a concierge reply", "canned": "a canned reply",
               "index_ready": "the video-ready message",
               "tray_submitted": "the uploads-received note"}


def reply_detail(kind):
    if not kind:
        return "No editing work followed within 15 minutes."
    words = REPLY_WORDS.get(kind) or f"a {kind.replace('_', ' ')} reply"
    return f"They got {words} instead of an edit."


TYPE_LABELS = {"failed_job": "Failed", "stuck_job": "Stuck",
               "upload_failed": "Upload refused",
               "payment_failing": "Payment failing",
               "billing_mismatch": "Billing problem",
               "message_no_edit": "Message didn't start an edit"}
SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2}


def _ref(r):
    return {"id": r["user_id"], "email": r["email"], "plan": r.get("plan"),
            "status": r.get("status") or "free"}


def _minutes(seconds):
    return max(1, int(round((seconds or 0) / 60)))


def failed_jobs(cur):
    cur.execute(f"""
        SELECT vj.id, vj.type, vj.project_id, vj.updated_at, vj.error,
               (SELECT o.duration_s FROM assets o WHERE o.project_id = vj.project_id
                   AND o.kind = 'original' ORDER BY o.id LIMIT 1) AS video_s,
               u.id AS user_id, u.email, COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE vj.updated_at > NOW() - INTERVAL '7 days'
           AND {defs.customer('u')} AND {projects.UNSUPERSEDED_FAILED}
         ORDER BY vj.updated_at DESC LIMIT 200""")
    out = []
    for r in cur.fetchall():
        label = defs.JOB_FAILED_LABELS.get(r["type"], "Failed")
        title = label
        if r["video_s"]:
            title += f" for a {_minutes(r['video_s'])}-min video"
        out.append({
            "id": f"failed_job:{r['id']}", "type": "failed_job",
            "type_label": label,
            "severity": "critical" if r["type"] == "final" else "serious",
            "title": title,
            "detail": (health.plain_error(r["error"], 200)),
            "customer": _ref(r), "project_id": r["project_id"],
            "occurred_at": defs.iso(r["updated_at"]),
            "href": f"/admin/projects/{r['project_id']}?tab=jobs"})
    return out


def stuck_jobs(cur):
    cur.execute(f"""
        SELECT vj.id, vj.type, vj.state, vj.project_id,
               COALESCE(vj.heartbeat_at, vj.created_at) AS since,
               EXTRACT(EPOCH FROM (NOW() - COALESCE(vj.heartbeat_at,
                                                    vj.created_at))) AS age_s,
               u.id AS user_id, u.email, COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE {projects.STUCK} AND {defs.customer('u')}
         ORDER BY since LIMIT 100""")
    return [{
        "id": f"stuck_job:{r['id']}", "type": "stuck_job",
        "type_label": TYPE_LABELS["stuck_job"], "severity": "serious",
        "title": (f"{defs.JOB_LABELS.get(r['type'], r['type'])} "
                  f"{'waiting' if r['state'] == 'queued' else 'running'} for "
                  f"{_minutes(r['age_s'])} min"),
        "detail": None, "customer": _ref(r), "project_id": r["project_id"],
        "occurred_at": defs.iso(r["since"]),
        "href": f"/admin/projects/{r['project_id']}?tab=jobs"}
        for r in cur.fetchall()]


def failed_uploads(cur):
    cur.execute(f"""
        SELECT ce.id, ce.created_at, ce.project_id, ce.detail->>'reason' AS reason,
               u.id AS user_id, u.email, COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('upload_rejected', 'upload_failed')
           AND ce.created_at > NOW() - INTERVAL '7 days'
           AND {defs.customer('u')}
           AND NOT EXISTS (
               SELECT 1 FROM client_events landed
                WHERE landed.user_id = ce.user_id AND landed.kind = 'upload_landed'
                  AND landed.created_at > ce.created_at
                  AND landed.created_at < ce.created_at + INTERVAL '24 hours')
         ORDER BY ce.created_at DESC LIMIT 100""")
    out = []
    for r in cur.fetchall():
        key = health.reason_key(r["reason"])
        out.append({
            "id": f"upload_failed:{r['id']}", "type": "upload_failed",
            "type_label": TYPE_LABELS["upload_failed"], "severity": "warning",
            "title": f"Upload didn't land: {health.REASON_LABELS[key].lower()}",
            "detail": "No successful upload in the 24 hours after.",
            "customer": _ref(r), "project_id": r["project_id"],
            "occurred_at": defs.iso(r["created_at"]),
            "href": f"/admin/customers/{r['user_id']}"})
    return out


def payment_failing(cur):
    cur.execute(f"""
        SELECT u.id AS user_id, u.email, COALESCE(u.billing_plan, u.plan) AS plan,
               u.billing_period, u.payment_failed_at, u.payment_failed_reason,
               'payment_failing' AS status
          FROM users u
         WHERE {defs.failing('u')} AND {defs.customer('u')}
         ORDER BY u.payment_failed_at DESC NULLS LAST LIMIT 100""")
    from billing import decline_message
    out = []
    for r in cur.fetchall():
        out.append({
            "id": f"payment_failing:{r['user_id']}", "type": "payment_failing",
            "type_label": TYPE_LABELS["payment_failing"], "severity": "critical",
            "title": f"Payment failing for {money.plan_label(r['plan'])}",
            "detail": (decline_message(r["payment_failed_reason"])
                       if r["payment_failed_reason"] else
                       "Paddle is retrying the card.")[:200],
            "customer": _ref(r), "project_id": None,
            "occurred_at": defs.iso(r["payment_failed_at"]),
            "href": f"/admin/customers/{r['user_id']}"})
    return out


def billing_mismatches(cur):
    rows, _ = money.billing_problems(cur)
    out = []
    for r in rows:
        title, ours, paddle = money.PROBLEM_TEXT.get(
            r["problem"], (r["problem"] or "Billing problem", "", ""))
        out.append({
            "id": f"billing_mismatch:{r['id']}", "type": "billing_mismatch",
            "type_label": TYPE_LABELS["billing_mismatch"], "severity": "serious",
            "title": title,
            "detail": f"Our records: {ours}. Paddle: {paddle}.",
            "customer": {"id": r["id"], "email": r["email"],
                         "plan": r["plan"],
                         "status": r.get("status") or "free"},
            "project_id": None,
            "occurred_at": defs.iso(r["billing_synced_at"]),
            "href": f"/admin/customers/{r['id']}"})
    return out


def messages_no_edit(cur):
    from admin_metrics import ranges
    now = ranges.utc_now()
    from datetime import timedelta
    period = ranges.Period(key="custom", label="Last 7 days",
                           start=now - timedelta(days=7), end=now)
    m = health.messages_without_edit(cur, period, recent_limit=100)
    out = []
    for r in m["rows"][:100]:
        paying = bool(r["paying"])
        out.append({
            "id": f"message_no_edit:{r['id']}", "type": "message_no_edit",
            "type_label": TYPE_LABELS["message_no_edit"],
            "severity": "critical" if paying else "warning",
            "title": ("A paying customer's message didn't start an edit"
                      if paying else "A free customer's message didn't start "
                      "an edit"),
            "detail": reply_detail(r.get("reply_kind")),
            "customer": {"id": r["user_id"], "email": r["email"],
                         "plan": r["plan"],
                         "status": r.get("status") or
                         ("paying" if paying else "free")},
            "project_id": r["project_id"],
            "occurred_at": defs.iso(r["created_at"]),
            "href": f"/admin/projects/{r['project_id']}?tab=conversation"})
    return out


SOURCES = {"failed_job": failed_jobs, "stuck_job": stuck_jobs,
           "upload_failed": failed_uploads, "payment_failing": payment_failing,
           "billing_mismatch": billing_mismatches,
           "message_no_edit": messages_no_edit}


def attention(cur, limit=50, only_type=None, errors=None):
    items = []
    counts = {t: 0 for t in TYPES}
    for t in TYPES:
        if only_type and t != only_type:
            continue
        try:
            found = SOURCES[t](cur)
        except Exception as e:  # one broken source never hides the others
            if errors is not None:
                errors.append({"section": f"attention.{t}",
                               "message": "Couldn't load this list."})
            continue
        counts[t] = len(found)
        items.extend(found)
    items.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]],
                              -_ts(i["occurred_at"])))
    return {"counts": counts, "total": len(items), "items": items[:limit]}


def _ts(iso):
    from datetime import datetime
    if not iso:
        return 0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0
