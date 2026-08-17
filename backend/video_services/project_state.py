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
