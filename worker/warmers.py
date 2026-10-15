"""Upload-time analysis policy (Wave 3, 3F).

ROADMAP Wave 3 / 3F (``warmers.py``) and AT-3F-5: the first matte or world
device on a warmed upload returns with no wait. The index already warms
``faces.yunet`` over the whole source (sidecars.prefetch_after_index) and,
after a shorts ranking, the person matte over the top shorts
(prefetch_top_shorts). This module decides what ELSE an upload warms, under
a compute budget, before anyone asks:

  * ``matte.person`` — the device most first edits reach for (type behind
    the speaker, the subject-aware punctuation). A short upload (<=
    ``FULL_S``) is matted whole; a long one over its opening ``HEAD_S``
    seconds and its longest shots, until the budget is spent.
  * ``landmarks.face`` — cheap (15 fps mesh on the face ROI): every warmed
    matte range, so "round his head" / an attached label lands at once.
  * hands, points, objects, planes, plates are NEVER warmed: they need a
    prompt (an object, a plane, a seed) or a hand that may never rise.

All of it runs on the batch lane at ``prefetch`` priority (the vision lane
stays dark), and only when the owner turned it on: ``SIDECAR_WARM_UPLOAD=1``
(default off — warming spends batch-lane compute nobody asked for yet;
"no new paid capacity without the owner"). Never raises; never waits.
"""

import os

FULL_S = 180.0          # uploads up to this long are warmed whole
HEAD_S = 60.0           # long uploads: the opening seconds ...
MAX_SHOTS = 4           # ... plus their longest shots
BUDGET_S = 900.0        # compute seconds (batch-lane rate) per upload
WARM_KINDS = ("matte.person", "landmarks.face")
NEVER = ("landmarks.hand", "landmarks.pose", "track.points", "camera.motion",
         "objects.sam2", "planes.h", "plate.clean")


def enabled():
    return os.getenv("SIDECAR_WARM_UPLOAD", "0") == "1"


def _cost_s(kind, seconds):
    """Compute seconds for ``seconds`` of footage at the kind's measured
    batch-lane rate (sidecars' own spec numbers; 0 when unknown)."""
    try:
        import sidecars
        sp = sidecars._KIND_SPECS[kind]
        params = sidecars.resolve_params(kind, None)
        fps = sidecars._rate(kind, params)
        return float(seconds) * float(fps) * float(sp["ms_per_frame"]) / 1e3
    except Exception:  # noqa: BLE001
        return 0.0


def _merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def policy(duration_s, shots=(), *, budget_s=BUDGET_S, cost=None):
    """[(kind, [(a, b)], priority)] an upload of ``duration_s`` seconds with
    index ``shots`` ([{start, end}] or [(a, b)]) warms, the matte first.
    ``cost(kind, seconds)`` defaults to the batch-lane estimate."""
    cost = cost or _cost_s
    d = float(duration_s or 0.0)
    if d <= 0:
        return []
    if d <= FULL_S:
        want = [(0.0, d)]
    else:
        want = [(0.0, HEAD_S)]
        spans = []
        for s in shots or ():
            a, b = (s.get("start"), s.get("end")) if isinstance(s, dict) \
                else s
            if a is None or b is None:
                continue
            spans.append((float(a), float(b)))
        spans.sort(key=lambda r: -(r[1] - r[0]))
        want += [(a, min(b, a + 30.0)) for a, b in spans[:MAX_SHOTS]
                 if a >= HEAD_S]
    want = _merge([(max(0.0, a), min(d, b)) for a, b in want if b > a])
    # spend the budget in order (opening first), trimming the last range
    left, matte = float(budget_s), []
    for a, b in want:
        c = cost("matte.person", b - a)
        if c <= left or c <= 0:
            matte.append((a, b))
            left -= max(0.0, c)
            continue
        per_s = c / (b - a)
        keep = left / per_s if per_s > 0 else 0.0
        if keep >= 5.0:
            matte.append((a, a + keep))
        break
    if not matte:
        return []
    # the mesh is ROI-cheap (≈7% of the matte's cost): it rides the matte's
    # ranges, outside the budget
    return [("matte.person", matte, "prefetch"),
            ("landmarks.face", list(matte), "prefetch")]


def warm_upload(src, *, budget_s=BUDGET_S):
    """Apply ``policy`` to a SourceRef (sidecars.source_from). Gated by
    SIDECAR_WARM_UPLOAD; returns {kind: handle} for what it asked (empty
    when off); never raises — an upload never fails on warming."""
    if not enabled() or src is None:
        return {}
    out = {}
    try:
        import sidecars
        for kind, ranges, prio in policy(src.duration_s, src.shots or (),
                                         budget_s=budget_s):
            if kind in NEVER:
                continue
            try:
                out[kind] = sidecars.ensure(kind, src, ranges, priority=prio)
            except Exception as e:  # noqa: BLE001
                print(f"[warmers] {kind} not warmed: {str(e)[:160]}",
                      flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[warmers] skipped: {str(e)[:160]}", flush=True)
    return out


def warm_state(src, ranges, kinds=WARM_KINDS):
    """{kind: share of ``ranges`` READY} — what a first device would find
    (AT-3F-5: 1.0 means it returns with no wait). Reads only."""
    out = {}
    try:
        import sidecars
    except Exception:  # noqa: BLE001
        return {k: 0.0 for k in kinds}
    for kind in kinds:
        try:
            chunks = sidecars.chunks_for(src.shots, src.duration_s, ranges)
            ready = sidecars.ready_chunks(kind, src.sha256, src.shots,
                                          src.duration_s, ranges)
            out[kind] = round(len(ready) / float(len(chunks)), 3) \
                if chunks else 0.0
        except Exception:  # noqa: BLE001
            out[kind] = 0.0
    return out
