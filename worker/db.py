"""Postgres access for the worker.

The queue is the video_jobs table: claims use FOR UPDATE SKIP LOCKED so any
number of worker processes/threads can poll safely. A running job whose
heartbeat goes stale (worker died mid-job) becomes claimable again until its
attempts are exhausted.
"""

import json
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


def connect():
    conn = psycopg2.connect(config.DATABASE_URL, cursor_factory=RealDictCursor)
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
    # TWO counters, deliberately. `attempts` is the refundable fairness budget
    # (release_jobs hands one back after a deploy); `total_claims` counts what
    # we have physically spent running this job and is NEVER given back. Job
    # 836 ran five 50-minute finals against a cap of three because only the
    # first counter existed. See config.MAX_CLAIMS_ABSOLUTE.
    has_claims = claims_column_ready(conn)
    claims_set = ", total_claims = COALESCE(total_claims, 0) + 1" if has_claims else ""
    claims_where = "AND COALESCE(total_claims, 0) < %s" if has_claims else ""
    params = [list(types), max_attempts]
    if has_claims:
        params.append(config.MAX_CLAIMS_ABSOLUTE)
    params.append(config.STALE_AFTER_S)
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE video_jobs
            SET state = 'running', attempts = attempts + 1{claims_set},
                heartbeat_at = NOW(), updated_at = NOW(), error = NULL
            WHERE id = (
                SELECT id FROM video_jobs
                WHERE type = ANY(%s)
                  AND attempts < %s
                  {claims_where}
                  AND (state = 'queued'
                       OR (state = 'running'
                           AND heartbeat_at < NOW() - make_interval(secs => %s)))
                ORDER BY CASE type WHEN 'preview' THEN 0
                                   WHEN 'final' THEN 1 ELSE 2 END, id
                FOR UPDATE SKIP LOCKED
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


def set_progress(conn, job_id, progress, attempts=None):
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

    `attempts` closes the race the state check alone cannot: once the retry has
    claimed the row it is `running` again, and an orphan checking only state
    would see its own replacement and carry on. The dispatcher already ships
    `attempts` to the executor in remote._job_payload, so the orphan knows
    which run it is.
    """
    with conn.cursor() as cur:
        if attempts is None:
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


def finish_job(conn, job_id, state, error=None, result=None):
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET state = %s, error = %s, result = %s,
                           progress = CASE WHEN %s = 'done' THEN 100 ELSE progress END,
                           updated_at = NOW()
                       WHERE id = %s""",
                    (state, (error or None) and str(error)[:2000],
                     Json(result) if result is not None else None, state, job_id))


def requeue_job(conn, job_id, error):
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
        cur.execute("""UPDATE video_jobs
                       SET state = 'queued', error = %s, updated_at = NOW()
                       WHERE id = %s AND state = 'running'""",
                    (str(error)[:2000], job_id))
        return cur.rowcount > 0


def get_job(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM video_jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


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
    hdb = Db()
    while True:
        time.sleep(config.HEARTBEAT_EVERY_S)
        with _ACTIVE_LOCK:
            ids = list(ACTIVE_JOBS)
        if not ids:
            continue
        try:
            def _beat(conn):
                with conn.cursor() as cur:
                    cur.execute("""UPDATE video_jobs
                                   SET heartbeat_at = NOW()
                                   WHERE id = ANY(%s) AND state = 'running'""",
                                (ids,))
            hdb.run(_beat)
        except Exception as e:
            print(f"[heartbeat] {e}", flush=True)


# ------------------------------------------------------------------ #
#  Domain helpers                                                      #
# ------------------------------------------------------------------ #

def get_project(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        return cur.fetchone()


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


def indexed_clips(conn, project_id, limit=12):
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


def delete_assets(conn, asset_ids):
    if not asset_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM assets WHERE id = ANY(%s)", (list(asset_ids),))
        return cur.rowcount


def assets_by_kinds(conn, project_id, kinds, limit=40):
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


def latest_edl(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 1""", (project_id,))
        return cur.fetchone()


def music_used_by_user(conn, user_id, exclude_project_id=None, limit=12):
    """Library tracks this user has scored their OTHER projects with.

    Round 52. "Do not use the exact background music as previous projects" is
    a real sentence a real customer typed, and it is the first thing anyone
    notices about a tool that scores video: the same 24 tracks, and one of them
    keeps coming back. The agent could not have honoured it — nothing in a turn
    can see any project but this one.

    Returns newest-first storage_keys, so the agent can pick something else
    without being told to.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (m.key) m.key, e.created_at
              FROM edls e
              JOIN projects p ON p.id = e.project_id
              CROSS JOIN LATERAL jsonb_array_elements(
                     COALESCE(e.json->'music', '[]'::jsonb)) AS mi(item)
              CROSS JOIN LATERAL (SELECT mi.item->>'storage_key' AS key) m
             WHERE p.user_id = %s
               AND (%s::int IS NULL OR e.project_id <> %s)
               AND m.key LIKE 'library:%%'
             ORDER BY m.key, e.created_at DESC
        """, (user_id, exclude_project_id, exclude_project_id))
        rows = cur.fetchall() or []
    rows = sorted(rows, key=lambda r: r[1] if not isinstance(r, dict)
                  else r["created_at"], reverse=True)
    keys = [r[0] if not isinstance(r, dict) else r["key"] for r in rows]
    return keys[:limit]


def get_edl_version(conn, project_id, version):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls
                       WHERE project_id = %s AND version = %s""",
                    (project_id, version))
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


def has_active_agent_turn(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND state IN ('queued','running') LIMIT 1""",
                    (project_id,))
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
    """Operator toggles from the admin panel: {'enabled', 'force'}.

    Falls back to the config defaults when the table does not exist yet (the
    backend creates it lazily, and the worker must not depend on having been
    deployed second). to_regclass is checked FIRST rather than catching the
    error, because in postgres a failed statement poisons the whole
    transaction — a missing table would take the render down with it, which
    is a spectacular way for a cosmetic toggle to break exports.
    """
    default = {"enabled": config.WATERMARK_ENABLED, "force": False}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.video_settings') AS t")
            if not (cur.fetchone() or {}).get("t"):
                return default
            cur.execute("SELECT watermark_enabled, watermark_force "
                        "FROM video_settings WHERE id = 1")
            row = cur.fetchone()
        if not row:
            return default
        return {"enabled": bool(row["watermark_enabled"]),
                "force": bool(row["watermark_force"])}
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
