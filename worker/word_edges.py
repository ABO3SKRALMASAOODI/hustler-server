"""A quiet waveform trough inside a transcript word is not a cut boundary."""
import math


def introduced(before, after, words, duration=None):
    old = {(round(float(t), 6), side) for a, b in before or []
           for t, side in ((a, 'start'), (b, 'end'))}
    hits = []
    spans = after or []
    for i, (a, b) in enumerate(spans):
        for t, side in ((float(a), 'start'), (float(b), 'end')):
            if (round(t, 6), side) in old or t <= .0005 or (duration is not None and t >= duration - .0005):
                continue
            if (side == 'start' and i and abs(spans[i-1][1] - t) < .0005 or
                    side == 'end' and i+1 < len(spans) and abs(spans[i+1][0] - t) < .0005):
                continue
            for w in words:
                if float(w['t0']) + .0005 < t < float(w['t1']) - .0005:
                    hits.append(dict(boundary=t, kind=side, word=w['w'], t0=w['t0'], t1=w['t1']))
    return hits


def whole(keep, words, duration, previous=None, pad=False):
    """Final outward snap, after every acoustic/shot pass, before item remapping.

Never discard the first word simply because an onset was chosen too late.
Repeated passes cover overlapping ASR word intervals; edges use the EDL's
centisecond grid outwards, so normalization cannot put them back inside.
"""
    spans = [[float(a), float(b)] for a, b in keep]
    moves = []
    for _ in range(len(words) + 1):
        hits = introduced(previous, spans, words, duration)
        if not hits:
            break
        for h in hits:
            old, side = h['boundary'], h['kind']
            edge = h['t0'] if side == 'start' else h['t1']
            if pad:
                # Leave room for consonants outside ASR's voiced-core times,
                # without entering the neighboring word on the removed side.
                import cut_audio
                if side == 'start':
                    prev = max((float(w['t1']) for w in words if float(w['t1']) <= edge), default=0.)
                    edge = max(prev, edge - cut_audio.PAD_START_S)
                else:
                    nxt = min((float(w['t0']) for w in words if float(w['t0']) >= edge), default=float(duration))
                    edge = min(nxt, edge + cut_audio.PAD_END_S)
            new = (math.floor(edge * 100 + 1e-7) / 100 if side == 'start'
                   else math.ceil(edge * 100 - 1e-7) / 100)
            new = max(0., min(float(duration), new))
            for span in spans:
                k = 0 if side == 'start' else 1
                if abs(span[k] - old) < 1e-7:
                    span[k] = new
            moves.append(f"Kept the whole word '{h['word']}': {side} {old:.3f}s → {new:.3f}s.")
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        spans = merged
    return spans, moves


def refusal(ctx, before, after):
    if before.get('keep') == after.get('keep'):
        return None
    words = (getattr(ctx, 'index', None) or {}).get('words') or []
    hits = introduced(before.get('keep'), after.get('keep'), words, getattr(ctx, 'duration', None))
    if not hits:
        return None
    return ('Cut would clip spoken words: ' + '; '.join(
        f"{h['kind']} {h['boundary']:.3f}s is inside '{h['word']}' ({h['t0']:.3f}–{h['t1']:.3f}s)"
        for h in hits) + '. Keep or remove each word whole; retry with word-safe boundaries.')
