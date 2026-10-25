"""Settled edit sessions of real users, and everything a reviewer needs to
judge one the way the owner does by hand.

A SESSION is one burst of work in one project family (a project, or a
Make-shorts parent with its shorts): the chat messages and jobs whose times
lie within GAP of each other. It is REVIEWABLE when the product did edit
work in it (an agent turn, an MCP tool call, a shorts plan, an export) or
failed the user in it (a failed job, an error reply). A request answered only
by the subscribe wall is not an edit session (the wall is deliberate: owner
decision, DECISIONS.md) and is counted, not reviewed. It is SETTLED when the
family has no queued or running job and nothing happened for SETTLE.

The key `p<project>-<epoch of the first event>` is stable once settled. The
fingerprint covers the session's messages and job states, ratings, and what
the user did next (exports, downloads, checkout, cancellation); a change puts
a reviewed session back in pending.

Only reads. Every query is bounded by time and by project family.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from admin_metrics import defs
from ops_loop import EXCLUDED_USER_IDS

GAP = timedelta(hours=3)
SETTLE = timedelta(minutes=30)
PAD = timedelta(days=7)          # how far back a session's start is looked for
WORK_JOBS = ("agent_turn", "mcp_tool", "shorts_plan")
RENDER_JOBS = ("preview", "final")
NEXT_KINDS = ("export_clicked", "download_triggered", "mcp_export_delivered",
              "export_blocked", "checkout_stage", "cancel_feedback",
              "subscription_cards_plan_selected",
              "subscription_cards_dismissed", "player_error", "upload_failed",
              "upload_rejected")
SOURCE_KINDS = ("original", "proxy", "video_clip", "image_ref", "music")
ADMIN_URL = "https://valmera.io/admin"
TIMELINE_CAP = 600


def _aware(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _naive(v):
    return _aware(v).astimezone(timezone.utc).replace(tzinfo=None)


def _iso(v):
    v = _aware(v)
    return v.astimezone(timezone.utc).isoformat(timespec="seconds") if v else None


def session_key(root_project_id, start):
    return f"p{int(root_project_id)}-{int(_aware(start).timestamp())}"


def evidence_slug(key):
    """The R2 folder for a session's pictures: ops/evidence/<slug>/."""
    return "".join(c for c in str(key).lower()
                   if c.isalnum() or c in "-_")[:80]


# ── Loading ──────────────────────────────────────────────────────────────

def _params(since, extra=None):
    p = {"since": _aware(since), "since_naive": _naive(since),
         "internal": list(EXCLUDED_USER_IDS)}
    p.update(extra or {})
    return p


def candidate_roots(cur, since, project_ids=None):
    """Root project ids (a short's parent, else the project) with real
    users' work or requests since `since`."""
    if project_ids:
        cur.execute("""SELECT COALESCE(parent_project_id, id) AS root
                         FROM projects WHERE id = ANY(%s)""",
                    (list(project_ids),))
        return sorted({r["root"] for r in cur.fetchall()})
    cur.execute(f"""
        SELECT DISTINCT COALESCE(p.parent_project_id, p.id) AS root
          FROM video_jobs j
          JOIN projects p ON p.id = j.project_id
          JOIN users u ON u.id = j.user_id
         WHERE j.updated_at >= %(since)s
           AND j.type IN ('agent_turn', 'mcp_tool', 'shorts_plan', 'final',
                          'index', 'preview')
           AND {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))
        UNION
        SELECT DISTINCT COALESCE(p.parent_project_id, p.id)
          FROM chat_messages m
          JOIN projects p ON p.chat_session_id = m.session_id
          JOIN users u ON u.id = p.user_id
         WHERE m.created_at >= %(since_naive)s AND m.role = 'user'
           AND {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))""",
                _params(since))
    return sorted({r["root"] for r in cur.fetchall()})


def load(cur, roots, since, now):
    """Everything the windows and the context need, for these roots."""
    lo = _aware(since) - PAD
    out = {"projects": {}, "users": {}, "messages": [], "jobs": [],
           "events": [], "assets": [], "live": set(), "payments": [],
           "subscription": []}
    if not roots:
        return out
    cur.execute("""
        SELECT p.id, p.user_id, p.kind, LEFT(p.title, 160) AS title,
               p.parent_project_id, p.chat_session_id, p.created_at,
               COALESCE(p.parent_project_id, p.id) AS root
          FROM projects p
         WHERE p.id = ANY(%(roots)s) OR p.parent_project_id = ANY(%(roots)s)""",
                {"roots": list(roots)})
    for r in cur.fetchall():
        out["projects"][r["id"]] = dict(r)
    pids = list(out["projects"])
    uids = sorted({p["user_id"] for p in out["projects"].values()})
    sessions = [p["chat_session_id"] for p in out["projects"].values()
                if p["chat_session_id"]]
    cur.execute(f"""
        SELECT u.id, u.created_at, u.plan, u.is_subscribed, u.billing_status,
               u.billing_plan, u.trial_status, u.last_seen_at,
               ({defs.paying('u')}) AS paying,
               ({defs.customer('u')} AND NOT (u.id = ANY(%(internal)s)))
                   AS real_customer
          FROM users u WHERE u.id = ANY(%(uids)s)""",
                {"uids": uids, "internal": list(EXCLUDED_USER_IDS)})
    for r in cur.fetchall():
        out["users"][r["id"]] = dict(r)
    if sessions:
        cur.execute("""
            SELECT m.id, m.session_id, m.role, m.created_at,
                   CASE WHEN m.role = 'activity' THEN LEFT(m.content, 700)
                        ELSE LEFT(m.content, 6000) END AS content,
                   m.meta->>'kind' AS kind, m.meta->>'error' AS error,
                   m.meta->>'feedback' AS feedback,
                   m.meta->>'tool' AS tool, m.meta->>'source' AS source,
                   m.meta->'tool_outcome'->>'status' AS tool_status,
                   LEFT((m.meta->'args')::text, 400) AS args,
                   m.meta->>'job' AS job,
                   m.meta->>'stage' AS stage,
                   COALESCE(m.meta->>'subscribe_gate', m.meta->>'subscribe_required')
                       AS subscribe_gate,
                   m.meta->>'request_message_id' AS request_message_id,
                   LEFT(m.meta->'blocked_prompt'->>'text', 6000) AS blocked_prompt
              FROM chat_messages m
             WHERE m.session_id = ANY(%(sessions)s) AND m.created_at >= %(lo)s
             ORDER BY m.id""", {"sessions": sessions, "lo": _naive(lo)})
        by_session = {p["chat_session_id"]: p["id"]
                      for p in out["projects"].values()}
        for r in cur.fetchall():
            d = dict(r)
            d["project_id"] = by_session.get(d["session_id"])
            d["created_at"] = _aware(d["created_at"])
            out["messages"].append(d)
    cur.execute("""
        SELECT j.id, j.project_id, j.user_id, j.type, j.state,
               LEFT(j.error, 600) AS error, j.attempts, j.created_at,
               j.updated_at,
               j.payload->>'message_id' AS message_id,
               j.payload->>'source' AS source,
               j.payload->>'quality' AS quality,
               j.payload->>'tool' AS tool,
               LEFT((j.payload->'args')::text, 500) AS args,
               j.payload->>'root_agent_job_id' AS root_agent_job_id,
               j.result->>'render_asset_id' AS render_asset_id,
               j.result->>'sheet_key' AS sheet_key,
               j.result->>'verify_sheet_key' AS verify_sheet_key,
               j.result->>'caption_sheet_key' AS caption_sheet_key,
               COALESCE(j.result->>'quality', j.result->>'variant')
                   AS result_quality,
               j.result->>'duration_s' AS duration_s,
               j.result->>'outcome' AS outcome,
               j.result->>'status' AS status,
               LEFT((j.result->'honesty')::text, 300) AS honesty,
               (j.result->>'is_error') IN ('true', '1') AS is_error,
               j.result->'tool_outcome'->>'status' AS tool_status,
               LEFT(j.result->>'text', 700) AS text,
               j.result->>'edl_version' AS edl_version,
               j.result->>'superseded_by' AS superseded_by,
               j.result->>'clips' AS clips,
               j.result->>'credits_charged' AS credits_charged
          FROM video_jobs j
         WHERE j.project_id = ANY(%(pids)s) AND j.updated_at >= %(lo)s
         ORDER BY j.id""", {"pids": pids, "lo": lo})
    out["jobs"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT project_id FROM video_jobs
                    WHERE project_id = ANY(%(pids)s)
                      AND state IN ('queued', 'running')""", {"pids": pids})
    out["live"] = {out["projects"][r["project_id"]]["root"]
                   for r in cur.fetchall()}
    cur.execute("""
        SELECT ce.id, ce.user_id, ce.project_id, ce.kind, ce.created_at,
               LEFT(ce.detail::text, 400) AS detail
          FROM client_events ce
         WHERE ce.user_id = ANY(%(uids)s) AND ce.created_at >= %(lo)s
           AND ce.kind = ANY(%(kinds)s)
         ORDER BY ce.id""", {"uids": uids, "lo": lo, "kinds": list(NEXT_KINDS)})
    out["events"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT a.id, a.project_id, a.kind, a.storage_key, a.bytes,
               a.duration_s, a.width, a.height,
               COALESCE(a.meta->>'quality', a.meta->>'variant') AS quality,
               a.meta->>'variant' AS variant,
               a.meta->>'sheet_key' AS sheet_key,
               a.meta->>'edl_version' AS edl_version,
               LEFT(a.meta->>'filename', 160) AS filename,
               a.meta->>'r2_purged_at' AS purged_at, a.created_at
          FROM assets a
         WHERE a.project_id = ANY(%(pids)s)
           AND (a.kind = ANY(%(src)s)
                OR (a.kind = 'render' AND a.created_at >= %(lo)s))
         ORDER BY a.id""", {"pids": pids, "lo": lo, "src": list(SOURCE_KINDS)})
    out["assets"] = [dict(r) for r in cur.fetchall()]
    cur.execute(f"""
        SELECT p.user_id, p.amount_cents, p.currency, p.plan, p.status,
               COALESCE(p.occurred_at, p.created_at) AS at
          FROM payments p
         WHERE p.user_id = ANY(%(uids)s)
           AND COALESCE(p.occurred_at, p.created_at) >= %(lo_naive)s
           AND {defs.success('p')}""", {"uids": uids, "lo_naive": _naive(lo)})
    out["payments"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT m.user_id, h.provider_updated_at AS at, h.status,
               h.scheduled_action, h.event_type
          FROM admin_subscription_history h
          JOIN (SELECT DISTINCT subscription_id, user_id FROM payments
                 WHERE user_id = ANY(%(uids)s)
                   AND subscription_id IS NOT NULL) m
            ON m.subscription_id = h.subscription_id
         WHERE h.provider_updated_at >= %(lo)s
           AND (h.scheduled_action = 'cancel' OR h.status = 'canceled')
         ORDER BY h.provider_updated_at""", {"uids": uids, "lo": lo})
    out["subscription"] = [dict(r) for r in cur.fetchall()]
    return out


# ── Windows ──────────────────────────────────────────────────────────────

def _spans(data, root):
    fam = {pid for pid, p in data["projects"].items() if p["root"] == root}
    spans = []
    for m in data["messages"]:
        if m["project_id"] in fam:
            spans.append((m["created_at"], m["created_at"], "m", m))
    for j in data["jobs"]:
        if j["project_id"] in fam:
            spans.append((_aware(j["created_at"]), _aware(j["updated_at"]),
                          "j", j))
    spans.sort(key=lambda s: (s[0], s[1]))
    return spans


def windows(data, root, gap=GAP):
    """[(start, end, [messages], [jobs])] for one family, oldest first."""
    out = []
    for start, end, kind, row in _spans(data, root):
        if out and start - out[-1][1] < gap:
            w = out[-1]
            out[-1] = (w[0], max(w[1], end), w[2], w[3])
        else:
            out.append((start, end, [], []))
        (out[-1][2] if kind == "m" else out[-1][3]).append(row)
    return out


def _is_paywall(m):
    return m["role"] == "assistant" and (
        m.get("subscribe_gate") in ("true", "1", True)
        or m.get("kind") in ("subscription_plans", "subscription_required"))


def classify(messages, jobs, project_kind):
    types = {j["type"] for j in jobs}
    failed = [j for j in jobs if j["state"] == "failed"]
    error_replies = [m for m in messages if m["role"] == "assistant"
                     and m.get("error")]
    has_work = bool(types & set(WORK_JOBS)) or "final" in types
    reviewable = has_work or bool(failed) or bool(error_replies)
    if project_kind in ("shorts", "short") or "shorts_plan" in types:
        surface = "shorts"
    elif "mcp_tool" in types or any(m.get("source") == "mcp"
                                    for m in messages):
        surface = "mcp"
    else:
        surface = "studio"
    paywalled = any(_is_paywall(m) for m in messages)
    return {"reviewable": reviewable, "surface": surface,
            "paywalled": paywalled, "failed_jobs": len(failed),
            "error_replies": len(error_replies)}


class Session(dict):
    """A plain dict with the window's rows kept aside for context()."""


def discover(cur, since, now=None, settle=SETTLE, gap=GAP, project_ids=None,
             include_unsettled=False):
    """(sessions, stats). Sessions are reviewable windows that ended on or
    after `since` (settled ones only, unless include_unsettled)."""
    now = _aware(now) or datetime.now(timezone.utc)
    since = _aware(since)
    roots = candidate_roots(cur, since, project_ids)
    data = load(cur, roots, since, now)
    sessions, stats = [], {"roots": len(roots), "paywalled_only": 0,
                           "unsettled": 0, "reviewable": 0}
    for root in roots:
        proj = data["projects"].get(root)
        if not proj:
            continue
        user = data["users"].get(proj["user_id"]) or {}
        if not user.get("real_customer"):
            continue
        for start, end, msgs, jobs in windows(data, root, gap):
            if end < since:
                continue
            c = classify(msgs, jobs, proj["kind"])
            if not c["reviewable"]:
                if c["paywalled"]:
                    stats["paywalled_only"] += 1
                continue
            stats["reviewable"] += 1
            settled = root not in data["live"] and end <= now - settle
            if not settled:
                stats["unsettled"] += 1
                if not include_unsettled:
                    continue
            s = Session(key=session_key(root, start), surface=c["surface"],
                        project_id=root, user_id=proj["user_id"],
                        started_at=start, ended_at=end, settled=settled,
                        classification=c)
            s.rows = {"messages": msgs, "jobs": jobs}
            s.data = data
            sessions.append(s)
    _attach_next(sessions, data, now)
    for s in sessions:
        s["fingerprint"] = fingerprint(s)
    return sessions, stats


def _attach_next(sessions, data, now):
    """What the user did after each session: this project's events from its
    start until the project's next session (or now); account-wide events
    (no project, payments, cancellations) until the user's next session."""
    by_user, by_root = {}, {}
    for s in sessions:
        by_user.setdefault(s["user_id"], []).append(s)
        by_root.setdefault(s["project_id"], []).append(s)

    def following(items, s):
        later = [x["started_at"] for x in items
                 if x["started_at"] > s["started_at"]]
        return min(later) if later else None
    for s in sessions:
        uid = s["user_id"]
        nxt_root = following(by_root[s["project_id"]], s)
        nxt_user = following(by_user[uid], s)
        stop_root, stop_user = nxt_root or now, nxt_user or now
        fam = {pid for pid, p in data["projects"].items()
               if p["root"] == s["project_id"]}
        s.next_events = [
            e for e in data["events"] if e["user_id"] == uid
            and s["started_at"] <= _aware(e["created_at"]) < (
                stop_user if e["project_id"] is None else stop_root)
            and (e["project_id"] is None or e["project_id"] in fam)]
        s.next_payments = [p for p in data["payments"] if p["user_id"] == uid
                           and s["started_at"] <= _aware(p["at"]) < stop_user]
        s.next_subscription = [h for h in data["subscription"]
                               if h["user_id"] == uid
                               and s["started_at"] <= _aware(h["at"])
                               < stop_user]
        s.next_session_at = nxt_user


def fingerprint(s):
    rows = s.rows
    parts = {
        "m": sorted((m["id"], m["role"], m.get("feedback") or "",
                     m.get("error") or "") for m in rows["messages"]),
        "j": sorted((j["id"], j["state"]) for j in rows["jobs"]),
        "e": sorted((e["id"], e["kind"]) for e in getattr(s, "next_events", [])),
        "pay": len(getattr(s, "next_payments", [])),
        "sub": len(getattr(s, "next_subscription", [])),
    }
    raw = json.dumps(parts, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def reviewed(cur, keys):
    """{session_key: {id, fingerprint, verdict, score, ...}} (empty when the
    table does not exist yet)."""
    if not keys:
        return {}
    cur.execute("SELECT to_regclass('public.session_reviews') AS t")
    if not (cur.fetchone() or {}).get("t"):
        return {}
    cur.execute("""SELECT id, session_key, fingerprint, verdict, score,
                          reasoning, root_cause, evidence, reviewed_at,
                          review_count
                     FROM session_reviews WHERE session_key = ANY(%s)""",
                (list(keys),))
    return {r["session_key"]: dict(r) for r in cur.fetchall()}


def pending(cur, since, now=None, **kw):
    """Settled sessions never reviewed, or whose fingerprint changed."""
    sessions, stats = discover(cur, since, now, **kw)
    done = reviewed(cur, [s["key"] for s in sessions])
    out = []
    for s in sessions:
        prev = done.get(s["key"])
        if prev and prev["fingerprint"] == s["fingerprint"]:
            continue
        s["previous_review"] = prev
        out.append(s)
    stats["already_reviewed"] = len(sessions) - len(out)
    stats["pending"] = len(out)
    return out, stats


# ── Context ──────────────────────────────────────────────────────────────

def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def request_ids(s):
    return {
        "messages": [m["id"] for m in s.rows["messages"] if m["role"] == "user"],
        "jobs": [j["id"] for j in s.rows["jobs"]
                 if j["type"] in WORK_JOBS + RENDER_JOBS + ("index",)][:400],
    }


def _tool_error(status):
    return status not in (None, "", "success")


def context(s, cur=None, presign=None):
    """The full, JSON-ready context of one session.

    presign(asset_ids, keys) -> {"assets": {id: url}, "keys": {key: url}};
    without it (or when it fails) the storage keys are listed without URLs.
    """
    data = s.data
    msgs, jobs = s.rows["messages"], s.rows["jobs"]
    fam = {pid for pid, p in data["projects"].items()
           if p["root"] == s["project_id"]}
    proj = data["projects"][s["project_id"]]
    user = data["users"].get(s["user_id"]) or {}
    ids_in = {m["id"] for m in msgs}
    # Agent turns that continue a request made before this window.
    earlier = sorted({_int(j["message_id"]) for j in jobs
                      if j["type"] == "agent_turn" and j.get("message_id")}
                     - ids_in - {None})
    earlier_rows = []
    if earlier and cur is not None:
        cur.execute("""SELECT id, created_at, LEFT(content, 6000) AS content
                         FROM chat_messages WHERE id = ANY(%s)""", (earlier,))
        earlier_rows = [{"message_id": r["id"], "at": _iso(r["created_at"]),
                         "text": r["content"]} for r in cur.fetchall()]
    requests = [{"message_id": m["id"], "at": _iso(m["created_at"]),
                 "project_id": m["project_id"], "text": m["content"]}
                for m in msgs if m["role"] == "user"]
    # A prompt the subscribe wall held is kept on the wall's reply; after a
    # checkout it replays, so it is part of what the user asked for.
    seen = {r["text"] for r in requests}
    for m in msgs:
        if m.get("blocked_prompt") and m["blocked_prompt"] not in seen:
            seen.add(m["blocked_prompt"])
            requests.append({"message_id": m["id"], "at": _iso(m["created_at"]),
                             "project_id": m["project_id"],
                             "held_by_subscribe_wall": True,
                             "text": m["blocked_prompt"]})
    requests.sort(key=lambda r: r["at"] or "")
    replies = [{"message_id": m["id"], "at": _iso(m["created_at"]),
                "kind": m.get("kind"), "error": m.get("error"),
                "feedback": m.get("feedback"),
                "paywall": _is_paywall(m), "text": m["content"]}
               for m in msgs if m["role"] == "assistant"]
    timeline = []
    for m in msgs:
        if m["role"] != "activity" or m.get("source") == "mcp":
            continue           # MCP calls come from their jobs below
        timeline.append({"at": _iso(m["created_at"]), "via": "studio",
                         "tool": m.get("tool"), "status": m.get("tool_status"),
                         "error": _tool_error(m.get("tool_status")),
                         "args": m.get("args"), "text": m["content"]})
    for j in jobs:
        if j["type"] != "mcp_tool":
            continue
        timeline.append({"at": _iso(j["created_at"]), "via": "mcp",
                         "job_id": j["id"], "tool": j.get("tool"),
                         "status": j.get("tool_status") or j["state"],
                         "error": bool(j.get("is_error")) or j["state"] == "failed"
                         or _tool_error(j.get("tool_status")),
                         "args": j.get("args"),
                         "text": j.get("text") or j.get("error")})
    timeline.sort(key=lambda t: t["at"] or "")
    if len(timeline) > TIMELINE_CAP:
        keep = [t for t in timeline if t["error"]]
        rest = [t for t in timeline if not t["error"]]
        timeline = sorted(keep + rest[-(TIMELINE_CAP - len(keep)):],
                          key=lambda t: t["at"] or "")
    job_rows = []
    for j in jobs:
        if j["type"] == "mcp_tool":
            continue
        c, u = _aware(j["created_at"]), _aware(j["updated_at"])
        job_rows.append(_compact({
            "job_id": j["id"], "project_id": j["project_id"],
            "type": j["type"], "state": j["state"], "error": j.get("error"),
            "attempts": j.get("attempts"), "created_at": _iso(c),
            "updated_at": _iso(u),
            "seconds": round((u - c).total_seconds()) if c and u else None,
            "message_id": _int(j.get("message_id")),
            "source": j.get("source"),
            "quality": j.get("quality") or j.get("result_quality"),
            "outcome": j.get("outcome"), "status": j.get("status"),
            "honesty": j.get("honesty"),
            "edl_version": _int(j.get("edl_version")),
            "render_asset_id": _int(j.get("render_asset_id")),
            "superseded_by": j.get("superseded_by"),
            "clips": j.get("clips"),
            "credits_charged": j.get("credits_charged")}))
    errors = ([{"at": r["created_at"], "what": f"{r['type']} job {r['job_id']} "
                f"failed", "detail": r.get("error")}
               for r in job_rows if r["state"] == "failed"]
              + [{"at": r["at"], "what": f"error reply ({r['error']})",
                  "detail": r["text"][:300]}
                 for r in replies if r["error"]]
              + [{"at": t["at"], "what": f"tool {t['tool']} {t['status']}",
                  "detail": (t["text"] or "")[:300]}
                 for t in timeline if t["error"]])
    # Renders delivered in the window, with the worker's own contact sheets.
    lo = s["started_at"] - timedelta(minutes=2)
    hi = s["ended_at"] + timedelta(minutes=15)
    job_by_asset = {_int(j.get("render_asset_id")): j for j in jobs
                    if j.get("render_asset_id")}
    renders = []
    for a in data["assets"]:
        if a["project_id"] not in fam or a["kind"] != "render":
            continue
        if not (lo <= _aware(a["created_at"]) <= hi or a["id"] in job_by_asset):
            continue
        j = job_by_asset.get(a["id"]) or {}
        renders.append({
            "asset_id": a["id"], "project_id": a["project_id"],
            "job_id": j.get("id"), "job_type": j.get("type"),
            "quality": a.get("quality"), "variant": a.get("variant"),
            "edl_version": _int(a.get("edl_version")),
            "duration_s": a.get("duration_s"), "width": a.get("width"),
            "height": a.get("height"), "bytes": a.get("bytes"),
            "created_at": _iso(a["created_at"]),
            "storage_key": a["storage_key"], "purged": bool(a.get("purged_at")),
            "sheets": {k: v for k, v in (
                ("sheet", a.get("sheet_key") or j.get("sheet_key")),
                ("verify_sheet", j.get("verify_sheet_key")),
                ("caption_sheet", j.get("caption_sheet_key"))) if v},
            "url": None, "sheet_urls": {}})
    renders.sort(key=lambda r: r["asset_id"])
    sources = []
    for a in data["assets"]:
        if a["project_id"] in fam and a["kind"] in SOURCE_KINDS:
            sources.append({"asset_id": a["id"], "project_id": a["project_id"],
                            "kind": a["kind"], "filename": a.get("filename"),
                            "duration_s": a.get("duration_s"),
                            "width": a.get("width"), "height": a.get("height"),
                            "bytes": a.get("bytes"),
                            "storage_key": a["storage_key"],
                            "purged": bool(a.get("purged_at")),
                            "created_at": _iso(a["created_at"]), "url": None})
    sources = sources[-40:]
    nxt = [{"at": _iso(e["created_at"]), "kind": e["kind"],
            "project_id": e["project_id"], "detail": e["detail"]}
           for e in getattr(s, "next_events", [])]
    nxt += [{"at": _iso(p["at"]), "kind": "payment",
             "detail": f"{p['currency']} {p['amount_cents'] / 100:.2f} "
                       f"{p['plan'] or ''}".strip()}
            for p in getattr(s, "next_payments", [])]
    nxt += [{"at": _iso(h["at"]), "kind": "subscription_" +
             (h["scheduled_action"] or h["status"] or "change"),
             "detail": h.get("event_type")}
            for h in getattr(s, "next_subscription", [])]
    nxt.sort(key=lambda e: e["at"] or "")
    kinds = {e["kind"] for e in nxt}
    last_seen = _aware(user.get("last_seen_at"))
    summary = {
        "exported": bool(kinds & {"export_clicked", "mcp_export_delivered"})
        or any(r["type"] == "final" and r["state"] == "done"
               for r in job_rows),
        "downloaded": bool(kinds & {"download_triggered",
                                    "mcp_export_delivered"}),
        "thumbs_up": any(r["feedback"] == "up" for r in replies),
        "thumbs_down": any(r["feedback"] == "down" for r in replies),
        "checkout": "checkout_stage" in kinds,
        "paid": any(e["kind"] == "payment" for e in nxt),
        "cancelled": any(k.startswith("subscription_") for k in kinds)
        or "cancel_feedback" in kinds,
        "came_back_at": _iso(getattr(s, "next_session_at", None)),
        "left": bool(last_seen and last_seen <= s["ended_at"]
                     + timedelta(minutes=10)
                     and not getattr(s, "next_session_at", None)),
        "user_last_seen_at": _iso(last_seen),
    }
    out = {
        "session_key": s["key"], "evidence_prefix":
            f"ops/evidence/{evidence_slug(s['key'])}/",
        "surface": s["surface"], "fingerprint": s["fingerprint"],
        "settled": s["settled"],
        "window": {"started_at": _iso(s["started_at"]),
                   "ended_at": _iso(s["ended_at"]),
                   "minutes": round((s["ended_at"] - s["started_at"])
                                    .total_seconds() / 60, 1)},
        "project": {"id": proj["id"], "kind": proj["kind"],
                    "title": proj["title"],
                    "family": sorted(fam),
                    "created_at": _iso(proj["created_at"])},
        "user": {"id": s["user_id"], "joined_at": _iso(user.get("created_at")),
                 "plan": user.get("billing_plan") or user.get("plan"),
                 "paying": bool(user.get("paying")),
                 "subscribed": user.get("is_subscribed") == 1,
                 "billing_status": user.get("billing_status"),
                 "trial_status": user.get("trial_status"),
                 "new_user": bool(user.get("created_at") and _aware(
                     user["created_at"]) >= s["started_at"] - timedelta(days=2))},
        "classification": s["classification"],
        "request_ids": request_ids(s),
        "earlier_requests": earlier_rows,
        "requests": requests, "replies": replies,
        "timeline": timeline, "jobs": job_rows, "errors": errors,
        "renders": renders, "sources": sources,
        "next": nxt, "next_summary": summary,
        "previous_review": _jsonable(s.get("previous_review")),
        "admin_url": f"{ADMIN_URL}?tab=projects&project={proj['id']}",
    }
    if presign is not None:
        attach_urls(out, presign)
    return out


def attach_urls(ctx, presign):
    ids = [r["asset_id"] for r in ctx["renders"] if not r["purged"]]
    # The smallest playable source first: a proxy, else the original.
    ids += [x["asset_id"] for x in ctx["sources"] if not x["purged"]
            and x["kind"] in ("proxy", "original", "video_clip", "image_ref")]
    keys = sorted({k for r in ctx["renders"] for k in r["sheets"].values()})
    try:
        got = presign(ids, keys) or {}
    except Exception as e:                      # URLs are a convenience
        ctx["url_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return ctx
    assets = {int(k): v for k, v in (got.get("assets") or {}).items()}
    by_key = got.get("keys") or {}
    for r in ctx["renders"]:
        r["url"] = assets.get(r["asset_id"])
        r["sheet_urls"] = {n: by_key.get(k) for n, k in r["sheets"].items()}
    for x in ctx["sources"]:
        x["url"] = assets.get(x["asset_id"])
    return ctx


def _compact(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _jsonable(v):
    if v is None:
        return None
    return json.loads(json.dumps(v, default=str))


def brief(s):
    """One line per session for listings."""
    c = s["classification"]
    return {"session_key": s["key"], "surface": s["surface"],
            "project_id": s["project_id"], "user_id": s["user_id"],
            "started_at": _iso(s["started_at"]), "ended_at": _iso(s["ended_at"]),
            "settled": s["settled"], "fingerprint": s["fingerprint"],
            "jobs": len(s.rows["jobs"]),
            "user_messages": sum(1 for m in s.rows["messages"]
                                 if m["role"] == "user"
                                 or m.get("blocked_prompt")),
            "failed_jobs": c["failed_jobs"], "error_replies": c["error_replies"],
            "re_review": bool(s.get("previous_review"))}
