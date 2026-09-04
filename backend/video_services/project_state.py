"""Read-only project, source, index and immutable EDL lookups."""


def project_for_user(cur, project_id, user_id):
    cur.execute(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (project_id, int(user_id)),
    )
    return cur.fetchone()


def active_original(cur, project_id):
    cur.execute(
        """SELECT * FROM assets
           WHERE project_id = %s AND kind = 'original'
           ORDER BY id DESC LIMIT 1""",
        (project_id,),
    )
    return cur.fetchone()


def index_row(cur, sha256):
    if not sha256:
        return None
    cur.execute(
        """SELECT id, created_at, pipeline_version
           FROM indexes WHERE video_sha256 = %s""",
        (sha256,),
    )
    return cur.fetchone()


def index_job_state(cur, project_id, asset_id):
    """Latest, active, and recent index work for one concrete asset.

    A project's ``index`` lane also carries perception jobs for uploaded
    clips and music.  Those jobs must never hide a failed main-video index or
    spend its bounded self-heal allowance.  Compare the JSON payload as text
    so legacy/malformed payloads cannot make the studio's polling endpoint
    fail on an integer cast.
    """
    if not asset_id:
        return {"id": None, "state": None, "progress": None,
                "error": None, "updated_at": None,
                "active": False, "recent_count": 0}
    cur.execute(
        """SELECT
             (ARRAY_AGG(id ORDER BY id DESC))[1] AS id,
             (ARRAY_AGG(state ORDER BY id DESC))[1] AS state,
             (ARRAY_AGG(progress ORDER BY id DESC))[1] AS progress,
             (ARRAY_AGG(error ORDER BY id DESC))[1] AS error,
             (ARRAY_AGG(updated_at ORDER BY id DESC))[1] AS updated_at,
             COALESCE(BOOL_OR(state IN ('queued', 'running')), FALSE)
               AS active,
             COUNT(*) FILTER (
               WHERE created_at > NOW() - INTERVAL '6 hours') AS recent_count
           FROM video_jobs
          WHERE project_id = %s AND type = 'index'
            AND payload->>'asset_id' = %s""",
        (project_id, str(asset_id)),
    )
    row = cur.fetchone() or {}
    return {
        "id": row.get("id"), "state": row.get("state"),
        "progress": row.get("progress"), "error": row.get("error"),
        "updated_at": row.get("updated_at"),
        "active": bool(row.get("active")),
        "recent_count": int(row.get("recent_count") or 0),
    }


def latest_edl(cur, project_id):
    cur.execute(
        """SELECT version, json, created_by, created_at FROM edls
           WHERE project_id = %s ORDER BY version DESC LIMIT 1""",
        (project_id,),
    )
    return cur.fetchone()


def edl_at(cur, project_id, version):
    cur.execute(
        """SELECT version, json, created_by, created_at FROM edls
           WHERE project_id = %s AND version = %s""",
        (project_id, version),
    )
    return cur.fetchone()
