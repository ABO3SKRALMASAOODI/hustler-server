"""The provider-neutral storage switch (storage_routing.py, Oct 2026).

Pins: the default changes nothing (no second store, no database read);
every mode's reads, writes and deletes; a switch mid-upload; the retention
sweeper reaching both stores; and the two copies of the module staying one.
"""

import json
import os
import pathlib

import pytest

import storage
import storage_routing as routing
import r2_retention as r2
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


def test_backend_and_worker_copies_are_identical():
    a = (ROOT / "backend" / "storage_routing.py").read_bytes()
    b = (ROOT / "worker" / "storage_routing.py").read_bytes()
    assert a == b, "edit backend/storage_routing.py and copy it to worker/"


@pytest.mark.parametrize("raw,want", [
    (None, None), ("", None), ("next", "next"), (" COPY ", "copy"),
    ('{"mode": "dual_write", "by": "x"}', "dual_write"), ('"rollback"',
                                                         "rollback"),
    ("{bad json", None), ("sideways", None), (b"next_only", "next_only"),
])
def test_parse_mode(raw, want):
    assert routing.parse_mode(raw) == want


@pytest.mark.parametrize("endpoint,configured,want", [
    ("https://s3.us-west-004.backblazeb2.com", "", "us-west-004"),
    ("https://s3.us-west-002.backblazeb2.com", "auto", "us-west-002"),
    ("https://s3.us-west-004.backblazeb2.com", "us-west-004", "us-west-004"),
    ("https://acct.r2.cloudflarestorage.com", "", "auto"),
    ("https://acct.r2.cloudflarestorage.com", "auto", "auto"),
    ("https://s3.us-west-2.amazonaws.com", None, "us-west-2"),
    ("https://usc1.contabostorage.com", "", "auto"),
    ("https://usc1.contabostorage.com", "default", "default"),
    ("http://minio:9000", "us-east-1", "us-east-1"),
])
def test_region_for(endpoint, configured, want):
    assert routing.region_for(endpoint, configured) == want


def test_store_config_requires_all_four_and_never_leaks_in_status():
    assert routing.store_config("next", {}) is None
    partial = dict(NEXT_ENV)
    partial.pop("S3_NEXT_BUCKET")
    assert routing.store_config("next", partial) is None
    cfg = routing.store_config("next", NEXT_ENV)
    assert cfg["region"] == "us-west-004"
    assert cfg["addressing_style"] == "path"
    assert cfg["host"] == "s3.us-west-004.backblazeb2.com"
    r = routing.Router(fetch=lambda: "copy", env={**LEGACY_ENV, **NEXT_ENV})
    text = json.dumps(r.status())
    assert "secret" not in text and "next-id" not in text
    assert r.status()["next_host"] == "s3.us-west-004.backblazeb2.com"


def test_plans_never_lose_an_object():
    for mode in routing.MODES:
        p = routing.plan_for(mode)
        assert p.presign in p.write, mode
        assert set(p.write) <= set(p.read) | {"legacy"}, mode
        # Deletes reach every store reads may come from.
        assert set(p.read) <= set(p.delete), mode
        # Until R2 is retired, everything stays readable from it.
        if mode != "next_only":
            assert "legacy" in p.read, mode
    assert routing.plan_for("copy").write == ("legacy",)
    assert routing.plan_for("copy").read == ("next", "legacy")
    assert routing.plan_for("dual_write").write == ("legacy", "next")
    assert routing.plan_for("next").presign == "next"
    assert routing.plan_for("rollback").read == ("legacy", "next")
    assert routing.plan_for("whatever").mode == "legacy"


def test_router_without_next_store_never_reads_the_database():
    calls = []
    r = routing.Router(fetch=lambda: calls.append(1) or "next",
                       env=dict(LEGACY_ENV))
    assert r.mode() == "legacy" and r.plan().read == ("legacy",)
    assert calls == []


def test_router_reads_caches_and_survives_a_bad_row():
    now = [0.0]
    rows = ["copy"]
    calls = []

    def fetch():
        calls.append(1)
        v = rows[0]
        if isinstance(v, Exception):
            raise v
        return v

    r = routing.Router(fetch=fetch, env={**LEGACY_ENV, **NEXT_ENV},
                       ttl_s=30, clock=lambda: now[0], log=lambda m: None)
    assert r.mode() == "copy"
    assert r.mode() == "copy" and len(calls) == 1     # cached
    rows[0] = "next"
    now[0] = 31
    assert r.mode() == "next" and len(calls) == 2
    rows[0] = RuntimeError("db down")
    now[0] = 62
    assert r.mode() == "next"                         # last good mode kept
    assert "error" in r.status()
    rows[0] = "nonsense"
    now[0] = 100
    assert r.mode() == "next"                         # garbage ignored
    rows[0] = None
    now[0] = 140
    assert r.mode() == "legacy"                       # row deleted: default


def test_router_degrades_to_legacy_and_says_so():
    r = routing.Router(fetch=lambda: "next", env=dict(LEGACY_ENV),
                       log=lambda m: None)
    r._mode = "next"                    # the row says next ...
    st = r.status()
    assert st["mode"] == "legacy" and st["requested"] == "next"
    assert st["degraded"] is True and st["next"] is False


# ── backend storage under each mode ─────────────────────────────────────────

@pytest.fixture
def two_stores(monkeypatch):
    legacy, nxt = FakeS3("legacy"), FakeS3("next")
    monkeypatch.setattr(storage, "client", lambda *a, **k: legacy)
    monkeypatch.setattr(storage, "bucket", lambda: "valmera-media")
    monkeypatch.setattr(storage, "_next_client", lambda *a, **k: nxt)
    for k, v in {**LEGACY_ENV, **NEXT_ENV}.items():
        monkeypatch.setenv(k, v)
    mode = {"value": "legacy"}
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=lambda: mode["value"], ttl_s=0, log=lambda m: None))
    storage._locator.clear()

    def set_mode(m):
        mode["value"] = m
        storage._locator.clear()
    return legacy, nxt, set_mode


def test_default_mode_touches_only_the_legacy_store(monkeypatch):
    legacy = FakeS3("legacy", {"media/1/a.mp4": b"abc"})
    monkeypatch.setattr(storage, "client", lambda *a, **k: legacy)
    monkeypatch.setattr(storage, "bucket", lambda: "valmera-media")
    for k in NEXT_ENV:
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):
        raise AssertionError("the next store must not be used")
    monkeypatch.setattr(storage, "_next_client", boom)
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=boom, log=lambda m: None))
    url = storage.presign_get("media/1/a.mp4")
    assert url.startswith("fake://legacy/")
    assert ("head_object", "media/1/a.mp4") not in legacy.calls   # no HEAD
    assert storage.head_bytes("media/1/a.mp4") == 3
    up = storage.presign_upload("originals/1/x.mp4", 100, "video/mp4")
    assert up["url"].startswith("fake://legacy/")
    storage.put_bytes("ops/evidence/x.png", b"png", "image/png")
    assert "ops/evidence/x.png" in legacy.objects


def test_copy_mode_reads_next_first_and_falls_back(two_stores):
    legacy, nxt, set_mode = two_stores
    legacy._store("media/1/old.mp4", b"old")
    legacy._store("media/1/both.mp4", b"r2")
    nxt._store("media/1/both.mp4", b"b2")
    set_mode("copy")
    assert storage.presign_get("media/1/old.mp4").startswith("fake://legacy/")
    assert storage.presign_get("media/1/both.mp4").startswith("fake://next/")
    def heads():
        return sum(1 for c in nxt.calls if c[0] == "head_object")
    before = heads()
    storage.presign_get("media/1/both.mp4")          # cached: no new HEAD
    assert heads() == before
    assert storage.get_range_at("media/1/old.mp4", 1, 2) == b"ld"
    assert storage.get_object_whole("media/1/both.mp4", 10) == b"b2"
    assert storage.get_object_whole("media/1/old.mp4", 1) is None   # too big
    # Writes and browser uploads stay on R2 in copy mode ...
    up = storage.presign_upload("originals/1/new.mp4", 100, "video/mp4")
    assert up["url"].startswith("fake://legacy/")
    storage.put_bytes("retention/m.json.gz", b"{}", "application/json",
                      content_encoding="gzip")
    assert "retention/m.json.gz" in legacy.objects
    assert "retention/m.json.gz" not in nxt.objects
    # ... and deletes reach both.
    storage.delete_key("media/1/both.mp4")
    assert "media/1/both.mp4" not in legacy.objects
    assert "media/1/both.mp4" not in nxt.objects


def test_dual_write_writes_both_and_tolerates_a_secondary_failure(two_stores):
    legacy, nxt, set_mode = two_stores
    set_mode("dual_write")
    storage.put_bytes("ops/evidence/a.png", b"png", "image/png",
                      metadata={"via": "t"})
    assert legacy.objects["ops/evidence/a.png"]["meta"] == {"via": "t"}
    assert nxt.objects["ops/evidence/a.png"]["data"] == b"png"
    nxt.fail["put_object"] = RuntimeError("b2 down")
    storage.put_bytes("ops/evidence/b.png", b"png", "image/png")
    assert "ops/evidence/b.png" in legacy.objects
    legacy.fail["put_object"] = RuntimeError("r2 down")
    with pytest.raises(RuntimeError):
        storage.put_bytes("ops/evidence/c.png", b"png", "image/png")


def test_an_upload_started_before_the_switch_finishes_after_it(two_stores):
    legacy, nxt, set_mode = two_stores
    set_mode("copy")
    nbytes = 40 * 1024 * 1024
    up = storage.presign_upload("originals/7/big.mp4", nbytes, "video/mp4")
    assert up["mode"] == "multipart"
    uid = up["upload_id"]
    assert uid.startswith("legacy-")
    size = up["part_size"]
    parts = []
    for p in up["part_urls"][:-1]:
        r = legacy.upload_part(Bucket="b", Key="originals/7/big.mp4",
                               UploadId=uid, PartNumber=p["part_number"],
                               Body=b"x" * size)
        parts.append({"part_number": p["part_number"], "etag": r["ETag"]})
    set_mode("next")                                   # THE SWITCH
    # The tab comes back: resume finds the legacy upload and presigns there.
    again = storage.resume_multipart("originals/7/big.mp4", uid, nbytes,
                                     part_size=size)
    todo = [p for p in again["part_urls"] if not p.get("done")]
    assert len(todo) == 1 and todo[0]["url"].startswith("fake://legacy/")
    last = todo[0]["part_number"]
    r = legacy.upload_part(Bucket="b", Key="originals/7/big.mp4",
                           UploadId=uid, PartNumber=last,
                           Body=b"x" * (nbytes - size * (last - 1)))
    parts.append({"part_number": last, "etag": r["ETag"]})
    res = storage.finish_multipart("originals/7/big.mp4", uid, parts)
    assert res["ok"] is True
    assert len(legacy.objects["originals/7/big.mp4"]["data"]) == nbytes
    # Readable in next mode through the fallback; new uploads go to next.
    assert storage.head_bytes("originals/7/big.mp4") == nbytes
    assert storage.presign_upload("originals/7/n.mp4", 10, "video/mp4")[
        "url"].startswith("fake://next/")
    assert storage.find_multipart_upload("originals/7/big.mp4") is None


def test_find_multipart_upload_searches_both_stores(two_stores):
    legacy, nxt, set_mode = two_stores
    set_mode("next")
    uid = legacy.create_multipart_upload(Bucket="b",
                                         Key="originals/1/a.mp4")["UploadId"]
    assert storage.find_multipart_upload("originals/1/a.mp4") == uid
    storage.abort_multipart("originals/1/a.mp4", uid)
    assert uid not in legacy.uploads


def test_delete_project_objects_wipes_every_store(two_stores):
    legacy, nxt, set_mode = two_stores
    for s in (legacy, nxt):
        s._store("originals/9/a.mp4", b"1")
        s._store("media/9/b.mp4", b"2")
    legacy._store("media/10/keep.mp4", b"3")
    set_mode("copy")
    assert storage.delete_project_objects(9) == 2
    assert not [k for k in legacy.objects if "/9/" in k]
    assert not [k for k in nxt.objects if "/9/" in k]
    assert "media/10/keep.mp4" in legacy.objects


def test_retention_deletes_from_both_stores(two_stores, monkeypatch):
    legacy, nxt, set_mode = two_stores
    set_mode("copy")
    for s in (legacy, nxt):
        s._store("originals/5/old.mp4", b"x" * 10)
    targets = storage.delete_targets()
    assert [t[0] for t in targets] == ["legacy", "next"]
    done, errors = r2._delete_batch(targets[0][1], targets[0][2],
                                    ["originals/5/old.mp4"])
    assert done == ["originals/5/old.mp4"] and not errors
    # run()'s loop over the other targets, exercised directly:
    for _n, c, b in targets[1:]:
        r2._delete_batch(c, b, ["originals/5/old.mp4"])
    assert "originals/5/old.mp4" not in nxt.objects
    # The manifest writer goes through put_bytes (every write store).
    write = r2._r2_manifest_writer(storage)
    key = write({"generated_at": "2026-10-29T00:00:00+00:00",
                 "mode": "live"})
    assert key in legacy.objects


def test_healthz_reports_storage_without_secrets(monkeypatch):
    for k, v in {**LEGACY_ENV, **NEXT_ENV}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=lambda: "copy", log=lambda m: None))
    st = storage.routing_status()
    assert st["mode"] == "copy" and st["next"] is True
    assert "next-secret" not in json.dumps(st)
    assert os.environ["S3_NEXT_BUCKET"] == "valmera-media-usw"
