"""Match the edges of a finished Stories read, using checked native takes from the same run."""
import json
import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

import measure
from timing_audio import Master, load, quiet_head, signatures


def sides(x, sr, t):
    start, end = measure.gap_at(x, sr, t) or (t, t)
    return [(max(0, int((start - 1.5) * sr)), int(start * sr)),
            (int(end * sr), min(len(x), int((end + 1.5) * sr)))]


def pitch_step(x, sr, t):
    pitches = [measure._f0_med(x[a:b], sr) for a, b in sides(x, sr, t) if b-a > sr // 2]
    return abs(12 * math.log2(pitches[0] / pitches[1])) if len(pitches) == 2 and all(pitches) else None


def level_join(x, sr, t):
    (a, b), (c, d) = sides(x, sr, t)
    if min(b-a, d-c) < sr // 2:
        return
    step = measure._speech_db(x[a:b], sr) - measure._speech_db(x[c:d], sr)
    # Ride down the louder phrase, preserving headroom and the floor at the break as it
    # enters or leaves the break, with a smooth return to its native level.
    a, b = (a, b) if step > 0 else (c, d)
    gain = min(6.0, max(0.0, abs(step) - 1.0))
    ramp = min(sr//8, (b-a)//2)
    db = np.full(b-a, gain)
    db[:ramp] *= np.linspace(0, 1, ramp)
    db[-ramp:] *= np.linspace(1, 0, ramp)
    x[a:b] *= 10 ** (-db / 20)
    return round(step, 2)


def fitted_take(target, i, donor, j):
    """Fit between quiet cuts, preserving all speech samples and the following clock."""
    sr = target.sr
    a, b, c, d = target.cut(i), target.cut(i+1), donor.cut(j), donor.cut(j+1)
    if None in (a, b, c, d):
        return
    raw = donor.audio[c:d]
    spare = (b-a) - len(raw)
    # Small differences in native read length can live in the adjacent pauses.
    if abs(spare) > 0.3 * sr:
        return
    left = spare//2
    start, end = a + left, a + left + len(raw)
    lo, hi = max(a,start), min(b,end)
    if lo-start > quiet_head(raw, sr) or end-hi > quiet_head(raw[::-1], sr):
        return
    from lines import room_tone
    audio = target.audio.copy()
    # Fill surplus space with the native floor, then crossfade at the quiet cuts.
    if spare > 0:
        audio[a:b] = room_tone(raw[:sr // 20], raw[-sr // 20:], sr, ((b-a)+0.25)/sr, seed=i)[:b-a]
    audio[lo:hi] = raw[lo-start:hi-start]
    fade = min(sr // 250, (hi-lo)//2)
    if fade:
        ramp = np.linspace(0, 1, fade)
        for k, weights in ((a,ramp),(b-fade,ramp[::-1])):
            audio[k:k+fade] = target.audio[k:k+fade]*(1-weights) + audio[k:k+fade]*weights
    return audio, (start-c)/sr, (a/sr, b/sr)


def replace_group(target, i, donor, j, shift):
    rows = [dict(w, start=round(w['start'] + shift, 3), end=round(w['end'] + shift, 3),
                 seg=target.groups[i][donor.groups[j].index(w['seg'])], chunk=i) for w in donor.rows[j]]
    target.rows[i] = rows
    target.report['chunks'][i].update(start=rows[0]['start'], end=rows[-1]['end'])
    target.report['lines'][i] = dict(donor.report['lines'][j], line=i)
    target.report['stories_read']['picks'][i] = donor.report['stories_read']['picks'][j]
    first, last = rows[0]['start'], rows[-1]['end']
    segs = dict(zip(donor.groups[j], target.groups[i]))
    phrases = [p for p in target.words['phrases'] if p['seg'] not in target.groups[i]]
    phrases.extend(dict(p, seg=segs[p['seg']], start=round(p['start']+shift, 3), end=round(p['end']+shift, 3))
                   for p in donor.words['phrases'] if p['seg'] in segs)
    left = target.rows[i-1][-1]['end'] if i else 0
    right = target.rows[i+1][0]['start'] if i+1 < len(target.rows) else target.words['duration']
    cuts = [t for t in target.words['cut_points'] if not left < t < right]
    cuts.extend(round(t + shift, 3) for t in donor.words['cut_points']
                if first < t + shift < last)
    for a, b in ((left, first), (last, right)):
        if b-a >= measure.PAUSE_S and 0 < a < b < target.words['duration']:
            cuts.append(round((a+b)/2, 3))
    target.words.update(words=[w for group in target.rows for w in group], cut_points=sorted(cuts),
                        phrases=sorted(phrases, key=lambda p: p['start']))


def native_edge_cost(take, context):
    x, sr = take['_x'], take['_sr']
    span = int(1.5 * sr)
    distances = []
    for samples, neighbor in zip((x[:span], x[-span:]), context):
        if neighbor:
            pitch = measure._f0_med(samples, sr)
            distances.append(abs(12 * math.log2(pitch / neighbor)) if pitch else math.inf)
    return max(distances, default=0.0)


def edge_candidate(target, i, donor, j):
    if (donor.chosen(j)['errs']
            or [w['w'] for w in donor.rows[j]] != [w['w'] for w in target.rows[i]]):
        return None
    fitted = fitted_take(target, i, donor, j)
    if fitted is None:
        return None
    audio, shift, cuts = fitted
    edges = [(k, t) for k, t in zip((i-1, i), cuts) if 0 <= k < len(target.report['boundaries'])]
    steps = [pitch_step(audio, target.sr, t) for _, t in edges]
    if steps and all(v is not None for v in steps):
        return max(steps), audio, shift, edges, i, donor, j


def fresh_neighbor(target, i, script, tmp):
    """A single native generation when the checked masters have no compatible alternate."""
    import argparse
    import lines
    import perform
    import voice
    if target.report['engine'] != 'lines:phonon' or target.report['model'] != lines.phonon_source()[0]:
        return None, 0
    a, b = target.cut(i), target.cut(i+1)
    if a is None or b is None:
        return None, 0
    sr = target.sr
    context = []
    for edge, side in ((a, 0), (b, 1)):
        lo, hi = sides(target.audio, sr, edge/sr)[side]
        context.append(measure._f0_med(target.audio[lo:hi], sr) if hi-lo > sr // 2 else None)
    local = dict(script, engine='phonon', segments=[script['segments'][k] for k in target.groups[i]],
                 phonon_voice=target.report['voice'].removeprefix('phonon:'))
    sp = tmp/'narration_script.json'
    sp.write_text(json.dumps(local))
    args = argparse.Namespace(out=str(tmp), script=str(sp), band=target.report['band'])
    original = lines.generate_phonon, lines.join_lines, perform.choose, perform.judge
    try:
        count = voice.render(args, edge_context=context, group_offset=i)
    finally:
        lines.generate_phonon, lines.join_lines, perform.choose, perform.judge = original
    donor = Master(tmp)
    if len(donor.groups) != 1 or donor.sr != sr or donor.report['voice'] != target.report['voice']:
        return None, count
    return edge_candidate(target, i, donor, 0), count


def repair(folder, donors, out):
    from master import lufs
    from voice import lra
    target = Master(folder)
    check = load(folder, 'check.json')
    script = load(folder, 'narration_script.json')
    keys = signatures(script, target.groups)
    flags = check['joins']['boundaries']
    bounds = target.report['boundaries']
    changed = set()
    takes, levels = [], []
    generated = 0
    read_errors = []
    natural = check['joins']['summary']
    pitch_limit = (natural['f0_step_st'].get('natural') or {}).get('max')
    level_limit = (natural['level_step_db'].get('natural') or {}).get('max')
    for n, flag in enumerate(flags):
        if not flag['artefacts'] or pitch_limit is None or (flag.get('f0_step_st') or 0) <= pitch_limit + 1:
            continue
        best = None
        for path in donors:
            if not (path/'check.json').is_file() or not (path/'narration_script.json').is_file():
                continue
            ds = load(path, 'narration_script.json')
            if any(ds.get(k) != script.get(k) for k in (set(ds) | set(script)) - {'segments', 'title'}):
                continue
            donor = Master(path)
            if donor.sr != target.sr or any(donor.report[k] != target.report[k] for k in ('engine', 'model', 'voice', 'band')):
                continue
            if ('max_tempo' not in donor.report['stories_read'] and any(row['tempo'] != 1 for row in donor.report['lines'])):
                continue
            if donor.report['stories_read'].get('max_tempo', 1.0) != target.report['stories_read'].get('max_tempo', 1.0):
                continue
            dc = load(path, 'check.json')
            misheard = {}
            if dc['wer']['wer']:
                import lines
                alignment = lines.seg_errors(donor.report['chunks'], dc.get('heard_spoken') or dc['heard'])
                if alignment is None:
                    continue
                misheard = alignment['by_line']
            for i in (n, n + 1):
                for j, key in enumerate(signatures(ds, donor.groups)):
                    if key != keys[i] or j in misheard or donor.chosen(j) == target.chosen(i):
                        continue
                    candidate = edge_candidate(target, i, donor, j)
                    if candidate and candidate[0] < (best[0] if best else flag['f0_step_st']):
                        best = (*candidate, path.name)
        if best is None or best[0] > pitch_limit + 1:
            for i in sorted((n, n+1), key=lambda k: len(target.rows[k])):
                with tempfile.TemporaryDirectory(dir=out) as tmp:
                    try:
                        candidate, count = fresh_neighbor(target, i, script, Path(tmp))
                    except (OSError, ValueError, RuntimeError) as exc:
                        read_errors.append(dict(group=i, error=str(exc)[:200]))
                        continue
                generated += count
                if candidate and candidate[0] < (best[0] if best else flag['f0_step_st']):
                    best = (*candidate, 'native neighbor read')
                if best and best[0] <= pitch_limit + 1:
                    break
        if best:
            score, target.audio, shift, edges, i, donor, j, name = best
            replace_group(target, i, donor, j, shift)
            for k, t in edges:
                bounds[k]['t'] = round(t, 3)  # where the replacement actually splices into the master
                bounds[k].pop('gap', None)
                changed.add(k)
            takes.append(dict(group=i, source=name, take=donor.chosen(j)['file'], worst_edge_st=round(score, 2)))
    for i, (flag, bound) in enumerate(zip(flags, bounds)):
        if level_limit is not None and (i in changed or (flag.get('level_step_db') or 0) > level_limit+1):
            step = level_join(target.audio, target.sr, bound['t'])
            levels.append(dict(boundary=i, signed_step_db=step))
    if not (takes or levels):
        return False
    rep = target.report['stories_read']
    rep['join_repair'] = dict(takes=takes, levels=levels, previous_gate=check['gate'], generated_takes=generated)
    if read_errors:
        rep['join_repair']['read_errors'] = read_errors
    rep['lufs'] = round(lufs(target.audio, target.sr), 2)
    rep['lra']['after'] = round(lra(target.audio, target.sr), 2)
    target.report['duration_s'] = target.words['duration']
    sf.write(out/'narration.wav', target.audio, target.sr, subtype='PCM_24')
    for name, data in [('words',target.words),('render',target.report),('narration_script',script)]:
        (out/(name+'.json')).write_text(json.dumps(data, indent=1))
    return True
