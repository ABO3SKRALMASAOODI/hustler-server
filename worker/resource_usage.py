"""Dependency-free cgroup telemetry for executor cost right-sizing.

Process RSS misses ffmpeg, Chromium, Whisper, and other child processes. The
container cgroup is the billing and OOM boundary, so resource decisions must
be based on it. Every reader is best-effort: telemetry must never make a render
fail on a developer machine or a different cgroup layout.
"""

import os
import threading


_MIB = 1024 * 1024


def _read_int(path):
    try:
        with open(path) as f:
            raw = f.read().strip()
        if not raw or raw == "max":
            return None
        value = int(raw)
        # cgroup v1 uses a huge sentinel for an unlimited memory controller.
        return None if value >= 1 << 60 else value
    except (OSError, TypeError, ValueError):
        return None


def _read_kv(path):
    values = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        values[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return values


def _mib(value):
    return round(value / _MIB, 2) if value is not None else None


def snapshot(root="/sys/fs/cgroup"):
    """Return one JSON-safe container snapshot for cgroup v2 or v1."""
    current = _read_int(os.path.join(root, "memory.current"))
    peak = _read_int(os.path.join(root, "memory.peak"))
    limit = _read_int(os.path.join(root, "memory.max"))
    cpu_usec = _read_kv(os.path.join(root, "cpu.stat")).get("usage_usec")
    pids_current = _read_int(os.path.join(root, "pids.current"))
    pids_peak = _read_int(os.path.join(root, "pids.peak"))

    if current is None:
        memory_root = os.path.join(root, "memory")
        current = _read_int(os.path.join(
            memory_root, "memory.usage_in_bytes"))
        peak = _read_int(os.path.join(
            memory_root, "memory.max_usage_in_bytes"))
        limit = _read_int(os.path.join(
            memory_root, "memory.limit_in_bytes"))
    if cpu_usec is None:
        cpu_ns = _read_int(os.path.join(
            root, "cpuacct", "cpuacct.usage"))
        cpu_usec = cpu_ns / 1000 if cpu_ns is not None else None

    values = {
        "container_memory_current_mib": _mib(current),
        # This is the peak for the reused container's life, not a claim that
        # one input alone consumed the full amount.
        "container_memory_peak_mib": _mib(peak),
        "container_memory_limit_mib": _mib(limit),
        "container_cpu_usage_s": (
            round(cpu_usec / 1_000_000, 3)
            if cpu_usec is not None else None),
        "container_pids_current": pids_current,
        "container_pids_peak": pids_peak,
    }
    return {key: value for key, value in values.items() if value is not None}


def usage_since(start, root="/sys/fs/cgroup"):
    """Return the end snapshot plus CPU used since ``start`` when available."""
    end = snapshot(root)
    start_cpu = (start or {}).get("container_cpu_usage_s")
    end_cpu = end.pop("container_cpu_usage_s", None)
    if start_cpu is not None and end_cpu is not None:
        end["container_cpu_s"] = round(max(0.0, end_cpu - start_cpu), 3)
    return end


class MemorySampler:
    """Sample cgroup-wide working memory while child processes are alive.

    Some Modal hosts expose ``memory.max`` but not the optional ``memory.peak``
    file. Reading only after ffmpeg exits then reports Python's quiet RSS and
    systematically under-sizes the lane. A cgroup file read twenty times a
    second is negligible beside media work and captures short child-process
    spikes even while the executor's cores are busy.
    """

    def __init__(self, root="/sys/fs/cgroup", interval_s=0.05):
        self._root = root
        self._interval_s = max(0.01, float(interval_s))
        self._done = threading.Event()
        self._peak_bytes = None
        self._finished = False
        self._finished_mib = None
        self._sample()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="cgroup-memory-sampler")
        self._thread.start()

    def _current(self):
        value = _read_int(os.path.join(self._root, "memory.current"))
        if value is None:
            value = _read_int(os.path.join(
                self._root, "memory", "memory.usage_in_bytes"))
        return value

    def _sample(self):
        value = self._current()
        if value is not None:
            self._peak_bytes = max(self._peak_bytes or 0, value)

    def _run(self):
        while not self._done.wait(self._interval_s):
            self._sample()

    def finish(self):
        if self._finished:
            return self._finished_mib
        self._sample()
        self._done.set()
        self._thread.join(timeout=max(0.1, self._interval_s * 2))
        self._finished_mib = _mib(self._peak_bytes)
        self._finished = True
        return self._finished_mib
