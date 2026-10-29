"""The R2 -> next-store copier (storage_migrate.py): verified, resumable,
never overwriting what the app wrote, never outliving a deleted source."""

import argparse
import hashlib
import json

import pytest

import storage_migrate as sm
from s3_fake import FakeHttp, FakeS3

MIB = 1024 * 1024


def _store(name, fake):
    cfg = {"bucket": f"{name}-bucket", "host": f"{name}.example",
           "endpoint": f"https://{name}.example", "region": "auto",
           "access_key_id": "id", "secret_access_key": "secret",
           "addressing_style": "path", "public_endpoint": "x"}
    return sm.Store(name, cfg, client=fake)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "SINGLE_MAX", 1 * MIB)
    monkeypatch.setattr(sm, "PART_MIN", 1 * MIB)
    r2, b2 = FakeS3("legacy"), FakeS3("next")
    src, dst = _store("legacy", r2), _store("next", b2)
    state = sm.State(str(tmp_path / "state.sqlite"))
    return r2, b2, src, dst, state


def _args(**kw):
    base = dict(prefix="", reverse=False, workers=4, max_mbps=0, limit=0,
                max_attempts=5, no_verify=False, overwrite=False, all=False,
                sample=0, grace_min=0, delete_orphans=False, mode=None,
                yes=False, force=False, note="", health=[])
    base.update(kw)
    return argparse.Namespace(**base)


def test_inventory_copy_verify_end_to_end(env):
    r2, b2, src, dst, state = env
    big = bytes(range(256)) * (10 * 4096)            # 10 MiB: multipart
    r2.put_object(Bucket="b", Key="originals/1/a.mp4", Body=big,
                  ContentType="video/mp4", Metadata={"x": "1"})
    r2.put_object(Bucket="b", Key="thumbs/1/s/shot_0.jpg", Body=b"jpg",
                  ContentType="image/jpeg", ContentDisposition="inline")
    r2.put_object(Bucket="b", Key="audio/1/empty.wav", Body=b"")
    inv = sm.cmd_inventory(_args(), {}, state, src, dst)
    assert inv["source_keys"] == 3 and inv["destination_keys"] == 0
    assert inv["by_prefix"]["originals"]["keys"] == 1
    out = sm.cmd_copy(_args(), {}, state, src, dst)
    assert out["copied"] == {"verified": 3}
    got = b2.objects["originals/1/a.mp4"]
    assert got["data"] == big and got["ct"] == "video/mp4"
    assert got["meta"]["x"] == "1"
    # The SHA-256 lives in the state DB for every object, and on single-part
    # copies in the object's own metadata too.
    shas = {r["key"]: r["sha256"] for r in state.rows()}
    assert shas["originals/1/a.mp4"] == hashlib.sha256(big).hexdigest()
    assert b2.objects["thumbs/1/s/shot_0.jpg"]["meta"][sm.SHA_META] == \
        hashlib.sha256(b"jpg").hexdigest()
    assert b2.objects["thumbs/1/s/shot_0.jpg"]["extra"][
        "ContentDisposition"] == "inline"
    assert b2.objects["audio/1/empty.wav"]["data"] == b""
    # Ranged reads, one per part: the 10 MiB object went up in 10 parts.
    assert sum(1 for c in b2.calls if c[0] == "upload_part") == 10
    ready = sm.readiness(state)
    assert ready["ready_for_switch"] is True, ready
    # A second pass has nothing to do (resumable, idempotent).
    assert sm.cmd_copy(_args(), {}, state, src, dst)["copied"] == {}


def test_objects_the_app_wrote_are_never_overwritten(env):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="media/1/r.mp4", Body=b"old-bytes")
    b2.put_object(Bucket="b", Key="media/1/r.mp4", Body=b"newer")  # app
    b2.put_object(Bucket="b", Key="media/1/same.mp4", Body=b"BBBB")
    r2.put_object(Bucket="b", Key="media/1/same.mp4", Body=b"AAAA")
    b2.objects["media/1/same.mp4"]["mtime"] = r2.objects[
        "media/1/same.mp4"]["mtime"]
    sm.cmd_inventory(_args(), {}, state, src, dst)
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert b2.objects["media/1/r.mp4"]["data"] == b"newer"
    rows = {r["key"]: r["status"] for r in state.rows()}
    assert rows["media/1/r.mp4"] == "conflict"
    assert rows["media/1/same.mp4"] == "present"
    # verify reads both: same size, different bytes -> conflict, kept.
    sm.cmd_verify(_args(), {}, state, src, dst)
    rows = {r["key"]: r["status"] for r in state.rows()}
    assert rows["media/1/same.mp4"] == "conflict"
    assert b2.objects["media/1/same.mp4"]["data"] == b"BBBB"
    assert sm.readiness(state)["ready_for_switch"] is False
    # In copy mode R2 is authoritative: --overwrite settles conflicts.
    sm.cmd_copy(_args(overwrite=True), {}, state, src, dst,
                mode_reader=lambda: "copy")
    assert b2.objects["media/1/same.mp4"]["data"] == b"AAAA"
    assert b2.objects["media/1/r.mp4"]["data"] == b"old-bytes"
    with pytest.raises(SystemExit):
        sm.cmd_copy(_args(overwrite=True), {}, state, src, dst,
                    mode_reader=lambda: "next")


def test_a_corrupt_destination_copy_is_deleted_and_retried(env):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="media/2/x.mp4", Body=b"payload" * 100)
    sm.cmd_inventory(_args(), {}, state, src, dst)
    b2.corrupt_writes = True
    sm.cmd_copy(_args(), {}, state, src, dst)
    row = state.rows()[0]
    assert row["status"] == "mismatch"
    assert "media/2/x.mp4" not in b2.objects     # never readable next-first
    b2.corrupt_writes = False
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert state.rows()[0]["status"] == "verified"


def test_source_read_corruption_is_caught_by_the_etag(env, monkeypatch):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="media/3/x.mp4", Body=b"abcdef")
    r2.objects["media/3/x.mp4"]["etag"] = "0" * 32       # not the bytes' MD5
    sm.cmd_inventory(_args(), {}, state, src, dst)
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert state.rows()[0]["status"] == "failed"
    assert "media/3/x.mp4" not in b2.objects


def test_a_source_deleted_mid_copy_leaves_no_copy(env, monkeypatch):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="originals/4/gone.mp4", Body=b"x" * 50)
    sm.cmd_inventory(_args(), {}, state, src, dst)
    original_upload = b2.put_object

    def put_then_source_deleted(**kw):
        out = original_upload(**kw)
        r2.objects.pop("originals/4/gone.mp4", None)    # retention, mid-copy
        return out
    b2.put_object = put_then_source_deleted
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert state.rows()[0]["status"] == "source_gone"
    assert "originals/4/gone.mp4" not in b2.objects


def test_inventory_marks_deleted_sources_and_changed_objects(env):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="a", Body=b"1")
    r2.put_object(Bucket="b", Key="b", Body=b"2")
    sm.cmd_inventory(_args(), {}, state, src, dst)
    sm.cmd_copy(_args(), {}, state, src, dst)
    r2.objects.pop("a")
    r2.put_object(Bucket="b", Key="b", Body=b"22")
    sm.cmd_inventory(_args(), {}, state, src, dst)
    rows = {r["key"]: r["status"] for r in state.rows()}
    assert rows == {"a": "source_gone", "b": "pending"}
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert b2.objects["b"]["data"] == b"22"


def test_reconcile_deletes_orphans_only_in_copy_modes(env):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="kept", Body=b"1")
    b2.put_object(Bucket="b", Key="kept", Body=b"1")
    b2.put_object(Bucket="b", Key="orphan", Body=b"2")
    out = sm.cmd_reconcile(_args(), {}, state, src, dst)
    assert out["destination_only"] == 1 and out["deleted"] == 0
    with pytest.raises(SystemExit):
        sm.cmd_reconcile(_args(delete_orphans=True), {}, state, src, dst,
                         mode_reader=lambda: "next")
    assert "orphan" in b2.objects
    out = sm.cmd_reconcile(_args(delete_orphans=True), {}, state, src, dst,
                           mode_reader=lambda: "copy")
    assert out["deleted"] == 1 and "orphan" not in b2.objects
    assert "kept" in b2.objects


def test_mode_switch_guards(env):
    r2, b2, src, dst, state = env
    written = []
    r2.put_object(Bucket="b", Key="a", Body=b"1")
    sm.cmd_inventory(_args(), {}, state, src, dst)
    ok_probe = lambda e: [("storage_probe_dispatcher", True, "{}")]  # noqa
    # Not copied yet: the switch is refused.
    with pytest.raises(SystemExit):
        sm.cmd_mode(_args(mode="next", yes=True), {}, state,
                    reader=lambda e: "copy", writer=written.append,
                    probes=ok_probe)
    sm.cmd_copy(_args(), {}, state, src, dst)
    # A process that does not report the next store blocks it too ...
    with pytest.raises(SystemExit):
        sm.cmd_mode(_args(mode="next", yes=True, health=["u"]), {}, state,
                    reader=lambda e: "dual_write", writer=written.append,
                    health=lambda urls: [("u", False, "{}")],
                    probes=ok_probe)
    # ... and so does the dispatcher's probe row, or no evidence at all.
    for probes in (lambda e: [("storage_probe_dispatcher", False, "{}")],
                   lambda e: []):
        with pytest.raises(SystemExit):
            sm.cmd_mode(_args(mode="next", yes=True), {}, state,
                        reader=lambda e: "dual_write",
                        writer=written.append, probes=probes)
    # copy and dual_write need no evidence: R2 still gets every write.
    sm.cmd_mode(_args(mode="dual_write", yes=True), {}, state,
                reader=lambda e: "copy", writer=written.append,
                probes=lambda e: [])
    sm.cmd_mode(_args(mode="next", yes=True, health=["u"]), {}, state,
                reader=lambda e: "dual_write", writer=written.append,
                health=lambda urls: [("u", True, "{}")], probes=ok_probe)
    doc = json.loads(written[-1])
    assert doc["mode"] == "next" and doc["previous"] == "dual_write"
    # next -> legacy would hide new objects: refused; rollback is allowed.
    with pytest.raises(SystemExit):
        sm.cmd_mode(_args(mode="legacy", yes=True), {}, state,
                    reader=lambda e: "next", writer=written.append)
    sm.cmd_mode(_args(mode="rollback", yes=True), {}, state,
                reader=lambda e: "next", writer=written.append,
                probes=ok_probe)
    assert json.loads(written[-1])["mode"] == "rollback"
    # Without --yes nothing is written.
    n = len(written)
    sm.cmd_mode(_args(mode="copy"), {}, state, reader=lambda e: "legacy",
                writer=written.append)
    assert len(written) == n


def test_reverse_copy_brings_next_only_objects_home(env):
    r2, b2, src, dst, state = env
    b2.put_object(Bucket="b", Key="originals/9/after-switch.mp4", Body=b"z")
    # --reverse: next is the source, legacy the destination.
    sm.cmd_inventory(_args(), {}, state, dst, src)
    sm.cmd_copy(_args(), {}, state, dst, src)
    assert r2.objects["originals/9/after-switch.mp4"]["data"] == b"z"


def test_limiter_spaces_bytes():
    clock = [0.0]
    slept = []
    lim = sm.Limiter(8)                               # 8 Mbit/s = 1 MB/s
    lim.next_free = 0.0
    import time as _t
    real_mono, real_sleep = _t.monotonic, _t.sleep
    try:
        sm.time.monotonic = lambda: clock[0]
        sm.time.sleep = lambda s: slept.append(s)
        lim.take(1_000_000)
        lim.take(1_000_000)
    finally:
        sm.time.monotonic, sm.time.sleep = real_mono, real_sleep
    assert slept == [pytest.approx(1.0)]
    assert sm.part_size(100 * 1024 ** 3) >= 100 * 1024 ** 3 / sm.MAX_PARTS


def test_smoke_exercises_every_product_call(env):
    r2, b2, src, dst, state = env
    b2.cors = sm.cors_rules()
    ok, results = sm.cmd_smoke(_args(), {}, store=dst, http=FakeHttp(b2))
    assert ok, results
    names = [r[0] for r in results]
    assert "multipart (presigned parts)" in names
    assert "CORS preflight from valmera.io" in names
    assert not [k for k in b2.objects if k.startswith("_smoke/")]
    # Without CORS the browser path fails loudly.
    b2.cors = None
    ok, results = sm.cmd_smoke(_args(), {}, store=dst, http=FakeHttp(b2))
    assert not ok
    assert [r for r in results if not r[1]][0][0].startswith("CORS")


def test_setup_bucket_sets_cors_and_b2_lifecycle(monkeypatch):
    b2 = FakeS3("next")
    env = {"S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
           "S3_NEXT_ACCESS_KEY_ID": "app", "S3_NEXT_SECRET_ACCESS_KEY": "s",
           "S3_NEXT_BUCKET": "valmera-media-usw",
           "S3_SETUP_ACCESS_KEY_ID": "setup",
           "S3_SETUP_SECRET_ACCESS_KEY": "t"}
    seen = []

    def http(method, url, data=None, headers=None, timeout=60):
        seen.append((method, url, json.loads(data) if data else None,
                     (headers or {}).get("Authorization", "")))
        if url.endswith("b2_authorize_account"):
            return 200, {}, json.dumps({"accountId": "acc",
                                        "apiUrl": "https://api004",
                                        "authorizationToken": "tok"}).encode()
        if url.endswith("b2_list_buckets"):
            return 200, {}, json.dumps({"buckets": [
                {"bucketId": "bid"}]}).encode()
        if url.endswith("b2_update_bucket"):
            body = json.loads(data)
            return 200, {}, json.dumps({
                "lifecycleRules": body["lifecycleRules"],
                "bucketType": "allPrivate"}).encode()
        return 404, {}, b""
    out = sm.cmd_setup_bucket(_args(), env, client=b2, http=http)
    assert out["ok"] is True
    rule = b2.cors["CORSRules"][0]
    assert "ETag" in rule["ExposeHeaders"] and "PUT" in rule[
        "AllowedMethods"]
    assert "https://valmera.io" in rule["AllowedOrigins"]
    update = [s for s in seen if s[1].endswith("b2_update_bucket")][0][2]
    assert update["bucketId"] == "bid"
    assert update["lifecycleRules"][0][
        "daysFromStartingToCancelingUnfinishedLargeFiles"] == 7
    assert update["lifecycleRules"][0]["daysFromHidingToDeleting"] == 1
    # The setup key, not the app key, authorized it.
    assert seen[0][3].startswith("Basic ")
    import base64
    assert base64.b64decode(seen[0][3][6:]).decode() == "setup:t"
    # Nothing secret in what it prints.
    assert "t" not in [out.get("bucket"), out.get("host")]


def test_load_env_file_never_needs_the_environment(tmp_path):
    p = tmp_path / "e.env"
    p.write_text("# comment\nS3_BUCKET=valmera-media\n"
                 "export S3_NEXT_BUCKET='valmera-media-usw'\n\nBAD LINE\n")
    env = sm.load_env(str(p), base={})
    assert env == {"S3_BUCKET": "valmera-media",
                   "S3_NEXT_BUCKET": "valmera-media-usw"}


def test_objects_that_changed_size_since_the_inventory_still_copy(env):
    r2, b2, src, dst, state = env
    r2.put_object(Bucket="b", Key="grew", Body=b"g")
    r2.put_object(Bucket="b", Key="shrank", Body=b"s" * (3 * MIB))
    sm.cmd_inventory(_args(), {}, state, src, dst)
    r2.put_object(Bucket="b", Key="grew", Body=b"G" * (3 * MIB))
    r2.put_object(Bucket="b", Key="shrank", Body=b"tiny")
    sm.cmd_copy(_args(), {}, state, src, dst)
    assert b2.objects["grew"]["data"] == b"G" * (3 * MIB)
    assert b2.objects["shrank"]["data"] == b"tiny"
    assert {r["status"] for r in state.rows()} == {"verified"}
    # Small objects cost one GET, no HEAD, on the source.
    heads = [c for c in r2.calls if c == ("head_object", "shrank")]
    assert len(heads) <= 2
