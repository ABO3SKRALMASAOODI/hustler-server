"""Cut points on sentence and clause boundaries (TRIAL_QUALITY_BUILD, 31 Oct 2026).

The trial editor's own cuts opened and closed on speech fragments: user
2670's "first 8.2 s standing, then a first touch" replays were built as asked
but started on "...and then" and ended on "so what you" (RS_ENGINE section 3,
the judge's hard faults), and every Studio request that trims a talk had the
same habit. Word snapping (audit.snap_keep_to_words) only keeps whole WORDS;
a cut between two words of one sentence is still a fragment.

This module is deterministic and pure: from the index's word timings (and its
sentences, when the index has them) it finds the gaps between words where a
sentence or a clause ends, and moves every NEW cut edge of a keep list that
lands inside a sentence to the nearest such gap. A piece START sits just
before the next sentence's first word, a piece END just after the last word
of the sentence it closes (a breath of tail). Edges in silence, before the
first word, after the last one, or already on a boundary stay exactly where
they are; edges the previous keep already had are never moved (the First
cut's and an earlier request's cuts are not this write's business).

Sentence boundaries win over clause boundaries (a clause gap costs CLAUSE_COST
times its distance); no edge moves further than MAX_SHIFT_S; a piece that
would shrink under MIN_PIECE_S keeps its own edges. Who gets it: the Claude
lanes (the trial and new subscribers), the editor's own cut tools
(agent_tools._write_keep); grandfathered lanes never call it.
"""
import re

SENTENCE_END = re.compile(r"[.!?。！？…؟۔][\"'”’)\]»]*$")
CLAUSE_END = re.compile(r"[,;:،؛、，；：—–][\"'”’)\]»]*$")
PAUSE_S = 0.45          # a spoken pause this long is a clause boundary
LEAD_S = 0.08           # a piece start sits this far before the next word
TAIL_S = 0.15           # a piece end keeps this much breath after its word
EDGE_TOL_S = 0.04       # an edge this close to a boundary gap is on it
CLAUSE_COST = 1.6       # a clause gap counts this many times its distance
MAX_SHIFT_S = 4.0       # an edge never moves further than this
MIN_PIECE_S = 0.4       # a snapped piece never gets shorter than this
SAME_EDGE_S = 0.03      # an edge the previous keep already had


def _wt(w):
    """(t0, t1, text) of a word in either index dialect."""
    t0 = w.get("t0", w.get("start"))
    t1 = w.get("t1", w.get("end"))
    return float(t0), float(t1), str(w.get("w", w.get("word", w.get("text", ""))) or "")


def _words(words):
    out = []
    for w in words or []:
        try:
            t0, t1, text = _wt(w)
        except (TypeError, ValueError, AttributeError):
            continue
        if t1 >= t0:
            out.append((t0, t1, text.strip()))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def boundaries(words, sentences=None):
    """[(gap_start, gap_end, kind)] for every gap between words where a
    sentence ('sentence') or a clause ('clause') ends, plus the open spans
    before the first word and after the last ('edge'). Pure."""
    ws = _words(words)
    if not ws:
        return []
    sentence_last = set()
    for s in sentences or []:
        try:
            sentence_last.add(int(s.get("wi1")))
        except (TypeError, ValueError, AttributeError):
            continue
    out = [(float("-inf"), ws[0][0], "edge")]
    for i in range(len(ws) - 1):
        a, b = ws[i][1], ws[i + 1][0]
        text = ws[i][2]
        if SENTENCE_END.search(text) or (sentence_last and i in sentence_last):
            kind = "sentence"
        elif CLAUSE_END.search(text) or b - a >= PAUSE_S:
            kind = "clause"
        else:
            continue
        out.append((a, max(a, b), kind))
    out.append((ws[-1][1], float("inf"), "edge"))
    return out


def is_clean(t, bounds):
    """Is a cut at t in silence or on a sentence / clause boundary?"""
    for a, b, _k in bounds:
        if a - EDGE_TOL_S <= t <= b + EDGE_TOL_S:
            return True
    return False


def snap_point(t, side, bounds, duration=None):
    """(new_t, kind) for one cut edge: side 'start' (a kept piece begins) or
    'end' (a kept piece ends). Unchanged (t, None) when the edge is clean or
    no boundary lies within MAX_SHIFT_S."""
    t = float(t)
    if not bounds or is_clean(t, bounds):
        return t, None
    best = None
    for a, b, kind in bounds:
        if side == "start":
            target = b - LEAD_S if b != float("inf") else None
            if target is not None:
                target = max(target, a) if a != float("-inf") else max(0.0, target)
        else:
            target = a + TAIL_S if a != float("-inf") else None
            if target is not None:
                target = min(target, b) if b != float("inf") else target
        if target is None:
            continue
        if duration is not None:
            target = min(max(0.0, target), float(duration))
        shift = abs(target - t)
        if shift > MAX_SHIFT_S:
            continue
        cost = shift * (CLAUSE_COST if kind == "clause" else 1.0)
        if best is None or cost < best[0]:
            best = (cost, round(target, 2), kind)
    if best is None:
        return t, None
    return best[1], best[2]


def _had(t, edges):
    return any(abs(t - x) <= SAME_EDGE_S for x in edges)


def snap_keep(keep, words, duration=None, prev_keep=None, sentences=None):
    """(keep, notes): every NEW cut edge of a source-clock keep list moved to
    the nearest sentence or clause boundary. notes: [(old, new, side, kind)].
    Pure; returns the list unchanged when there are no words."""
    keep = [[float(s), float(e)] for s, e in keep or []]
    bounds = boundaries(words, sentences)
    if not bounds or not keep:
        return keep, []
    prev_edges = []
    for s, e in prev_keep or []:
        prev_edges += [float(s), float(e)]
    dur = float(duration) if duration else None
    out, notes = [], []
    for s, e in keep:
        ns, ne = s, e
        ks = ke = None
        if s > 0.05 and not _had(s, prev_edges):
            ns, ks = snap_point(s, "start", bounds, dur)
        if (dur is None or e < dur - 0.05) and not _had(e, prev_edges):
            ne, ke = snap_point(e, "end", bounds, dur)
        if ne - ns < MIN_PIECE_S:
            ns, ne, ks, ke = s, e, None, None
        if ks:
            notes.append((s, ns, "start", ks))
        if ke:
            notes.append((e, ne, "end", ke))
        out.append([ns, ne])
    out.sort()
    merged = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged, notes


def note_line(notes):
    """The tool-result line for the editor, or ''."""
    if not notes:
        return ""
    moved = ", ".join(f"{a:g}s->{b:g}s" for a, b, _side, _k in notes[:6])
    more = f" (+{len(notes) - 6} more)" if len(notes) > 6 else ""
    return ("\nSENTENCE SNAP: " + str(len(notes)) + " cut edge(s) moved to the "
            "nearest sentence or clause boundary so no piece starts or ends "
            "mid-sentence: " + moved + more + ".")
