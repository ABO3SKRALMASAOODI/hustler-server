"""The executor cache may speed up renders, but may not strand the next one."""

import hashlib
import os
import time
from pathlib import Path

import config
import renderer


def _sized(path, size, mtime):
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def test_prune_removes_oversized_source_even_while_it_is_fresh(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 10)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 20)
    huge = tmp_path / "huge.mp4"
    _sized(huge, 11, 100)

    renderer._prune_source_cache(str(tmp_path))

    assert not huge.exists()


def test_prune_evicts_oldest_until_total_cache_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 10)
    oldest = tmp_path / "old.mp4"
    newest = tmp_path / "new.mp4"
    now = time.time()
    _sized(oldest, 6, now - 200)
    _sized(newest, 6, now - 100)

    renderer._prune_source_cache(str(tmp_path))

    assert not oldest.exists()
    assert newest.exists()


def test_prune_never_deletes_file_being_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 10)
    protected = tmp_path / "active.mp4"
    other = tmp_path / "other.mp4"
    now = time.time()
    _sized(protected, 8, now - 200)
    _sized(other, 8, now - 100)

    renderer._prune_source_cache(str(tmp_path), protect=str(protected))

    assert protected.exists()
    assert not other.exists()


def test_cache_hit_does_not_issue_a_storage_size_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 20)
    key = "originals/source.mp4"
    cache_dir = tmp_path / "srccache"
    cache_dir.mkdir()
    name = hashlib.sha256(key.encode()).hexdigest()[:32] + ".mp4"
    cached = cache_dir / name
    cached.write_bytes(b"already here")

    def unexpected(_key):
        raise AssertionError("cache hits must not make an R2 metadata call")

    monkeypatch.setattr(renderer.storage, "object_bytes", unexpected)

    assert renderer._cached_source(key) == str(cached)


def test_concurrent_prune_does_not_delete_a_download_before_its_atomic_rename(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 20)
    monkeypatch.setattr(renderer.storage, "object_bytes", lambda _key: 7)

    def download(_key, path):
        partial = Path(path + ".boto-transfer")
        partial.write_bytes(b"source!")
        renderer._prune_source_cache(str(partial.parent))
        os.replace(partial, path)

    monkeypatch.setattr(renderer.storage, "download_to", download)
    cached = renderer._cached_source("originals/concurrent.mp4")
    assert cached is not None
    assert Path(cached).read_bytes() == b"source!"


def test_prune_reaps_stale_transfer_parts_but_keeps_live_transfers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 10)
    now = time.time()
    stale, active, finished = (tmp_path / n for n in ("old.mp4.part-old", "new.mp4.part-live", "done.mp4"))
    _sized(stale, 7, now - 7 * 3600)
    _sized(active, 7, now)
    _sized(finished, 11, now - 10)
    renderer._prune_source_cache(str(tmp_path))
    assert not stale.exists()
    assert active.exists()
    assert not finished.exists()


def test_job_hardlink_survives_concurrent_prune_before_ffmpeg_open(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 20)
    key = "originals/source.mp4"
    cache_dir = tmp_path / "srccache"
    workdir = tmp_path / "render_101"
    cache_dir.mkdir()
    workdir.mkdir()
    name = hashlib.sha256(key.encode()).hexdigest()[:32] + ".mp4"
    cached = cache_dir / name
    cached.write_bytes(b"active!!")

    leased = renderer._job_cached_source(key, str(workdir))

    assert leased != str(cached)
    assert os.stat(leased).st_ino == os.stat(cached).st_ino

    # Model the other concurrent request applying aggregate pressure after
    # this request has its source but before ffmpeg opens it.
    other = cache_dir / "other.mp4"
    other.write_bytes(b"newest")
    now = time.time()
    os.utime(cached, (now - 100, now - 100))
    os.utime(other, (now, now))
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 8)
    renderer._prune_source_cache(str(cache_dir))

    assert not cached.exists()
    assert os.path.exists(leased)
    with open(leased, "rb") as handle:
        assert handle.read() == b"active!!"


def test_concurrent_cache_prune_does_not_remove_an_inflight_download(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 20)
    monkeypatch.setattr(renderer.storage, "object_bytes", lambda key: 8)
    def download(key, target):
        from pathlib import Path
        partial = Path(target)
        partial.write_bytes(b"complete")
        # A second render starts while storage is about to promote its file.
        renderer._prune_source_cache(str(partial.parent))
        assert partial.exists()
    monkeypatch.setattr(renderer.storage, "download_to", download)
    result = renderer._cached_source("originals/first-film.mp4")
    assert result is not None and open(result, "rb").read() == b"complete"
    assert not list((tmp_path/"srccache").glob("*.part*"))


def test_cache_accounts_for_live_partials_and_removes_abandoned_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_ITEM_BYTES", 20)
    monkeypatch.setattr(config, "SOURCE_CACHE_MAX_BYTES", 10)
    now = time.time()
    live, abandoned, old = (tmp_path/n for n in ("live.mp4.part1", "stale.mp4.part2", "old.mp4"))
    _sized(live, 8, now)
    _sized(abandoned, 8, now-7*3600)
    _sized(old, 6, now-100)
    renderer._prune_source_cache(str(tmp_path))
    assert live.exists() and not abandoned.exists() and not old.exists()
