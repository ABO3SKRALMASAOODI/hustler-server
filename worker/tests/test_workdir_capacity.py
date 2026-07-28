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
