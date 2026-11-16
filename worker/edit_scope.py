"""The reply of a typed turn covers what the edit actually keeps.

The editing model decides what a request needs; no second model authorizes its writes, and
nothing it saved is rolled back after the turn (owner, 16 Nov 2026). This module only measures
the retained change for the record; the editor's reply discloses it.
"""

def changes(before, after):
    return {k: {"before": before.get(k), "after": after.get(k)}
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}


def _remember_disclosure(ctx, delta, measured, offer=None):
    description = ('The previous film is unchanged.' if not delta else
                   'Changes kept in the film: ' + (measured or ', '.join(delta)) + '.')
    if offer:
        description += ' ' + offer
    ctx.retained_reply_facts = {
        'fields': (sorted(delta) if delta else ['unchanged']) + (['alternative'] if offer else []),
        'description': description,
    }


def disclose(ctx, reply):
    """Records the measured retained change of the turn (ctx.retained_reply_facts, for the message
    meta and the checks), and the real alternative a tool named when it met a request it could not
    do. The reply is returned as the editor wrote it.

    The reply itself is the editor's, written from the measured change its write results carry
    (edit_provenance.turn_change) and from where each part of the film came from. No second model
    rewrites it and no boilerplate is appended (EDIT-CORE B1, 16 Nov 2026: a third rewrite told to
    "say the previous film is unchanged" put "Nothing in the edit changed" beside "I have cut the
    silences" on p5717, and "No changes were kept; your previous film is unchanged." under p5744's
    replies)."""
    ctx.retained_reply_facts = None
    baseline = (getattr(ctx, 'turn_start_edl', None) or {}).get('json')
    if not isinstance(baseline, dict):
        return reply
    current = ctx.latest_edl()['json']
    delta = changes(baseline, current)
    # A tool that met a request it could not do names a real alternative (edit_alternatives). Its
    # result already gives that alternative to the editor, who offers it in its own words and in
    # the customer's language; it is recorded here, never appended (an English copy of it landed
    # under Hindi, Spanish and Portuguese replies that had already made the offer).
    alternatives = getattr(ctx, 'edit_alternatives', None) or []
    import reply_grounding
    measured = reply_grounding.change_list_text(reply_grounding.edl_changes(baseline, current))
    offer = alternatives[0].get('offer') if alternatives and isinstance(alternatives[0], dict) else None
    _remember_disclosure(ctx, delta, measured, offer)
    return reply
