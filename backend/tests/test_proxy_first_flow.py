"""The proxy-first upload flow, end to end through the route functions.

This is the path where an asset row points at an object THAT DOES NOT EXIST
YET, which is the whole trick and also the whole risk. The pieces are tested
elsewhere (worker/tests/test_proxy_first_upload.py for adoption and the
self-healing rule, tests/test_mp4probe.py for the header probe); what is
pinned here is the sequence a real upload actually walks:

    presign proxy -> complete(kind=proxy) -> complete(original, deferred)
    -> index enqueued -> ... -> original-ready -> verified

and the two ways it must refuse: while the rollout flag is off, and while the
original's bytes have not arrived.

    cd backend && python -m pytest tests/test_proxy_first_flow.py -q
"""

import os
import sys
import contextlib

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402
import storage                                          # noqa: E402


class _Cur:
    """A cursor that remembers what it was asked and what it was told to do."""

    def __init__(self, assets=None, project_owner=7):
        self.assets = assets or {}          # id -> row
        self.project_owner = project_owner
        self.enqueued = []
        self.updates = []
        self._one = None
        self._rows = []
        self._next_id = 900

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = params or ()
        if s.startswith("SELECT id FROM projects WHERE id"):
            self._one = {"id": p[0]}
        elif "SELECT user_id FROM projects" in s:
            self._one = {"user_id": self.project_owner}
        elif "FROM assets WHERE id" in s:
            self._one = self.assets.get(p[0])
        elif "FROM assets" in s and "storage_key" in s and s.startswith("SELECT id, kind"):
            self._one = next((a for a in self.assets.values()
                              if a["storage_key"] == p[1]), None)
        elif s.startswith("INSERT INTO assets"):
            # TWO shapes reach here — the deferred path names width/height
            # (the browser measured the original) and the ordinary path does
            # not. Bind by the column list rather than by position, so a test
            # of one path cannot pass because it silently read the other's
            # parameters.
            #
            # Alignment is by PLACEHOLDER, not by column index: the deferred
            # INSERT writes kind as a literal 'original' in its VALUES, so a
            # naive zip(columns, params) shifts every field by one and the
            # test reads bytes where it expects a storage key.
            cols = [c.strip() for c in
                    s.split("(", 1)[1].split(")", 1)[0].split(",")]
            vals = [v.strip() for v in
                    s.split("VALUES (", 1)[1].split(")", 1)[0].split(",")]
            assert len(cols) == len(vals), f"unparsed INSERT: {s[:120]}"
            row, i = {}, 0
            for col, val in zip(cols, vals):
                if val == "%s":
                    row[col] = p[i]
                    i += 1
                else:
                    row[col] = val.strip("'")
            meta = row.get("meta")
            self._next_id += 1
            self.assets[self._next_id] = {
                "id": self._next_id,
                "project_id": row.get("project_id"),
                "kind": row.get("kind", "original"),
                "storage_key": row.get("storage_key"),
                "bytes": row.get("bytes"),
                "duration_s": row.get("duration_s"),
                "width": row.get("width"), "height": row.get("height"),
                "meta": dict(getattr(meta, "adapted", meta) or {}),
            }
            self._one = {"id": self._next_id}
        elif s.startswith("UPDATE assets"):
            patch = p[1].adapted if hasattr(p[1], "adapted") else p[1]
            self.updates.append(patch)
            aid = p[-1]
            if aid in self.assets:
                self.assets[aid]["meta"] = {**self.assets[aid]["meta"], **patch}
        elif "FROM video_jobs" in s:
            self._one = None                 # no prior job
            self._rows = []
        elif s.startswith("INSERT INTO video_jobs"):
            self._next_id += 1
            self.enqueued.append({"type": p[2], "payload": dict(p[3].adapted)})
            self._one = {"id": self._next_id}
        else:                                 # pragma: no cover
            raise AssertionError(f"unexpected query: {s[:140]}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


@pytest.fixture
def env(monkeypatch):
    cur = _Cur()

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user", lambda c, p, u: {"id": p})
    monkeypatch.setattr(video, "_running_jobs_count", lambda c, u: 0)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: None)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video.storage, "head_bytes", lambda k: 4_353_925_022)
    monkeypatch.setattr(video.storage, "get_range", lambda k, n=64: b"\x00" * 4 + b"ftypisom")
    monkeypatch.setattr(video.storage, "content_matches_kind", lambda h, k: True)
    monkeypatch.setattr(video.storage, "complete_multipart", lambda *a: None)
    monkeypatch.setattr(video.storage, "PROXY_FIRST_UPLOADS", True)
    monkeypatch.setattr(storage, "PROXY_FIRST_UPLOADS", True)
    return cur


def _defer(cur, **over):
    data = {
        "storage_key": "originals/5/abc.mov",
        "kind": "original",
        "filename": "IMG_9114.MOV",
        "original_pending": True,
        "client_proxy_key": "clientproxies/5/p.mp4",
        "bytes": 4_353_925_022,
        "duration_s": 355.0,
        "width": 3840, "height": 2160,
    }
    data.update(over)
    return video.complete_upload_core(7, 5, data)


# ── the sequence ────────────────────────────────────────────────────────────

def test_a_proxy_upload_creates_no_asset_and_returns_its_key(env):
    """The bytes are not an asset. Only the WORKER decides whether they are a
    usable proxy — publishing one here would put an unprobed file in front of
    the player."""
    out, status = video.complete_upload_core(7, 5, {
        "storage_key": "clientproxies/5/p.mp4", "kind": "proxy",
        "upload_id": "u1", "parts": [{"part_number": 1, "etag": "e"}]})
    assert status == 200
    assert out["storage_key"] == "clientproxies/5/p.mp4"
    assert not env.assets, "a proxy upload must not create an asset row"
    assert not env.enqueued, "and must not start indexing on its own"


def test_a_deferred_original_registers_and_indexes_with_zero_bytes_uploaded(env):
    """The point of the whole path: indexing starts while the original is still
    in the browser."""
    out, status = _defer(env)
    assert status == 200
    assert out["original_pending"] is True

    asset = env.assets[out["asset_id"]]
    assert asset["storage_key"] == "originals/5/abc.mov"
    assert asset["meta"]["upload_state"] == "pending"
    assert asset["meta"]["client_proxy_key"] == "clientproxies/5/p.mp4"
    # The browser's measurement of the ORIGINAL, not the proxy's 540p shape.
    assert (asset["width"], asset["height"]) == (3840, 2160)
    assert asset["duration_s"] == 355.0

    assert len(env.enqueued) == 1
    job = env.enqueued[0]
    assert job["type"] == "index"
    assert job["payload"]["client_proxy_key"] == "clientproxies/5/p.mp4"


def test_a_deferred_original_without_a_proxy_is_refused(env):
    """Registering an asset whose bytes do not exist, with nothing to index
    instead, would leave a project that can never load."""
    out, status = _defer(env, client_proxy_key="")
    assert status == 400
    assert not env.assets


def test_a_proxy_key_from_another_project_is_refused(env):
    out, status = _defer(env, client_proxy_key="clientproxies/999/p.mp4")
    assert status == 400
    assert not env.assets


def test_a_missing_proxy_object_is_refused(env, monkeypatch):
    monkeypatch.setattr(video.storage, "head_bytes", lambda k: None)
    out, status = _defer(env)
    assert status == 400
    assert not env.assets


# ── the rollout gate ────────────────────────────────────────────────────────

def test_with_the_flag_off_a_deferred_original_is_not_deferred(env, monkeypatch):
    """The executor is a separate manual deploy. With the flag off the server
    must not accept a path the indexer may not be deployed to understand — and
    a stale bundle in an open tab is the one client a deploy cannot update.
    """
    monkeypatch.setattr(video.storage, "PROXY_FIRST_UPLOADS", False)
    monkeypatch.setattr(storage, "PROXY_FIRST_UPLOADS", False)
    out, status = _defer(env)
    # Falls through to the ORDINARY path: the object is checked for real and
    # the asset is created as a normal, complete original.
    assert status == 200
    asset = env.assets[out["asset_id"]]
    assert asset["meta"].get("upload_state") is None, (
        "with the flag off nothing may be registered as pending")
    assert env.enqueued[0]["payload"].get("client_proxy_key") is None


# ── the original landing ────────────────────────────────────────────────────

def _pending_asset(cur, duration=355.0):
    cur.assets[901] = {
        "id": 901, "project_id": 5, "kind": "original",
        "storage_key": "originals/5/abc.mov", "duration_s": duration,
        "meta": {"upload_state": "pending", "upload_progress": 0.4,
                 "client_proxy_key": "clientproxies/5/p.mp4"},
    }
    return 901


def test_a_matching_original_is_adopted_without_re_indexing(env, monkeypatch, app_ctx):
    aid = _pending_asset(env)
    monkeypatch.setattr(video.mp4probe, "duration_of_key",
                        lambda s, k, n: 355.04)
    body, status = app_ctx(aid)
    assert status == 200
    assert body["upload_state"] == "ready"
    assert body.get("reindexing") is None
    assert env.assets[aid]["meta"]["upload_state"] == "ready"
    assert not env.enqueued, "a matching original must not re-index"


def test_an_original_that_disagrees_with_the_browser_re_indexes(env, monkeypatch,
                                                               app_ctx):
    """The hole this closes. Every timestamp in the edit was written against a
    duration the browser reported; if the file disagrees, the first thing that
    would notice is the export."""
    aid = _pending_asset(env, duration=355.0)
    monkeypatch.setattr(video.mp4probe, "duration_of_key",
                        lambda s, k, n: 402.0)
    body, status = app_ctx(aid)
    assert status == 200
    assert body["reindexing"] is True
    assert env.assets[aid]["meta"]["duration_drift"]["actual_s"] == 402.0
    assert len(env.enqueued) == 1
    # No client_proxy_key: the re-index takes the ordinary trusted path.
    assert env.enqueued[0]["payload"].get("client_proxy_key") is None
    assert env.enqueued[0]["payload"]["reindex"] is True


def test_an_unreadable_header_fails_open(env, monkeypatch, app_ctx):
    """A container we cannot parse is not evidence the browser lied."""
    aid = _pending_asset(env)
    monkeypatch.setattr(video.mp4probe, "duration_of_key", lambda s, k, n: None)
    body, status = app_ctx(aid)
    assert status == 200
    assert body.get("reindexing") is None
    assert not env.enqueued


def test_landing_twice_is_idempotent(env, monkeypatch, app_ctx):
    """The studio retries this POST after a network blip; a finished upload
    must not come back as an error to a client that only lost the response."""
    aid = _pending_asset(env)
    monkeypatch.setattr(video.mp4probe, "duration_of_key", lambda s, k, n: 355.0)
    app_ctx(aid)
    env.enqueued.clear()
    body, status = app_ctx(aid)
    assert status == 200
    assert body["upload_state"] == "ready"
    assert not env.enqueued, "the retry must not enqueue a second index"


@pytest.fixture
def app_ctx(monkeypatch):
    """Call original_upload_ready past its @token_required decorator."""
    from flask import Flask
    app = Flask(__name__)
    fn = video.original_upload_ready.__wrapped__ \
        if hasattr(video.original_upload_ready, "__wrapped__") else None

    def call(asset_id):
        with app.test_request_context(json={"asset_id": asset_id}):
            target = fn or video.original_upload_ready
            resp = target(7, 5) if fn else target(5)
            body, status = (resp if isinstance(resp, tuple) else (resp, 200))
            return body.get_json(), status
    if fn is None:
        pytest.skip("original_upload_ready is not unwrappable")
    return call
