"""First/latest acquisition labels. CRM identities never enter this database."""
import math
import re
import time

LABEL = re.compile(r'^[a-zA-Z0-9_.-]{1,80}$')
CODE = re.compile(r'^[A-Za-z0-9_-]{16,32}$')
TTL_MS = 30 * 86400 * 1000

def clean_touch(value, now=None):
    if not isinstance(value, dict):
        return None
    now = time.time() * 1000 if now is None else now
    at = value.get('at')
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at > now + 60000 or now-at > TTL_MS:
        return None
    def label(key):
        v = value.get(key)
        return v if isinstance(v, str) and LABEL.fullmatch(v) else ''
    source = label('source')
    if not source:
        return None
    code = value.get('code')
    code = code if isinstance(code, str) and CODE.fullmatch(code) else ''
    result = dict(source=source, medium=label('medium'), campaign=label('campaign'), content=label('content'), code=code, at=int(at))
    if code:
        result.update(source='instagram', medium='outreach', campaign='instagram_outreach', content='')
    return result

def clean_attribution(value):
    value = value if isinstance(value, dict) else {}
    return {key: clean_touch(value.get(key)) for key in ('first', 'last')}

def report(cur, scope, codes=None):
    # Payment totals are reduced to one row per user before joining signups.
    # Browser labels describe acquisition; they never establish user identity.
    params = []
    code_filter = ''
    if codes is not None:
        code_filter = "WHERE COALESCE(touch->>'code','') = ANY(%s)"
        params = [codes, codes]
    cur.execute(f'''
      WITH payment AS (
        SELECT user_id, bool_or(amount_cents > 0 AND status IN ('paid','completed')) AS paid,
          COALESCE(sum(amount_cents) FILTER (WHERE amount_cents > 0 AND status IN ('paid','completed') AND currency='USD'),0) AS usd_cents
        FROM payments GROUP BY user_id
      ), signup_touches AS (
        SELECT u.id, m.model, s.attribution->m.model AS touch, p.paid, p.usd_cents
        FROM users u LEFT JOIN website_signups s ON s.user_id=u.id
        LEFT JOIN payment p ON p.user_id=u.id
        CROSS JOIN (VALUES ('first'),('last')) AS m(model)
        WHERE u.is_verified=1 AND {scope}
      ), signup AS (
        SELECT model, COALESCE(touch->>'source','unknown') AS source,
          COALESCE(touch->>'medium','') AS medium, COALESCE(touch->>'campaign','') AS campaign,
          COALESCE(touch->>'code','') AS code, count(*) AS signups,
          count(*) FILTER (WHERE paid) AS paying_users, COALESCE(sum(usd_cents),0) AS revenue_usd_cents
        FROM signup_touches {code_filter} GROUP BY 1,2,3,4,5
      ), visit_touches AS (
        SELECT device_id, m.model, attribution->m.model AS touch
        FROM page_visits CROSS JOIN (VALUES ('first'),('last')) AS m(model)
        WHERE analytics_id IS NOT NULL AND attribution IS NOT NULL
      ), visits AS (
        SELECT model, COALESCE(touch->>'source','unknown') AS source,
          COALESCE(touch->>'medium','') AS medium, COALESCE(touch->>'campaign','') AS campaign,
          COALESCE(touch->>'code','') AS code, count(DISTINCT device_id) AS visitors
        FROM visit_touches {code_filter} GROUP BY 1,2,3,4,5
      )
      SELECT COALESCE(s.model,v.model) AS model, COALESCE(s.source,v.source) AS source,
        COALESCE(s.medium,v.medium) AS medium, COALESCE(s.campaign,v.campaign) AS campaign,
        COALESCE(s.code,v.code) AS code, COALESCE(v.visitors,0) AS visitors,
        COALESCE(s.signups,0) AS signups, COALESCE(s.paying_users,0) AS paying_users,
        COALESCE(s.revenue_usd_cents,0) AS revenue_usd_cents
      FROM signup s FULL JOIN visits v USING(model,source,medium,campaign,code)
      ORDER BY signups DESC, visitors DESC, model, source, code LIMIT 2001
    ''', params)
    rows = [dict(row) for row in cur.fetchall()]
    return {'rows': rows[:2000], 'truncated': len(rows)>2000, 'window_days': 30, 'version': 1}
