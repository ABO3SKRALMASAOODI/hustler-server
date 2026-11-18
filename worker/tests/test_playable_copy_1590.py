"""Ticket #1590: H.264 + AAC in MPEG-TS (named .mp4) is unplayable in every
browser (media error 4) although its streams are what browsers decode. The
proxy-only job now stores a stream-copied MP4 (`playable_copy`) before the
540p encode, so the Studio plays it in about a minute instead of after the
whole encode (13 min on the customer's 2680 s 1080p60 file).

    cd worker && PYTHONPATH=.. python -m pytest tests/test_playable_copy_1590.py -q
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                  # noqa: E402
import db as dbx                                               # noqa: E402
import indexer                                                 # noqa: E402
import media                                                   # noqa: E402
import storage                                                 # noqa: E402


def _make(path, fmt, vcodec="libx264", extra=()):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not on PATH")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=640x360:rate=30:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:v", vcodec, *extra,
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-f", fmt,
         str(path)], check=True)


def _format(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                          "-show_format", str(path)], capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)["format"]["format_name"]


def test_ts_named_mp4_is_remuxed_to_a_real_mp4(tmp_path):
    src = tmp_path / "Yasuo SOLO Q.mp4"          # TS bytes, .mp4 name
    _make(src, "mpegts", extra=("-preset", "ultrafast"))
    assert media.playable_by_remux(str(src)) is True
    dst = tmp_path / "play.mp4"
    media.remux_playable(str(src), str(dst), duration=2)
    assert "mp4" in _format(dst)
    p = media.probe(str(dst))
    assert p["has_audio"] and p["width"] == 640 and p["duration"] > 1.5


def test_a_real_mp4_or_an_undecodable_codec_takes_the_proxy_path(tmp_path):
    mp4 = tmp_path / "a.mp4"
    _make(mp4, "mp4", extra=("-preset", "ultrafast"))
    assert media.playable_by_remux(str(mp4)) is False
    ts_mpeg2 = tmp_path / "b.ts"
    _make(ts_mpeg2, "mpegts", vcodec="mpeg2video")
    assert media.playable_by_remux(str(ts_mpeg2)) is False


def test_proxy_only_job_stores_the_copy_before_the_540p_proxy(monkeypatch,
                                                              tmp_path):
    src = tmp_path / "upload.mp4"
    _make(src, "mpegts", extra=("-preset", "ultrafast"))
    uploads, inserts = [], []
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(config, "index_source_should_stream",
                        lambda kind, n: False)
    monkeypatch.setattr(storage, "download_to",
                        lambda key, dst: shutil.copyfile(src, dst))
    monkeypatch.setattr(storage, "upload_file",
                        lambda path, key, ctype=None: uploads.append(key))

    class Db:
        def run(self, fn, *a, **k):
            if fn is dbx.insert_asset:
                inserts.append((a, k))
                return len(inserts)
            return None

    asset = {"id": 41, "kind": "original", "storage_key": "originals/7/a.mp4",
             "bytes": os.path.getsize(src), "sha256": None, "meta": {}}
    job = {"id": 901, "project_id": 7, "user_id": 3, "type": "index",
           "payload": {"asset_id": 41, "proxy_only": True}}

    result = indexer.run_proxy_only_job(Db(), job, asset)

    sha = result["sha256"]
    assert uploads == [f"proxies/7/{sha}.play.mp4", f"proxies/7/{sha}.mp4"]
    assert "playable_copy_s" in result["timings"]
    copy_args, copy_kw = inserts[0]
    assert copy_args[1:3] == ("proxy", f"proxies/7/{sha}.play.mp4")
    assert copy_kw["sha256"] == sha
    assert copy_kw["meta"] == {"playable_copy": True}
    # The 540p proxy row comes after it (newer id: the Studio swaps to it).
    assert inserts[1][0][1:3] == ("proxy", f"proxies/7/{sha}.mp4")
    assert not (inserts[1][1].get("meta") or {}).get("playable_copy")


def test_a_playable_copy_is_never_a_proxy_donor():
    import inspect
    sql = inspect.getsource(dbx.any_asset_by_sha)
    assert "playable_copy" in sql


def test_full_index_path_makes_the_copy_before_its_lanes():
    """Verify of 2e86004e failed: a paying or trial upload takes the full
    index (run_index_job), which made no copy, so a TS stayed unplayable
    for the whole 540p encode. The copy is made on the job thread before
    the cache lookup and the lanes (the 540p proxy row is inserted later,
    so it is newer and replaces the copy)."""
    import inspect
    body = inspect.getsource(indexer.run_index_job)
    at = body.find("_ensure_playable_copy(")
    assert at != -1
    assert at < body.find("dbx.get_index_by_sha")
    assert at < body.find("def _picture_lane")


def test_streamed_scratch_reserve_is_counted(monkeypatch, tmp_path):
    src = tmp_path / "upload.mp4"
    _make(src, "mpegts", extra=("-preset", "ultrafast"))
    made = []
    monkeypatch.setattr(media, "remux_playable",
                        lambda *a, **k: made.append(a))

    class Db:
        def run(self, fn, *a, **k):
            return None

    free = shutil.disk_usage(tmp_path).free
    stored = indexer._ensure_playable_copy(
        Db(), 7, "s" * 64, "proxies/7/x.mp4", str(src), {"duration": 2},
        str(tmp_path), os.path.getsize(src), reserve_bytes=free)
    assert stored is False and made == []
