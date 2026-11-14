"""A quiet waveform trough inside a transcript word is not a cut boundary."""
import math


def introduced(before, after, words, duration=None):
    import audit
    old = {(round(float(t), 6), side) for a, b in before or []
           for t, side in ((a, 'start'), (b, 'end'))}
    return [h for h in audit.midword_boundaries(after, words, duration)
            if (round(float(h['boundary']), 6), h['kind']) not in old]


def whole(keep, words, duration, previous=None):
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
            new = (math.floor(h['t0'] * 100 + 1e-7) / 100 if side == 'start'
                   else math.ceil(h['t1'] * 100 - 1e-7) / 100)
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
        if merged == spans and all(abs(h['t0' if h['kind'] == 'start' else 't1'] - h['boundary']) < .011 for h in hits):
            break
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
