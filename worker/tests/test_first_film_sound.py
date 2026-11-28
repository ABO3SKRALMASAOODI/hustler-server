import numpy as np
from first_film_sound import peak_power, bed_space


def test_tiny_spectral_peak_is_not_a_full_audible_event():
    rate = 16000
    t = np.arange(rate) / rate
    long = .1 * np.sin(2*np.pi*1000*t)
    tiny = long.copy(); tiny[int(.02*rate):] = 0
    assert peak_power(tiny, rate) < peak_power(long, rate) * .12
    sub = .4 * np.sin(2*np.pi*60*t)
    assert peak_power(sub, rate) < peak_power(long, rate) * .01


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


def test_silent_upload_uses_nominal_reference_for_audible_cue(monkeypatch):
    from score import sound
    from first_film_sound import level
    monkeypatch.setattr(sound, 'library', lambda: {'whoosh': {}})
    t = np.arange(sound.AR)/sound.AR
    monkeypatch.setattr(sound, 'sample_pcm', lambda sid: .1*np.sin(2*np.pi*1000*t))
    cue={'id':'event','s':'whoosh','tier':'body','event_f':30,'db':-20}
    sounds, evidence=level([cue],None,30)
    assert abs(evidence[0]['peak50_over_voice400_db']+3) < .1
    assert sounds[0]['db'] > cue['db']


def test_short_recording_gets_reference_peak_level_without_clipping(monkeypatch):
    from score import sound
    from first_film_sound import level
    t = np.arange(sound.AR)/sound.AR
    # A real-recording-shaped short attack against sustained dialogue.
    raw = .4*np.sin(2*np.pi*1200*t)*np.exp(-t/0.008)
    voice = .35*np.sin(2*np.pi*800*t)
    monkeypatch.setattr(sound, 'library', lambda: {'hit': {}})
    monkeypatch.setattr(sound, 'sample_pcm', lambda sid: raw)
    cue = {'id':'hit','s':'hit','tier':'body','event_f':15,'db':-20}
    result, proof = level([cue],voice,30)
    assert abs(proof[0]['peak50_over_voice400_db']+3)<.15
    assert np.max(np.abs(raw))*10**(result[0]['db']/20) < .9
    assert result[0]['db'] > -6-20*np.log10(np.max(np.abs(raw)))


def test_a_cue_in_a_breath_keeps_nearby_dialogue_reference(monkeypatch):
    from score import sound
    from first_film_sound import level
    t = np.arange(3*sound.AR)/sound.AR
    voice = .2*np.sin(2*np.pi*900*t)
    gap = voice.copy(); gap[sound.AR:2*sound.AR] = 0
    raw = .1*np.sin(2*np.pi*1200*t[:sound.AR])
    monkeypatch.setattr(sound, 'library', lambda: {'cue': {}})
    monkeypatch.setattr(sound, 'sample_pcm', lambda sid: raw)
    cue={'id':'cue','s':'cue','tier':'body','event_f':45,'db':-20}
    on_voice, _=level([cue],voice,30)
    in_breath, _=level([cue],gap,30)
    assert in_breath[0]['db'] == on_voice[0]['db']


def test_music_only_source_and_separate_bed_do_not_bury_picture_cues(monkeypatch):
    from score import sound
    from first_film_sound import level
    t = np.arange(3*sound.AR)/sound.AR
    source = .07*np.sin(2*np.pi*700*t)
    raw = .15*np.sin(2*np.pi*1300*t[:sound.AR])
    monkeypatch.setattr(sound, 'library', lambda: {'cue': {}})
    monkeypatch.setattr(sound, 'sample_pcm', lambda sid: raw)
    cues = [{'id': tier, 's': 'cue', 'tier': tier, 'event_f': 45, 'db': -30}
            for tier in ('hero', 'body', 'tick')]
    music, proof = level(cues, source, 30, speaks=False)
    separate, _ = level(cues, None, 30, bed=source)
    assert [x['db'] for x in music] == [x['db'] for x in separate]
    assert [x['peak50_over_background_db'] for x in proof] == [10., 7., 6.]
    assert music[0]['db']-music[-1]['db'] == 4
    # The ordinary dialogue-relative path retains its softer body cue.
    dialogue, _ = level(cues, source, 30)
    assert dialogue[1]['db'] < music[1]['db']-8


def test_opening_impact_keeps_its_attack_and_tail(monkeypatch):
    from score import sound
    from first_film_sound import level
    t = np.arange(sound.AR)/sound.AR
    raw = .3*np.sin(2*np.pi*1000*t)*np.exp(-((t-.5)/.012)**2)
    monkeypatch.setattr(sound, 'library', lambda: {'hit': {'hit_s': .5}})
    monkeypatch.setattr(sound, 'sample_pcm', lambda sid: raw)
    cue = {'id': 'open', 's': 'hit', 'tier': 'hero', 'event_f': 0,
           'start_s': 0., 'peak_s': .513, 'trim_s': .513, 'dur_s': .3, 'db': -20}
    out, _ = level([cue], None, 30)
    assert out[0]['trim_s'] == .475
    assert abs(out[0]['dur_s']+out[0]['trim_s']-.813)<1e-6
    assert out[0]['start_s'] == 0
    assert cue['trim_s'] == .513


def test_chain_uses_validated_source_cuts_and_keeps_source_bed_detection(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import agent_tools, music_gate
    from score import sound, preparation
    from first_film_sound import chain
    edl = {'keep': [[0, 10]]}
    index = {'director_preparation': {'version': 1},
             'video': {'width': 720, 'height': 1280},
             'shots': [{'start': 0, 'end': 10}]}
    ctx = SimpleNamespace(index=index, duration=10, workdir=str(tmp_path),
                          latest_edl=lambda: {'json': edl}, proxy_path=lambda: 'source.mp4')
    per = {'shots': [{'t': [i, i+2]} for i in range(0, 10, 2)]}
    def restore(idx, src, work):
        assert idx['first_film']['frame'] == '9:16'
        assert src == 'source.mp4'
        return idx, per
    monkeypatch.setattr(preparation, 'restore', restore)
    def design(l1, **kwargs):
        assert kwargs['bed'] is None
        assert kwargs['level_source'] is True
        events = sound.l1_events(l1, sound._kshots(kwargs['perc'], kwargs['index']))
        assert [e['f'] for e in events] == [60, 120, 180, 240]
        return [], {}
    monkeypatch.setattr(sound, 'design', design)
    assert chain(ctx)['placed_on_chain'] == 0


def test_source_reference_matches_renderer_local_gain(tmp_path, monkeypatch):
    import subprocess
    import dialogue_level
    from first_film_sound import renderer_source
    # A quiet opening followed by louder music: a single integrated shift
    # underpredicts the rendered opening, which buried the p5575 cue.
    rate = 16000
    t = np.arange(6*rate)/rate
    mono = np.sin(2*np.pi*700*t)*np.where(t < 2, .004, .04)
    stereo = np.stack([mono, mono*.8], axis=1).astype(np.float32)
    out, knots = renderer_source(stereo, rate)
    assert knots[0][1] > knots[-1][1] + 4
    # Compare against the renderer's actual volume filter, including its
    # audio-frame evaluation cadence, not another copy of interpolation.
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-f', 'f32le',
                                  '-ar', str(rate), '-ac', '2', '-i', 'pipe:0',
                                  '-af', dialogue_level.volume_filter(knots),
                                  '-f', 'f32le', '-'], input=stereo.tobytes())
    rendered = np.frombuffer(raw, np.float32).reshape(-1, 2).mean(axis=1)
    for a in [.4, 1.4, 3., 5.]:
        lo, hi = round(a*rate), round((a+.2)*rate)
        ratio = np.mean(out[lo:hi]**2)/np.mean(rendered[lo:hi]**2)
        assert abs(10*np.log10(ratio)) < .4


def test_silent_source_has_no_predicted_gain():
    from first_film_sound import renderer_source
    out, knots = renderer_source(np.zeros((32000, 2), np.float32), 16000)
    assert knots == []
    assert not out.any()


def test_director_design_uses_rendered_source_level_from_loaded_index(tmp_path, monkeypatch):
    import json
    import wave
    from score import sound
    rate = sound.AR
    t = np.arange(6 * rate) / rate
    mono = np.sin(2*np.pi*700*t) * np.where(t < 2, .004, .04)
    stereo = np.stack([mono, mono*.8], axis=1)
    source = tmp_path / 'source.wav'
    with wave.open(str(source), 'wb') as handle:
        handle.setnchannels(2); handle.setsampwidth(2); handle.setframerate(rate)
        handle.writeframes((stereo*32767).astype('<i2').tobytes())
    index = {'first_film': {'frame': '9:16'}, 'words': [], 'video': {'duration': 6}}
    (tmp_path / 'index.json').write_text(json.dumps(index))
    l1 = {'fps': 30, 'dur_f': 180, 'items': [], 'sound': [],
          'spine': [{'f': [0, 180], 'src': [0., 6.]}]}
    observed = []
    def plan(events, ctx):
        observed.append(ctx['voice_shift_db'])
        return [], {}
    monkeypatch.setattr(sound, 'plan_sounds', plan)
    _, report = sound.design(l1, work=str(tmp_path), src=str(source))
    knots = report['renderer_source_gain_knots']
    assert knots[0][1] > knots[-1][1] + 4
    assert observed[-1] == 0  # no second global shift after the renderer curve
    # The legacy caller can explicitly select natural mastering.
    _, natural = sound.design(l1, work=str(tmp_path), src=str(source), level_source=False)
    assert 'renderer_source_gain_knots' not in natural
