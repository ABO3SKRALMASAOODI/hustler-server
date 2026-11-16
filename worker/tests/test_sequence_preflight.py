import inspect

import sequence_preflight as sp


def test_cleanup_cannot_silently_monopolize_a_cold_open_request():
    request = 'Build a 60s cold open, then logo, then the full episode.'
    assert sp.cleanup_reminder(request, ['cut_range'] * 6)
    assert not sp.cleanup_reminder(request, ['cut_range'] * 7)
    assert not sp.cleanup_reminder(request, ['cut_range'] * 5 + ['insert_media'])
    assert not sp.cleanup_reminder('Only remove silences.', ['cut_range'] * 6)


def test_no_second_model_vetoes_a_render_on_story_order():
    """Owner, 16 Nov 2026: the editor renders and looks at its own film; the
    order check that refused a full preview before it started is gone."""
    import agent_tools
    assert not hasattr(sp, 'check') and not hasattr(sp, 'REFUSAL_PREFIX')
    assert 'sequence_preflight' not in inspect.getsource(agent_tools.render_preview)
