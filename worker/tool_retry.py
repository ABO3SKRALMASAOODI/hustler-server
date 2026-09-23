"""Retry decisions for one live tool call, separate from queue ownership."""
import failure_policy
from tool_outcome import ToolOutcome


# Not being an EDL setter does NOT make an operation idempotent: rendering,
# scouting, fetching and extracting can accept work before losing the reply.
# Only these inspected reads may be repeated without first reconciling state.
SAFE_READS = frozenset({
    'get_edl', 'get_video_info', 'get_transcript', 'get_words',
    'get_kept_transcript', 'get_shots', 'search_transcript', 'list_assets',
    'wait_for_job',
})


def decision(error):
    if isinstance(error, ValueError) and not hasattr(error, 'failure_kind'):
        return failure_policy.FailureDecision('deterministic_input', False, 0)
    return failure_policy.decision_for(error, 'mcp_tool')


def may_retry(name, error):
    return name in SAFE_READS and decision(error).retryable


def outcome(name, error, retried=False):
    failure = decision(error)
    correction = failure.agent_repairable or failure.kind in {
        'invalid_edl', 'deterministic_input', 'invalid_media',
    }
    status = ('correction_needed' if correction else
              'transient_failure' if failure.retryable else 'unavailable')
    state_read = name in SAFE_READS
    if correction:
        fallback = 'inspect the arguments and current EDL, correct the input, then try the corrected operation'
    elif not failure.retryable:
        fallback = 'preserve saved work and report the exact unavailable prerequisite; do not repeat unchanged'
    elif not state_read:
        fallback = ('inspect current project state and existing jobs/assets before '
                    'retrying; the operation may have completed before its reply was lost')
    elif failure.retryable:
        fallback = 'continue independent work before retrying this read'
    payload = failure.payload(error)
    return ToolOutcome(
        status=status,
        message=f"Tool {name} failed{' after one safe read retry' if retried else ''}: {payload['error'][:300]}",
        retryable=failure.retryable, idempotent=state_read,
        safe_fallback=fallback,
        evidence={'failure': payload, 'automatic_retry_attempted': retried})
