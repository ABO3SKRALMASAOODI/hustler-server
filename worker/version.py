"""What code this process is actually running (round 56).

THE PROBLEM THIS EXISTS FOR. The render/index work lives on a SECOND service —
a Cloud Run executor deployed by hand with `gcloud run deploy` — while the
dispatcher on Render redeploys itself on every push. So a push updates one half
of the system and silently leaves the other half on whatever was built last.
Nothing anywhere reported the difference. It has now cost real users twice:

  * Round 53 — every finished export was hidden behind a `trans_v` stamp the
    executor never wrote, because the executor predated the code that writes it.
    41 of 41 finals undownloadable, one customer pressing Download 17 times.
  * Round 55 — an executor 19 hours older than the round-52 render fix stretched
    a customer's export to 158.58s for a 150.48s edit and failed verification
    three times, while REJECTING his previews outright because his EDL used a
    `stylize` kind the older schema had never heard of. Both round-52 changes
    were committed, pushed, tested and live on the dispatcher. Neither was
    running where the pixels are made.

Both diagnoses took a day of database forensics to reach a fact that a single
HTTP GET should have stated. So this module makes the answer cheap.

WHY A CONTENT FINGERPRINT AND NOT A VERSION CONSTANT. There are already four
paired constants in this codebase (`PIPELINE_VERSION`, `OUTRO_VERSION`,
`TRANSITION_VERSION`, `TIMELINE_MEDIA_VERSION`) and every one of them catches
skew only when someone remembers to bump it. Neither incident above involved a
bumped constant — round 52 changed the render CLOCK and the stylize ENUM, which
are not the kind of change anyone thinks to stamp. A hash over the source needs
no discipline at all: if the two services are running different bytes, they
report different fingerprints, whatever changed.

Scope is the worker package's TOP-LEVEL `*.py` — the program itself. Not
`tests/`, not `tools/`, not the asset directories: those are the files whose
presence can legitimately differ between a Docker build context and a
`gcloud run deploy --source` upload, and a fingerprint that cries skew when
nothing is wrong is worse than no fingerprint, because the next real one gets
ignored. Every top-level module here is imported by one role or the other, so
nothing that changes behaviour is outside the window.

The fingerprint is NEVER a gate. It does not refuse a job, hold back a render
or withhold a result — that is precisely the round-53 mistake, where a version
check that could only say "no" hid finished work from every user on the
platform. It only ever explains.
"""

import hashlib
import os

_DIR = os.path.dirname(os.path.abspath(__file__))

_cached = None


def _source_files():
    """The top-level modules, sorted — a stable list on any filesystem."""
    try:
        names = sorted(n for n in os.listdir(_DIR)
                       if n.endswith(".py") and not n.startswith("."))
    except OSError:
        return []
    return [os.path.join(_DIR, n) for n in names]


def code_version():
    """A short fingerprint of this process's worker source.

    Same bytes -> same string, on any host. Computed once and cached: the files
    cannot change under a running process, and both callers (a boot line and a
    health response) are hot enough that re-reading forty files would be waste.

    Returns "unknown" if the source cannot be read. A diagnostic must never be
    the thing that fails, and "unknown" compares equal to nothing, so a skew
    warning it cannot substantiate is simply not printed.
    """
    global _cached
    if _cached is not None:
        return _cached
    h = hashlib.sha256()
    try:
        for path in _source_files():
            # The NAME is hashed as well as the bytes, so adding or deleting a
            # module moves the fingerprint even if the surviving files are
            # untouched — a deleted module is a code change like any other.
            h.update(os.path.basename(path).encode("utf-8"))
            h.update(b"\0")
            with open(path, "rb") as f:
                h.update(f.read())
            h.update(b"\0")
    except OSError:
        _cached = "unknown"
        return _cached
    _cached = h.hexdigest()[:12]
    return _cached


def version_report():
    """Everything a human needs to tell two deploys apart, as plain JSON.

    The paired constants ride along beside the fingerprint. They are the coarse
    signal — when one of them differs the mismatch has a KNOWN consequence
    (stale index artifacts, an old end card, a stale transition cache, withheld
    timeline artwork) and the fix is documented next to each. The fingerprint is
    the fine one: it differs for any change at all, including the two that
    actually broke production.
    """
    import config
    import filmstrip
    import schemas
    feats = list(FEATURES)
    # Stems is per-BUILD, not per-code: the demucs dependency is baked into
    # the executor image and absent on slim builds, so the honest answer is
    # whether THIS process could actually run one (round 97).
    try:
        import stems
        if stems.available():
            feats.append("stems")
    except Exception:
        pass
    return {
        "code_version": code_version(),
        "role": config.WORKER_ROLE,
        "pipeline_version": schemas.PIPELINE_VERSION,
        "outro_version": config.OUTRO_VERSION,
        "transition_version": config.TRANSITION_VERSION,
        "timeline_media_version": filmstrip.TIMELINE_MEDIA_VERSION,
        "features": feats,
    }


# Capabilities this build can RENDER, advertised through /health so the
# dispatcher can gate a WRITE that the executor could not draw (round 96).
# The fingerprint above answers "same bytes?"; this answers "can it do X?" —
# which is the question a feature gate actually has, because the dispatcher
# auto-deploys on every push and the executor does not: exact-version
# equality would switch features off after every unrelated worker deploy.
# Append-only: removing a name re-enables the round-53 failure shape.
FEATURES = ("custom_filter",)
