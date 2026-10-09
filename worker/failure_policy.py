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
        # The command's own last words, kept separately from the message so
        # a caller that truncates `error` for display still has them.
        tail = getattr(error, "stderr_tail", "") or ""
        if tail:
            out["stderr_tail"] = tail[-media.STDERR_TAIL_CHARS:]
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
    "canvas program needs at least one insert",
    "render duration check failed", "render is the wrong length",
    "render black-frame check failed",
)

_DETERMINISTIC_FFMPEG = (
    "invalid argument", "error initializing filter", "no such filter",
    "failed to configure output pad", "error reinitializing filters",
    "cannot find a matching stream", "matches no streams", "filtergraph",
    "unable to parse", "error parsing", "non-monotonous dts",
)

_PROVIDER_BUDGET = (
    "budget exceeded", "spending limit", "spend limit",
    "insufficient credits", "insufficient balance", "credit balance",
    "billing limit", "payment required", "no credits remaining",
    "insufficient_quota",
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
    if isinstance(error, dbx.PrerequisitePending):
        # Not a failed try: the input (usually the source's analysis) is
        # still being produced. Dispatchers defer it without spending an
        # attempt (defer_prerequisite); this decision is only the fallback
        # once the bounded deferrals are used up.
        return FailureDecision("prerequisite_pending", True,
                               _base_attempts(job_type), False)
    if isinstance(error, dbx.RemoteExecutionUnconfirmed):
        return FailureDecision(
            "remote_ownership_unconfirmed", True,
            min(_base_attempts(job_type), 2), False)
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

    # Duration is measured only after the original reaches an executor, so an
    # over-limit upload can legitimately arrive as a plain RuntimeError from
    # the indexer.  Re-downloading and probing the identical bytes cannot make
    # a 3.3-hour source fit a 3-hour product limit.
    if text.startswith("video is ") and "the limit is " in text:
        return FailureDecision("input_limit_exceeded", False, 0, False)

    # Provider account capacity does not change when the same user job is
    # replayed seconds later. Production used all three media attempts for
    # every Modal budget rejection, multiplying errors and queue delay without
    # one additional chance of success. A configured alternate provider is a
    # dispatch decision; retrying this unchanged lane is never that fallback.
    if any(value in text for value in _PROVIDER_BUDGET):
        return FailureDecision("provider_budget_exhausted", False, 0, False)

    if ('no remote executor is configured' in text
            or 'modal or cloudflare is required' in text):
        return FailureDecision('executor_unavailable', False, 0, False)

    # A provider lease expiring is the same exhausted physical budget as
    # an encoder wall timeout. Do not buy an identical render again, or ask
    # the editor to shorten a valid customer timeline to repair infrastructure.
    if media_edit and ("exceeded its executor lease" in text
                       or "through its executor deadline" in text):
        return FailureDecision("render_budget_exceeded", False, 0, False)

    # A clock budget is an infrastructure limit, not permission to shorten
    # or otherwise rewrite a customer's valid edit. Runaway output is a
    # distinct graph defect that the editor may repair on a new version.
    if re.search(r"wall-clock\s+[0-9.]+s\s+exceeded", text) \
            or re.search(r"timed out after\s+[0-9.]+s", text):
        return FailureDecision("render_budget_exceeded", False, 0, False)
    if "runaway encode" in text:
        return FailureDecision("render_budget_exceeded", False, 0,
                               job_type in ("preview", "preview_check"))

    # The kernel OOM killer ended the encoder (SIGKILL we did not send, plus a
    # kernel OOM-kill counter that moved — media.MediaOOMError). All 8 failed
    # finals in one 72 h window were this, each replayed once with the
    # identical graph on the identical 12 GiB box as a generic media error.
    # The same graph on the same executor shape cannot fit the second time —
    # a larger shape can (remote.py switches provider / takes the heavy lane
    # for this kind). Like render_budget_exceeded, an infrastructure limit is
    # no reason to rewrite a valid final; a preview may be made lighter.
    if isinstance(error, media.MediaOOMError) or (
            "out-of-memory killer" in text and "exit -9" in text):
        return FailureDecision("executor_memory", False, 0,
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
    # The executor's ffmpeg stderr tail rides in the payload; without this
    # copy every remote failure (i.e. every production render) lost it here.
    tail = (payload or {}).get("stderr_tail")
    if tail:
        error.stderr_tail = str(tail)[-media.STDERR_TAIL_CHARS:]
    return error


def defer_prerequisite(worker_db, job, error, decision, claim):
    """Requeue a prerequisite-pending job without spending its attempt.

    Returns True when the row went back to the queue; False means the caller
    applies the ordinary retry policy (bounded deferrals exhausted, the lease
    moved on, or the decision is a different kind)."""
    if decision.kind != "prerequisite_pending" or not job.get("id"):
        return False
    try:
        return bool(worker_db.run(
            dbx.defer_prerequisite_pending, job["id"], claim, error,
            config.PREREQUISITE_MAX_DEFERRALS))
    except Exception as exc:
        print(f"[job {job.get('id')}] prerequisite deferral failed: "
              f"{str(exc)[:160]}", flush=True)
        return False


def decision_for(error, job_type=None):
    """Prefer an executor decision already attached by :mod:`remote`."""
    if hasattr(error, "failure_kind"):
        return FailureDecision(
            str(error.failure_kind), bool(getattr(error, "retryable", False)),
            int(getattr(error, "max_attempts", 0)),
            bool(getattr(error, "agent_repairable", False)))
    return classify(error, job_type)
