"""Delivery facts for media consumers running inside one queued or egress job."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

import db

_context = ContextVar("export_context", default=None)


@contextmanager
def job_scope(worker_db, job):
    @lru_cache(maxsize=None)
    def read(query, key):
        return worker_db.run(query, key)

    token = _context.set((read, job))
    try:
        yield
    finally:
        _context.reset(token)


def account_plan():
    context = _context.get()
    if context:
        read, job = context
        if job.get("user_id"):
            return read(db.render_account, job["user_id"])[1]
    return "free"


def _source_frame(conn, project_id):
    # Only read the geometry fields. An index or EDL can be several megabytes.
    with conn.cursor() as cur:
        cur.execute("""SELECT a.width, a.height, e.frame, e.canvas
                       FROM (SELECT 1) base
                       LEFT JOIN LATERAL (
                         SELECT width, height FROM assets
                         WHERE project_id = %s AND kind = 'original'
                         ORDER BY id DESC LIMIT 1) a ON true
                       LEFT JOIN LATERAL (
                         SELECT json->'frame' AS frame,
                                CASE WHEN jsonb_array_length(COALESCE(json->'keep', '[]')) = 0
                                     THEN json->'canvas' END AS canvas
                         FROM edls WHERE project_id = %s
                         ORDER BY version DESC LIMIT 1) e ON true""",
                    (project_id, project_id))
        return cur.fetchone()


def rendition_size():
    context = _context.get()
    if not context:
        return None
    read, job = context
    if not job.get("project_id"):
        return None
    row = read(_source_frame, job["project_id"])
    if not row:
        return None
    from renderer import delivery_resolution_dims, frame_dims
    frame, canvas = row["frame"] or {}, row["canvas"] or {}
    if canvas.get("width") and canvas.get("height"):
        w, h = canvas["width"], canvas["height"]
    elif row["width"] and row["height"]:
        w, h = frame_dims(row["width"], row["height"], frame.get("ratio"), delivery=True)
    else:
        return None
    return delivery_resolution_dims(w, h, frame.get("resolution"))
