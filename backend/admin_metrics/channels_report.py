"""Where signups and people came from (Q-CHANNEL-SIGNUPS, Q-CHANNEL-PEOPLE).

Signups are classified in Python with acquisition.channel(), the same
classifier the CRM endpoint uses, so the admin and the CRM can never disagree
about a label. A signup without a usable source always carries a reason
(plan §4.4); "unknown" no longer exists.
"""
from statistics import median

import acquisition
from acquisition import CHANNELS, REASON_LABELS, channel, not_recorded
from admin_metrics import db, defs, ranges, visitors

SURVEY_LABELS = {
    "tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube",
    "x": "X (Twitter)", "google": "Google", "reddit": "Reddit",
    "friend": "A friend", "ai_chatbot": "ChatGPT / AI", "other": "Other",
    "valmera_message": "A message from Valmera", "no_answer": "No answer",
}


def survey_label(key):
    if not key:
        return SURVEY_LABELS["no_answer"]
    return SURVEY_LABELS.get(key, key.replace("_", " ").capitalize())


def _tracking_sql(cur):
    return ("ws.tracking" if db.has_column(cur, "website_signups", "tracking")
            else "NULL::text")


def signup_rows(cur, period, extra_where="", extra_params=None):
    """Customer signups in the period with their stored touch and outcomes."""
    params = period.params()
    params.update(extra_params or {})
    where = "" if period.is_all else \
        "AND u.created_at >= %(start)s AND u.created_at < %(end)s"
    cur.execute(f"""
        SELECT u.id, u.created_at, (ws.user_id IS NOT NULL) AS has_row,
               ws.attribution, {_tracking_sql(cur)} AS tracking,
               ws.device_id,
               CASE WHEN o.skipped THEN NULL ELSE o.channel END AS told,
               COALESCE(pay.paid, FALSE) AS paid,
               COALESCE(pay.cents, 0) AS cents,
               fp.page AS first_page
          FROM users u
          LEFT JOIN website_signups ws ON ws.user_id = u.id
          LEFT JOIN onboarding_responses o ON o.user_id = u.id
          LEFT JOIN LATERAL (
              SELECT bool_or({defs.success('p')}) AS paid,
                     COALESCE(sum(p.amount_cents) FILTER (
                         WHERE {defs.success('p')} AND p.currency = 'USD'), 0)
                         AS cents
                FROM payments p WHERE p.user_id = u.id) pay ON TRUE
          LEFT JOIN LATERAL (
              SELECT pv.page FROM page_visits pv
               WHERE ws.device_id IS NOT NULL AND pv.device_id = ws.device_id
                 AND pv.analytics_id IS NOT NULL
               ORDER BY pv.visited_at LIMIT 1) fp ON TRUE
         WHERE {defs.customer('u')} {where} {extra_where}""", params)
    return [dict(r) for r in cur.fetchall()]


def reasons_since(cur):
    """When the backend began saving a reason for every signup (031 + code)."""
    if not db.has_column(cur, "website_signups", "tracking"):
        return None

    def compute():
        cur.execute("""SELECT min(completed_at) AS t FROM website_signups
                        WHERE tracking IN ('tracked','privacy_signal',
                                           'no_identity')""")
        row = cur.fetchone()
        return row["t"] if row else None
    return db.cached_value("reasons_since", 600, compute)


def touch_for(row, model="first"):
    """The Touch (plan §6.4) for one signup row, with a reason when missing."""
    created = row["created_at"]
    before = created is not None and \
        created < ranges.naive(defs.SOURCES_SINCE)
    tracking = row.get("tracking")
    attribution = row.get("attribution") or {}
    touch = attribution.get(model) if isinstance(attribution, dict) else None
    if row.get("has_row"):
        if tracking == "privacy_signal":
            return not_recorded("privacy_browser")
        if tracking == "no_identity":
            return not_recorded("nothing_sent")
        if touch:
            out = channel(touch, row.get("first_page"))
            out.update(code=touch.get("code") or None,
                       at=_touch_at(touch),
                       estimated=tracking == "estimated_from_referrer",
                       reason=None, reason_label=None)
            return out
        return not_recorded("before_tracking" if before else "landing_lost")
    return not_recorded("before_tracking" if before else "no_row_unknown")


def _touch_at(touch):
    at = touch.get("at")
    if not isinstance(at, (int, float)):
        return None
    from datetime import datetime, timezone
    try:
        return defs.iso(datetime.fromtimestamp(at / 1000.0, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def complete_touch(t):
    """Fill every Touch key so the frontend can rely on the shape."""
    base = {"channel": "not_recorded", "channel_label": "Not recorded",
            "detail": None, "detail_label": None, "campaign": None,
            "campaign_label": None, "code": None, "at": None,
            "estimated": False, "reason": None, "reason_label": None}
    base.update({k: v for k, v in t.items() if k in base})
    return base


def _people_touch(row, model):
    attribution = row.get("first_attribution")
    if attribution is None:
        # Visits before 7 Oct 12:34 carried no labels: fall back to the
        # referrer host the tracker stored (empty means no referrer).
        ref = (row.get("first_referrer") or "").strip().lower()
        if ref in ("valmera.io", "www.valmera.io"):
            return None
        return {"source": ref or "direct"}
    return attribution.get(model) if isinstance(attribution, dict) else None


def people_by_channel(cur, period, model="first"):
    """{channel: {"people": n, "details": {detail: n}}} for person browsers."""
    out = {}
    for row in visitors.people_rows(cur, period):
        touch = _people_touch(row, model)
        c = channel(touch, row.get("first_page")) if touch else \
            not_recorded("landing_lost")
        bucket = out.setdefault(c["channel"], {"people": 0, "details": {}})
        bucket["people"] += 1
        key = c.get("detail") or c.get("reason") or "unknown"
        bucket["details"][key] = bucket["details"].get(key, 0) + 1
    return out


def coverage(signups):
    """How many signups have a recorded source, and why the rest do not."""
    reasons = {}
    tracked = estimated = 0
    for s in signups:
        t = s["touch"]
        if t["channel"] == "not_recorded":
            r = t.get("reason") or "no_row_unknown"
            reasons[r] = reasons.get(r, 0) + 1
        elif t.get("estimated"):
            estimated += 1
        else:
            tracked += 1
    return {"signups": len(signups), "tracked": tracked, "estimated": estimated,
            "not_recorded": [
                {"reason": key, "label": label, "signups": reasons[key]}
                for key, label in acquisition.NOT_RECORDED_REASONS
                if reasons.get(key)]}


def classify_signups(cur, period, model="first"):
    rows = signup_rows(cur, period)
    for r in rows:
        r["touch"] = touch_for(r, model)
    return rows


def acquisition_report(cur, period, model="first"):
    """GET /admin/v2/acquisition data."""
    signups = classify_signups(cur, period, model)
    tracked_people = period.end is None or period.end > defs.VISITS_SINCE
    people = people_by_channel(cur, period, model) if tracked_people else {}
    classes = visitors.classify(cur, period) if tracked_people else None
    previews = (classes or {}).get("link_preview", 0)

    rows = []
    for key, label in CHANNELS:
        in_channel = [s for s in signups if s["touch"]["channel"] == key]
        p = people.get(key, {}).get("people", 0) if tracked_people else None
        details = {}
        for s in in_channel:
            t = s["touch"]
            dkey = t.get("detail") or t.get("reason") or "unknown"
            dlabel = t.get("detail_label") or t.get("reason_label") or dkey
            d = details.setdefault(dkey, {"key": dkey, "label": dlabel,
                                          "people": 0, "signups": 0,
                                          "paying": 0, "collected_usd": 0.0,
                                          "estimated": 0})
            d["signups"] += 1
            d["paying"] += 1 if s["paid"] else 0
            d["collected_usd"] = round(d["collected_usd"]
                                       + defs.usd(s["cents"]), 2)
            d["estimated"] += 1 if t.get("estimated") else 0
        for dkey, n in people.get(key, {}).get("details", {}).items():
            d = details.setdefault(dkey, {"key": dkey, "label": _detail_label(
                key, dkey), "people": 0, "signups": 0, "paying": 0,
                "collected_usd": 0.0, "estimated": 0})
            d["people"] = n
        if not tracked_people:
            for d in details.values():
                d["people"] = None
        signups_n = len(in_channel)
        rows.append({
            "channel": key, "label": label,
            "people": p,
            "link_previews": previews if key == "outreach" else 0,
            "signups": signups_n,
            # A rate needs people and signups measured the same way; "not
            # recorded" signups have no matching people, so it has none.
            "signup_rate": (defs.pct(signups_n, p)
                            if p and key != "not_recorded" else None),
            "paying": sum(1 for s in in_channel if s["paid"]),
            "collected_usd": round(sum(defs.usd(s["cents"])
                                       for s in in_channel), 2),
            "details": sorted(details.values(),
                              key=lambda d: (-d["signups"],
                                             -(d["people"] or 0)))[:25],
        })

    told = told_vs_measured(signups)
    before = None
    if period.is_all or period.start < defs.SOURCES_SINCE:
        before = before_tracking(signups)
    return {"model": model, "coverage": coverage(signups), "channels": rows,
            "told_vs_measured": told, "before_tracking": before}


def _detail_label(channel_key, detail_key):
    probe = {"search": acquisition.SEARCH_LABELS,
             "ai_assistant": acquisition.AI_LABELS,
             "social": acquisition.SOCIAL_LABELS,
             "email": acquisition.EMAIL_LABELS}.get(channel_key, {})
    if channel_key == "no_referrer":
        return "No referrer"
    if channel_key == "outreach":
        return acquisition.campaign_label(detail_key)
    if channel_key == "not_recorded":
        return REASON_LABELS.get(detail_key, detail_key)
    return probe.get(detail_key, detail_key)


def told_vs_measured(signups):
    measured = [s for s in signups if s["touch"]["channel"] != "not_recorded"
                and s.get("told")]
    columns = [k for k, _ in CHANNELS
               if any(s["touch"]["channel"] == k for s in measured)]
    rows = {}
    for s in measured:
        r = rows.setdefault(s["told"], {"told": s["told"],
                                        "told_label": survey_label(s["told"]),
                                        "counts": {c: 0 for c in columns},
                                        "total": 0})
        r["counts"][s["touch"]["channel"]] += 1
        r["total"] += 1
    return {"columns": columns,
            "rows": sorted(rows.values(), key=lambda r: -r["total"])}


def before_tracking(signups):
    group = [s for s in signups
             if s["touch"].get("reason") == "before_tracking"]
    told = {}
    no_answer = 0
    for s in group:
        if not s.get("told"):
            no_answer += 1
            continue
        told[s["told"]] = told.get(s["told"], 0) + 1
    return {"signups": len(group), "no_answer": no_answer,
            "told": [{"told": k, "told_label": survey_label(k), "signups": n}
                     for k, n in sorted(told.items(), key=lambda kv: -kv[1])]}


def summary_channels(cur, period):
    """Today's "Where signups came from" block (model = first)."""
    signups = classify_signups(cur, period, "first")
    tracked_people = period.end is None or period.end > defs.VISITS_SINCE
    people = people_by_channel(cur, period, "first") if tracked_people else {}
    rows = []
    for key, label in CHANNELS:
        if key == "not_recorded":
            continue
        n = sum(1 for s in signups if s["touch"]["channel"] == key)
        p = people.get(key, {}).get("people", 0) if tracked_people else None
        if n or p:
            rows.append({"channel": key, "label": label, "signups": n,
                         "people": p})
    cov = coverage(signups)
    return {"model": "first", "rows": rows,
            "not_recorded": {
                "signups": sum(r["signups"] for r in cov["not_recorded"]),
                "reasons": cov["not_recorded"]}}


def landing_pages(cur, period, limit=30):
    """Q-PAGES: first page of each person, median active time, later signups."""
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return []
    extra = """,
               (array_agg(v.page ORDER BY v.visited_at))[1] AS first_page,
               min(v.visited_at) AS first_at"""
    cur.execute(f"""WITH {visitors.browsers_cte(cur, extra_cols=extra)},
        ppl AS (SELECT d.device_id, d.first_page, d.first_at FROM d
                 WHERE {visitors.CLASS_SQL} = 'person'),
        lp AS (SELECT p.device_id, p.first_page, p.first_at,
                      sum(v.active_s) AS active_s
                 FROM ppl p JOIN v ON v.device_id = p.device_id
                                  AND v.page = p.first_page
                GROUP BY 1, 2, 3)
        SELECT lp.first_page AS page, count(*) AS people,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY lp.active_s)
                   AS active_median_s,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM website_signups ws
                     JOIN users u ON u.id = ws.user_id
                    WHERE ws.device_id = lp.device_id
                      AND u.created_at >= lp.first_at
                      AND {defs.customer('u')})) AS signups
          FROM lp GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(limit)s""",
                visitors.params(cur, period, limit=int(limit)))
    out = []
    for r in cur.fetchall():
        people = int(r["people"])
        signups = int(r["signups"])
        med = r["active_median_s"]
        out.append({"page": r["page"], "people": people,
                    "active_median_s": round(float(med)) if med is not None
                    else None,
                    "signups": signups,
                    "signup_share": defs.pct(signups, people)})
    return out


def customer_touches(cur, user_ids):
    """{user_id: (first Touch, last Touch, tracking)} for a page of customers."""
    if not user_ids:
        return {}
    cur.execute(f"""
        SELECT u.id, u.created_at, (ws.user_id IS NOT NULL) AS has_row,
               ws.attribution, {_tracking_sql(cur)} AS tracking,
               NULL::text AS first_page
          FROM users u LEFT JOIN website_signups ws ON ws.user_id = u.id
         WHERE u.id = ANY(%s)""", (list(user_ids),))
    out = {}
    for r in cur.fetchall():
        r = dict(r)
        out[r["id"]] = (complete_touch(touch_for(r, "first")),
                        complete_touch(touch_for(r, "last")),
                        _tracking_state(r))
    return out


def _tracking_state(row):
    if not row.get("has_row"):
        if row["created_at"] < ranges.naive(defs.SOURCES_SINCE):
            return "before_tracking"
        return "unknown"
    return row.get("tracking") or ("tracked" if row.get("attribution")
                                   else ("before_tracking"
                                         if row["created_at"]
                                         < ranges.naive(defs.SOURCES_SINCE)
                                         else "tracked"))


def median_or_none(values):
    values = [v for v in values if v is not None]
    return median(values) if values else None
