"""Per-input byte accounting safe under concurrent Modal inputs.

The orchestration pools intentionally share one container among independent
projects. Process-global counters would blend those bills together, so byte
accounting follows the current execution context instead.
"""

from contextvars import ContextVar


_COUNTERS = ContextVar("valmera_io_counters", default=None)


def begin():
    return _COUNTERS.set({"downloaded_bytes": 0, "uploaded_bytes": 0})


def add_downloaded(value):
    row = _COUNTERS.get()
    if row is not None:
        row["downloaded_bytes"] += max(0, int(value or 0))


def add_uploaded(value):
    row = _COUNTERS.get()
    if row is not None:
        row["uploaded_bytes"] += max(0, int(value or 0))


def finish(token):
    row = dict(_COUNTERS.get() or {})
    _COUNTERS.reset(token)
    return row
