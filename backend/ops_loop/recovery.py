"""Severity is what happened to the customer, measured, never who pays and
never what the error text says (owner, 6 Nov 2026: "it's not important if it's
for the customers that are paying or not").

A failure a customer might still get past is filed at once at P1, so it is on a
ticket within a minute, and looked at again when its grace runs out:

  got past it   the ticket stays where it is, open. Nobody is paged, and the
                cause is still fixed: less urgent this minute, not less real.
  still stuck   the ticket goes to P0 and the P0 watch starts a Fixer.
  trying again  an upload is moving again: the stop detector owns it from here
                and starts its own look if it stops.

What counts as "got past it" is a row the product itself wrote, read here per
kind of failure. No customer words are read.

The list of second looks lives in the intake's watermark row, so it survives a
restart of the web process. A failure the customer cannot get past alone (a held
request after a payment) and a failure that blocks nobody (a background check,
a budget stop) are not looked at twice: their severity is already the truth.
"""
from datetime import datetime, timedelta, timezone

GRACE = timedelta(minutes=10)
# Work still running is slow, not stuck. On 14 days of real rows a 10 minute look
# called 54 overdue analyses, silent turns and long renders stuck, and 8 of them
# still were 45 minutes on. The watch runs every 5 minutes now, so a page reaches a
# Fixer at once and the look decides alone. Replayed on the 28 overdue analyses of
# the 14 days to 7 Nov, 18 were called stuck at 10 minutes, 15 at 15, 10 at 20, 4 at
# 30 and 3 at 45: 30 costs one more page in a fortnight and a customer 15 minutes.
SLOW_GRACE = timedelta(minutes=30)
SLOW_SOURCES = ("analysis", "turn")
# The client says the customer's own line is down: it comes back on its own.
OFFLINE_GRACE = timedelta(minutes=30)
# Getting a file in again takes as long as it takes: the p10 of 330 real
# transfers, with the same 1.5 margin that caught 26 of 26 lost uploads with 2
# false alarms in 340 (audit-invisible, finding 1).
UPLOAD_BPS = 1.02e6
UPLOAD_MARGIN = 1.5
UPLOAD_GRACE_MAX = timedelta(hours=2)
MAX_WATCHED = 1000
MAX_TRIES = 5

LANDED = ("upload_landed", "upload_deduped", "upload_original_ready")
MOVING = ("upload_started", "upload_resumed", "upload_progress")
PLAYED = ("player_recovered", "free_result_watched_pct")
EXPORT_REACHED = ("download_url_ready", "download_triggered",
                  "mcp_export_delivered")
OPEN_JOB = ("queued", "running")
# A customer who went on with what a request gave them is not stuck on it.
WENT_ON = ("free_result_watched_pct", "export_clicked", "export_job_started",
           "download_triggered", "mcp_export_delivered")
# New work on the project after the player failed.
WORK = ("preview", "final", "agent_turn")
# An assistant reading state has not carried on: it reads, and it stops.
MCP_READS = ("__state__", "__media__")
RECOVERED, TRYING, STUCK = "recovered", "trying", "stuck"


def _aware(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _naive(v):
    return _aware(v).astimezone(timezone.utc).replace(tzinfo=None)


def _one(cur):
    return bool(cur.fetchone())


def _family(kind):
    """What a file is for: a song landing does not bring back a lost video."""
    return {"music": "music", "image": "image",
            "image_ref": "image"}.get(str(kind or "original"), "video")


def _same_upload(w, row, detail):
    """This row is about what the customer failed to get in: the same kind of
    file, in the same project or with the same size (a new project for the same
    file). A failure that names neither has only the kind to go on. Before this,
    any file of the account counted: 24 refused videos of account 2649 were
    called recovered by one song, and another clip starting would have dropped
    the look at a lost 7.8 GB upload for good."""
    if _family(detail.get("kind")) != _family(w.get("file_kind")):
        return False
    size = w.get("bytes")
    return (not size or w.get("project_id") in (None, row.get("project_id"))
            or str(detail.get("bytes")) == str(size))


def _upload(cur, w, now):
    cur.execute("""SELECT kind, project_id, detail FROM client_events
                    WHERE user_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s
                      AND COALESCE(detail->>'kind', 'original') <> 'proxy'
                    ORDER BY created_at DESC LIMIT 50""",
                (w["user_id"], list(LANDED + MOVING), _aware(w["at"]), now))
    moving = False
    for r in cur.fetchall():
        d = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        if not _same_upload(w, r, d):
            continue
        if r["kind"] in LANDED and not d.get("original_pending"):
            return RECOVERED
        moving = moving or r["kind"] in MOVING
    return TRYING if moving else STUCK


def _player(cur, w, now):
    """It played, or new work started on the project: nothing writes a row when
    a video simply plays, so without the second half account 2647, back at work
    5 minutes later, was "stuck" on one network error for good."""
    cur.execute("""SELECT 1 FROM client_events
                    WHERE project_id = %s AND user_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s
                    UNION ALL
                   SELECT 1 FROM video_jobs
                    WHERE project_id = %s AND type = ANY(%s) AND state = 'done'
                      AND created_at > %s AND updated_at <= %s LIMIT 1""",
                (w["project_id"], w["user_id"], list(PLAYED),
                 _aware(w["at"]), now, w["project_id"], list(WORK),
                 _aware(w["at"]), now))
    return RECOVERED if _one(cur) else STUCK


def _job(cur, w, now):
    """The same work finished on the same project afterwards. A failed turn is
    behind the customer only when a later turn did what they asked."""
    cur.execute("""SELECT 1 FROM video_jobs
                    WHERE project_id = %s AND type = %s AND state = 'done'
                      AND updated_at > %s AND updated_at <= %s
                      AND (%s::text IS NULL OR payload->>'tool' = %s)
                      AND (type <> 'agent_turn'
                           OR result->>'outcome' = 'fulfilled')
                    LIMIT 1""",
                (w["project_id"], w.get("job_type") or "agent_turn",
                 _aware(w["at"]), now, w.get("tool"), w.get("tool")))
    return RECOVERED if _one(cur) else STUCK


def _request(cur, w, now):
    """A request that ended short is behind the customer when a later turn did
    it, or when they went on with what it gave them: watched it or asked for the
    export. In 14 days 7 requests were called stuck with the customer already
    doing that. The ticket stays open: the cause is still fixed."""
    if _job(cur, w, now) == RECOVERED:
        return RECOVERED
    cur.execute("""SELECT 1 FROM client_events
                    WHERE project_id = %s AND user_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s LIMIT 1""",
                (w["project_id"], w["user_id"], list(WENT_ON),
                 _aware(w["at"]), now))
    return RECOVERED if _one(cur) else STUCK


def _reply(cur, w, now):
    cur.execute("""SELECT 1 FROM chat_messages m
                     JOIN projects p ON p.chat_session_id = m.session_id
                    WHERE p.id = %s AND m.role = 'assistant'
                      AND m.created_at > %s AND m.created_at <= %s
                      AND COALESCE(m.meta->>'error', '') = '' LIMIT 1""",
                (w["project_id"], _naive(w["at"]), _naive(now)))
    return RECOVERED if _one(cur) else STUCK


def _mcp(cur, w, now):
    """The same tool worked afterwards, or the assistant carried on with its
    work. Waiting for that one tool alone could never end: wait_for_job,
    project_state, open_project, apply_edit_batch, export_final, upload_start and
    watch_video write no job under their own name, so account 2555, 15 tool calls
    past an error, stayed "stuck" on it."""
    cur.execute("""SELECT 1 FROM video_jobs
                    WHERE user_id = %s AND type = 'mcp_tool' AND state = 'done'
                      AND updated_at > %s AND updated_at <= %s
                      AND (payload->>'tool' = %s
                           OR NOT COALESCE(payload->>'tool', '') = ANY(%s))
                      AND COALESCE(result->'tool_outcome'->>'status',
                                   'success') = 'success'
                    LIMIT 1""",
                (w["user_id"], _aware(w["at"]), now, w.get("tool"),
                 list(MCP_READS)))
    return RECOVERED if _one(cur) else STUCK


def _analysis(cur, w, now):
    cur.execute("""SELECT 1 FROM video_jobs WHERE id = %s AND state = 'done'
                      AND updated_at <= %s""", (w["job_id"], now))
    return RECOVERED if _one(cur) else STUCK


def _export(cur, w, now):
    cur.execute("""SELECT 1 FROM client_events
                    WHERE project_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s LIMIT 1""",
                (w["project_id"], list(EXPORT_REACHED), _aware(w["at"]), now))
    return RECOVERED if _one(cur) else STUCK


def _checkout(cur, w, now):
    cur.execute("""SELECT 1 FROM client_events
                    WHERE user_id = %s AND kind = 'checkout_stage'
                      AND detail->>'stage' IN ('loaded', 'completed')
                      AND created_at > %s AND created_at <= %s LIMIT 1""",
                (w["user_id"], _aware(w["at"]), now))
    return RECOVERED if _one(cur) else STUCK


def _turn(cur, w, now):
    """A turn that went silent: it spoke again, or the request ended. A slice
    handing over to the next is not an end: 127 turns in 14 days ended that way.
    The customer's own "hello?" is the opposite of the turn speaking (account
    2752 sent "؟" two minutes in and waited another 1,060 s). A step row or the
    turn's preview render moving is the product speaking (ticket #842)."""
    cur.execute("""SELECT 1 FROM video_jobs
                    WHERE project_id = %s AND type = 'agent_turn'
                      AND state = ANY(%s) LIMIT 1""",
                (w["project_id"], list(OPEN_JOB)))
    if not _one(cur):
        return RECOVERED
    cur.execute("""SELECT 1 FROM chat_messages m
                     JOIN projects p ON p.chat_session_id = m.session_id
                    WHERE p.id = %s AND m.role IN ('assistant', 'activity')
                      AND m.created_at > %s AND m.created_at <= %s
                    UNION ALL
                   SELECT 1 FROM video_jobs
                    WHERE project_id = %s AND type = 'preview'
                      AND heartbeat_at > %s AND heartbeat_at <= %s
                    LIMIT 1""",
                (w["project_id"], _naive(w["at"]), _naive(now),
                 w["project_id"], _aware(w["at"]), now))
    return RECOVERED if _one(cur) else STUCK


CHECKS = {"upload": _upload, "proxy": _upload, "player": _player, "job": _job,
          "request": _request, "reply": _reply, "mcp": _mcp, "analysis": _analysis,
          "export": _export, "checkout": _checkout, "turn": _turn}


def _upload_wait(nbytes):
    transfer = timedelta(seconds=float(nbytes or 0) / UPLOAD_BPS * UPLOAD_MARGIN)
    return min(max(GRACE, transfer), UPLOAD_GRACE_MAX)


def _stop_bytes(cur, ref):
    """The size of a silent upload, and whether it ever beat. A client with a
    heartbeat that fell silent has stopped; one that never beat may be an older
    tab still sending, so it gets the time the file needs."""
    try:
        cur.execute("SELECT kind, detail FROM client_events WHERE id = %s",
                    (int(str(ref).split(":", 1)[1]),))
        row = cur.fetchone() or {}
    except Exception:
        return 0                    # the look still happens, a little sooner
    d = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return d.get("bytes") if row.get("kind") == "upload_started" else 0


def grace(f, cur=None):
    if f.get("offline"):
        return OFFLINE_GRACE
    if f.get("source") in SLOW_SOURCES:
        return SLOW_GRACE
    # A refused file never started moving: it needs no time to transfer, and a
    # customer stopped at the door should not wait 2 hours for someone to know.
    if f.get("source") != "upload" or f.get("refused"):
        return GRACE
    if str(f.get("ref")).startswith("stopped:") and cur is not None:
        return _upload_wait(_stop_bytes(cur, f["ref"]))
    return _upload_wait(f.get("bytes"))


def plan(f, cur=None):
    """(severity to file at, the second look or None)."""
    severity = f.get("severity") or "P0"
    stopped = str(f.get("ref")).startswith("stopped:")
    if not (severity == "P0" or stopped) or f.get("source") not in CHECKS:
        return severity, None
    return "P1", {
        "ref": f["ref"], "source": f["source"], "cause_key": f["cause_key"],
        "at": _aware(f["at"]).isoformat(), "user_id": f.get("user_id"),
        "due": (_aware(f["at"]) + grace(f, cur)).isoformat(),
        "project_id": f.get("project_id"), "job_id": f.get("job_id"),
        "job_type": f.get("job_type"), "tool": f.get("tool"),
        "bytes": f.get("bytes"), "file_kind": f.get("file_kind")}


def outcome(cur, item, now):
    return CHECKS[item["source"]](cur, item, _aware(now))


def due(watching, now):
    now = _aware(now)
    return sorted((w for w in watching.values() if _aware(w["due"]) <= now),
                  key=lambda w: w["due"])


def settle(rcur, wcur, watching, now):
    """Look again at every failure whose grace ran out. Returns who was left
    stuck. A read that fails is tried again on the next ticks; one that never
    works counts as stuck, because nobody can say the customer got past it."""
    from ops_loop import store
    blocked = []
    for w in due(watching, now):
        note = None
        try:
            got = outcome(rcur, w, now)
        except Exception as e:
            w["tries"] = int(w.get("tries") or 0) + 1
            print(f"⚠️ [ops_intake] second look at {w['ref']}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if w["tries"] < MAX_TRIES:
                continue
            got, note = STUCK, f"the second look failed: {type(e).__name__}"
        wcur.execute("SAVEPOINT ops_intake_look")
        try:
            if got != TRYING:
                store.settle_failure(wcur, w, got == STUCK, note)
        except Exception as e:
            wcur.execute("ROLLBACK TO SAVEPOINT ops_intake_look")
            print(f"⚠️ [ops_intake] settling {w['ref']}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        wcur.execute("RELEASE SAVEPOINT ops_intake_look")
        watching.pop(w["ref"], None)
        if got == STUCK:
            blocked.append(w["ref"])
    return blocked
