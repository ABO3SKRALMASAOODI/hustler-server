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

# Cloudflare could not provide a container before any /run (the runtime's
# own words, the dispatcher's final summary, and a startup it abandoned).
# Nothing ran, so the unchanged job may run again; remote.py has already
# spread it across Durable Objects and lanes before this verdict.
_PROVIDER_CAPACITY = (
    "there is no container instance",
    "requesting too many containers per second",
    "throttling the container service",
    "cloudflare could not provide a container",
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
    if getattr(error, "failure_kind", None) == "provider_rollout_pending":
        # An executor on another release refused before any work
        # (skew_guard), or it was draining for a new release. Not a failed
        # try: the dispatcher relaunches the claim once the rollout lands.
        return FailureDecision("provider_rollout_pending", True, 1, False)
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
    if any(x in text for x in _PROVIDER_CAPACITY):
        return FailureDecision("provider_capacity_unavailable", True,
                               min(_base_attempts(job_type), 2), False)
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


# ── Infrastructure failures the user never sees (strategy 1025 R1) ────────
# Kinds and texts that describe OUR fleet, not the customer's edit: nothing
# about the job is wrong, so the honest move is to try again quietly for a
# while (on a new claim: a different shard, a fresh container) and only then
# say, in plain words, that the problem is on our side.
INFRA_KINDS = frozenset({
    "transient_infrastructure", "provider_capacity_unavailable",
    "executor_unavailable", "remote_ownership_unconfirmed",
    "provider_rollout_pending", "provider_start_abandoned", "stalled_io",
})
_INFRA_TEXT = (
    "lost the executor", "container exited", "readiness mismatch",
    "sidecar is shutting down", "source version does not match",
    "deploy in progress", "switching to a new release", "shard is busy",
    "no remote executor is configured", "modal or cloudflare is required",
    "could not be recovered", "executor is draining",
)
# Re-running these from scratch is idempotent: each writes one artifact for
# one EDL version.
INFRA_RETRY_TYPES = frozenset({"index", "preview", "preview_check", "final",
                               "filmstrip", "agent_turn", "mcp_tool"})
# Agent turns and MCP tools mutate the timeline, so they are retried quietly
# only while their mutation_receipt shows no committed write (main.
# _quiet_infra_retry re-reads it); an agent turn that already wrote resumes
# as a continuation from its last committed EDL instead. Before 25 Oct these
# were excluded: 10 requests of 5 users ended on "shard is busy", a lost
# lease or a sidecar shutdown, and 16 MCP calls failed the same way in the
# last 30 days.
MUTATING_RETRY_TYPES = frozenset({"agent_turn", "mcp_tool"})
# Whether the remote is still executing is unknown: never replayed for a
# mutating job (its reply or write could land twice).
AMBIGUOUS_OWNERSHIP_KINDS = frozenset({"remote_ownership_unconfirmed"})
INFRA_RETRY_WINDOW_S = 600
# A Studio turn may have run for a while before its container went away.
INFRA_RETRY_WINDOW_BY_TYPE = {"agent_turn": 1800}
INFRA_RETRY_MAX = 3
# The owner's render box (execution_provider "box"): a job is never failed
# by our own infrastructure there (owner's rule: jobs wait, never fail). A
# slot container lost after 20 minutes of a 40-minute index is the same
# infrastructure failure as one lost after a minute, so box jobs are bounded
# by the retry COUNT, not by their age (R1 chaos, 30 Oct 2026: a 30-min
# index whose slot died at minute 11 spent its attempt; a second loss would
# have failed the upload with "our processing service had a problem").
BOX_INFRA_RETRY_WINDOW_S = 6 * 3600


def _on_box(job):
    return (str(job.get("_executor_target") or "") == "box"
            or str((job.get("payload") or {}).get("execution_provider")
                   or "").strip().lower() == "box")


def infra_retry_window_s(job):
    if _on_box(job):
        return BOX_INFRA_RETRY_WINDOW_S
    return INFRA_RETRY_WINDOW_BY_TYPE.get(str(job.get("type") or ""),
                                          INFRA_RETRY_WINDOW_S)


def is_infrastructure(error, decision=None):
    decision = decision or decision_for(error)
    if decision.kind in INFRA_KINDS:
        return True
    text = str(error).lower()
    return decision.kind == "unknown" and any(t in text for t in _INFRA_TEXT)


def infra_retry_delay(job, now_ts, attempt):
    """Seconds to wait before the next quiet retry, or None when the job is
    outside its 10-minute window (or out of retries). The requeued row then
    also waits CLOUDFLARE_BUSY_RETRY_DELAY_S before it can be claimed, so the
    lane itself is held only briefly (10, 20, 40 s)."""
    if str(job.get("type") or "") not in INFRA_RETRY_TYPES:
        return None
    if attempt >= INFRA_RETRY_MAX:
        return None
    created = job.get("created_at")
    if created is None:
        return None
    try:
        age = now_ts - created.timestamp()
    except AttributeError:
        return None
    remaining = infra_retry_window_s(job) - age
    if remaining <= 0:
        return None
    return max(5.0, min(remaining, 10.0 * (2 ** attempt), 40.0))


def user_reason(error, decision=None):
    """The words a customer may see for a failure. Never the raw text: raw
    executor, ffmpeg and provider messages reached 16 of 28 paying
    customers (95 messages) before this existed."""
    decision = decision or decision_for(error)
    if decision.kind == "input_limit_exceeded":
        return "the video is longer than this plan can process"
    if decision.kind == "invalid_media":
        return "the file has no video track we could read"
    if decision.kind in ("invalid_edl", "deterministic_input",
                         "deterministic_ffmpeg"):
        return "this version could not be rendered as it stands"
    if decision.kind in ("render_budget_exceeded", "executor_memory",
                         "executor_capacity"):
        return "this version needs more processing than one render allows"
    return "a problem on our side, not with your video"
