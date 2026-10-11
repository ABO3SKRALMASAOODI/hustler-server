"""First/latest acquisition labels. CRM identities never enter this database.

`clean_touch` bounds what the browser may store; `channel` is the ONE
classifier every admin report and the CRM endpoint use to turn a stored touch
into a channel and detail (plan §4.3). Stored data is never rewritten for
classification: history relabels itself at read time.
"""
import math
import re
import time

LABEL = re.compile(r'^[a-zA-Z0-9_.-]{1,80}$')
CODE = re.compile(r'^[A-Za-z0-9_-]{16,32}$')
TTL_MS = 30 * 86400 * 1000

# Outreach links without a campaign tag (and every link stored before the
# CRM tagged campaigns) are reported together as "Untagged".
UNTAGGED_CAMPAIGNS = {'', 'outreach_untagged', 'instagram_outreach'}


def clean_touch(value, now=None):
    if not isinstance(value, dict):
        return None
    now = time.time() * 1000 if now is None else now
    at = value.get('at')
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        return None
    # A browser whose clock runs fast must not lose its source: clamp to the
    # server's now instead of rejecting. Stale touches still expire.
    at = min(at, now)
    if now - at > TTL_MS:
        return None

    def label(key):
        v = value.get(key)
        return v if isinstance(v, str) and LABEL.fullmatch(v) else ''
    code = value.get('code')
    code = code if isinstance(code, str) and CODE.fullmatch(code) else ''
    source = label('source')
    if not source and not code:
        return None
    result = dict(source=source, medium=label('medium'), campaign=label('campaign'),
                  content=label('content'), code=code, at=int(at))
    if code:
        # Keep whatever the link supplied; only default what is missing.
        result['source'] = result['source'] or 'instagram'
        result['medium'] = result['medium'] or 'outreach'
        result['campaign'] = result['campaign'] or 'outreach_untagged'
    return result


def clean_attribution(value):
    value = value if isinstance(value, dict) else {}
    return {key: clean_touch(value.get(key)) for key in ('first', 'last')}


# ── The channel classifier (plan §4.3, first match wins) ─────────────────
CHANNELS = (
    ('search', 'Search'),
    ('ai_assistant', 'AI assistants'),
    ('outreach', 'Outreach'),
    ('social', 'Social'),
    ('email', 'Email'),
    ('other_website', 'Other websites'),
    ('no_referrer', 'No referrer'),
    ('not_recorded', 'Not recorded'),
)
CHANNEL_LABELS = dict(CHANNELS)
CHANNEL_ORDER = {key: i + 1 for i, (key, _) in enumerate(CHANNELS)}

NOT_RECORDED_REASONS = (
    ('before_tracking', 'Before tracking'),
    ('privacy_browser', 'Privacy browser (not tracked by design)'),
    ('nothing_sent', 'Browser sent nothing'),
    ('landing_lost', 'Landing page not saved'),
    ('no_row_unknown', 'No record (reason not saved)'),
)
REASON_LABELS = dict(NOT_RECORDED_REASONS)

# (domain or label, detail key). A domain matches itself and subdomains.
AI_ASSISTANTS = (
    ('chatgpt.com', 'chatgpt'), ('chat.openai.com', 'chatgpt'),
    ('openai.com', 'chatgpt'), ('openai', 'chatgpt'), ('chatgpt', 'chatgpt'),
    ('claude.ai', 'claude'), ('claude', 'claude'),
    ('gemini.google.com', 'gemini'), ('bard.google.com', 'gemini'),
    ('gemini', 'gemini'),
    # Google's AI tools are not Google Search (checked before Search).
    ('notebooklm.google.com', 'notebooklm'), ('notebooklm', 'notebooklm'),
    ('aistudio.google.com', 'aistudio'),
    ('perplexity.ai', 'perplexity'), ('perplexity', 'perplexity'),
    ('copilot.microsoft.com', 'copilot'), ('copilot', 'copilot'),
    ('chat.deepseek.com', 'deepseek'), ('deepseek.com', 'deepseek'),
    ('deepseek', 'deepseek'),
    ('grok.com', 'grok'), ('x.ai', 'grok'), ('grok', 'grok'),
    ('meta.ai', 'meta_ai'),
    ('chat.mistral.ai', 'mistral'), ('mistral.ai', 'mistral'),
    ('poe.com', 'poe'), ('you.com', 'you'),
    ('mcp', 'mcp'),
)
AI_LABELS = {'chatgpt': 'ChatGPT', 'claude': 'Claude', 'gemini': 'Gemini',
             'perplexity': 'Perplexity', 'copilot': 'Copilot',
             'deepseek': 'DeepSeek', 'grok': 'Grok', 'meta_ai': 'Meta AI',
             'mistral': 'Mistral', 'poe': 'Poe', 'you': 'You.com',
             'notebooklm': 'NotebookLM', 'aistudio': 'Google AI Studio',
             'mcp': 'Connector (MCP)'}

EMAIL_HOSTS = (('mail.google.com', 'gmail'), ('outlook.live.com', 'outlook'),
               ('outlook.office.com', 'outlook'),
               ('outlook.office365.com', 'outlook'),
               ('mail.yahoo.com', 'yahoo_mail'))
EMAIL_SOURCES = {'newsletter': 'newsletter', 'brevo': 'valmera_email',
                 'email': 'email', 'gmail': 'gmail', 'outlook': 'outlook'}
EMAIL_LABELS = {'gmail': 'Gmail', 'outlook': 'Outlook',
                'yahoo_mail': 'Yahoo Mail', 'newsletter': 'Newsletter',
                'valmera_email': 'Valmera email', 'email': 'Email'}

GOOGLE_NOT_SEARCH = ('gemini.', 'mail.', 'docs.', 'drive.', 'accounts.',
                     'classroom.', 'sites.', 'bard.')
GOOGLE_HOST = re.compile(r'^(?:[a-z0-9-]+\.)*google\.(?:[a-z]{2,3})(?:\.[a-z]{2})?$')
YANDEX_HOST = re.compile(r'^(?:[a-z0-9-]+\.)*yandex\.(?:[a-z]{2,3})(?:\.[a-z]{2})?$')
SEARCH = (('bing.com', 'bing'), ('duckduckgo.com', 'duckduckgo'),
          ('search.yahoo.com', 'yahoo'), ('baidu.com', 'baidu'),
          ('search.brave.com', 'brave'), ('ecosia.org', 'ecosia'),
          ('startpage.com', 'startpage'))
SEARCH_LABELS = {'google': 'Google', 'bing': 'Bing', 'duckduckgo': 'DuckDuckGo',
                 'yahoo': 'Yahoo', 'yandex': 'Yandex', 'baidu': 'Baidu',
                 'brave': 'Brave Search', 'ecosia': 'Ecosia',
                 'startpage': 'Startpage'}

SOCIAL = (('instagram.com', 'instagram'), ('facebook.com', 'facebook'),
          ('fb.com', 'facebook'), ('tiktok.com', 'tiktok'),
          ('youtube.com', 'youtube'), ('youtu.be', 'youtube'),
          ('x.com', 'x'), ('twitter.com', 'x'), ('t.co', 'x'),
          ('reddit.com', 'reddit'), ('linkedin.com', 'linkedin'),
          ('lnkd.in', 'linkedin'), ('threads.net', 'threads'),
          ('threads.com', 'threads'), ('pinterest.com', 'pinterest'),
          ('pin.it', 'pinterest'), ('snapchat.com', 'snapchat'),
          ('t.me', 'telegram'), ('telegram.org', 'telegram'),
          ('telegram.me', 'telegram'), ('whatsapp.com', 'whatsapp'),
          ('wa.me', 'whatsapp'), ('discord.com', 'discord'),
          ('discord.gg', 'discord'))
SOCIAL_LABELS = {'instagram': 'Instagram', 'facebook': 'Facebook',
                 'tiktok': 'TikTok', 'youtube': 'YouTube', 'x': 'X',
                 'reddit': 'Reddit', 'linkedin': 'LinkedIn',
                 'threads': 'Threads', 'pinterest': 'Pinterest',
                 'snapchat': 'Snapchat', 'telegram': 'Telegram',
                 'whatsapp': 'WhatsApp', 'discord': 'Discord'}
# Short utm_source names apps add themselves. Instagram tags every bio link
# `utm_source=ig&utm_medium=social&utm_content=link_in_bio`, which would
# otherwise read as a website called "ig".
SOCIAL_ALIASES = {'twitter': 'x', 'fb': 'facebook', 'ig': 'instagram',
                  'insta': 'instagram', 'yt': 'youtube'}
SOCIAL_NAMES = set(SOCIAL_LABELS) | set(SOCIAL_ALIASES)
# A link tagged utm_medium=social from a network not listed above is still
# social (e.g. utm_source=bluesky&utm_medium=social).
SOCIAL_MEDIA = {'social', 'social_media', 'social-media', 'socialmedia',
                'social_network', 'social-network', 'sm'}
PAID_MEDIA = {'cpc', 'ppc', 'paid', 'ads', 'paid_social', 'paidsocial'}
OWN_HOSTS = ('valmera.io',)
# Steps inside sign-in or checkout, never where someone came from: Google's
# consent screen links to /legal, a failed Google sign-in returns to /login,
# and Paddle's checkout can send the buyer back. A touch from one of these is
# a landing that was not saved, like a touch from valmera.io itself.
NOT_A_SOURCE_HOSTS = ('accounts.google.com', 'accounts.youtube.com',
                      'appleid.apple.com', 'paddle.com', 'paddle.io')


def _host(value):
    v = (value or '').strip().lower()
    if '://' in v:
        v = v.split('://', 1)[1]
    v = v.split('/', 1)[0].split(':', 1)[0]
    return v[4:] if v.startswith('www.') else v


def _is(host, domain):
    return host == domain or host.endswith('.' + domain)


def campaign_label(campaign):
    c = (campaign or '').strip()
    if c.lower() in UNTAGGED_CAMPAIGNS:
        return 'Untagged'
    words = re.sub(r'[_\-.]+', ' ', c).strip()
    return (words[:1].upper() + words[1:]) if words else 'Untagged'


def _result(channel, detail=None, detail_label=None, **extra):
    out = {'channel': channel, 'channel_label': CHANNEL_LABELS[channel],
           'detail': detail, 'detail_label': detail_label,
           'campaign': None, 'campaign_label': None, 'network': None}
    out.update(extra)
    return out


def channel(touch, first_page=None):
    """Classify one stored touch (plan §4.3). Never raises.

    Returns {channel, channel_label, detail, detail_label, campaign,
    campaign_label, network}. A missing touch is 'not_recorded'; the caller
    supplies the reason because only it knows why (§4.4).
    """
    if not isinstance(touch, dict):
        return _result('not_recorded')
    source = str(touch.get('source') or '').strip().lower()
    medium = str(touch.get('medium') or '').strip().lower()
    campaign = str(touch.get('campaign') or '').strip()
    content = str(touch.get('content') or '').strip().lower()
    code = str(touch.get('code') or '').strip()
    host = _host(source)
    paid = medium in PAID_MEDIA

    # 1. Outreach: a recipient code, or a link tagged as outreach/DM.
    if code or medium in ('outreach', 'dm'):
        network = host or 'instagram'
        network = SOCIAL_ALIASES.get(network, network)
        key = '' if campaign.lower() in UNTAGGED_CAMPAIGNS else campaign
        return _result('outreach', key or 'untagged', campaign_label(campaign),
                       campaign=key or 'untagged',
                       campaign_label=campaign_label(campaign),
                       network=SOCIAL_LABELS.get(network, network.title()))
    if not source:
        return _result('not_recorded')
    if any(_is(host, own) for own in OWN_HOSTS + NOT_A_SOURCE_HOSTS):
        return _result('not_recorded')

    # 2. Email.
    if medium == 'email' or source in EMAIL_SOURCES:
        key = EMAIL_SOURCES.get(source)
        if campaign and campaign.lower() not in UNTAGGED_CAMPAIGNS:
            return _result('email', campaign, campaign_label(campaign),
                           campaign=campaign,
                           campaign_label=campaign_label(campaign))
        key = key or (host if host else 'email')
        return _result('email', key, EMAIL_LABELS.get(key, key))
    for domain, key in EMAIL_HOSTS:
        if _is(host, domain):
            return _result('email', key, EMAIL_LABELS[key])

    # 3. AI assistants, before Search (gemini.google.com is not search). A
    # first page of /mcp/authorize is an AI app adding Valmera as a connector:
    # named after the assistant when it sent a referrer, else "Connector".
    connector = (first_page or '').startswith('/mcp/authorize')
    for domain, key in AI_ASSISTANTS:
        if _is(host, domain) or source == domain:
            if connector and key != 'mcp':
                return _result('ai_assistant', key,
                               f'{AI_LABELS[key]} (connector)')
            return _result('ai_assistant', key, AI_LABELS[key])
    if connector and source in ('direct', '(direct)', 'none', ''):
        return _result('ai_assistant', 'mcp', AI_LABELS['mcp'])

    # 4. Search.
    engine = None
    if source == 'google' or (GOOGLE_HOST.match(host)
                              and not host.startswith(GOOGLE_NOT_SEARCH)):
        engine = 'google'
    elif source == 'yandex' or YANDEX_HOST.match(host):
        engine = 'yandex'
    elif source in SEARCH_LABELS:
        engine = source
    else:
        for domain, key in SEARCH:
            if _is(host, domain):
                engine = key
                break
    if engine:
        if paid:
            label = 'Google Ads (paid)' if engine == 'google' \
                else f'{SEARCH_LABELS[engine]} (paid)'
            return _result('search', engine + '_paid', label)
        return _result('search', engine, SEARCH_LABELS[engine])

    # 5. Social (hosts, in-app browser labels, Android app packages).
    platform = None
    if source in SOCIAL_NAMES:
        platform = SOCIAL_ALIASES.get(source, source)
    else:
        for domain, key in SOCIAL:
            if _is(host, domain):
                platform = key
                break
    if platform:
        label = SOCIAL_LABELS[platform]
        if paid:
            return _result('social', platform + '_paid', f'{label} (paid)')
        if content == 'in_app':
            return _result('social', platform, f'{label} (in-app)')
        return _result('social', platform, label)
    if medium in SOCIAL_MEDIA and source not in ('direct', '(direct)', 'none'):
        name = host or source
        label = name if '.' in name else name.replace('_', ' ').title()
        return _result('social', name, label)

    # 7. No referrer (checked before "other" so 'direct' is never a host).
    if source in ('direct', '(direct)', 'none'):
        return _result('no_referrer', 'direct', 'No referrer')

    # 6. Any other website or utm_source.
    return _result('other_website', host or source, host or source)


def not_recorded(reason):
    out = _result('not_recorded')
    out.update(reason=reason, reason_label=REASON_LABELS.get(reason))
    return out


def report(cur, scope, codes=None, group_by="link", with_people=False):
    if group_by not in {"link", "source", "all"}:
        raise ValueError("Invalid acquisition grouping")
    source = "COALESCE(touch->>'source','unknown')" if group_by != "all" else "'all'::text"
    medium = "COALESCE(touch->>'medium','')" if group_by != "all" else "''::text"
    campaign = "COALESCE(touch->>'campaign','')" if group_by == "link" else "''::text"
    code = "COALESCE(touch->>'code','')" if group_by == "link" else "''::text"
    # Payment totals are reduced to one row per user before joining signups.
    # Browser labels describe acquisition; they never establish user identity.
    params = []
    code_filter = ''
    if codes is not None:
        code_filter = "WHERE COALESCE(touch->>'code','') = ANY(%s)"
        params = [codes, codes]
    people_cte, people_cols, people_join = '', '', ''
    if with_people:
        # The CRM's "visitors" counted Meta's link-preview robots as people.
        # Classify every browser with the admin's visitor rules (G8) so each
        # row also says how many were people and how many were previews.
        from admin_metrics import db, defs
        from admin_metrics.visitors import CLASS_SQL, internal_ids
        interacted = ('bool_or(COALESCE(pv.interacted, FALSE))'
                      if db.has_column(cur, 'page_visits', 'interacted')
                      else 'FALSE')
        people_cte = f''', d AS (
        SELECT pv.device_id,
          bool_or(COALESCE(pv.user_agent,'') ~* %s) AS robot,
          count(*) AS pages,
          max(COALESCE(pv.scroll_depth,0)) AS scroll,
          max(COALESCE(pv.time_on_page,0)) AS active_s,
          {interacted} AS interacted,
          bool_or(EXISTS (SELECT 1 FROM website_events e WHERE e.visit_id=pv.analytics_id)) AS clicked,
          bool_and((COALESCE(pv.attribution->'first'->>'code','') <> ''
                    OR COALESCE(pv.attribution->'last'->>'code','') <> '')
                   AND COALESCE(pv.referrer,'') = ANY(%s)) AS preview_shape,
          -- The owner's own browsers are never people (same rule as the
          -- admin's outreach report).
          bool_or(pv.device_id = ANY(%s)) AS internal
        FROM page_visits pv
        WHERE pv.analytics_id IS NOT NULL
          AND pv.device_id IN (SELECT device_id FROM visit_touches)
        GROUP BY pv.device_id
      ), device_class AS (SELECT d.device_id, {CLASS_SQL} AS cls FROM d)'''
        people_cols = ''',
          count(DISTINCT vt.device_id) FILTER (WHERE dc.cls='person') AS people,
          count(DISTINCT vt.device_id) FILTER (WHERE dc.cls='link_preview') AS link_previews'''
        people_join = 'LEFT JOIN device_class dc ON dc.device_id=vt.device_id'
        classify = [defs.ROBOT_UA, list(defs.PREVIEW_REFERRERS),
                    list(internal_ids(cur))]
        params = params[:1] + classify + params[1:] if codes is not None \
            else classify
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
        SELECT model, {source} AS source,
          {medium} AS medium, {campaign} AS campaign,
          {code} AS code, count(*) AS signups,
          count(*) FILTER (WHERE paid) AS paying_users, COALESCE(sum(usd_cents),0) AS revenue_usd_cents
        FROM signup_touches {code_filter} GROUP BY 1,2,3,4,5
      ), visit_touches AS (
        SELECT device_id, m.model, attribution->m.model AS touch
        FROM page_visits CROSS JOIN (VALUES ('first'),('last')) AS m(model)
        WHERE analytics_id IS NOT NULL AND attribution IS NOT NULL
      ){people_cte}, visits AS (
        SELECT model, {source} AS source,
          {medium} AS medium, {campaign} AS campaign,
          {code} AS code, count(DISTINCT vt.device_id) AS visitors{people_cols}
        FROM visit_touches vt {people_join} {code_filter} GROUP BY 1,2,3,4,5
      )
      SELECT COALESCE(s.model,v.model) AS model, COALESCE(s.source,v.source) AS source,
        COALESCE(s.medium,v.medium) AS medium, COALESCE(s.campaign,v.campaign) AS campaign,
        COALESCE(s.code,v.code) AS code, COALESCE(v.visitors,0) AS visitors,
        {"COALESCE(v.people,0) AS people, COALESCE(v.link_previews,0) AS link_previews," if with_people else ""}
        COALESCE(s.signups,0) AS signups, COALESCE(s.paying_users,0) AS paying_users,
        COALESCE(s.revenue_usd_cents,0) AS revenue_usd_cents
      FROM signup s FULL JOIN visits v USING(model,source,medium,campaign,code)
      ORDER BY signups DESC, visitors DESC, model, source, code LIMIT 2001
    ''', params)
    rows = [dict(row) for row in cur.fetchall()]
    if with_people:
        for row in rows:
            touch = {'source': row['source'] if row['source'] != 'unknown' else '',
                     'medium': row['medium'], 'campaign': row['campaign'],
                     'code': row['code']}
            c = channel(touch) if touch['source'] or touch['code'] else not_recorded(None)
            row['channel'] = c['channel']
            row['campaign_label'] = c['campaign_label']
    return {'rows': rows[:2000], 'truncated': len(rows)>2000, 'window_days': 30,
            'version': 2 if with_people else 1}
