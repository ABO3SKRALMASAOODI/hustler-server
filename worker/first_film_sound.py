"""Level first-film cues on the reference's transient and voice windows.

A maximum in one 20 ms FFT band is not a perceptual level: a tiny bright
click could pass that test while sitting 15 dB under the voice. The reference
uses a 50 ms cue peak against 400 ms of voice, not a 200 ms cue average.
"""
import math
import numpy as np

# Reference-led hierarchy, measured over the ear's transient integration
# window. A counter still sits below an accent; all retain their own timbre.
RELATIVE_DB = {'hero': 0.0, 'accent': -2.0, 'body': -3.0, 'tick': -6.0}
BACKGROUND_DB = {'hero': 10.0, 'accent': 8.0, 'body': 7.0, 'tick': 6.0}


def renderer_source(signal, rate):
    """Predict the renderer's source leveller, before the cue mix.

    Its local gain can differ from a film-wide loudness shift by >10 dB.
    Measure stereo exactly as the renderer does, then use the same knots.
    The temporary metadata belongs to the worker's run-local TMPDIR.
    """
    import pathlib
    import subprocess
    import tempfile
    import dialogue_level
    x = np.asarray(signal, np.float32)
    if x.ndim == 1:
        x = x[:, None]
    with tempfile.TemporaryDirectory(prefix='first-film-level-') as temp:
        path = pathlib.Path(temp) / 'levels.txt'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(rate),
                        '-ac', str(x.shape[1]), '-i', 'pipe:0', '-af',
                        dialogue_level.probe_chain(str(path)), '-f', 'null', '-'],
                       input=x.tobytes(), capture_output=True, check=True, timeout=20)
        knots = dialogue_level.gain_knots(dialogue_level.parse_probe(path.read_text()))
    if not knots:
        return x.mean(axis=1), []
    ts, gains = zip(*knots)
    gain = np.interp(np.arange(len(x)) / rate, ts, gains)
    return x.mean(axis=1) * 10 ** (gain / 20), knots


def peak_power(signal, rate, window_s=.2):
    x = np.asarray(signal, np.float32).reshape(-1)
    if not len(x):
        return 0.0
    # Phone-speaker range; a sub-only impact cannot satisfy this test.
    bins = np.fft.rfftfreq(len(x), 1 / rate)
    spectrum = np.fft.rfft(x)
    spectrum[(bins < 150) | (bins > 6500)] = 0
    x = np.fft.irfft(spectrum, len(x))
    n = max(1, int(window_s * rate))
    if len(x) < n:
        return float(np.sum(x*x) / n)
    sums = np.concatenate(([0.0], np.cumsum(x*x)))
    return float(np.max((sums[n:] - sums[:-n]) / n))


def background_power(signal, rate):
    """Typical 50 ms background energy, excluding sparse source transients."""
    x = np.asarray(signal, np.float32).reshape(-1)
    n = max(1, round(.05 * rate))
    count = len(x) // n
    if not count:
        return 0.0
    return float(np.median(np.mean(x[:count*n].reshape(count, n)**2, axis=1)))


def level(sounds, voice, fps, shift_db=0.0, *, speaks=True, bed=None):
    from score import sound
    out, evidence = [], []
    v = (np.asarray(voice, np.float32).reshape(-1) if voice is not None else np.zeros(0, np.float32)) * 10 ** (shift_db / 20)
    for item in sounds:
        it = dict(item)
        if it.get('s') not in sound.library() or it.get('layer') == 'lead':
            out.append(it)
            continue
        raw = sound.sample_pcm(it['s'])
        # A cue at frame zero cannot lead the picture. The former negative
        # start trim cut past its attack, leaving only the impact's tail.
        if int(it.get('event_f') or 0) == 0 and it.get('trim_s'):
            meta = sound.library()[it['s']]
            attack = float(meta.get('hit_s') or meta.get('peak_s') or 0)
            trim = max(0.0, attack - .025)
            if trim < float(it['trim_s']):
                if it.get('dur_s') is not None:
                    it['dur_s'] += float(it['trim_s']) - trim
                it['trim_s'] = round(trim, 4)
        raw = raw[int(round(float(it.get('trim_s') or 0) * sound.AR)):]
        if it.get('dur_s') is not None:
            raw = raw[:max(0, int(round(float(it['dur_s']) * sound.AR)))]
        hit = float(it.get('event_f') or 0) / fps
        # Use nearby dialogue, including when the cue lands in a breath.
        # Matching only the silence at the exact hit made gap cues disappear.
        a, b = max(0, int((hit - 1.5) * sound.AR)), min(len(v), int((hit + 1.5) * sound.AR))
        vp = peak_power(v[a:b], sound.AR, min(.4, max(1, b-a)/sound.AR))
        # Level the transient that actually lands on the picture. The
        # recording's loudest 50 ms can be a second, unrelated hit later.
        peak_s = float(sound.library()[it["s"]].get("hit_s") or 0)
        pos = max(0, round((peak_s - float(it.get("trim_s") or 0)) * sound.AR))
        transient = raw[max(0, pos - sound.AR//12):pos + sound.AR//8]
        sp = peak_power(transient, sound.AR, .05)
        if sp <= 1e-12:
            out.append(it)
            continue
        # A quiet source gets the nominal dialogue reference, not a huge
        # boost from matching an unmeasurable silence or noise floor.
        voice_db = (-20.0 if vp <= 1e-8 else
                    max(-26.0, min(-12.0, 10 * math.log10(vp))))
        target = voice_db + RELATIVE_DB.get(it.get('tier'), -3.0)
        # A music-only upload is a background, not a dialogue reference.
        # Matching a body cue below that source makes it disappear. Likewise
        # account for a separately arranged bed on talking films. Keep the
        # hero/body/tick hierarchy; this is component gain, never a count.
        h = int(hit * sound.AR)
        attack = slice(max(0, h - sound.AR//10), h + sound.AR//5)
        background = 0.0
        it.pop("source_duck_db", None)
        it.pop("music_duck_db", None)
        if not speaks:
            # Masking is local to the attack. A loud beat on this frame
            # disappears in a three-second median of a quieter passage.
            background += max(background_power(v[a:b], sound.AR), peak_power(v[attack], sound.AR, .05))
        bed_power = 0.
        if bed is not None:
            song = np.asarray(bed)
            bed_power = max(background_power(song[a:max(a, int((hit+1.5)*sound.AR))], sound.AR),
                            peak_power(song[attack], sound.AR, .05))
            background += bed_power
        background_db = 10 * math.log10(background) if background > 1e-8 else None
        if background_db is not None:
            target = max(target, background_db + BACKGROUND_DB.get(it.get('tier'), 7.0))
        wanted = target - 10 * math.log10(sp)
        peak = float(np.abs(raw).max()) if len(raw) else 0
        # A blanket -6 dB sample-peak ceiling left short, high-crest real
        # recordings 7-13 dB under dialogue, regardless of the target. Keep
        # each recording below full scale; the ordinary float mix/master
        # owns the combined peak. Do not clip or synthesize the recording.
        headroom = -1 - 20 * math.log10(max(peak, 1e-9))
        gain = round(min(12.0, headroom, max(float(it.get('db', -30)), wanted)), 1)
        # Explicitly softer authored cues stay soft unless they are part of
        # the automatic hierarchy. All generated body/tick cues are levelled.
        if it.get('tier'):
            it['db'] = gain
        if background_db is not None:
            actual = 10 * math.log10(sp) + float(it["db"])
            room = max(0., background_db + BACKGROUND_DB.get(it.get("tier"), 7.) - actual)
            if room > .5:
                depth = round(min(room, 14.), 1)
                if not speaks and background > bed_power:
                    it["source_duck_db"] = depth
                if bed_power:
                    it["music_duck_db"] = depth
        evidence.append({'id': it.get('id'), 'gain_db': it.get('db'),
                         'peak50_over_voice400_db': round(10 * math.log10(sp) + float(it['db']) - voice_db, 1),
                         'target_over_voice_db': RELATIVE_DB.get(it.get('tier'), -3.0),
                         'headroom_limited': wanted > headroom,
                         'background_db': background_db,
                         'peak50_over_background_db': (round(10 * math.log10(sp) + float(it['db']) - background_db, 1) if background_db is not None else None),
                         'method': '50ms phone-band cue peak / nearby 400ms voice; pre-master, not a final-mix audible count'})
        out.append(it)
    return out, evidence


def recorded_music_trim(proof):
    """Seat the upload's song under the placed accents before mastering.

    A 90 ms source dip makes room at the attack but leaves the surrounding
    musical phrase at full level. Once the mix hits the master ceiling a
    louder click no longer buys separation. Reserve summing headroom on the
    song bus, using the weakest measured cue-to-background contrast.
    """
    contrasts = [row["peak50_over_background_db"] for row in proof
                 if row.get("peak50_over_background_db") is not None]
    if not contrasts:
        return 0.
    # Two equal peaks can sum 6 dB above either component. Allow that
    # headroom in addition to the 6 dB audible transient over the phrase.
    return round(max(-12., min(0., min(contrasts) - 12.)), 1)


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


def rendered_bed(path, track, reference, duration):
    """Identify the placed music by matching two passages against the encoded mix."""
    from score import sound
    import cv2
    a, b = float(track.get("start") or 0), min(float(track.get("end") or duration), duration)
    if not reference or b-a < .5 or track.get("mute"):
        return False
    rate = sound.AR
    length = min(.4, (b-a)/4)
    for u in (.2, .7):
        t = a + (b-a-length)*u
        offset = float(track.get("offset_s") or 0) + t-a
        # These short reads stay run-local; no extra media fetch or separation pass.
        ref = sound.pcm(reference, rate, 1, t0=offset, dur=length)
        actual = sound.pcm(path, rate, 1, t0=max(0., t-.04), dur=length+.08)
        if len(ref) < .1*rate or len(actual) < len(ref) or np.mean(ref**2) < 1e-9:
            return False
        corr = cv2.matchTemplate(actual, ref, cv2.TM_CCORR_NORMED)
        k = int(corr.argmax())
        gain = float(np.sum(actual[k:k+len(ref)]*ref) / np.sum(ref**2))
        if float(corr[k, 0]) < .25 or np.mean((gain*ref)**2) < 1e-6:
            return False
    return True


def rendered_accents(path, cues):
    """Match library attacks to the delivered audio, including the first sample."""
    import subprocess
    import sound_library
    import cv2
    from score import sound
    rate = sound.AR
    found = []
    for cue in cues:
        sid = sound_library.id_for_key(cue["storage_key"])
        if not sid:
            continue
        at = float(cue.get("at") or 0)
        offset = float(cue.get("offset_s") or 0)
        start = max(0., sound_library.hit_at(cue)-at-.04)
        length = min(.12, float(cue.get("dur_s") or (start+.12))-start)
        if length < .05:
            continue
        ref = sound.sample_pcm(sid)[round((offset+start)*rate):round((offset+start+length)*rate)]
        if len(ref) < rate//20 or np.mean(ref**2) < 1e-8:
            continue
        lo = max(0., at+start-.045)
        seek = ["-ss", str(lo)] if lo > 0 else []
        cmd = ["ffmpeg", "-v", "error", "-nostdin", *seek, "-i", path,
               "-t", str(length+.09), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
        raw = np.frombuffer(subprocess.check_output(cmd, timeout=8), np.float32)
        if len(raw) < len(ref):
            continue
        ref = np.asarray(ref, np.float32).reshape(-1, 1)
        corr = cv2.matchTemplate(raw.reshape(-1, 1), ref, cv2.TM_CCORR_NORMED)
        k = int(corr.argmax())
        if corr[k, 0] < .75:
            continue
        x = raw[k:k+len(ref)]
        r = ref[:, 0]
        fitted = r * (np.dot(x, r) / np.dot(r, r))
        power = float(np.mean(fitted**2))
        over = 10 * math.log10(max(power, 1e-12) / max(float(np.mean((x-fitted)**2)), 1e-12))
        if power > 1e-5 and over >= 3:
            found.append({"role": sound_library.get(sid)["role"], "at": sound_library.hit_at(cue),
                          "over_residual_db": round(over, 1)})
    return found


def delivery_music_windows(music, sounds, paths):
    """Compare the decoded, gained recordings at the mixer input, on their actual clocks."""
    import subprocess
    import sound_library
    rate = 12000
    def decode(path, start, length):
        seek = ["-ss", f"{start:.6f}"] if start > 0 else []
        cmd = ["ffmpeg", "-v", "error", "-nostdin", *seek, "-i", path, "-t", f"{length:.6f}", "-vn", "-ac", "1",
               "-ar", str(rate), "-f", "f32le", "-"]
        return np.frombuffer(subprocess.check_output(cmd, timeout=10), np.float32)

    hits = []
    for _, cue, _ in sounds:
        sid = sound_library.id_for_key(cue["storage_key"])
        if not sid:
            continue
        at = sound_library.hit_at(cue)
        pos = max(0., at-float(cue.get("at") or 0))
        left = max(0., pos-.06)
        length = min(pos+.12, float(cue.get("dur_s") or (pos+.12))) - left
        if length <= 0:
            continue
        path = paths[cue["storage_key"]]
        wave = decode(path, float(cue.get("offset_s") or 0)+left, length)
        power = peak_power(wave, rate, .05)
        if power:
            hits.append((at, 10*math.log10(power) + float(cue.get("gain_db", -6))))
    result = []
    for idx, track, duration in music:
        windows = []
        for at, level_db in hits:
            if not track["start"] <= at < track["end"]:
                continue
            offset = float(track.get("offset_s") or 0)
            if duration and offset >= duration-.05:
                offset = 0.
            pos = offset + at-track["start"]
            if duration and track.get("loop"):
                pos %= duration
            wave = decode(paths[track["storage_key"]], max(0., pos-.1), .35)
            power = peak_power(wave, rate, .05)
            if power > 1e-10:
                bed_db = 10*math.log10(power) + float(track.get("gain_db", -18))
                depth = min(18., bed_db + 8. - level_db)
                if depth > .5:
                    windows.append((at, round(depth, 1)))
        result.append((idx, dict(track, _cue_windows=windows), duration))
    return result


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
    perception = None
    if index.get('director_preparation'):
        from score import preparation
        restored = preparation.restore(index, src, ctx.workdir)
        if restored:
            index, perception = restored
    # The coarse scene index misses cuts within montages. Reuse only the
    # source-validated pixel measurements already made during indexing.
    # No arranged track does not imply no music: the upload may carry a bed.
    video = index.get('video') or {}
    sounds, report = sound.design(l1, src=src, index=index, perc=perception,
                                  bed=True if edl.get('music') else None,
                                  level_source=schemas.master_loudness(
                                      edl, video.get('width'), video.get('height')) == 'social')
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
        if item.get("source_duck_db"):
            cue["source_duck_db"] = item["source_duck_db"]
        if item.get('trim_s'):
            cue['offset_s'] = item['trim_s']
        if item.get('dur_s'):
            cue['dur_s'] = item['dur_s']
        placed.append(cue)
    if placed:
        edl['sfx'] = placed
        edl['master'] = dict(edl.get('master') or {}, measure_cue_bed=True)
        if report.get("recorded_music_gain_db"):
            edl["master"] = dict(edl.get("master") or {}, recorded_music_gain_db=report["recorded_music_gain_db"])
        result = ctx.apply_score(edl, 'sound design on the first film’s picture and graphic events')
        report['write'] = str(result)[:120]
    report['placed_on_chain'] = len(placed)
    phases = dict(getattr(ctx, 'first_cut_phases', None) or {})
    phases['chain_sound'] = report
    ctx.first_cut_phases = phases
    return report
