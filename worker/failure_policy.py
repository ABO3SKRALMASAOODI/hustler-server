"""One retry policy for every worker/executor failure.

Retries are a reliability feature only when a second run has a credible chance
of seeing different conditions.  Replaying the same invalid EDL, ffmpeg graph,
or exhausted wall-clock is both slower for the user and paid twice.  This
module keeps that decision identical on Render and Cloud Run and gives the
agent enough structured context to repair an EDL on a *new* version.
"""

from dataclasses import asdict, dataclass
import re

import config
import db as dbx
import error_text
import media
from schemas import EDLValidationError
from storage import WorkdirTooSmall


@dataclass(frozen=True)
class FailureDecision:
    kind: str
    retryable: bool
    max_attempts: int
    agent_repairable: bool = False

    def payload(self, error):
        out = asdict(self)
        out["error"] = error_text.excerpt(error, 2000)
        return out


_TRANSIENT = (
    "connection reset", "connection refused", "connection aborted",
    "temporarily unavailable", "temporary failure", "service unavailable",
    "bad gateway", "gateway timeout", "remote disconnected", "broken pipe",
    "http 429", "http 500", "http 502", "http 503", "http 504",
    "status 429", "status 500", "status 502", "status 503", "status 504",
    "name resolution", "network is unreachable",
)

_INVALID_EDL = (
    "edl version", "edl validation", "invalid edl", "invalid keep",
    "invalid speed", "invalid frame", "invalid transition",
    "render duration check failed", "render is the wrong length",
    "render black-frame check failed",
)

_DETERMINISTIC_FFMPEG = (
    "invalid argument", "error initializing filter", "no such filter",
    "failed to configure output pad", "error reinitializing filters",
    "cannot find a matching stream", "matches no streams", "filtergraph",
    "unable to parse", "error parsing", "non-monotonous dts",
)


def _base_attempts(job_type):
    if job_type == "agent_turn":
        return config.MAX_ATTEMPTS_AGENT
    if job_type == "mcp_tool":
        return config.MAX_ATTEMPTS_MCP
    return config.MAX_ATTEMPTS_MEDIA


def classify(error, job_type=None):
    """Return whether running the *unchanged* physical job again is useful."""
    text = str(error).lower()
    media_edit = job_type in ("preview", "preview_check", "final")

    if isinstance(error, dbx.JobLeaseLost):
        return FailureDecision("lease_lost", False, 0, False)
    if "job was cancelled or handed to another worker" in text:
        return FailureDecision("lease_lost", False, 0, False)
    if isinstance(error, WorkdirTooSmall):
        return FailureDecision("executor_capacity", False, 0, False)
    if isinstance(error, EDLValidationError):
        return FailureDecision("invalid_edl", False, 0, media_edit)
    if isinstance(error, dbx.PermanentJobError):
        repairable = media_edit and any(x in text for x in _INVALID_EDL)
        return FailureDecision(
            "invalid_edl" if repairable else "deterministic_input",
            False, 0, repairable)

    # The watchdog already proved the exact command cannot finish inside the
    # fleet's physical budget.  Buying the same 50 minutes again cannot make
    # it shorter; a new/simpler EDL or a different execution shape can.
    if re.search(r"wall-clock\s+[0-9.]+s\s+exceeded", text) \
            or "runaway encode" in text \
            or re.search(r"timed out after\s+[0-9.]+s", text):
        return FailureDecision("render_budget_exceeded", False, 0,
                               job_type in ("preview", "preview_check"))

    if any(x in text for x in _INVALID_EDL):
        return FailureDecision("invalid_edl", False, 0, media_edit)
    if any(x in text for x in _DETERMINISTIC_FFMPEG):
        return FailureDecision("deterministic_ffmpeg", False, 0,
                               job_type in ("preview", "preview_check"))
    if "no video stream found" in text \
            or "could not determine video duration" in text:
        return FailureDecision("invalid_media", False, 0, False)

    # A stalled source/download can recover once.  More than one repeat is no
    # longer a blip and used to turn one bad object into hours of Cloud Run.
    if "no progress" in text or "stalled" in text:
        return FailureDecision("stalled_io", True,
                               min(_base_attempts(job_type), 2),
                               job_type in ("preview", "preview_check"))
    if any(x in text for x in _TRANSIENT):
        return FailureDecision("transient_infrastructure", True,
                               min(_base_attempts(job_type), 2), False)

    if isinstance(error, media.MediaError):
        return FailureDecision("media_command", True,
                               min(_base_attempts(job_type), 2),
                               job_type in ("preview", "preview_check"))

    # Unknown failures retain a bounded second chance.  The old default was
    # three physical runs for every media exception, including deterministic
    # ones; two preserves resilience without paying for a third guess.
    base = _base_attempts(job_type)
    return FailureDecision("unknown", base > 1, min(base, 2), False)


def attach(error, decision, payload=None):
    """Carry an executor's structured decision through the HTTP client."""
    error.failure_kind = (payload or {}).get("kind") or decision.kind
    error.retryable = (payload or {}).get("retryable", decision.retryable)
    error.max_attempts = int(
        (payload or {}).get("max_attempts", decision.max_attempts))
    error.agent_repairable = bool(
        (payload or {}).get("agent_repairable", decision.agent_repairable))
    return error


def decision_for(error, job_type=None):
    """Prefer an executor decision already attached by :mod:`remote`."""
    if hasattr(error, "failure_kind"):
        return FailureDecision(
            str(error.failure_kind), bool(getattr(error, "retryable", False)),
            int(getattr(error, "max_attempts", 0)),
            bool(getattr(error, "agent_repairable", False)))
    return classify(error, job_type)
