from types import SimpleNamespace
from first_cut_spelling import apply, context


def test_printed_source_spelling_survives_legacy_caption_path():
    idx = {'words': [{'w': 'jadot', 't0': 1, 't1': 2}],
           'visual_storyboard': {'evidence': [{'visible_text': 'A conversation with Fatima Jakoet'}]}}
    ctx = SimpleNamespace(index=idx)
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    assert ctx.index['words'] == [{'w': 'Jakoet', 't0': 1, 't1': 2}]
    assert idx['words'][0]['w'] == 'jadot'
    assert 'Fatima Jakoet' in context(idx)


def test_model_cannot_invent_an_inscription():
    ctx = SimpleNamespace(index={'words': [{'w': 'jadot'}], 'visual_storyboard': {'evidence': [{'visible_text': 'Other title'}]}})
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    assert ctx.index['words'][0]['w'] == 'jadot'
    assert context({'words': []}) == ''
