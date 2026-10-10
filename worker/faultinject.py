"""Fault injection: prove every creative device degrades instead of failing.

WAVE 0 INTERFACE SEAM — owned by track 0C (the fault suite and the CI gate
AT-0C-3), pinned in worker/devices/INTERFACES.md §6.2 so tracks 0A and 0B
can place their consult sites now.

A consult site asks ``fault(name)`` exactly where the real failure would
surface and, when it returns True, takes the SAME path the real failure
takes (raise the same exception, report the same status). Off by default;
nothing in production sets it. Activation is either the context manager
(tests, in-process) or VALMERA_FAULTS=name,name (a subprocess render).
"""

import contextlib
import contextvars
import os

# Pinned names. A site may refine a fault with ":<detail>", e.g.
# "ffmpeg_filter_missing:maskedmerge" or "model_missing:matte.person".
FAULTS = (
    "model_missing",          # 0A sidecar runner / personseg: model file absent
    "sidecar_timeout",        # 0A sidecars.wait: budget spent, still pending
    "chromium_crash",         # 0B motion_engine: one item's page dies
    "executor_skew",          # 0A remote/version: lane reports other code/fx types
    "oom",                    # 0A/0B: a sidecar or fx child is OOM-killed
    "ffmpeg_filter_missing",  # 0B fxgraph: a filter absent from this ffmpeg
)

_ACTIVE = contextvars.ContextVar("valmera_faults", default=frozenset())


def _env():
    raw = os.getenv("VALMERA_FAULTS", "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def fault(name):
    """Is `name` (or its base before ':') being injected right now?"""
    active = _ACTIVE.get() | _env()
    if not active:
        return False
    return name in active or name.split(":", 1)[0] in active


@contextlib.contextmanager
def inject(*names):
    for n in names:
        if n.split(":", 1)[0] not in FAULTS:
            raise ValueError(f"unknown fault {n!r}; pinned: {FAULTS}")
    token = _ACTIVE.set(_ACTIVE.get() | frozenset(names))
    try:
        yield
    finally:
        _ACTIVE.reset(token)
