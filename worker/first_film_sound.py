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


def chain(ctx):
    """Give the old first-cut path the same sound designer on its real events.

    Translate timeline facts, not transcript keywords. Captions are excluded.
    Checked library recordings are resolved through the ordinary write gate.
    """
    import copy
    import agent_tools
    import music_gate
    import schemas
    import timeline
    from score import sound
    edl = copy.deepcopy(ctx.latest_edl()['json'])
    if edl.get('sfx') or getattr(ctx, 'director_version', None):
        return None
    fps = 30.0
    duration = float(schemas.program_duration(edl))
    if duration <= .1:
        return None
    items, spine = [], []
    for i, block in enumerate(timeline.program_blocks(edl)):
        frames = [round(block['out_start']*fps), round(block['out_end']*fps)]
        if block['kind'] == 'insert':
            items.append({'id': f'insert{i}', 'k': 'shot', 'f': frames, 'pic': f'insert:{i}'})
        else:
            src = [block['src_start'], block['src_end']]
            items.append({'id': f'take{i}', 'k': 'shot', 'f': frames, 'pic': 'main', 'src': src})
            spine.append({'f': frames, 'src': src})
    # Legacy cutaways live in fx, not in timeline inserts. Expose their
    # actual entry AND return cuts to the sound designer while retaining
    # the original voice spine underneath them.
    cutaways = [fx for fx in edl.get('fx') or [] if fx.get('type') == 'broll'
                and (fx.get('source') or {}).get('asset_key')]
    if cutaways:
        base, items = items, []
        bounds = {f for shot in base for f in shot['f']}
        for fx in cutaways:
            bounds.update([max(0, min(round(duration*fps), round(float(fx.get(k) or 0)*fps)))
                           for k in ('start', 'end')])
        bounds = sorted(bounds)
        for n, (a, b) in enumerate(zip(bounds, bounds[1:])):
            if b <= a:
                continue
            active = next((fx for fx in reversed(cutaways)
                           if float(fx.get('start') or 0)*fps <= (a+b)/2
                           < float(fx.get('end') or 0)*fps), None)
            if active:
                items.append({'id': f'broll{n}', 'k': 'shot', 'f': [a,b],
                              'pic': 'asset:' + active['source']['asset_key']})
                continue
            shot = next((shot for shot in base if shot['f'][0] <= a < shot['f'][1]), None)
            if shot:
                part = dict(shot, id=f"{shot['id']}.{n}", f=[a,b])
                if shot.get('src'):
                    start, end = shot['src']
                    scale = (end-start)/max(1, shot['f'][1]-shot['f'][0])
                    part['src'] = [start+(a-shot['f'][0])*scale,
                                   start+(b-shot['f'][0])*scale]
                items.append(part)
    for i, item in enumerate(edl.get('motion') or []):
        # Motion-caption templates are stored in captions, not this display list.
        a, b = float(item.get('start') or 0), float(item.get('end') or 0)
        if b > a:
            items.append({'id': f'display{i}', 'k': 'word', 'role': 'big',
                          'on': round(a*fps), 'f': [round(a*fps), round(b*fps)]})
    for i, item in enumerate(edl.get('overlays') or []):
        a = float(item.get('start') or 0)
        b = a + float(item.get('duration_s') or 0)
        if b > a:
            items.append({'id': f'overlay{i}', 'k': 'card', 'pic': f'overlay:{i}',
                          'w': 1, 'h': 1, 'f': [round(a*fps), round(b*fps)]})
    src = ctx.proxy_path()
    l1 = {'fps': fps, 'dur_f': round(duration*fps), 'spine': spine,
          'items': items, 'assets': {'main': {'abs': src}}, 'sound': []}
    index = dict(ctx.index or {}, first_film={'frame': '9:16'})
    sounds, report = sound.design(l1, src=src, index=index, bed=bool(edl.get('music')))
    placed = []
    for item in sounds:
        if item.get('s') not in sound.library():
            continue
        asset, error = agent_tools._resolve_sfx(ctx, 'sound:' + item['s'])
        if error or not asset:
            continue
        key = asset['storage_key']
        music_gate.grant_sfx(ctx, key)
        cue = {'id': item['id'], 'storage_key': key, 'at': round(item['start_s'], 3),
               'gain_db': item['db'], 'purpose': f"first-film {item.get('for') or 'picture event'}"}
        if item.get('trim_s'):
            cue['offset_s'] = item['trim_s']
        if item.get('dur_s'):
            cue['dur_s'] = item['dur_s']
        placed.append(cue)
    if placed:
        edl['sfx'] = placed
        result = ctx.apply_score(edl, 'sound design on the first film’s picture and graphic events')
        report['write'] = str(result)[:120]
    report['placed_on_chain'] = len(placed)
    phases = dict(getattr(ctx, 'first_cut_phases', None) or {})
    phases['chain_sound'] = report
    ctx.first_cut_phases = phases
    return report
