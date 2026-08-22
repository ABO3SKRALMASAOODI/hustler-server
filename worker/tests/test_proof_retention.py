"""Preview-check cleanup is scoped to one immutable logical proof set."""

import db as dbx


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params

    @staticmethod
    def fetchall():
        return []


class _Conn:
    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur


def test_stale_proof_query_cannot_delete_another_proof_set():
    conn = _Conn()

    assert dbx.stale_preview_checks(conn, 1003, 12704, "proof-abc") == []

    assert "meta->>'proof_set_id' = %s" in conn.cur.sql
    assert conn.cur.params == (1003, "proof-abc", 12704)
