from types import SimpleNamespace

import numpy as np
import pytest

from score import brief, compiler, scout, taste, transcript_confidence
from tests.test_score_compiler import talk_index
from tests.test_score_taste import beats_over, film


@pytest.mark.parametrize('measured,expected_low', [(0.95, False), (0.5, True), (0.0, True), (None, False)])
def test_corrected_words_reach_brief_scout_and_caption_compilation(measured, expected_low):
    idx = talk_index(5)
    for w in idx['words']:
        w['confidence'] = measured
    # transcript_verify's real output contract: a replacement no longer
    # carries the ASR score that belonged to the original token.
    idx['words'][0].update(w='corrected', confidence=None)
    per = {'dur': 5., 'W': 1080, 'H': 1920, 'shots': idx['shots']}
    text = '\n'.join(brief.words_part('', idx, per, np.zeros(brief.SR * 5), None))
    assert 'corrected' in text
    assert 'corrected?' not in scout.user_text(idx, {}, per)
    assert brief.doubtful_words(idx) is expected_low
    ctx = SimpleNamespace(index=idx, words=idx['words'], wf_any=True)
    assert taste.captions_off(ctx) is expected_low
    assert taste.voiceless(ctx) is expected_low
    score = film(beats_over(idx, 1, lambda k, a, z: {'pic': 'main', 'type': f'{a}-{z}'}))
    result = compiler.compile_score(score, index=idx)
    assert not compiler.validate_l1(result.l1)
    assert bool(result.meter['captions']) is (not expected_low)


def test_unknown_scores_do_not_dilute_measured_low_confidence():
    words = [{'confidence': None}, {}, {'confidence': .5}]
    assert transcript_confidence.mean(words) == .5
    assert transcript_confidence.low_mean(words)
    assert transcript_confidence.mean(words[:2]) is None
