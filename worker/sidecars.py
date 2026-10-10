"""Sidecar store: analyse once, in source space, cache forever (ROADMAP A1).

WAVE 0 INTERFACE STUB — owned by track 0A, pinned by the round lead in
worker/devices/INTERFACES.md §2. The signatures, constants, key formula and
chunking below are the contract other tracks code against; 0A replaces the
bodies marked STUB (storage, launching, waiting) without changing them.

Until 0A lands, every sidecar reads as "absent": consumers must already take
their degrade path for that answer (a creative device degrades, never fails a
render).

Model outputs are immutable objects keyed by
    sha256(source_sha, kind, model_id, model_version, params, src_range)
measured on the SOURCE (proxy) clock, never on program time; the renderer
maps them through the same geometry chain as the picture. Writes never
block on analysis: ensure() returns at once, and a write reports
"measuring (~N s)" from estimate_s().
"""

import hashlib
import json
import math
from dataclasses import dataclass, field

STATUSES = ("ready", "pending", "failed", "absent")
PRIORITIES = ("interactive", "prefetch", "backfill")   # queue order, §8
CHUNK_MAX_S = 15.0

# Pinned kind names. Wave 0 ships the first two; the rest are reserved so
# later tracks cannot pick a colliding name. A kind's model id/version live
# with its runner (0A: models/*.json + models.lock), never at a call site.
KINDS = ("matte.person", "faces.yunet")
RESERVED_KINDS = (
    "rhythm.music", "rhythm.speech",           # 2C (speech = prosody grid)
    "embed.siglip2", "embed.dinov2",           # 2E
    "landmarks.face", "landmarks.hand", "landmarks.pose", "track.points",
    "camera.motion",                           # 3A
    "objects.sam2", "depth.small",             # 3B / 4A
    "planes.h", "plate.clean",                 # 3C
    "ocr.text",                                # 0D refbench / 2F
)


@dataclass(frozen=True)
class SourceRef:
    """The footage a sidecar measures. ``sha256`` is the ORIGINAL upload's
    content hash (assets.sha256 / render meta src_sha256); ``storage_key``
    the file the job reads (the 540p/720p proxy for model work)."""
    sha256: str
    storage_key: str
    duration_s: float
    fps: float
    width: int
    height: int
    project_id: int = None
    shots: tuple = ()          # index shot start times, SOURCE seconds


@dataclass(frozen=True)
class ChunkRef:
    key: str
    t0: float                  # SOURCE seconds, [t0, t1)
    t1: float


@dataclass(frozen=True)
class Handle:
    kind: str
    source_sha: str
    chunks: tuple = field(default_factory=tuple)
    priority: str = "interactive"
    params: str = "{}"         # canonical JSON of the params


def canonical_params(params):
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      default=str)


def sidecar_key(kind, source_sha, model_id, model_version, params,
                src_range):
    """The immutable object key. ``src_range`` is (t0, t1) in SOURCE seconds,
    folded to whole milliseconds so float noise never forks a cache entry."""
    t0, t1 = src_range
    blob = json.dumps([source_sha, kind, model_id, str(model_version),
                       canonical_params(params),
                       [int(round(float(t0) * 1000)),
                        int(round(float(t1) * 1000))]],
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chunk_ranges(shots, duration_s, max_s=CHUNK_MAX_S):
    """Deterministic chunk grid: every index shot [a, b) is split into
    ceil((b - a) / max_s) EQUAL pieces. A range request maps onto the chunks
    it overlaps, so the same footage always hits the same keys whoever asks.
    ``shots`` are shot start times (SOURCE seconds); none means one shot."""
    duration_s = float(duration_s)
    starts = sorted({0.0} | {float(s) for s in shots or ()
                             if 0.0 < float(s) < duration_s})
    edges = starts + [duration_s]
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a <= 1e-6:
            continue
        n = max(1, math.ceil((b - a) / max_s - 1e-9))
        step = (b - a) / n
        for i in range(n):
            out.append((round(a + i * step, 3),
                        round(b if i == n - 1 else a + (i + 1) * step, 3)))
    return out


def chunks_for(shots, duration_s, ranges, max_s=CHUNK_MAX_S):
    """The grid chunks that overlap any of ``ranges`` (SOURCE seconds)."""
    grid = chunk_ranges(shots, duration_s, max_s)
    want = [(float(a), float(b)) for a, b in ranges or ()]
    return [(a, b) for a, b in grid
            if any(b > s + 1e-6 and a < e - 1e-6 for s, e in want)]


def ensure(kind, source, ranges, priority="interactive", params=None):
    """Request a sidecar over SOURCE ranges; returns at once with a Handle.
    STUB: resolves the chunk keys but launches nothing."""
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    spans = chunks_for(source.shots, source.duration_s, ranges)
    chunks = tuple(ChunkRef(sidecar_key(kind, source.sha256, "stub", "0",
                                        params, span), *span)
                   for span in spans)
    return Handle(kind=kind, source_sha=source.sha256, chunks=chunks,
                  priority=priority, params=canonical_params(params))


def chunk_status(handle):
    """{chunk key: status}. STUB: nothing is stored yet."""
    return {c.key: "absent" for c in handle.chunks}


def status(handle):
    """Aggregate: ready only when every chunk is; pending if any is; failed
    if any failed and none is pending; otherwise absent."""
    states = set(chunk_status(handle).values())
    if states and states == {"ready"}:
        return "ready"
    for state in ("pending", "failed"):
        if state in states:
            return state
    return "absent"


def wait(handles, budget_s):
    """Block up to ``budget_s`` (previews pass 0; finals at most 90) and
    return {chunk key: status}. STUB: returns at once."""
    out = {}
    for handle in handles:
        out.update(chunk_status(handle))
    return out


def fetch(handle, dest_dir):
    """Ready chunks as local files: [{key, t0, t1, path, meta}], ordered by
    t0. Missing chunks are simply absent from the list (consumers treat
    them as black / inert). STUB: nothing is ready."""
    return []


def estimate_s(handle):
    """Seconds until ready if launched now (the write's "measuring" note).
    STUB: 0."""
    return 0.0


def summary(source_sha):
    """{kind: {"ready": n, "pending": n, "failed": n}} for project_state.
    STUB: empty."""
    return {}
