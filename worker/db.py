"""Postgres access for the worker.

The queue is the video_jobs table: claims use FOR UPDATE SKIP LOCKED so any
number of worker processes/threads can poll safely. A running job whose
heartbeat goes stale (worker died mid-job) becomes claimable again until its
attempts are exhausted.
"""

import json
import math
import os
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor, Json

import config
import model_prices

# ------------------------------------------------------------------ #
#  Connections                                                         #
# ------------------------------------------------------------------ #


# A BLOCKING SOCKET WITH NO KEEPALIVE IS HOW THE HEARTBEAT DIED (round 101).
#
# psycopg2 talks to Postgres over a plain blocking socket. Given no
# keepalives, a connection whose peer has gone away — a managed-Postgres
# failover, an idle NAT mapping dropped by the network in between — does not
# raise: the next execute() sits in recv() waiting for an answer that will
# never come, forever, with no timeout of any kind.
#
# heartbeat_forever runs on ONE shared Db, in one thread, in a `while True`.
# So one wedged socket there does not degrade the heartbeat; it ENDS it, for
# the life of the process, silently. Every in-flight job then stops being
# heartbeated while the process is otherwise perfectly healthy — still
# claiming work, still rendering, still answering — and 120s later
# (STALE_AFTER_S) the reaper marks each long-running one "Worker died and
# retries are exhausted".
#
# That is the top live failure in the product. In the last-50-user audit it
# killed 8 real users' agent turns on Aug 8 alone, five of them inside eleven
# minutes (jobs 3389/3411/3423/3430/3431) — the cluster shape a stuck
# heartbeat makes, not the scattered shape a crash makes. The tool that ran
# last before each death was different every time (get_kept_transcript,
# list_assets, reset_edit, add_zoom, render_preview…) because the tool was
# never the problem, and the gap from that tool to the reaper was 128-378s in
# every single case: just past the stale window, which is exactly what "the
# beats stopped" looks like and nothing else does.
#
# So: bound every phase of the connection.
#   connect_timeout  — the connect phase cannot hang.
#   keepalives       — the kernel probes an idle socket and the connection
#                      FAILS (raising OperationalError, which Db.run already
#                      reconnects through) instead of blocking. Sized to
#                      detect inside ~60s: comfortably under STALE_AFTER_S, so
#                      a beat recovers before the reaper can act on its silence.
#   statement_timeout — server-side backstop. Every query here is an indexed
#                      single-row write or a small select; one that has run for
#                      a minute is wedged, not slow.
_CONNECT_KW = {
    "connect_timeout": int(os.getenv("PGCONNECT_TIMEOUT_S", "10")),
    "keepalives": 1,
    "keepalives_idle": int(os.getenv("PGKEEPALIVE_IDLE_S", "30")),
    "keepalives_interval": int(os.getenv("PGKEEPALIVE_INTERVAL_S", "10")),
    "keepalives_count": int(os.getenv("PGKEEPALIVE_COUNT", "3")),
    "options": "-c statement_timeout=%d"
               % (int(os.getenv("PGSTATEMENT_TIMEOUT_S", "60")) * 1000),
}


def connect():
    conn = psycopg2.connect(config.DATABASE_URL, cursor_factory=RealDictCursor,
                            **_CONNECT_KW)
    conn.autocommit = False
    return conn


class Db:
    """One per worker thread. Reconnects on connection loss."""

    def __init__(self):
        self._conn = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = connect()
        return self._conn

    def reset(self):
        try:
            if self._conn and not self._conn.closed:
                self._conn.rollback()
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    def run(self, fn, *args, **kwargs):
        """Run fn(conn, ...) with one reconnect retry on connection errors."""
        for attempt in (1, 2):
            try:
                out = fn(self.conn, *args, **kwargs)
                self.conn.commit()
                return out
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self.reset()
                if attempt == 2:
                    raise
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:
                    self.reset()
                raise


# ------------------------------------------------------------------ #
#  Job queue                                                           #
# ------------------------------------------------------------------ #

_CLAIMS_COL = {"ok": False, "checked_at": 0.0}
_CLAIMS_RECHECK_S = 60.0


def claims_column_ready(conn):
    """True once 014_total_claims.sql has run. Cached once True.

    Same pattern (and same reason) as backend.billing.columns_ready: prod
    schema is applied by hand from the Render shell, so this code has to
    survive the window between the deploy and the psql. Until the column
    exists the queue behaves exactly as it did before — refundable `attempts`
    only, which is the pre-round-73 behaviour, not a broken one.
    """
    if _CLAIMS_COL["ok"]:
        return True
    if time.time() - _CLAIMS_COL["checked_at"] < _CLAIMS_RECHECK_S:
        return False
    _CLAIMS_COL["checked_at"] = time.time()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n FROM information_schema.columns
                 WHERE table_name = 'video_jobs' AND column_name = 'total_claims'
            """)
            row = cur.fetchone()
        n = (row or {}).get("n") if isinstance(row, dict) else (row or [0])[0]
        _CLAIMS_COL["ok"] = bool(n)
    except Exception as e:                                  # pragma: no cover
        print(f"[db] total_claims probe failed: {e}", flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    return _CLAIMS_COL["ok"]


def claim_job(conn, types, max_attempts):
    # Previews (always a user or agent actively waiting) jump the queue over
    # finals, and both jump over indexing — a turn's render_preview never
    # waits behind another project's index job.
    #
    # WITHIN a class, subscribers claim first (Aug 10): both of that day's
    # paying users sat 19-23 minutes in the agent queue behind free users'
    # half-hour turns (jobs 3707/4395 — the wait was the whole latency).
    # Free jobs still run whenever no paid work is queued; under sustained
    # paid load a free turn waits, and that trade is deliberate.
    #
    # TWO counters, deliberately. `attempts` is the refundable fairness budget
    # (release_jobs hands one back after a deploy); `total_claims` counts what
    # we have physically spent running this job and is NEVER given back. Job
    # 836 ran five 50-minute finals against a cap of three because only the
    # first counter existed. See config.MAX_CLAIMS_ABSOLUTE.
    #
    # Round 100 — ONE EDITOR PER PROJECT MOVES HERE. It used to live in the
    # backend as a 409 ("The editor is still working on your previous
    # request"), which turned a busy timeline into a user-facing error and,
    # combined with a starved lane, locked a real user out of chat for 14
    # minutes on Aug 8. Enforced at claim time instead: an agent-lane job
    # whose project already has a LIVE agent-lane job (running with a fresh
    # heartbeat) is simply skipped and picked up when the running one ends.
    # The backend can now accept and stack messages instead of refusing them.
    # Agent work serializes per project. Index work instead gets a dynamic
    # fair share: when another project is waiting, only the first N live/
    # older index jobs of this project are eligible. When nobody else waits,
    # the condition opens and a batch upload may use every lane. This prevents
    # one five-file upload from occupying all index threads for minutes while
    # a different user's tiny clip sits behind it.
    has_claims = claims_column_ready(conn)
    claims_set = ", total_claims = COALESCE(total_claims, 0) + 1" if has_claims else ""
    claims_where = "AND COALESCE(total_claims, 0) < %s" if has_claims else ""
    serialize = any(t in ("agent_turn", "shorts_plan", "mcp_tool")
                    for t in types)
    serial_where = ""
    index_fair_where = ""
    params = [list(types), max_attempts]
    if has_claims:
        params.append(config.MAX_CLAIMS_ABSOLUTE)
    params.append(config.STALE_AFTER_S)
    if serialize:
        # A sibling blocks this row when it is RUNNING with a fresh heartbeat
        # (the live editor), or QUEUED with a lower id and attempts left (the
        # row every lane would claim first — making the OLDEST live job the
        # only claimable one is what closes the READ COMMITTED window where
        # two lanes could each claim one of two queued turns of the same
        # project). A stale-running or spent sibling blocks nothing: those
        # are the reaper's to bury, and a dead row must never wedge its
        # project's queue behind it.
        serial_where = """
                  AND NOT EXISTS (
                      SELECT 1 FROM video_jobs live
                      WHERE live.project_id = video_jobs.project_id
                        AND live.id <> video_jobs.id
                        AND live.type IN ('agent_turn', 'shorts_plan',
                                          'mcp_tool')
                        AND ((live.state = 'running'
                              AND live.heartbeat_at >= NOW()
                                  - make_interval(secs => %s))
                             OR (live.state = 'queued'
                                 AND live.id < video_jobs.id
                                 AND live.attempts < %s)))"""
        params.extend([config.STALE_AFTER_S, max_attempts])
    if tuple(types) == ("index",):
        # The lower-id queued rows make the rank stable even while another
        # claim transaction has locked the first row but not committed its
        # RUNNING update yet. That is the same READ COMMITTED race closure as
        # agent serialization above. The outer NOT EXISTS preserves full
        # throughput for a solo project; the rank applies only during real
        # cross-project contention.
        index_fair_where = """
                  AND (
                    NOT EXISTS (
                      SELECT 1 FROM video_jobs waiting
                      WHERE waiting.project_id <> video_jobs.project_id
                        AND waiting.type = 'index'
                        AND waiting.state = 'queued'
                        AND waiting.attempts < %s)
                    OR (
                      SELECT COUNT(*) FROM video_jobs ahead
                      WHERE ahead.project_id = video_jobs.project_id
                        AND ahead.id <> video_jobs.id
                        AND ahead.type = 'index'
                        AND ((ahead.state = 'running'
                              AND ahead.heartbeat_at >= NOW()
                                  - make_interval(secs => %s))
                             OR (ahead.state = 'queued'
                                 AND ahead.id < video_jobs.id
                                 AND ahead.attempts < %s)))
                       < %s)"""
        params.extend([max_attempts, config.STALE_AFTER_S, max_attempts,
                       config.INDEX_FAIR_SHARE_PER_PROJECT])
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE video_jobs
            SET state = 'running', attempts = attempts + 1{claims_set},
                heartbeat_at = NOW(), updated_at = NOW(), error = NULL
            WHERE id = (
                SELECT video_jobs.id FROM video_jobs
                LEFT JOIN users u ON u.id = video_jobs.user_id
                WHERE type = ANY(%s)
                  AND attempts < %s
                  {claims_where}
                  AND (state = 'queued'
                       OR (state = 'running'
                           AND heartbeat_at < NOW() - make_interval(secs => %s)))
                  {serial_where}
                  {index_fair_where}
                ORDER BY CASE type WHEN 'preview' THEN 0
                                   WHEN 'final' THEN 1 ELSE 2 END,
                         COALESCE(u.is_subscribed, 0) DESC,
                         video_jobs.id
                FOR UPDATE OF video_jobs SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        """, tuple(params))
        return cur.fetchone()


def fail_ceilinged_jobs(conn):
    """Jobs that burned through MAX_CLAIMS_ABSOLUTE become failed, loudly.

    Without this they would be invisible rather than bounded: claim_job simply
    stops selecting them, so the row sits `queued` forever and the studio spins
    on a job no worker will ever pick up again. A ceiling that strands the user
    is not better than no ceiling — it just moves the damage off the invoice
    and onto the person waiting.
    """
    if not claims_column_ready(conn):
        return []
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE video_jobs
            SET state = 'failed', updated_at = NOW(),
                error = COALESCE(error, 'This job was restarted too many times '
                                        'and has been stopped. Please try again.')
            WHERE COALESCE(total_claims, 0) >= %s
              AND (state = 'queued'
                   OR (state = 'running'
                       AND heartbeat_at < NOW() - make_interval(secs => %s)))
            RETURNING id, type, project_id, user_id, error, payload
        """, (config.MAX_CLAIMS_ABSOLUTE, config.STALE_AFTER_S))
        return cur.fetchall()


def fail_exhausted_jobs(conn):
    """Reaper: stale running jobs with no attempts left become failed.
    Returns the failed rows so the caller can surface each in chat."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE video_jobs
            SET state = 'failed', updated_at = NOW(),
                error = COALESCE(error, 'Worker died and retries are exhausted')
            WHERE state = 'running'
              AND heartbeat_at < NOW() - make_interval(secs => %s)
              AND attempts >= CASE WHEN type IN ('agent_turn', 'mcp_tool')
                                   THEN %s ELSE %s END
            RETURNING id, type, project_id, user_id, error, payload
        """, (config.STALE_AFTER_S, config.MAX_ATTEMPTS_AGENT,
              config.MAX_ATTEMPTS_MEDIA))
        return cur.fetchall()


def release_jobs(conn, ids):
    """Hand in-flight jobs back to the queue WITHOUT charging them an attempt.

    Render SIGTERMs the container before replacing it on every deploy, and an
    index of a long video is ~16 minutes of work — so a deploy lands on one
    routinely. claim_job counts EVERY claim as an attempt, including the
    re-claim after a death the job did nothing to cause, so deploys ate a real
    customer's 3 attempts and her 24-min video died as "Worker died and retries
    are exhausted". Her third and final death was literally the redeploy from
    setting an env var. A planned shutdown is our fault, not the job's.

    agent_turn is deliberately excluded: it is MAX_ATTEMPTS_AGENT=1 on purpose
    because a turn has side effects (it writes EDL versions), so replaying one
    could re-apply work. Those still die honestly via the reaper's "I lost my
    connection — please send it again".

    This refund is still right, and it is deliberately NOT what bounds cost:
    total_claims (claim_job) counts the runs we actually paid for and is never
    handed back. Before it existed this line was the whole reason final job=836
    could run five 50-minute encodes against MAX_ATTEMPTS_MEDIA=3 — 50 deploys
    in a week, and every one of them bought that job another life.
    """
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET state = 'queued', progress = 0,
                           attempts = GREATEST(0, attempts - 1),
                           updated_at = NOW()
                       WHERE id = ANY(%s) AND state = 'running'
                         AND type <> 'agent_turn'
                       RETURNING id""", (list(ids),))
        return len(cur.fetchall())


def active_job_ids():
    with _ACTIVE_LOCK:
        return list(ACTIVE_JOBS)


def set_progress(conn, job_id, progress, attempts=None, total_claims=None):
    """The `state = 'running'` clause is what makes a cancellation STICK.

    This writes heartbeat_at, so without it a job cancelled out from under a
    still-live process keeps getting heartbeated by its own zombie: the row
    reads `failed` with a 0-second-old heartbeat forever, the reaper's
    staleness rule is lied to, and there is no way to tell from the DB whether
    the process actually died. That is not theoretical — a hung render kept
    stamping progress=89 onto a job that had been failed for twenty minutes,
    and it made "is it dead yet?" unanswerable. heartbeat_forever already has
    this clause; this is the same rule, applied to the other writer.

    RETURNS whether the row is still ours (round 73). That answer was already
    being computed by the WHERE clause above and thrown away, which is why the
    executor had no way to learn it had been abandoned: when the dispatcher's
    REMOTE_EXECUTOR_TIMEOUTS fired it requeued and retried, while the original
    Cloud Run instance rendered the dead job to completion beside the retry —
    two instances, 16 vCPU, one job. Nothing polls; this is the write the
    renderer already makes every 3 seconds.

    `total_claims` is the real execution lease. Unlike `attempts`, it is never
    refunded during a graceful dispatcher deploy, so a replacement claim can
    never receive the same value as the executor it superseded. `attempts` is
    retained as a compatibility fallback for pre-lease callers.
    """
    with conn.cursor() as cur:
        if total_claims is not None:
            cur.execute("""UPDATE video_jobs
                           SET progress = %s, heartbeat_at = NOW(),
                               updated_at = NOW()
                           WHERE id = %s AND state = 'running'
                             AND total_claims = %s""",
                        (min(100, max(0, int(progress))), job_id,
                         total_claims))
        elif attempts is None:
            cur.execute("""UPDATE video_jobs
                           SET progress = %s, heartbeat_at = NOW(),
                               updated_at = NOW()
                           WHERE id = %s AND state = 'running'""",
                        (min(100, max(0, int(progress))), job_id))
        else:
            cur.execute("""UPDATE video_jobs
                           SET progress = %s, heartbeat_at = NOW(),
                               updated_at = NOW()
                           WHERE id = %s AND state = 'running'
                             AND attempts = %s""",
                        (min(100, max(0, int(progress))), job_id, attempts))
        return cur.rowcount > 0


def lease_is_current(conn, job_id, total_claims):
    """Cheap preflight before an executor downloads or encodes anything."""
    if job_id is None or total_claims is None:
        return True
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE id = %s AND state = 'running'
                         AND total_claims = %s""",
                    (job_id, total_claims))
        return cur.fetchone() is not None


def finish_job(conn, job_id, state, error=None, result=None,
               total_claims=None):
    """Finish only the execution lease that produced this result.

    A timed-out or deploy-orphaned request can return after its replacement.
    Without the lease predicate that stale response can overwrite the new
    run's state/result even if progress cancellation worked correctly.
    """
    with conn.cursor() as cur:
        lease_where = " AND total_claims = %s" if total_claims is not None else ""
        params = [state, (error or None) and str(error)[:2000],
                  Json(_json_safe(result)) if result is not None else None,
                  state, job_id]
        if total_claims is not None:
            params.append(total_claims)
        cur.execute(f"""UPDATE video_jobs
                        SET state = %s, error = %s, result = %s,
                            progress = CASE WHEN %s = 'done' THEN 100 ELSE progress END,
                            updated_at = NOW()
                        WHERE id = %s AND state = 'running'{lease_where}""",
                    tuple(params))
        return cur.rowcount > 0


def _json_safe(value):
    """Return strict-JSON data without mutating the runner's result.

    PostgreSQL rejects NaN and +/-Infinity. Media analyzers can legitimately
    emit those for silence or missing measurements, and a terminal metadata
    quirk must never turn a completed encode into another executor attempt.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def requeue_job(conn, job_id, error, total_claims=None):
    """Only a job we STILL HOLD goes back on the queue.

    Same rule as release_jobs and set_progress, and the last writer that was
    missing it. Round 73 made it reachable: an abandoned executor now raises
    instead of rendering a dead job to completion, and without this clause that
    error would walk into the reaper's terminal row and set it `queued` again —
    resurrecting a job the user has already been told died, and buying it
    another 8-vCPU run in the process. Returns whether it actually requeued so
    the caller's log cannot claim a requeue that did not happen.
    """
    with conn.cursor() as cur:
        lease_where = " AND total_claims = %s" if total_claims is not None else ""
        params = [str(error)[:2000], job_id]
        if total_claims is not None:
            params.append(total_claims)
        cur.execute(f"""UPDATE video_jobs
                        SET state = 'queued', error = %s, updated_at = NOW()
                        WHERE id = %s AND state = 'running'{lease_where}""",
                    tuple(params))
        return cur.rowcount > 0


def get_job(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM video_jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


_metrics_table_ok = None


def bump_metric(conn, name, n=1):
    """Add n to a named reliability counter (migration 017) — and never let
    that be the story: this is called from failure paths (a job dying, a
    tool refusing), where an exception here would replace the real event
    with a bookkeeping one.

    ALWAYS call this in its own transaction (its own Db.run), never inside
    another operation's — a failed statement aborts the whole postgres
    transaction, and the swallowed error would silently roll back the very
    write it was annotating. to_regclass is checked first, same as
    video_settings: a worker deployed before the migration must not poison
    anything, it just counts nothing yet."""
    global _metrics_table_ok
    try:
        if not n:
            return
        with conn.cursor() as cur:
            # Only a POSITIVE probe is cached: bumps are rare (failures), so
            # re-probing while the table is absent costs nothing and means a
            # migration applied after boot starts counting without a restart.
            if _metrics_table_ok is not True:
                cur.execute(
                    "SELECT to_regclass('public.metrics_counters') AS t")
                _metrics_table_ok = bool((cur.fetchone() or {}).get("t"))
                if not _metrics_table_ok:
                    return
            cur.execute("""INSERT INTO metrics_counters (name, count)
                           VALUES (%s, %s)
                           ON CONFLICT (name) DO UPDATE
                           SET count = metrics_counters.count
                                       + EXCLUDED.count,
                               updated_at = NOW()""", (name, int(n)))
    except Exception as e:
        print(f"[metrics] bump {name} failed: {e}", flush=True)


def user_has_prior_agent_turn(conn, user_id, before_job_id):
    """Round 81: anything but this user's FIRST agent turn ever? Consulted
    only while the first-turn model lane is configured, so the common case
    costs no query at all."""
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE user_id = %s AND type = 'agent_turn'
                         AND id < %s LIMIT 1""",
                    (user_id, int(before_job_id)))
        return cur.fetchone() is not None


def enqueue_job(conn, project_id, user_id, jtype, payload):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO video_jobs (project_id, user_id, type, payload)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (project_id, user_id, jtype, Json(payload)))
        return cur.fetchone()["id"]


def pending_preview_job(conn, project_id, edl_version):
    """The id of a queued/running preview of THIS project at THIS EDL
    version, or None. How render_preview adopts the loop's speculative
    encode (round 98) instead of paying for the same render twice."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'preview'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, str(edl_version)))
        row = cur.fetchone()
        return row["id"] if row else None


def get_or_enqueue_preview_job(conn, project_id, user_id, payload):
    """Atomically join or enqueue one live preview for an EDL version.

    ``pending_preview_job`` followed by ``enqueue_job`` is a check-then-insert
    race across agent turns, the backend self-heal, and speculative rendering.
    Production accumulated up to eleven successful previews of one version.
    A transaction advisory lock is shared with no long-running work: it covers
    only this lookup+insert and disappears at commit.

    Returns ``(job_id, created)``.
    """
    version = int((payload or {}).get("edl_version"))
    with conn.cursor() as cur:
        # The two-int namespace keeps this lock separate from future project
        # locks while making the key stable across Python processes.
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (int(project_id), version))
        # Once a newer immutable EDL exists, no older preview can ever become
        # the player's current picture. Marking a RUNNING row done makes its
        # next fenced progress write return False; the executor watchdog then
        # kills ffmpeg within seconds instead of finishing obsolete work.
        cur.execute("""UPDATE video_jobs
                       SET state = 'done', result = %s, updated_at = NOW()
                       WHERE project_id = %s AND type = 'preview'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' ~ '^[0-9]+$'
                         AND (payload->>'edl_version')::int < %s""",
                    (Json({"superseded_by": version}), project_id, version))
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'preview'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, str(version)))
        row = cur.fetchone()
        if row:
            return row["id"], False
        signature = str((payload or {}).get("render_signature") or "")
        if signature and not (payload or {}).get("force"):
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'preview'
                             AND state = 'failed'
                             AND payload->>'render_signature' = %s
                             AND created_at > NOW() - INTERVAL '30 minutes'
                             AND COALESCE(
                                 (result->'failure'->>'retryable')::boolean,
                                 false) = false
                           ORDER BY id DESC LIMIT 1""",
                        (project_id, signature))
            row = cur.fetchone()
            if row:
                return row["id"], False
        cur.execute("""INSERT INTO video_jobs
                          (project_id, user_id, type, payload)
                       VALUES (%s, %s, 'preview', %s) RETURNING id""",
                    (project_id, user_id, Json(payload)))
        return cur.fetchone()["id"], True


def get_or_enqueue_preview_check_job(conn, project_id, user_id, payload):
    """Atomically join one changed-section proof for an immutable EDL.

    Proof reels are intentionally a separate job type from ``preview``: their
    short file is evidence for the editor, not the complete file the Studio
    player should adopt.  The ranges are part of the identity so a broader
    later check of the same EDL cannot accidentally join a narrower one.
    """
    version = int((payload or {}).get("edl_version"))
    ranges = (payload or {}).get("check_ranges") or []
    with conn.cursor() as cur:
        # Negative version gives proof jobs their own advisory-lock namespace
        # without introducing a schema migration.
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (int(project_id), -version))
        cur.execute("""UPDATE video_jobs
                       SET state = 'done', result = %s, updated_at = NOW()
                       WHERE project_id = %s AND type = 'preview_check'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' ~ '^[0-9]+$'
                         AND (payload->>'edl_version')::int < %s""",
                    (Json({"superseded_by": version}), project_id, version))
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'preview_check'
                         AND state IN ('queued', 'running')
                         AND payload->>'edl_version' = %s
                         AND payload->'check_ranges' = %s::jsonb
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, str(version), Json(ranges)))
        row = cur.fetchone()
        if row:
            return row["id"], False
        signature = str((payload or {}).get("render_signature") or "")
        if signature:
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'preview_check'
                             AND state = 'failed'
                             AND payload->>'render_signature' = %s
                             AND created_at > NOW() - INTERVAL '30 minutes'
                             AND COALESCE(
                                 (result->'failure'->>'retryable')::boolean,
                                 false) = false
                           ORDER BY id DESC LIMIT 1""",
                        (project_id, signature))
            row = cur.fetchone()
            if row:
                return row["id"], False
        cur.execute("""INSERT INTO video_jobs
                          (project_id, user_id, type, payload)
                       VALUES (%s, %s, 'preview_check', %s) RETURNING id""",
                    (project_id, user_id, Json(payload)))
        return cur.fetchone()["id"], True


def reserve_batch_launch(conn, job_id, total_claims):
    """Persist an idempotency key before asking Cloud Run to start a Job."""
    with conn.cursor() as cur:
        marker = Json({"batch_launch_claim": int(total_claims)})
        cur.execute("""UPDATE video_jobs
                       SET payload = COALESCE(payload, '{}'::jsonb) || %s,
                           updated_at = NOW()
                       WHERE id = %s AND state = 'running'
                         AND total_claims = %s
                         AND COALESCE(payload->>'batch_launch_claim', '')
                             <> %s
                       RETURNING id""",
                    (marker, job_id, total_claims, str(total_claims)))
        return cur.fetchone() is not None


def record_batch_launch(conn, job_id, total_claims, operation):
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET payload = COALESCE(payload, '{}'::jsonb) || %s,
                           heartbeat_at = NOW(), updated_at = NOW()
                       WHERE id = %s AND state = 'running'
                         AND total_claims = %s
                         AND payload->>'batch_launch_claim' = %s""",
                    (Json({"batch_operation": str(operation)[:500]}),
                     job_id, total_claims, str(total_claims)))
        return cur.rowcount > 0


def clear_batch_launch(conn, job_id, total_claims):
    """Allow safe request-service fallback after a definite launch refusal."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET payload = COALESCE(payload, '{}'::jsonb)
                                     - 'batch_launch_claim'
                                     - 'batch_operation',
                           updated_at = NOW()
                       WHERE id = %s AND state = 'running'
                         AND total_claims = %s
                         AND payload->>'batch_launch_claim' = %s""",
                    (job_id, total_claims, str(total_claims)))
        return cur.rowcount > 0


def kv_get(conn, key):
    """One value from app_kv, or None — including when the table itself
    does not exist yet (migration 016 may land after the code that wants
    it; a worker must keep booting and fetching either way)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return None
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def kv_put(conn, key, value):
    """Upsert one app_kv row; a no-op when the table is missing, for the
    same must-not-hurt-a-boot reason as kv_get."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (key, value))


def publish_mcp_catalog(conn, catalog):
    """Publish the tool catalog the MCP surface serves (see mcp_exec.catalog).

    Written on every worker boot because only THIS process knows which tools
    are really available: the honest-off gating depends on the worker's own
    env (image key, stock key, music pack), and the backend has neither those
    imports nor those variables. A catalog the backend guessed at would offer
    the outside model tools that can only answer "unavailable"."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO mcp_catalog (id, json, updated_at)
                       VALUES (1, %s, NOW())
                       ON CONFLICT (id) DO UPDATE
                       SET json = EXCLUDED.json, updated_at = NOW()""",
                    (Json(catalog),))


# ------------------------------------------------------------------ #
#  Heartbeat — one daemon thread covers every active job               #
# ------------------------------------------------------------------ #

ACTIVE_JOBS = set()
_ACTIVE_LOCK = threading.Lock()


def track_job(job_id):
    with _ACTIVE_LOCK:
        ACTIVE_JOBS.add(job_id)


def untrack_job(job_id):
    with _ACTIVE_LOCK:
        ACTIVE_JOBS.discard(job_id)


def heartbeat_forever():
    """Keep every in-flight job's heartbeat fresh so the reaper leaves live
    work alone.

    This loop is load-bearing in a way its size hides: if it stops, nothing
    else notices, and every long job on this process is failed by the reaper
    from another thread while the work itself is running fine. So it is
    written to survive its own database going away — see _CONNECT_KW for the
    socket-level half of that, and note the two rules here:

      * a failed beat DROPS THE CONNECTION. Db.run only reconnects on the
        errors it recognises as connection loss; a statement timeout arrives
        as a plain QueryCanceled and would otherwise leave this thread
        re-using a session that has already proven unhealthy.
      * a run of failures is said OUT LOUD, once, with the consequence spelled
        out — the silent version of this cost eight real users their turn on
        one day before anyone knew the beats had stopped.
    """
    hdb = Db()
    fails = 0
    last_ok = time.time()
    while True:
        time.sleep(config.HEARTBEAT_EVERY_S)
        with _ACTIVE_LOCK:
            ids = list(ACTIVE_JOBS)
        if not ids:
            last_ok = time.time()
            continue
        # RSS beside the job ids, every beat. 19 turns died as "Worker died
        # and retries are exhausted" over 3 days (Aug 7-9) with no epitaph;
        # if the killer is memory, the LAST logged beat now names the number
        # the process died at. Linux-only read, never allowed to break beat.
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS"):
                        print(f"[heartbeat] jobs={ids} rss="
                              f"{line.split()[1]}kB", flush=True)
                        break
        except OSError:
            pass
        try:
            def _beat(conn):
                with conn.cursor() as cur:
                    cur.execute("""UPDATE video_jobs
                                   SET heartbeat_at = NOW()
                                   WHERE id = ANY(%s) AND state = 'running'""",
                                (ids,))
            hdb.run(_beat)
            if fails:
                print(f"[heartbeat] recovered after {fails} failed beat(s) — "
                      f"{len(ids)} job(s) still alive", flush=True)
            fails, last_ok = 0, time.time()
        except Exception as e:
            fails += 1
            hdb.reset()
            stale_in = config.STALE_AFTER_S - (time.time() - last_ok)
            print(f"[heartbeat] beat {fails} FAILED ({str(e)[:160]}) — "
                  f"{len(ids)} running job(s) will be reaped as 'Worker died' "
                  f"in {stale_in:.0f}s if this does not recover", flush=True)


# ------------------------------------------------------------------ #
#  Domain helpers                                                      #
# ------------------------------------------------------------------ #

def get_project(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        return cur.fetchone()


def set_project_kind(conn, project_id, kind):
    """Worker-side mode steering after the source duration is known."""
    if kind not in ("edit", "shorts"):
        raise ValueError("project kind must be edit or shorts")
    with conn.cursor() as cur:
        cur.execute("UPDATE projects SET kind = %s WHERE id = %s",
                    (kind, project_id))


def get_asset(conn, asset_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM assets WHERE id = %s", (asset_id,))
        return cur.fetchone()


def latest_asset(conn, project_id, kind):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, kind))
        return cur.fetchone()


def asset_by_key(conn, project_id, storage_key):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND storage_key = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, storage_key))
        return cur.fetchone()


def indexed_clips(conn, project_id, limit=80):
    """Uploaded video clips whose perception pass finished (round 84) —
    every one of these has a filmstrip + transcript in `indexes` keyed by
    its sha256. Oldest first, so filmstrip order matches upload order."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'video_clip'
                         AND sha256 IS NOT NULL
                         AND COALESCE(meta->>'indexed', '') = 'true'
                         AND COALESCE(meta->>'role', '') <> 'shorts_reference'
                       ORDER BY id ASC LIMIT %s""", (project_id, limit))
        return cur.fetchall()


def tray_pending_assets(conn, project_id):
    """Assets a tray submit marked for timeline placement
    (meta.tray_place = {order, before_main, duration_s}) that no one has
    placed yet. Ordered by the user's tray arrangement."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s
                         AND kind IN ('video_clip', 'image_ref')
                         AND meta ? 'tray_place'
                         AND (meta->'tray_place') IS NOT NULL
                         AND jsonb_typeof(meta->'tray_place') = 'object'
                       ORDER BY COALESCE(
                           (meta->'tray_place'->>'order')::float, 1e9),
                                id ASC""", (project_id,))
        return cur.fetchall()


def rescue_abandoned_trays(conn):
    """Commit a tray that was uploaded to and then never submitted.

    Round 101, from the last-50-user audit. The tray (round 84) asks for a
    Submit press before an upload becomes the project's main footage, and a
    press that never comes leaves the studio permanently dead: no `original`,
    no index, no filmstrip, no greeting — an upload the user watched succeed
    and a page that then does nothing forever. Ten users in three weeks
    (3.5% of everyone who opened a project) ended their whole session there,
    and EIGHT of them had staged exactly one file, where a tray has one
    possible arrangement and nothing to arrange.

    Deliberately narrow, because a tray someone is still filling must never
    be committed under them:
      - the project has NO main footage and has NEVER had an index job, so
        there is no working state this could disturb — only a dead one it can
        revive;
      - nothing has been uploaded or touched for TRAY_RESCUE_AFTER_S, so a
        multi-file batch mid-arrival is left alone;
      - the WHOLE tray is committed, exactly as the Submit button commits it:
        the first video becomes the footage, every other clip/image keeps its
        arrangement as a tray_place for the indexer's existing sweep, and
        every video gets the same perception pass. A rescue that revived the
        project but silently dropped the user's other four uploads would be a
        second bug wearing the fix's clothes.

    Returns one (project_id, user_id, asset_id, index_job_id) per rescue.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT s.project_id, p.user_id
            FROM assets s
            JOIN projects p ON p.id = s.project_id
            WHERE COALESCE(s.meta->>'staged', '') = 'true'
              AND s.kind = 'video_clip'
              AND NOT EXISTS (SELECT 1 FROM assets o
                              WHERE o.project_id = s.project_id
                                AND o.kind = 'original')
              AND NOT EXISTS (SELECT 1 FROM video_jobs j
                              WHERE j.project_id = s.project_id
                                AND j.type = 'index')
              AND NOT EXISTS (SELECT 1 FROM assets q
                              WHERE q.project_id = s.project_id
                                AND q.created_at > NOW()
                                    - make_interval(secs => %s))
            LIMIT 20
        """, (config.TRAY_RESCUE_AFTER_S,))
        targets = cur.fetchall()
    out = []
    for t in targets:
        pid, uid = t["project_id"], t["user_id"]
        with conn.cursor() as cur:
            # Re-check under the project lock: a real submit landing between
            # the scan and here must win, and this is the same lock that
            # route takes.
            cur.execute("SELECT id FROM projects WHERE id = %s FOR UPDATE",
                        (pid,))
            cur.execute("""SELECT 1 FROM assets WHERE project_id = %s
                             AND kind = 'original' LIMIT 1""", (pid,))
            if cur.fetchone():
                continue
            cur.execute("""SELECT id, kind, duration_s FROM assets
                           WHERE project_id = %s
                             AND COALESCE(meta->>'staged','') = 'true'
                           ORDER BY COALESCE((meta->>'tray_pos')::float, 1e9),
                                    id""", (pid,))
            tray = cur.fetchall()
            main_i = next((i for i, a in enumerate(tray)
                           if a["kind"] == "video_clip"), None)
            if main_i is None:
                continue
            main_job = None
            for i, a in enumerate(tray):
                if i == main_i:
                    cur.execute(
                        """UPDATE assets SET kind = 'original',
                             meta = COALESCE(meta,'{}'::jsonb)
                                    - 'staged' - 'tray_pos'
                           WHERE id = %s AND kind = 'video_clip'
                             AND COALESCE(meta->>'staged','') = 'true'""",
                        (a["id"],))
                    if cur.rowcount != 1:
                        break
                    cur.execute(
                        """INSERT INTO video_jobs (project_id, user_id, type,
                                                   payload)
                           VALUES (%s, %s, 'index', %s) RETURNING id""",
                        (pid, uid, Json({"asset_id": a["id"]})))
                    main_job = cur.fetchone()["id"]
                    continue
                patch = {"staged": None}
                if a["kind"] in ("video_clip", "image_ref"):
                    patch["tray_place"] = {"order": i,
                                           "before_main": i < main_i,
                                           "duration_s": a["duration_s"]}
                cur.execute("""UPDATE assets
                               SET meta = COALESCE(meta,'{}'::jsonb) || %s
                               WHERE id = %s""", (Json(patch), a["id"]))
                if a["kind"] in ("video_clip", "music"):
                    cur.execute(
                        """INSERT INTO video_jobs (project_id, user_id, type,
                                                   payload)
                           VALUES (%s, %s, 'index', %s)""",
                        (pid, uid, Json({"asset_id": a["id"]})))
            if main_job is not None:
                out.append((pid, uid, tray[main_i]["id"], main_job))
    return out


def staged_assets(conn, project_id):
    """Uploads sitting in the staging tray — not yet submitted to the
    timeline. Ordered by the tray position the client last saved, then id."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s
                         AND COALESCE(meta->>'staged', '') = 'true'
                       ORDER BY COALESCE((meta->>'tray_pos')::float, 1e9),
                                id ASC""", (project_id,))
        return cur.fetchall()


def extracted_audio_asset(conn, project_id, source_key, source_sha=None):
    """The music asset previously extracted from THIS video, or None.

    Matched on the source's sha as well as its storage key: the studio mints a
    fresh object per upload, so a user who attaches the same file twice — which
    is exactly what someone does when the first attempt was refused — would
    otherwise pay the download + encode again for audio we already have.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'music'
                         AND (meta->>'from_asset_key' = %s
                              OR (%s <> '' AND meta->>'from_sha256' = %s))
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, source_key, source_sha or "",
                     source_sha or ""))
        return cur.fetchone()


def any_asset_by_sha(conn, kind, sha256):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE kind = %s AND sha256 = %s
                       ORDER BY id DESC LIMIT 1""", (kind, sha256))
        return cur.fetchone()


def project_asset_keys_by_sha(conn, project_id, sha256):
    """Storage aliases for identical bytes inside one project.

    The upload tray can legitimately register the same file as both the main
    ``original`` and a ``video_clip``.  A final that splices that clip should
    open the already-local original a second time, not download another 12 GB
    copy of identical bytes into executor scratch.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT storage_key FROM assets
                       WHERE project_id = %s AND sha256 = %s
                         AND storage_key IS NOT NULL""",
                    (project_id, sha256))
        return [r["storage_key"] for r in cur.fetchall()]


def latest_render_version(conn, project_id, variant):
    """EDL version of the newest render of this variant, or None — the
    baseline for round 81's verify plan: the render the user last SAW is the
    state a "what changed" claim must be written against."""
    with conn.cursor() as cur:
        cur.execute("""SELECT (meta->>'edl_version')::int AS v FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, variant))
        row = cur.fetchone()
        return row["v"] if row else None


def latest_render_asset(conn, project_id, variant):
    """The newest render of this variant regardless of version — the stitch
    base (round 93): a preview built by re-encoding only what changed is
    spliced into whatever the user most recently watched."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, variant))
        return cur.fetchone()


def find_render_asset(conn, project_id, variant, edl_version):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                         AND (meta->>'edl_version')::int = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, variant, int(edl_version)))
        return cur.fetchone()


def latest_render(conn, project_id, variant):
    """The newest render of a variant REGARDLESS of EDL version — what the
    project last actually produced. find_render_asset answers "is version N
    rendered"; this answers "is there anything to watch", which is the only
    question with a useful answer when the edit has moved on since."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, variant))
        return cur.fetchone()


def superseded_renders(conn, project_id, variant, edl_version, keep_asset_id):
    """Render assets for this exact (project, variant, version) that an id
    newer than keep_asset_id has replaced.

    Renders used to live at one fixed key per version, so a re-render simply
    overwrote the bytes and storage stayed bounded. Unique-per-render keys
    (see renderer._render_stamp) fixed recovery but made every superseded
    object immortal — a slow, permanent R2 leak paid for monthly. Scoped to a
    single version so version HISTORY is never touched: the studio lets users
    pin and replay older versions, and those renders must survive.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT id, storage_key, meta FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                         AND (meta->>'edl_version')::int = %s
                         AND id < %s""",
                    (project_id, variant, int(edl_version), int(keep_asset_id)))
        return cur.fetchall()


def stale_preview_checks(conn, project_id, keep_asset_id):
    """Disposable proof reels older than the newest one for this project.

    Complete previews are timeline history and must survive. A preview_check
    is an editor's short-lived scratch proof, never linked by the Studio
    player, so retaining one per EDL version would turn compute savings into
    permanent object-storage churn.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT id, storage_key, meta FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = 'preview_check'
                         AND id < %s""",
                    (project_id, int(keep_asset_id)))
        return cur.fetchall()


def delete_assets(conn, asset_ids):
    if not asset_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM assets WHERE id = ANY(%s)", (list(asset_ids),))
        return cur.rowcount


def assets_by_kinds(conn, project_id, kinds, limit=200):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = ANY(%s)
                       ORDER BY id DESC LIMIT %s""",
                    (project_id, list(kinds), limit))
        return cur.fetchall()


def update_asset_meta(conn, asset_id, patch):
    """Shallow-merge patch into assets.meta."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE assets
                       SET meta = COALESCE(meta, '{}'::jsonb) || %s::jsonb
                       WHERE id = %s""", (json.dumps(patch), asset_id))


def insert_asset(conn, project_id, kind, storage_key, *, bytes_=None,
                 duration_s=None, width=None, height=None, fps=None,
                 sha256=None, meta=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO assets (project_id, kind, storage_key, bytes,
                                duration_s, width, height, fps, sha256, meta)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (project_id, kind, storage_key, bytes_, duration_s, width, height,
              fps, sha256, Json(meta or {})))
        return cur.fetchone()["id"]


def update_asset_probe(conn, asset_id, duration_s, width, height, fps, sha256):
    with conn.cursor() as cur:
        cur.execute("""UPDATE assets
                       SET duration_s = %s, width = %s, height = %s,
                           fps = %s, sha256 = %s
                       WHERE id = %s""",
                    (duration_s, width, height, fps, sha256, asset_id))


def get_index_by_sha(conn, sha256):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM indexes WHERE video_sha256 = %s", (sha256,))
        return cur.fetchone()


def upsert_index(conn, project_id, sha256, index_json):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO indexes (project_id, video_sha256, json,
                                 pipeline_version)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (video_sha256)
            DO UPDATE SET json = EXCLUDED.json,
                          pipeline_version = EXCLUDED.pipeline_version
        """, (project_id, sha256, Json(index_json),
              config.PIPELINE_VERSION))


def set_index_perception(conn, sha256, perception_json, pipeline_version):
    """Merge ONLY the perception sidecar into an index row, atomically.

    Deliberately not upsert_index: the sidecar is computed over a
    minutes-long analysis window, and writing the whole row back would
    (a) clobber any transcript edit or re-index that landed meanwhile and
    (b) re-stamp pipeline_version, laundering a stale index as current and
    cancelling the backend's self-heal re-index. The single-statement
    jsonb_set touches nothing but the 'perception' key, and the
    pipeline_version guard makes this a silent no-op if a re-index replaced
    the row mid-analysis — the sidecar then simply recomputes next call."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE indexes
            SET json = jsonb_set(json, '{perception}', %s::jsonb)
            WHERE video_sha256 = %s AND pipeline_version = %s
        """, (json.dumps(perception_json), sha256, pipeline_version))


def set_index_spatial(conn, sha256, spatial_json, pipeline_version):
    """Merge only the spatial sidecar; see set_index_perception's race rule."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE indexes
            SET json = jsonb_set(json, '{spatial}', %s::jsonb)
            WHERE video_sha256 = %s AND pipeline_version = %s
        """, (json.dumps(spatial_json), sha256, pipeline_version))


def set_index_motion(conn, sha256, motion_json, pipeline_version):
    """Merge only the motion sidecar; see set_index_perception's race rule."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE indexes
            SET json = jsonb_set(json, '{motion}', %s::jsonb)
            WHERE video_sha256 = %s AND pipeline_version = %s
        """, (json.dumps(motion_json), sha256, pipeline_version))


def latest_edl(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 1""", (project_id,))
        return cur.fetchone()


def get_edl_version(conn, project_id, version):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls
                       WHERE project_id = %s AND version = %s""",
                    (project_id, version))
        return cur.fetchone()


def previous_edl_version(conn, project_id, before_version):
    """Newest immutable EDL before ``before_version`` for delta proofs."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls
                       WHERE project_id = %s AND version < %s
                       ORDER BY version DESC LIMIT 1""",
                    (project_id, int(before_version)))
        return cur.fetchone()


def insert_edl(conn, project_id, edl_json, created_by):
    """Append-only: always a new version row."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO edls (project_id, version, json, created_by)
            VALUES (%s,
                    (SELECT COALESCE(MAX(version), 0) + 1 FROM edls
                     WHERE project_id = %s),
                    %s, %s)
            RETURNING version
        """, (project_id, project_id, Json(edl_json), created_by))
        return cur.fetchone()["version"]


def edl_history(conn, project_id, limit=8):
    with conn.cursor() as cur:
        cur.execute("""SELECT version, created_by, created_at FROM edls
                       WHERE project_id = %s ORDER BY version DESC LIMIT %s""",
                    (project_id, limit))
        return cur.fetchall()


LLM_PAYLOAD_CAP = 200_000     # bytes of JSON per side, then truncated marker


def _capped_payload(obj):
    """Redact secrets and cap the stored JSON. The cap keeps llm_calls
    readable in the admin inspector without ever dropping a call."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if config.OPENAI_API_KEY:
        s = s.replace(config.OPENAI_API_KEY, "[REDACTED]")
    if config.IMAGE_API_KEY:
        s = s.replace(config.IMAGE_API_KEY, "[REDACTED]")
    if config.VISION_API_KEY:
        s = s.replace(config.VISION_API_KEY, "[REDACTED]")
    if len(s) > LLM_PAYLOAD_CAP:
        return {"_truncated": True, "_original_bytes": len(s),
                "_prefix": s[:LLM_PAYLOAD_CAP] + "…[truncated]"}
    return json.loads(s)


class PermanentJobError(RuntimeError):
    """A job failure no retry can change — the input itself is the reason
    (a 23s video asked for shorts, a silent video asked for speech cuts).
    run_job fails these immediately instead of burning the retry budget:
    jobs 3988/3989 each ran the same no-speech shorts_plan three times and
    then told the user to press the button again."""


class JobLeaseLost(RuntimeError):
    """This physical run was replaced; continuing would bill for no result."""


def recent_llm_tokens(conn, seconds=60):
    """Total tokens the whole fleet pushed through the model provider in the
    last `seconds`. The provider's TPM ceiling is org-wide, so a turn about
    to open a ~50K-token first call can check the shared burn instead of
    walking into a 429 (Aug 9 16:39: 375K tokens/min against a 200K limit)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(COALESCE(prompt_tokens, 0)
                                           + COALESCE(completion_tokens, 0)), 0)
                       FROM llm_calls
                       WHERE created_at > NOW() - make_interval(secs => %s)""",
                    (seconds,))
        row = cur.fetchone()
        return int(list(row.values())[0] if isinstance(row, dict) else row[0])


def reserve_llm_tokens(conn, estimated_tokens, soft_cap, window_s=60):
    """Atomically reserve org-wide TPM capacity before an agent call.

    Completed-call telemetry arrives too late to prevent two workers from
    starting large prompts together. A tiny rolling ledger in ``app_kv`` plus
    a transaction advisory lock makes admission fleet-wide without a schema
    migration. Returns seconds to wait; zero means the reservation is held.
    """
    estimate = max(1, min(int(estimated_tokens), int(soft_cap)))
    now = time.time()
    key = "agent_tpm_reservations_v1"
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (841731, 1))
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        table = cur.fetchone()
        table = table.get("t") if isinstance(table, dict) else table[0]
        if not table:
            return 0.0
        cur.execute("SELECT value FROM app_kv WHERE key = %s FOR UPDATE",
                    (key,))
        row = cur.fetchone()
        raw = (row.get("value") if isinstance(row, dict) else row[0]) \
            if row else None
        try:
            reservations = json.loads(raw or "[]")
            if not isinstance(reservations, list):
                reservations = []
        except (TypeError, ValueError):
            reservations = []
        cutoff = now - max(1, int(window_s))
        live = []
        for item in reservations:
            try:
                ts, tokens = float(item[0]), int(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if ts > cutoff and tokens > 0:
                live.append([ts, tokens])
        used = sum(item[1] for item in live)
        if live and used + estimate > int(soft_cap):
            return max(0.25, live[0][0] + window_s - now)
        live.append([now, estimate])
        value = json.dumps(live, separators=(",", ":"))
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (key, value))
        return 0.0


def insert_llm_call(conn, project_id, job_id, purpose, model, request,
                    response, prompt_tokens=None, completion_tokens=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO llm_calls (project_id, job_id, purpose, model,
                                   request, response, prompt_tokens,
                                   completion_tokens)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (project_id, job_id, purpose[:32], model,
              Json(_capped_payload(request)),
              Json(_capped_payload(response) if response is not None else None),
              prompt_tokens, completion_tokens))


def add_message(conn, session_id, role, content, meta=None):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO chat_messages (session_id, role, content, meta)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (session_id, role, content,
                     Json(meta) if meta is not None else None))
        return cur.fetchone()["id"]


def latest_creative_blueprint(conn, session_id):
    """Newest durable director blueprint recorded in this project's chat.

    Blueprints ride activity metadata instead of a new mutable project table:
    chat_messages is already append-only, project-scoped through session_id,
    and gives us an audit trail of every legitimate change of direction.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT meta->'creative_blueprint' AS blueprint
                       FROM chat_messages
                       WHERE session_id = %s
                         AND meta ? 'creative_blueprint'
                       ORDER BY id DESC LIMIT 1""", (session_id,))
        row = cur.fetchone()
        return row.get("blueprint") if row else None


def editorial_preference_rows(conn, user_id, family, limit=120):
    """Same-account, same-family stable decision outcomes for taste memory.

    The model-facing reducer never receives chat text, URLs or candidate ids.
    Export is bounded to the assistant message's own response window so a
    later unrelated download cannot bless an earlier treatment.
    """
    limit = min(200, max(1, int(limit or 120)))
    with conn.cursor() as cur:
        cur.execute("""SELECT base.feedback, base.decisions, base.profile,
                         EXISTS (
                           SELECT 1 FROM client_events ce
                           WHERE ce.project_id = base.project_id
                             AND ce.kind = 'download_triggered'
                             AND ce.created_at >= base.created_at
                             AND ce.created_at < LEAST(
                               base.created_at + INTERVAL '24 hours',
                               COALESCE((
                                 SELECT MIN(nu.created_at)
                                 FROM chat_messages nu
                                 WHERE nu.session_id = base.session_id
                                   AND nu.role = 'user' AND nu.id > base.id
                               ), base.created_at + INTERVAL '24 hours'))
                         ) AS exported_after
                       FROM (
                         SELECT cm.id, cm.session_id, cm.created_at,
                                p.id AS project_id,
                                cm.meta->>'feedback' AS feedback,
                                cm.meta->'editing_metrics'->
                                  'editorial_decisions' AS decisions,
                                cm.meta->'editing_metrics'->
                                  'treatment_profile' AS profile
                         FROM chat_messages cm
                         JOIN projects p
                           ON p.chat_session_id = cm.session_id
                         WHERE p.user_id = %s
                           AND cm.role = 'assistant'
                           AND cm.created_at > NOW() - INTERVAL '180 days'
                           AND cm.meta->'editing_metrics'->>
                                 'editorial_family' = %s
                           AND cm.meta->'editing_metrics' ?
                                 'editorial_decisions'
                         ORDER BY cm.id DESC LIMIT %s
                       ) base
                       ORDER BY base.id DESC""",
                    (user_id, str(family), limit))
        return cur.fetchall()


def record_client_event(conn, user_id, project_id, kind, detail=None,
                        asset_id=None):
    """Server-authoritative funnel event emitted by the worker.

    The backend validates browser-authored events. Worker events already come
    from a leased project/job, so this deliberately stays a small INSERT and
    lets the caller treat observability as best effort.
    """
    with conn.cursor() as cur:
        payload = dict(detail or {})
        payload["origin"] = "worker"
        cur.execute("""INSERT INTO client_events
                           (user_id, project_id, kind, asset_id, detail)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (user_id, project_id, str(kind)[:40], asset_id,
                     Json(payload)))


def has_index_greet(conn, session_id, greet_key):
    """Has this chat already been greeted for this asset? The greet must be
    idempotent against the CHAT, not against job flags: an index job that is
    reaped mid-run and re-claimed re-runs _finish_setup with no reindex flag,
    and on 2026-07-31 a real project got 'Your video is ready to edit' twice,
    3.5 minutes apart, from one job row (job 1618)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM chat_messages
                       WHERE session_id = %s
                         AND meta->>'index_greet' = %s LIMIT 1""",
                    (session_id, str(greet_key)))
        return cur.fetchone() is not None


def add_index_greet(conn, session_id, content, meta, greet_key):
    """Insert the index greeting, racing safely: the partial unique index
    (session_id, meta->>'index_greet') makes two live workers greeting the
    same asset resolve to ONE message. Returns the message id, or None when
    the other side won."""
    meta = dict(meta or {})
    meta["index_greet"] = str(greet_key)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO chat_messages (session_id, role, content,
                                                  meta)
                       VALUES (%s, 'assistant', %s, %s)
                       ON CONFLICT (session_id, (meta->>'index_greet'))
                         WHERE meta->>'index_greet' IS NOT NULL
                       DO NOTHING
                       RETURNING id""",
                    (session_id, content, Json(meta)))
        row = cur.fetchone()
        return row["id"] if row else None


def recent_chat(conn, session_id, limit=24):
    """Recent user/assistant turns (activity rows excluded), oldest first."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, role, content, meta FROM chat_messages
            WHERE session_id = %s AND role IN ('user', 'assistant')
            ORDER BY id DESC LIMIT %s
        """, (session_id, limit))
        return list(reversed(cur.fetchall()))


def pending_user_message(conn, project_id, session_id):
    """Latest user message that never got an agent turn. A message sent
    while indexing was still running lands here — the index job replays it
    automatically instead of asking the user to resend."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.id, m.content, m.meta FROM chat_messages m
            WHERE m.session_id = %s AND m.role = 'user'
              AND NOT EXISTS (
                  SELECT 1 FROM video_jobs j
                  WHERE j.project_id = %s AND j.type = 'agent_turn'
                    AND j.payload->>'message_id' = m.id::text)
            ORDER BY m.id DESC LIMIT 1""", (session_id, project_id))
        return cur.fetchone()


def adopt_queued_agent_steers(conn, project_id, active_job_id, session_id,
                              after_message_id):
    """Move mid-turn user messages into the live editor atomically.

    The backend still creates a durable queued follow-up, so a message cannot
    be lost if the live turn ends before seeing it. Adoption marks the row;
    successful reply delivery retires it, while a crash leaves it queued so
    the same instruction is never silently lost.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (int(project_id), 841732))
        cur.execute("""SELECT id, (payload->>'message_id')::bigint AS message_id
                       FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND id <> %s AND state = 'queued'
                         AND payload->>'message_id' ~ '^[0-9]+$'
                         AND (payload->>'message_id')::bigint > %s
                         AND NOT (payload ? 'steered_into')
                       ORDER BY id FOR UPDATE""",
                    (project_id, active_job_id, int(after_message_id or 0)))
        jobs = cur.fetchall()
        if not jobs:
            return []
        upper = max(int(row["message_id"]) for row in jobs)
        cur.execute("""SELECT id, content, meta FROM chat_messages
                       WHERE session_id = %s AND role = 'user'
                         AND id > %s AND id <= %s
                       ORDER BY id""",
                    (session_id, int(after_message_id or 0), upper))
        messages = cur.fetchall()
        ids = [int(row["id"]) for row in jobs]
        # Keep the durable fallback queued until the live turn has actually
        # posted its answer. If that turn crashes, claim_job later runs this
        # row; if it succeeds, complete_adopted_agent_steers retires it.
        cur.execute("""UPDATE video_jobs
                       SET payload = payload || %s, updated_at = NOW()
                       WHERE id = ANY(%s) AND state = 'queued'""",
                    (Json({"steered_into": int(active_job_id)}), ids))
        return {"messages": messages, "job_ids": ids}


def complete_adopted_agent_steers(conn, job_ids, active_job_id):
    if not job_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET state = 'done', progress = 100,
                           result = %s, updated_at = NOW()
                       WHERE id = ANY(%s) AND state = 'queued'
                         AND payload->>'steered_into' = %s
                       RETURNING id""",
                    (Json({"steered_into": int(active_job_id),
                           "billable": False}), list(job_ids),
                     str(active_job_id)))
        return len(cur.fetchall())


def has_active_agent_turn(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND state IN ('queued','running') LIMIT 1""",
                    (project_id,))
        return cur.fetchone() is not None


def has_newer_agent_turn(conn, project_id, after_job_id):
    """A chat edit queued after an automatic shorts run started."""
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND id > %s
                       LIMIT 1""", (project_id, after_job_id))
        return cur.fetchone() is not None


def asset_upload_ready(conn, asset_id):
    """Flip a deferred/dedup original to ready once its bytes exist. The
    same stamp /uploads/original-ready writes — upload_state is load-bearing
    for export (round 58)."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE assets SET meta = meta ||
                         '{"upload_state": "ready", "upload_progress": 1.0}'
                       WHERE id = %s""", (asset_id,))


def has_active_job(conn, project_id, jtype):
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE project_id = %s AND type = %s
                         AND state IN ('queued','running') LIMIT 1""",
                    (project_id, jtype))
        return cur.fetchone() is not None


def user_credits_balance(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT credits_balance FROM users WHERE id = %s",
                    (user_id,))
        row = cur.fetchone()
        return float(row["credits_balance"] or 0) if row else 0.0


def user_is_subscribed(conn, user_id):
    """Needed because "you're out of credits" has two different truths.

    A subscriber's pool really does come back — daily, and in full on renewal.
    A free user's does NOT: the allowance is granted once (see
    backend/credits.FREE_GRANT_CREDITS). Telling a free user to wait for a
    refresh that will never arrive is a lie that also costs the sale, because
    the moment they hit the wall is the only moment the upgrade is relevant.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT is_subscribed FROM users WHERE id = %s",
                    (user_id,))
        row = cur.fetchone()
        return bool(row and row["is_subscribed"])


def user_billing(conn, user_id):
    """(is_subscribed, plan, trialing) — everything the turn needs about who is
    paying, in one query.

    The plan is here because routing stopped being a boolean in round 49: the
    Frontier tier ('ai_max') runs its agent AND its vision on the frontier
    provider, so "which model answers" now depends on WHICH plan, not merely on
    whether there is one.

    `trialing` is here because "you're out of credits" has a third truth. A
    trialling account is is_subscribed, but its pool is 10% of the plan and the
    rest is released by converting — so telling that user their credits
    "refresh on your plan's cycle" points them at waiting when the thing that
    helps is one click away.

    Degrades rather than raises: a missing trial_status column (they are added
    by hand in the Render shell) yields trialing=False and the old behaviour.
    """
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT is_subscribed, plan, trial_status "
                        "FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        except Exception:
            conn.rollback()     # a missing column poisons the transaction
            cur.execute("SELECT is_subscribed, plan FROM users WHERE id = %s",
                        (user_id,))
            row = cur.fetchone()
    if not row:
        return False, "free", False
    return (bool(row["is_subscribed"]),
            (row.get("plan") or "free"),
            bool(row["is_subscribed"]) and row.get("trial_status") == "trialing")


# ── Credits ──────────────────────────────────────────────────────────────────
# A credit is model_prices.USD_PER_CREDIT of model cost -- $0.005 since round
# 49, i.e. a credit burns at TWICE what the model actually costs, which is
# where the plans' margin comes from. Do not re-introduce a literal here: the
# in-turn cap (agent_tools.running_credits) divides by the same constant, and
# if the two ever differ a turn is stopped at a number the invoice contradicts.
# Charged from actual llm_calls usage after each agent turn, spending
# daily -> bonus -> monthly, never below zero.

# Prices come from config so there is ONE source of truth for the fallback, and
# from model_prices for everything listed — a turn can now run on DeepSeek for a
# free user and Grok for a subscriber, so a single global rate is wrong for one
# of them by construction. These names are kept because the tests and the admin
# notes refer to them; they are the UNLISTED-model fallback, nothing more.
LLM_PRICE_IN_PER_M = config.LLM_PRICE_IN_PER_M
LLM_PRICE_OUT_PER_M = config.LLM_PRICE_OUT_PER_M
LLM_PRICE_CACHED_IN_PER_M = config.LLM_PRICE_CACHED_IN_PER_M
# Flat price per successful image generation/edit (no token usage is
# reported for those calls, so they are priced per image). MUST match
# config.IMAGE_PRICE_USD — 0.055 tracks grok-imagine-image-quality.
IMAGE_PRICE_USD = float(os.getenv("IMAGE_PRICE_USD", "0.055"))
MIN_TURN_CREDITS = 1.0

# Built once at import: a per-row USD cost expression that reads each llm_calls
# row's own `model` column. Contains no percent sign — psycopg2 scans the whole
# statement for placeholders, so one in here would raise before Postgres saw it.
_ROW_COST_SQL = model_prices.row_cost_sql(config.PRICE_FALLBACK)


def video_settings(conn):
    """Operator toggles: {'enabled', 'force', 'scene_top', 'lower'}.

    Falls back to the config defaults when the table does not exist yet (the
    backend creates it lazily, and the worker must not depend on having been
    deployed second). to_regclass is checked FIRST rather than catching the
    error, because in postgres a failed statement poisons the whole
    transaction — a missing table would take the render down with it, which
    is a spectacular way for a cosmetic toggle to break exports.
    """
    default = {"enabled": config.WATERMARK_ENABLED, "force": False,
               "scene_top": False, "lower": False}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.video_settings') AS t")
            if not (cur.fetchone() or {}).get("t"):
                return default
            # Read the new key through the row's JSON so an executor deployed
            # before the admin endpoint has migrated the live table still
            # honours the existing enabled/force switches.
            cur.execute("SELECT watermark_enabled, watermark_force, "
                        "COALESCE((to_jsonb(video_settings)->>"
                        "'watermark_scene_top')::boolean, FALSE) "
                        "AS watermark_scene_top, "
                        "COALESCE((to_jsonb(video_settings)->>"
                        "'watermark_lower')::boolean, FALSE) "
                        "AS watermark_lower "
                        "FROM video_settings WHERE id = 1")
            row = cur.fetchone()
        if not row:
            return default
        return {"enabled": bool(row["watermark_enabled"]),
                "force": bool(row["watermark_force"]),
                "scene_top": bool(row["watermark_scene_top"]),
                "lower": bool(row["watermark_lower"])}
    except Exception:
        # A toggle lookup must never be the reason an export fails.
        return default


def user_is_paid(conn, user_id):
    """Is this user on a paid plan right now? Decides the free-tier
    watermark, so the failure directions are NOT symmetric.

    Marking a paying customer's export is a visible broken promise; failing
    to mark a free export costs nothing but a little attribution. So this
    errs toward PAID: either signal is enough. The backend sets
    is_subscribed and plan together (models.update_user_subscription_status
    clears BOTH to 0/'free' on cancel or refund), so a half-applied webhook
    is the only state where they disagree — and in that state the user has
    more likely just paid than just lapsed.

    An unknown user (no row, no id) is treated as FREE: that is the
    anonymous/unresolvable case, not a lapsed customer.
    """
    if not user_id:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT is_subscribed, plan FROM users WHERE id = %s",
                    (user_id,))
        row = cur.fetchone()
    if not row:
        return False
    plan = (row["plan"] or "free").strip().lower()
    return bool(row["is_subscribed"]) or plan not in ("", "free")


def charge_turn_credits(conn, user_id, job_id, extra_credits=0.0):
    """Deduct this turn's model cost from the user's credit pools.
    Returns the credits charged (float). Never raises the balance below 0
    and never fails the turn — callers swallow exceptions.

    extra_credits (round 99): a flat surcharge on top of the model cost —
    the shorts pipeline adds SHORTS_CLIP_CREDITS per finished clip for the
    render compute a plain chat turn never spends. Applied only when the
    job had real model usage, so a run that produced nothing still charges
    nothing."""
    with conn.cursor() as cur:
        # Token cost is summed PER ROW at that row's own model price (see
        # model_prices.row_cost_sql). Summing tokens first and multiplying once
        # only works while every row is the same model, which stopped being true
        # the moment paying users were routed to a different one.
        cur.execute("""SELECT COUNT(*) AS n,
                              COALESCE(SUM(prompt_tokens),0) AS tin,
                              COALESCE(SUM(completion_tokens),0) AS tout,
                              COALESCE(SUM(""" + _ROW_COST_SQL + """), 0)
                                  AS token_cost,
                              COUNT(*) FILTER (
                                  WHERE purpose IN ('image_gen','image_edit')
                                    AND response ? 'image_url') AS n_images,
                              COALESCE(SUM((response->>'cost_usd')::float)
                                  FILTER (WHERE purpose IN
                                          ('sfx_gen','video_gen')), 0)
                                  AS gen_cost
                       FROM llm_calls WHERE job_id = %s""", (job_id,))
        row = cur.fetchone()
        if not row["n"]:
            # A turn that never reached the model costs nothing.
            return 0.0
        # ...and neither does a turn that reached it and got nothing back. The
        # test is real USAGE, not row count: failed calls now leave llm_calls
        # rows too (so an outage is visible in admin), and counting those as
        # "n" would have MIN_TURN_CREDITS bill 1 credit for a turn that produced
        # no tokens, no image and no audio — while the chat tells the user it
        # didn't cost them anything. That message has to stay true.
        if not (float(row["tin"] or 0) or float(row["tout"] or 0)
                or float(row["n_images"] or 0) or float(row["gen_cost"] or 0)):
            return 0.0
        # prompt_tokens stays the TRUE total on every row (so admin token views
        # remain honest); 'cached_in' is the slice of it the provider served
        # from cache and 'reasoning_out' the thinking tokens some providers
        # report beside the completion. Both are clamped inside the SQL, so a
        # stale or oversized provider number can never make a charge negative.
        cost = float(row["token_cost"] or 0)
        cost += float(row["n_images"] or 0) * IMAGE_PRICE_USD
        # Generated sound effects (flat) + AI video (per-second) — the real USD
        # cost is stored on each generation's llm_calls row by the worker tool.
        cost += float(row["gen_cost"] or 0)
        credits = max(MIN_TURN_CREDITS,
                      model_prices.usd_to_credits(cost, ndigits=1))
        credits = round(credits + max(0.0, float(extra_credits or 0.0)), 1)
        cur.execute("""SELECT credits_daily, credits_bonus, credits_monthly
                       FROM users WHERE id = %s FOR UPDATE""", (user_id,))
        u = cur.fetchone()
        if not u:
            return 0.0
        daily = float(u["credits_daily"] or 0)
        bonus = float(u["credits_bonus"] or 0)
        monthly = float(u["credits_monthly"] or 0)
        left = credits
        spend_daily = min(daily, left); left -= spend_daily
        spend_bonus = min(bonus, left); left -= spend_bonus
        spend_monthly = min(monthly, left)
        cur.execute("""UPDATE users
                       SET credits_daily = credits_daily - %s,
                           credits_bonus = credits_bonus - %s,
                           credits_monthly = credits_monthly - %s,
                           credits_balance = (credits_daily - %s)
                                           + (credits_bonus - %s)
                                           + (credits_monthly - %s)
                       WHERE id = %s""",
                    (spend_daily, spend_bonus, spend_monthly,
                     spend_daily, spend_bonus, spend_monthly, user_id))
        # Ledger row so admin usage stats cover the video lane too. The
        # savepoint keeps a ledger hiccup from rolling back the charge.
        cur.execute("SAVEPOINT ledger")
        try:
            cur.execute("""INSERT INTO job_credits (job_id, user_id, turn,
                                                    tokens_used, credits_used)
                           VALUES (%s, %s, 1, %s, %s)""",
                        (f"video:{job_id}"[:16], user_id,
                         int(row["tin"]) + int(row["tout"]), credits))
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT ledger")
            print(f"[credits] ledger insert failed: {e}", flush=True)
        return credits
