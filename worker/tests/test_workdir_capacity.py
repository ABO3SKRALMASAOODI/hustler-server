"""A job that cannot fit must SAY so, not be OOM-killed.

On Cloud Run `/tmp` is an in-memory filesystem, so every byte a job stages
there is RAM counted against `--memory`. Exceeding it does not raise: the
kernel kills the container, the HTTP request dies with no body, and the
dispatcher writes "Worker died and retries are exhausted" — the least
actionable error we produce, for the one failure an operator could fix in a
minute by raising a flag.

These pin the guard that turns that into a sentence.
"""

import pytest

import config
import storage


def test_a_source_that_fits_is_not_refused():
    storage.check_workdir_capacity(1024, path=".", headroom=2.0)


def test_a_source_bigger_than_the_scratch_space_is_refused(monkeypatch):
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: 4 * 1024 ** 3)
    with pytest.raises(storage.WorkdirTooSmall):
        storage.check_workdir_capacity(9 * 1024 ** 3)


def test_the_message_names_the_flag_that_fixes_it(monkeypatch):
    """An error nobody can act on is barely better than no error. This one has
    to carry the remedy, because the person reading it is looking at an admin
    job list and nothing else."""
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: 1024 ** 3)
    with pytest.raises(storage.WorkdirTooSmall) as e:
        storage.check_workdir_capacity(20 * 1024 ** 3)
    msg = str(e.value)
    assert "--memory" in msg
    assert "WORKER_TMP_DIR" in msg
    assert "DEPLOY_EXECUTOR.md" in msg


def test_headroom_covers_the_artifacts_written_beside_the_source(monkeypatch):
    """A render stages the source AND writes its output next to it; an index
    writes a proxy and a wav. Checking the bare source size would pass a job
    that then dies halfway through writing what it produces."""
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: 10 * 1024 ** 3)
    # 8 GB of source needs more than 8 GB of room.
    with pytest.raises(storage.WorkdirTooSmall):
        storage.check_workdir_capacity(8 * 1024 ** 3)
    assert config.WORKDIR_HEADROOM > 1.0


def test_an_unmeasurable_filesystem_never_blocks_a_job(monkeypatch):
    """Fail OPEN. Refusing work because we could not read a disk stat would
    take the product down to prevent a maybe — the round-53 mistake."""
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: None)
    storage.check_workdir_capacity(500 * 1024 ** 3)


def test_an_unknown_source_size_never_blocks_a_job(monkeypatch):
    """head_object can fail for reasons that have nothing to do with capacity."""
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: 1)
    storage.check_workdir_capacity(None)
    storage.check_workdir_capacity(0)


# ── what actually backs the workdir ─────────────────────────────────────────
# The deploy doc asserted Cloud Run's /tmp is always an in-memory tmpfs. The
# live executor reported a 755 GB filesystem there, which no 32 GiB instance
# could back with memory. Both worlds are real and the correct bound differs:
# on tmpfs the memory limit binds (and is STRICTER than statvfs, because
# ffmpeg's buffers spend the same budget); on a real disk memory is irrelevant
# and bounding by it would refuse work that fits fine.

class _FakeUsage:
    def __init__(self, free):
        self.free, self.total, self.used = free, free, 0


def test_a_memory_backed_workdir_is_bounded_by_the_memory_limit(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.setattr(storage, "workdir_fstype", lambda path=None: "tmpfs")
    monkeypatch.setattr(storage, "_cgroup_memory_available", lambda: 20 * gb)
    monkeypatch.setattr(storage.shutil, "disk_usage",
                        lambda p: _FakeUsage(700 * gb))
    # statvfs says 700 GB, but every byte is RAM and only 20 GB of RAM is left.
    assert storage.free_workdir_bytes() == 20 * gb


def test_a_disk_backed_workdir_ignores_the_memory_limit(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.setattr(storage, "workdir_fstype", lambda path=None: "overlay")
    monkeypatch.setattr(storage, "_cgroup_memory_available", lambda: 20 * gb)
    monkeypatch.setattr(storage.shutil, "disk_usage",
                        lambda p: _FakeUsage(700 * gb))
    # Real disk: staging 100 GB costs no RAM, so the memory limit must not
    # shrink the budget or a 16 GB upload gets refused on a healthy instance.
    assert storage.free_workdir_bytes() == 700 * gb


def test_no_cgroup_falls_back_to_the_filesystem(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.setattr(storage, "workdir_fstype", lambda path=None: "tmpfs")
    monkeypatch.setattr(storage, "_cgroup_memory_available", lambda: None)
    monkeypatch.setattr(storage.shutil, "disk_usage",
                        lambda p: _FakeUsage(12 * gb))
    assert storage.free_workdir_bytes() == 12 * gb


def test_fstype_picks_the_longest_matching_mount(tmp_path, monkeypatch):
    """'/' matches every path, so a naive scan would report the root's type for
    a workdir that is really its own mount."""
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "overlay / overlay rw 0 0\n"
        "tmpfs /tmp tmpfs rw 0 0\n")
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/proc/mounts":
            return real_open(mounts, *a, **kw)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(storage.os.path, "realpath", lambda p: "/tmp/valmera")
    assert storage.workdir_fstype("/tmp/valmera") == "tmpfs"
