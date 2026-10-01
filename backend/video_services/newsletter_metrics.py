"""Audience counts without repeatedly scanning activity for each account."""


def read_counts(cur, base_filter, export_states):
    cur.execute(f"""WITH activity AS MATERIALIZED (
        SELECT user_id, MAX(active_at) last_active FROM (
            SELECT user_id, MAX(created_at) active_at FROM client_events GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM video_jobs GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM projects GROUP BY user_id
            UNION ALL SELECT user_id, MAX(created_at) FROM chat_sessions GROUP BY user_id
        ) signals GROUP BY user_id
    ), exports AS MATERIALIZED (
        SELECT DISTINCT user_id FROM video_jobs
        WHERE type ILIKE '%%final%%' AND state IN {export_states}
    ), project_users AS MATERIALIZED (
        SELECT DISTINCT user_id FROM projects
    ), audience AS (
        SELECT u.*, GREATEST(COALESCE(a.last_active, TIMESTAMPTZ 'epoch'),
                            u.created_at::timestamptz) AS last_active,
               e.user_id IS NOT NULL AS has_export,
               p.user_id IS NOT NULL AS has_project
        FROM users u LEFT JOIN activity a ON a.user_id=u.id
        LEFT JOIN exports e ON e.user_id=u.id
        LEFT JOIN project_users p ON p.user_id=u.id
    ) SELECT
        COUNT(*) FILTER (WHERE {base_filter}) AS verified,
        COUNT(*) FILTER (WHERE u.is_verified=1 AND u.unsubscribed_at IS NOT NULL) AS unsubscribed,
        COUNT(*) FILTER (WHERE {base_filter} AND u.created_at >= NOW()-INTERVAL '7 days') AS new_7d,
        COUNT(*) FILTER (WHERE {base_filter} AND u.last_active >= NOW()-INTERVAL '3 days') AS active,
        COUNT(*) FILTER (WHERE {base_filter} AND u.last_active <= NOW()-INTERVAL '3 days'
                          AND u.last_active > NOW()-INTERVAL '30 days') AS dormant,
        COUNT(*) FILTER (WHERE {base_filter} AND u.last_active <= NOW()-INTERVAL '30 days') AS inactive,
        COUNT(*) FILTER (WHERE {base_filter} AND u.plan IS NOT NULL AND u.plan <> 'free') AS paid,
        COUNT(*) FILTER (WHERE {base_filter} AND u.has_project AND NOT u.has_export) AS never_exported
        FROM audience u""", ())
    return dict(cur.fetchone())
