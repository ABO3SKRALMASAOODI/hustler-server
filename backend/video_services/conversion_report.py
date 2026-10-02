"""Observed account funnels, with payments verified by the server ledger."""


def read_report(cur, scope):
    cur.execute(f"""SELECT detail->>'stage' stage,
                       COUNT(DISTINCT ce.user_id) users
                FROM client_events ce JOIN users u ON u.id=ce.user_id
                WHERE ce.kind='checkout_stage'
                  AND ce.created_at >= NOW()-INTERVAL '30 days'
                  AND {scope}
                GROUP BY 1""")
    checkout = {r['stage']: int(r['users']) for r in cur.fetchall() if r['stage']}
    cur.execute(f"""WITH opened AS (
                    SELECT ce.user_id, MIN(ce.created_at) started_at
                    FROM client_events ce JOIN users u ON u.id=ce.user_id
                    WHERE ce.kind='checkout_stage' AND ce.detail->>'stage'='opened'
                      AND ce.created_at >= NOW()-INTERVAL '30 days' AND {scope}
                    GROUP BY ce.user_id)
                SELECT COUNT(*) AS users FROM opened o
                WHERE EXISTS (SELECT 1 FROM payments p WHERE p.user_id=o.user_id
                    AND p.status IN ('completed','paid') AND p.amount_cents>0
                    AND p.occurred_at >= o.started_at
                    AND p.occurred_at < o.started_at+INTERVAL '7 days')""")
    checkout['confirmed_payers'] = int(cur.fetchone()['users'])
    # One last-observed campaign per account, not multiple credited campaigns
    # for the same person. This is observational attribution, not proof that
    # an email caused a payment (a renewal can also fall within the window).
    cur.execute(f"""WITH touches AS (
                    SELECT DISTINCT ON (ce.user_id) ce.user_id, ce.created_at,
                           ce.detail->>'campaign' campaign,
                           ce.detail->>'source' source, ce.detail->>'medium' medium
                    FROM client_events ce JOIN users u ON u.id=ce.user_id
                    WHERE ce.kind='campaign_visit'
                      AND ce.created_at >= NOW()-INTERVAL '30 days' AND {scope}
                    ORDER BY ce.user_id,ce.created_at DESC,ce.id DESC)
                SELECT campaign, source, medium, COUNT(*) visitors,
                    COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM video_jobs j WHERE j.user_id=t.user_id
                          AND j.state='done' AND j.type='final'
                          AND j.updated_at >= t.created_at
                          AND j.updated_at < t.created_at+INTERVAL '7 days')) exporters,
                    COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM payments p WHERE p.user_id=t.user_id
                          AND p.status IN ('completed','paid') AND p.amount_cents>0
                          AND p.occurred_at >= t.created_at
                          AND p.occurred_at < t.created_at+INTERVAL '7 days')) payers
                FROM touches t GROUP BY campaign,source,medium
                ORDER BY visitors DESC,campaign LIMIT 50""")
    campaigns = [dict(r) for r in cur.fetchall()]
    return {'window_days': 30, 'outcome_window_days': 7,
            'checkout': checkout, 'campaigns': campaigns,
            'tracking_started': '2026-10-02',
            'method': 'Distinct signed-in real customers; owner/test accounts excluded. '
                      'Last observed campaign per account. Seven-day outcomes are '
                      'associations, include renewals, and are incomplete for recent visits. '
                      'Browser telemetry can be blocked; counts are not all visitors.'}
