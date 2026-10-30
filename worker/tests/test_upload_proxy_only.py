"""A walled upload the browser cannot decode gets ONLY its playable proxy.

29 Oct 2026 (B_RELIABILITY.md): eight free accounts uploaded HEVC / 4K .mov
recordings their browser could not decode (MediaError 4). A walled upload is
never analysed before the account pays, so there was no server proxy and the
Studio said "This browser couldn't play the preview". backend/upload_proxy.py
now queues a proxy-only index job (payload.proxy_only, stamped for the render
box); the executor builds the same 540p proxy the analysis would build and
nothing else.

    cd worker && PYTHONPATH=.. python -m pytest tests/test_upload_proxy_only.py -q
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                  # noqa: E402
import db as dbx                                               # noqa: E402
import indexer                                                 # noqa: E402
import main                                                    # noqa: E402
import remote                                                  # noqa: E402
import storage                                                 # noqa: E402


class _Db:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kw):
        self.calls.append((getattr(fn, "__name__", "?"), args))
        return None


def _clip(path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not on PATH")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=1280x720:rate=30:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:v", "libx264", "-preset",
         "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def test_a_proxy_only_job_builds_the_proxy_and_nothing_else(monkeypatch,
                                                             tmp_path):
    src = tmp_path / "upload.mov"
    _clip(src)
    uploads = []
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dst: shutil.copyfile(src, dst))
    monkeypatch.setattr(storage, "upload_file",
                        lambda path, key, ctype=None: uploads.append(
                            (key, os.path.getsize(path))))
    db = _Db()
    asset = {"id": 41, "kind": "original", "storage_key": "originals/7/a.mov",
             "bytes": os.path.getsize(src), "sha256": None, "meta": {}}
    monkeypatch.setattr(dbx, "get_asset", lambda conn, aid: asset)
    db.run = (lambda fn, *a, **k: asset if fn is dbx.get_asset
              else _Db.run(db, fn, *a, **k))
    job = {"id": 900, "project_id": 7, "user_id": 3, "type": "index",
           "payload": {"asset_id": 41, "proxy_only": True,
                       "execution_provider": "box"}}

    result = indexer.run_index_job(db, job)

    assert result["proxy_only"] is True
    sha = result["sha256"]
    assert result["proxy_key"] == f"proxies/7/{sha}.mp4"
    assert [k for k, _ in uploads] == [f"proxies/7/{sha}.mp4"]
    names = [n for n, _ in db.calls]
    # The asset learns its probe and sha (the Studio matches the proxy to the
    # active original by sha) and gets exactly one proxy row.
    assert "update_asset_probe" in names
    inserts = [a for n, a in db.calls if n == "insert_asset"]
    assert len(inserts) == 1 and inserts[0][1] == "proxy"
    # No analysis: no index row, no First cut, no chat, no model.
    for forbidden in ("upsert_index", "add_message", "update_asset_meta",
                      "record_client_event"):
        assert forbidden not in names, forbidden
    assert not os.path.exists(os.path.join(config.TMP_DIR, "proxy_900"))


def _audio_only(path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not on PATH")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:a", "aac", str(path)],
        check=True)


def test_an_audio_only_source_is_skipped_quietly_never_failed(monkeypatch,
                                                              tmp_path):
    """SWEEP_UPLOAD #43 (30 Oct 2026): an audio-only .mp4 uploaded as the
    video (sound, no picture) used to fail the proxy-only job with "No video
    stream found in file", a filed box failure for a file the browser plays
    as it is. The job now ends done with skipped "audio_only", uploads
    nothing, and marks the asset so the backend never queues another."""
    src = tmp_path / "podcast.mp4"
    _audio_only(src)
    uploads = []
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dst: shutil.copyfile(src, dst))
    monkeypatch.setattr(storage, "upload_file",
                        lambda path, key, ctype=None: uploads.append(key))
    db = _Db()
    asset = {"id": 42, "kind": "original", "storage_key": "originals/7/p.mp4",
             "bytes": os.path.getsize(src), "sha256": None, "meta": {}}
    db.run = (lambda fn, *a, **k: asset if fn is dbx.get_asset
              else _Db.run(db, fn, *a, **k))
    job = {"id": 901, "project_id": 7, "user_id": 3, "type": "index",
           "payload": {"asset_id": 42, "proxy_only": True,
                       "execution_provider": "box"}}

    result = indexer.run_index_job(db, job)

    assert result == {"proxy_only": True, "skipped": "audio_only"}
    assert uploads == []
    assert ("update_asset_meta", (42, {"no_video": True})) in db.calls
    names = [n for n, _ in db.calls]
    for forbidden in ("insert_asset", "update_asset_probe", "upsert_index",
                      "add_message"):
        assert forbidden not in names, forbidden
    assert not os.path.exists(os.path.join(config.TMP_DIR, "proxy_901"))
    # Marked once: the next job (should one ever be queued) never downloads.
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dst: pytest.fail("downloaded again"))
    asset["meta"] = {"no_video": True}
    assert indexer.run_index_job(db, job) == {"proxy_only": True,
                                              "skipped": "audio_only"}


def test_any_other_probe_failure_still_fails_the_job(monkeypatch, tmp_path):
    """Only "no video stream" is a skip: a broken file still fails as it
    always did (sensitivity)."""
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all" * 100)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dst: shutil.copyfile(bad, dst))
    db = _Db()
    asset = {"id": 43, "kind": "original", "storage_key": "originals/7/b.mp4",
             "bytes": os.path.getsize(bad), "sha256": None, "meta": {}}
    with pytest.raises(Exception) as err:
        indexer.run_proxy_only_job(
            db, {"id": 902, "project_id": 7, "payload": {"proxy_only": True}},
            asset)
    assert "no video stream" not in str(err.value).lower()
    assert "update_asset_meta" not in [n for n, _ in db.calls]


def test_an_existing_proxy_is_not_rebuilt(monkeypatch):
    db = _Db()
    monkeypatch.setattr(dbx, "asset_by_key",
                        lambda conn, pid, key: {"id": 1, "storage_key": key})
    db.run = lambda fn, *a, **k: fn(None, *a) if not a else None
    out = indexer.run_proxy_only_job(
        db, {"id": 1, "project_id": 7, "payload": {"proxy_only": True}},
        {"id": 41, "kind": "original", "sha256": "abc",
         "storage_key": "originals/7/a.mov"})
    assert out == {"proxy_only": True, "skipped": "proxy exists",
                   "sha256": "abc"}


def test_a_failed_proxy_job_never_writes_to_the_chat(monkeypatch):
    posted = []
    db = _Db()
    db.run = lambda fn, *a, **k: posted.append(fn)
    main._notify_failure(db, {"id": 5, "type": "index", "project_id": 7,
                              "payload": {"asset_id": 41,
                                          "proxy_only": True}},
                         RuntimeError("decoder died"))
    assert posted == []
    assert main._proxy_only({"type": "index",
                             "payload": {"proxy_only": True}})
    assert not main._proxy_only({"type": "index", "payload": {}})
    assert not main._proxy_only({"type": "preview",
                                 "payload": {"proxy_only": True}})


def test_the_reaper_repair_and_death_notes_skip_proxy_jobs():
    import inspect
    sql = inspect.getsource(dbx.unnotified_terminal_failures)
    assert "proxy_only" in sql
    reaper = inspect.getsource(main.reaper)
    assert "_proxy_only(row)" in reaper


def test_the_box_stamp_is_final_and_never_cloudflare(monkeypatch):
    """The backend stamps execution_provider "box": provider selection reads
    the stamp first, so neither a box stage switched off nor Cloudflare being
    enabled can move it (the owner: Cloudflare is never a fallback)."""
    monkeypatch.setattr(remote.box_routing, "selected", lambda job: False)
    monkeypatch.setattr(remote, "_cloudflare_selected", lambda job: True)
    job = {"id": 9, "type": "index", "user_id": 3,
           "payload": {"asset_id": 41, "proxy_only": True,
                       "execution_provider": "box"}}
    assert remote.desired_execution_provider(job) == "box"
