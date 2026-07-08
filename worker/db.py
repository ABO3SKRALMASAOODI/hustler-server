"""Postgres access for the worker.

The queue is the video_jobs table: claims use FOR UPDATE SKIP LOCKED so any
number of worker processes/threads can poll safely. A running job whose
heartbeat goes stale (worker died mid-job) becomes claimable again until its
attempts are exhausted.
"""

import json
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor, Json

import config

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

def claim_job(conn, types, max_attempts):
    # Previews (always a user or agent actively waiting) jump the queue over
    # finals, and both jump over indexing — a turn's render_preview never
    # waits behind another project's index job.
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE video_jobs
            SET state = 'running', attempts = attempts + 1,
                heartbeat_at = NOW(), updated_at = NOW(), error = NULL
            WHERE id = (
                SELECT id FROM video_jobs
                WHERE type = ANY(%s)
                  AND attempts < %s
                  AND (state = 'queued'
                       OR (state = 'running'
                           AND heartbeat_at < NOW() - make_interval(secs => %s)))
                ORDER BY CASE type WHEN 'preview' THEN 0
                                   WHEN 'final' THEN 1 ELSE 2 END, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        """, (list(types), max_attempts, config.STALE_AFTER_S))
        return cur.fetchone()


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
              AND attempts >= CASE WHEN type = 'agent_turn' THEN %s ELSE %s END
            RETURNING id, type, project_id, error
        """, (config.STALE_AFTER_S, config.MAX_ATTEMPTS_AGENT,
              config.MAX_ATTEMPTS_MEDIA))
        return cur.fetchall()


def set_progress(conn, job_id, progress):
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET progress = %s, heartbeat_at = NOW(), updated_at = NOW()
                       WHERE id = %s""", (min(100, max(0, int(progress))), job_id))


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
    with conn.cursor() as cur:
        cur.execute("""UPDATE video_jobs
                       SET state = 'queued', error = %s, updated_at = NOW()
                       WHERE id = %s""", (str(error)[:2000], job_id))


def get_job(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM video_jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


def enqueue_job(conn, project_id, user_id, jtype, payload):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO video_jobs (project_id, user_id, type, payload)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (project_id, user_id, jtype, Json(payload)))
        return cur.fetchone()["id"]


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


def any_asset_by_sha(conn, kind, sha256):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE kind = %s AND sha256 = %s
                       ORDER BY id DESC LIMIT 1""", (kind, sha256))
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


def recent_chat(conn, session_id, limit=24):
    """Recent user/assistant turns (activity rows excluded), oldest first."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, role, content, meta FROM chat_messages
            WHERE session_id = %s AND role IN ('user', 'assistant')
            ORDER BY id DESC LIMIT %s
        """, (session_id, limit))
        return list(reversed(cur.fetchall()))
