"""Make first-film cues audible as 200 ms events over the finished programme.

A maximum in one 20 ms FFT band is not a perceptual level: a tiny bright
click could pass that test while sitting 15 dB under the voice. Measure the
recording's body and leave space in the music at its audible attack.
"""
import math
import numpy as np

# Reference-led hierarchy, measured over the ear's transient integration
# window. A counter still sits below an accent; all retain their own timbre.
RELATIVE_DB = {'hero': 0.0, 'accent': -2.0, 'body': -3.0, 'tick': -6.0}


def power200(signal, rate):
    x = np.asarray(signal, np.float32).reshape(-1)
    if not len(x):
        return 0.0
    # Phone-speaker range; a sub-only impact cannot satisfy this test.
    bins = np.fft.rfftfreq(len(x), 1 / rate)
    spectrum = np.fft.rfft(x)
    spectrum[(bins < 150) | (bins > 6500)] = 0
    x = np.fft.irfft(spectrum, len(x))
    n = max(1, int(.2 * rate))
    if len(x) < n:
        return float(np.sum(x*x) / n)
    sums = np.concatenate(([0.0], np.cumsum(x*x)))
    return float(np.max((sums[n:] - sums[:-n]) / n))


def level(sounds, voice, fps, shift_db=0.0):
    from score import sound
    out, evidence = [], []
    v = np.asarray(voice, np.float32).reshape(-1) * 10 ** (shift_db / 20)
    for item in sounds:
        it = dict(item)
        if it.get('s') not in sound.library() or it.get('layer') == 'lead':
            out.append(it)
            continue
        raw = sound.sample_pcm(it['s'])
        raw = raw[int(round(float(it.get('trim_s') or 0) * sound.AR)):]
        hit = float(it.get('event_f') or 0) / fps
        a, b = max(0, int((hit - 1.5) * sound.AR)), min(len(v), int((hit + 1.5) * sound.AR))
        vp, sp = power200(v[a:b], sound.AR), power200(raw, sound.AR)
        if sp <= 1e-12:
            out.append(it)
            continue
        # A quiet source gets the nominal dialogue reference, not a huge
        # boost from matching an unmeasurable silence or noise floor.
        voice_db = max(-26.0, min(-12.0, 10 * math.log10(max(vp, 1e-12))))
        target = voice_db + RELATIVE_DB.get(it.get('tier'), -3.0)
        wanted = target - 10 * math.log10(sp)
        peak = float(np.abs(raw).max()) if len(raw) else 0
        headroom = -6 - 20 * math.log10(max(peak, 1e-9))
        gain = round(min(6.0, headroom, wanted), 1)
        # Explicitly softer authored cues stay soft unless they are part of
        # the automatic hierarchy. All generated body/tick cues are levelled.
        if it.get('tier'):
            it['db'] = gain
        evidence.append({'id': it.get('id'), 'gain_db': it.get('db'),
                         'body_over_voice_db': round(10 * math.log10(sp) + float(it['db']) - voice_db, 1),
                         'method': '200ms phone-band power; pre-master'})
        out.append(it)
    return out, evidence


def bed_space(song, sounds, fps, rate):
    """Ease a short 6 dB bed dip at each cue attack, not just hero impacts."""
    out = np.asarray(song, np.float32).copy()
    curve = np.ones(len(out), np.float32)
    for item in sounds:
        if item.get('layer') == 'lead':
            continue
        hit = float(item.get('event_f') or 0) / fps
        a, h, b = max(0, int((hit - .08)*rate)), max(0, int(hit*rate)), min(len(out), int((hit + .28)*rate))
        h = min(h, b)
        if b <= a:
            continue
        env = np.concatenate((np.linspace(1, .5, h-a, endpoint=False),
                              .5 + .5 * (1-np.cos(np.linspace(0, np.pi, b-h))) / 2))
        curve[a:b] = np.minimum(curve[a:b], env)
    out *= curve[:, None] if out.ndim == 2 else curve
    return out
