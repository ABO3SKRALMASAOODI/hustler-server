"""Identical project assets reuse one executor-local source file."""

import db


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.cur = _Cursor(rows)

    def cursor(self):
        return self.cur


def test_project_aliases_are_scoped_by_project_and_sha():
    conn = _Conn([
        {"storage_key": "clips/435/original.mp4"},
        {"storage_key": "clips/435/duplicate.mp4"},
    ])

    keys = db.project_asset_keys_by_sha(conn, 435, "same-bytes")

    assert keys == ["clips/435/original.mp4", "clips/435/duplicate.mp4"]
    assert conn.cur.params == (435, "same-bytes")
    assert "project_id = %s AND sha256 = %s" in conn.cur.sql


def test_fetch_prefers_a_supplied_local_alias(tmp_path, monkeypatch):
    """Pin the behavior at render_edl's input resolver without an encode."""
    import renderer

    local = tmp_path / "source.mp4"
    local.write_bytes(b"same bytes")
    called = []

    def cache(key):
        called.append(key)
        return None

    monkeypatch.setattr(renderer, "_cached_source", cache)
    # The small resolver is exercised through its public behavior helper.
    assert renderer._resolve_asset_local(
        "clips/435/duplicate.mp4",
        {"clips/435/duplicate.mp4": str(local)}, cache) == str(local)
    assert called == []
