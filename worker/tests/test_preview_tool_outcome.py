import pytest

import agent_tools
from tool_outcome import from_legacy


@pytest.mark.parametrize('receipt', [
    'Preview v2 rendered: 30.0s (source 30.0s).',
    'Preview v2 was ALREADY rendered — the cached complete preview is attached.',
    'Preview v2 is already rendered and attached — inspect it.',
])
def test_successful_preview_review_instructions_are_not_executor_failures(receipt):
    result = receipt + (
        ' If a fix does not land, never repeat the exact call that just failed.'
        ' VERSION VERIFICATION RECORD: repair required — audio is quiet.')
    outcome = from_legacy(result, idempotent=True)
    assert outcome.status == 'success'
    assert not outcome.retryable
    assert outcome.render_text() == result
    assert 'repair required' in outcome.message
    assert agent_tools.tool_result_kind(result) == 'success'


@pytest.mark.parametrize('result', [
    'Preview v2 failed: source download failed',
    'Preview render failed: encoder unavailable',
    'TRANSIENT_FAILURE: Preview v2 rendered: storage registration failed',
    'UNSAFE: Preview v2 rendered: project ownership failed',
])
def test_real_preview_failures_keep_their_failure_status(result):
    assert from_legacy(result).status in {'transient_failure', 'unsafe'}
