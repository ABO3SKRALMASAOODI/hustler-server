"""Refusal storms (owner, 7 Nov 2026: "issues needs to be handled faster than i
realise them ... nor issues neither latency in handling them is accepted").

A refused tool call writes no failure row, so the intake learned about one only
when the request ended. Project 5520 took 54 refusals over 2 h 36 min, one
paying customer's whole 1,500 credits, and the first thing filed was a request
that ended partial at 16:43, 16 minutes after set_junction first said no, on a
ticket that already held 30 other customers' failures.

This reads refusals as they land, while the turn is still running, on any plan.
The measure is the same tool refused again with no success of that tool between,
on one project, because a customer who asks for the colours back in the next
request and is refused again is the strongest sign there is. What the tool said
only names the cause, one ticket however many tools say it: no customer words are
read, and the status comes from the tool's own outcome. Nothing here knows which refusals are "right": one the model
corrects ends its streak with a success, and that is the whole test.

Back-tested on the 15 days to 7 Nov, 59 customer Studio requests with a refusal,
of which 31 (53%) went undone:

  twice in a row   37 requests, 25 undone (68%), 2.5 a day, a median 35 s after
                   the first refusal: a ticket, at P1, within the minute
  three times      24 requests, 18 undone (75%), 1.6 a day: the request is going
                   undone, P0, and the watch starts a Fixer
  twice, and the   25 requests, all undone by definition: P0 at once, without
  request ended    the 10 minutes a request's own ticket waits for a second look
  short

Requests that ended fulfilled keep their ticket: the model got round a refusal
it should never have met, and the cause is still fixed.

An operator's repair turn on a customer's project stays at P1: nobody is waiting
on it (intake.OPERATOR_REPAIR).

MCP has no request or project on its error rows, so its streak is one account
and one tool within MCP_WINDOW, read from the public error responses, which also
cover what the gateway answers without a job. The assistant may simply correct
itself there, so the third refusal goes through the same second look as any MCP
error (recovery._mcp). A response the intake already files as ours is not counted
again here.
"""
import hashlib
from collections import defaultdict
from datetime import timedelta

TICKET_AT = 2                   # refused in a row: a ticket, within the minute
UNDONE_AT = 3                   # the request is going undone: P0
# A streak older than this is another sitting. 5520's longest gap between two
# refusals of one tool was 1 h 44 min (set_junction).
STREAK_WINDOW = timedelta(hours=6)
# An assistant's retries come minutes apart; a day later it is another job.
MCP_WINDOW = timedelta(minutes=30)
SUCCESS = "success"
_REFUSED = "COALESCE(m.meta->'tool_outcome'->>'status', 'success') <> 'success'"
_AGENT = "m.role = 'activity' AND m.meta->>'source' = 'agent'"


def _i():
    from ops_loop import intake
    return intake


def scan(cur, since, now, p):
    return _studio(cur, since, now, p) + _mcp(cur, since, now, p)


def said(text):
    """The refusal sentence, one line. Never what came before the arrow: that is
    the call's arguments, which can hold the customer's words."""
    return " ".join((text or "").split())[:300]


def _cause(text):
    """One ticket per cause: the refusal with its ids and numbers out, whichever
    tool said it and in whichever project. "The bounded edit/review cycle has
    stopped" came from 8 tools in 5520 and is one gate. Not intake.signature: its
    'oom' class matches 'zoom', and a refusal is our own sentence, so there is
    nothing to classify. The words before the first colon are only its status."""
    i = _i()
    norm = i.normalise(text)
    return i.cause_key("refusal", norm.split(":", 1)[-1],
                       hashlib.sha1(norm.encode()).hexdigest()[:8])


def _times(n):
    return {2: "twice"}.get(n, f"{n} times")


# ── Studio ───────────────────────────────────────────────────────────────

def _projects(cur, p):
    """The projects with a refusal in the window, and the requests that ended
    short in it (a streak from earlier in that request is raised by the end)."""
    i = _i()
    cur.execute(f"""
        SELECT DISTINCT pr.id AS project_id, pr.user_id, pr.chat_session_id
          FROM chat_messages m
          JOIN projects pr ON pr.chat_session_id = m.session_id
          JOIN users u ON u.id = pr.user_id
         WHERE m.created_at > %(since_naive)s AND m.created_at <= %(now_naive)s
           AND {_AGENT} AND {_REFUSED} AND {i._customers()}
         LIMIT %(limit)s""", p)
    projects = {r["project_id"]: dict(r) for r in cur.fetchall()}
    cur.execute(f"""
        SELECT j.id, j.project_id, j.user_id, j.updated_at AS at,
               pr.chat_session_id
          FROM video_jobs j JOIN users u ON u.id = j.user_id
          JOIN projects pr ON pr.id = j.project_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND {i.ENDED_SHORT} AND {i._customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    ended = defaultdict(list)
    for r in cur.fetchall():
        ended[r["project_id"]].append(r)
        projects.setdefault(r["project_id"], {
            "project_id": r["project_id"], "user_id": r["user_id"],
            "chat_session_id": r["chat_session_id"]})
    return projects, ended


def _activity(cur, projects, p):
    """Every agent tool call of these projects in the streak window, oldest
    first, with what a refused one said."""
    i = _i()
    sessions = sorted({x["chat_session_id"] for x in projects.values()})
    frm = i._aware(p["since"]) - STREAK_WINDOW
    cur.execute(f"""
        SELECT m.id, m.session_id, m.created_at AS at, m.meta->>'tool' AS tool,
               COALESCE(m.meta->'tool_outcome'->>'status', 'success') AS status,
               CASE WHEN {_REFUSED} AND position('→' in m.content) > 0
                    THEN substring(m.content from
                                   position('→' in m.content) + 1 for 400)
               END AS said
          FROM chat_messages m
         WHERE m.session_id = ANY(%s) AND {_AGENT}
           AND m.created_at > %s AND m.created_at <= %s
         ORDER BY m.id""", (sessions, i._naive(frm), p["now_naive"]))
    rows = defaultdict(list)
    for r in cur.fetchall():
        if r["tool"]:
            rows[r["session_id"]].append(dict(r, at=i._aware(r["at"])))
    return rows


def _turns(cur, projects, p):
    """The turns of these projects, oldest first: which request a refusal
    belongs to, and whether a customer asked for it."""
    i = _i()
    cur.execute(f"""
        SELECT j.id, j.project_id, j.created_at,
               COALESCE(j.payload->>'root_agent_job_id', j.id::text) AS root,
               {i.OPERATOR_REPAIR} AS operator_repair
          FROM video_jobs j
         WHERE j.project_id = ANY(%s) AND j.type = 'agent_turn'
           AND j.created_at <= %s AND j.updated_at > %s
         ORDER BY j.created_at, j.id""",
                (sorted(projects), p["now"],
                 i._aware(p["since"]) - STREAK_WINDOW))
    turns = defaultdict(list)
    for r in cur.fetchall():
        turns[r["project_id"]].append(dict(r, created_at=i._aware(r["created_at"])))
    return turns


def _turn_at(turns, at):
    """The turn running at `at`: the last one started by then."""
    found = None
    for t in turns:
        if t["created_at"] > at:
            break
        found = t
    return found


def _item(project, streak, turn, at, ref, severity, ended=False):
    i = _i()
    last, n = streak[-1], len(streak)
    tool = last["tool"]
    text = said(last.get("said")) or last["status"]
    how = f"{tool} refused {_times(n)} in a row"
    if ended:
        how += " and the request ended short"
    item = {"ref": ref, "source": "refusal", "at": at, "tool": tool,
            "user_id": project["user_id"], "project_id": project["project_id"],
            "severity": severity,
            "cause_key": _cause(last.get("said") or last["status"]),
            "title": i._short(f"{how}: {text}"),
            "detail": i._short(f"{how}, no success of it since "
                               f"{streak[0]['at']:%H:%M:%S} UTC · "
                               f"{last['status']} · {text}", 600)}
    if turn:
        item["job_id"] = turn["id"]
    return item


def _walk(project, rows, turns, ended, since, now):
    """One project's calls and ended requests in order: every threshold crossed
    in (since, now], each seen once, so a replay of a past window answers what
    would have been filed."""
    i = _i()
    since, now = i._aware(since), i._aware(now)
    events = [(r["at"], 0, r) for r in rows]
    events += [(i._aware(e["at"]), 1, e) for e in ended]
    events.sort(key=lambda x: x[:2])
    streaks, out = {}, []
    for at, is_end, row in events:
        if is_end:
            done = _turn_at(turns, at)
            root = done["root"] if done else str(row["id"])
            for tool, streak in streaks.items():
                last = _turn_at(turns, streak[-1]["at"])
                if len(streak) < TICKET_AT or not last or last["root"] != root:
                    continue
                severity = "P1" if last["operator_repair"] else "P0"
                out.append(_item(project, streak, last, at,
                                 f"refusal:{streak[0]['id']}:ended:{row['id']}",
                                 severity, ended=True))
            continue
        tool = row["tool"]
        if row["status"] == SUCCESS:
            streaks.pop(tool, None)
            continue
        streak = [r for r in streaks.get(tool, [])
                  if at - r["at"] <= STREAK_WINDOW] + [row]
        streaks[tool] = streak
        if len(streak) not in (TICKET_AT, UNDONE_AT) or \
                not since < at <= now:
            continue
        turn = _turn_at(turns, at)
        severity = "P0" if len(streak) >= UNDONE_AT and not (
            turn and turn["operator_repair"]) else "P1"
        out.append(_item(project, streak, turn, at,
                         f"refusal:{streak[0]['id']}:{len(streak)}", severity))
    return out


def _studio(cur, since, now, p):
    projects, ended = _projects(cur, p)
    if not projects:
        return []
    rows = _activity(cur, projects, p)
    turns = _turns(cur, projects, p)
    out = []
    for pid, project in projects.items():
        out.extend(_walk(project, rows.get(project["chat_session_id"], []),
                         turns.get(pid, []), ended.get(pid, []), since, now))
    return out


# ── MCP ──────────────────────────────────────────────────────────────────

def _mcp(cur, since, now, p):
    i = _i()
    since, now = i._aware(since), i._aware(now)
    q = dict(p, frm=since - MCP_WINDOW)
    cur.execute(f"""
        SELECT ce.id, ce.kind, ce.user_id, ce.project_id, ce.created_at AS at,
               ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = 'mcp_error_response'
           AND ce.created_at > %(frm)s AND ce.created_at <= %(now)s
           AND {i._customers()}
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    refused = [dict(r, at=i._aware(r["at"])) for r in cur.fetchall()
               if not i._event_failure(r)]
    if not any(r["at"] > since for r in refused):
        return []
    cur.execute("""
        SELECT user_id, payload->>'tool' AS tool, updated_at AS at
          FROM video_jobs
         WHERE user_id = ANY(%(users)s) AND type = 'mcp_tool' AND state = 'done'
           AND updated_at > %(frm)s AND updated_at <= %(now)s
           AND COALESCE(result->'tool_outcome'->>'status', 'success') = 'success'
    """, dict(q, users=sorted({r["user_id"] for r in refused})))
    worked = defaultdict(list)
    for r in cur.fetchall():
        worked[(r["user_id"], r["tool"])].append(i._aware(r["at"]))
    streaks, out = {}, []
    for r in refused:
        d = i._detail(r)
        tool = str(d.get("tool") or "tool")
        key, at = (r["user_id"], tool), r["at"]
        streak = [x for x in streaks.get(key, [])
                  if at - x["at"] <= MCP_WINDOW
                  and not any(x["at"] < t <= at for t in worked[key])] + [r]
        streaks[key] = streak
        n = len(streak)
        if n not in (TICKET_AT, UNDONE_AT) or at <= since:
            continue
        kind = str(d.get("category") or d.get("outcome") or "refused")
        how = f"MCP {tool} refused {_times(n)} in a row ({kind})"
        # A P0 here is filed at P1 with a second look: recovery._mcp.
        out.append({"ref": f"mcp-refusal:{streak[0]['id']}:{n}", "source": "mcp",
                    "at": at, "user_id": r["user_id"], "project_id": None,
                    "severity": "P0" if n >= UNDONE_AT else "P1", "tool": tool,
                    "cause_key": i.cause_key("refusal", "mcp", tool, kind),
                    "title": i._short(how),
                    "detail": i._short(f"{how}, no success of it since "
                                       f"{streak[0]['at']:%H:%M:%S} UTC", 600)})
    return out
