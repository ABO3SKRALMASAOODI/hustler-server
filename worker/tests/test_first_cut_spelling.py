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


def test_scout_reads_small_card_missing_from_summary(monkeypatch):
    from PIL import Image
    from first_cut_spelling import content
    from score import boards
    ctx = SimpleNamespace(index={'words': [{'w': 'Jadot', 't0': 1}],
                                  'shots': [{'start': 0, 'end': 8}]},
                          duration=8, proxy_path=lambda: '/source.mp4')
    seen = []
    def grab(path, at, width):
        seen.append((path, at, width))
        return Image.new('RGB', (1280, 720))
    monkeypatch.setattr(boards, 'grab', grab)
    parts = content(ctx, 'Transcript')
    assert seen == [('/source.mp4', 4., 1280)]
    assert 'word_ids' in parts[0]['text']
    assert parts[-1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    assert ctx.index['words'][0]['w'] == 'Jakoet'
    assert ctx.index['caption_evidence']['0']['read'] == 'Fatima Jakoet'


def test_missing_frame_does_not_authorize_unseen_spelling(monkeypatch):
    from first_cut_spelling import content
    from score import boards
    ctx = SimpleNamespace(index={'words': [{'w': 'Jadot'}]}, duration=8,
                          proxy_path=lambda: '/source.mp4', first_cut_spelling_frames=True)
    monkeypatch.setattr(boards, 'grab', lambda *a, **kw: None)
    assert content(ctx, 'Transcript') == 'Transcript'
    assert not ctx.first_cut_spelling_frames
    apply(ctx, {'fixes': [{'w': 0, 't': 'Jakoet', 'read': 'Fatima Jakoet', 'evidence': 'screen'}]})
    assert ctx.index['words'][0]['w'] == 'Jadot'
