"""Executor side: the storage mode is read in the background, never inside
/health, a render, a download or an upload (K2, 30 Oct 2026).

The box probes every slot's /health with a 3 s timeout and checks readiness
before every /run. Once a slot can see the next store's keys (K1), the router
read app_kv 'storage_routing' inline every 30 s; in a database outage that
read could hang the probe and make a healthy slot look down. kv_feed.py and
storage._build_router move it to one background reader per process; the
shared behaviour is pinned in backend/tests/test_storage_mode_feed.py, the
executor's own wiring and routes here.
"""

import http.client
import json
import pathlib
import socket
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import config
import http_server
import storage
import storage_routing as routing
from s3_fake import FakeS3

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Script-style suites (test_index_lanes, test_round95_image_senses) replace
# storage functions at import time for the whole run; rebind the real ones.
_ROUTED = ("download_to", "upload_file", "exists", "object_bytes",
           "copy_object", "presign_get", "delete_keys", "sha256_object",
           "locate", "_stream_copy", "_head", "routing_status", "_plan")


def _real_functions():
    import types
    ns = {"__name__": "_storage_pristine", "__file__": storage.__file__}
    with open(storage.__file__, encoding="utf-8") as f:
        exec(compile(f.read(), storage.__file__, "exec"), ns)
    out = {n: types.FunctionType(ns[n].__code__, storage.__dict__, n,
                                 ns[n].__defaults__, ns[n].__closure__)
           for n in _ROUTED}
    # The pristine copy started nothing it keeps: its feed is dropped here.
    ns.get("_mode_feed") and ns["_mode_feed"].stop()
    return out


_REAL = _real_functions()

NEXT_ENV = {
    "S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "S3_NEXT_ACCESS_KEY_ID": "next-id",
    "S3_NEXT_SECRET_ACCESS_KEY": "next-secret",
    "S3_NEXT_BUCKET": "valmera-media-usw",
}
LEGACY_ENV = {"S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
              "S3_ACCESS_KEY_ID": "l", "S3_SECRET_ACCESS_KEY": "l",
              "S3_BUCKET": "valmera-media"}


class OperationalError(Exception):
    pass


def _wait(pred, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


@pytest.fixture(autouse=True)
def _pristine_storage(monkeypatch):
    for name, fn in _REAL.items():
        monkeypatch.setattr(storage, name, fn)


@pytest.fixture
def stores(monkeypatch, tmp_path):
    legacy, nxt = FakeS3("legacy"), FakeS3("next")
    monkeypatch.setattr(storage, "client", lambda: legacy)
    monkeypatch.setattr(storage, "_next_client", lambda: nxt)
    monkeypatch.setattr(config, "S3_BUCKET", "valmera-media")
    monkeypatch.setattr(storage, "check_workdir_capacity",
                        lambda *a, **k: None)
    for k, v in {**LEGACY_ENV, **NEXT_ENV}.items():
        monkeypatch.setenv(k, v)
    storage._locator.clear()
    return legacy, nxt


@pytest.fixture
def built(monkeypatch):
    made = []

    def build(fetch, cache_path=None, ttl=None, backoff=None, start=True):
        feed, router = storage._build_router(fetch, cache_path=cache_path)
        if ttl is not None:
            feed._ttl = ttl
        if backoff is not None:
            feed._backoff = feed._backoff_max = backoff
        monkeypatch.setattr(storage, "_mode_feed", feed)
        monkeypatch.setattr(storage, "_router", router)
        made.append(feed)
        if start:
            feed.start()
        return feed, router
    yield build
    for feed in made:
        assert feed.stop(), "a refresher outlived its test"


def _hang():
    gate, calls = threading.Event(), []

    def fetch():
        calls.append(1)
        gate.wait(30)
        raise OperationalError("timeout expired")
    return fetch, gate, calls


def test_worker_and_backend_copies_are_identical():
    assert (ROOT / "worker" / "kv_feed.py").read_bytes() == \
        (ROOT / "backend" / "kv_feed.py").read_bytes(), \
        "edit backend/kv_feed.py, copy it to worker/"


def test_module_router_reads_through_the_background_feed():
    assert storage._router._fetch == storage._mode_feed.get
    assert storage._router._ttl == 0
    assert storage._mode_feed._fetch is storage._fetch_routing_row
    assert storage._mode_feed._enabled is storage._next_configured


def test_health_answers_at_once_while_the_database_hangs(stores, built):
    fetch, gate, calls = _hang()
    built(fetch, start=False)               # the first /health starts it
    srv = ThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        for i in range(3):
            conn = http.client.HTTPConnection("127.0.0.1",
                                              srv.server_address[1],
                                              timeout=3)
            t0 = time.monotonic()
            conn.request("GET", "/health")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            assert resp.status == 200 and body["status"] == "ok"
            assert time.monotonic() - t0 < 1.0, i
            st = body["storage"]
            assert st["mode"] == "legacy" and st["degraded"] is False
            assert st["next"] is True
            assert st["mode_read"]["source"] == "default"
            if i == 0:
                assert _wait(lambda: calls)   # the refresher is stuck ...
        assert len(calls) == 1                # ... alone, not per probe
    finally:
        gate.set()
        srv.shutdown()
        srv.server_close()


def test_downloads_and_uploads_never_wait_on_the_mode(stores, built,
                                                      tmp_path):
    legacy, nxt = stores
    legacy._store("proxies/1/a.mp4", b"abc")
    fetch, gate, _ = _hang()
    built(fetch)
    try:
        t0 = time.monotonic()
        dest = tmp_path / "a.mp4"
        storage.download_to("proxies/1/a.mp4", str(dest))
        storage.upload_file(str(dest), "media/1/r.mp4", "video/mp4")
        assert storage.exists("media/1/r.mp4")
        assert storage.presign_get("media/1/r.mp4").startswith(
            "fake://legacy/")
        assert time.monotonic() - t0 < 1.0
        assert "media/1/r.mp4" in legacy.objects
        assert "media/1/r.mp4" not in nxt.objects
    finally:
        gate.set()


def test_outage_keeps_the_mode_and_recovery_brings_the_new_one(stores,
                                                               built,
                                                               tmp_path):
    legacy, nxt = stores
    rows = ["dual_write"]

    def fetch():
        v = rows[0]
        if isinstance(v, Exception):
            raise v
        return v
    feed, _ = built(fetch, cache_path=str(tmp_path / "mode.json"),
                    ttl=0.02, backoff=0.02)
    assert _wait(lambda: storage.routing_status()["mode"] == "dual_write")
    rows[0] = OperationalError("server closed the connection")
    assert _wait(lambda: feed.status().get("failures", 0) >= 3)
    src = tmp_path / "x.bin"
    src.write_bytes(b"x")
    storage.upload_file(str(src), "media/2/x.mp4", "video/mp4")
    assert "media/2/x.mp4" in legacy.objects and "media/2/x.mp4" in \
        nxt.objects                           # still dual_write
    assert storage.routing_status()["mode_read"]["error"] == \
        "OperationalError"
    rows[0] = '{"mode": "next", "by": "storage_migrate"}'
    assert _wait(lambda: storage.routing_status()["mode"] == "next")
    storage.upload_file(str(src), "media/2/y.mp4", "video/mp4")
    assert "media/2/y.mp4" in nxt.objects
    assert "media/2/y.mp4" not in legacy.objects
    # A restart during a later outage starts from the cached mode.
    assert _wait(lambda: json.loads(
        (tmp_path / "mode.json").read_text())["value"] == "next")

    def down():
        raise OperationalError("down")
    built(down, cache_path=str(tmp_path / "mode.json"))
    st = storage.routing_status()
    assert st["mode"] == "next" and st["mode_read"]["source"] == "cache"


def test_the_kv_read_is_bounded(monkeypatch):
    import psycopg2
    seen = {"sql": []}

    class Cur:
        def __init__(self):
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            seen["sql"].append((sql, args))
            self.row = {"t": "app_kv"} if "to_regclass" in sql else \
                {"value": "copy"}

        def fetchone(self):
            return self.row

    class Conn:
        autocommit = True

        def cursor(self):
            return Cur()

        def rollback(self):
            seen["rolled_back"] = True

        def close(self):
            seen["closed"] = True

    def connect(url, **kw):
        seen["kw"] = kw
        return Conn()
    monkeypatch.setattr(psycopg2, "connect", connect)
    assert storage._fetch_routing_row() == "copy"
    kw = seen["kw"]
    assert kw["connect_timeout"] == 3
    assert kw["keepalives"] == 1 and kw["keepalives_idle"] <= 5
    assert "options" not in kw          # PgBouncer rejects startup options
    first_sql, first_args = seen["sql"][0]
    assert "set_config('statement_timeout'" in first_sql
    assert first_args == ("2000",)
    assert seen["closed"] and seen["rolled_back"]


def test_a_silent_database_is_given_up_on_in_seconds(monkeypatch):
    pytest.importorskip("psycopg2")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held, stop = [], threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                pass
    t = threading.Thread(target=accept, daemon=True)
    t.start()
    port = srv.getsockname()[1]
    monkeypatch.setattr(config, "DATABASE_URL",
                        f"postgresql://u@127.0.0.1:{port}/db?sslmode=disable")
    t0 = time.monotonic()
    try:
        with pytest.raises(Exception) as err:
            storage._fetch_routing_row()
        assert 2.0 <= time.monotonic() - t0 < 8.0
        assert "timeout" in str(err.value).lower()
    finally:
        stop.set()
        t.join(2)
        for c in held:
            c.close()
        srv.close()


def test_one_refresher_and_none_left_behind(stores):
    name = f"kv-feed:{routing.KV_KEY}"

    def alive():
        return {t for t in threading.enumerate()
                if t.name == name and t.is_alive()}
    before = alive()
    reads = []

    def down():
        reads.append(1)
        raise OperationalError("down")
    feed, router = storage._build_router(down)
    feed._backoff = feed._backoff_max = 0.01
    workers = [threading.Thread(target=lambda: [router.plan()
                                                for _ in range(200)])
               for _ in range(12)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(10)
    assert _wait(lambda: len(reads) >= 5)
    assert alive() - before == {feed._thread}
    assert feed.stop()
    assert not alive() - before
