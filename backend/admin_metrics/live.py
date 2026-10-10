"""Advanced → Live: who is on the site and what is running right now.

No IP addresses and no user agents ever leave this module.
"""
from acquisition import channel
from admin_metrics import defs, registry, visitors

LIVE_WINDOW = "5 minutes"


def people_now(cur):
    """Browsers active on a page in the last 5 minutes, without robots, link
    previews or the owner's devices. One definition for Live and the phone
    widget's "active now"."""
    ids = visitors.internal_ids(cur)
    refs = list(defs.PREVIEW_REFERRERS)
    cur.execute(f"""
        SELECT count(DISTINCT pv.device_id) AS n
          FROM page_visits pv
         WHERE pv.analytics_id IS NOT NULL
           AND pv.visited_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'
           AND COALESCE(pv.last_seen_at, pv.visited_at)
               >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{LIVE_WINDOW}'
           AND COALESCE(pv.user_agent, '') !~* %(robot)s
           AND NOT (pv.device_id = ANY(%(ids)s))
           AND NOT ((COALESCE(pv.attribution->'first'->>'code','') <> ''
                     OR COALESCE(pv.attribution->'last'->>'code','') <> '')
                    AND COALESCE(pv.referrer,'') = ANY(%(refs)s)
                    AND COALESCE(pv.scroll_depth, 0) = 0)""",
                {"robot": defs.ROBOT_UA, "ids": ids, "refs": refs})
    return int(cur.fetchone()["n"])


def live(cur):
    ids = visitors.internal_ids(cur)
    now_n = people_now(cur)
    cur.execute(f"""SELECT count(*) AS n FROM users u
                     WHERE u.last_seen_at >= NOW() - INTERVAL '{LIVE_WINDOW}'
                       AND {defs.customer('u')}""")
    signed_in = int(cur.fetchone()["n"])
    cur.execute(f"""
        SELECT vj.type, count(*) FILTER (WHERE vj.state = 'running') AS running,
               count(*) FILTER (WHERE vj.state = 'queued') AS queued
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE vj.state IN ('queued', 'running') AND {defs.customer('u')}
         GROUP BY 1 ORDER BY 1""")
    running = [{"type": r["type"],
                "label": defs.JOB_LABELS.get(r["type"], r["type"]),
                "running": int(r["running"]), "queued": int(r["queued"])}
               for r in cur.fetchall()]
    cur.execute("""
        SELECT pv.visited_at, pv.page, pv.device_type, pv.referrer,
               pv.attribution, COALESCE(pv.time_on_page, 0) AS active_s,
               COALESCE(pv.scroll_depth, 0) AS scroll,
               EXISTS (SELECT 1 FROM website_events e
                        WHERE e.visit_id = pv.analytics_id) AS clicked
          FROM page_visits pv
         WHERE pv.analytics_id IS NOT NULL
           AND pv.visited_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 days'
           AND COALESCE(pv.user_agent, '') !~* %(robot)s
           AND NOT (pv.device_id = ANY(%(ids)s))
         ORDER BY pv.visited_at DESC LIMIT 50""",
                {"robot": defs.ROBOT_UA, "ids": ids})
    hits = []
    for r in cur.fetchall():
        att = r["attribution"] if isinstance(r["attribution"], dict) else None
        touch = (att or {}).get("first") if att is not None else (
            {"source": r["referrer"] or "direct"})
        c = channel(touch, r["page"]) if touch else {"channel": "not_recorded"}
        coded = bool(att and any((att.get(k) or {}).get("code")
                                 for k in ("first", "last")))
        if r["scroll"] or r["clicked"] or r["active_s"] > \
                defs.PERSON_ACTIVE_SECONDS:
            cls = "person"
        elif coded and (r["referrer"] or "") in defs.PREVIEW_REFERRERS:
            cls = "link_preview"
        else:
            cls = "no_signal"
        hits.append({"at": defs.iso(r["visited_at"]), "page": r["page"],
                     "device_type": r["device_type"], "channel": c["channel"],
                     "active_s": int(r["active_s"]), "class": cls})
    return {"people_now": registry.metric("people_now", now_n),
            "signed_in_now": registry.metric("signed_in_now", signed_in),
            "running": running, "recent_hits": hits}
