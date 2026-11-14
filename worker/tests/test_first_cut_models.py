import first_cut_models as models


def test_openai_scout_is_independent_of_anthropic_wallet(monkeypatch):
    import claude_trial, claude_lane, edit_rung
    monkeypatch.setattr(claude_trial, 'flag', lambda db: {'model': 'gpt-6.1-sol'})
    monkeypatch.setattr(edit_rung, 'available', lambda: True)
    monkeypatch.setattr(claude_lane, 'available', lambda: False)
    def forbidden(*args):
        raise AssertionError('OpenAI must not read Anthropic credit')
    monkeypatch.setattr(claude_trial, 'credit_status', forbidden)
    assert models.paid_scout(None) == 'gpt-6.1-sol'
    assert models.available('gpt-6.1-sol')
    monkeypatch.setattr(edit_rung, 'available', lambda: False)
    assert models.paid_scout(None) is None
    assert not models.available('gpt-6.1-sol')


def test_anthropic_scout_still_obeys_its_credit_hold(monkeypatch):
    import claude_trial
    monkeypatch.setattr(claude_trial, 'flag', lambda db: {'model': 'claude-haiku-5-5'})
    monkeypatch.setattr(claude_trial, 'credit_status', lambda db: {'on': False})
    assert models.paid_scout(None) is None
