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
# An edge that keeps less than this share of the word it lands in was aimed
# at the word's far edge: snapping "outward" there pulled in the stutter or
# doubled word the editor was cutting ('What|what is', kept 16% of 'What';
# Oct 2026, 14 edges over 5 shorts kept 8-34%). Such an edge drops the word;
# from this share up the whole word is kept, as before.
KEEP_WORD_FRAC = 0.4


# set_volume this low (dB) on the main sound silences what it covers: the
# editor's tool for a next speaker's first word in the payoff's tail.
SILENCED_DB = -40.0


def ducked_spans(edl, floor_db=SILENCED_DB):
    """SOURCE windows whose main sound set_volume turns down to ``floor_db``
    or below: [(start, end)]."""
    out = []
    for v in (edl or {}).get("volume") or []:
        try:
            a, b, g = float(v["start"]), float(v["end"]), float(v["gain_db"])
        except (KeyError, TypeError, ValueError):
            continue
        if g <= floor_db and b > a:
            out.append((a, b))
    return out


def silenced(s0, s1, spans, share=0.5):
    """Is the word at SOURCE [s0, s1] silenced — at least ``share`` of it
    under ``spans`` (ducked_spans)? A word with no source times is not.

    The Gates run's payoff tails: the next speaker starts 0.16-0.66 s after
    the last word, so the editor extended the keep, ducked that word to
    -60 dB and muted its caption; critique_short's payoff hold and the
    review's EARN ITS PLACE still counted it as the last word said (a 0.00 s
    hold) and the caption check called it heard but never shown (s01, s02,
    s05, s06, s08, s09, s11-s13, s15)."""
    if not spans or s0 is None or s1 is None:
        return False
    try:
        s0, s1 = float(s0), float(s1)
    except (TypeError, ValueError):
        return False
    n = max(1e-6, s1 - s0)
    cov = sum(max(0.0, min(s1, b) - max(s0, a)) for a, b in spans)
    return cov / n >= share


def word_at_boundary(words, b):
    """The word whose interior strictly contains boundary time b, or None."""
    for w in words:
        t0 = w["t0"] if isinstance(w, dict) else w.t0
        t1 = w["t1"] if isinstance(w, dict) else w.t1
        if t0 + EPS < b < t1 - EPS:
            return {"w": w["w"] if isinstance(w, dict) else w.w,
                    "t0": t0, "t1": t1}
    return None


def midword_boundaries(keep, words, duration=None, ducks=None):
    """All keep boundaries (excluding 0 and the video end) that land inside
    a word. Returns [{'boundary', 'kind': 'start'|'end', 'word', 't0','t1'}].
    ``ducks`` (ducked_spans): a boundary whose KEPT part of the word
    set_volume silences cuts nothing anyone hears — the next speaker's
    first word ducked in a payoff's tail (Gates s15 4368: 'That'
    1214.50-1214.82 under a -60 dB duck, keep end 1214.67, read 'sound
    -8 dB' on the source by the MID-WORD AUDIT)."""
    out = []
    for i, (s, e) in enumerate(keep):
        for b, kind in ((s, "start"), (e, "end")):
            # A split in continuous source coverage is a picture/scene seam,
            # not removed speech. Do not tell editors to trim the same word
            # twice merely because a camera cut divides adjacent keep spans.
            if kind == "start" and i and abs(keep[i - 1][1] - s) < 1e-6:
                continue
            if kind == "end" and i + 1 < len(keep) and abs(keep[i + 1][0] - e) < 1e-6:
                continue
            if b <= EPS or (duration is not None and b >= duration - EPS):
                continue
            hit = word_at_boundary(words, b)
            if hit:
                kept = (b, hit["t1"]) if kind == "start" else (hit["t0"], b)
                if ducks and silenced(kept[0], kept[1], ducks):
                    continue
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


def boundary_warning_lines(keep, words, silences, duration=None, skip=(),
                           safe=None, ducks=None):
    """WARNING lines for keep boundaries inside words. ``skip``: boundary
    times (hundredths) already judged clean on the source's sound — the
    transcript puts a word there but the sound is quiet (cut_audio).
    ``safe``: {boundary (hundredths): the sound-safe edge} (edge_verdicts)
    — named instead of the transcript's word edges, which run 0.05-0.25 s
    off the sound, so this warning, the render's MID-WORD AUDIT,
    critique_short and check_speech all name the same edge."""
    lines = []
    skip = {round(float(t), 2) for t in skip or ()}
    safe = {round(float(k), 2): float(v) for k, v in (safe or {}).items()}
    for hit in midword_boundaries(keep, words, duration, ducks):
        if round(float(hit["boundary"]), 2) in skip:
            continue
        at = safe.get(round(float(hit["boundary"]), 2))
        if at is not None:
            lines.append(
                f"WARNING: keep {hit['kind']} boundary {hit['boundary']:.2f} "
                f"lands inside the word '{hit['word']}' while it sounds "
                f"({hit['t0']:.2f}-{hit['t1']:.2f} in the transcript) — "
                f"move it to {at:.2f}, where the source's sound is quiet "
                "(measured; the transcript's word edges run off the sound).")
            continue
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


def midword_audit(keep, words, duration=None, source=None, timeout=10.0,
                  frame_s=1.0 / 30.0, ducks=None):
    """Compact strings for render results / logs.

    With ``source`` (the source media the render read: a path or a URL
    ffmpeg range-reads), each boundary the transcript puts inside a word is
    reported only where the SOUND there is loud — judged exactly as the
    audio-safe keep tools judge it (cut_audio.place_edge). Those tools leave
    an edge inside a transcript word on purpose when its sound is already
    quiet (Whisper's onsets run late and its ends early), and the render's
    audit used to flag the very edge they chose (Oct 10 podcast run, s04 and
    s05). Unread sound keeps the transcript's verdict."""
    hits = midword_boundaries(keep, words, duration, ducks)
    if hits and source:
        hits = _audible(hits, source, duration, timeout, frame_s, keep, words)
    out = [f"boundary {h['boundary']:.2f} inside '{h['word']}' "
           f"({h['t0']:.2f}-{h['t1']:.2f}"
           + (f", sound {h['db']:.0f} dB" if h.get("db") is not None else "")
           + ")"
           + (f" -> the sound-safe edge is {h['safe']:.2f}"
              if h.get("safe") is not None else "")
           for h in hits]
    if source:
        out += _clipped_onset_lines(keep, words, duration, source, timeout)
    return out


def _clipped_onset_lines(keep, words, duration, source, timeout):
    """Starts that keep a word from inside its sound although the transcript
    puts them on its onset (cut_audio.clipped_onsets): the index's onsets run
    late on contiguous word boundaries, so no word-based check sees them."""
    try:
        import cut_audio
        starts = [b for _i, side, b in cut_audio.cut_edges(keep, duration)
                  if side == "start"]
        if not starts:
            return []
        level = cut_audio.source_levels(source, starts, duration,
                                        timeout=timeout)
        return cut_audio.onset_lines(
            cut_audio.clipped_onsets(keep, words, duration, level))
    except Exception:  # noqa: BLE001 — a diagnostic never fails a render
        return []


def _audible(hits, source, duration, timeout, frame_s, keep=None,
             words=None):
    """``hits`` (midword_boundaries) whose sound is loud at the cut, each
    with its level in ``db`` (and, given ``keep`` and ``words``, the
    sound-safe edge in ``safe``: edge_verdicts); all of them when the sound
    cannot be read."""
    try:
        import cut_audio
        level = cut_audio.source_levels(
            source, [h["boundary"] for h in hits], duration, timeout=timeout)
    except Exception:  # noqa: BLE001 — a diagnostic never fails a render
        level = None
    if level is None:
        return hits
    return [h for h in edge_verdicts(hits, level, frame_s, keep, words)
            if h["loud"]]


def edge_verdicts(hits, level, frame_s=1.0 / 30.0, keep=None, words=None):
    """THE sound-based verdict on keep edges the transcript puts inside a
    word (midword_boundaries) — one rule for the keep tools' warnings, the
    render's MID-WORD AUDIT, critique_short and check_speech. Each hit comes
    back with ``db`` (the level at the cut; None unread), ``loud`` (the cut
    clips sound: judged as cut_audio.place_edge judges it) and ``safe``
    (where the cut should sit: place_edge on the same sound, inside the
    edge's own span; None when it cannot move or the sound is unread).

    The ASR's word edges run 0.05-0.25 s off the sound, so the four checks
    used to disagree: the audit asked for 846.63 where the keep's 'word
    end' advice said 846.75 (Gates s15 4364), 2361.30 vs 2361.49 (4383), and
    critique_short's op moved an edge onto a transcript edge that the next
    render's audit flagged again (Diamandis 4416-4425)."""
    import cut_audio
    end_level = cut_audio._robust(level, frame_s) if level else None
    spans = [(float(s), float(e)) for s, e in keep or []]
    out = []
    for h in hits:
        b = float(h["boundary"])
        lv = end_level if h["kind"] == "end" else level
        db = lv(b) if lv else None
        if db is None:
            out.append(dict(h, db=None, loud=True, safe=None))
            continue
        # the floor read on the same reader place_edge judged that edge on
        # (an end's: the loudest of the frame either side); a clear dip
        # between sounds is a pause whatever the transcript's word times
        # say: the index's boundaries run up to ~0.1 s off, and the pause
        # before a word its onset was moved back to (cut_audio.
        # _contiguous_onset) sits inside the index's previous word
        loud = cut_audio._is_loud(db, cut_audio._floor(lv, b)) and \
            _dip_depth(lv, b) < cut_audio.ONSET_DIP_DB
        safe = None
        if loud and words:
            i = next((k for k, (s, e) in enumerate(spans)
                      if abs((s if h["kind"] == "start" else e) - b) < 0.006),
                     None)
            lo = hi = None
            if i is not None:
                if h["kind"] == "start":
                    lo = spans[i - 1][1] + 0.02 if i else 0.0
                    hi = spans[i][1] - 0.05
                else:
                    lo = spans[i][0] + 0.05
                    hi = spans[i + 1][0] - 0.02 if i + 1 < len(spans) \
                        else None
            try:
                got = cut_audio.place_edge(h["kind"], b, words, lv, lo, hi)
            except Exception:  # noqa: BLE001 — a verdict never fails a check
                got = None
            if got and got.get("why") is not None and \
                    abs(float(got["t"]) - b) >= 0.005:
                safe = round(float(got["t"]), 2)
        out.append(dict(h, db=db, loud=loud, safe=safe))
    return out


def _dip_depth(level, b, reach=0.08):
    """How far (dB) the sound at b sits under the loudest point within
    ``reach`` either side; 0.0 when the sound cannot be read."""
    vals = [level(round(b + k * 0.01, 2)) for k in range(-int(reach * 100),
                                                         int(reach * 100) + 1)]
    vals = [v for v in vals if v is not None]
    here = level(b)
    if not vals or here is None:
        return 0.0
    return max(vals) - here


def kept_share(side, b, t0, t1):
    """Share of the word [t0, t1] a keep edge at b keeps ('start': the part
    after b, 'end': the part before it)."""
    span = max(1e-6, float(t1) - float(t0))
    return (float(t1) - b) / span if side == "start" else (b - float(t0)) / span


def snap_keep_to_words(keep, words, duration):
    """Move any keep boundary that lands inside a word OUTWARD to the word
    edge (span start -> word start, span end -> word end), so whole words
    survive — unless it keeps less than KEEP_WORD_FRAC of the word: then it
    moves to the word's far edge and the word is cut. Returns a new merged,
    sorted keep list.

    Round 100 — BREATH PADDING on the snapped edges. Whisper's word t1 runs
    consistently early (the model marks the end of the voiced core, not the
    release of the final consonant), so cutting exactly AT t1 audibly clips
    the word's tail — the "ends mid-word" complaint from the Aug 8 shorts,
    where every clip boundary was a snapped word edge. The end edge now
    carries +0.12s of breath and the start edge -0.05s of lead-in, each
    clamped so the pad can never swallow a neighbouring word."""
    starts = sorted(float(w["t0"]) for w in words) if words else []
    ends = sorted(float(w["t1"]) for w in words) if words else []

    # A word that TOUCHES the edge (starts exactly where the snapped word
    # ends: 'What|what is', 'an|an AI') is the neighbour the pad must not
    # swallow: the lead-in of 'what' at 974.90 reached into 'What' and the
    # audio-safe pass then kept all of it (Oct 2026, four shorts).
    def _next_start_after(t):
        i = bisect.bisect_left(starts, t - 1e-6)
        return starts[i] if i < len(starts) else None

    def _prev_end_before(t):
        i = bisect.bisect_right(ends, t + 1e-6)
        return ends[i - 1] if i > 0 else None

    snapped = []
    for s, e in keep:
        hs = word_at_boundary(words, s)
        he = word_at_boundary(words, e)
        if hs and kept_share("start", s, hs["t0"], hs["t1"]) < KEEP_WORD_FRAC:
            s, hs = float(hs["t1"]), None        # the word is cut: start after it
        if he and kept_share("end", e, he["t0"], he["t1"]) < KEEP_WORD_FRAC:
            e, he = float(he["t0"]), None        # ...end before it
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
