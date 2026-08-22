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
import error_text
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
_REMOTE_EXEC_TABLE = {"ok": False, "checked_at": 0.0}


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


def remote_executions_table_ready(conn):
    """True once migration 025 has installed the provider handoff ledger."""
    if _REMOTE_EXEC_TABLE["ok"]:
        return True
    if time.time() - _REMOTE_EXEC_TABLE["checked_at"] < _CLAIMS_RECHECK_S:
        return False
    _REMOTE_EXEC_TABLE["checked_at"] = time.time()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('remote_executions') AS name")
            row = cur.fetchone()
        name = ((row or {}).get("name") if isinstance(row, dict)
                else ((row or [None])[0]))
        _REMOTE_EXEC_TABLE["ok"] = bool(name)
    except Exception as exc:                              # pragma: no cover
        print(f"[db] remote execution ledger probe failed: {exc}",
              flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    return _REMOTE_EXEC_TABLE["ok"]


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
    has_remote_ledger = has_claims and remote_executions_table_ready(conn)
    claims_set = ", total_claims = COALESCE(total_claims, 0) + 1" if has_claims else ""
    claims_where = "AND COALESCE(total_claims, 0) < %s" if has_claims else ""
    remote_where = "" if not has_remote_ledger else """
                  AND NOT EXISTS (
                    SELECT 1 FROM remote_executions remote_live
                    WHERE remote_live.job_id = video_jobs.id
                      AND remote_live.total_claims = video_jobs.total_claims
                      AND remote_live.state IN ('submitted', 'running')
                      AND remote_live.deadline_at > NOW())"""
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
        # (the live editor), or QUEUED ahead of it in continuity priority.
        # A durable continuation is physically newer than follow-ups typed
        # while its root was running, but it is still the SAME logical turn:
        # it keeps the project lane until it replies and retires any steering
        # messages it adopted.  Within the same priority, lower id remains the
        # READ COMMITTED/SKIP LOCKED race-closer. A stale-running or spent
        # sibling blocks nothing: those are the reaper's to bury, and a dead
        # row must never wedge its project's queue behind it.
        serial_where = """
                  AND (
                    (video_jobs.type = 'mcp_tool'
                     AND video_jobs.payload->>'mutation' = 'false')
                    OR NOT EXISTS (
                      SELECT 1 FROM video_jobs live
                      WHERE live.project_id = video_jobs.project_id
                        AND live.id <> video_jobs.id
                        AND live.type IN ('agent_turn', 'shorts_plan',
                                          'mcp_tool')
                        AND (live.type <> 'mcp_tool'
                             OR COALESCE(live.payload->>'mutation', 'true')
                                <> 'false')
                        AND ((live.state = 'running'
                              AND live.heartbeat_at >= NOW()
                                  - make_interval(secs => %s))
                             OR (live.state = 'queued'
                                 AND live.attempts < %s
                                 AND (
                                   CASE WHEN live.type = 'agent_turn'
                                             AND COALESCE(
                                               live.payload->>'logical_turn_continuation',
                                               'false') = 'true'
                                        THEN 0 ELSE 1 END
                                   <
                                   CASE WHEN video_jobs.type = 'agent_turn'
                                             AND COALESCE(
                                               video_jobs.payload->>'logical_turn_continuation',
                                               'false') = 'true'
                                        THEN 0 ELSE 1 END
                                   OR (
                                     CASE WHEN live.type = 'agent_turn'
                                               AND COALESCE(
                                                 live.payload->>'logical_turn_continuation',
                                                 'false') = 'true'
                                          THEN 0 ELSE 1 END
                                     =
                                     CASE WHEN video_jobs.type = 'agent_turn'
                                               AND COALESCE(
                                                 video_jobs.payload->>'logical_turn_continuation',
                                                 'false') = 'true'
                                          THEN 0 ELSE 1 END
                                     AND live.id < video_jobs.id))))))"""
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
                  AND (
                    video_jobs.type <> 'agent_turn'
                    OR NOT EXISTS (
                      SELECT 1 FROM assets original_wait
                      WHERE original_wait.project_id = video_jobs.project_id
                        AND original_wait.kind = 'original')
                    OR EXISTS (
                      SELECT 1 FROM assets original_ready
                      JOIN indexes ready_index
                        ON ready_index.video_sha256 = original_ready.sha256
                      WHERE original_ready.project_id = video_jobs.project_id
                        AND original_ready.kind = 'original'))
                  AND (state = 'queued'
                       OR (state = 'running'
                           AND heartbeat_at < NOW() - make_interval(secs => %s)))
                  {remote_where}
                  {serial_where}
                  {index_fair_where}
                ORDER BY CASE type WHEN 'preview' THEN 0
                                   WHEN 'final' THEN 1 ELSE 2 END,
                         COALESCE(u.is_subscribed, 0) DESC,
                         /* continuity priority: finish the logical request
                            before starting its queued follow-up */
                         CASE WHEN video_jobs.type = 'agent_turn'
                                   AND COALESCE(
                                     video_jobs.payload->>'logical_turn_continuation',
                                     'false') = 'true'
                              THEN 0 ELSE 1 END,
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
    remote_where = "" if not remote_executions_table_ready(conn) else """
              AND NOT EXISTS (
                SELECT 1 FROM remote_executions remote_live
                WHERE remote_live.job_id = video_jobs.id
                  AND remote_live.total_claims = video_jobs.total_claims
                  AND remote_live.state IN ('submitted', 'running')
                  AND remote_live.deadline_at > NOW())"""
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE video_jobs
            SET state = 'failed', updated_at = NOW(),
                error = COALESCE(error, 'This job was restarted too many times '
                                        'and has been stopped. Please try again.')
            WHERE COALESCE(total_claims, 0) >= %s
              AND (state = 'queued'
                   OR (state = 'running'
                       AND heartbeat_at < NOW() - make_interval(secs => %s)))
              {remote_where}
            RETURNING id, type, project_id, user_id, error, payload
        """, (config.MAX_CLAIMS_ABSOLUTE, config.STALE_AFTER_S))
        return cur.fetchall()


def fail_exhausted_jobs(conn):
    """Reaper: stale running jobs with no attempts left become failed.
    Returns the failed rows so the caller can surface each in chat."""
    remote_where = "" if not remote_executions_table_ready(conn) else """
              AND NOT EXISTS (
                SELECT 1 FROM remote_executions remote_live
                WHERE remote_live.job_id = video_jobs.id
                  AND remote_live.total_claims = video_jobs.total_claims
                  AND remote_live.state IN ('submitted', 'running')
                  AND remote_live.deadline_at > NOW())"""
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE video_jobs
            SET state = 'failed', updated_at = NOW(),
                error = COALESCE(error, 'Worker died and retries are exhausted')
            WHERE state = 'running'
              AND heartbeat_at < NOW() - make_interval(secs => %s)
              AND attempts >= CASE WHEN type IN ('agent_turn', 'mcp_tool')
                                   THEN %s ELSE %s END
              {remote_where}
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


def record_agent_turn_baseline(conn, job_id, total_claims, version, digest):
    """Persist the original user-turn EDL identity for a death resume.

    A reaper successor copies the payload but starts from any EDL writes the
    dead worker already landed. Keeping this small SHA-256 identity lets the
    successor report whether the original request changed the timeline without
    storing a potentially huge EDL in the queue row.
    """
    with conn.cursor() as cur:
        lease = " AND total_claims = %s" if total_claims is not None else ""
        params = [str(digest), int(version), job_id]
        if total_claims is not None:
            params.append(total_claims)
        cur.execute(f"""UPDATE video_jobs
                        SET payload = COALESCE(payload, '{{}}'::jsonb)
                            || jsonb_build_object(
                                'root_agent_job_id', %s,
                                'turn_baseline_digest', %s,
                                'turn_baseline_version', %s),
                            updated_at = NOW()
                        WHERE id = %s AND state = 'running'{lease}
                          AND NOT (COALESCE(payload, '{{}}'::jsonb)
                                   ? 'turn_baseline_digest')""",
                    tuple([job_id] + params))
        return cur.rowcount > 0


def enqueue_agent_continuation(conn, project_id, user_id, root_job_id,
                               sequence, payload):
    """Idempotently enqueue one physical slice of a logical agent request."""
    root_job_id = int(root_job_id)
    sequence = max(1, int(sequence))
    body = dict(payload or {})
    body.update(root_agent_job_id=root_job_id,
                continuation_sequence=sequence,
                logical_turn_continuation=True)
    with conn.cursor() as cur:
        # A function retry after enqueue but before its response must discover
        # the same child instead of creating two physical continuations.
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (root_job_id, sequence))
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND payload->>'root_agent_job_id' = %s
                         AND payload->>'continuation_sequence' = %s
                       ORDER BY id LIMIT 1""",
                    (project_id, str(root_job_id), str(sequence)))
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        cur.execute("""INSERT INTO video_jobs
                            (project_id, user_id, type, payload)
                       VALUES (%s, %s, 'agent_turn', %s)
                       RETURNING id""",
                    (project_id, user_id, Json(body)))
        return cur.fetchone()["id"]


def finish_job(conn, job_id, state, error=None, result=None,
               total_claims=None):
    """Finish only the execution lease that produced this result.

    A timed-out or deploy-orphaned request can return after its replacement.
    Without the lease predicate that stale response can overwrite the new
    run's state/result even if progress cancellation worked correctly.
    """
    with conn.cursor() as cur:
        lease_where = " AND total_claims = %s" if total_claims is not None else ""
        params = [state, (error or None) and error_text.excerpt(error, 2000),
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


def completed_job_lease_matches(conn, job_id, total_claims):
    """Recognize a successful replay after a lost COMMIT acknowledgement.

    ``Db.run`` retries once when psycopg2 reports an OperationalError, which
    can happen after PostgreSQL committed but before the client received the
    acknowledgement.  On that retry ``finish_job`` correctly updates zero
    rows because the job is already done.  The same immutable lease generation
    proves this is our committed result rather than a stale executor.
    """
    if total_claims is None:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                        WHERE id = %s AND state = 'done'
                          AND total_claims = %s""",
                    (job_id, total_claims))
        return cur.fetchone() is not None


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
        params = [error_text.excerpt(error, 2000), job_id]
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


def stamp_execution_provider(conn, job_id, total_claims, provider,
                             execution_shape=None):
    """Persist provider + comparison shape under the current queue lease."""
    if provider not in {"modal", "cloudflare", "cloud_run", "local"}:
        raise ValueError("invalid execution provider")
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET payload = CASE
                         WHEN COALESCE(payload->>'execution_provider', '') = ''
                         THEN jsonb_set(
                                jsonb_set(COALESCE(payload, '{}'::jsonb),
                                          '{execution_provider}',
                                          to_jsonb(%s::text), true),
                                '{execution_shape}', %s, true)
                         ELSE payload END,
                           updated_at = NOW()
                       WHERE id = %s AND state = 'running'
                         AND (%s::integer IS NULL OR total_claims = %s)
                       RETURNING payload->>'execution_provider' AS provider""",
                    (provider, Json(_json_safe(execution_shape or {})),
                     job_id, total_claims, total_claims))
        row = cur.fetchone()
        return row and row.get("provider")


def project_execution_shape(conn, project_id, asset_id=None):
    """Small capacity fingerprint used before a provider is selected."""
    with conn.cursor() as cur:
        if asset_id is not None:
            cur.execute("""SELECT COALESCE(bytes, 0) AS total_bytes,
                                  COALESCE(bytes, 0) AS max_bytes,
                                  COALESCE(duration_s, 0) AS max_duration_s,
                                  COALESCE(width, 0) AS max_width,
                                  COALESCE(height, 0) AS max_height,
                                  1 AS assets
                           FROM assets WHERE id = %s AND project_id = %s""",
                        (asset_id, project_id))
        else:
            cur.execute("""SELECT COALESCE(SUM(bytes), 0) AS total_bytes,
                                  COALESCE(MAX(bytes), 0) AS max_bytes,
                                  COALESCE(MAX(duration_s), 0) AS max_duration_s,
                                  COALESCE(MAX(width), 0) AS max_width,
                                  COALESCE(MAX(height), 0) AS max_height,
                                  COUNT(*) AS assets
                           FROM assets WHERE project_id = %s
                             AND kind IN ('original', 'video_clip',
                                          'image_ref', 'generated_video',
                                          'generated_image')""",
                        (project_id,))
        return cur.fetchone() or {}


def record_remote_execution(conn, job_id, total_claims, provider, call_id,
                            function_name, timeout_s, meta=None):
    """Persist a provider handoff before the dispatcher begins waiting."""
    if job_id is None or total_claims is None \
            or not remote_executions_table_ready(conn):
        return False
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO remote_executions
              (job_id, total_claims, provider, call_id, function_name,
               state, deadline_at, meta)
            VALUES (%s, %s, %s, %s, %s, 'submitted',
                    NOW() + make_interval(secs => %s), %s)
            ON CONFLICT (job_id) DO UPDATE SET
              total_claims = EXCLUDED.total_claims,
              provider = EXCLUDED.provider,
              call_id = EXCLUDED.call_id,
              function_name = EXCLUDED.function_name,
              state = 'submitted', deadline_at = EXCLUDED.deadline_at,
              submitted_at = NOW(), started_at = NULL,
              last_observed_at = NOW(), completed_at = NULL,
              error = NULL, meta = EXCLUDED.meta
            WHERE remote_executions.total_claims < EXCLUDED.total_claims
               OR (remote_executions.total_claims = EXCLUDED.total_claims
                   AND remote_executions.provider = EXCLUDED.provider
                   AND remote_executions.call_id = EXCLUDED.call_id)
               OR (remote_executions.total_claims = EXCLUDED.total_claims
                   AND remote_executions.state = 'cancelled')
        """, (job_id, total_claims, provider, str(call_id), function_name,
              max(1, int(timeout_s)), Json(meta or {})))
        return cur.rowcount > 0


def confirm_remote_execution_ownership(conn, job_id, total_claims, provider,
                                       call_id):
    """Fence expensive work to the exact provider call recorded for a lease.

    ``pending`` is the normal few-millisecond Modal spawn-to-ledger window.
    ``superseded`` means another physical call owns this queue claim and the
    caller must exit before decoding media. ``unavailable`` preserves the
    migration window only when the additive table genuinely is not installed;
    database errors propagate and therefore fail closed before compute.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.remote_executions') AS name")
        if not (cur.fetchone() or {}).get("name"):
            return "unavailable"
        cur.execute("""UPDATE remote_executions
                       SET state = 'running',
                           started_at = COALESCE(started_at, NOW()),
                           last_observed_at = NOW()
                       WHERE job_id = %s AND total_claims = %s
                         AND provider = %s AND call_id = %s
                         AND state IN ('submitted', 'running')""",
                    (job_id, total_claims, provider, str(call_id)))
        if cur.rowcount > 0:
            return "owned"
        cur.execute("""SELECT total_claims, provider, call_id, state
                       FROM remote_executions WHERE job_id = %s""",
                    (job_id,))
        row = cur.fetchone()
        if not row:
            return "pending"
        exact_active_identity = (
            row.get("total_claims") == total_claims
            and row.get("provider") == provider
            and str(row.get("call_id")) == str(call_id)
            and row.get("state") in {"submitted", "running"}
        )
        if exact_active_identity:
            # The dispatcher can commit the handoff between the UPDATE above
            # and this SELECT.  READ COMMITTED gives each statement a fresh
            # snapshot, so that legitimate insert is visible here even though
            # the UPDATE matched zero rows.  It is our exact physical call,
            # not a superseding one; let the executor's 100-ms loop acquire it
            # on the next statement.
            return "pending"
        # Keep this diagnostic deliberately identity-only: it explains which
        # fence rejected an executor without copying payload, user, or project
        # data into provider logs.  The exact physical call id is already in
        # the caller's error and is not repeated here.
        return ("superseded:"
                f"state={row.get('state')};"
                f"claim_match={row.get('total_claims') == total_claims};"
                f"provider_match={row.get('provider') == provider};"
                f"call_match={str(row.get('call_id')) == str(call_id)}")


def get_remote_execution(conn, job_id):
    if job_id is None or not remote_executions_table_ready(conn):
        return None
    with conn.cursor() as cur:
        cur.execute("""SELECT job_id, total_claims, provider, call_id,
                              function_name, state, deadline_at
                       FROM remote_executions WHERE job_id = %s""",
                    (job_id,))
        return cur.fetchone()


def mark_remote_execution_running(conn, job_id, total_claims,
                                  provider=None, call_id=None):
    if job_id is None or total_claims is None \
            or not remote_executions_table_ready(conn):
        return False
    with conn.cursor() as cur:
        identity_where = "" if provider is None or call_id is None else \
            " AND provider = %s AND call_id = %s"
        params = [job_id, total_claims]
        if identity_where:
            params.extend([provider, str(call_id)])
        cur.execute("""UPDATE remote_executions
                       SET state = 'running',
                           started_at = COALESCE(started_at, NOW()),
                           last_observed_at = NOW()
                       WHERE job_id = %s AND total_claims = %s
                         AND state IN ('submitted', 'running')"""
                    + identity_where, params)
        return cur.rowcount > 0


def heartbeat_remote_execution(conn, job_id, total_claims):
    """Heartbeat only after the provider or executor proved the call alive."""
    if job_id is None or total_claims is None \
            or not remote_executions_table_ready(conn):
        return False
    with conn.cursor() as cur:
        cur.execute("""UPDATE remote_executions
                       SET last_observed_at = NOW()
                       WHERE job_id = %s AND total_claims = %s
                         AND state IN ('submitted', 'running')""",
                    (job_id, total_claims))
        if cur.rowcount <= 0:
            return False
        cur.execute("""UPDATE video_jobs
                       SET heartbeat_at = NOW(), updated_at = NOW()
                       WHERE id = %s AND total_claims = %s
                         AND state = 'running'""",
                    (job_id, total_claims))
        return cur.rowcount > 0


def finish_remote_execution(conn, job_id, total_claims, state, error=None,
                            provider=None, call_id=None):
    if state not in {"done", "failed", "cancelled"}:
        raise ValueError("remote execution terminal state is invalid")
    if job_id is None or total_claims is None \
            or not remote_executions_table_ready(conn):
        return False
    with conn.cursor() as cur:
        identity_where = "" if provider is None or call_id is None else \
            " AND provider = %s AND call_id = %s"
        params = [state,
                  error_text.excerpt(error, 2000) if error else None,
                  job_id, total_claims]
        if identity_where:
            params.extend([provider, str(call_id)])
        cur.execute("""UPDATE remote_executions
                       SET state = %s, completed_at = NOW(),
                           last_observed_at = NOW(), error = %s
                       WHERE job_id = %s AND total_claims = %s"""
                    + identity_where, params)
        return cur.rowcount > 0


def active_remote_executions(conn):
    """Orphan-takeover candidates whose queue lease remains current.

    The submitting dispatcher is already attached to a fresh call and the
    executor owns its terminal database write. Polling the same call from the
    guardian during Modal's visibility window can turn an ambiguous lookup
    into a false terminal state. Only take over after a bounded attachment
    grace; real provider work continues throughout it.
    """
    if not remote_executions_table_ready(conn):
        return []
    with conn.cursor() as cur:
        cur.execute("""SELECT r.*, j.type, j.project_id, j.user_id,
                              j.attempts, j.payload, j.state AS job_state,
                              j.error AS job_error, j.result AS job_result
                       FROM remote_executions r
                       JOIN video_jobs j ON j.id = r.job_id
                        AND j.total_claims = r.total_claims
                       WHERE r.state IN ('submitted', 'running')
                         AND r.deadline_at > NOW()
                         AND r.submitted_at < NOW()
                             - make_interval(secs => %s)
                         AND j.state = 'running'
                       ORDER BY r.last_observed_at ASC""",
                    (config.REMOTE_GUARDIAN_ATTACH_GRACE_S,))
        return cur.fetchall()


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
    payload = dict(payload or {})
    payload.setdefault("execution_policy", config.EXECUTION_POLICY_MODE)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO video_jobs (project_id, user_id, type, payload)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (project_id, user_id, jtype, Json(payload)))
        return cur.fetchone()["id"]


def upsert_change_manifest(conn, project_id, edl_version, manifest):
    """Persist one immutable-version manifest when migration 023 is live."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.change_manifests') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return False
        cur.execute("""INSERT INTO change_manifests
                          (project_id, edl_version, manifest)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (project_id, edl_version) DO UPDATE
                       SET manifest = EXCLUDED.manifest""",
                    (project_id, int(edl_version), Json(manifest)))
    return True


def upsert_verification_record(conn, project_id, edl_version, record):
    """Persist the newest repair/pass evidence for one immutable EDL."""
    status = str((record or {}).get("status") or "pending")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.verification_records') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return False
        cur.execute("""INSERT INTO verification_records
                          (project_id, edl_version, status, record)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (project_id, edl_version) DO UPDATE
                       SET status = EXCLUDED.status,
                           record = EXCLUDED.record,
                           updated_at = NOW()""",
                    (project_id, int(edl_version), status, Json(record)))
    return True


def get_verification_record(conn, project_id, edl_version):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.verification_records') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return None
        cur.execute("""SELECT status, record, updated_at
                       FROM verification_records
                       WHERE project_id = %s AND edl_version = %s""",
                    (project_id, int(edl_version)))
        return cur.fetchone()


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
    audio_model_review = (payload or {}).get("audio_model_review", True) \
        is not False
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
                         AND COALESCE(
                               (payload->>'audio_model_review')::boolean,
                               true) = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, str(version), audio_model_review))
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
# A successful provider spawn transfers heartbeat + terminal-write ownership
# to the durable remote function. The dispatcher keeps the id only for clean
# shutdown bookkeeping; it must not heartbeat work it cannot observe. The
# durable remote_executions row protects the cold-start gap, and the executor
# itself begins heartbeating after it actually starts.
REMOTE_OWNED_JOBS = set()
REMOTE_LAUNCHING_JOBS = set()
_REMOTE_LAUNCHES_STOPPED = False
_ACTIVE_LOCK = threading.Lock()


def track_job(job_id):
    with _ACTIVE_LOCK:
        ACTIVE_JOBS.add(job_id)


def mark_remote_owned(job_id):
    """Reserve one provider submission unless process draining already won.

    The lock is shared with ``begin_remote_shutdown``.  Exactly one side can
    win: either shutdown releases the still-local queue claim and all later
    provider launches are refused, or the launch becomes remote-owned and
    shutdown waits for its durable call id to be published.
    """
    global _REMOTE_LAUNCHES_STOPPED
    with _ACTIVE_LOCK:
        if _REMOTE_LAUNCHES_STOPPED:
            return False
        if job_id in ACTIVE_JOBS:
            REMOTE_OWNED_JOBS.add(job_id)
            REMOTE_LAUNCHING_JOBS.add(job_id)
        return True


def remote_launch_recorded(job_id):
    """Publish that an accepted/ambiguous provider launch is now durable."""
    with _ACTIVE_LOCK:
        REMOTE_LAUNCHING_JOBS.discard(job_id)


def unmark_remote_owned(job_id):
    with _ACTIVE_LOCK:
        REMOTE_OWNED_JOBS.discard(job_id)
        REMOTE_LAUNCHING_JOBS.discard(job_id)


def untrack_job(job_id):
    with _ACTIVE_LOCK:
        ACTIVE_JOBS.discard(job_id)
        REMOTE_OWNED_JOBS.discard(job_id)
        REMOTE_LAUNCHING_JOBS.discard(job_id)


def begin_remote_shutdown():
    """Atomically stop new handoffs and snapshot shutdown ownership."""
    global _REMOTE_LAUNCHES_STOPPED
    with _ACTIVE_LOCK:
        _REMOTE_LAUNCHES_STOPPED = True
        return (list(ACTIVE_JOBS - REMOTE_OWNED_JOBS),
                list(REMOTE_OWNED_JOBS))


def remote_owned_job_ids():
    with _ACTIVE_LOCK:
        return list(REMOTE_OWNED_JOBS)


def remote_launching_job_ids():
    with _ACTIVE_LOCK:
        return list(REMOTE_LAUNCHING_JOBS)


def locally_owned_job_ids():
    with _ACTIVE_LOCK:
        return list(ACTIVE_JOBS - REMOTE_OWNED_JOBS)


def _read_int_file(path):
    try:
        with open(path) as f:
            raw = f.read().strip()
        return int(raw) if raw and raw != "max" else None
    except (OSError, TypeError, ValueError):
        return None


def _linux_memory_snapshot_kb(proc_root="/proc",
                              cgroup_root="/sys/fs/cgroup",
                              root_pid=None):
    """Best-effort memory for Python, its children and the whole container.

    ``/proc/self/status`` only measures the dispatcher process. That hid the
    ffmpeg children which pushed Render over its 512-MiB cgroup limit: the
    final heartbeat said 222 MiB while the platform killed the container for
    using more than 512 MiB. Scan the tiny container process table and read
    cgroup v2/v1 counters so the next last heartbeat records what Render is
    actually enforcing. Returns None off Linux and never affects a beat.
    """
    root_pid = int(root_pid or os.getpid())
    rows = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return None
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(os.path.join(proc_root, name, "stat")) as f:
                # comm is parenthesized and may contain spaces or ')'. Split
                # at the last ')' so tail[1] remains the parent pid.
                tail = f.read().rsplit(")", 1)[1].split()
            ppid = int(tail[1])
            rss_kb = None
            with open(os.path.join(proc_root, name, "status")) as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
            if rss_kb is not None:
                rows[pid] = (ppid, rss_kb)
        except (OSError, IndexError, TypeError, ValueError):
            continue                         # process exited during the scan
    if root_pid not in rows:
        return None

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _rss) in rows.items():
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    self_kb = rows[root_pid][1]
    tree_kb = sum(rows[pid][1] for pid in descendants)

    # Render currently uses cgroup v2; the v1 fallback keeps the diagnostic
    # valid on older Docker hosts and local Linux test boxes.
    cgroup_kb = _read_int_file(os.path.join(cgroup_root, "memory.current"))
    limit_kb = _read_int_file(os.path.join(cgroup_root, "memory.max"))
    if cgroup_kb is None:
        cgroup_kb = _read_int_file(os.path.join(
            cgroup_root, "memory", "memory.usage_in_bytes"))
        limit_kb = _read_int_file(os.path.join(
            cgroup_root, "memory", "memory.limit_in_bytes"))
    return {
        "self": self_kb,
        "tree": tree_kb,
        "children": len(descendants) - 1,
        "cgroup": (cgroup_kb // 1024 if cgroup_kb is not None else None),
        "limit": (limit_kb // 1024 if limit_kb is not None else None),
    }


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
            # A dispatcher waiting on Modal is not proof that ffmpeg or the
            # agent is alive. Heartbeating REMOTE_OWNED_JOBS here is how a
            # failed provider call looked healthy indefinitely.
            ids = list(ACTIVE_JOBS - REMOTE_OWNED_JOBS)
        if not ids:
            last_ok = time.time()
            continue
        # Memory beside the job ids, every beat. Python RSS alone proved
        # misleading: ffmpeg children put the cgroup above 512 MiB while the
        # parent still reported 222 MiB. Linux-only reads, never allowed to
        # break the database heartbeat.
        try:
            mem = _linux_memory_snapshot_kb()
            if mem:
                extra = (f" tree_rss={mem['tree']}kB"
                         f" child_procs={mem['children']}")
                if mem["cgroup"] is not None:
                    extra += f" cgroup={mem['cgroup']}kB"
                if mem["limit"] is not None:
                    extra += f" cgroup_limit={mem['limit']}kB"
                print(f"[heartbeat] jobs={ids} rss={mem['self']}kB{extra}",
                      flush=True)
        except Exception:
            pass
        try:
            def _beat(conn):
                with conn.cursor() as cur:
                    cur.execute("""UPDATE video_jobs
                                   SET heartbeat_at = NOW()
                                   WHERE id = ANY(%s) AND state = 'running'""",
                                (ids,))
                    if remote_executions_table_ready(conn):
                        # On an executor process these ids have a matching
                        # durable handoff row. A local Render job simply
                        # updates zero rows here.
                        cur.execute("""UPDATE remote_executions r
                                       SET state = 'running',
                                           started_at = COALESCE(
                                               started_at, NOW()),
                                           last_observed_at = NOW()
                                       FROM video_jobs j
                                       WHERE r.job_id = j.id
                                         AND r.job_id = ANY(%s)
                                         AND r.total_claims = j.total_claims
                                         AND r.state IN
                                             ('submitted', 'running')""",
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


class RemoteExecutionUnconfirmed(RuntimeError):
    """Provider launch never acquired its durable call-id ownership row."""


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


def reserve_llm_tokens(conn, estimated_tokens, soft_cap, window_s=60,
                       reservation_id=None):
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
                live.append([ts, tokens]
                            + ([str(item[2])] if len(item) > 2 and item[2]
                               else []))
        used = sum(item[1] for item in live)
        if live and used + estimate > int(soft_cap):
            return max(0.25, live[0][0] + window_s - now)
        live.append([now, estimate]
                    + ([str(reservation_id)] if reservation_id else []))
        value = json.dumps(live, separators=(",", ":"))
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (key, value))
        return 0.0


def reconcile_llm_tokens(conn, reservation_id, actual_tokens, window_s=60):
    """Replace one accepted TPM estimate with provider-reported usage.

    Without reconciliation, a 13K-token tool dispatch occupied 24K of the
    shared minute ledger for its full 60 seconds. Repeated calls from one turn
    could therefore starve a waiting large-source user's first call even while
    the provider still had real capacity. Unknown/expired ids fail open.
    """
    if not reservation_id:
        return False
    now = time.time()
    key = "agent_tpm_reservations_v1"
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (841731, 1))
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        table = cur.fetchone()
        table = table.get("t") if isinstance(table, dict) else table[0]
        if not table:
            return False
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
        changed = False
        live = []
        for item in reservations:
            try:
                ts, tokens = float(item[0]), int(item[1])
                rid = str(item[2]) if len(item) > 2 and item[2] else None
            except (TypeError, ValueError, IndexError):
                continue
            if ts <= cutoff or tokens <= 0:
                continue
            if rid == str(reservation_id):
                tokens = max(1, int(actual_tokens))
                changed = True
            live.append([ts, tokens] + ([rid] if rid else []))
        if not changed:
            return False
        value = json.dumps(live, separators=(",", ":"))
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (key, value))
        return True


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


def mark_subscribe_gate_qualified(conn, user_id, job_id):
    """Durably mark that an account completed its free real edit.

    ``project_id`` is intentionally NULL: deleting a project must not refund
    the account-level free edit.  The NOT EXISTS check keeps this compatible
    before migration 019 is applied; its partial unique index closes the
    concurrent-insert race during and after rolling deploys.
    """
    with conn.cursor() as cur:
        # Same row lock as every message/auto-resume decision. The marker and
        # the next enqueue therefore have a real before/after order even when
        # they happen on different projects and different services.
        cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE",
                    (int(user_id),))
        if not cur.fetchone():
            return False
        cur.execute("""INSERT INTO client_events
                            (user_id, project_id, kind, detail)
                       SELECT %s, NULL, 'subscribe_gate_qualified', %s
                       WHERE NOT EXISTS (
                           SELECT 1 FROM client_events
                            WHERE user_id = %s
                              AND kind = 'subscribe_gate_qualified')
                       ON CONFLICT DO NOTHING""",
                    (user_id, Json({"origin": "worker",
                                    "source_job_id": job_id}), user_id))
        return bool(cur.rowcount)


def subscribe_gate_applies(conn, user_id):
    """Return whether an unsubscribed account must stop at the wall.

    There is no free first edit. The HTTP message/Shorts routes check this
    before accepting new work. An upload-time prompt can be accepted before
    index; the worker re-checks immediately before enqueueing that saved
    prompt so a free account cannot auto-run after analysis.
    """
    with conn.cursor() as cur:
        # Lock in a separate statement *before* reading the marker. A single
        # SELECT ... EXISTS(...) FOR UPDATE may evaluate the subquery against
        # its old statement snapshot before it waits on a concurrent writer.
        cur.execute("""SELECT is_subscribed, email FROM users
                        WHERE id = %s FOR UPDATE""", (int(user_id),))
        user = cur.fetchone()
        if not user or user.get("is_subscribed"):
            return False
        if (user.get("email") or "").lower() == "thevalmera@gmail.com":
            return False
        return True


def mark_message_subscribe_gated(conn, message_id):
    """Retire one saved upload-time prompt without pretending it ran.

    ``pending_user_message`` deliberately ignores this marker.  The user can
    send the text again once subscribed, but a re-index or worker retry cannot
    silently auto-run the blocked request merely because cards were shown.
    """
    with conn.cursor() as cur:
        cur.execute("""UPDATE chat_messages
                          SET meta = COALESCE(meta, '{}'::jsonb)
                                     || jsonb_build_object(
                                          'subscribe_gated', TRUE)
                        WHERE id = %s AND role = 'user'""",
                    (int(message_id),))
        return bool(cur.rowcount)


def resolve_pending_auto_resume(conn, project_id, session_id, user_id,
                                message_id, payload=None):
    """Gate-or-enqueue one saved upload brief in a single transaction.

    Lock order is account then message. Qualification writers and checkout
    updates also lock the account row, so a cross-project completion or plan
    activation cannot land in the gap between eligibility lookup and enqueue.
    The message lock/idempotency probe prevents two index retries from queuing
    the same brief.
    """
    gated = subscribe_gate_applies(conn, user_id)  # locks the account row
    with conn.cursor() as cur:
        cur.execute("""SELECT m.id FROM chat_messages m
                        WHERE m.id = %s AND m.session_id = %s
                          AND m.role = 'user'
                          AND COALESCE(m.meta->>'subscribe_gated', 'false')
                                <> 'true'
                          AND NOT EXISTS (
                              SELECT 1 FROM video_jobs j
                               WHERE j.project_id = %s
                                 AND j.type = 'agent_turn'
                                 AND j.payload->>'message_id' = m.id::text)
                        FOR UPDATE OF m""",
                    (int(message_id), session_id, int(project_id)))
        if not cur.fetchone():
            return {"state": "consumed", "job_id": None}

    if gated:
        mark_message_subscribe_gated(conn, message_id)
        return {"state": "gated", "job_id": None}

    if user_credits_balance(conn, user_id) < 1.0:
        return {"state": "no_credits", "job_id": None}

    job_payload = dict(payload or {})
    job_payload["message_id"] = int(message_id)
    job_payload["auto_resumed"] = True
    job_id = enqueue_job(
        conn, project_id, user_id, "agent_turn", job_payload)
    return {"state": "enqueued", "job_id": job_id}


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
              AND COALESCE(m.meta->>'subscribe_gated', 'false') <> 'true'
              AND NOT EXISTS (
                  SELECT 1 FROM video_jobs j
                  WHERE j.project_id = %s AND j.type = 'agent_turn'
                    AND j.payload->>'message_id' = m.id::text)
            ORDER BY m.id DESC LIMIT 1""", (session_id, project_id))
        return cur.fetchone()


def adopt_queued_agent_steers(conn, project_id, active_root_id, session_id,
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
                    (project_id, active_root_id, int(after_message_id or 0)))
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
                    (Json({"steered_into": int(active_root_id)}), ids))
        return {"messages": messages, "job_ids": ids}


def complete_adopted_agent_steers(conn, job_ids, active_root_id):
    if not job_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET state = 'done', progress = 100,
                           result = %s, updated_at = NOW()
                       WHERE id = ANY(%s) AND state = 'queued'
                         AND payload->>'steered_into' = %s
                       RETURNING id""",
                    (Json({"steered_into": int(active_root_id),
                           "billable": False}), list(job_ids),
                     str(active_root_id)))
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
    Returns the credits claimed for this job/turn (float). Repeated calls
    return the existing claim without another debit. Never raises the balance
    below 0 and never fails the turn — callers swallow exceptions.

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
                       FROM llm_calls
                       WHERE job_id IN (
                           SELECT id FROM video_jobs
                           WHERE id = %s
                              OR (type = 'agent_turn'
                                  AND payload->>'root_agent_job_id' = %s)
                       )""", (job_id, str(job_id)))
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

        # Claim the charge in the audit ledger BEFORE touching a balance.  The
        # unique (job_id, turn) index from migration 020 is the durable fence:
        # two executor responses for one job can race here, but only one may
        # own the debit.  The transaction-scoped advisory lock + pre-check keep
        # the same guarantee during the safe rolling-deploy window before the
        # migration has been applied; ON CONFLICT without a target remains
        # valid on both schemas.
        ledger_job_id = f"video:{job_id}"[:16]
        ledger_turn = 1
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s), %s)",
                    (ledger_job_id, ledger_turn))
        cur.execute("""SELECT credits_used FROM job_credits
                        WHERE job_id = %s AND turn = %s LIMIT 1""",
                    (ledger_job_id, ledger_turn))
        existing = cur.fetchone()
        if existing:
            return float(existing["credits_used"] or 0)
        cur.execute("""INSERT INTO job_credits
                            (job_id, user_id, turn, tokens_used, credits_used)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING
                       RETURNING credits_used""",
                    (ledger_job_id, user_id, ledger_turn,
                     int(row["tin"]) + int(row["tout"]), credits))
        claimed = cur.fetchone()
        if not claimed:
            # The unique index settled a race after our pre-check. PostgreSQL
            # waits for the winning transaction before reporting the conflict,
            # so its committed audit amount is available now.
            cur.execute("""SELECT credits_used FROM job_credits
                            WHERE job_id = %s AND turn = %s LIMIT 1""",
                        (ledger_job_id, ledger_turn))
            existing = cur.fetchone()
            return float((existing or {}).get("credits_used") or 0)

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
        return credits


def patch_done_job_result(conn, job_id, patch=None, remove=()):
    """Merge accounting state into a completed job under its row lock."""
    with conn.cursor() as cur:
        cur.execute("""SELECT result FROM video_jobs
                        WHERE id = %s AND state = 'done' FOR UPDATE""",
                    (job_id,))
        row = cur.fetchone()
        if not row:
            return False
        stored = dict(row.get("result") or {})
        for key in remove or ():
            stored.pop(str(key), None)
        stored.update(dict(patch or {}))
        cur.execute("UPDATE video_jobs SET result = %s WHERE id = %s",
                    (Json(_json_safe(stored)), job_id))
        return True


def finish_accounted_job(conn, job_id, result, total_claims, user_id,
                         billable=False, extra_credits=0.0,
                         qualify_subscribe=False, accounting_job_id=None):
    """Fence completion before charging, in one database transaction.

    A remote executor can return after its lease was cancelled or replaced.
    Charging before ``finish_job`` let that stale response debit a user even
    though its result was correctly refused. This helper first wins the
    execution lease, then performs the idempotent ledger/debit and durable
    free-edit marker under the same transaction. Billing/telemetry failures
    use savepoints so they never erase a completed edit.
    """
    committed = finish_job(conn, job_id, "done", None, result, total_claims)
    if not committed:
        committed = completed_job_lease_matches(
            conn, job_id, total_claims)
    outcome = {"committed": bool(committed), "charged": None,
               "billing_error": None, "qualification_error": None}
    if not committed:
        return outcome

    accounting_job_id = int(accounting_job_id or job_id)

    if billable:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT terminal_billing")
        try:
            charged = charge_turn_credits(
                conn, user_id, accounting_job_id, extra_credits)
            patch_done_job_result(
                conn, job_id, {"credits_charged": charged},
                remove=("billing_pending", "billing_error",
                        "billing_extra_credits", "billing_root_job_id"))
            with conn.cursor() as cur:
                cur.execute("RELEASE SAVEPOINT terminal_billing")
            outcome["charged"] = charged
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT terminal_billing")
                cur.execute("RELEASE SAVEPOINT terminal_billing")
            error = str(exc)[:500]
            # This write is deliberately outside the failed savepoint. If it
            # cannot be persisted either, let the whole terminal transaction
            # roll back so the job remains recoverable instead of becoming a
            # completed, uncharged orphan.
            patch_done_job_result(
                conn, job_id,
                {"billing_pending": True, "billing_error": error,
                 "billing_root_job_id": accounting_job_id,
                 "billing_extra_credits": max(
                     0.0, float(extra_credits or 0.0))})
            outcome["billing_error"] = error

    if qualify_subscribe:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT terminal_qualification")
        try:
            mark_subscribe_gate_qualified(conn, user_id, accounting_job_id)
            patch_done_job_result(
                conn, job_id, {},
                remove=("qualification_pending", "qualification_error",
                        "qualification_root_job_id"))
            with conn.cursor() as cur:
                cur.execute("RELEASE SAVEPOINT terminal_qualification")
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT terminal_qualification")
                cur.execute("RELEASE SAVEPOINT terminal_qualification")
            error = str(exc)[:500]
            patch_done_job_result(
                conn, job_id,
                {"qualification_pending": True,
                 "qualification_root_job_id": accounting_job_id,
                 "qualification_error": error})
            outcome["qualification_error"] = error
    return outcome


def reconcile_pending_accounting(conn, limit=50):
    """Retry durable terminal accounting work without rerunning the edit.

    A transient database/schema error after the lease is fenced must not make
    the user repeat a completed creative turn. ``finish_accounted_job`` leaves
    explicit flags in the job result; the worker janitor repairs those flags
    idempotently. Project deletion blocks briefly while either flag exists so
    the llm_calls needed to price the turn cannot disappear underneath this
    repair.
    """
    limit = max(1, min(200, int(limit or 50)))
    with conn.cursor() as cur:
        cur.execute("""SELECT id, user_id, result
                         FROM video_jobs
                        WHERE state = 'done'
                          AND (result->>'billing_pending' = 'true'
                               OR result->>'qualification_pending' = 'true')
                        ORDER BY id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED""", (limit,))
        rows = cur.fetchall()

    repaired = []
    for row in rows:
        job_id = row["id"]
        result = dict(row.get("result") or {})
        fixed = []
        if result.get("billing_pending") is True:
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT reconcile_terminal_billing")
            try:
                charged = charge_turn_credits(
                    conn, row["user_id"],
                    int(result.get("billing_root_job_id") or job_id),
                    float(result.get("billing_extra_credits") or 0.0))
                patch_done_job_result(
                    conn, job_id, {"credits_charged": charged},
                    remove=("billing_pending", "billing_error",
                            "billing_extra_credits",
                            "billing_root_job_id"))
                with conn.cursor() as cur:
                    cur.execute("RELEASE SAVEPOINT reconcile_terminal_billing")
                fixed.append("billing")
            except Exception as exc:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK TO SAVEPOINT reconcile_terminal_billing")
                    cur.execute("RELEASE SAVEPOINT reconcile_terminal_billing")
                patch_done_job_result(
                    conn, job_id,
                    {"billing_pending": True,
                     "billing_error": str(exc)[:500]})

        if result.get("qualification_pending") is True:
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT reconcile_terminal_qualification")
            try:
                mark_subscribe_gate_qualified(
                    conn, row["user_id"],
                    int(result.get("qualification_root_job_id") or job_id))
                patch_done_job_result(
                    conn, job_id, {},
                    remove=("qualification_pending",
                            "qualification_error",
                            "qualification_root_job_id"))
                with conn.cursor() as cur:
                    cur.execute(
                        "RELEASE SAVEPOINT reconcile_terminal_qualification")
                fixed.append("qualification")
            except Exception as exc:
                with conn.cursor() as cur:
                    cur.execute(
                        "ROLLBACK TO SAVEPOINT reconcile_terminal_qualification")
                    cur.execute(
                        "RELEASE SAVEPOINT reconcile_terminal_qualification")
                patch_done_job_result(
                    conn, job_id,
                    {"qualification_pending": True,
                     "qualification_error": str(exc)[:500]})

        if fixed:
            repaired.append({"job_id": job_id, "fixed": fixed})
    return repaired
