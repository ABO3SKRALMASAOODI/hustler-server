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
RECOVERED, TRYING, STUCK = "recovered", "trying", "stuck"


def _aware(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _naive(v):
    return _aware(v).astimezone(timezone.utc).replace(tzinfo=None)


def _one(cur):
    return bool(cur.fetchone())


def _upload(cur, w, now):
    cur.execute("""SELECT kind, detail FROM client_events
                    WHERE user_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s
                      AND COALESCE(detail->>'kind', 'original') <> 'proxy'
                    ORDER BY created_at DESC LIMIT 50""",
                (w["user_id"], list(LANDED + MOVING), _aware(w["at"]), now))
    moving = False
    for r in cur.fetchall():
        d = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        if r["kind"] in LANDED and not d.get("original_pending"):
            return RECOVERED
        moving = moving or r["kind"] in MOVING
    return TRYING if moving else STUCK


def _player(cur, w, now):
    cur.execute("""SELECT 1 FROM client_events
                    WHERE project_id = %s AND user_id = %s AND kind = ANY(%s)
                      AND created_at > %s AND created_at <= %s LIMIT 1""",
                (w["project_id"], w["user_id"], list(PLAYED),
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


def _reply(cur, w, now):
    cur.execute("""SELECT 1 FROM chat_messages m
                     JOIN projects p ON p.chat_session_id = m.session_id
                    WHERE p.id = %s AND m.role = 'assistant'
                      AND m.created_at > %s AND m.created_at <= %s
                      AND COALESCE(m.meta->>'error', '') = '' LIMIT 1""",
                (w["project_id"], _naive(w["at"]), _naive(now)))
    return RECOVERED if _one(cur) else STUCK


def _mcp(cur, w, now):
    cur.execute("""SELECT 1 FROM video_jobs
                    WHERE user_id = %s AND type = 'mcp_tool' AND state = 'done'
                      AND updated_at > %s AND updated_at <= %s
                      AND payload->>'tool' = %s
                      AND COALESCE(result->'tool_outcome'->>'status',
                                   'success') = 'success'
                    LIMIT 1""",
                (w["user_id"], _aware(w["at"]), now, w.get("tool")))
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
    """A turn that went silent: it spoke again, or it ended."""
    cur.execute("SELECT state, updated_at FROM video_jobs WHERE id = %s",
                (w["job_id"],))
    row = cur.fetchone()
    if not row or (row["state"] not in OPEN_JOB and row["updated_at"] <= now):
        return RECOVERED
    cur.execute("""SELECT 1 FROM chat_messages m
                     JOIN projects p ON p.chat_session_id = m.session_id
                    WHERE p.id = %s AND m.created_at > %s
                      AND m.created_at <= %s LIMIT 1""",
                (w["project_id"], _naive(w["at"]), _naive(now)))
    return RECOVERED if _one(cur) else STUCK


CHECKS = {"upload": _upload, "proxy": _upload, "player": _player, "job": _job,
          "request": _job, "reply": _reply, "mcp": _mcp, "analysis": _analysis,
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
    if f.get("source") != "upload":
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
        "job_type": f.get("job_type"), "tool": f.get("tool")}


def outcome(cur, item, now):
    return CHECKS[item["source"]](cur, item, _aware(now))


def due(watching, now):
    now = _aware(now)
    return sorted((w for w in watching.values() if _aware(w["due"]) <= now),
                  key=lambda w: w["due"])


def settle(rcur, wcur, watching, now):
    """Look again at every failure whose grace ran out. Returns who was left
    stuck. A read that fails is tried again on the next ticks."""
    from ops_loop import store
    blocked = []
    for w in due(watching, now):
        try:
            got = outcome(rcur, w, now)
        except Exception as e:
            w["tries"] = int(w.get("tries") or 0) + 1
            print(f"⚠️ [ops_intake] second look at {w['ref']}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if w["tries"] >= MAX_TRIES:
                watching.pop(w["ref"], None)
            continue
        wcur.execute("SAVEPOINT ops_intake_look")
        try:
            if got != TRYING:
                store.settle_failure(wcur, w, got == STUCK)
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
