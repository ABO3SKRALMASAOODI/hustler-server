import numpy as np
from first_film_sound import power200, bed_space


def test_tiny_spectral_peak_is_not_a_full_audible_event():
    rate = 16000
    t = np.arange(rate) / rate
    long = .1 * np.sin(2*np.pi*1000*t)
    tiny = long.copy(); tiny[int(.02*rate):] = 0
    assert power200(tiny, rate) < power200(long, rate) * .12
    sub = .4 * np.sin(2*np.pi*60*t)
    assert power200(sub, rate) < power200(long, rate) * .01


def test_music_dip_is_on_the_hit_and_returns_smoothly():
    song = np.ones((2000, 2), np.float32)
    out = bed_space(song, [{'event_f': 30, 'layer': 'hit'}], 30, 1000)
    np.testing.assert_allclose(song, 1)  # no mutation of cached arrangement
    assert out[1000, 0] == .5
    assert out[800, 0] == out[1500, 0] == 1
    assert np.max(np.abs(np.diff(out[:, 0]))) < .01


def test_chain_uses_real_timeline_events_and_one_gated_write(monkeypatch):
    from types import SimpleNamespace
    import agent_tools, music_gate
    from score import sound
    from first_film_sound import chain
    edl = {'keep': [[0, 10]], 'inserts': [], 'motion': [{'start': 1, 'end': 2}],
           'captions': [{'start': 3, 'end': 4}],
           'fx': [{'type': 'broll', 'start': 5, 'end': 6,
                   'source': {'asset_key': 'verified/thing.mp4'}}]}
    written = []
    ctx = SimpleNamespace(index={}, latest_edl=lambda: {'json': edl}, proxy_path=lambda: 'source.mp4',
                          apply_score=lambda value, why: written.append(value) or 'EDL v2')
    def design(l1, **kwargs):
        assert len([i for i in l1['items'] if i['k'] == 'word']) == 1
        assert l1['spine'] == [{'f': [0, 300], 'src': [0., 10.]}]
        assert kwargs['index']['first_film']['frame'] == '9:16'
        assert [(e['f'],e['kind']) for e in sound.l1_events(l1)] == [(30,'display'),(150,'picture'),(180,'picture')]
        return [{'id': 'cue1', 's': 'click_1', 'start_s': .9, 'event_f': 30, 'db': -2}], {}
    monkeypatch.setattr(sound, 'design', design)
    monkeypatch.setattr(sound, 'library', lambda: {'click_1': {}})
    monkeypatch.setattr(agent_tools, '_resolve_sfx', lambda *a: ({'storage_key': 'safe/project/click'}, None))
    granted = []
    monkeypatch.setattr(music_gate, 'grant_sfx', lambda c, k: granted.append(k))
    chain(ctx)
    assert granted == ['safe/project/click']
    assert written[0]['sfx'][0]['gain_db'] == -2
    assert 'sfx' not in edl
