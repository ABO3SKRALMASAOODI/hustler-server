"""Q-OUTREACH: what the CRM's links did, per campaign and per recipient code.

An outreach link is `valmera.io/?r=<code>[&utm_campaign=<slug>]`. Meta opens
every link in a DM when the message is SENT to build a preview, so "opened"
alone means nothing: each browser is classified with the visitor rules (G8)
and previews are reported separately from people. Valmera never stores who a
code was sent to; the CRM maps code → recipient.
"""
from acquisition import CODE, campaign_label, UNTAGGED_CAMPAIGNS
from admin_metrics import defs, ranges, visitors

_TOUCH = """CASE WHEN COALESCE(v.attribution->'last'->>'code','') <> ''
                 THEN v.attribution->'last' ELSE v.attribution->'first' END"""
_SIGNUP_TOUCH = """CASE WHEN COALESCE(ws.attribution->'first'->>'code','') <> ''
                        THEN ws.attribution->'first'
                        ELSE ws.attribution->'last' END"""
_CODED_ROW = """(COALESCE(attribution->'first'->>'code','') <> ''
                 OR COALESCE(attribution->'last'->>'code','') <> '')"""


def _campaign_key(raw):
    raw = (raw or "").strip()
    return "untagged" if raw.lower() in UNTAGGED_CAMPAIGNS else raw


def coded_browsers(cur, period, code=None):
    """One row per (browser, code) with the browser's class over the period."""
    code_filter = ""
    extra = {}
    only_code = ""
    if code:
        code_filter = """AND (attribution->'first'->>'code' = %(code)s
                              OR attribution->'last'->>'code' = %(code)s)"""
        extra["code"] = code
    only_code = "WHERE c.code = %(code)s" if code else ""
    restrict = f"""AND pv.device_id IN (
        SELECT device_id FROM page_visits
         WHERE analytics_id IS NOT NULL
           AND visited_at >= %(start)s AND visited_at < %(end)s
           AND {_CODED_ROW} {code_filter})"""
    cur.execute(f"""WITH {visitors.browsers_cte(cur, extra_where=restrict)},
        cls AS (SELECT d.device_id, {visitors.CLASS_SQL} AS class,
                       d.pages FROM d),
        coded AS (
          SELECT v.device_id, ({_TOUCH})->>'code' AS code,
                 COALESCE(({_TOUCH})->>'campaign', '') AS campaign,
                 COALESCE(({_TOUCH})->>'source', '') AS network,
                 min(v.visited_at) AS first_at, max(v.visited_at) AS last_at,
                 count(*) AS pages
            FROM v WHERE v.coded GROUP BY 1, 2, 3, 4)
        SELECT c.*, cls.class, cls.pages AS browser_pages
          FROM coded c JOIN cls USING (device_id) {only_code}""",
                visitors.params(cur, period, **extra))
    return [dict(r) for r in cur.fetchall()]


def coded_signups(cur, period, code=None):
    """Customer signups whose browser carried an outreach code."""
    params = period.params()
    where = "" if period.is_all else \
        "AND u.created_at >= %(start)s AND u.created_at < %(end)s"
    code_filter = ("AND (ws.attribution->'first'->>'code' = %(code)s "
                   "OR ws.attribution->'last'->>'code' = %(code)s)") \
        if code else ("AND (COALESCE(ws.attribution->'first'->>'code','') <> '' "
                      "OR COALESCE(ws.attribution->'last'->>'code','') <> '')")
    if code:
        params["code"] = code
    cur.execute(f"""
        SELECT u.id, u.email, u.plan, u.created_at,
               ({_SIGNUP_TOUCH})->>'code' AS code,
               COALESCE(({_SIGNUP_TOUCH})->>'campaign', '') AS campaign,
               COALESCE(pay.paid, FALSE) AS paid,
               COALESCE(pay.cents, 0) AS cents,
               CASE WHEN {defs.paying('u')} THEN 'paying'
                    WHEN {defs.failing('u')} THEN 'payment_failing'
                    WHEN COALESCE(pay.paid, FALSE) THEN 'canceled'
                    ELSE 'free' END AS status
          FROM website_signups ws JOIN users u ON u.id = ws.user_id
          LEFT JOIN LATERAL (
              SELECT bool_or({defs.success('p')}) AS paid,
                     COALESCE(sum(p.amount_cents) FILTER (
                         WHERE {defs.success('p')} AND p.currency='USD'), 0)
                         AS cents
                FROM payments p WHERE p.user_id = u.id) pay ON TRUE
         WHERE {defs.customer('u')} {where} {code_filter}""", params)
    return [dict(r) for r in cur.fetchall()]


def campaigns(cur, period):
    """GET /admin/v2/acquisition/outreach data."""
    rows = coded_browsers(cur, period)
    signups = coded_signups(cur, period)
    out = {}

    def bucket(key, network=None):
        b = out.get(key)
        if b is None:
            b = out[key] = {"campaign": key, "label": campaign_label(
                "" if key == "untagged" else key),
                "network": None, "codes": set(), "previews": set(),
                "people": set(), "signups": 0, "paying": 0,
                "collected_usd": 0.0, "first_seen_at": None,
                "last_seen_at": None, "untagged_codes": set()}
        if network and not b["network"]:
            b["network"] = network.title() if network.islower() else network
        return b

    for r in rows:
        b = bucket(_campaign_key(r["campaign"]), r["network"])
        b["codes"].add(r["code"])
        if r["class"] == "link_preview":
            b["previews"].add(r["device_id"])
        elif r["class"] == "person":
            b["people"].add(r["device_id"])
        b["first_seen_at"] = min(filter(None, [b["first_seen_at"],
                                               r["first_at"]]))
        b["last_seen_at"] = max(filter(None, [b["last_seen_at"],
                                              r["last_at"]]))
    for s in signups:
        b = bucket(_campaign_key(s["campaign"]))
        b["signups"] += 1
        b["paying"] += 1 if s["paid"] else 0
        b["collected_usd"] = round(b["collected_usd"] + defs.usd(s["cents"]),
                                   2)
    result = []
    for b in out.values():
        result.append({
            "campaign": b["campaign"], "label": b["label"],
            "network": b["network"],
            "links_opened": len(b["codes"]),
            "link_previews": len(b["previews"]),
            "people": len(b["people"]),
            "signups": b["signups"], "paying": b["paying"],
            "collected_usd": b["collected_usd"],
            "first_seen_at": defs.iso(b["first_seen_at"]),
            "last_seen_at": defs.iso(b["last_seen_at"]),
        })
    result.sort(key=lambda r: r["last_seen_at"] or "", reverse=True)
    totals = {k: sum(r[k] for r in result)
              for k in ("links_opened", "link_previews", "people", "signups",
                        "paying")}
    totals["collected_usd"] = round(sum(r["collected_usd"] for r in result),
                                    2)
    all_codes = {r["code"] for r in rows}
    untagged = {r["code"] for r in rows
                if _campaign_key(r["campaign"]) == "untagged"}
    return {"campaigns": result, "totals": totals,
            "untagged_share": defs.pct(len(untagged), len(all_codes))}


def code_lookup(cur, code):
    """GET /admin/v2/acquisition/outreach/code: one recipient's link."""
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError("A link code is 16–32 letters, digits, - or _.")
    period = ranges.Period(key="all", label="Any time",
                           start=defs.VISITS_SINCE, end=ranges.utc_now())
    rows = coded_browsers(cur, period, code=code)
    signups = coded_signups(cur, ranges.Period(key="all", label="Any time"),
                            code=code)
    if not rows and not signups:
        return None
    campaign = next((r["campaign"] for r in rows if r["campaign"]), None)
    if campaign is None and signups:
        campaign = signups[0]["campaign"]
    return {
        "code": code,
        "campaign_label": campaign_label(campaign or ""),
        "first_seen_at": defs.iso(min((r["first_at"] for r in rows),
                                      default=None)),
        "last_seen_at": defs.iso(max((r["last_at"] for r in rows),
                                     default=None)),
        "link_previews": len({r["device_id"] for r in rows
                              if r["class"] == "link_preview"}),
        "people": len({r["device_id"] for r in rows
                       if r["class"] == "person"}),
        "pages_viewed_by_people": sum(int(r["browser_pages"] or 0)
                                      for r in rows
                                      if r["class"] == "person"),
        "signups": [{"customer": {"id": s["id"], "email": s["email"],
                                  "plan": s["plan"], "status": s["status"]},
                     "joined_at": defs.iso(s["created_at"]),
                     "paid": bool(s["paid"])} for s in signups],
    }
