"""Explicit output counts must be backed by separate, current playable edits."""
import re

_NUMBERS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,
            'eight':8,'nine':9,'ten':10}
_REQUEST = re.compile(
    r'\b(?:create|make|produce|deliver|generate|export)\s+'
    r'(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+'
    r'(?:(?:separate|short|vertical|horizontal|different|individual|standalone|'
    r'ugc(?:-style)?|9:16|16:9)\s+){0,7}(?:videos?|reels?|shorts|clips?)\b', re.I)


def requested_video_count(text):
    matches=list(_REQUEST.finditer(text or ''))
    if not matches: return 1
    value=matches[-1].group(1).lower()
    return max(1,int(value) if value.isdigit() else _NUMBERS[value])


def _ready_projects(conn, project_id, user_id):
    with conn.cursor() as cur:
        cur.execute('''SELECT COUNT(*) AS ready FROM projects p
          JOIN LATERAL (SELECT version FROM edls WHERE project_id=p.id
                        ORDER BY version DESC LIMIT 1) e ON TRUE
          WHERE p.user_id=%s AND (p.id=%s OR p.parent_project_id=%s)
            AND EXISTS (SELECT 1 FROM assets a WHERE a.project_id=p.id
              AND a.kind='render' AND a.meta->>'variant' IN ('preview','final')
              AND a.meta->>'edl_version'=e.version::text)
            AND EXISTS (SELECT 1 FROM verification_records v
              WHERE v.project_id=p.id AND v.edl_version=e.version
                AND v.status IN ('passed','justified'))''',
            (user_id, project_id, project_id))
        return int((cur.fetchone() or {}).get('ready') or 0)


def delivery_requirement(ctx):
    required=requested_video_count(getattr(ctx,'verification_request',None)
                                   or getattr(ctx,'user_message',''))
    if required <= 1: return None
    try:
        ready=ctx.db.run(_ready_projects,ctx.project_id,ctx.project['user_id'])
    except Exception:
        # Missing evidence is never evidence that five videos were delivered.
        ready=0
    return {'requested_videos':required,'playable_projects':ready,
            'complete':ready>=required}


def disclose(ctx, text):
    delivery = getattr(ctx, 'delivery_requirement', None)
    if not delivery or delivery['complete']:
        return text
    return (text + f"\n\nDelivery is incomplete: {delivery['playable_projects']} of "
            f"{delivery['requested_videos']} requested separate videos have a "
            "current preview that passed verification. The saved work remains "
            "available; the full set has not been delivered.")
