"""A large upload library must reach its first model call (ticket 117)."""
from copy import deepcopy
import agent_loop
from test_turn_outcomes import _ctx


def overview(clips=15, sheets=4):
    parts = [{'type': 'text', 'text': 'FILMSTRIPS & STILLS — overview'}]
    for i in range(clips):
        parts.append({'type': 'text', 'text': f'[UPLOADED CLIP clip-{i}]'})
        parts.extend({'type': 'image_url', 'image_url': {'url': f'clip-{i}-{j}'}}
                     for j in range(sheets))
    return {'role': 'user', 'content': parts}


def test_large_library_fits_without_losing_any_asset_or_request():
    messages = [{'role': 'system', 'content': 'x' * 60000}, overview(),
                {'role': 'user', 'content': 'Use the last fishing clip.'}]
    original = deepcopy(messages)
    assert agent_loop._agent_request_token_estimate(messages, [], 3000) > 90000
    removed = agent_loop._page_initial_visual_context(messages, [], 3000, 90000)
    assert removed > 0
    assert agent_loop._agent_request_token_estimate(messages, [], 3000) < 90000
    assert messages[0] == original[0] and messages[-1] == original[-1]
    images = [p['image_url']['url'] for p in messages[1]['content']
              if p['type'] == 'image_url']
    assert all(any(u.startswith(f'clip-{i}-') for u in images) for i in range(15))
    assert 'look_at_asset' in str(messages[1])


def test_small_overview_and_exact_tool_evidence_are_unchanged():
    exact = {'role': 'tool', 'content': [{'type': 'image_url', 'image_url': {'url': 'exact'}}]}
    messages = [overview(2, 2), exact]
    original = deepcopy(messages)
    assert agent_loop._page_initial_visual_context(messages, [], 3000, 90000) == 0
    assert messages == original
    messages.insert(0, {'role': 'system', 'content': 'x' * 335000})
    agent_loop._page_initial_visual_context(messages, [], 3000, 90000)
    assert messages[-1] == exact


def test_text_overflow_does_not_silently_truncate_the_request():
    messages = [{'role': 'user', 'content': 'x' * 400000}, overview(1, 1)]
    agent_loop._page_initial_visual_context(messages, [], 3000, 90000)
    assert len(messages[0]['content']) == 400000


def test_context_blocked_before_first_call_is_never_fulfilled():
    assert agent_loop._turn_completion(_ctx(), 'blocked')[0] == 'blocked'
