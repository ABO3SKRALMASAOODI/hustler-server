"""P0 23 Nov 2026: the storage health probe and request threads built boto3
clients under different locks; right after a restart both ran boto3's lazy
imports at once and deadlocked on Python's import locks, and every upload
start, asset link and download in that worker hung. One process-wide build
lock, and the lazy imports done once on the importing thread."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_every_client_builder_shares_one_lock():
    import storage_health
    import storage_routing
    assert storage_health._CLIENT_LOCK is storage_routing.CLIENT_BUILD_LOCK
    src = (BACKEND / "storage.py").read_text()
    assert "_clients_lock = routing.CLIENT_BUILD_LOCK" in src
    assert "routing.warm_boto3()" in src


def test_warm_runs_boto3_lazy_imports_on_the_calling_thread():
    pytest.importorskip("boto3")
    code = textwrap.dedent("""
        import sys
        import storage_routing
        assert "boto3.s3.inject" not in sys.modules
        assert storage_routing.warm_boto3() is True
        assert "boto3.s3.inject" in sys.modules
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr


def test_concurrent_first_builds_finish_in_a_fresh_process():
    pytest.importorskip("boto3")
    code = textwrap.dedent("""
        import threading
        import storage_routing
        import storage_health
        cfg = {"endpoint": "https://s3.us-west-004.backblazeb2.com",
               "access_key_id": "k", "secret_access_key": "s",
               "region": "us-west-004", "addressing_style": "path"}
        def probe():
            storage_health._default_client(cfg)
        def request():
            import boto3
            with storage_routing.CLIENT_BUILD_LOCK:
                boto3.client("s3", region_name="us-east-1",
                             aws_access_key_id="k", aws_secret_access_key="s")
        ts = [threading.Thread(target=f) for f in (probe, request) * 4]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        assert not any(t.is_alive() for t in ts)
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                         capture_output=True, text=True, timeout=90)
    assert out.stdout.strip() == "ok", out.stderr


def test_a_lock_held_at_fork_is_free_in_the_child():
    """gunicorn preloads the app and forks: a master thread holding the
    build lock at the fork must not leave every worker waiting forever."""
    import os
    import threading
    if not hasattr(os, "fork"):
        pytest.skip("no fork")
    import storage_routing
    held, done = threading.Event(), threading.Event()

    def holder():
        with storage_routing.CLIENT_BUILD_LOCK:
            held.set()
            done.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    pid = os.fork()
    if pid == 0:
        ok = storage_routing.CLIENT_BUILD_LOCK.acquire(timeout=5)
        os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    done.set()
    t.join(5)
    assert os.WEXITSTATUS(status) == 0
