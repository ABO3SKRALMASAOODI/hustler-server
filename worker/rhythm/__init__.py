"""The rhythm engine for a user-supplied track (ROADMAP Wave 2, track 2C).

``map_for(path, sha256, ...)`` returns the track's RhythmMap, analysed once
per audio sha256 and cached in the sidecar store as kind ``rhythm.music``
(INTERFACES_W2.md §4: one "chunk" spans the whole track; the key is the
pinned sidecar_key formula with model ``rhythm.dsp``). Model-free (numpy +
ffmpeg), so every lane can compute a missing map inline.

Evaluation helpers (``f_measure``) follow the MIREX convention: a detection
matches an unmatched reference within +/- tolerance.
"""

import json

from .analyze import PRODUCER, VERSION, analyze, analyze_samples  # noqa: F401

KIND = "rhythm.music"
MODEL_ID = "rhythm.dsp"
FORMAT = "rhythm.json.v1"


def _key(sha, duration_s, params=None):
    import sidecars
    return sidecars.sidecar_key(KIND, sha, MODEL_ID, VERSION, params or {},
                                (0.0, float(duration_s or 0.0)))


def cached(sha, duration_s):
    """The stored RhythmMap for an audio sha256, or None."""
    try:
        import sidecars
        store = sidecars._store()
        key = _key(sha, duration_s)
        blob = store.get_bytes(sidecars.data_key(KIND, sha, key,
                                                 "rhythm.json"))
        if not blob:
            return None
        rmap = json.loads(blob if isinstance(blob, str) else blob.decode())
        return rmap if rmap.get("producer") == PRODUCER else None
    except Exception:  # noqa: BLE001 — a cold cache is a recompute
        return None


def store(sha, duration_s, rmap):
    try:
        import sidecars
        st = sidecars._store()
        key = _key(sha, duration_s)
        st.put_bytes(sidecars.data_key(KIND, sha, key, "rhythm.json"),
                     json.dumps(rmap, separators=(",", ":")).encode(),
                     "application/json")
        st.put_bytes(sidecars.manifest_key(KIND, sha, key),
               json.dumps({"key": key, "kind": KIND, "model_id": MODEL_ID,
                           "model_version": str(VERSION), "params": {},
                           "src_range": [0.0, float(duration_s or 0.0)],
                           "format": FORMAT}).encode(), "application/json")
        return True
    except Exception:  # noqa: BLE001
        return False


def map_for(path, sha256=None, duration_s=None, ref=None):
    """The RhythmMap of the audio at ``path`` (cached by sha256)."""
    if sha256 and duration_s:
        hit = cached(sha256, duration_s)
        if hit:
            return hit
    rmap = analyze(path, ref=ref)
    if sha256:
        store(sha256, duration_s or rmap.get("duration") or 0.0, rmap)
    return rmap


def f_measure(est, ref, tol=0.07):
    """(F, precision, recall) of event times ``est`` against ``ref``."""
    est, ref = sorted(est), sorted(ref)
    used = [False] * len(ref)
    hit = 0
    j0 = 0
    for t in est:
        while j0 < len(ref) and ref[j0] < t - tol:
            j0 += 1
        j = j0
        best = None
        while j < len(ref) and ref[j] <= t + tol:
            if not used[j] and (best is None or abs(ref[j] - t) <
                                abs(ref[best] - t)):
                best = j
            j += 1
        if best is not None:
            used[best] = True
            hit += 1
    p = hit / len(est) if est else 0.0
    r = hit / len(ref) if ref else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return f, p, r
