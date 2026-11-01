import copy
from types import SimpleNamespace

import pytest

import counter_facts


def _edit(label='dias de promoção'):
    return {'keep': [[0, 30]], 'motion': [{'id': 'dates', 'template': 'counter',
        'start': 5, 'end': 10, 'params': {'value': '20–29', 'label': label}}]}


def _words(text, start=5):
    return [{'w': w, 't0': start + i * .2, 't1': start + (i + 1) * .2}
            for i, w in enumerate(text.split())]


@pytest.mark.parametrize('text,label', [
    ('Começa dia 20 até dia 29. Dias maravilhosos.', 'dias de promoção'),
    ('From the 20th to the 29th.', 'days of offers'),
    ('Empieza el día 20 hasta el día 29.', 'días de ofertas'),
])
def test_calendar_dates_cannot_become_a_duration(text, label):
    assert 'calendar dates' in counter_facts.check_write(
        {}, _edit(label), _words(text), lambda t: t)


def test_real_duration_and_neutral_date_labels_still_work():
    assert counter_facts.check_write({}, _edit(), _words('Dura de 20 até 29 dias.'), lambda t: t) is None
    assert counter_facts.check_write({}, _edit('promoção'), _words('dia 20 até dia 29'), lambda t: t) is None


def test_existing_content_and_explicit_customer_copy_are_preserved():
    edit = _edit()
    words = _words('dia 20 até dia 29')
    assert counter_facts.check_write(edit, copy.deepcopy(edit), words, lambda t: t) is None
    assert counter_facts.check_write({}, edit, words, lambda t: t, 'Escreva 20–29 dias de férias') is None


def test_only_nearby_audible_source_can_trigger_the_guard():
    words = _words('dia 20 até dia 29', start=100)
    assert counter_facts.check_write({}, _edit(), words, lambda t: t) is None
    assert counter_facts.check_write({}, _edit(), words, lambda t: None) is None
    assert counter_facts.check_write({}, _edit(), words, lambda t: t - 95)


def test_editor_refuses_before_saving_an_invented_duration():
    import agent_tools
    before = {'keep': [[0, 30]]}
    ctx = SimpleNamespace(_states_seen={}, duration=30,
        index={'words': _words('Começa dia 20 até dia 29.')},
        user_message='Prepare a Black Friday promotion.',
        latest_edl=lambda: {'version': 1, 'json': before})
    result = agent_tools.ToolContext.write_edl(ctx, _edit(), 'Add sale dates')
    assert result.startswith('REJECTED (EDL v1 unchanged)')
    assert 'calendar dates' in result
