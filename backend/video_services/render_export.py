"""Pure render/export admission decisions.

This module never renders media. It decides whether immutable input should be
queued, reused, or rejected before a paid executor is launched.
"""


DETERMINISTIC_FINAL_FAILURE_KINDS = frozenset({
    "invalid_edl", "deterministic_input", "deterministic_ffmpeg",
    "render_budget_exceeded",
})
DETERMINISTIC_FINAL_ERROR_MARKERS = (
    "black-frame check failed", "duration check failed",
    "wrong duration", "wrong length", "invalid edl",
)


def deterministic_final_failure(row):
    """Whether re-running the same immutable EDL would repeat its failure."""
    if not row:
        return False
    result = row.get("result") or {}
    failure = result.get("failure") or {}
    kind = str(failure.get("kind") or "").strip().lower()
    if kind in DETERMINISTIC_FINAL_FAILURE_KINDS:
        return True
    error = str(row.get("error") or failure.get("error") or "").lower()
    return any(marker in error for marker in DETERMINISTIC_FINAL_ERROR_MARKERS)


def export_edl_error(edl, validator, source_duration=None):
    """Return a deterministic timeline error before spending a render job."""
    try:
        validator(edl, source_duration)
        return None
    except Exception as exc:
        return str(exc)[:500]
