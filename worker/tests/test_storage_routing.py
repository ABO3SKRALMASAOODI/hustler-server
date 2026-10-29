"""Executor-side storage under each routing mode (storage_routing.py).

The default reads nothing new and writes nowhere new; while media moves off
R2, downloads fall back, uploads land where the mode says, server-side
copies cross stores without staging, sidecars follow the same rules, and
deletes reach every store so a fallback read cannot resurrect a file."""

import pytest

import config
import sidecars
import storage
import storage_routing as routing
from s3_fake import FakeS3

# Script-style suites (test_index_lanes, test_round95_image_senses) replace
# storage.download_to / upload_file / exists at import time, for the whole
# run. These tests need the real functions, bound to the real module.
_ROUTED = ("download_to", "upload_file", "exists", "object_bytes",
           "copy_object", "presign_get", "delete_keys", "sha256_object",
           "locate", "_stream_copy", "_head")


def _real_functions():
    import types
    ns = {"__name__": "_storage_pristine", "__file__": storage.__file__}
    with open(storage.__file__, encoding="utf-8") as f:
        exec(compile(f.read(), storage.__file__, "exec"), ns)
    return {n: types.FunctionType(ns[n].__code__, storage.__dict__, n,
                                  ns[n].__defaults__, ns[n].__closure__)
            for n in _ROUTED}


_REAL = _real_functions()


@pytest.fixture(autouse=True)
def _pristine_storage(monkeypatch):
    for name, fn in _REAL.items():
        monkeypatch.setattr(storage, name, fn)

NEXT_ENV = {
    "S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "S3_NEXT_ACCESS_KEY_ID": "next-id",
    "S3_NEXT_SECRET_ACCESS_KEY": "next-secret",
    "S3_NEXT_BUCKET": "valmera-media-usw",
}


@pytest.fixture
def stores(monkeypatch):
    legacy, nxt = FakeS3("legacy"), FakeS3("next")
    monkeypatch.setattr(storage, "client", lambda: legacy)
    monkeypatch.setattr(storage, "_next_client", lambda: nxt)
    monkeypatch.setattr(config, "S3_BUCKET", "valmera-media")
    monkeypatch.setattr(storage, "check_workdir_capacity",
                        lambda *a, **k: None)
    for k, v in NEXT_ENV.items():
        monkeypatch.setenv(k, v)
    for k, v in {"S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
                 "S3_ACCESS_KEY_ID": "l", "S3_SECRET_ACCESS_KEY": "l",
                 "S3_BUCKET": "valmera-media"}.items():
        monkeypatch.setenv(k, v)
    mode = {"v": "legacy"}
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=lambda: mode["v"], ttl_s=0, log=lambda m: None))

    def set_mode(m):
        mode["v"] = m
        storage._locator.clear()
    storage._locator.clear()
    return legacy, nxt, set_mode


def test_default_mode_never_builds_a_next_client(monkeypatch, tmp_path):
    legacy = FakeS3("legacy", {"proxies/1/a.mp4": b"abc"})
    monkeypatch.setattr(storage, "client", lambda: legacy)
    monkeypatch.setattr(config, "S3_BUCKET", "valmera-media")
    monkeypatch.setattr(storage, "check_workdir_capacity",
                        lambda *a, **k: None)
    for k in NEXT_ENV:
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):
        raise AssertionError("next store used in legacy mode")
    monkeypatch.setattr(storage, "_next_client", boom)
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=boom, log=lambda m: None))
    dest = tmp_path / "a.mp4"
    storage.download_to("proxies/1/a.mp4", str(dest))
    assert dest.read_bytes() == b"abc"
    storage.upload_file(str(dest), "media/1/r.mp4", "video/mp4")
    storage.copy_object("media/1/r.mp4", "media/1/r2.mp4")
    assert storage.presign_get("media/1/r.mp4").startswith("fake://legacy/")
    assert storage.exists("media/1/r2.mp4")
    assert storage.delete_keys(["media/1/r2.mp4"]) == 1
    assert storage.routing_status()["mode"] == "legacy"


def test_copy_mode_downloads_fall_back_and_writes_stay_on_r2(stores,
                                                           tmp_path):
    legacy, nxt, set_mode = stores
    legacy._store("originals/1/a.mp4", b"r2-bytes")
    nxt._store("proxies/1/p.mp4", b"b2-bytes")
    set_mode("copy")
    a, p = tmp_path / "a", tmp_path / "p"
    storage.download_to("originals/1/a.mp4", str(a))
    storage.download_to("proxies/1/p.mp4", str(p))
    assert a.read_bytes() == b"r2-bytes" and p.read_bytes() == b"b2-bytes"
    assert storage.object_bytes("originals/1/a.mp4") == 8
    assert storage.sha256_object("proxies/1/p.mp4", 8)
    storage.upload_file(str(a), "media/1/out.mp4", "video/mp4")
    assert "media/1/out.mp4" in legacy.objects
    assert "media/1/out.mp4" not in nxt.objects
    assert storage.presign_get("proxies/1/p.mp4").startswith("fake://next/")
    assert storage.presign_get("originals/1/a.mp4").startswith(
        "fake://legacy/")
    nxt._store("media/1/out.mp4", b"copied")
    storage.delete_keys(["media/1/out.mp4"])
    assert "media/1/out.mp4" not in legacy.objects
    assert "media/1/out.mp4" not in nxt.objects


def test_dual_write_uploads_both_and_survives_a_secondary_failure(
        stores, tmp_path):
    legacy, nxt, set_mode = stores
    set_mode("dual_write")
    f = tmp_path / "f"
    f.write_bytes(b"render")
    storage.upload_file(str(f), "media/2/a.mp4", "video/mp4")
    assert legacy.objects["media/2/a.mp4"]["data"] == b"render"
    assert nxt.objects["media/2/a.mp4"]["data"] == b"render"
    nxt.fail["upload_file"] = RuntimeError("b2 down")
    storage.upload_file(str(f), "media/2/b.mp4", "video/mp4")
    assert "media/2/b.mp4" in legacy.objects
    legacy.fail["upload_file"] = RuntimeError("r2 down")
    with pytest.raises(RuntimeError):
        storage.upload_file(str(f), "media/2/c.mp4", "video/mp4")


def test_next_mode_copies_across_stores_without_staging(stores):
    legacy, nxt, set_mode = stores
    legacy._store("originals/3/src.mp4", b"original-bytes")
    set_mode("next")
    storage.copy_object("originals/3/src.mp4", "originals/3/dedup.mp4")
    assert nxt.objects["originals/3/dedup.mp4"]["data"] == b"original-bytes"
    assert "originals/3/dedup.mp4" not in legacy.objects
    assert ("upload_fileobj", "originals/3/dedup.mp4") in nxt.calls
    # Source already in next: a server-side copy there.
    storage.copy_object("originals/3/dedup.mp4", "originals/3/again.mp4")
    assert ("copy", "originals/3/again.mp4") in nxt.calls
    # Nowhere at all: the copy's own error.
    with pytest.raises(Exception):
        storage.copy_object("originals/3/missing.mp4", "x")
    assert storage.exists("originals/3/src.mp4")      # legacy, via fallback


def test_degraded_process_keeps_using_r2(monkeypatch):
    for k in NEXT_ENV:
        monkeypatch.delenv(k, raising=False)
    r = routing.Router(fetch=lambda: "next", log=lambda m: None)
    r._mode = "next"
    assert r.mode() == "legacy" and r.status()["degraded"] is True


def test_sidecar_store_follows_the_routing(stores, monkeypatch):
    legacy, nxt, set_mode = stores
    store = sidecars.R2Store()
    monkeypatch.setattr(store, "client", lambda: legacy)
    monkeypatch.setattr(store, "_next_client", lambda: nxt)
    legacy._store("sidecars/ab/x/faces/k.json", b"{\"r2\": 1}")
    nxt._store("sidecars/ab/x/faces/k2.json", b"{\"b2\": 1}")
    set_mode("copy")
    assert store.get_bytes("sidecars/ab/x/faces/k.json") == b'{"r2": 1}'
    assert store.get_bytes("sidecars/ab/x/faces/k2.json") == b'{"b2": 1}'
    assert store.get_bytes("sidecars/ab/x/faces/none.json") is None
    listed = store.list("sidecars/ab/x/")
    assert set(listed) == {"sidecars/ab/x/faces/k.json",
                           "sidecars/ab/x/faces/k2.json"}
    store.put_bytes("sidecars/ab/x/faces/new.json", b"{}")
    assert "sidecars/ab/x/faces/new.json" in legacy.objects
    assert "sidecars/ab/x/faces/new.json" not in nxt.objects
    set_mode("next")
    store.put_bytes("sidecars/ab/x/faces/n2.json", b"{}")
    assert "sidecars/ab/x/faces/n2.json" in nxt.objects
    store.delete("sidecars/ab/x/faces/k.json")
    assert "sidecars/ab/x/faces/k.json" not in legacy.objects
    # A real error (not "missing") still surfaces.
    nxt.fail["get_object"] = RuntimeError("timeout")
    legacy.fail["get_object"] = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        store.get_bytes("sidecars/ab/x/faces/n2.json")


def test_health_route_reports_storage(stores):
    legacy, nxt, set_mode = stores
    set_mode("copy")
    st = storage.routing_status()
    assert st["mode"] == "copy" and st["next"] is True
    assert st["next_host"] == "s3.us-west-004.backblazeb2.com"
    assert "next-secret" not in repr(st)
