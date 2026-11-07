"""Automatic intake: every NEW customer failure becomes a ticket within a
minute, without waiting for the hourly Reviewer (owner, 29 Oct 2026:
"Failures are no longer tolerated. Any failure is handled immediately by an
agent automatically").

A tick (every minute in the web process, one gunicorn worker at a time via
an advisory lock) reads what failed since the last tick and files it:

  job       a real customer's video_jobs row that FAILED (index, preview,
            final, agent_turn, mcp_tool, shorts_plan, ...)
  reply     an error reply the customer saw in the chat (meta.error) whose
            job was not already filed
  request   a Studio request that ended unfulfilled (agent_turn done with
            outcome partial or blocked, not continued). A request that
            ended on a question to the user (ask_user: outcome
            awaiting_user, or an older worker's status awaiting_user with
            outcome blocked) is waiting on the customer, not a failure.
  upload    an upload the customer could not get in (upload_failed, or
            upload_rejected unless the file was accepted anyway), and any
            transfer that fell silent without landing and without saying why
            (no heartbeat for UPLOAD_SILENT_AFTER; 18 of 23 lost uploads left
            nothing else behind, census 6 Nov 2026)
  player    the editor could not play the customer's video (player_error)
  mcp       an MCP tool error that was ours, not the AI app's mistake
            (internal, unclassified, timeout, upload, unavailable or
            transient; corrections and prerequisites are the caller's)
  proxy     the browser could not build the playback proxy (P1)
  held      a payment landed HELD_GRACE ago and the request the subscribe
            wall held before it has still not run (ticket #11), on a plan
            whose Studio could run it (an MCP-only plan hands it to the
            customer's own assistant instead)
  gaps      what should have happened by a measured deadline and did not, or
            a measured fact no failure row carries (ops_loop.gaps)
  refusal   one tool refused again and again on a project, read as the calls
            land, while the turn is still running (ops_loop.refusals)
  credits   a subscription's credits gone with the request undone: P0, and the
            owner hears it at once (ops_loop.paid_out)

One ticket per CAUSE: the cause key is derived from the failure's own
signature (`auto-<source>-<kind>-<signature>`), so one customer's retry loop
and the same failure in other accounts fold into one ticket. A new failure
after a ticket closed reopens it (recurrence + 1), however it was closed; only
an owner decision stays closed, and is reported in the digest (store.decide).

Severity is what happened to the customer, never who pays and never what an
error sentence says. A failure is filed at once at P1 and looked at again when
its grace runs out (ops_loop.recovery): still stuck, it goes to P0, and the
Mac's P0 watch (check_fixer.sh p0, every 5 minutes, up to 3 Fixers at once)
starts a Fixer; got past it, the ticket stays open for the daily Fixer and nobody
is paged. A P0 nobody
picked up within UNHANDLED_AFTER, or a claim gone stale, and a scheduled agent
that stopped running are what the owner is emailed about (cockpit_alerts).

Writes go through ops_loop.store (tickets only, guarded); reads are bounded
by time and use the existing (updated_at|created_at, ...) indexes.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from admin_metrics import defs
from ops_loop import EXCLUDED_USER_IDS

TICK_LOCK_ID = 7_710_291_029
WATERMARK_KEY = "ops_intake_watermark"
OVERLAP = timedelta(minutes=10)     # late commits are still seen
FIRST_LOOKBACK = timedelta(minutes=30)  # a fresh install never replays history
MAX_PER_TICK = 400
# The webhook resumes a held request seconds after the payment; this long
# after it, a request still waiting was dropped.
HELD_GRACE = timedelta(minutes=3)
# The watch runs every 5 minutes (owner, 7 Nov 2026: "nor issues neither latency
# in handling them is accepted") and a Fixer marks its ticket in its first
# minute: this long means a watch came and went, or every Fixer slot is taken.
UNHANDLED_AFTER = timedelta(minutes=10)
FIXING_STALE = timedelta(hours=6)
# A P0 handed back with a note is waiting on something (a replay, a rating, a
# release): the next run could only repeat the last. Without this quiet every
# watch would start a Fixer on it, and the weekly limit is what stopped them all
# for 70 hours. A new failure on it ends the quiet at once.
RELEASE_QUIET = timedelta(hours=6)
# A scheduled agent's own heartbeat (ops_review.py heartbeat). For 70 hours from
# 2 Nov nothing ran and nothing said so; the alert can only live here, because
# whatever stopped them stops anything else on the Mac too.
HEARTBEAT_LATE = {"p0_watch": timedelta(minutes=20),    # 3 runs missed
                  "fixer": timedelta(hours=26),
                  "reviewer": timedelta(hours=3)}
REVIEWS_LATE = timedelta(hours=24)
# The intake's own last pass. It ticks every minute; a pass that keeps raising
# leaves no ticket and no row, and until now only the next morning's digest said
# so. The alert runs on its own scheduler, so it outlives a broken tick.
INTAKE_LATE = timedelta(minutes=15)
# A running transfer beats every 4 minutes (lib/upload.js HEARTBEAT_MS), so this
# long with nothing heard is a transfer that stopped.
UPLOAD_SILENT_AFTER = timedelta(minutes=15)
UPLOAD_ALIVE_KINDS = ("upload_started", "upload_resumed", "upload_progress",
                      "upload_interrupted")
P1_JOB_TYPES = ("preview_check", "filmstrip")
# agent_turn result.status of a request the trial or plan budget held.
BUDGET_STATUSES = ("budget",)
# A turn an operator started on a customer's project, not one they asked for. An
# error is still a ticket, but nobody is waiting on it: 8 of 58 pages in 14 days
# were the repair batch of 3-4 Nov, "stuck" with no customer there to get past it.
OPERATOR_REPAIR = "COALESCE(j.payload->>'operator_repair', '') <> ''"
# A request that ended unfulfilled: not a slice handing over, and not a question
# waiting on the customer.
ENDED_SHORT = """(j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->>'outcome' IN ('partial', 'blocked')
           AND COALESCE(j.result->>'status', '') <> 'awaiting_user'
           AND COALESCE(j.result->>'continued_job_id', '') = '')"""
MCP_OURS = ("internal_error", "unclassified", "timeout", "upload_error",
            "executor_version_mismatch")
MCP_OURS_OUTCOMES = ("transient_failure", "unavailable", "unsafe")

# Error classes worth naming in a cause key (first match wins); anything
# else is keyed by a hash of the normalised error text.
ERROR_CLASSES = (
    # Before "not-found": "moov atom not found" is a customer's cut-short file,
    # not one of our objects going missing.
    ("truncated-media", re.compile(r"moov atom not found|unfinished file",
                                   re.I)),
    ("no-executor", re.compile(r"no remote executor|modal or cloudflare is "
                               r"required", re.I)),
    ("scratch-space", re.compile(r"scratch space", re.I)),
    ("oom", re.compile(r"out-of-memory|oom|exit -9", re.I)),
    ("wall-clock", re.compile(r"wall-clock|timed out|timeout", re.I)),
    ("lease", re.compile(r"exceeded its executor lease", re.I)),
    ("version-skew", re.compile(r"different code|readiness mismatch|source "
                                r"version does not match", re.I)),
    ("deploy-kill", re.compile(r"deploy in progress|new release|shutting "
                               r"down|lost the executor", re.I)),
    ("shard-busy", re.compile(r"shard is busy|stayed busy|no container "
                              r"instance", re.I)),
    ("model-unreachable", re.compile(r"insufficient_quota|credits remaining|"
                                     r"http 429|reach the editing model",
                                     re.I)),
    ("not-found", re.compile(r"headobject|404|nosuchkey|not found", re.I)),
)
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")
_HEX = re.compile(r"\b[0-9a-f]{6,}\b", re.I)
_PATH = re.compile(r"(/[\w.\-]+)+")
_URL = re.compile(r"https?://\S+")
_SLUG = re.compile(r"[^a-z0-9]+")


def _aware(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _naive(v):
    return _aware(v).astimezone(timezone.utc).replace(tzinfo=None)


def slug(text, limit=40):
    return _SLUG.sub("-", (text or "").lower()).strip("-")[:limit].strip("-")


def normalise(text):
    """The error with its ids, numbers, paths and links removed, so two
    occurrences of one cause read the same."""
    t = _URL.sub("<url>", text or "")
    t = _PATH.sub("<path>", t)
    t = _HEX.sub("<id>", t)
    t = _NUM.sub("#", t)
    return " ".join(t.lower().split())[:160]


def signature(text):
    for name, rx in ERROR_CLASSES:
        if rx.search(text or ""):
            return name
    norm = normalise(text)
    if not norm:
        return "no-error-text"
    return hashlib.sha1(norm.encode()).hexdigest()[:8]


def cause_key(*parts):
    key = "auto-" + "-".join(p for p in (slug(x) for x in parts) if p)
    key = re.sub(r"-{2,}", "-", key)[:80].strip("-")
    return key if len(key) >= 3 else "auto-failure"


def _short(text, n=180):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


# ── What failed since `since` ────────────────────────────────────────────

def _customers(alias="u"):
    return f"{defs.customer(alias)} AND NOT ({alias}.id = ANY(%(internal)s))"


def scan(cur, since, now):
    """Failures in (since, now], newest last: dicts with ref, source, at,
    user_id, project_id, severity, cause_key, title, detail."""
    p = {"since": _aware(since), "since_naive": _naive(since),
         "now": _aware(now), "now_naive": _naive(now),
         "internal": list(EXCLUDED_USER_IDS),
         "limit": MAX_PER_TICK}
    out = []
    cur.execute(f"""
        SELECT j.id, j.type, j.user_id, j.project_id, j.updated_at AS at,
               LEFT(j.error, 600) AS error, j.payload->>'tool' AS tool,
               {OPERATOR_REPAIR} AS operator_repair
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.state = 'failed' AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    failed_jobs = set()
    for r in cur.fetchall():
        failed_jobs.add(str(r["id"]))
        kind = r["type"] + (f"-{r['tool']}" if r.get("tool") else "")
        sig = signature(r["error"])
        out.append({
            "ref": f"job:{r['id']}", "source": "job", "at": r["at"],
            "user_id": r["user_id"], "project_id": r["project_id"],
            "severity": "P1" if r["type"] in P1_JOB_TYPES
                        or r.get("operator_repair") else "P0",
            "cause_key": cause_key("job", kind, sig),
            "title": _short(f"{kind} job failed: {r['error'] or 'no error'}"),
            "detail": _short(r["error"], 600), "job_id": r["id"],
            "job_type": r["type"], "tool": r.get("tool")})
    cur.execute(f"""
        SELECT m.id, m.created_at AS at, m.meta->>'error' AS error,
               NULLIF(m.meta->>'job', '') AS job, LEFT(m.content, 600) AS text,
               p.user_id, p.id AS project_id
          FROM chat_messages m
          JOIN projects p ON p.chat_session_id = m.session_id
          JOIN users u ON u.id = p.user_id
         WHERE m.created_at > %(since_naive)s
           AND m.created_at <= %(now_naive)s
           AND m.role = 'assistant' AND COALESCE(m.meta->>'error', '') <> ''
           AND {_customers()}
         ORDER BY m.created_at LIMIT %(limit)s""", p)
    replies = cur.fetchall()
    jobs_of_replies = [int(r["job"]) for r in replies
                       if (r["job"] or "").isdigit()
                       and r["job"] not in failed_jobs]
    filed_elsewhere = set()
    if jobs_of_replies:
        # The reply belongs to a job that failed earlier (already filed by
        # an earlier tick): the job is the failure, the reply its symptom.
        cur.execute("""SELECT id FROM video_jobs WHERE id = ANY(%s)
                        AND state = 'failed'""", (jobs_of_replies,))
        filed_elsewhere = {str(r["id"]) for r in cur.fetchall()}
    for r in replies:
        if r["job"] in failed_jobs or r["job"] in filed_elsewhere:
            continue
        out.append({
            "ref": f"msg:{r['id']}", "source": "reply", "at": _aware(r["at"]),
            "user_id": r["user_id"], "project_id": r["project_id"],
            "severity": "P0",
            "cause_key": cause_key("reply", r["error"], signature(r["text"])),
            "title": _short(f"Error reply ({r['error']}): {r['text']}"),
            "detail": _short(r["text"], 600)})
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->>'outcome' AS outcome,
               COALESCE(j.result->>'status', '') AS status,
               LEFT(COALESCE(j.result->>'honesty', j.result->>'text', ''),
                    600) AS why, {OPERATOR_REPAIR} AS operator_repair
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND {ENDED_SHORT} AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    for r in cur.fetchall():
        # The turn's status says WHY it ended unfulfilled (first_cut,
        # budget, blocked, replied, ...). Without it every unfulfilled
        # request folded into one key, so a trial budget stop "recurred" the
        # fixed box range-read P0 #60 (ticket #69). A budget stop is the
        # trial's own limit answering with the plans: filed, never paged. On a
        # subscription it is not that: ops_loop.paid_out pages it.
        status = r.get("status") or ""
        out.append({
            "ref": f"request:{r['id']}", "source": "request", "at": r["at"],
            "user_id": r["user_id"], "project_id": r["project_id"],
            "severity": "P2" if status in BUDGET_STATUSES
                        else "P1" if r.get("operator_repair") else "P0",
            "cause_key": cause_key("request", r["outcome"],
                                   "" if status == r["outcome"] else status),
            "title": f"A Studio request ended {r['outcome']} (not fulfilled)",
            "detail": _short(r["why"], 600), "job_id": r["id"]})
    cur.execute(f"""
        SELECT ce.id, ce.kind, ce.user_id, ce.project_id, ce.created_at AS at,
               ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('upload_failed', 'upload_rejected', 'player_error',
                           'upload_proxy_failed', 'mcp_error_response',
                           'director_fell_back')
           AND ce.created_at > %(since)s AND ce.created_at <= %(now)s
           AND {_customers()}
         ORDER BY ce.created_at LIMIT %(limit)s""", p)
    for r in cur.fetchall():
        item = _event_failure(r)
        if item:
            out.append(item)
    out.extend(_held_not_resumed(cur, since, now, p))
    out.extend(_stopped_uploads(cur, since, now, p))
    from ops_loop import paid_out, refusals
    for reader in (refusals.scan, paid_out.scan):
        out.extend(_guarded(reader, cur, since, now, p))
    out.sort(key=lambda f: _aware(f["at"]))
    return out


def _guarded(reader, cur, since, now, p):
    """One reader that breaks never costs the intake its tick, and files itself
    (gaps.scan does the same)."""
    try:
        return reader(cur, since, now, p)
    except Exception as e:
        name = f"{reader.__module__.rsplit('.', 1)[-1]}-{reader.__name__}"
        print(f"⚠️ [ops_intake] {name}: {type(e).__name__}: {e}", flush=True)
        return [{"ref": f"broken:{name}:{_aware(now):%Y%m%d%H}", "source": "gap",
                 "at": _aware(now), "user_id": None, "project_id": None,
                 "severity": "P1", "cause_key": cause_key("gap", "check-broken",
                                                          name),
                 "title": _short(f"A failure check is broken: {name}"),
                 "detail": _short(f"{type(e).__name__}: {e}", 600)}]


def _stopped_uploads(cur, since, now, p):
    """Transfers whose last sign of life fell into this window, and that never
    landed and never said why. An `upload_failed` row was filed on its own; a
    proxy's bytes land as part of the original they belong to, and a link
    import has no transfer (no bytes): its index job reports it. One row per
    project and size: a resumed transfer is the same upload, judged by its last
    sign of life, and its ref is that row, so a transfer that comes back and
    stops again is a new stop. Only a row about this file ends it: another file
    landing or failing in the project says nothing about this one. A row with no
    size cannot be told apart, so it counts."""
    q = dict(p, kinds=list(UPLOAD_ALIVE_KINDS),
             silent_from=_naive(since - UPLOAD_SILENT_AFTER),
             silent_to=_naive(now - UPLOAD_SILENT_AFTER))
    cur.execute(f"""
        SELECT last.* FROM (
            SELECT DISTINCT ON (ce.user_id, ce.project_id, ce.detail->>'bytes')
                   ce.id, ce.kind, ce.user_id, ce.project_id, ce.created_at AS at,
                   ce.detail
              FROM client_events ce JOIN users u ON u.id = ce.user_id
             WHERE ce.kind = ANY(%(kinds)s)
               AND ce.created_at > %(silent_from)s
               AND ce.created_at <= %(now)s
               AND ce.project_id IS NOT NULL
               AND ce.detail->>'bytes' IS NOT NULL
               AND COALESCE(ce.detail->>'kind', 'original') <> 'proxy'
               AND {_customers()}
             ORDER BY ce.user_id, ce.project_id, ce.detail->>'bytes',
                      ce.created_at DESC) last
         WHERE last.at <= %(silent_to)s
           AND EXISTS (SELECT 1 FROM projects pr WHERE pr.id = last.project_id)
           AND NOT EXISTS (
                 SELECT 1 FROM client_events done
                  WHERE done.project_id = last.project_id
                    AND done.user_id = last.user_id
                    AND done.created_at >= last.at
                    AND (done.detail->>'bytes' = last.detail->>'bytes'
                         OR done.detail->>'bytes' IS NULL
                         OR last.detail->>'bytes' IS NULL)
                    AND (done.kind IN ('upload_failed', 'upload_original_ready')
                         OR (done.kind IN ('upload_landed', 'upload_deduped')
                             AND COALESCE(done.detail->>'original_pending',
                                          'false') <> 'true')))
         ORDER BY last.at LIMIT %(limit)s""", q)
    out = []
    for r in cur.fetchall():
        d = _detail(r)
        left = r["kind"] == "upload_interrupted"
        sent = d.get("sent_bytes", d.get("bytes_done"))
        out.append({
            "ref": f"stopped:{r['id']}", "source": "upload", "at": _aware(r["at"]),
            "user_id": r["user_id"], "project_id": r["project_id"],
            "severity": "P1", "bytes": d.get("bytes"),
            "file_kind": d.get("kind"),
            "cause_key": cause_key("upload-stopped",
                                   "page-left" if left else "no-signal"),
            "title": ("An upload stopped without landing: the page went away"
                      if left else
                      "An upload stopped without landing: nothing heard"),
            "detail": _short(f"{d.get('filename') or 'file'} · {d.get('bytes')} "
                             f"bytes · {d.get('kind') or 'original'} · sent "
                             f"{sent if sent is not None else 'unknown'} · last "
                             f"heard {r['kind']}", 600)})
    return out


def _held_not_resumed(cur, since, now, p):
    """Paid customers whose held request never ran (see the module doc).
    The window trails by HELD_GRACE; the plans reply must predate the
    payment by at most the resume window (held_request.WINDOW_H)."""
    from video_services import held_request
    q = dict(p, held_since=_naive(since - HELD_GRACE),
             held_until=_naive(now - HELD_GRACE),
             window_h=held_request.WINDOW_H)
    cur.execute(f"""
        SELECT pay.id AS payment_id, pay.user_id, pay.occurred_at AS at,
               pay.plan, h.id AS plans_id, h.project_id, h.surface, h.wall_at,
               h.wall_meta
          FROM payments pay JOIN users u ON u.id = pay.user_id
          CROSS JOIN LATERAL (
                SELECT m.id, pr.id AS project_id, m.created_at AS wall_at,
                       m.meta AS wall_meta,
                       m.meta->>'surface' AS surface
                  FROM projects pr
                  JOIN chat_messages m ON m.session_id = pr.chat_session_id
                 WHERE pr.user_id = pay.user_id AND m.role = 'assistant'
                   AND m.meta->>'kind' = 'subscription_plans'
                   AND m.meta ? 'blocked_prompt'
                   AND NOT (m.meta ? 'replayed_at')
                   AND m.created_at <= pay.occurred_at
                   AND m.created_at > pay.occurred_at
                                      - make_interval(hours => %(window_h)s)
                   AND NOT EXISTS (
                         SELECT 1 FROM chat_messages n
                          WHERE n.session_id = m.session_id AND n.id > m.id
                            AND n.role = 'user')
                 ORDER BY m.id DESC LIMIT 1) h
         WHERE pay.occurred_at > %(held_since)s
           AND pay.occurred_at <= %(held_until)s
           AND pay.amount_cents > 0 AND pay.status IN ('completed', 'paid')
           AND {_customers()}
         ORDER BY pay.occurred_at LIMIT %(limit)s""", q)
    out, seen = [], set()
    for r in cur.fetchall():
        if r["plans_id"] in seen:
            continue                    # Paddle's paid + completed rows
        seen.add(r["plans_id"])
        try:
            if not held_request.studio_lane_open(cur, r["user_id"]):
                continue                # handed to the customer's assistant
            if held_request.assistant_took_over(cur, r["user_id"],
                                                r["wall_at"],
                                                r["wall_meta"]):
                continue                # its assistant made it (D1V)
        except Exception:
            pass                        # unknown lane: file it, look at it
        out.append({
            "ref": f"held:{r['plans_id']}", "source": "held",
            "at": _aware(r["at"]) + HELD_GRACE,
            "user_id": r["user_id"], "project_id": r["project_id"],
            "severity": "P0",
            "cause_key": cause_key("held", "request-not-resumed"),
            "title": "A request the subscribe wall held did not run after "
                     "the customer paid",
            "detail": _short(f"plan {r['plan']} · {r['surface'] or 'chat'} "
                             f"wall · plans reply {r['plans_id']} · payment "
                             f"{r['payment_id']}", 600)})
    return out


def _detail(row):
    d = row.get("detail")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {"text": d}
    return d if isinstance(d, dict) else {}


def _event_failure(r):
    d = _detail(r)
    base = {"ref": f"event:{r['id']}", "at": _aware(r["at"]),
            "user_id": r["user_id"], "project_id": r["project_id"]}
    kind = r["kind"]
    if kind == "upload_rejected":
        if d.get("accepted"):
            return None             # stored and analysed: not a failure
        reason = str(d.get("reason") or "rejected")
        size = d.get("bytes")
        # Whoever's file it is, a refusal stopped them: the second look says
        # whether a finished copy landed. Left stuck, the cause gets the product
        # change that stops it hurting the customer (FIXER.md section 11).
        return dict(base, source="upload", bytes=size, file_kind=d.get("kind"),
                    severity="P0", refused=True,
                    cause_key=cause_key("upload-rejected", reason),
                    title=_short(f"Upload refused: {reason}"),
                    detail=_short(f"{reason} · {size} bytes · "
                                  f"{d.get('ext') or ''} "
                                  f"{('duration ' + str(d['duration_s']) + ' s') if d.get('duration_s') else ''}",
                                  600))
    if kind == "upload_failed":
        reason = str(d.get("reason") or "failed")
        return dict(base, source="upload", severity="P0", bytes=d.get("bytes"),
                    file_kind=d.get("kind"), offline=bool(d.get("offline")),
                    cause_key=cause_key("upload-failed", signature(reason)
                                        if len(reason) > 40 else reason),
                    title=_short(f"Upload failed: {reason}"),
                    detail=_short(f"{reason} · {d.get('bytes')} bytes · stage "
                                  f"{d.get('stage') or 'foreground'}", 600))
    if kind == "player_error":
        media = d.get("media_error") or {}
        code = media.get("code") if isinstance(media, dict) else media
        ext = (str(d.get("src_path") or "").rsplit(".", 1) + [""])[1][:5]
        return dict(base, source="player", severity="P0",
                    cause_key=cause_key("player-error", f"code{code}"
                                        if code is not None else "", ext),
                    title=_short(f"The editor could not play the video "
                                 f"(media error {code}, .{ext})"),
                    detail=_short(json.dumps({k: d.get(k) for k in (
                        "media_error", "is_proxy", "is_mobile", "video_w",
                        "video_h", "src_path")}, default=str), 600))
    if kind == "upload_proxy_failed":
        reason = str(d.get("reason") or "failed")
        # 8 of 9 proxy read failures were followed by a failed upload or no
        # landing within 6 minutes, so it blocks unless the file lands anyway.
        return dict(base, source="proxy", severity="P0",
                    cause_key=cause_key("proxy-failed", reason),
                    title=_short(f"The browser could not build the playback "
                                 f"proxy: {reason}"),
                    detail=_short(json.dumps(d, default=str), 600))
    if kind == "mcp_error_response":
        cat, outcome = d.get("category"), d.get("outcome")
        if outcome in ("correction_needed", "prerequisite") or (
                cat not in MCP_OURS and outcome not in MCP_OURS_OUTCOMES):
            return None             # the AI app's correction, not our failure
        tool = str(d.get("tool") or "tool")
        # "unclassified" means none of our markers matched: often a validation
        # the caller fixes, sometimes ours. The second look tells them apart by
        # what happened: the same tool worked afterwards, or it did not.
        return dict(base, source="mcp", severity="P0", tool=tool,
                    cause_key=cause_key("mcp", tool, outcome or cat),
                    title=_short(f"MCP {tool} answered an error ({outcome or cat})"
                                 + (f": {d['message']}" if d.get("message")
                                    else "")),
                    detail=_short(d.get("message") or json.dumps(
                        d, default=str), 600))
    if kind == "director_fell_back":
        # The Director's First cut did not land and the chain delivered in the
        # same job (worker/score/film.py). The customer got a result, so it is
        # never P0, but the film's cause is a ticket like any failure: one per
        # stage and kind of failure, never per sentence.
        stage = str(d.get("stage") or "film")
        reason = str(d.get("reason") or "")
        head = reason.split(":", 1)[0].strip().lower()[:40] if ":" in reason[:48] else signature(reason)
        return dict(base, source="director", severity="P1",
                    cause_key=cause_key("director", stage, head),
                    title=_short(f"The Director's First cut fell to the chain at {stage}: {reason}"),
                    detail=_short(json.dumps(d, default=str), 600))
    return None


# ── Filing ───────────────────────────────────────────────────────────────

def _watermark(cur, now):
    cur.execute("SELECT value FROM app_kv WHERE key = %s", (WATERMARK_KEY,))
    row = cur.fetchone()
    try:
        v = json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        v = {}
    at = v.get("at") if isinstance(v, dict) else None
    return (_aware(at) if at else now - FIRST_LOOKBACK), v


def run(rcur, wcur, now=None):
    """One intake pass. Returns {"seen", "filed", "tickets": [...]}.

    The window overlaps the previous one by OVERLAP so a row committed late
    is still seen; refs filed in that overlap are remembered in the
    watermark (`recent`), so nothing is counted twice whatever the ticket's
    history holds. A failure the customer might still get past is filed at P1
    and looked at again when its grace runs out (ops_loop.recovery)."""
    from ops_loop import gaps, recovery, store
    now = _aware(now) or datetime.now(timezone.utc)
    if not _tables_ready(rcur):
        return {"seen": 0, "filed": 0, "tickets": [], "skipped": "no tables"}
    mark, state = _watermark(rcur, now)
    state = state if isinstance(state, dict) else {}
    recent = dict(state.get("recent") or {})
    watching = dict(state.get("watching") or {})
    since = min(mark, now) - OVERLAP
    failures = [f for f in scan(rcur, since, now) + gaps.scan(rcur, since, now)
                if f["ref"] not in recent]
    filed, touched = 0, {}
    for f in failures:
        # One failure's savepoint: a bad row never loses the others.
        wcur.execute("SAVEPOINT ops_intake_one")
        try:
            severity, look = recovery.plan(f, rcur)
            t, action = store.record_failure(wcur, dict(f, severity=severity))
        except Exception as e:
            wcur.execute("ROLLBACK TO SAVEPOINT ops_intake_one")
            print(f"⚠️ [ops_intake] {f.get('ref')}: {type(e).__name__}: {e}",
                  flush=True)
            continue
        wcur.execute("RELEASE SAVEPOINT ops_intake_one")
        # When it was seen, not when it happened: a stop or a re-rating is
        # filed long after its own moment and must still be remembered.
        recent[f["ref"]] = now.isoformat(timespec="seconds")
        if action != "duplicate":
            filed += 1
            touched[t["id"]] = {"id": t["id"], "cause_key": t["cause_key"],
                                "severity": t["severity"],
                                "status": t["status"], "action": action}
            if look and action in ("opened", "attached", "reopened",
                                   "attached_to_closed") and \
                    len(watching) < recovery.MAX_WATCHED:
                watching[f["ref"]] = look
    blocked = recovery.settle(rcur, wcur, watching, now)
    keep_after = now - OVERLAP - timedelta(minutes=5)
    recent = {r: at for r, at in recent.items()
              if _aware(at) >= keep_after}
    store.put_state(wcur, WATERMARK_KEY, {"at": now.isoformat(),
                                          "seen": len(failures),
                                          "filed": filed, "recent": recent,
                                          "watching": watching})
    return {"seen": len(failures), "filed": filed,
            "tickets": list(touched.values()), "left_blocked": blocked}


def _tables_ready(cur):
    cur.execute("SELECT to_regclass('public.tickets') AS t")
    return bool((cur.fetchone() or {}).get("t"))


# ── What needs a person now ──────────────────────────────────────────────

# When a ticket last became someone's to act on, and when it was last claimed.
# Never updated_at: every new failure bumps it, so a stuck P0 looked more alive
# the more customers it hurt (#77 sat 22 h in 'fixing' and took 5 failures).
OPENED_AT = """(SELECT max((h->>'at')::timestamptz) FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' IN ('opened', 'recurred', 'reopened',
                                       'escalated', 'released'))"""
CLAIMED_AT = """COALESCE((SELECT max((h->>'at')::timestamptz) FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' = 'fixing'), updated_at)"""
RELEASED_AT = """(SELECT max((h->>'at')::timestamptz) FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' = 'released')"""
BACK_AT = f"""(SELECT min((h->>'at')::timestamptz) FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' = 'failure'
                   AND (h->>'at')::timestamptz > {RELEASED_AT})"""


def _waiting_since(r):
    """When an open P0 became the watch's to take: the moment pending_p0 turns
    true, so the two can never disagree. A ticket a Fixer handed back is nobody's
    until its quiet ends or a failure lands on it; counted from the release, one
    that came out of its 6 h quiet was already "not handled" before the next
    watch could reach it."""
    opened = _aware(r["opened_at"] or r["created_at"])
    released = _aware(r.get("released_at"))
    if not released or opened > released:
        return opened
    back = _aware(r.get("back_at"))
    last = _aware(r["last_seen"])
    if not back and last > released:
        back = last             # past FAILURE_REFS_KEPT: no entry, only the time
    quiet_ends = released + RELEASE_QUIET
    return min(back, quiet_ends) if back else quiet_ends


def unhandled(cur, now=None, after=UNHANDLED_AFTER, stale=FIXING_STALE):
    """P0 tickets no agent is acting on: open ones nobody started within `after`
    of their latest (re)opening (the Mac asleep, the P0 watch off, or the run
    failed) and 'fixing' ones whose claim is older than `stale` (the run died).
    What the owner is emailed about."""
    now = _aware(now) or datetime.now(timezone.utc)
    if not _tables_ready(cur):
        return []
    cur.execute(f"""
        SELECT id, cause_key, title, severity, status, occurrences,
               recurrence_count, first_seen, last_seen, created_at,
               updated_at, {OPENED_AT} AS opened_at, {CLAIMED_AT} AS claimed_at,
               {RELEASED_AT} AS released_at, {BACK_AT} AS back_at,
               (SELECT count(DISTINCT h->>'user') FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' = 'failure') AS users
          FROM tickets
         WHERE status IN ('open', 'fixing') AND severity = 'P0'
         ORDER BY id""")
    out = []
    for r in cur.fetchall():
        fixing = r["status"] == "fixing"
        since = _aware(r["claimed_at"]) if fixing else _waiting_since(r)
        if since and now - since >= (stale if fixing else after):
            d = dict(r)
            d["opened_at"] = since
            d["waiting_min"] = int((now - since).total_seconds() // 60)
            out.append(d)
    return out


def pending_p0(cur, now=None, stale=FIXING_STALE):
    """P0 tickets the Mac's Fixer should take now: open ones, unless a Fixer
    handed one back within RELEASE_QUIET and nothing failed on it since, and
    'fixing' ones claimed more than `stale` ago (a run that died)."""
    now = _aware(now) or datetime.now(timezone.utc)
    if not _tables_ready(cur):
        return []
    cur.execute(f"""SELECT id, cause_key, title, status, occurrences,
                          recurrence_count, updated_at, last_seen
                     FROM tickets
                    WHERE severity = 'P0'
                      AND ((status = 'open'
                            AND (COALESCE({RELEASED_AT}, 'epoch') < %s
                                 OR last_seen > {RELEASED_AT}))
                           OR (status = 'fixing' AND {CLAIMED_AT} < %s))
                    ORDER BY id""", (now - RELEASE_QUIET, now - stale))
    return [dict(r) for r in cur.fetchall()]


def watch_beat(cur, now=None):
    """Minutes since the P0 watch last ran, or None if it never beat. Tells the
    owner why a P0 sits open: the watch is off, or all its Fixers are busy."""
    now = _aware(now) or datetime.now(timezone.utc)
    cur.execute("SELECT value FROM app_kv WHERE key = %s",
                ("ops_heartbeat_p0_watch",))
    row = cur.fetchone()
    try:
        at = _aware(json.loads(row["value"])["at"])
    except (TypeError, ValueError, KeyError):
        return None
    return int((now - at).total_seconds() // 60)


def silent_agents(cur, now=None):
    """The scheduled agents that stopped, read from their own heartbeats, the
    intake's own last pass, and the reviews that stopped arriving. An agent that
    never beat yet says nothing: its first beat arms it. Nobody watched the
    watcher for 70 hours."""
    now = _aware(now) or datetime.now(timezone.utc)
    keys = {f"ops_heartbeat_{a}": a for a in HEARTBEAT_LATE}
    keys[WATERMARK_KEY] = "intake"
    late = dict(HEARTBEAT_LATE, intake=INTAKE_LATE)
    cur.execute("SELECT key, value FROM app_kv WHERE key = ANY(%s)",
                (sorted(keys),))
    out = []
    for r in cur.fetchall():
        try:
            at = _aware(json.loads(r["value"])["at"])
        except (TypeError, ValueError, KeyError):
            continue
        agent = keys[r["key"]]
        if now - at > late[agent]:
            out.append({"agent": agent, "last_at": at,
                        "silent_min": int((now - at).total_seconds() // 60)})
    cur.execute("SELECT max(reviewed_at) AS at FROM session_reviews")
    at = _aware((cur.fetchone() or {}).get("at"))
    if at and now - at > REVIEWS_LATE:
        out.append({"agent": "session reviews", "last_at": at,
                    "silent_min": int((now - at).total_seconds() // 60)})
    return out


# ── The scheduler ────────────────────────────────────────────────────────

def _connect(read_only):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from database_config import preferred_database_url
    opts = "-c statement_timeout=30000 -c lock_timeout=2000"
    if read_only:
        opts += " -c default_transaction_read_only=on"
    conn = psycopg2.connect(preferred_database_url(),
                            cursor_factory=RealDictCursor, connect_timeout=10,
                            options=opts)
    # Reads autocommit; the writes of one pass are one transaction.
    conn.autocommit = read_only
    return conn


def run_tick(now=None, rconn=None, wconn=None):
    """One locked pass on its own connections (the web process tick)."""
    own = rconn is None
    rconn = rconn or _connect(True)
    wconn = wconn or _connect(False)
    try:
        with wconn.cursor() as w:
            w.execute("SELECT pg_try_advisory_lock(%s) AS got",
                      (TICK_LOCK_ID,))
            got = w.fetchone()["got"]
        wconn.commit()
        if not got:
            return {"skipped": "another worker is filing"}
        try:
            with rconn.cursor() as r, wconn.cursor() as w:
                out = run(r, w, now)
            wconn.commit()
            return out
        except Exception:
            wconn.rollback()
            raise
        finally:
            with wconn.cursor() as w:
                w.execute("SELECT pg_advisory_unlock(%s)", (TICK_LOCK_ID,))
            wconn.commit()
    finally:
        if own:
            for c in (rconn, wconn):
                try:
                    c.close()
                except Exception:
                    pass


_scheduler = None


def start_scheduler(app):
    """Every minute in each gunicorn worker; the advisory lock picks one.
    OPS_INTAKE_ENABLED=0 turns it off."""
    global _scheduler
    import os
    import threading
    url = str(app.config.get("DATABASE_URL") or "")
    if os.getenv("OPS_INTAKE_ENABLED", "1") not in ("1", "true", "True") \
            or not url or "stub" in url or os.getenv("SKIP_DB_INIT") == "1":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:                                  # pragma: no cover
        app.logger.warning("apscheduler missing: ops intake not started")
        return
    lock = getattr(start_scheduler, "_lock", None) or threading.Lock()
    start_scheduler._lock = lock
    with lock:
        if _scheduler is not None:
            return

        def job():
            try:
                with app.app_context():
                    result = run_tick()
                if result.get("filed"):
                    app.logger.info("ops intake: %s", result)
            except Exception as e:                      # never kill the thread
                app.logger.error("ops intake tick failed: %s", e)

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(job, "interval", minutes=1, id="ops_intake",
                      replace_existing=True, max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc)
                      + timedelta(seconds=90))
        sched.start()
        _scheduler = sched
        app.logger.info("ops intake scheduler started")
