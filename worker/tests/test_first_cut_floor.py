import numpy as np
import pytest
from first_cut_floor import prefer_floor
from film_measure import numeric


def reading(cls='light_touch', shots=0, sounds=0, motion=0, steps=0):
    return {'class': cls, 'program_s': 10, 'shots_added': shots,
            'camera_moves_added': motion,
            'taste': {'sound_times': list(range(sounds)), 'off_edit_steps': steps}}


def test_before_style_wins_over_thin_fallback():
    assert prefer_floor(reading(), reading('edited', shots=2, sounds=3, motion=2))
    assert prefer_floor(reading(), reading(sounds=3, motion=1))


def test_unknown_evidence_and_extra_punches_never_win():
    assert not prefer_floor(reading(), reading('unknown', shots=30))
    assert not prefer_floor(reading(), reading('edited', steps=2))
    assert not prefer_floor(reading('edited', shots=3), reading('edited', sounds=20, motion=15))


@pytest.mark.parametrize('size', [2, 3, 5, 18, 101])
@pytest.mark.parametrize('mode', ['nearest', 'reflect'])
def test_signal_filters_match_fixed_meter(size, mode):
    ndimage = pytest.importorskip("scipy.ndimage")
    a = np.random.default_rng(48).normal(size=170)
    np.testing.assert_allclose(numeric.median_filter(a, size, mode), ndimage.median_filter(a, size, mode=mode))
    np.testing.assert_allclose(numeric.uniform_filter1d(a, size, mode), ndimage.uniform_filter1d(a, size, mode=mode), atol=1e-12)
    np.testing.assert_allclose(numeric.maximum_filter1d(a, size, mode), ndimage.maximum_filter1d(a, size, mode=mode))


def test_connected_pixels_do_not_join_diagonally():
    ndimage = pytest.importorskip("scipy.ndimage")
    a = np.eye(6, dtype=np.uint8)
    actual, n = numeric.label(a)
    expected, m = ndimage.label(a)
    assert n == m == 6
    np.testing.assert_array_equal(actual, expected)


def test_choice_builds_before_style_and_restores_candidate_after_failure(monkeypatch):
    from types import SimpleNamespace
    import first_cut, first_cut_floor as floor
    row = {'version': 2, 'json': {'keep': [[1, 10]], 'overlays': [{'kind': 'video', 'asset_key': 'baked-source-window'}]}}
    writes = []
    def apply(edl, reason):
        writes.append(edl); row.update(version=row['version']+1, json=edl)
        return 'EDL v' + str(row['version'])
    ctx = SimpleNamespace(latest_edl=lambda: row, apply_score=apply, director_version=2)
    monkeypatch.setattr(floor, 'measure', lambda *a: reading())
    monkeypatch.setattr(first_cut, '_render_cut', lambda *a: 'restored preview')
    def failed_baseline():
        raise RuntimeError('baseline media unavailable')
    result = floor.choose(ctx, None, 0, {'json': {'keep': [[0, 10]]}}, [], [],
                          {'how': 'director'}, 'candidate preview', failed_baseline)
    assert writes[0] == {'keep': [[0, 10]]}
    assert writes[-1]['keep'] == [[1, 10]]
    assert writes[-1]['overlays'][0]['asset_key'] == 'baked-source-window'
    assert result[-1] == 'restored preview'
    assert 'unavailable' in ctx.first_cut_phases['never_below']


def test_an_edited_label_does_not_waive_a_zero_picture_fallback():
    from first_cut_floor import needs_floor
    assert needs_floor(reading('edited', motion=4))
    assert not needs_floor(reading('edited', shots=3))
    assert prefer_floor(reading('edited', sounds=0, motion=4), reading('light_touch', sounds=3, motion=1))


def test_camera_motion_cannot_buy_lost_real_cuts_or_longer_holds():
    candidate = reading('edited', motion=0, steps=4)
    candidate['program_s'] = 15.44
    candidate['taste']['raw'] = {'kinds': [[t, 'new', .5] for t in (1, 3, 3.93, 5.93, 6.86, 7.6, 8.6, 10.53, 11.73, 13.66)]}
    fallback = reading('edited', motion=17)
    fallback['program_s'] = 137.
    fallback['taste']['raw'] = {'kinds': [[31., 'new', .5], [32.13, 'new', .5]]}
    assert not prefer_floor(candidate, fallback)
    # Equal density also cannot hide one long, static stretch.
    fallback['program_s'] = candidate['program_s']
    fallback['taste']['raw']['kinds'] = [[t, 'new', .5] for t in range(1, 11)]
    assert not prefer_floor(candidate, fallback)


def test_camera_motion_cannot_buy_lost_audible_effects():
    assert not prefer_floor(reading('edited', sounds=3), reading('edited', sounds=1, motion=9))


def test_better_real_cut_sequence_can_win_without_new_stock_shots():
    candidate, baseline = reading('edited'), reading('edited')
    candidate['taste']['raw'] = {'kinds': [[5, 'jump', .5]]}
    baseline['taste']['raw'] = {'kinds': [[t, 'jump', .5] for t in (2, 4, 6, 8)]}
    assert prefer_floor(candidate, baseline)


def test_shortening_does_not_buy_a_lost_picture_change():
    candidate, baseline = reading(), reading('edited')
    candidate['program_s'] = 7.47
    candidate['taste']['raw'] = {'kinds': [[3.533, 'new', -.016], [4.4, 'jump', .87], [6.733, 'step', 1.466]]}
    candidate['taste']['off_edit_steps'] = 1
    baseline['program_s'] = 3.42
    baseline['taste']['raw'] = {'kinds': [[1.467, 'jump', .815]]}
    assert not prefer_floor(candidate, baseline)


def test_one_jump_cannot_replace_one_distinct_subject_picture():
    candidate, baseline = reading(), reading('edited')
    candidate['program_s'] = 8.
    candidate['taste']['raw'] = {'kinds': [[5.467, 'new', .889]]}
    baseline['program_s'] = 3.42
    baseline['taste']['raw'] = {'kinds': [[1.467, 'jump', .815]]}
    assert not prefer_floor(candidate, baseline)


@pytest.mark.parametrize('department', ['shots', 'sounds'])
def test_shortening_does_not_buy_lost_added_pictures_or_sounds(department):
    candidate = reading(**{department: 3})
    baseline = reading('edited', **{department: 2})
    baseline['program_s'] = 4
    assert not prefer_floor(candidate, baseline)
