"""The storage mode is read in the background, never inside a call (K2,
30 Oct 2026; kv_feed.py + storage._build_router).

Pins: a database that is down (or hangs) at boot answers the health route and
every storage call at once with the default mode; a database lost mid-run
keeps the last-known mode; recovery picks up a mode set meanwhile; the read
is bounded (3 s connect, 2 s statement, against a real libpq too); one
refresher per process, none left behind; the local cache; and A2's rule that
a cutover mode only takes effect where the next store's keys are.
"""

import gc
import json
import os
import pathlib
import socket
import threading
import time

import pytest

import kv_feed
import storage
import storage_routing as routing
from s3_fake import FakeS3

ROOT = pathlib.Path(__file__).resolve().parents[2]

NEXT_ENV = {
    "S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "S3_NEXT_ACCESS_KEY_ID": "next-id",
    "S3_NEXT_SECRET_ACCESS_KEY": "next-secret",
    "S3_NEXT_BUCKET": "valmera-media-usw",
}
LEGACY_ENV = {
    "S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
    "S3_ACCESS_KEY_ID": "legacy-id",
    "S3_SECRET_ACCESS_KEY": "legacy-secret",
    "S3_BUCKET": "valmera-media",
}


class OperationalError(Exception):
    """Stands in for psycopg2's (the type name is what status reports)."""


def _wait(pred, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def _feed_threads(feed=None):
    name = f"kv-feed:{routing.KV_KEY}"
    ts = [t for t in threading.enumerate() if t.name == name and t.is_alive()]
    if feed is not None:
        ts = [t for t in ts if t is feed._thread]
    return ts


@pytest.fixture
def both_envs(monkeypatch):
    for k, v in {**LEGACY_ENV, **NEXT_ENV}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def built(monkeypatch, both_envs):
    """storage's own wiring around a test fetch; installs it as THE router
    and stops its refresher afterwards."""
    made = []

    def build(fetch, cache_path=None, ttl=None, backoff=None,
              backoff_max=None, start=True):
        feed, router = storage._build_router(fetch, cache_path=cache_path)
        if ttl is not None:
            feed._ttl = ttl
        if backoff is not None:
            feed._backoff = feed._backoff_max = backoff
        if backoff_max is not None:
            feed._backoff_max = backoff_max
        monkeypatch.setattr(storage, "_mode_feed", feed)
        monkeypatch.setattr(storage, "_router", router)
        storage._locator.clear()
        made.append(feed)
        if start:
            feed.start()
        return feed, router
    yield build
    for feed in made:
        assert feed.stop(), "a refresher outlived its test"


def _hang():
    gate = threading.Event()
    calls = []

    def fetch():
        calls.append(time.monotonic())
        gate.wait(30)
        raise OperationalError("timeout expired")
    return fetch, gate, calls


def test_backend_and_worker_copies_are_identical():
    other = ROOT / "worker" / "kv_feed.py"
    if not other.exists():
        pytest.skip("worker/kv_feed.py ships with the next executor release")
    assert (ROOT / "backend" / "kv_feed.py").read_bytes() == \
        other.read_bytes(), "edit backend/kv_feed.py, copy it to worker/"


def test_module_router_reads_through_the_background_feed():
    assert storage._router._fetch == storage._mode_feed.get
    assert storage._router._ttl == 0          # the feed is the cache
    assert storage._mode_feed._fetch is storage._fetch_routing_row
    assert storage._mode_feed._enabled is storage._next_configured
    assert storage._mode_feed._ttl == float(
        os.environ.get("STORAGE_ROUTING_TTL_S") or 30)
    assert storage._mode_feed.name == "storage_routing"


# ── a database that is down or hangs at boot ────────────────────────────────

def test_db_hung_at_boot_answers_status_and_calls_at_once(built,
                                                          monkeypatch):
    fetch, gate, calls = _hang()
    feed, router = built(fetch, start=False)    # the first call starts it
    legacy, nxt = FakeS3("legacy", {"media/1/a.mp4": b"abc"}), FakeS3("next")
    monkeypatch.setattr(storage, "client", lambda *a, **k: legacy)
    monkeypatch.setattr(storage, "bucket", lambda: "valmera-media")
    monkeypatch.setattr(storage, "_next_client", lambda *a, **k: nxt)
    try:
        t0 = time.monotonic()
        st = storage.routing_status()
        assert time.monotonic() - t0 < 0.5
        assert st["mode"] == "legacy" and st["requested"] == "legacy"
        assert st["degraded"] is False and st["next"] is True
        assert _wait(lambda: calls)               # the refresher is reading
        t0 = time.monotonic()
        up = storage.presign_upload("originals/1/x.mp4", 100, "video/mp4")
        url = storage.presign_get("media/1/a.mp4")
        assert storage.head_bytes("media/1/a.mp4") == 3
        assert time.monotonic() - t0 < 0.5
        assert up["url"].startswith("fake://legacy/")
        assert url.startswith("fake://legacy/")
        st = storage.routing_status()
        assert st["mode_read"]["source"] == "default"
        assert "NotReadYet" in st.get("error", "")
        assert len(calls) == 1                    # one read in flight, no pile
    finally:
        gate.set()
    assert _wait(lambda: feed.status().get("error") == "OperationalError")
    st = storage.routing_status()
    assert st["mode"] == "legacy" and st["mode_read"]["failures"] >= 1
    assert "timeout expired" not in json.dumps(st)   # type names only


def test_db_down_at_boot_with_a_fresh_cache_uses_the_last_mode(built,
                                                              tmp_path):
    cache = tmp_path / "mode.json"
    feed, _ = built(lambda: '{"mode": "copy", "note": "x"}',
                    cache_path=str(cache))
    assert _wait(lambda: cache.exists())
    assert json.loads(cache.read_text())["value"] == "copy"   # the word only
    assert "note" not in cache.read_text()
    feed.stop()

    def down():
        raise OperationalError("could not connect")
    feed2, _ = built(down, cache_path=str(cache))
    st = storage.routing_status()
    assert st["mode"] == "copy" and st["mode_read"]["source"] == "cache"
    assert storage._plan().read == ("next", "legacy")


def test_a_stale_or_foreign_cache_is_ignored(built, tmp_path):
    cache = tmp_path / "mode.json"

    def down():
        raise OperationalError("down")
    cache.write_text(json.dumps({"v": 1, "name": "storage_routing",
                                 "value": "next",
                                 "at": time.time() - 2 * 86400}))
    built(down, cache_path=str(cache))
    assert storage.routing_status()["mode"] == "legacy"
    for bad in ({"v": 1, "name": "other_row", "value": "next",
                 "at": time.time()},
                {"v": 1, "name": "storage_routing", "value": "sideways",
                 "at": time.time()},
                {"v": 1, "name": "storage_routing", "value": "next",
                 "at": time.time() + 3600},
                "not json {"):
        cache.write_text(bad if isinstance(bad, str) else json.dumps(bad))
        built(down, cache_path=str(cache))
        assert storage.routing_status()["mode"] == "legacy", bad
    link = tmp_path / "link.json"
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"v": 1, "name": "storage_routing",
                                "value": "next", "at": time.time()}))
    link.symlink_to(good)
    built(down, cache_path=str(link))
    assert storage.routing_status()["mode"] == "legacy"   # never a symlink
    built(down, cache_path=str(good))
    assert storage.routing_status()["mode"] == "next"


# ── a database lost mid-run, and its recovery ───────────────────────────────

def test_db_down_mid_run_keeps_the_last_known_mode(built):
    rows = ["next"]

    def fetch():
        v = rows[0]
        if isinstance(v, Exception):
            raise v
        return v
    feed, router = built(fetch, ttl=0.02, backoff=0.02, backoff_max=0.05)
    assert _wait(lambda: storage.routing_status()["mode"] == "next")
    rows[0] = OperationalError("server closed the connection")
    assert _wait(lambda: feed.status().get("failures", 0) >= 3)
    st = storage.routing_status()
    assert st["mode"] == "next" and st["requested"] == "next"
    assert st["mode_read"]["source"] == "db"
    assert st["mode_read"]["error"] == "OperationalError"
    assert "error" not in {k for k in st if k != "mode_read"}
    assert storage._plan().presign == "next"


def test_recovery_picks_up_a_mode_set_during_the_outage(built):
    rows = [OperationalError("down")]

    def fetch():
        v = rows[0]
        if isinstance(v, Exception):
            raise v
        return v
    feed, _ = built(fetch, ttl=0.02, backoff=0.02, backoff_max=0.05)
    assert _wait(lambda: feed.status().get("failures", 0) >= 2)
    assert storage.routing_status()["mode"] == "legacy"
    rows[0] = '{"mode": "rollback"}'
    assert _wait(lambda: storage.routing_status()["mode"] == "rollback")
    st = storage.routing_status()
    assert "failures" not in st["mode_read"] and "error" not in st
    rows[0] = None                                   # the row is deleted
    assert _wait(lambda: storage.routing_status()["mode"] == "legacy")


def test_a_mode_change_propagates_within_one_ttl(built):
    rows = ["copy"]
    feed, _ = built(lambda: rows[0], ttl=0.1)
    assert _wait(lambda: storage.routing_status()["mode"] == "copy")
    t0 = time.monotonic()
    rows[0] = "dual_write"
    assert _wait(lambda: storage.routing_status()["mode"] == "dual_write")
    assert time.monotonic() - t0 < 1.0


def test_failures_back_off_and_cap():
    def down():
        raise OperationalError("down")
    feed = kv_feed.Feed(down, "t", ttl_s=30, backoff_s=5, backoff_max_s=60,
                        log=lambda m: None)
    assert [feed._tick() for _ in range(7)] == [5, 10, 20, 40, 60, 60, 60]
    feed2 = kv_feed.Feed(lambda: "x", "t", ttl_s=30, log=lambda m: None)
    assert feed2._tick() == 30 and feed2.get() == "x"


def test_a_row_naming_no_mode_keeps_the_mode_and_is_said_once(both_envs):
    logs, rows = [], ["next"]
    feed, router = storage._build_router(lambda: rows[0])
    feed._log = logs.append
    try:
        assert feed.refresh_now() and router.mode() == "next"
        rows[0] = "sideways"
        for _ in range(5):
            assert feed.refresh_now()
            assert router.mode() == "next"
        assert len([m for m in logs if "no usable value" in m]) == 1
        assert feed.status()["rejected"] is True
    finally:
        feed.stop()


# ── A2's safety rules are untouched ─────────────────────────────────────────

def test_without_the_next_store_nothing_reads_and_nothing_starts(
        monkeypatch):
    for k, v in LEGACY_ENV.items():
        monkeypatch.setenv(k, v)
    for k in NEXT_ENV:
        monkeypatch.delenv(k, raising=False)
    calls = []
    feed, router = storage._build_router(lambda: calls.append(1) or "next")
    try:
        assert router.mode() == "legacy" and router.status()["next"] is False
        assert not feed.started() and calls == []
        feed.start()
        assert _wait(lambda: feed.status().get("idle") is True)
        time.sleep(0.05)
        assert calls == []
    finally:
        feed.stop()


def test_a_cutover_mode_needs_this_processes_own_keys(monkeypatch, tmp_path):
    for k, v in LEGACY_ENV.items():
        monkeypatch.setenv(k, v)
    for k in NEXT_ENV:
        monkeypatch.delenv(k, raising=False)
    cache = tmp_path / "mode.json"
    cache.write_text(json.dumps({"v": 1, "name": "storage_routing",
                                 "value": "next", "at": time.time()}))
    feed, router = storage._build_router(lambda: "next", str(cache))
    try:
        router._mode = "next"            # even if the row says next ...
        st = router.status()
        assert st["mode"] == "legacy" and st["degraded"] is True
        assert router.plan().write == ("legacy",)
        for k, v in NEXT_ENV.items():    # ... the keys arrive: it applies
            monkeypatch.setenv(k, v)
        assert router.mode() == "next"
    finally:
        feed.stop()


# ── bounded ─────────────────────────────────────────────────────────────────

def test_the_database_read_is_bounded(monkeypatch):
    import psycopg2
    seen = {}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            seen.setdefault("sql", []).append(sql)

        def fetchone(self):
            return ('{"mode": "copy"}',)

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            seen["closed"] = True

    def connect(url, **kw):
        seen["kw"] = kw
        return Conn()
    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setenv("DIRECT_DATABASE_URL", "postgresql://u@h/db")
    assert storage._fetch_routing_row() == '{"mode": "copy"}'
    kw = seen["kw"]
    assert kw["connect_timeout"] == 3
    assert kw["options"] == "-c statement_timeout=2000"
    assert kw["keepalives"] == 1 and kw["keepalives_idle"] <= 5
    assert seen["closed"] is True


def test_a_silent_server_is_given_up_on_in_seconds(monkeypatch):
    """A real libpq connect to a port that accepts and never answers (the
    shape of a hung database) gives up after the 3 s connect timeout."""
    pytest.importorskip("psycopg2")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held = []
    stop = threading.Event()

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
    monkeypatch.setenv("DIRECT_DATABASE_URL",
                       f"postgresql://u@127.0.0.1:{port}/db"
                       "?sslmode=disable")
    t0 = time.monotonic()
    try:
        with pytest.raises(Exception) as err:
            storage._fetch_routing_row()
        took = time.monotonic() - t0
        assert 2.0 <= took < 8.0, took
        assert "timeout" in str(err.value).lower()
    finally:
        stop.set()
        t.join(2)
        for c in held:
            c.close()
        srv.close()


# ── one refresher, none left behind ─────────────────────────────────────────

def test_one_refresher_per_process_and_none_left_behind(both_envs):
    reads = []

    def down():
        reads.append(1)
        raise OperationalError("down")
    before = set(_feed_threads())
    feed, router = storage._build_router(down)
    feed._backoff, feed._backoff_max = 0.01, 0.02
    workers = [threading.Thread(target=lambda: [router.mode()
                                                for _ in range(200)])
               for _ in range(16)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(10)
    assert _wait(lambda: len(reads) >= 5)
    assert set(_feed_threads()) - before == {feed._thread}
    assert feed.stop(timeout=2.0)
    assert not set(_feed_threads()) - before
    router.mode()                      # a stopped feed never starts again
    assert not feed.started() and not set(_feed_threads()) - before


def test_a_dropped_feed_ends_its_thread(both_envs):
    before = set(_feed_threads())
    feed, router = storage._build_router(lambda: "copy")
    assert router.mode() in ("legacy", "copy")
    t = feed._thread
    assert t.is_alive()
    del feed, router
    gc.collect()
    t.join(2.0)
    assert not t.is_alive()
    assert not set(_feed_threads()) - before


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_forked_child_runs_its_own_refresher(both_envs):
    feed, router = storage._build_router(lambda: "copy")
    try:
        assert feed.start()
        assert _wait(lambda: feed.status()["source"] == "db")
        pid = os.fork()
        if pid == 0:                   # the child: no thread came along
            code = 1
            try:
                ok = not feed.started()
                ok = ok and router.mode() == "copy"   # last good value
                ok = ok and feed.started()            # its own refresher
                code = 0 if ok else 2
            finally:
                os._exit(code)
        end = time.monotonic() + 15
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            if time.monotonic() > end:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                pytest.fail("the forked child hung")
            time.sleep(0.02)
        assert os.WEXITSTATUS(status) == 0
    finally:
        feed.stop()


# ── the sealed keys' first read waits FIRST_WAIT_S in all, not per lookup ──

def test_a_hung_first_key_read_holds_a_health_probe_first_wait_in_all():
    """One status() looks the S3_NEXT_* names up a dozen times. While the
    first read of the sealed keys hangs (a DB outage at boot), each lookup
    used to wait FIRST_WAIT_S of its own: 3 s for the first /health on the
    box (its probe timeout), measured. Now the wait is one budget from the
    read's start."""
    import storage_creds as creds
    gate = threading.Event()

    def hung():
        gate.wait(30)
        raise OperationalError("timeout expired")
    base = dict(LEGACY_ENV)
    c = creds.Credentials(fetch=hung, env=base, log=lambda m: None)
    c._first_wait = 0.3
    view = creds.Env(base, c)
    feed = kv_feed.Feed(lambda: None, routing.KV_KEY, log=lambda m: None,
                        enabled=lambda: routing.store_config(
                            routing.NEXT, view) is not None)
    router = routing.Router(fetch=feed.get, env=view, ttl_s=0,
                            log=lambda m: None)
    try:
        c.prefetch()                                   # the import
        t0 = time.monotonic()
        for _ in range(3):
            st = router.status()
            assert st["mode"] == "legacy" and st["next"] is False
        assert routing.store_config(routing.NEXT, view) is None
        took = time.monotonic() - t0
        assert took < 0.3 + 0.25, took
        t1 = time.monotonic()
        router.status()                                # past the budget
        assert time.monotonic() - t1 < 0.05
    finally:
        gate.set()
        feed.stop()
