from types import SimpleNamespace
import wave

import pytest

import transcript_verify as tv
from schemas import Word


def words(text='These seats will be up for grapes. Republicans currently control Congress.'):
    return [Word(w=w, t0=i * .5, t1=i * .5 + .4, speaker=2, confidence=.966)
            for i, w in enumerate(text.split())]


HEARD = 'These seats will be up for grabs. Republicans currently control Congress.'


def corrected(source, heard):
    return tv.apply_votes(source, {i: [w] for i, w in tv.aligned_votes(source, heard).items()})


def test_customer_mishearing_changes_only_word_and_confidence():
    source = words()
    output, changes = corrected(source, HEARD)
    assert output[6].w == 'grabs.'
    assert output[6].confidence is None
    assert source[6].w == 'grapes.'
    for before, after in zip(source, output):
        assert (before.t0, before.t1, before.speaker, before.filler) == (
            after.t0, after.t1, after.speaker, after.filler)
    assert changes == [{'t0': 3., 'before': 'grapes.', 'after': 'grabs.'}]


@pytest.mark.parametrize('token,replacement,extra', [
    ('grapes', 'grabs', {'confidence': .98}),
    ('grapes', 'grabs', {'confidence': None}),
    ('grapes', 'grabs', {'filler': True}),
    ('Grapes', 'grabs', {}),
    ('grapes', 'Grabs', {}),
    ('35', '36', {}),
    ('fifteen', 'fifty', {}),
    ('not', 'now', {}),
    ("can't", 'can', {}),
    ('blue', 'red', {}),
])
def test_preserves_unsupported_or_meaning_changing_substitutions(token, replacement, extra):
    source = words(f'the next {token} should stay')
    source[2] = source[2].model_copy(update=extra)
    assert corrected(source, f'the next {replacement} should stay')[0] == source


@pytest.mark.parametrize('heard', [
    'These seats will be up for grabs.',  # no right anchor
    'for grabs. Republicans currently control Congress.',  # one left anchor
    'These seats will be available soon. Republicans currently control Congress.',
    'These seats will be up for grabs tomorrow. Republicans currently control Congress.',
])
def test_rewrites_insertions_and_unanchored_changes_are_not_accepted(heard):
    source = words()
    assert corrected(source, heard)[0] == source


def test_punctuation_and_multitoken_word_elsewhere_do_not_disable_alignment():
    source = words('U.S. seats will be up for grapes. Republicans currently control Congress.')
    result, changes = corrected(source, HEARD.replace('These', 'U.S.'))
    assert result[6].w == 'grabs.'
    assert len(changes) == 1


def test_overlapping_recognizers_disagree_then_original_is_retained():
    source = words()
    assert tv.apply_votes(source, {6: ['grabs', 'grapes']}) == (source, [])


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(tv.config, 'TRANSCRIPT_VERIFY_ENABLED', True)
    monkeypatch.setattr(tv.config, 'OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(tv.config, 'OPENAI_BASE_URL', 'https://api.openai.com/v1')


def audio_file(tmp_path, seconds=8):
    path = str(tmp_path / 'audio.wav')
    with wave.open(path, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * int(16000 * seconds))
    return path


def install_client(monkeypatch, create):
    def options(**kwargs):
        assert kwargs == {'timeout': 25, 'max_retries': 0}
        return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr(tv.llm, 'client', lambda: SimpleNamespace(with_options=options))


def test_audio_call_is_unprompted_and_pipeline_keeps_timing(enabled, monkeypatch, tmp_path):
    calls = []
    records = []
    def recognize(**kwargs):
        assert set(kwargs) == {'model', 'file', 'extra_body'}
        assert kwargs['model'] == 'gpt-transcribe'
        assert kwargs['extra_body'] == {'languages': ['en']}
        with wave.open(kwargs['file'], 'rb') as audio:
            assert audio.getnframes() == 8 * 16000
        calls.append(True)
        return SimpleNamespace(text=HEARD)
    install_client(monkeypatch, recognize)
    monkeypatch.setattr(tv.llm, 'record', lambda *a: records.append(a))
    path = audio_file(tmp_path)
    import indexer
    monkeypatch.setattr(indexer.transcribe, 'transcribe', lambda *a, **k: (words(), 'en'))
    output, sentences, language = indexer._transcribe_wav(123, path, 8., [])
    assert output[6].w == 'grabs.' and output[6].t0 == 3.
    assert language == 'en' and 'grabs' in ' '.join(s.text for s in sentences)
    assert calls == [True]
    assert records[0][2]['provider_cost_usd'] == .0006
    assert records[1][0] == 'transcript_corrections'


def test_provider_outage_is_fail_open_and_stops_queued_calls(enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(tv, 'WINDOW_S', 5)
    calls = []
    def unavailable(**kwargs):
        calls.append(True)
        raise TimeoutError('provider unavailable')
    install_client(monkeypatch, unavailable)
    source = words(' '.join(['the words for grapes should stay here'] * 6))
    warnings = []
    assert tv.verify(source, audio_file(tmp_path, 24), 'en', warnings) == source
    assert 1 <= len(calls) <= 2
    assert len(warnings) == 1


@pytest.mark.parametrize('setting,value,language', [
    ('OPENAI_API_KEY', '', 'en'),
    ('OPENAI_BASE_URL', 'https://another-provider.example/v1', 'en'),
    ('TRANSCRIPT_VERIFY_ENABLED', False, 'en'),
    ('TRANSCRIPT_VERIFY_ENABLED', True, 'ru'),
    ('TRANSCRIPT_VERIFY_ENABLED', True, None),
])
def test_disabled_or_unsupported_context_never_reads_audio(enabled, monkeypatch, setting, value, language):
    monkeypatch.setattr(tv.config, setting, value)
    source = words()
    assert tv.verify(source, '/nonexistent.wav', language) is source
