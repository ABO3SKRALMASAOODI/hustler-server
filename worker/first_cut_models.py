"""First-cut scouts use the configured provider's availability and credit."""


def available(model):
    import edit_rung
    import claude_lane
    if edit_rung.is_rung_model(model):
        return edit_rung.available()
    # Only a Claude id reaches Anthropic (claude_lane.create refuses any other).
    return claude_lane.is_claude_model(model) and claude_lane.available()


def paid_scout(worker_db):
    import claude_trial
    import edit_rung
    model = claude_trial.flag(worker_db)['model']
    if edit_rung.is_rung_model(model):
        # OpenAI's configured, credited rung is independent of the held
        # Anthropic wallet. Its normal replay/spend fences still apply.
        return model if edit_rung.available() else None
    if not claude_lane_model(model):
        return None              # Anthropic serves only Claude ids
    return model if claude_trial.credit_status(worker_db).get('on') else None


def claude_lane_model(model):
    import claude_lane
    return claude_lane.is_claude_model(model)
