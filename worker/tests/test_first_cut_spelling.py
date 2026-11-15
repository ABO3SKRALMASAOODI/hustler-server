from types import SimpleNamespace
from first_cut_spelling import apply, context


def test_renderer_uses_original_index_with_persisted_scoped_spelling():
    import copy
    from captions import transcript_words
    from timeline import Timeline
    from first_cut_spelling import persist
    original = {'words': [{'w': 'jadot', 't0': 1., 't1': 1.5},
                          {'w': 'jadot', 't0': 5., 't1': 5.5}],
                'visual_storyboard': {'evidence': [{'visible_text': 'Fatima Jakoet'}]}}
    # Reorder the two occurrences and speed up the source containing the name.
    edl = {'keep': [[4., 7.], [0., 3.]],
           'speed': [{'start': 0., 'end': 3., 'factor': 2.}],
           'captions': {'mode': 'from_transcript',
                        'corrections': [{'from': 'unrelated', 'to': 'retained'}]}}
    rows = [edl]
    def save(value, why):
        rows.append(copy.deepcopy(value))
        return 'EDL v2'
    ctx = SimpleNamespace(index=original, latest_edl=lambda: {'json': rows[-1]}, apply_score=save)
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    assert persist(ctx) == 'EDL v2'
    assert persist(ctx) is None
    written = rows[-1]
    result = transcript_words(written, original, Timeline(written['keep'], speed=written['speed']))
    assert [(w['w'], w['t0'], w['t1']) for w in result] == [
        ('Jakoet', 3.5, 3.75), ('jadot', 1., 1.5)]
    assert original['words'][0]['w'] == 'jadot'
    assert written['captions']['corrections'][0]['to'] == 'retained'
    assert len(edl['captions']['corrections']) == 1


def test_manual_captions_are_not_rewritten():
    from first_cut_spelling import persist
    ctx = SimpleNamespace(index={'caption_evidence': {'0': {}}},
                          latest_edl=lambda: {'json': {'captions': [{'text': 'Jakoet'}]}})
    assert persist(ctx) is None


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
