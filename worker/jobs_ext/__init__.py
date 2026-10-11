"""Executor job kinds added without editing http_server.py.

Wave 0 interface pass (worker/devices/INTERFACES.md §3). http_server merges
``runners()`` into COMPUTE_RUNNERS once, so only the compute executor roles
(interactive, batch, a future vision lane) run them; the agent, MCP and
Shorts roles keep their single job family.

Every ``jobs_ext/<name>.py`` whose name does not start with ``_`` exports
``JOB_KINDS``: a sequence of JobKind. Runners are named as "module:function"
and imported on first call, so an executor cold start never pays for a
model library it is not asked to use.

Routing a kind to a Cloudflare lane is NOT automatic: the dispatcher's lane
map (remote._cloudflare_lane), config.CLOUDFLARE_EXECUTOR_TYPES /
CLOUDFLARE_SYNCHRONOUS_TYPES and the Worker's type sets in
cloudflare/src/index.ts must list the kind too (the owning track edits those
once). Until they do, a registered kind is runnable only by a direct /run.
"""

import importlib
import os
from dataclasses import dataclass, field

LANES = ("batch", "interactive", "vision")


@dataclass(frozen=True)
class JobKind:
    name: str
    runner: str                  # "module:function"; fn(worker_db, job) -> dict
    lane: str = "batch"          # home Cloudflare lane; "vision" is dark and
                                 # falls back to "batch" while its flag is off
    synchronous: bool = True     # no video_jobs row; the caller waits
    timeout_s: int = 900
    features: tuple = field(default_factory=tuple)   # lane features required


_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY = {}


def discover():
    mods = []
    for fname in sorted(os.listdir(_DIR)):
        if fname.endswith(".py") and not fname.startswith("_"):
            mods.append(importlib.import_module(f"{__name__}.{fname[:-3]}"))
    return mods


def register(kind):
    if not isinstance(kind, JobKind) or ":" not in kind.runner:
        raise TypeError(f"{kind!r} must be a JobKind with runner "
                        "'module:function'")
    if kind.lane not in LANES:
        raise ValueError(f"job kind {kind.name}: lane must be one of {LANES}")
    old = REGISTRY.get(kind.name)
    if old is not None and old != kind:
        raise ValueError(f"job kind {kind.name} is registered twice")
    REGISTRY[kind.name] = kind


def load(modules=None):
    for mod in (discover() if modules is None else modules):
        for kind in getattr(mod, "JOB_KINDS", None) or ():
            register(kind)
    return dict(REGISTRY)


def _lazy(spec):
    module, _, fn = spec.partition(":")

    def run(worker_db, job):
        return getattr(importlib.import_module(module), fn)(worker_db, job)
    run.__name__ = f"lazy_{fn}"
    run.__qualname__ = run.__name__
    return run


def runners(modules=None):
    """{kind: runner} for every registered extension kind."""
    return {name: _lazy(kind.runner) for name, kind in load(modules).items()}


def merge_into(core, modules=None):
    """Add the extension runners to a core runner map. A kind that would
    shadow a core runner fails at import, never silently."""
    ext = runners(modules)
    clash = sorted(set(ext) & set(core))
    if clash:
        raise ValueError("jobs_ext kinds shadow core runners: "
                         + ", ".join(clash))
    core.update(ext)
    return core
