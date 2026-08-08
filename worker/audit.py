"""Deterministic, LLM-free audits over EDL keep sets and the index.

Three consumers:
  - keep-modifying tools warn about boundaries landing inside words and
    about keep_segments re-including previously cut material;
  - the renderer stamps a mid-word audit into every render result;
  - snap_to_words moves keep boundaries outward to word edges so a cut can
    never clip a word.

Everything here is pure functions over plain data — no DB, no network.
"""
import bisect

EPS = 0.011   # times are rounded to 0.01s; boundaries must be STRICTLY inside


def word_at_boundary(words, b):
    """The word whose interior strictly contains boundary time b, or None."""
    for w in words:
        t0 = w["t0"] if isinstance(w, dict) else w.t0
        t1 = w["t1"] if isinstance(w, dict) else w.t1
        if t0 + EPS < b < t1 - EPS:
            return {"w": w["w"] if isinstance(w, dict) else w.w,
                    "t0": t0, "t1": t1}
    return None


def midword_boundaries(keep, words, duration=None):
    """All keep boundaries (excluding 0 and the video end) that land inside
    a word. Returns [{'boundary', 'kind': 'start'|'end', 'word', 't0','t1'}]."""
    out = []
    for s, e in keep:
        for b, kind in ((s, "start"), (e, "end")):
            if b <= EPS or (duration is not None and b >= duration - EPS):
                continue
            hit = word_at_boundary(words, b)
            if hit:
                out.append({"boundary": b, "kind": kind, "word": hit["w"],
                            "t0": hit["t0"], "t1": hit["t1"]})
    return out


def speech_gaps(words, duration, min_s=0.7, silences=None):
    """Spans of the source where NOBODY IS TALKING, from the word timings.

    This is what a user means by "cut the silences". The index's `silences`
    are something narrower: spans where the WAVEFORM is below a noise floor.
    On a talking-head in a quiet room the two agree, which is why the gap
    went unnoticed — but on anything with a continuous bed (gameplay, music,
    room noise, a stream) the audio never drops below the floor, so
    ffmpeg's silencedetect returns NOTHING and the product told a real user
    with a 2-minute gameplay clip that his video had no silences to cut
    while he was looking at long stretches of nobody speaking.

    Derived from `words`, which every index already carries, so it needs no
    re-index and no PIPELINE_VERSION bump — it works on every project ever
    uploaded, including the ones already sitting in the database.

    Returns [{'start', 'end', 'quiet_frac'}], where quiet_frac is how much
    of the gap is ALSO below the noise floor (1.0 = true silence, ~0.0 =
    there is sound there, just not speech). Callers disclose the difference
    rather than pretending a game explosion is silence.
    """
    # No transcript at all means no speech to find gaps BETWEEN. Returning
    # the whole video as one gap would be literally true and operationally
    # awful — a caller that trusted it would cut every frame — so the empty
    # answer is given here rather than left for each call site to remember.
    if not words:
        return []
    spans = sorted(((float(w["t0"] if isinstance(w, dict) else w.t0),
                     float(w["t1"] if isinstance(w, dict) else w.t1))
                    for w in words), key=lambda x: x[0])
    gaps, cursor = [], 0.0
    for t0, t1 in spans:
        if t0 - cursor >= min_s:
            gaps.append((round(cursor, 2), round(t0, 2)))
        cursor = max(cursor, t1)
    if duration and duration - cursor >= min_s:
        gaps.append((round(cursor, 2), round(float(duration), 2)))

    out = []
    for s, e in gaps:
        quiet = sum(max(0.0, min(e, qe) - max(s, qs))
                    for qs, qe in (silences or []))
        out.append({"start": s, "end": e,
                    "quiet_frac": round(quiet / (e - s), 2) if e > s else 0.0})
    return out


def nearest_silence_midpoint(silences, t):
    best = None
    for s, e in silences or []:
        mid = (s + e) / 2.0
        if best is None or abs(mid - t) < abs(best - t):
            best = mid
    return best


def boundary_warning_lines(keep, words, silences, duration=None):
    lines = []
    for hit in midword_boundaries(keep, words, duration):
        cands = [f"{hit['t0']:.2f} (word start)", f"{hit['t1']:.2f} (word end)"]
        mid = nearest_silence_midpoint(silences, hit["boundary"])
        if mid is not None:
            cands.append(f"{mid:.2f} (nearest silence midpoint)")
        lines.append(
            f"WARNING: keep {hit['kind']} boundary {hit['boundary']:.2f} "
            f"lands inside the word '{hit['word']}' "
            f"({hit['t0']:.2f}-{hit['t1']:.2f}) — snap to "
            + " or ".join(cands) + ".")
    return lines


def midword_audit(keep, words, duration=None):
    """Compact strings for render results / logs."""
    return [f"boundary {h['boundary']:.2f} inside '{h['word']}' "
            f"({h['t0']:.2f}-{h['t1']:.2f})"
            for h in midword_boundaries(keep, words, duration)]


def snap_keep_to_words(keep, words, duration):
    """Move any keep boundary that lands inside a word OUTWARD to the word
    edge (span start -> word start, span end -> word end), so whole words
    survive. Returns a new merged, sorted keep list.

    Round 100 — BREATH PADDING on the snapped edges. Whisper's word t1 runs
    consistently early (the model marks the end of the voiced core, not the
    release of the final consonant), so cutting exactly AT t1 audibly clips
    the word's tail — the "ends mid-word" complaint from the Aug 8 shorts,
    where every clip boundary was a snapped word edge. The end edge now
    carries +0.12s of breath and the start edge -0.05s of lead-in, each
    clamped so the pad can never swallow a neighbouring word."""
    starts = sorted(float(w["t0"]) for w in words) if words else []
    ends = sorted(float(w["t1"]) for w in words) if words else []

    def _next_start_after(t):
        i = bisect.bisect_right(starts, t + 1e-6)
        return starts[i] if i < len(starts) else None

    def _prev_end_before(t):
        i = bisect.bisect_left(ends, t - 1e-6)
        return ends[i - 1] if i > 0 else None

    snapped = []
    for s, e in keep:
        hs = word_at_boundary(words, s)
        he = word_at_boundary(words, e)
        ns = round(hs["t0"], 2) if hs else s
        ne = round(he["t1"], 2) if he else e
        if he:
            pad_to = float(he["t1"]) + 0.12
            nxt = _next_start_after(float(he["t1"]))
            if nxt is not None:
                pad_to = min(pad_to, nxt - 0.01)
            ne = round(max(ne, pad_to), 2)
        elif words:
            # Boundary sitting AT a word edge (the sentence-timestamp case —
            # every shorts clip) still ends at whisper's early t1; give the
            # same breath whenever the edge rides a word's tail.
            prv = _prev_end_before(e + 0.02)
            if prv is not None and e - prv <= 0.15:
                pad_to = prv + 0.12
                nxt = _next_start_after(prv)
                if nxt is not None:
                    pad_to = min(pad_to, nxt - 0.01)
                ne = round(max(ne, pad_to), 2)
        if hs:
            pad_to = float(hs["t0"]) - 0.05
            prv = _prev_end_before(float(hs["t0"]))
            if prv is not None:
                pad_to = max(pad_to, prv + 0.01)
            ns = round(min(ns, pad_to), 2)
        elif words:
            nxt = _next_start_after(s - 0.02)
            if nxt is not None and 0 <= nxt - s <= 0.15:
                pad_to = nxt - 0.05
                prv = _prev_end_before(nxt)
                if prv is not None:
                    pad_to = max(pad_to, prv + 0.01)
                ns = round(min(ns, pad_to), 2)
        ns = max(0.0, ns)
        if duration is not None:
            ne = min(float(duration), ne)
        if ne - ns > 0.01:
            snapped.append([ns, ne])
    snapped.sort(key=lambda x: x[0])
    merged = []
    for s, e in snapped:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def subtract_spans(a, b):
    """Parts of span list a not covered by span list b (all [start,end])."""
    out = []
    for s, e in a:
        pieces = [(float(s), float(e))]
        for bs, be in b:
            nxt = []
            for ps, pe in pieces:
                if be <= ps or bs >= pe:
                    nxt.append((ps, pe))
                    continue
                if ps < bs:
                    nxt.append((ps, bs))
                if be < pe:
                    nxt.append((be, pe))
            pieces = nxt
        out.extend((round(ps, 2), round(pe, 2))
                   for ps, pe in pieces if pe - ps > 0.02)
    return out


def _norm_text(t):
    return " ".join((t or "").lower().split())


def regression_warnings(prev_keep, new_keep, index):
    """Mechanical warning when a full keep replacement re-includes ranges the
    previous version had cut, annotated from the index (silence / duplicate
    sentences). No LLM involved."""
    readded = subtract_spans(new_keep, prev_keep)
    silences = index.get("silences") or []
    sentences = index.get("sentences") or []
    by_text = {}
    for s in sentences:
        by_text.setdefault(_norm_text(s.get("text")), []).append(s)
    lines = []
    for rs, re_ in readded:
        if re_ - rs < 0.2:
            continue
        notes = []
        sil = sum(max(0.0, min(re_, e) - max(rs, s)) for s, e in silences)
        if sil / (re_ - rs) >= 0.6:
            notes.append("leading silence" if rs <= 0.05 else "mostly silence")
        for sent in sentences:
            ov = max(0.0, min(re_, sent["t1"]) - max(rs, sent["t0"]))
            if ov < 0.5 * max(0.05, sent["t1"] - sent["t0"]):
                continue
            twins = [t for t in by_text.get(_norm_text(sent.get("text")), [])
                     if t["id"] != sent["id"]]
            if twins and _norm_text(sent.get("text")):
                notes.append(f"{sent['id']} is a verbatim duplicate "
                             f"of {twins[0]['id']}")
        lines.append(f"re-includes {rs:.2f}-{re_:.2f}"
                     + (f" — {'; '.join(notes)}" if notes else ""))
    if not lines:
        return []
    return ["WARNING (regression): this keep list " + "; ".join(lines)
            + ". If that was not intentional, call get_edl and use "
              "cut_range/restore_range for local fixes."]
