"""Bounded first-party measurement. Never accept form text, query strings or tokens."""
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from acquisition import clean_attribution
from admin_metrics.defs import ROBOT_UA_RE
from flask import current_app

ID = re.compile(r'^[a-zA-Z0-9_-]{8,64}$')
# One robot list for the write path and every admin report (admin_metrics).
BOT = ROBOT_UA_RE
TRACKING_VALUES = ('tracked', 'privacy_signal', 'no_identity')
SIGNUP_WRITE_FAILURES = {'count': 0}

# Columns from migration 031 are detected, not assumed: until the migration is
# applied by hand the beacon and signup writes behave exactly as before.
_SCHEMA_TTL_S = 300
_schema = {}
_schema_lock = threading.Lock()


def _schema_flags(cur):
    now = time.monotonic()
    with _schema_lock:
        hit = _schema.get('flags')
        if hit and now - hit[0] < _SCHEMA_TTL_S:
            return hit[1]
    cur.execute("""SELECT table_name, column_name, is_nullable
                     FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND ((table_name = 'page_visits'
                            AND column_name IN ('interacted', 'signed_in'))
                        OR (table_name = 'website_signups'
                            AND column_name IN ('tracking', 'device_id')))""")
    rows = {(r['table_name'], r['column_name']): r['is_nullable'] for r in cur.fetchall()}
    flags = {
        'interacted': ('page_visits', 'interacted') in rows,
        'signed_in': ('page_visits', 'signed_in') in rows,
        'signup_tracking': ('website_signups', 'tracking') in rows
                           and rows.get(('website_signups', 'device_id')) == 'YES',
    }
    with _schema_lock:
        _schema['flags'] = (now, flags)
    return flags


def reset_schema_cache():
    with _schema_lock:
        _schema.clear()
EVENTS = frozenset({
 'signup_cta', 'upload_cta', 'edit_cta', 'pricing_cta', 'google_start',
 'email_start', 'email_code_start', 'email_code_error', 'email_code_success', 'register_submit', 'register_success', 'register_error',
 'login_submit', 'login_success', 'login_error', 'verify_submit',
 'verify_error', 'verify_success', 'resend_code', 'resend_error',
 'google_error', 'google_success', 'form_started', 'form_invalid',
 # the proof-first funnel (landing results wall, before/after, demo replay,
 # onboarding examples): what visitors actually watch before signing up
 'proof_view', 'showcase_sound', 'before_after_toggle', 'demo_replay_start',
 'demo_replay_step', 'demo_replay_complete', 'starter_request_pick',
 'onboarding_example_view',
})

def identity(data):
    if not isinstance(data, dict):
        return None
    values = tuple(data.get(k) for k in ('device_id', 'session_id'))
    return values if all(isinstance(v, str) and ID.fullmatch(v) for v in values) else None

def bounded_number(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return max(0, min(maximum, int(value)))
    except (ValueError, OverflowError):
        return 0

def safe_path(value):
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//'):
        return '/[other]'
    value = value.split('?', 1)[0].split('#', 1)[0]
    value = re.sub(r'/(google-callback|reset-password|verify-email)/[^/]+', r'/\1/[redacted]', value)
    parts = value.split('/')
    return '/'.join(p if (re.fullmatch(r'[a-zA-Z0-9_-]{0,64}|\[redacted\]', p) and not p.isdigit()) else '[redacted]' for p in parts)[:240]

def clean_payload(data):
    ids = identity(data)
    if not ids:
        raise ValueError('invalid anonymous identity')
    try:
        visit = str(uuid.UUID(data.get('visit_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid visit identity')
    active = bounded_number(data.get('active_seconds'), 86400)
    events = []
    for item in (data.get('events') if isinstance(data.get('events'), list) else [])[:20]:
        if not isinstance(item, dict) or item.get('kind') not in EVENTS:
            continue
        try:
            event_id = str(uuid.UUID(item.get('id', '')))
        except (ValueError, TypeError, AttributeError):
            continue
        status = bounded_number(item.get('status'), 599)
        events.append((event_id, visit, item['kind'], min(active, bounded_number(item.get('active_seconds'), 86400)), status or None))
    try:
        host = urlsplit(str(data.get('referrer', ''))).hostname or ''
        host = host[:160] if re.fullmatch(r'[a-zA-Z0-9.-]*', host) else ''
    except ValueError:
        host = ''
    source = ('direct' if not host else 'internal' if host in ('valmera.io','www.valmera.io') else
              'search' if any(h in host for h in ('google.','bing.','duckduckgo.','yahoo.')) else
              'social' if any(host == h or host.endswith('.'+h) for h in ('tiktok.com','youtube.com','instagram.com','x.com','facebook.com','reddit.com','linkedin.com')) else 'referral')
    return dict(device_id=ids[0], session_id=ids[1], visit_id=visit,
                interacted=data.get('interacted') is True,
                signed_in=data.get('signed_in') is True,
                page=safe_path(data.get('page')), active=active,
                scroll=bounded_number(data.get('scroll_depth'), 100),
                reason=data.get('exit_reason') if data.get('exit_reason') in ('hidden','pagehide','navigation','heartbeat','event') else 'heartbeat',
                referrer=host, source=source, events=events, attribution=clean_attribution(data.get('attribution')))

def connect():
    return psycopg2.connect(current_app.config['DATABASE_URL'], connect_timeout=3,
                           options='-c statement_timeout=3000 -c lock_timeout=1000', cursor_factory=RealDictCursor)

def save_visit(data, user_agent):
    p = clean_payload(data)
    if BOT.search(user_agent):
        return False
    ua = user_agent.lower()
    device = 'tablet' if any(x in ua for x in ('ipad','tablet')) else 'mobile' if any(x in ua for x in ('mobile','iphone','android')) else 'desktop'
    browser = next((name for marker,name in [('edg/','Edge'),('opr/','Opera'),('crios/','Chrome'),('chrome/','Chrome'),('fxios/','Firefox'),('firefox/','Firefox'),('safari/','Safari')] if marker in ua), 'Other')
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            # Cumulative values make retries and out-of-order beacons idempotent.
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (p['device_id'],))
            cur.execute("SELECT count(*) AS n FROM page_visits WHERE device_id=%s AND visited_at > (now() AT TIME ZONE 'UTC')-interval '1 minute'", (p['device_id'],))
            if cur.fetchone()['n'] >= 60:
                return False
            flags = _schema_flags(cur)
            # interacted/signed_in only ever turn on: a later beacon from an idle
            # tab can never undo the scroll or click an earlier one reported.
            extra_cols = ''.join(f',{c}' for c in ('interacted', 'signed_in') if flags[c])
            extra_vals = ''.join(',%s' for c in ('interacted', 'signed_in') if flags[c])
            extra_set = ''.join(f',{c}=page_visits.{c} OR EXCLUDED.{c}'
                                for c in ('interacted', 'signed_in') if flags[c])
            extra_params = tuple(p[c] for c in ('interacted', 'signed_in') if flags[c])
            cur.execute(f'''INSERT INTO page_visits
                (analytics_id,page,device_id,session_id,ip,user_agent,country,referrer,
                 referrer_source,device_type,browser,time_on_page,last_seen_at,scroll_depth,exit_reason,attribution{extra_cols})
                VALUES (%s,%s,%s,%s,NULL,%s,'Unknown',%s,%s,%s,%s,%s,now() AT TIME ZONE 'UTC',%s,%s,%s{extra_vals})
                ON CONFLICT (analytics_id) DO UPDATE SET
                  time_on_page=GREATEST(page_visits.time_on_page,EXCLUDED.time_on_page),
                  scroll_depth=GREATEST(page_visits.scroll_depth,EXCLUDED.scroll_depth),
                  last_seen_at=EXCLUDED.last_seen_at,exit_reason=EXCLUDED.exit_reason{extra_set}
                WHERE page_visits.device_id=EXCLUDED.device_id AND page_visits.session_id=EXCLUDED.session_id
                RETURNING analytics_id''', (p['visit_id'],p['page'],p['device_id'],p['session_id'],user_agent[:300],p['referrer'],p['source'],device,browser,p['active'],p['scroll'],p['reason'],Json(p['attribution']),*extra_params))
            if not cur.fetchone():
                return False
            cur.execute('SELECT count(*) AS n FROM website_events WHERE visit_id=%s', (p['visit_id'],))
            room = max(0, 100-cur.fetchone()['n'])
            for row in p['events'][:room]:
                cur.execute('''INSERT INTO website_events(id,visit_id,kind,active_seconds,status)
                               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING''', row)
        return True
    finally:
        conn.close()

def signup_tracking(data):
    """What the browser sent at signup: tracked ids, a privacy signal, or nothing."""
    if identity(data):
        return 'tracked'
    if isinstance(data, dict) and data.get('tracking') == 'privacy_signal':
        return 'privacy_signal'
    return 'no_identity'


def record_signup(user_id, data):
    """Called ONLY by successful first verification / new Google exchange.

    Once migration 031 is applied, ALWAYS writes exactly one row per new
    verified account, so "where did they come from" is never silently empty:
    tracking = tracked | privacy_signal | no_identity. Privacy-signal browsers
    store no ids and no labels (privacy policy: we honour DNT/GPC). Before the
    migration it keeps the old behaviour (a row only when ids were sent).
    """
    ids = identity(data)
    tracking = signup_tracking(data)
    conn = None
    try:
        conn = connect()
        with conn, conn.cursor() as cur:
            if _schema_flags(cur)['signup_tracking']:
                attribution = Json(clean_attribution(data.get('attribution'))) if ids else None
                cur.execute('''INSERT INTO website_signups(user_id,device_id,session_id,attribution,tracking)
                               SELECT id,%s,%s,%s,%s FROM users WHERE id=%s AND is_verified=1
                               ON CONFLICT(user_id) DO NOTHING''',
                            (ids[0] if ids else None, ids[1] if ids else None,
                             attribution, tracking, user_id))
            elif ids:
                cur.execute('''INSERT INTO website_signups(user_id,device_id,session_id,attribution)
                               SELECT id,%s,%s,%s FROM users WHERE id=%s AND is_verified=1
                               ON CONFLICT(user_id) DO NOTHING''', (*ids, Json(clean_attribution(data.get('attribution'))), user_id))
    except Exception:
        # Never fail a signup over measurement, but never hide the loss either.
        SIGNUP_WRITE_FAILURES['count'] += 1
        current_app.logger.exception('Signup source could not be saved (%s failures since start)',
                                     SIGNUP_WRITE_FAILURES['count'])
    finally:
        if conn is not None:
            conn.close()
