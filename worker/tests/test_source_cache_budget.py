"""The executor cache may speed up renders, but may not strand the next one."""

import hashlib
import os
import time

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
