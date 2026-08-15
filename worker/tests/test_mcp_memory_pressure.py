"""MCP context reuse must yield before the 512-MiB dispatcher is OOM-killed."""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import mcp_exec  # noqa: E402


class _Session:
    def __init__(self, workdir, used):
        self.workdir = str(workdir)
        self.used = used
        self.lock = threading.Lock()


def test_pressure_evicts_fresh_idle_session_but_preserves_current(tmp_path):
    old = dict(mcp_exec._sessions)
    try:
        now = time.time()
        keep_dir = tmp_path / "keep"
        idle_dir = tmp_path / "idle"
        keep_dir.mkdir()
        idle_dir.mkdir()
        mcp_exec._sessions.clear()
        mcp_exec._sessions.update({
            1: _Session(keep_dir, now),
            2: _Session(idle_dir, now),
        })
        mcp_exec._drop_dead_sessions(now, keep=1, pressure=True)
        assert set(mcp_exec._sessions) == {1}
        assert keep_dir.exists()
        assert not idle_dir.exists()
    finally:
        mcp_exec._sessions.clear()
        mcp_exec._sessions.update(old)
