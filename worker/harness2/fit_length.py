"""fit_length: bring a cut into a length band by signal, never by judgement.

The least active seconds at piece edges go first (``activity.score``), and
an edge never lands inside a sentence (word times and
``sentence_snap.boundaries``): a sentence leaves whole or stays whole.
Seconds that carry no words go before seconds that do. A piece left under
``MIN_PIECE_S`` is dropped whole. When the cut is short, the most active
neighbouring seconds come back the same way. Every move is reported.

Pure: it reads a keep list, a score array and word times, and returns a new
keep list with its report. Nothing here reads the request.
"""
import bisect
import heapq
import math

import sentence_snap

MIN_PIECE_S = 2.0
STEP_S = 0.5            # the trim unit at a free edge
SPEECH_COST = 2.5       # per-second cost added when a chunk carries words
PAUSE_BREAK_S = 1.0     # a pause this long between words reads as a break


def _blocks(words, sentences=None):
    """Spans an edge may not land inside: each sentence, first word start to
    last word end. Between them (and outside speech) every time is free."""
    bounds = sentence_snap.boundaries(words, sentences)
    if not bounds:
        return []
    allowed = []
    for start, end, kind in bounds:
        if kind in ("sentence", "edge") or (end - start) >= PAUSE_BREAK_S:
            allowed.append((start, end))
    blocks = []
    for (a0, a1), (b0, b1) in zip(allowed, allowed[1:]):
        if b0 > a1 + 1e-6:
            blocks.append((a1, b0))
    return blocks


class _Signal:
    def __init__(self, score, fps):
        self.s = list(score or [])
        self.fps = float(fps or 4.0)
        prefix = [0.0]
        for v in self.s:
            prefix.append(prefix[-1] + float(v))
        self.prefix = prefix

    def mean(self, a, b):
        if not self.s:
            return 0.0
        i = max(0, min(len(self.s), int(math.floor(a * self.fps))))
        j = max(i + 1, min(len(self.s), int(math.ceil(b * self.fps))))
        if j > len(self.s):
            return float(self.s[-1]) if self.s else 0.0
        return (self.prefix[j] - self.prefix[i]) / float(j - i)


def _covering(blocks, t, at_end=False):
    """The block holding time t: start <= t < end, or for an end edge
    start < t <= end. None when t is free."""
    starts = [b[0] for b in blocks]
    i = bisect.bisect_right(starts, t if not at_end else t - 1e-9) - 1
    if i < 0:
        return None
    blk = blocks[i]
    if at_end:
        return blk if blk[0] < t <= blk[1] + 1e-9 else None
    return blk if blk[0] - 1e-9 <= t < blk[1] else None


def _next_start(blocks, t):
    starts = [b[0] for b in blocks]
    i = bisect.bisect_right(starts, t + 1e-9)
    return blocks[i][0] if i < len(blocks) else None


def _prev_end(blocks, t):
    ends = [b[1] for b in blocks]
    i = bisect.bisect_left(ends, t - 1e-9) - 1
    return blocks[i][1] if i >= 0 else None


def _has_words(word_spans, a, b):
    return any(ws < b and we > a for ws, we in word_spans)


def _trim_chunk(piece, side, blocks, word_spans):
    """(chunk start, chunk end) the next trim at ``side`` would remove: a
    whole sentence when the edge meets one, else STEP_S of free time (up to
    the next sentence)."""
    a, b = piece
    if side == "start":
        blk = _covering(blocks, a)
        if blk:
            return a, min(b, blk[1])
        nxt = _next_start(blocks, a)
        return a, min(b, a + STEP_S, nxt if nxt is not None else b)
    blk = _covering(blocks, b, at_end=True)
    if blk:
        return max(a, blk[0]), b
    prev = _prev_end(blocks, b)
    return max(a, b - STEP_S, prev if prev is not None else a), b


def _cost(sig, word_spans, a, b):
    return sig.mean(a, b) + (SPEECH_COST if _has_words(word_spans, a, b)
                             else 0.0)


def _length(keep):
    return sum(max(0.0, e - s) for s, e in keep)


def _protected(protect, a, b):
    return any(pa < b - 1e-6 and pb > a + 1e-6 for pa, pb in protect or ())


def trim(keep, sig, blocks, word_spans, remove_s, protect=()):
    """Remove about ``remove_s`` seconds from piece edges, quietest first.
    Spans in ``protect`` (what the same edit just restored or kept on
    purpose) are never trimmed: the cost is paid elsewhere."""
    pieces = [[float(s), float(e)] for s, e in keep]
    alive = [True] * len(pieces)
    removed, dropped, touched = 0.0, 0, set()
    heap, stamp = [], [0] * len(pieces)

    def push(i):
        if not alive[i]:
            return
        stamp[i] += 1
        for side in ("start", "end"):
            ca, cb = _trim_chunk(pieces[i], side, blocks, word_spans)
            if cb - ca <= 1e-6 or _protected(protect, ca, cb):
                continue
            heapq.heappush(heap, (_cost(sig, word_spans, ca, cb), i, side,
                                  stamp[i], ca, cb))

    for i in range(len(pieces)):
        push(i)
    while heap and removed < remove_s - 1e-6:
        cost, i, side, st, ca, cb = heapq.heappop(heap)
        if not alive[i] or st != stamp[i]:
            continue
        a, b = pieces[i]
        if side == "start":
            na, nb = cb, b
        else:
            na, nb = a, ca
        touched.add(i)
        if nb - na < MIN_PIECE_S:
            removed += b - a
            alive[i] = False
            dropped += 1
            continue
        removed += (b - a) - (nb - na)
        pieces[i] = [na, nb]
        push(i)
    out = [[round(a, 3), round(b, 3)] for (a, b), ok in zip(pieces, alive)
           if ok]
    return out, {"removed_s": round(removed, 1),
                 "pieces_trimmed": len(touched) - dropped,
                 "pieces_dropped": dropped}


def extend(keep, sig, blocks, word_spans, add_s, duration):
    """Bring back about ``add_s`` seconds, most active neighbours first."""
    pieces = sorted([float(s), float(e)] for s, e in keep)
    added = 0.0
    guard = 0
    while added < add_s - 1e-6 and guard < 100000:
        guard += 1
        best = None
        for i, (a, b) in enumerate(pieces):
            lo = pieces[i - 1][1] if i > 0 else 0.0
            hi = pieces[i + 1][0] if i + 1 < len(pieces) else float(duration)
            if a - lo > 1e-6:      # grow the start backwards
                blk = _covering(blocks, a, at_end=True)
                na = max(lo, blk[0] if blk else a - STEP_S)
                score = sig.mean(na, a)
                if best is None or score > best[0]:
                    best = (score, i, "start", na)
            if hi - b > 1e-6:      # grow the end forwards
                blk = _covering(blocks, b)
                nb = min(hi, blk[1] if blk else b + STEP_S)
                score = sig.mean(b, nb)
                if best is None or score > best[0]:
                    best = (score, i, "end", nb)
        if best is None:
            break
        _score, i, side, t = best
        a, b = pieces[i]
        if side == "start":
            added += a - t
            pieces[i][0] = t
        else:
            added += t - b
            pieces[i][1] = t
        merged = []
        for p in pieces:
            if merged and p[0] <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], p[1])
            else:
                merged.append(list(p))
        pieces = merged
    return ([[round(a, 3), round(b, 3)] for a, b in pieces],
            {"added_s": round(added, 1)})


def fit(keep, score, fps, words, duration, max_s=None, min_s=None,
        other_s=0.0, sentences=None, protect=()):
    """(new keep, report lines, measures). ``other_s`` is programme time not
    in the keep (inserts), held constant. The target is the middle of the
    band when both ends are given, else just inside the one given."""
    sig = _Signal(score, fps)
    blocks = _blocks(words or [], sentences)
    word_spans = [(w[0], w[1]) for w in sentence_snap._words(words or [])]
    current = _length(keep) + float(other_s or 0.0)
    lines, measures = [], {"before_s": round(current, 1)}
    hi = float(max_s) if max_s is not None else None
    lo = float(min_s) if min_s is not None else None
    if hi is not None and lo is not None and lo > hi:
        lo, hi = hi, lo
        lines.append(f"fit_length: min and max were swapped ({lo:g}-{hi:g} s)")
    if hi is not None and current > hi:
        target = (lo + hi) / 2.0 if lo is not None else hi - min(
            10.0, 0.01 * hi)
        new_keep, info = trim(keep, sig, blocks, word_spans,
                              current - target, protect=protect)
        measures.update(info)
        lines.append(
            f"fit_length trimmed {info['removed_s']:g} s from the quietest "
            f"ends of {info['pieces_trimmed']} piece(s)"
            + (f" and dropped {info['pieces_dropped']} piece(s) left under "
               f"{MIN_PIECE_S:g} s" if info["pieces_dropped"] else "")
            + "; no edge inside a sentence")
    elif lo is not None and current < lo:
        target = (lo + hi) / 2.0 if hi is not None else lo + min(
            10.0, 0.01 * lo)
        new_keep, info = extend(keep, sig, blocks, word_spans,
                                target - current, duration)
        measures.update(info)
        lines.append(f"fit_length restored {info['added_s']:g} s of the most "
                     "active neighbouring footage")
    else:
        new_keep = [list(map(float, k)) for k in keep]
        lines.append(f"fit_length: already in the band at {current:.1f} s")
    measures["after_s"] = round(_length(new_keep) + float(other_s or 0.0), 1)
    return new_keep, lines, measures
