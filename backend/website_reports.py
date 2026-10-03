"""Admin-only aggregate reports; anonymous session journeys contain no customer text."""

def visits_report(cur, scope):
    cur.execute(f'''WITH visits AS (
      SELECT visited_at::date AS day, count(*) views,
             count(DISTINCT COALESCE(NULLIF(device_id,''),ip)) visitors
      FROM analytics_page_visits GROUP BY 1
    ), signups AS (
      SELECT created_at::date AS day,count(*) signups FROM users u
      WHERE is_verified=1 AND {scope} GROUP BY 1
    ), bounds AS (
      SELECT LEAST((SELECT min(day) FROM visits),(SELECT min(day) FROM signups),CURRENT_DATE) start
    )
    SELECT to_char(d::date,'YYYY-MM-DD') AS day,coalesce(v.views,0) count,
           coalesce(v.visitors,0) unique_visitors,coalesce(s.signups,0) signups,
           CASE WHEN v.visitors>0 THEN round(100.0*coalesce(s.signups,0)/v.visitors,1) ELSE NULL END conversion_rate
    FROM bounds,generate_series(start,CURRENT_DATE,interval '1 day') d
    LEFT JOIN visits v ON v.day=d::date LEFT JOIN signups s ON s.day=d::date ORDER BY d''')
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute('''SELECT count(*) views,count(DISTINCT COALESCE(NULLIF(device_id,''),ip)) unique_visitors,
                   min(visited_at)::date first_tracked FROM analytics_page_visits''')
    totals = dict(cur.fetchone())
    totals['signups'] = sum(r['signups'] for r in rows)
    totals['conversion_rate'] = round(100*totals['signups']/totals['unique_visitors'],1) if totals['unique_visitors'] else None
    peak = max(rows, key=lambda r:r['unique_visitors']) if rows else None
    return dict(data=rows,totals=totals,peak=peak,timezone='UTC',
                note='Verified signups / unique browsers is a period ratio, not a linked visitor cohort. Known bots and legacy time-update rows are excluded. Historical signups use account creation day; active time and linked journeys begin with this release.')

def journey_report(cur, scope):
    cur.execute('''SELECT min(visited_at) started_at FROM page_visits WHERE analytics_id IS NOT NULL''')
    started = cur.fetchone()['started_at']
    cur.execute('''WITH sessions AS (
      SELECT session_id,device_id,min(visited_at) started_at,max(coalesce(last_seen_at,visited_at)) last_seen,
             max(device_type) device,max(browser) browser,sum(time_on_page) active_seconds,
             jsonb_agg(jsonb_build_object('page',page,'at',visited_at,'active_seconds',time_on_page,
                       'scroll_depth',scroll_depth,'last_signal',exit_reason) ORDER BY visited_at) pages
      FROM analytics_page_visits WHERE analytics_id IS NOT NULL AND visited_at >= (now() AT TIME ZONE 'UTC')-interval '30 days'
      GROUP BY session_id,device_id
    ) SELECT substr(md5(s.session_id || s.device_id),1,10) session,s.started_at,s.last_seen,s.device,s.browser,
             s.active_seconds,s.pages,
             s.last_seen < (now() AT TIME ZONE 'UTC')-interval '30 minutes' inactive,
             EXISTS(SELECT 1 FROM website_signups w WHERE w.session_id=s.session_id AND w.device_id=s.device_id) signed_up,
             (SELECT coalesce(jsonb_agg(jsonb_build_object('kind',e.kind,'page',v.page,'at',e.created_at,'active_seconds',e.active_seconds,'status',e.status) ORDER BY e.created_at),'[]'::jsonb)
              FROM website_events e JOIN page_visits v ON v.analytics_id=e.visit_id
              WHERE v.session_id=s.session_id AND v.device_id=s.device_id) events
      FROM sessions s ORDER BY s.started_at DESC LIMIT 50''')
    sessions = [dict(r) for r in cur.fetchall()]
    cur.execute('''SELECT page,count(*) views,round(avg(time_on_page),1) active_seconds,
      round(avg(scroll_depth),0) scroll_depth,
      count(*) FILTER(WHERE time_on_page<5) under_five_seconds,
      count(*) FILTER(WHERE exit_reason IN ('hidden','pagehide')) exit_signals
      FROM analytics_page_visits WHERE analytics_id IS NOT NULL AND visited_at >= (now() AT TIME ZONE 'UTC')-interval '30 days'
      GROUP BY page ORDER BY views DESC LIMIT 35''')
    pages = [dict(r) for r in cur.fetchall()]
    cur.execute('''SELECT e.kind,count(*) events,count(DISTINCT v.device_id) visitors
      FROM website_events e JOIN analytics_page_visits v ON v.analytics_id=e.visit_id
      WHERE v.visited_at >= (now() AT TIME ZONE 'UTC')-interval '30 days'
      GROUP BY e.kind ORDER BY visitors DESC''')
    events = [dict(r) for r in cur.fetchall()]
    # A true linked conversion: visitors whose first recorded visit is in the
    # measurement window and whose verified account was created after that visit.
    # Returning historical browsers are excluded; anonymous cookie resets remain a limitation.
    cur.execute(f'''WITH first_visits AS (
      SELECT device_id,min(visited_at) first_at FROM analytics_page_visits
      WHERE device_id IS NOT NULL GROUP BY device_id
    ), cohort AS (
      SELECT * FROM first_visits WHERE first_at >= %s
    ) SELECT count(*) visitors,
      count(*) FILTER (WHERE EXISTS(SELECT 1 FROM website_signups w JOIN users u ON u.id=w.user_id
        WHERE w.device_id=c.device_id AND u.created_at>=c.first_at AND u.is_verified=1 AND {scope})) signups
      FROM cohort c''', (started,))
    cohort = dict(cur.fetchone())
    cohort['conversion_rate'] = round(100*cohort['signups']/cohort['visitors'],1) if cohort['visitors'] else None
    return dict(started_at=started,sessions=sessions,pages=pages,events=events,cohort=cohort,
                note='Visible, recently active seconds only. Last observed page is an exit candidate after 30 minutes of inactivity; tab hiding is not proof of abandonment. No field values, prompts, files, email addresses or session recordings are collected. Ad blockers, bots and cookie resets limit measurement.')
