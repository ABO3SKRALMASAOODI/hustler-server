"""Every session of a window, newest first, with its kind (owner, 18 Nov
2026).

  "the sessions page in the admin is confusing. First it loads the newer
  sessions slower than the older ones, and it loads only a handful of them
  ... I can't differentiate between sessions that were edited or not edited
  and the ones that were not edited in Valmera but in the MCP etc."

The Journeys report builds a full story per session (context, renders,
reviews) and is served from the 10-minute shared report cache, so the newest
sessions reached the admin last and only edit sessions were listed. This list
is computed live from a few lean, time-bounded queries (no per-session
query), keeps every session in the window, including uploads and first cuts
nobody asked to change, and says what kind each one is.

A session is the same burst of work as in `sessions` (one project family,
messages and jobs within GAP). Its kind comes from what the product
recorded, never from what the customer wrote:

  surface    mcp            mcp_tool jobs or MCP activity rows (an outside AI
                            app drove our tools)
             shorts         a Make-shorts project or a shorts_plan job
             website_demo   a website recording: an agent turn given a URL,
                            or the record_website / showcase_demo tools
             studio         everything else in the Valmera studio
  edited     an EDL version was written by an agent turn answering a request,
             by an MCP tool call, or by the user's own timeline edit. The
             automatic first cut is not an edit.

Only reads.
"""
from datetime import timedelta

from admin_metrics import defs
from ops_loop import EXCLUDED_USER_IDS, sessions as S, views

SURFACES = ("studio", "mcp", "shorts", "website_demo")
SURFACE_LABELS = {"studio": "Studio", "mcp": "MCP", "shorts": "Shorts",
                  "website_demo": "Website demo"}
WEBSITE_TOOLS = ("record_website", "record_website_demo", "showcase_demo")
MAX_DAYS = 90
# An EDL version is written just before its job is marked done.
VERSION_PAD = timedelta(seconds=90)
ASK_CHARS = 180


def _load(cur, roots, lo):
    """The rows the windows and the kinds need, lean (no long text)."""
    out = {"projects": {}, "users": {}, "messages": [], "jobs": [],
           "edls": [], "live": set()}
    if not roots:
        return out
    cur.execute("""
        SELECT p.id, p.user_id, p.kind, LEFT(p.title, 120) AS title,
               p.parent_project_id, p.chat_session_id,
               COALESCE(p.parent_project_id, p.id) AS root
          FROM projects p
         WHERE p.id = ANY(%(roots)s) OR p.parent_project_id = ANY(%(roots)s)""",
                {"roots": list(roots)})
    for r in cur.fetchall():
        out["projects"][r["id"]] = dict(r)
    pids = list(out["projects"])
    uids = sorted({p["user_id"] for p in out["projects"].values()})
    chats = [p["chat_session_id"] for p in out["projects"].values()
             if p["chat_session_id"]]
    cur.execute(f"""
        SELECT u.id, u.email, u.plan, u.billing_plan,
               ({defs.paying('u')}) AS paying,
               ({defs.customer('u')} AND NOT (u.id = ANY(%(internal)s)))
                   AS real_customer
          FROM users u WHERE u.id = ANY(%(uids)s)""",
                {"uids": uids, "internal": list(EXCLUDED_USER_IDS)})
    out["users"] = {r["id"]: dict(r) for r in cur.fetchall()}
    if chats:
        cur.execute("""
            SELECT m.id, m.session_id, m.role, m.created_at,
                   CASE WHEN m.role = 'user' THEN LEFT(m.content, 400) END
                       AS content,
                   m.meta->>'kind' AS kind, m.meta->>'error' AS error,
                   m.meta->>'tool' AS tool, m.meta->>'source' AS source,
                   m.meta->>'feature' AS feature,
                   m.meta->>'surface' AS wall_surface,
                   COALESCE(m.meta->>'subscribe_gate',
                            m.meta->>'subscribe_required') AS subscribe_gate
              FROM chat_messages m
             WHERE m.session_id = ANY(%(chats)s) AND m.created_at >= %(lo)s
             ORDER BY m.id""", {"chats": chats, "lo": S._naive(lo)})
        by_chat = {p["chat_session_id"]: p["id"]
                   for p in out["projects"].values()}
        for r in cur.fetchall():
            d = dict(r)
            d["project_id"] = by_chat.get(d["session_id"])
            d["created_at"] = S._aware(d["created_at"])
            out["messages"].append(d)
    cur.execute("""
        SELECT j.id, j.project_id, j.type, j.state, j.created_at,
               j.updated_at, j.payload->>'tool' AS tool,
               j.payload->>'message_id' AS message_id,
               (j.payload->>'first_cut') IN ('true', '1') AS first_cut,
               (j.payload ? 'url') AS has_url,
               j.result->'download_receipt'->>'render_type' AS receipt_type
          FROM video_jobs j
         WHERE j.project_id = ANY(%(pids)s) AND j.updated_at >= %(lo)s
         ORDER BY j.id""", {"pids": pids, "lo": lo})
    out["jobs"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT e.project_id, e.version, e.created_by, e.created_at
          FROM edls e
         WHERE e.project_id = ANY(%(pids)s) AND e.created_at >= %(lo)s""",
                {"pids": pids, "lo": lo})
    out["edls"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT project_id FROM video_jobs
                    WHERE project_id = ANY(%(pids)s)
                      AND state IN ('queued', 'running')
                      AND type <> ALL(%(passive)s)""",
                {"pids": pids, "passive": list(S.PASSIVE_JOBS)})
    out["live"] = {out["projects"][r["project_id"]]["root"]
                   for r in cur.fetchall()}
    return out


def _edit_versions(fam, start, end, jobs, edls):
    """EDL versions written in this window by an edit: an agent turn that is
    not the automatic first cut (its version 1 is the untouched base it
    starts from), an MCP tool call, or the user's own timeline edit."""
    spans = [(S._aware(j["created_at"]), S._aware(j["updated_at"])
              + VERSION_PAD, j["type"] == "mcp_tool")
             for j in jobs
             if (j["type"] == "agent_turn" and not j["first_cut"])
             or j["type"] == "mcp_tool"]
    n = 0
    for e in edls:
        if e["project_id"] not in fam:
            continue
        at = S._aware(e["created_at"])
        if not (start - VERSION_PAD <= at <= end + VERSION_PAD):
            continue
        if e["created_by"] == "user":
            n += 1
        elif any(a <= at <= b and (mcp or e["version"] > 1)
                 for a, b, mcp in spans):
            n += 1
    return n


def kind_of(proj_kind, msgs, jobs, edit_versions):
    """{surface, edited, why}: what kind of session this was, from the
    product's own rows (job types, tool names, reply kinds)."""
    c = S.classify(msgs, jobs, proj_kind)
    surface = c["surface"]
    tools = {j.get("tool") for j in jobs} | {m.get("tool") for m in msgs}
    features = {m.get("feature") for m in msgs if m.get("feature")}
    if surface == "studio":
        if (any(j["type"] == "agent_turn" and j["has_url"] for j in jobs)
                or tools & set(WEBSITE_TOOLS) or "website_demo" in features):
            surface = "website_demo"
        elif "shorts" in features:
            surface = "shorts"
    # Requests: the user's messages in the window, plus earlier requests
    # (or held ones replayed after a checkout) that an agent turn here
    # answered.
    said = {m["id"] for m in msgs if m["role"] == "user"}
    answered = {j["message_id"] for j in jobs
                if j["type"] == "agent_turn" and not j["first_cut"]
                and j.get("message_id")}
    asks = len(said) + len({x for x in answered
                            if S._int(x) not in said})
    replies = {m.get("kind") for m in msgs if m["role"] == "assistant"}
    work = [j for j in jobs if j["type"] in S.WORK_JOBS]
    edited = edit_versions > 0
    first_cut = any(j["type"] == "agent_turn" and j["first_cut"]
                    for j in jobs)
    failed = c["failed_jobs"] + c["error_replies"]
    blank = any(m.get("wall_surface") == "blank_canvas" for m in msgs
                if S._is_paywall(m))
    if edited:
        why = None
    elif surface == "mcp":
        why = "no edit version (the app only looked, rendered or exported)"
    elif asks and c["paywalled"]:
        why = ("asked on a blank canvas, held by the subscribe wall"
               if blank else "asked, held by the subscribe wall")
    elif asks and "feature_upgrade" in replies:
        why = "asked, answered with the upgrade card"
    elif asks and failed:
        why = "asked, the edit failed"
    elif asks and not work:
        why = "asked, answered in chat without an edit"
    elif asks:
        why = "asked, the agent ran but the edit did not change"
    elif first_cut:
        why = "first cut only, no request"
    elif any(j["type"] == "index" for j in jobs):
        why = "upload only"
    elif failed:
        why = "the agent failed"
    elif any(j["type"] == "final" for j in jobs):
        why = "exported, no request"
    elif any(j["type"] == "preview" for j in jobs):
        why = "rendered a preview, no request"
    elif c["paywalled"]:
        why = ("pressed Export, held by the subscribe wall"
               if any(m.get("wall_surface") == "export" for m in msgs)
               else "shown the plans, no request")
    else:
        why = "opened, no request"
    return {"surface": surface, "edited": edited, "why": why,
            "asks": asks, "first_cut": first_cut, "failed": failed,
            "paywalled": c["paywalled"]}


def _reviews(cur, roots, since):
    if not roots or not views.tables_ready(cur):
        return {}, {}
    cur.execute("""
            SELECT r.id, r.session_key, r.project_id, r.started_at,
                   r.verdict, r.score
              FROM session_reviews r
             WHERE r.project_id = ANY(%(roots)s)
               AND r.started_at >= %(since)s""",
                {"roots": list(roots), "since": S._aware(since) - S.PAD})
    by_key, by_project = {}, {}
    for r in cur.fetchall():
        d = dict(r)
        by_key[d["session_key"]] = d
        by_project.setdefault(d["project_id"], []).append(d)
    return by_key, by_project


def _review_for(key, root, start, end, by_key, by_project):
    if key in by_key:
        return by_key[key]
    for r in by_project.get(root, []):
        t = S._aware(r["started_at"])
        if start - timedelta(minutes=1) <= t <= end:
            return r
    return None


def compute(cur, now, days):
    """Every real customer's session that ended in the last `days`, newest
    first, each with its kind. Not filtered: the route filters and pages."""
    days = max(1, min(int(days), MAX_DAYS))
    since = now - timedelta(days=days)
    roots = S.candidate_roots(cur, since)
    data = _load(cur, roots, since - S.PAD)
    by_key, by_project = _reviews(cur, roots, since)
    edls_by_root = {}
    for e in data["edls"]:
        p = data["projects"].get(e["project_id"])
        if p:
            edls_by_root.setdefault(p["root"], []).append(e)
    rows = []
    for root in roots:
        proj = data["projects"].get(root)
        if not proj:
            continue
        user = data["users"].get(proj["user_id"]) or {}
        if not user.get("real_customer"):
            continue
        fam = {pid for pid, p in data["projects"].items()
               if p["root"] == root}
        for start, end, msgs, jobs in S.windows(data, root):
            if end < since:
                continue
            versions = _edit_versions(fam, start, end, jobs,
                                      edls_by_root.get(root, []))
            k = kind_of(proj["kind"], msgs, jobs, versions)
            key = S.session_key(root, start)
            rv = _review_for(key, root, start, end, by_key, by_project)
            renders = sum(1 for j in jobs if j["type"] in S.RENDER_JOBS
                          and j["state"] == "done")
            exported = any(j["type"] == "final" and j["state"] == "done"
                           for j in jobs) or any(
                S._mcp_delivery(j) == "final" for j in jobs)
            first_ask = next((m["content"] for m in msgs
                              if m["role"] == "user" and m.get("content")),
                             None)
            rows.append({
                "key": key, "project_id": root,
                "project_title": proj.get("title"),
                "user_id": proj["user_id"], "email": user.get("email"),
                "paying": bool(user.get("paying")),
                "plan": user.get("billing_plan") or user.get("plan"),
                "started_at": S._iso(start), "ended_at": S._iso(end),
                "running": root in data["live"] and end >= now - S.SETTLE,
                "surface": k["surface"],
                "surface_label": SURFACE_LABELS[k["surface"]],
                "edited": k["edited"], "why_not_edited": k["why"],
                "asks": k["asks"], "edit_versions": versions,
                "first_cut": k["first_cut"], "renders": renders,
                "exported": exported, "failed": k["failed"],
                "mcp_calls": sum(1 for j in jobs if j["type"] == "mcp_tool"),
                "first_ask": (S._redact(" ".join(first_ask.split()))[:ASK_CHARS]
                              if first_ask else None),
                "review": ({"id": rv["id"], "verdict": rv["verdict"],
                            "score": rv["score"]} if rv else None),
            })
    rows.sort(key=lambda r: (r["started_at"], r["key"]), reverse=True)
    return {"days": days, "since": S._iso(since), "sessions": rows}


def counts(rows):
    """{surface: {"total": n, "edited": n}} plus "all"."""
    out = {s: {"total": 0, "edited": 0} for s in ("all",) + SURFACES}
    for r in rows:
        for s in ("all", r["surface"]):
            out[s]["total"] += 1
            out[s]["edited"] += 1 if r["edited"] else 0
    return out


def page(report, surface=None, edited=None, page=1, per_page=50):
    """Filter and page a computed report. edited: True, False or None."""
    rows = report["sessions"]
    shown = [r for r in rows
             if (not surface or r["surface"] == surface)
             and (edited is None or r["edited"] == edited)]
    per_page = max(1, min(int(per_page), 200))
    pages = max(1, -(-len(shown) // per_page))
    page = max(1, min(int(page), pages))
    lo = (page - 1) * per_page
    return {"days": report["days"], "since": report["since"],
            "total": len(shown), "all_total": len(rows),
            "counts": counts(rows), "page": page, "pages": pages,
            "per_page": per_page, "surface": surface, "edited": edited,
            "sessions": shown[lo:lo + per_page]}
