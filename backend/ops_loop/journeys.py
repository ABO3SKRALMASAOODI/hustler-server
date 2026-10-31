"""Journeys: what happened to each user, one session at a time (owner,
31 Oct 2026).

  "I need to understand what happened with the user and what the product
  did and what the user wanted and whether we made that or not ...
  minimise the bluff and things that don't inform, and inform me more."

Two read models for the admin's Sessions tab:

  funnel    the trial funnel for one window (24 h or 7 d): of the accounts
            that signed up in it, how many uploaded, got a first result,
            asked for a change, were satisfied (a Reviewer score of 7 or
            more), pressed Download, subscribed. Counted IN ORDER: an account
            counts at a step only if it also did every step before it; the
            out-of-order count is given beside it. Each account's first time
            at each step is kept, so a card can be drawn for an account that
            never reached an edit session.
  sessions  one compact story per edit session (sessions.discover, settled
            or not, reviewed or not), newest first: who, the uploaded video,
            the user's exact words, the delivered video, what the Reviewer
            found per ask, what the user did next, and the linked tickets.

Only reads. Storage keys are returned, never URLs: the route presigns them
on every request (a cached report must never hold an expiring link).
"""
import re
from datetime import datetime, timedelta, timezone

from admin_metrics import defs
from ops_loop import EXCLUDED_USER_IDS, sessions as S, views

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
STEPS = (
    ("signed_up", "Signed up"),
    ("uploaded", "Uploaded a video"),
    ("first_cut", "Got a first cut"),
    ("asked", "Asked for a change"),
    ("satisfied", "Satisfied (score 7+)"),
    ("download", "Pressed Download"),
    ("subscribed", "Subscribed"),
)
SATISFIED_SCORE = 7
DELIVERED_KINDS = ("free_result_delivered", "first_cut_delivered",
                   "trial_result_delivered")
DOWNLOAD_KINDS = ("export_clicked", "download_triggered", "export_blocked",
                  "mcp_export_delivered")
# Events the session context does not carry (sessions.NEXT_KINDS is part of
# the review fingerprint, so it stays as it is) but the story needs.
STORY_KINDS = ("free_result_watched_pct", "free_result_delivered",
               "trial_gate_shown", "subscription_cards_impression",
               "first_prompt_blocked", "upload_failed", "upload_rejected")
REQUEST_CHARS = 1500
REPLY_CHARS = 420
SHOWN_RENDERS = 4
MAX_SESSIONS = 200


def _aware(v):
    return S._aware(v)


def _iso(v):
    return S._iso(v)


def _naive(v):
    return S._naive(v)


def shape(width, height):
    """'9:16 vertical' / '16:9 wide' / 'square' from stored pixels."""
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return None
    if not w or not h:
        return None
    r = w / float(h)
    if r < 0.8:
        name = "vertical"
    elif r > 1.25:
        name = "wide"
    else:
        name = "square"
    for label, val in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0),
                       ("4:3", 4 / 3), ("16:9", 16 / 9), ("3:4", 3 / 4)):
        if abs(r - val) < 0.03:
            return f"{label} {name}"
    return f"{w}x{h} {name}"


PLAN_NAMES = {"plus_2026": "New Plus", "mcp_connect": "MCP Connect",
              "ai": "Creator", "ai_pro": "Pro", "free": "Free"}


def plan_label(user, trial=False):
    """trial / new Plus / paying / MCP, in the owner's words."""
    plan = (user or {}).get("billing_plan") or (user or {}).get("plan")
    if (user or {}).get("paying"):
        if plan == "plus_2026":
            return "New Plus (paying)"
        if plan == "mcp_connect":
            return "MCP Connect (paying)"
        return f"Paying · {PLAN_NAMES.get(plan, plan or 'plan')}"
    if trial:
        return "Free trial"
    if (user or {}).get("billing_status") in ("past_due", "paused"):
        return "Payment failing"
    return "Free"


def device_label(u):
    t, b = (u or {}).get("device_type"), (u or {}).get("device_browser")
    if not t and not b:
        return None
    return " · ".join(x for x in ((t or "").capitalize(), b) if x)


def _clip(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


_MD = re.compile(r"(\*\*|__|`|^#+\s*)", re.M)


def _plain(text, n):
    return _clip(_MD.sub("", S._redact(str(text or ""))), n)


# ── The funnel ───────────────────────────────────────────────────────────

def _first(rows, key="user_id", at="at"):
    out = {}
    for r in rows:
        u, t = r[key], _aware(r[at])
        if u is not None and t is not None and (u not in out or t < out[u]):
            out[u] = t
    return out


def funnel(cur, now=None, window="7d"):
    """{window, since, steps:[{key, label, n, ids, any_order_n, stopped_ids}],
    people:{id: {...first time at each step...}}}."""
    now = _aware(now) or datetime.now(timezone.utc)
    since = now - WINDOWS[window]
    internal = list(EXCLUDED_USER_IDS)
    cur.execute(f"""
        SELECT u.id, u.email, u.created_at, u.last_seen_at, u.plan,
               u.billing_plan, u.billing_status, u.is_subscribed,
               u.device_type, u.device_browser,
               ({defs.paying('u')}) AS paying
          FROM users u
         WHERE {defs.customer('u')} AND NOT (u.id = ANY(%(internal)s))
           AND u.created_at >= %(since)s
         ORDER BY u.created_at DESC""",
                {"internal": internal, "since": _naive(since)})
    users = {r["id"]: dict(r) for r in cur.fetchall()}
    ids = list(users)
    at = {k: {} for k, _ in STEPS}
    at["signed_up"] = {u: _aware(r["created_at"]) for u, r in users.items()}
    trial = set()
    if ids:
        cur.execute("""
            SELECT p.user_id, min(a.created_at) AS at
              FROM assets a JOIN projects p ON p.id = a.project_id
             WHERE p.user_id = ANY(%(ids)s)
               AND a.kind IN ('original', 'video_clip')
             GROUP BY 1""", {"ids": ids})
        at["uploaded"] = _first(cur.fetchall())
        cur.execute("""
            SELECT user_id, min(created_at) AS at FROM client_events
             WHERE user_id = ANY(%(ids)s) AND kind = ANY(%(kinds)s)
             GROUP BY 1
            UNION ALL
            SELECT p.user_id, min(a.created_at)
              FROM assets a JOIN projects p ON p.id = a.project_id
             WHERE p.user_id = ANY(%(ids)s) AND a.kind = 'render'
               AND COALESCE(a.meta->>'variant', '') <> 'preview_check'
             GROUP BY 1""", {"ids": ids, "kinds": list(DELIVERED_KINDS)})
        at["first_cut"] = _first(cur.fetchall())
        cur.execute("""
            SELECT p.user_id, min(m.created_at) AS at
              FROM projects p
              JOIN chat_messages m ON m.session_id = p.chat_session_id
             WHERE p.user_id = ANY(%(ids)s) AND m.role = 'user'
             GROUP BY 1
            UNION ALL
            SELECT user_id, min(created_at) FROM video_jobs
             WHERE user_id = ANY(%(ids)s)
               AND type IN ('agent_turn', 'mcp_tool')
             GROUP BY 1""", {"ids": ids})
        at["asked"] = _first(cur.fetchall())
        if views.tables_ready(cur):
            cur.execute("""
                SELECT user_id, min(reviewed_at) AS at FROM session_reviews
                 WHERE user_id = ANY(%(ids)s) AND score >= %(s)s
                 GROUP BY 1""", {"ids": ids, "s": SATISFIED_SCORE})
            at["satisfied"] = _first(cur.fetchall())
        cur.execute("""
            SELECT user_id, min(created_at) AS at FROM client_events
             WHERE user_id = ANY(%(ids)s) AND kind = ANY(%(kinds)s)
             GROUP BY 1
            UNION ALL
            SELECT user_id, min(created_at) FROM video_jobs
             WHERE user_id = ANY(%(ids)s) AND type = 'final'
             GROUP BY 1""", {"ids": ids, "kinds": list(DOWNLOAD_KINDS)})
        at["download"] = _first(cur.fetchall())
        cur.execute(f"""
            SELECT user_id, min(COALESCE(occurred_at, created_at)) AS at
              FROM payments p
             WHERE user_id = ANY(%(ids)s) AND {defs.success('p')}
             GROUP BY 1""", {"ids": ids})
        at["subscribed"] = _first(cur.fetchall())
        for u, r in users.items():
            if r.get("paying") and u not in at["subscribed"]:
                at["subscribed"][u] = _aware(r["created_at"])
        cur.execute("""SELECT DISTINCT user_id FROM projects
                        WHERE user_id = ANY(%(ids)s)
                          AND meta->'free_result'->>'kind' = 'trial'""",
                    {"ids": ids})
        trial = {r["user_id"] for r in cur.fetchall()}
    steps, reached = [], set(ids)
    for i, (key, label) in enumerate(STEPS):
        did = set(at[key])
        now_reached = reached & did if i else set(ids)
        steps.append({"key": key, "label": label, "n": len(now_reached),
                      "ids": sorted(now_reached,
                                    key=lambda u: at["signed_up"][u],
                                    reverse=True),
                      "any_order_n": len(did & set(ids))})
        reached = now_reached
    for i, st in enumerate(steps):
        nxt = set(steps[i + 1]["ids"]) if i + 1 < len(steps) else set()
        st["stopped_ids"] = [u for u in st["ids"] if u not in nxt] \
            if i + 1 < len(steps) else []
        st["drop"] = (steps[i - 1]["n"] - st["n"]) if i else None
    people = {}
    for u, r in users.items():
        people[str(u)] = {
            "user_id": u, "email": r["email"],
            "plan": plan_label(r, trial=u in trial),
            "device": device_label(r),
            "last_seen_at": _iso(r.get("last_seen_at")),
            **{f"{k}_at": _iso(at[k].get(u)) for k, _ in STEPS}}
    return {"window": window, "since": _iso(since), "steps": steps,
            "people": people}


# ── One story per session ────────────────────────────────────────────────

def _reviews(cur, keys, roots, since):
    """Reviews for these sessions: by key, and by project for reviews whose
    window later grew (a key that changed when new work joined it)."""
    if not views.tables_ready(cur) or not roots:
        return {}, {}
    cur.execute("""
        SELECT r.id, r.session_key, r.project_id, r.started_at, r.verdict,
               r.score, r.reasoning, r.root_cause, r.request_summary,
               r.evidence, r.signals, r.reviewer, r.review_count,
               r.reviewed_at,
               COALESCE((SELECT json_agg(json_build_object(
                           'id', t.id, 'cause_key', t.cause_key,
                           'title', t.title, 'severity', t.severity,
                           'status', t.status, 'fix_sha', t.fix_sha,
                           'closed_at', t.closed_at,
                           'recurrence_count', t.recurrence_count)
                           ORDER BY t.id)
                           FROM ticket_reviews tr
                           JOIN tickets t ON t.id = tr.ticket_id
                          WHERE tr.review_id = r.id), '[]'::json) AS tickets
          FROM session_reviews r
         WHERE r.session_key = ANY(%(keys)s)
            OR (r.project_id = ANY(%(roots)s) AND r.started_at >= %(since)s)
         ORDER BY r.started_at""",
                {"keys": list(keys), "roots": list(roots),
                 "since": _aware(since) - S.PAD})
    by_key, by_project = {}, {}
    for r in cur.fetchall():
        d = dict(r)
        by_key[d["session_key"]] = d
        by_project.setdefault(d["project_id"], []).append(d)
    return by_key, by_project


def _match_review(s, by_key, by_project):
    if s["key"] in by_key:
        return by_key[s["key"]]
    for r in by_project.get(s["project_id"], []):
        t = _aware(r["started_at"])
        if s["started_at"] - timedelta(minutes=1) <= t <= s["ended_at"]:
            return r
    return None


def _story_events(cur, uids, since):
    if not uids:
        return []
    cur.execute("""
        SELECT ce.id, ce.user_id, ce.project_id, ce.kind, ce.created_at,
               LEFT(ce.detail::text, 300) AS detail
          FROM client_events ce
         WHERE ce.user_id = ANY(%(uids)s) AND ce.created_at >= %(lo)s
           AND ce.kind = ANY(%(kinds)s)
         ORDER BY ce.id""", {"uids": list(uids),
                             "lo": _aware(since) - S.PAD,
                             "kinds": list(STORY_KINDS)})
    return [dict(r) for r in cur.fetchall()]


def _people(cur, uids):
    if not uids:
        return {}, set()
    cur.execute("""SELECT id, email, device_type, device_browser
                     FROM users WHERE id = ANY(%s)""", (list(uids),))
    out = {r["id"]: dict(r) for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT user_id FROM projects
                    WHERE user_id = ANY(%s)
                      AND meta->'free_result'->>'kind' = 'trial'""",
                (list(uids),))
    return out, {r["user_id"] for r in cur.fetchall()}


def _detail(raw):
    import json
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _pick_source(ctx, want=None):
    srcs = [x for x in ctx["sources"] if x["kind"] in
            ("original", "video_clip", "proxy")]
    if not srcs:
        return None
    by_id = {x["asset_id"]: x for x in srcs}
    main = by_id.get(want) if want else None
    if main is None:
        root = ctx["project"]["id"]
        originals = [x for x in srcs if x["kind"] in ("original", "video_clip")]
        main = (next((x for x in originals if x["project_id"] == root), None)
                or (originals[0] if originals else srcs[0]))
    # Play the browser-friendly proxy of the same project when there is one
    # (an iPhone HEVC original does not play in Chrome).
    play = main
    if main["kind"] != "proxy":
        proxy = next((x for x in srcs if x["kind"] == "proxy"
                      and x["project_id"] == main["project_id"]
                      and not x["purged"]), None)
        if proxy:
            play = proxy
    return {"asset_id": main["asset_id"], "kind": main["kind"],
            "filename": main.get("filename"),
            "duration_s": S._num(main.get("duration_s")),
            "width": main.get("width"), "height": main.get("height"),
            "shape": shape(main.get("width"), main.get("height")),
            "uploaded_at": main.get("created_at"),
            "play_asset_id": play["asset_id"],
            "play_key": None if play["purged"] else play["storage_key"],
            "purged": bool(play["purged"])}


def _pick_render(ctx, want=None):
    rs = [r for r in ctx["renders"] if r.get("variant") != "preview_check"]
    if not rs:
        return None, []
    chosen = next((r for r in rs if r["asset_id"] == want), None) if want \
        else None
    if chosen is None:
        finals = [r for r in rs if r.get("variant") == "final"
                  or r.get("job_type") == "final"]
        chosen = (finals or rs)[-1]
    out = []
    for r in rs:
        out.append({
            "asset_id": r["asset_id"],
            "kind": "export" if (r.get("variant") == "final"
                                 or r.get("job_type") == "final")
            else "preview",
            "edl_version": r.get("edl_version"),
            "duration_s": S._num(r.get("duration_s")),
            "width": r.get("width"), "height": r.get("height"),
            "shape": shape(r.get("width"), r.get("height")),
            "created_at": r.get("created_at"),
            "play_key": None if r["purged"] else r["storage_key"],
            "purged": bool(r["purged"])})
    pick = next(x for x in out if x["asset_id"] == chosen["asset_id"])
    return pick, out


TOOL_NAMES = {"render_preview": "rendered a preview",
              "export_final": "exported", "keep_segments": "cut",
              "cut_range": "cut", "add_captions": "captions",
              "set_caption_style": "captions", "set_color_grade": "grade",
              "set_grade_custom": "grade", "apply_look": "look",
              "enhance_video": "enhance", "add_music": "music",
              "add_sfx": "sound effects", "add_zoom": "zooms",
              "look_at": "looked at frames", "watch_video": "watched",
              "make_shorts": "shorts", "auto_reframe": "reframe",
              "cut_silences": "silence cut", "add_text": "text",
              "add_title_card": "title"}


# Bookkeeping calls that say nothing about the edit.
META_TOOLS = {"load_tools", "read_skill", "justify_verification_findings",
              "expand_toolset", "get_edl", "project_state", "index_status",
              "wait_for_job", "getJobStatus", "shorts_status", "list_assets"}


def _visible_tool(name):
    return bool(name) and not name.startswith("_") and name not in META_TOOLS


def _process(ctx):
    """What the product did, in one line of facts (no judgement)."""
    tl = ctx["timeline"]
    counts = {}
    for t in tl:
        name = t.get("tool")
        if not _visible_tool(name):
            continue
        counts[name] = counts.get(name, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    failed_steps = sum(1 for t in tl if t["error"])
    failed_jobs = sum(1 for j in ctx["jobs"] if j.get("state") == "failed")
    start = _aware(ctx["window"]["started_at"])
    first_render = min((_aware(r["created_at"]) for r in ctx["renders"]
                        if r.get("variant") != "preview_check"),
                       default=None)
    replies = [r for r in ctx["replies"] if not r["paywall"]
               and (r.get("text") or "").strip()]
    last = replies[-1] if replies else None
    paywalls = sum(1 for r in ctx["replies"] if r["paywall"])
    return {
        "steps": len(tl), "failed_steps": failed_steps,
        "failed_jobs": failed_jobs,
        "tools": [{"tool": k, "n": v} for k, v in top],
        "minutes_to_first_result": round(
            (first_render - start).total_seconds() / 60, 1)
        if first_render and start else None,
        "session_minutes": ctx["window"]["minutes"],
        "paywall_replies": paywalls,
        "error_replies": sum(1 for r in ctx["replies"] if r.get("error")),
        "last_reply": {"at": last["at"], "text": _plain(last["text"],
                                                        REPLY_CHARS),
                       "error": last.get("error"),
                       "feedback": last.get("feedback")} if last else None,
    }


NEXT_LABEL = {
    "export_clicked": "Pressed Download",
    "export_blocked": "Download stopped by the subscribe wall",
    "download_triggered": "Downloaded the file",
    "mcp_export_delivered": "Their AI app fetched the export",
    "checkout_stage": "Opened checkout",
    "subscription_cards_plan_selected": "Picked a plan on the plans card",
    "subscription_cards_dismissed": "Closed the plans card",
    "trial_gate_shown": "Shown the plans card",
    "subscription_cards_impression": "Shown the plans card",
    "first_prompt_blocked": "Request held by the subscribe wall",
    "cancel_feedback": "Gave a cancellation reason",
    "player_error": "The video failed to play for them",
    "upload_failed": "An upload failed",
    "upload_rejected": "An upload was refused",
    "project_reopened": "Came back and opened the project",
    "payment": "Paid",
    "free_result_delivered": "First cut ready",
}


def _timeline(ctx, s, extra, renders, src=None):
    """What the user saw and did, in order, with times."""
    items = []
    start = s["started_at"]
    fam = set(ctx["project"]["family"])
    if src and src.get("uploaded_at"):
        up = _aware(src["uploaded_at"])
        if start - timedelta(minutes=5) <= up <= s["ended_at"]:
            dur = f" ({src['duration_s'] / 60:.1f} min)" \
                if (src.get("duration_s") or 0) >= 90 else \
                (f" ({src['duration_s']:.0f} s)" if src.get("duration_s")
                 else "")
            items.append({"at": src["uploaded_at"], "kind": "uploaded",
                          "label": "Uploaded the video" + dur})
    stop_root = getattr(s, "_next_root_at", None)
    stop_user = getattr(s, "next_session_at", None)
    for i, rq in enumerate(ctx["requests"]):
        items.append({"at": rq["at"], "kind": "asked" if i else "asked_first",
                      "label": ("Typed again" if i else "Typed the request")
                      + (" (held by the subscribe wall)"
                         if rq.get("held_by_subscribe_wall") else ""),
                      "quote": _clip(rq["text"], 90)})
    shown = renders if len(renders) <= SHOWN_RENDERS else (
        renders[:1] + renders[-(SHOWN_RENDERS - 1):])
    if len(renders) > SHOWN_RENDERS:
        items.append({"at": renders[1]["created_at"], "kind": "renders_more",
                      "label": f"{len(renders) - SHOWN_RENDERS} more "
                               "previews in between"})
    for r in shown:
        d = r.get("duration_s")
        dur = (f" ({d / 60:.1f} min)" if d >= 90 else f" ({d:.0f} s)") \
            if d else ""
        items.append({"at": r["created_at"], "kind": "render",
                      "label": ("Export ready" if r["kind"] == "export"
                                else "Preview ready") + dur
                      + (f" · v{r['edl_version']}" if r.get("edl_version")
                         else "")})
    for e in ctx["next"]:
        k = e["kind"]
        if k == "payment":
            label = f"Paid {e.get('detail') or ''}".strip()
        elif k.startswith("subscription_"):
            label = "Turned subscription renewal off" if "cancel" in k \
                else "Subscription changed"
        else:
            label = NEXT_LABEL.get(k, k.replace("_", " "))
        items.append({"at": e["at"], "kind": k, "label": label})
    watched = {}
    for e in extra:
        if e["user_id"] != s["user_id"]:
            continue
        t = _aware(e["created_at"])
        stop = stop_user if e["project_id"] is None else stop_root
        if t < start or (stop and t >= stop):
            continue
        if e["project_id"] is not None and e["project_id"] not in fam:
            continue
        d = _detail(e["detail"])
        if e["kind"] == "free_result_watched_pct":
            v = d.get("version")
            pct = int(d.get("pct") or 0)
            if v not in watched or pct > watched[v]["pct"]:
                watched[v] = {"at": _iso(t), "pct": pct, "version": v,
                              "dur": d.get("duration_s")}
            continue
        if e["kind"] == "free_result_delivered":
            continue          # the render row says it, with the length
        items.append({"at": _iso(t), "kind": e["kind"],
                      "label": NEXT_LABEL.get(e["kind"],
                                              e["kind"].replace("_", " "))})
    for w in watched.values():
        items.append({"at": w["at"], "kind": "watched",
                      "label": f"Watched {w['pct']}%"
                      + (f" of v{w['version']}" if w["version"] else "")})
    items.sort(key=lambda x: x["at"] or "")
    # The same thing twice within two minutes is one line.
    kept = []
    for it in items:
        prev = kept[-1] if kept else None
        if prev and prev["label"] == it["label"] and not it.get("quote") \
                and prev["at"] and it["at"] and (
                    _aware(it["at"]) - _aware(prev["at"])) \
                <= timedelta(minutes=2):
            continue
        kept.append(it)
    items = kept
    n = ctx["next_summary"]
    if n.get("left"):
        last = max(x for x in (n.get("user_last_seen_at"),
                               ctx["window"]["ended_at"],
                               items[-1]["at"] if items else None) if x)
        items.append({"at": last, "kind": "left",
                      "label": "Left: nothing after this"})
    elif n.get("came_back_at"):
        items.append({"at": n["came_back_at"], "kind": "came_back",
                      "label": "Came back later"})
    return items


def _review_card(r):
    if not r:
        return None
    sig = r.get("signals") or {}
    asks = sig.get("asks") or []
    fmt = "v2" if sig.get("journey_format") and asks else "older"
    return {
        "id": r["id"], "session_key": r["session_key"],
        "verdict": r["verdict"], "score": r["score"],
        "format": fmt,
        "backfilled_from": sig.get("backfilled_from"),
        "asks": asks, "why_short": sig.get("why_short"),
        "next_actions": sig.get("next_actions") or [],
        "request_summary": r.get("request_summary"),
        "reasoning": r.get("reasoning"), "root_cause": r.get("root_cause"),
        "evidence": r.get("evidence") or [],
        "reviewer": r.get("reviewer"), "review_count": r.get("review_count"),
        "reviewed_at": _iso(r.get("reviewed_at")),
        "source_asset_id": sig.get("source_asset_id"),
        "delivered_asset_id": sig.get("delivered_asset_id"),
        "tickets": [dict(t, closed_at=_iso(_aware(t.get("closed_at")))
                         if t.get("closed_at") else None,
                         commits=views.commit_links(t.get("fix_sha"))
                         if t.get("status") == "fixed" else [])
                    for t in (r.get("tickets") or [])],
    }


def story(s, ctx, review, person, trial, extra):
    rv = _review_card(review)
    src = _pick_source(ctx, rv and rv.get("source_asset_id"))
    got, renders = _pick_render(ctx, rv and rv.get("delivered_asset_id"))
    user = ctx["user"]
    joined = _aware(user.get("joined_at"))
    asks = [{"at": rq["at"], "text": _clip(rq["text"], REQUEST_CHARS),
             "held": bool(rq.get("held_by_subscribe_wall"))}
            for rq in ctx["requests"]]
    earlier = [{"at": rq["at"], "text": _clip(rq["text"], REQUEST_CHARS)}
               for rq in ctx.get("earlier_requests") or []]
    first_cut = bool(s["surface"] != "mcp" and not asks and not earlier
                     and renders)
    # Over MCP the user's own words stay in their AI app: what reached us is
    # its tool calls, in order.
    mcp_calls = []
    if s["surface"] == "mcp":
        for t in ctx["timeline"]:
            if t.get("via") != "mcp" or not _visible_tool(t.get("tool")):
                continue
            mcp_calls.append({"at": t["at"], "tool": t["tool"],
                              "args": _plain(t.get("args") or "", 160),
                              "error": bool(t["error"])})
    return {
        "key": s["key"], "surface": s["surface"],
        "project_id": ctx["project"]["id"],
        "project_title": ctx["project"]["title"],
        "started_at": ctx["window"]["started_at"],
        "ended_at": ctx["window"]["ended_at"],
        "settled": s["settled"],
        "who": {"user_id": s["user_id"],
                "email": (person or {}).get("email"),
                "plan": plan_label(dict(user, billing_plan=user.get("plan")),
                                   trial=s["user_id"] in trial),
                "signed_up_at": _iso(joined),
                "new_user": user.get("new_user"),
                "device": device_label(person)},
        "uploaded": src,
        "asked": asks, "asked_earlier": earlier,
        "automatic_first_cut": first_cut,
        "mcp_calls": mcp_calls[:8], "mcp_calls_total": len(mcp_calls),
        "got": got, "renders": len(renders),
        "process": _process(ctx),
        "review": rv,
        "next": _timeline(ctx, s, extra, renders, src),
        "outcome": {k: ctx["next_summary"].get(k) for k in (
            "exported", "downloaded", "checkout", "paid", "cancelled",
            "left", "thumbs_up", "thumbs_down")},
        "admin_url": ctx["admin_url"],
    }


def sessions(cur, now=None, window="7d", limit=MAX_SESSIONS):
    """{window, since, stories:[...], counts:{reviewed, unreviewed, live}}."""
    now = _aware(now) or datetime.now(timezone.utc)
    since = now - WINDOWS[window]
    found, stats = S.discover(cur, since, now, include_unsettled=True)
    found.sort(key=lambda x: x["started_at"], reverse=True)
    found = found[:limit]
    # The next session of the same project bounds its project events.
    by_root = {}
    for x in found:
        by_root.setdefault(x["project_id"], []).append(x["started_at"])
    for x in found:
        later = [t for t in by_root[x["project_id"]] if t > x["started_at"]]
        x._next_root_at = min(later) if later else None
    roots = sorted({x["project_id"] for x in found})
    by_key, by_project = _reviews(cur, [x["key"] for x in found], roots, since)
    uids = sorted({x["user_id"] for x in found})
    people, trial = _people(cur, uids)
    extra = _story_events(cur, uids, since)
    stories, matched = [], set()
    for x in found:
        ctx = S.context(x, cur)
        review = _match_review(x, by_key, by_project)
        if review:
            matched.add(review["id"])
        stories.append(story(x, ctx, review, people.get(x["user_id"]),
                             trial, extra))
    reviewed = sum(1 for st in stories if st["review"])
    return {"window": window, "since": _iso(since), "stories": stories,
            "counts": {"sessions": len(stories), "reviewed": reviewed,
                       "unreviewed": len(stories) - reviewed,
                       "running": sum(1 for st in stories
                                      if not st["settled"])},
            "discovery": stats}


def report(cur, now=None, window="7d"):
    now = _aware(now) or datetime.now(timezone.utc)
    return {"funnel": {w: funnel(cur, now, w) for w in WINDOWS},
            **sessions(cur, now, window)}


# ── Presigning, at request time ──────────────────────────────────────────

def storage_keys(rep):
    keys = set()
    for st in rep.get("stories") or []:
        for part in (st.get("uploaded"), st.get("got")):
            if part and part.get("play_key"):
                keys.add(part["play_key"])
        for e in ((st.get("review") or {}).get("evidence") or []):
            if e.get("storage_key"):
                keys.add(e["storage_key"])
    return sorted(keys)


def attach_urls(rep, urls):
    """Copy of the report with `url` next to every storage key (None when
    the key could not be signed); the keys themselves are dropped."""
    import copy
    rep = copy.deepcopy(rep)
    for st in rep.get("stories") or []:
        for part in (st.get("uploaded"), st.get("got")):
            if part:
                part["url"] = urls.get(part.pop("play_key", None) or "")
        rv = st.get("review")
        if rv:
            for e in rv.get("evidence") or []:
                e["url"] = urls.get(e.get("storage_key") or "")
    return rep
