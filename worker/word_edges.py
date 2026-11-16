"""A quiet waveform trough inside a transcript word is not a cut boundary.

A new cut edge inside a word is repaired, never refused: ``whole`` snaps it
outward (the keep tools), ``nearest`` moves it to the word's nearest edge
(the shared write boundary, ToolContext.write_edl), and both report the exact
seconds each edge moved."""
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
microsecond precision outwards, so normalization cannot put them back inside
without swallowing an adjacent word that begins at a millisecond boundary.
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
            new = (math.floor(edge * 1000000 + 1e-7) / 1000000 if side == 'start'
                   else math.ceil(edge * 1000000 - 1e-7) / 1000000)
            new = max(0., min(float(duration), new))
            for span in spans:
                k = 0 if side == 'start' else 1
                if abs(span[k] - old) < 1e-7:
                    span[k] = new
            moves.append(f"Kept the whole word '{h['word']}': {side} {old:.3f}s → {new:.3f}s "
                         f"({abs(new - old):.3f} s {'later' if new > old else 'earlier'}).")
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        spans = merged
    return spans, moves


def merge_spans(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _edge(t, up):
    """An edge at the EDL's microsecond precision, rounded away from the word
    it leaves, so normalization cannot put it back inside."""
    return (math.ceil(t * 1000000 - 1e-7) / 1000000 if up
            else math.floor(t * 1000000 + 1e-7) / 1000000)


def nearest(keep, words, duration, previous=None):
    """Repair, never refuse: every NEW cut edge that lands inside a spoken
    word moves to that word's nearest edge (keeping the word whole, or leaving
    it out whole, whichever is closer to where the edge was written; a tie
    keeps the word). A move that would leave a span under 50 ms keeps the word
    instead. Returns (spans, moves); each move is {'side', 'from', 'to',
    'word', 'kept'} with the exact seconds, so the write can say what moved."""
    spans = [[float(a), float(b)] for a, b in keep or []]
    dur = float(duration) if duration is not None else None
    moves = []
    for _ in range(len(words or []) + 1):
        hits = introduced(previous, spans, words or [], duration)
        if not hits:
            break
        for h in hits:
            old, side = float(h['boundary']), h['kind']
            t0, t1 = float(h['t0']), float(h['t1'])
            whole_word = _edge(t0, False) if side == 'start' else _edge(t1, True)
            without = _edge(t1, True) if side == 'start' else _edge(t0, False)
            k = 0 if side == 'start' else 1
            target = [s for s in spans if abs(s[k] - old) < 1e-7]
            if not target:
                continue
            new = whole_word if abs(old - whole_word) <= abs(old - without) else without
            other = target[0][1 - k]
            if new != whole_word and (abs(other - new) < 0.05
                                      or (side == 'start' and new >= other)
                                      or (side == 'end' and new <= other)):
                new = whole_word
            if dur is not None:
                new = max(0., min(dur, new))
            for span in target:
                span[k] = new
            moves.append({'side': side, 'from': old, 'to': new, 'word': h['word'],
                          'kept': new == whole_word})
        spans = merge_spans(spans)
    return spans, moves


def move_lines(moves):
    """One plain line per repaired edge, with the exact seconds it moved."""
    out = []
    for m in moves:
        shift = m['to'] - m['from']
        how = "kept '{}' whole".format(m['word']) if m.get('kept') else \
            "left '{}' out whole".format(m['word'])
        out.append(f"{m['side']} {m['from']:.3f}s -> {m['to']:.3f}s "
                   f"({abs(shift):.3f} s {'later' if shift > 0 else 'earlier'}; {how})")
    return out
