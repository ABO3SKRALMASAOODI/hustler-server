"""Source-timeline <-> output-timeline mapping for a set of keep segments,
optionally with inserts spliced at keep boundaries and speed spans remapping
time (round 35).

Everything the agent writes is in SOURCE seconds (except music, voiceover,
overlays and texts, which are output-positioned by definition). The renderer
uses this mapping to place captions, volume automation, and ducking windows
on the OUTPUT (final program) timeline after the cuts are applied, speed
ramps remap time, and inserts are spliced in. A JS mirror of this math lives
in the studio page (frontend repo) — keep in sync.

Speed model: `speed` is the EDL's list of SOURCE-time spans with a constant
factor each (validated non-overlapping). Each keep segment is split into
constant-rate pieces by schemas.speed_pieces — the single source of truth
for the split — and a piece of source length L occupies L/factor output
seconds. With no speed spans every code path below reduces EXACTLY to the
pre-speed behavior (piece factor 1.0, one piece per segment).
"""

try:
    from schemas import (speed_pieces, clip_anim, keep_boundaries,
                         SCREEN_TAKEOVER_MIN_S)
except ImportError:      # loaded standalone by the backend (importlib):
    # routes/video.py registers the schemas module as 'worker_schemas'
    from worker_schemas import (speed_pieces, clip_anim, keep_boundaries,
                                SCREEN_TAKEOVER_MIN_S)


def _ins_tuple(i):
    if isinstance(i, dict):
        return (float(i["at_output_s"]), float(i["duration_s"]))
    return (float(i.at_output_s), float(i.duration_s))


def _ins_sort_key(i):
    """Program order of the inserts, and ONLY at_output_s decides it.

    Round 61. Two inserts can legitimately share a boundary — that is the only
    way the timeline can express "this clip, then that clip, both spliced at
    the same cut", and it is exactly what splitting one inserted clip in two
    produces (both halves stay at the boundary; source_start_s says which half
    is which). The order used to fall out of sorting the (at, duration) TUPLE,
    which made the SHORTER half play first: split a 24s clip at 15s and the
    tail played before the head. Duration is not an intention. List order is —
    it is the order the halves were written, the order the agent appended its
    b-roll, the order the user sees in the EDL — so ties keep it, which
    Python's stable sort gives for free.

    Nothing else in this module cares: every offset here sums the durations at
    or before a boundary, and a sum does not have an order.
    """
    return _ins_tuple(i)[0]


def _ins_id(i):
    return i.get("id") if isinstance(i, dict) else getattr(i, "id", None)


def _ins_asset(inserts, iid):
    for i in (inserts or []):
        if _ins_id(i) == iid:
            return (i.get("asset_key") if isinstance(i, dict)
                    else getattr(i, "asset_key", None))
    return None


def insert_windows(inserts, tl):
    """{insert id: (program_start, program_end)} for a Timeline built from
    the SAME insert list.

    Timeline sorts its inserts by at_output_s (see _ins_sort_key) and
    insert_positions() returns windows in that order, so re-sorting the
    items by the same key pairs each id with its own window. Python's sort
    is stable, so two inserts sharing a position stay in list order on both
    sides and still get the right window each — the ambiguity that made
    callers guess a "last matching index" before.
    """
    ordered = sorted((inserts or []), key=_ins_sort_key)
    out = {}
    for item, (start, dur) in zip(ordered, tl.insert_positions()):
        iid = _ins_id(item)
        if iid:
            out[iid] = (round(start, 2), round(start + dur, 2))
    return out


def card_text_window(card_start, card_end):
    """Program window for text that OWNS a spliced card, held a hair inside
    it so the entrance/exit animation plays on the blank frame instead of
    bleeding onto the footage either side. One definition, used both when
    the card is created and every time it is re-anchored."""
    pad = min(0.12, (card_end - card_start) / 8.0)
    return round(card_start + pad, 2), round(card_end - pad, 2)


class Timeline:
    def __init__(self, keep, inserts=None, speed=None):
        """keep: sorted, non-overlapping [[s, e], ...] in source seconds.
        inserts: items with at_output_s (a position in the PRE-INSERT output
        timeline, i.e. a keep boundary) and duration_s. An insert at a
        boundary plays BEFORE the segment that starts there.
        speed: the EDL's speed span list (dicts or SpeedSpan models), or
        None for the classic 1:1 timeline."""
        self.segs = [(float(s), float(e)) for s, e in keep]
        self.speed = list(speed or [])
        # Stable, keyed on at_output_s alone — insert_windows pairs ids to
        # windows by sorting the SAME way, so the two must agree exactly.
        self.ins = [_ins_tuple(i)
                    for i in sorted((inserts or []), key=_ins_sort_key)]
        # Constant-rate pieces per segment: [(src_s, src_e, factor)].
        self.pieces = [speed_pieces(s, e, self.speed) for s, e in self.segs]
        self.seg_out_len = [
            sum((pe - ps) / f for ps, pe, f in pcs) for pcs in self.pieces]
        pre = []                   # pre-insert output start of each segment
        acc = 0.0
        for L in self.seg_out_len:
            pre.append(acc)
            acc += L
        self.pre_duration = acc
        self.inserted_total = sum(d for _, d in self.ins)
        # Final-program start of each segment: its pre-insert start shifted
        # by every insert at or before that boundary.
        self.offsets = [
            p + sum(d for at, d in self.ins if at <= p + 1e-6) for p in pre]
        self.out_duration = acc + self.inserted_total

    def insert_positions(self):
        """[(final_start, duration)] for each insert, in program order."""
        out, consumed = [], 0.0
        for at, d in self.ins:
            out.append((at + consumed, d))
            consumed += d
        return out

    def seg_program_range(self, t):
        """(program_start, program_end) of the kept segment containing source
        time t, or None if t is cut. This is the continuous run of program
        time that footage plays in — nothing spliced can fall inside it."""
        for i, (s, e) in enumerate(self.segs):
            if s - 1e-6 <= t <= e + 1e-6:
                return self.offsets[i], self.offsets[i] + self.seg_out_len[i]
        return None

    def src_to_out(self, t):
        """Map a source time to final-program time. Times inside a cut
        region map to None."""
        for (s, e), pcs, off in zip(self.segs, self.pieces, self.offsets):
            if s - 1e-6 <= t <= e + 1e-6:
                tt = min(max(t, s), e)
                acc = 0.0
                for ps, pe, f in pcs:
                    if tt <= pe + 1e-9:
                        return off + acc + max(0.0, tt - ps) / f
                    acc += (pe - ps) / f
                return off + acc
        return None

    def out_to_src(self, t):
        """Map a final-program time back to source time. Times inside a
        spliced insert (or past the end) map to None."""
        for (s, e), pcs, off, L in zip(self.segs, self.pieces, self.offsets,
                                       self.seg_out_len):
            if off - 1e-6 <= t <= off + L + 1e-6:
                rem = min(max(t - off, 0.0), L)
                for ps, pe, f in pcs:
                    plen = (pe - ps) / f
                    if rem <= plen + 1e-9:
                        return ps + rem * f
                    rem -= plen
                return e
        return None

    @staticmethod
    def _off_in_pieces(pcs, tt):
        """Offset of source time tt within ONE segment's own pieces
        (segment-local output seconds)."""
        acc = 0.0
        for ps, pe, f in pcs:
            if tt <= pe + 1e-9:
                return acc + max(0.0, tt - ps) / f
            acc += (pe - ps) / f
        return acc

    def span_to_out(self, t0, t1):
        """Map a source span to a list of output spans (a span crossing cut
        regions splits into pieces; fully-cut spans map to []).

        Endpoints resolve within the CURRENT iterated segment — never via
        src_to_out, whose in-order first-match resolves a time shared by two
        CONTIGUOUS keep segments (the shape insert_media's mid-take split
        writes) through the EARLIER one, silently dropping the spliced
        insert's duration from the later span. That bug shipped briefly in
        round 35 and changed legacy duck windows; the per-segment arithmetic
        below is byte-identical to the pre-round-35 renderer for factor-1.0
        pieces."""
        out = []
        for (s, e), pcs, off in zip(self.segs, self.pieces, self.offsets):
            lo, hi = max(t0, s), min(t1, e)
            if hi - lo > 1e-3:
                if len(pcs) == 1 and pcs[0][2] == 1.0:
                    a = off + lo - s       # exact legacy arithmetic
                    b = off + hi - s
                else:
                    a = off + self._off_in_pieces(pcs, lo)
                    b = off + self._off_in_pieces(pcs, hi)
                if b - a > 1e-3:
                    out.append((a, b))
        return out

    def kept_words(self, words):
        """Words (objects or dicts with t0/t1) whose midpoint survives the
        cut, with output-mapped times. Returns [{'w', 't0', 't1'}] in output
        time, in order."""
        out = []
        for w in words:
            t0 = w["t0"] if isinstance(w, dict) else w.t0
            t1 = w["t1"] if isinstance(w, dict) else w.t1
            token = w["w"] if isinstance(w, dict) else w.w
            mid = (t0 + t1) / 2.0
            o = self.src_to_out(mid)
            if o is None:
                continue
            o0 = self.src_to_out(t0)
            o1 = self.src_to_out(t1)
            half = (t1 - t0) / 2.0
            t0o = o0 if o0 is not None else max(0.0, o - half)
            t1o = o1 if o1 is not None else min(self.out_duration, o + half)
            # Keep the word inside the ONE run of program time it actually
            # plays in. A word's edges map independently, and a source time
            # sitting exactly on a keep boundary is ambiguous — src_to_out
            # resolves it to the earlier segment. So a word starting exactly
            # where a segment was split (which is where insert_media splits,
            # deliberately, to avoid clipping a word) had its start mapped
            # BEFORE the spliced media and its end AFTER it: the caption for
            # that word stretched across the whole insert and burned over it.
            # That is the "captions overlap the title card" report. Clamping
            # to the midpoint's own segment makes the ambiguity resolve the
            # same way the footage does.
            rng = self.seg_program_range(mid)
            if rng:
                lo, hi = rng
                t0o = min(max(t0o, lo), hi)
                t1o = min(max(t1o, lo), hi)
            out.append({"w": token, "t0": t0o, "t1": t1o})
        return out


def program_blocks(edl):
    """The ASSEMBLED PROGRAM in viewer order — round 71.

    Users think in OUTPUT scenes ("the second scene is the laptop shot"),
    while every index artifact speaks source time and the EDL summary only
    counts inserts without placing them. This is the missing map: one entry
    per render block (keep segment or spliced insert), in the exact order
    build_filtergraph assembles them — the block loop below is the same one
    transition_junctions uses and renderer.build_filtergraph mirrors, so a
    scene number here IS the piece of program the viewer sees at that time.

    Returns [{"n", "kind": "footage"|"insert", "out_start", "out_end",
              "src_start"/"src_end" (footage) or
              "id"/"asset_key"/"media"/"clip_start_s" (insert)}].
    A canvas program (no keep) is just its inserts in order.
    """
    keep = [(float(k[0]), float(k[1])) for k in (edl.get("keep") or [])
            if k is not None and len(k) >= 2]
    inserts = sorted(list(edl.get("inserts") or []), key=_ins_sort_key)
    tl = Timeline(keep, inserts, edl.get("speed"))
    ins_order = [_ins_tuple(i) for i in inserts]
    at_list = [at for at, _d in ins_order]
    blocks, ins_j, pre = [], 0, 0.0
    for i, (s, e) in enumerate(keep):
        while ins_j < len(at_list) and at_list[ins_j] <= pre + 1e-6:
            blocks.append(("ins", ins_j))
            ins_j += 1
        blocks.append(("seg", i))
        pre += tl.seg_out_len[i]
    while ins_j < len(at_list):
        blocks.append(("ins", ins_j))
        ins_j += 1

    out, acc = [], 0.0
    for n, (kind, i) in enumerate(blocks, start=1):
        if kind == "seg":
            L = tl.seg_out_len[i]
            out.append({"n": n, "kind": "footage",
                        "out_start": round(acc, 2),
                        "out_end": round(acc + L, 2),
                        "src_start": round(keep[i][0], 2),
                        "src_end": round(keep[i][1], 2)})
        else:
            item = inserts[i]
            d = float(item.get("duration_s") if isinstance(item, dict)
                      else item.duration_s)
            get = (item.get if isinstance(item, dict)
                   else lambda k, _i=item: getattr(_i, k, None))
            out.append({"n": n, "kind": "insert",
                        "out_start": round(acc, 2),
                        "out_end": round(acc + d, 2),
                        "id": get("id"),
                        "asset_key": get("asset_key"),
                        "media": get("kind") or "video",
                        "clip_start_s": get("source_start_s")})
            L = d
        acc += L
    return out


def describe_program(edl, name_of=None):
    """Multi-line, viewer-ordered scene listing of the assembled program.

    This is what lets the agent resolve "the second scene": scenes are
    numbered in OUTPUT order, each with its program window and where its
    pixels come from (main-footage source range, or the inserted clip and
    the offset into it). `name_of` optionally maps an asset_key to a
    display filename. Returns "" for an empty program.
    """
    try:
        blocks = program_blocks(edl)
    except Exception:
        return ""
    if not blocks:
        return ""
    total = blocks[-1]["out_end"]
    lines = [f"THE ASSEMBLED PROGRAM — {len(blocks)} scene"
             f"{'s' if len(blocks) != 1 else ''}, {total:g}s, in the order "
             "the VIEWER sees them (output seconds). \"The second scene\" "
             "means scene 2 of THIS list:"]
    for b in blocks:
        span = f"  scene {b['n']}  {b['out_start']:g}-{b['out_end']:g}s: "
        if b["kind"] == "footage":
            lines.append(span + f"main footage {b['src_start']:g}-"
                         f"{b['src_end']:g}s (source clock)")
        else:
            name = None
            if name_of:
                try:
                    name = name_of(b["asset_key"])
                except Exception:
                    name = None
            label = name or (b["asset_key"] or "?").split("/")[-1]
            frm = (f" from {b['clip_start_s']:g}s of that clip"
                   if b.get("clip_start_s") else "")
            lines.append(span + f"inserted {b['media']} '{label}' "
                         f"[{b['id']}]{frm}")
    return "\n".join(lines)


def keep_anchor_times(keep):
    """The SOURCE time that identifies each keep boundary.

    keep_boundaries() gives the OUTPUT position of every splice point; those
    positions move whenever ANY earlier segment is trimmed or deleted, so they
    cannot say WHICH junction an insert was sitting at. The source time can:
    boundary i is the seam immediately BEFORE keep[i], so it is identified by
    that segment's start (an insert at a boundary plays before the segment that
    starts there — see Timeline), and the final boundary is the end of the last
    segment.

    Returns len(keep) + 1 anchors, index-aligned with keep_boundaries().
    """
    keep = [(float(s), float(e)) for s, e in (keep or [])]
    if not keep:
        return [0.0]
    return [s for s, _e in keep] + [keep[-1][1]]


def resnap_inserts(inserts, old_keep, new_keep, old_speed=None,
                   new_speed=None):
    """Follow every insert to the same JUNCTION after the keep list changed.

    Returns (new_inserts, notes).

    An insert's at_output_s must land exactly on a keep boundary or
    validate_edl rejects the whole EDL — so every write that changes the keep
    list has to move the inserts with it. Doing that by nearest OUTPUT VALUE is
    what shipped before, and it is wrong in both directions:

      * It can miss entirely. A real session (project 246) held one clip at the
        LAST boundary, 33.57s, of keep [[111.85, 130.08], [339.27, 354.61]].
        Deleting either take removes that boundary, the nearest surviving one is
        18.23s — 15s away — and the backend UI ops never re-snapped at all, so
        the user clicked delete and got
        "at_output_s 33.57 is not on a keep-segment boundary" with nothing
        deleted. There was no way out of that state from the timeline.
      * When it does snap, it can snap to the WRONG cut, because every boundary
        after an edit has a different output value than it had before. Trimming
        4s off take one moves take two's junction 4s earlier, and an insert
        that was at take three can land at take two.

    So the junction is followed through the SOURCE, exactly like the
    content-anchored effects in remap_program_items: the anchor is the footage
    the insert sat in front of, which does not move when something upstream is
    cut. Ties resolve to the LATER anchor — when the take an insert sat in
    front of is deleted outright, its seam collapses onto the footage that now
    follows, which is where the clip visibly was.

    With the keep list unchanged (a speed write) the anchors are identical, so
    every insert matches its own anchor exactly and this reduces to
    "same junction index, new speed-remapped clock" — the behaviour
    _write_speed had to special-case before.
    """
    inserts = list(inserts or [])
    if not inserts:
        return inserts, []
    old_bounds = keep_boundaries(old_keep, old_speed)
    new_bounds = keep_boundaries(new_keep, new_speed)
    old_anchors = keep_anchor_times(old_keep)
    new_anchors = keep_anchor_times(new_keep)
    if len(old_bounds) != len(old_anchors) or \
            len(new_bounds) != len(new_anchors) or not new_bounds:
        return inserts, []          # cannot reason; leave it to validation
    out, notes = [], []
    for ins in inserts:
        at = float(ins["at_output_s"] if isinstance(ins, dict)
                   else ins.at_output_s)
        oi = min(range(len(old_bounds)),
                 key=lambda k: (abs(old_bounds[k] - at), -k))
        anchor = old_anchors[oi]
        nj = min(range(len(new_anchors)),
                 key=lambda k: (abs(new_anchors[k] - anchor), -new_anchors[k]))
        new_at = new_bounds[nj]
        if abs(new_at - at) > 0.01:
            iid = _ins_id(ins) or "?"
            where = ("the start of the edit" if nj == 0
                     else "the end of the edit" if nj == len(new_bounds) - 1
                     else f"the cut at {new_at}s")
            notes.append(f"note: insert {iid} now splices at {new_at}s — "
                         f"{where}, still in front of the same footage.")
        if isinstance(ins, dict):
            out.append({**ins, "at_output_s": new_at})
        else:
            ins.at_output_s = new_at
            out.append(ins)
    return out, notes


def remap_program_span(old_tl, new_tl, s, e):
    """Follow a program-time span from an OLD cut to a NEW one via the SOURCE
    footage underneath it.

    Content-anchored effects (a zoom placed on a moment) must move with that
    moment when an unrelated cut shifts it earlier — otherwise the zoom silently
    lands on different footage. Returns (new_s, new_e), None when the footage the
    span covered was cut away entirely, or None when an endpoint sits inside a
    spliced insert (which has no source time) and the caller must fall back to
    clamping. A span straddling an internal cut maps to one contiguous span
    because the new timeline collapses the removed middle.
    """
    a, b = old_tl.out_to_src(s), old_tl.out_to_src(e)
    if a is None or b is None:
        return None
    pieces = new_tl.span_to_out(a, b)
    if not pieces:
        return None
    return round(pieces[0][0], 2), round(pieces[-1][1], 2)


def remap_program_items(edl, old_tl, new_tl):
    """Re-anchor every program-time collection after the program's time base
    changed — a keep write, a speed write, or ANY insert add/move/resize/
    removal (all of them shift where footage lands in the output). Mutates
    edl in place, returns disclosure notes. A stale item that no longer fits
    would fail validation and reject the whole write — so a pre-existing
    zoom could make an unrelated cut impossible. Shared by the worker tools
    AND the backend UI ops (routes/video.py loads this module), so both
    surfaces apply ONE anchor policy. Each collection follows its own anchor:
      zooms /    - CONTENT-anchored ("push in on the skyline", "grain that
      stylize      moment"): remap through the source so the window stays on
                   the footage it was placed on; drop it when that footage
                   is cut away. Output-time units do NOT decide the anchor —
                   what the item is attached to decides it.
      sfx        - CONTENT-anchored one-shot ("a whoosh on that cut"): remap
                   the POINT through the source; drop when cut away.
      music /    - PROGRAM-anchored ("music under the whole video", "narrate
      voiceover    at 10s"): clamp to the new program length.
      regions    - PROGRAM-anchored censor window: clamp (drop if outside).
      overlays / - PROGRAM-anchored (they cover a span of the EDIT, not a
      texts        moment of the footage): clamp ends, drop when outside;
                   keyframed x/y is trimmed (clip_anim) when a window
                   shrinks, or validation rejects the stranded keyframes."""
    region_notes = []
    prog = round(new_tl.out_duration, 2)

    fx = dict(edl.get("effects") or {})
    fx_changed = False
    if fx.get("zooms"):
        kept_zooms = []
        for z in fx["zooms"]:
            z = dict(z)
            moved = remap_program_span(
                old_tl, new_tl, float(z["start"]), float(z["end"]))
            if moved is None:
                # Endpoints inside a spliced insert have no source time; only
                # a genuinely cut-away zoom maps to nothing.
                if old_tl.out_to_src(float(z["start"])) is None or \
                        old_tl.out_to_src(float(z["end"])) is None:
                    # Kept — but CLAMPED to the new program (round 74).
                    # Stylize has clamped this exact case since round 71;
                    # zooms never did, so an insert-anchored zoom left
                    # hanging past a shortened edit made validate_edl
                    # reject the WHOLE write: a user splitting the last
                    # scene and deleting the tail chunk was refused with a
                    # raw "effects.zooms[0]: end 9.5 exceeds the limit
                    # 8.88s" — blocked from an ordinary cut by a zoom the
                    # cut never touched.
                    if float(z["start"]) > max(0.0, prog - 0.2):
                        region_notes.append(
                            f"note: zoom {z.get('id')} was removed — its "
                            "window falls outside the shortened edit.")
                        fx_changed = True
                        continue
                    if float(z["end"]) > prog:
                        z["end"] = round(prog, 2)
                        region_notes.append(
                            f"note: zoom {z.get('id')} now ends at "
                            f"{z['end']}s to fit the shortened edit.")
                        fx_changed = True
                    kept_zooms.append(z)
                    continue
                region_notes.append(
                    f"note: zoom {z.get('id')} was removed — the footage it "
                    "was on is no longer in the edit.")
                fx_changed = True
                continue
            ns, ne = moved
            if ne - ns < 0.2:
                region_notes.append(
                    f"note: zoom {z.get('id')} was removed — only "
                    f"{ne - ns:.2f}s of the footage it was on survives the "
                    "cut.")
                fx_changed = True
                continue
            if (ns, ne) != (z["start"], z["end"]):
                region_notes.append(
                    f"note: zoom {z.get('id')} moved to {ns}-{ne}s (output "
                    "time) so it stays on the same footage.")
                z["start"], z["end"] = ns, ne
                fx_changed = True
            kept_zooms.append(z)
        if fx_changed:
            fx["zooms"] = kept_zooms
    if fx.get("stylize"):
        # Content-anchored like zooms — "make THAT moment VHS" must follow
        # the moment. Whole-video items (start None) have no window to move.
        kept_st = []
        st_changed = False
        for st in fx["stylize"]:
            st = dict(st)
            if st.get("start") is None:
                kept_st.append(st)
                continue
            moved = remap_program_span(
                old_tl, new_tl, float(st["start"]), float(st["end"]))
            if moved is None:
                if old_tl.out_to_src(float(st["start"])) is None or \
                        old_tl.out_to_src(float(st["end"])) is None:
                    # anchored inside a spliced insert: keep the program
                    # window, but clamp so validation cannot reject the write
                    if float(st["start"]) > max(0.0, prog - 0.1):
                        region_notes.append(
                            f"note: stylize {st.get('id')} "
                            f"({st.get('kind')}) was removed — its window "
                            "falls outside the shortened edit.")
                        st_changed = True
                        continue
                    if float(st["end"]) > prog:
                        st["end"] = round(prog, 2)
                        region_notes.append(
                            f"note: stylize {st.get('id')} "
                            f"({st.get('kind')}) now ends at {st['end']}s "
                            "to fit the shortened edit.")
                        st_changed = True
                    kept_st.append(st)
                    continue
                region_notes.append(
                    f"note: stylize {st.get('id')} ({st.get('kind')}) was "
                    "removed — the footage it was on is no longer in the "
                    "edit.")
                st_changed = True
                continue
            ns, ne = moved
            if ne - ns < 0.1:
                region_notes.append(
                    f"note: stylize {st.get('id')} ({st.get('kind')}) was "
                    "removed — almost none of the footage it was on "
                    "survives the cut.")
                st_changed = True
                continue
            if (ns, ne) != (st["start"], st["end"]):
                region_notes.append(
                    f"note: stylize {st.get('id')} ({st.get('kind')}) moved "
                    f"to {ns}-{ne}s so it stays on the same footage.")
                st["start"], st["end"] = ns, ne
                st_changed = True
            kept_st.append(st)
        if st_changed:
            fx["stylize"] = kept_st
            fx_changed = True
    if fx.get("regions"):
        kept_regs = []
        rg_changed = False
        for r in fx["regions"]:
            r = dict(r)
            if r.get("end") is not None and r["end"] > prog:
                if (r.get("start") or 0.0) >= prog - 0.05:
                    region_notes.append(
                        f"note: censor region {r.get('id')} was removed — "
                        "its time window falls entirely outside the "
                        "shortened edit.")
                    rg_changed = True
                    continue
                r["end"] = round(prog, 2)
                region_notes.append(
                    f"note: censor region {r.get('id')}'s time window now "
                    f"ends at {r['end']}s to fit the shortened edit.")
                rg_changed = True
            kept_regs.append(r)
        if rg_changed:
            fx["regions"] = kept_regs
            fx_changed = True
    if fx.get("frame_shifts"):
        # CONTENT-anchored POINT, exactly like an sfx: "go vertical when he
        # says X" belongs to the moment he says it. Left in program time, a cut
        # made earlier would have the frame close in over the wrong sentence —
        # and unlike a drifting sound, a drifting matte is a full-screen
        # change nobody could mistake for anything but a bug.
        kept_fs, fs_changed = [], False
        for sh in fx["frame_shifts"]:
            sh = dict(sh)
            at = float(sh["at"])
            src = old_tl.out_to_src(at)
            new_at = new_tl.src_to_out(src) if src is not None else at
            if new_at is None:
                region_notes.append(
                    f"note: aspect shift {sh.get('id')} was removed — the "
                    "moment it was placed on is no longer in the edit.")
                fs_changed = True
                continue
            if abs(new_at - at) > 0.01:
                region_notes.append(
                    f"note: aspect shift {sh.get('id')} moved to "
                    f"{round(new_at, 2)}s so it stays on the same moment.")
                fs_changed = True
            sh["at"] = round(new_at, 2)
            if sh["at"] > max(0.0, prog - 0.05):
                region_notes.append(
                    f"note: aspect shift {sh.get('id')} was removed — it "
                    "sits after the end of the shortened edit.")
                fs_changed = True
                continue
            kept_fs.append(sh)
        if fs_changed:
            fx["frame_shifts"] = kept_fs or None
            fx_changed = True
    if fx_changed:
        edl["effects"] = fx

    if edl.get("music"):
        old_prog = round(old_tl.out_duration, 2)
        kept_music = []
        for m in edl["music"]:
            m = dict(m)
            # A bed laid under the WHOLE video follows the program in both
            # directions. Clamping only (the old behaviour) meant any insert
            # that GREW the program left the music short: add a title card
            # and the last seconds of the edit silently lose their music,
            # with nothing said. The agent had to notice and call
            # set_music_fit by hand, and when it forgot, the studio drew a
            # music lane visibly shorter than the video — which is exactly
            # what the founder reported seeing.
            covers_all = (float(m["start"]) <= 0.05
                          and float(m["end"]) >= old_prog - 0.05)
            if covers_all:
                if abs(float(m["end"]) - prog) > 0.01:
                    m["end"] = round(prog, 2)
                    region_notes.append(
                        f"note: music {m.get('id')} now runs to {m['end']}s, "
                        "still covering the whole video.")
                kept_music.append(m)
                continue
            # A deliberately shorter cue keeps its own length; only trim it
            # when the edit no longer reaches that far.
            if m["end"] > prog:
                if m["start"] >= prog - 0.1:
                    region_notes.append(
                        f"note: music {m.get('id')} was removed — it starts "
                        "after the end of the shortened edit.")
                    continue
                m["end"] = round(prog, 2)
                region_notes.append(
                    f"note: music {m.get('id')} now ends at {m['end']}s to "
                    "fit the shortened edit.")
            kept_music.append(m)
        edl["music"] = kept_music
    if edl.get("voiceover"):
        kept_vo = []
        for v in edl["voiceover"]:
            v = dict(v)
            if v["start_output_s"] > max(0.0, prog - 0.05):
                region_notes.append(
                    f"note: voiceover {v.get('id')} was removed — it starts "
                    "after the end of the shortened edit.")
                continue
            kept_vo.append(v)
        edl["voiceover"] = kept_vo
    if edl.get("sfx"):
        # CONTENT-anchored, like a zoom — NOT program-anchored like music. The
        # prompt tells the agent to land a whoosh ON a cut point and an impact
        # ON the reveal, so the sound belongs to a moment in the footage and
        # has to follow it. Left in program time it silently drifts by the
        # length of every cut made before it: trim 10s off the front and the
        # whoosh that was on the cut now fires 10s into the next take, with no
        # note, while write_edl still reports success.
        #
        # A point, not a span, so remap_program_span is no use here — a
        # zero-length span maps to no output pieces and returns None. Map the
        # point itself through the source.
        kept_sfx = []
        for s in edl["sfx"]:
            s = dict(s)
            at = float(s["at"])
            src = old_tl.out_to_src(at)
            # No source time means the point sits inside a spliced insert;
            # those keep their program position.
            new_at = new_tl.src_to_out(src) if src is not None else at
            if new_at is None:
                region_notes.append(
                    f"note: sound effect {s.get('id')} was removed — the "
                    "moment it was placed on is no longer in the edit.")
                continue
            if abs(new_at - at) > 0.01:
                region_notes.append(
                    f"note: sound effect {s.get('id')} moved to "
                    f"{round(new_at, 2)}s so it stays on the same moment.")
            s["at"] = round(new_at, 2)
            # A point past the end of a shortened edit is dropped, not
            # clamped: clamping would pile every orphan onto the last frame.
            # Without this the sfx bounds check in validate_edl rejects the
            # whole CUT — the user asks to trim the end and is told the edit
            # is invalid, over a sound they never mentioned.
            if s["at"] > max(0.0, prog - 0.05):
                region_notes.append(
                    f"note: sound effect {s.get('id')} was removed — it sits "
                    "after the end of the shortened edit.")
                continue
            kept_sfx.append(s)
        edl["sfx"] = kept_sfx
    if edl.get("overlays"):
        kept_ov = []
        # A screen takeover is pinned to a clip, not to a span of the edit: its
        # window has to END exactly where the clip it hands off to begins, or
        # the one join the effect exists to hide becomes a jump. So it follows
        # the CLIP through the remap and is re-derived from where that clip
        # ended up, rather than being clamped like an ordinary overlay — and
        # when the clip is gone, so is the takeover. Clamping it (the generic
        # path below) would leave a push into a screen that hands off to
        # nothing, which renders as the shot snapping back at full zoom.
        tk_windows = insert_windows(edl.get("inserts"), new_tl)

        def _scene_floor(t):
            """out_start of the program block that ENDS at t — the scene the
            push actually films. Falls back to the block containing t."""
            try:
                blocks = program_blocks(edl)
            except Exception:
                return 0.0
            for b in blocks:
                if abs(b["out_end"] - t) < 0.05:
                    return float(b["out_start"])
            for b in blocks:
                if b["out_start"] - 1e-6 <= t - 0.01 < b["out_end"]:
                    return float(b["out_start"])
            return 0.0

        for ov in edl["overlays"]:
            ov = dict(ov)
            if ov.get("screen"):
                hit = next(((iid, w) for iid, w in tk_windows.items()
                            if _ins_asset(edl.get("inserts"), iid)
                            == ov.get("asset_key")
                            and abs(w[0] - (float(ov["start"])
                                            + float(ov["duration_s"]))) < 2.5),
                           None)
                if hit is None:
                    region_notes.append(
                        f"note: screen takeover {ov.get('id')} was removed — "
                        "the clip it pushed into is no longer in the edit.")
                    continue
                h_id, win = hit
                dur = float(ov["duration_s"])
                # The push films ONE scene (round 74). Its corners were
                # measured on the shot right before the handoff, so a window
                # crossing the cut BEHIND that shot pins content onto
                # different footage: a real launch video carried a 2.42s
                # push whose device shot had shrunk to 1.88s, and the first
                # half-second of the "transition" played over the previous
                # scene. The push shortens to the scene instead of bleeding
                # across the cut behind it.
                floor = _scene_floor(win[0])
                room = round(win[0] - floor - 0.05, 2)
                if room < SCREEN_TAKEOVER_MIN_S:
                    region_notes.append(
                        f"note: screen takeover {ov.get('id')} was removed — "
                        f"only {max(room, 0.0):.2f}s of the shot it pushes "
                        "into survives, too short for the push.")
                    continue
                if dur > room:
                    shift = round(dur - room, 3)
                    scr = dict(ov.get("screen") or {})
                    path = scr.get("corner_path")
                    if path:
                        # Knots are seconds from the window START, and the
                        # cut came off the FRONT: shift every knot and drop
                        # the ones now before zero. The renderer holds flat
                        # before the first surviving knot, so nothing needs
                        # interpolating; under two knots is no track at all.
                        kept_p = [[round(float(p[0]) - shift, 3)]
                                  + [float(v) for v in p[1:]]
                                  for p in path
                                  if float(p[0]) >= shift - 1e-6]
                        scr["corner_path"] = kept_p if len(kept_p) >= 2 \
                            else None
                        ov["screen"] = scr
                    ov["duration_s"] = dur = room
                    region_notes.append(
                        f"note: screen takeover {ov.get('id')} now pushes "
                        f"for {room}s — the shot it pushes into shrank, and "
                        "the push stays inside it.")
                new_start = round(win[0] - dur, 2)
                if new_start < 0.0:
                    region_notes.append(
                        f"note: screen takeover {ov.get('id')} was removed — "
                        "there is no longer room before the clip for the push.")
                    continue
                if abs(new_start - float(ov["start"])) > 0.01:
                    region_notes.append(
                        f"note: screen takeover {ov.get('id')} moved to "
                        f"{new_start}-{win[0]}s, staying joined to its clip.")
                    ov["start"] = new_start
                # The handoff is invisible ONLY while the pin's last frame
                # is the clip's first frame. The offset is re-derived from
                # the clip EVERY pass: a clip whose own clip_start changed
                # (set_insert_window) otherwise leaves the pin ending short
                # of the frame the clip opens on — a real EDL carried
                # source_start 5.66 against a clip at 8.78, a 0.7s jump
                # inside the one join the effect exists to hide.
                if ov.get("kind") == "video":
                    h_ins = next((i for i in (edl.get("inserts") or [])
                                  if i.get("id") == h_id), None)
                    if h_ins is not None:
                        want = round(max(0.0, float(
                            h_ins.get("source_start_s") or 0.0) - dur), 2)
                        if abs(want - float(ov.get("source_start_s")
                                            or 0.0)) > 0.01:
                            ov["source_start_s"] = want
                            region_notes.append(
                                f"note: screen takeover {ov.get('id')} "
                                "re-aligned its pinned footage so the "
                                "handoff stays on the same frame.")
                kept_ov.append(ov)
                continue
            if float(ov["start"]) > max(0.0, prog - 0.2):
                region_notes.append(
                    f"note: overlay {ov.get('id')} was removed — it starts "
                    "after the end of the shortened edit.")
                continue
            if float(ov["start"]) + float(ov["duration_s"]) > prog + 0.01:
                nd = round(prog - float(ov["start"]), 2)
                if nd < 0.2:
                    region_notes.append(
                        f"note: overlay {ov.get('id')} was removed — under "
                        "0.2s of its window survives the shortened edit.")
                    continue
                ov["duration_s"] = nd
                # keyframed motion must shrink WITH the window, or the
                # stranded keyframes make validation reject the whole write
                for prop in ("x", "y"):
                    if isinstance(ov.get(prop), list):
                        ov[prop] = clip_anim(ov[prop], nd)
                region_notes.append(
                    f"note: overlay {ov.get('id')} now ends at "
                    f"{round(float(ov['start']) + nd, 2)}s to fit the "
                    "shortened edit.")
            kept_ov.append(ov)
        edl["overlays"] = kept_ov
    if edl.get("texts"):
        # A text carrying anchor_insert does not live in program time at all
        # — it belongs to a spliced card, and the card moves whenever any
        # earlier insert is added, moved, resized or removed. Re-derive it
        # from the card's NEW window (and drop it when the card is gone) so
        # the words can never be left behind on the footage while the card
        # renders blank.
        windows = insert_windows(edl.get("inserts"), new_tl)
        kept_tx = []
        for tx in edl["texts"]:
            tx = dict(tx)
            behind = tx.get("behind")
            if behind:
                # CONTENT-anchored, and it has to be: the mask is pixels of
                # PARTICULAR footage (behind.src_start/src_end are source
                # seconds). Clamped in program time like an ordinary text, the
                # words would slide off their own matte the first time anything
                # upstream was trimmed — and the subject would then be cut out
                # of a different second of video, which reads as the person
                # being erased in the wrong place.
                moved = remap_program_span(old_tl, new_tl, float(tx["start"]),
                                           float(tx["end"]))
                if moved is None:
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\", behind the "
                        "subject) was removed — the footage it was on is no "
                        "longer in the edit.")
                    continue
                ns, ne = moved
                if ne - ns < 0.4:
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\", behind the "
                        "subject) was removed — too little of the footage it "
                        "was measured on survives the cut.")
                    continue
                if (ns, ne) != (tx.get("start"), tx.get("end")):
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\") moved to "
                        f"{ns}-{ne}s, staying behind the same subject.")
                    tx["start"], tx["end"] = ns, ne
                kept_tx.append(tx)
                continue
            anchor = tx.get("anchor_insert")
            if anchor:
                win = windows.get(anchor)
                if win is None:
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\") was removed "
                        "with the card it was on.")
                    continue
                ns, ne = card_text_window(*win)
                if ne - ns < 0.3:      # card too short to hold readable text
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\") was removed — "
                        "its card is now too short to show it.")
                    continue
                if (ns, ne) != (tx.get("start"), tx.get("end")):
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\") moved to "
                        f"{ns}-{ne}s, staying on its card.")
                    tx["start"], tx["end"] = ns, ne
                kept_tx.append(tx)
                continue
            if float(tx["end"]) > prog:
                if float(tx["start"]) >= prog - 0.3:
                    region_notes.append(
                        f"note: text {tx.get('id')} "
                        f"(\"{str(tx.get('text', ''))[:24]}\") was removed — "
                        "its window falls outside the shortened edit.")
                    continue
                tx["end"] = round(prog, 2)
                region_notes.append(
                    f"note: text {tx.get('id')} now ends at {tx['end']}s to "
                    "fit the shortened edit.")
            kept_tx.append(tx)
        edl["texts"] = kept_tx
    if edl.get("caption_mutes"):
        # CONTENT-anchored: a mute exists to keep captions off a particular
        # moment (the effect / graphic it was paired with), so it must follow
        # that footage exactly like the stylize it shadows. Left program-
        # anchored, a cut upstream would slide the mute onto innocent speech
        # and silently delete those captions instead.
        kept_mu = []
        for m0, m1 in edl["caption_mutes"]:
            moved = remap_program_span(old_tl, new_tl, float(m0), float(m1))
            if moved is None:
                # Anchored inside spliced media (which is never captioned
                # anyway): keep the window, clamped so it still validates.
                if old_tl.out_to_src(float(m0)) is None or \
                        old_tl.out_to_src(float(m1)) is None:
                    if float(m0) < max(0.0, prog - 0.05):
                        kept_mu.append([float(m0),
                                        round(min(float(m1), prog), 2)])
                    continue
                region_notes.append(
                    "note: a caption mute was dropped — the footage it was "
                    "on is no longer in the edit, so those captions show "
                    "again.")
                continue
            ns, ne = moved
            if ne - ns < 0.05:
                region_notes.append(
                    "note: a caption mute was dropped — almost none of the "
                    "footage it was on survives the cut.")
                continue
            if (ns, ne) != (m0, m1):
                region_notes.append(
                    f"note: a caption mute moved to {ns}-{ne}s so it stays "
                    "on the same moment.")
            kept_mu.append([ns, ne])
        edl["caption_mutes"] = kept_mu
    return region_notes


def _shot_at(shots, t):
    """Which indexed shot contains source second `t`, or None.

    Shots are contiguous and sorted, so a plain scan is fine (a long video has
    tens of them, not thousands). `t` is nudged INWARD by a frame's worth of
    time by the caller, because a keep segment's own end lands exactly on a
    shot boundary about as often as not and the answer there is ambiguous.
    """
    for sh in shots:
        try:
            if float(sh["start"]) - 1e-6 <= t < float(sh["end"]) + 1e-6:
                return int(sh["id"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


# A keep segment's endpoints sit ON the cut. Comparing the exact endpoints
# would ask "which shot owns this instant", which is ambiguous at a boundary;
# stepping ~one frame inside each side asks "which shot is the footage either
# side of this cut from", which is the real question.
_JUNCTION_EPS = 0.04


def transition_junctions(edl, index, n_blocks=None):
    """Which junctions a transition should land on.

    Returns a set of junction indices, where junction k sits between render
    block k and block k+1 (blocks are the keep segments with inserts spliced in
    at their boundaries — the same order build_filtergraph assembles).

    THE POINT OF THIS FUNCTION. After cut_silences a talking-head video has one
    junction per removed pause, and nearly all of them are inside a single
    continuous shot. Those are jump cuts, and a jump cut works by being
    invisible. A whip pan on each one fires a full-screen effect every couple
    of seconds through footage that never changed scene — a real user shipped
    exactly that and called it broken. So scope 'scene' keeps only the
    junctions where the footage genuinely changes:

      * either side is an INSERT (b-roll, title card, generated clip) — always
        a real scene change;
      * the two keep segments either side come from DIFFERENT indexed shots.

    With no shots in the index (a canvas program, or an index too old to carry
    them) there is nothing to distinguish a scene change from a jump cut, so
    this returns every junction — the honest answer is the previous behaviour,
    not silently zero transitions.
    """
    keep = [(float(k[0]), float(k[1])) for k in (edl.get("keep") or [])
            if k is not None and len(k) >= 2]
    inserts = list(edl.get("inserts") or [])
    scope = ((edl.get("effects") or {}).get("transition") or {}).get("scope") \
        or "scene"

    # Rebuild the block order build_filtergraph uses: inserts splice in at
    # their keep boundary, before the segment that starts there. Segment
    # lengths must be the speed-REMAPPED ones, exactly as the renderer uses,
    # or an edit with speed spans would place its inserts at the wrong blocks.
    try:
        seg_out_len = Timeline(keep, inserts, edl.get("speed")).seg_out_len
    except Exception:
        seg_out_len = [max(0.0, e - s) for s, e in keep]
    # ONE ordering, shared with ins_durs below and with Timeline.ins — the
    # block list indexes into both, so a different tie-break in either would
    # pair a junction with another insert's length.
    ins_order = [_ins_tuple(i) for i in sorted(inserts, key=_ins_sort_key)]
    at_list = [at for at, _d in ins_order]
    blocks, ins_j, pre = [], 0, 0.0
    for i, (s, e) in enumerate(keep):
        while ins_j < len(at_list) and at_list[ins_j] <= pre + 1e-6:
            blocks.append(("ins", ins_j))
            ins_j += 1
        blocks.append(("seg", i))
        pre += seg_out_len[i] if i < len(seg_out_len) else max(0.0, e - s)
    while ins_j < len(at_list):
        blocks.append(("ins", ins_j))
        ins_j += 1
    if n_blocks is not None and len(blocks) != n_blocks:
        # The renderer is authoritative about its own block count. If the two
        # ever disagree, fall back to every junction rather than dropping
        # transitions the user asked for at the wrong places.
        return set(range(max(0, n_blocks - 1)))

    n_junctions = max(0, len(blocks) - 1)

    # A SCREEN TAKEOVER'S HANDOFF IS NOT A CUT (round 55). The clip a takeover
    # hands off to is spliced in like any other, so every rule above calls its
    # junction a scene change — and a dip to black or a whip pan lands in the
    # exact middle of the one join the whole effect exists to make invisible.
    # It is also the junction a user is least likely to spot in a tool result
    # and most likely to describe as "the transition is broken". These are
    # excluded before scope is even consulted, so 'every_cut' cannot reinstate
    # them either: nothing about a takeover is a cut the user chose.
    ends = set()
    for ov in (edl.get("overlays") or []):
        if isinstance(ov, dict) and ov.get("screen"):
            ends.add(round(float(ov["start"]) + float(ov["duration_s"]), 2))
    protected = set()
    if ends:
        ins_durs = [d for _at, d in ins_order]
        acc, starts = 0.0, []
        for kind, i in blocks:
            starts.append(round(acc, 2))
            if kind == "seg":
                acc += (seg_out_len[i] if i < len(seg_out_len)
                        else max(0.0, keep[i][1] - keep[i][0]))
            else:
                acc += ins_durs[i] if i < len(ins_durs) else 0.0
        for k in range(n_junctions):
            # junction k sits at the START of block k+1
            if any(abs(starts[k + 1] - e) < 0.06 for e in ends):
                protected.add(k)

    if scope == "every_cut":
        return set(range(n_junctions)) - protected

    shots = (index or {}).get("shots") or []
    if not shots:
        return set(range(n_junctions)) - protected

    out = set()
    for k in range(n_junctions):
        if k in protected:
            continue
        a_kind, a_i = blocks[k]
        b_kind, b_i = blocks[k + 1]
        if a_kind == "ins" or b_kind == "ins":
            out.add(k)                      # spliced media: a real change
            continue
        a_end = keep[a_i][1] - _JUNCTION_EPS
        b_start = keep[b_i][0] + _JUNCTION_EPS
        sa, sb = _shot_at(shots, a_end), _shot_at(shots, b_start)
        # Unknown on either side (a keep span outside every shot) counts as a
        # change: a transition the user did not want is a smaller error than
        # silently dropping one at a real scene boundary.
        if sa is None or sb is None or sa != sb:
            out.add(k)
    return out


def merge_spans(spans, gap=0.3):
    """Merge output spans closer than `gap` — keeps ffmpeg enable expressions
    short when there are hundreds of speech spans."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]
