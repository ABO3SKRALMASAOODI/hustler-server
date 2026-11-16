"""edit_scope records the turn's measured retained change; the editor's reply is never rewritten by a
second model or padded with boilerplate (EDIT-CORE B1, 16 Nov 2026)."""
from types import SimpleNamespace as NS

import edit_scope
from score import asks


def _no_model(monkeypatch):
    monkeypatch.setattr(asks, '_call', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no rewrite model')))


def test_the_editors_reply_goes_out_as_written_and_the_change_is_recorded(monkeypatch):
    _no_model(monkeypatch)
    ctx = NS(turn_start_edl={'json': {'texts': [{'text': 'Title'}]}},
             latest_edl=lambda: {'json': {'texts': []}})
    reply = 'I removed the title; the replacement animation could not be made from a still.'
    assert edit_scope.disclose(ctx, reply) == reply
    assert ctx.retained_reply_facts['fields'] == ['texts']
    assert 'removed 1 title' in ctx.retained_reply_facts['description']


def test_an_unchanged_turn_gets_no_boilerplate(monkeypatch):
    """p5744 R4 replied 'No changes were kept; your previous film is unchanged.' under every answer."""
    _no_model(monkeypatch)
    ctx = NS(turn_start_edl={'json': {'keep': [[0, 2]]}}, latest_edl=lambda: {'json': {'keep': [[0, 2]]}},
             ask_verdict={'asks': [{'id': 'a1', 'state': 'not_done'}]})
    reply = 'Calm Currents is on the film now at -18 LUFS under the walk.'
    out = edit_scope.disclose(ctx, reply)
    assert out == reply
    assert 'previous film is unchanged' not in out and 'No changes were kept' not in out
    assert ctx.retained_reply_facts['fields'] == ['unchanged']


def test_a_tools_real_alternative_is_recorded_and_the_editors_own_offer_stands(monkeypatch):
    """The tool result gives the editor the alternative; the editor offers it in the customer's
    language. Nothing appends the tool's English text under a Hindi, Spanish or Portuguese reply."""
    _no_model(monkeypatch)
    offer = 'I can show your uploaded reference as a still; it will not animate.'
    ctx = NS(turn_start_edl={'json': {}}, latest_edl=lambda: {'json': {}},
             edit_alternatives=[{'offer': offer}])
    reply = ('No pude animar este archivo. Puedo usar tu referencia como imagen fija, '
             'o colocar un clip animado si lo subes.')
    assert edit_scope.disclose(ctx, reply) == reply
    assert ctx.retained_reply_facts['fields'] == ['unchanged', 'alternative']
    assert offer in ctx.retained_reply_facts['description']


def test_no_second_model_authorizes_the_editors_writes():
    """Owner, 16 Nov: the editing model decides; edit_scope keeps disclosure only,
    and nothing the editor saved is rolled back after the turn."""
    import agent_tools
    import agent_loop
    import inspect
    assert not hasattr(edit_scope, "write_refusal") and not hasattr(edit_scope, "SYSTEM")
    assert not hasattr(edit_scope, "restore_unmet")
    assert "edit_scope.write_refusal" not in inspect.getsource(agent_tools)
    assert "restore_unmet" not in inspect.getsource(agent_loop)
