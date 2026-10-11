"""Fault injection: prove every creative device degrades instead of failing.

Owned by track 0C (the fault suite and the CI gate AT-0C-3,
tests/test_w0c_faults.py); the names are pinned in
worker/devices/INTERFACES.md §6.2 so tracks 0A and 0B place their consult
sites against them.

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

# Where each fault is consulted, and by whom (INTERFACES.md §6.2). The suite
# reports this next to every result so a skipped or missing site is visible.
CONSULT_SITES = {
    "model_missing": ("0A", "sidecar runner / personseg model load; "
                            "devices.lane_features drops the onnx feature"),
    "sidecar_timeout": ("0A", "sidecars.wait"),
    "chromium_crash": ("0B", "motion_engine, per item"),
    "executor_skew": ("0A", "remote health / feature reads; the render "
                            "path strips fx types it does not know"),
    "oom": ("0A/0B", "sidecar child and fx child"),
    "ffmpeg_filter_missing": ("0B", "fxgraph filter availability; "
                                    "devices.lane_features drops "
                                    "ffmpeg:<filter>"),
}

_ACTIVE = contextvars.ContextVar("valmera_faults", default=frozenset())


def _env():
    raw = os.getenv("VALMERA_FAULTS", "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def active():
    """Every fault name injected right now (context and environment)."""
    return _ACTIVE.get() | _env()


def fault(name):
    """Is `name` (or its base before ':') being injected right now?"""
    on = active()
    if not on:
        return False
    return name in on or name.split(":", 1)[0] in on


def details(base):
    """The ':<detail>' refinements of `base` injected right now; ``None``
    when the bare base name is injected (every detail fires)."""
    on = active()
    if base in on:
        return None
    return frozenset(n.split(":", 1)[1] for n in on
                     if n.startswith(base + ":"))


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
