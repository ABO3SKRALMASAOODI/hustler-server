"""What CHANGED between two EDL versions, as OUTPUT-time ranges of the NEW
program — round 71.

The studio flashes the changed region of the scrubber/timeline for a moment
when a new preview lands, so the user recording a product-launch video can
SEE where the agent just worked without hunting for it. That needs machine-
readable ranges, and the prose diff line ("title text ... at 2.0-3.5s") was
never that: some tools speak output time, some source time, and only English
says which. This module diffs the two EDL dicts structurally and maps every
localizable change into the NEW program's output clock.

A REMOVAL has no extent in the new program — the place it used to be has
closed up — so removals become zero-length ranges (points) at the junction
where the removed material met the survivors. Changes with no meaningful
locality (a color grade, caption restyle, mastering, a frame change) set
"global" instead of pretending to a position.

Consumed by ToolContext.write_edl -> agent_loop._activity, which attaches it
as chat meta ("change") on the activity row of the write; the frontend reads
it straight out of /state. Never raises: a diff failure returns None and the
write proceeds — this is telemetry for a highlight, not part of the edit.
"""

from timeline import Timeline, insert_windows

_GAP = 0.25          # merge flash ranges closer than this (seconds)
_MAX_RANGES = 12     # beyond this the flash reads as noise: go global


def _spans(lst):
    return [(float(a), float(b)) for a, b in (lst or [])]


def _interval_sub(a, b):
    """Interval-list subtraction a - b (source-time [[s,e],...])."""
    out = []
    for s, e in a:
        cur = [(s, e)]
        for bs, be in b:
            nxt = []
            for cs, ce in cur:
                if be <= cs + 1e-6 or bs >= ce - 1e-6:
                    nxt.append((cs, ce))
                    continue
                if bs > cs:
                    nxt.append((cs, bs))
                if be < ce:
                    nxt.append((be, ce))
            cur = nxt
        out.extend(cur)
    return [(s, e) for s, e in out if e - s > 1e-3]


def _junction_out(tl, src_t):
    """Program time where footage cut around source time src_t closes up."""
    best = 0.0
    for (ks, ke), off, L in zip(tl.segs, tl.offsets, tl.seg_out_len):
        if ke <= src_t + 1e-6:
            best = off + L
        elif ks >= src_t - 1e-6:
            break
    return best


def _by_id(items, prefix):
    out = {}
    for i, it in enumerate(items or []):
        key = (it.get("id") if isinstance(it, dict) else None) \
            or f"{prefix}#{i}"
        out[key] = it
    return out


def _canon(it):
    """Comparable form of an item (dicts compare fine; None-safe)."""
    return it if isinstance(it, dict) else repr(it)


def change_ranges(prev, new):
    """{"out_ranges": [[a, b], ...], "cut_ranges": [[a, b], ...],
    "global": bool}, or None when nothing could be derived. Never raises.

    out_ranges are NEW-program output seconds (zero-length = removal
    point, where the cut closed up). cut_ranges are the REMOVED material
    in the PREVIOUS program's output seconds — the coordinates of the
    render the user is still watching while the turn runs, which is the
    only clock in which "what is being cut" is visible at all."""
    try:
        return _change_ranges(prev or {}, new or {})
    except Exception:
        return None


def _change_ranges(prev, new):
    new_keep = _spans(new.get("keep"))
    tl_new = Timeline(new_keep, new.get("inserts"), new.get("speed"))
    tl_prev = Timeline(_spans(prev.get("keep")), prev.get("inserts"),
                       prev.get("speed"))
    dur = tl_new.out_duration
    prev_dur = tl_prev.out_duration
    ranges, cuts, glob = [], [], False

    def add(a, b):
        a = min(max(float(a), 0.0), dur)
        b = min(max(float(b), a), dur)
        ranges.append((a, b))

    def add_cut(a, b):
        a = min(max(float(a), 0.0), prev_dur)
        b = min(max(float(b), a), prev_dur)
        if b > a + 1e-3:
            cuts.append((a, b))

    # ── footage: keep-list / speed changes ────────────────────────────────
    prev_keep = _spans(prev.get("keep"))
    if prev_keep != new_keep:
        for s, e in _interval_sub(new_keep, prev_keep):      # restored
            for a, b in tl_new.span_to_out(s, e):
                add(a, b)
        for s, e in _interval_sub(prev_keep, new_keep):      # cut away
            t = _junction_out(tl_new, s)
            add(t, t)
            # and where that footage sits in the render still on screen
            for a, b in tl_prev.span_to_out(s, e):
                add_cut(a, b)
    if (prev.get("speed") or []) != (new.get("speed") or []):
        seen = {repr(sp) for sp in (prev.get("speed") or [])}
        changed = [sp for sp in (new.get("speed") or [])
                   if repr(sp) not in seen]
        removed = [sp for sp in (prev.get("speed") or [])
                   if repr(sp) not in {repr(x)
                                       for x in (new.get("speed") or [])}]
        for sp in changed + removed:
            s = float(sp.get("start") if isinstance(sp, dict) else sp.start)
            e = float(sp.get("end") if isinstance(sp, dict) else sp.end)
            for a, b in tl_new.span_to_out(s, e):
                add(a, b)

    # ── inserts ───────────────────────────────────────────────────────────
    # Trims and splits are diffed as CONTENT, per asset, not per id: a split
    # re-mints the tail's id, so an id-level diff would call the surviving
    # tail "added" (white) and the whole old body "changed" (white again)
    # while the actually-REMOVED middle got no red at all — which is exactly
    # the trim-goes-unhighlighted the owner reported.
    ip, im = _by_id(prev.get("inserts"), "ins"), \
        _by_id(new.get("inserts"), "ins")
    if ip != im:
        wins_new = insert_windows(new.get("inserts"), tl_new)
        wins_prev = insert_windows(prev.get("inserts"), tl_prev)

        def _plays(items):
            by_asset = {}
            for it in (items or []):
                off = float(it.get("source_start_s") or 0.0)
                by_asset.setdefault(it.get("asset_key"), []).append(
                    (off, off + float(it.get("duration_s") or 0.0)))
            return by_asset
        prev_plays, new_plays = _plays(prev.get("inserts")), \
            _plays(new.get("inserts"))
        for k, it in im.items():
            old = ip.get(k)
            if old is not None and _canon(old) == _canon(it):
                continue
            moved = old is not None and (
                old.get("asset_key") != it.get("asset_key")
                or float(old.get("at_output_s", 0)) !=
                float(it.get("at_output_s", 0)))
            if old is None:
                # brand new id — but a split's tail carries content some
                # prev item already played; that is survival, not addition
                off = float(it.get("source_start_s") or 0.0)
                span = [(off, off + float(it.get("duration_s") or 0.0))]
                if not _interval_sub(span,
                                     prev_plays.get(it.get("asset_key"), [])):
                    continue
            elif not moved:
                # pure window trim: the red removed-content pass below is
                # the whole story — white over the survivors is noise
                continue
            w = wins_new.get(k)
            if w:
                add(w[0], w[1])
        for k in ip:
            if k not in im:
                w = wins_prev.get(k)
                t = w[0] if w else 0.0
                add(t, t)
        # removed CONTENT -> red, in the OLD program's coordinates
        for it in (prev.get("inserts") or []):
            w = wins_prev.get(it.get("id"))
            if not w:
                continue
            off = float(it.get("source_start_s") or 0.0)
            span = [(off, off + float(it.get("duration_s") or 0.0))]
            for r0, r1 in _interval_sub(
                    span, new_plays.get(it.get("asset_key"), [])):
                add_cut(w[0] + (r0 - off), w[0] + (r1 - off))

    # ── program-anchored spans: texts, vectors, overlays, music ───────────
    for field, prefix in (("texts", "tx"), ("overlays", "ov"),
                          ("vectors", "vec"), ("music", "mu")):
        p, n = _by_id(prev.get(field), prefix), _by_id(new.get(field), prefix)
        if p == n:
            continue
        for k, it in n.items():
            if k not in p or _canon(p[k]) != _canon(it):
                s = float(it.get("start", 0.0))
                e = float(it.get("end")) if it.get("end") is not None else \
                    s + float(it.get("duration_s") or 0.0)
                add(s, e)
        for k, it in p.items():
            if k not in n:
                s = float(it.get("start", 0.0))
                e = float(it.get("end")) if it.get("end") is not None else \
                    s + float(it.get("duration_s") or 0.0)
                add(s, s)
                add_cut(s, e)

    # ── sfx: points in the output ─────────────────────────────────────────
    p, n = _by_id(prev.get("sfx"), "sfx"), _by_id(new.get("sfx"), "sfx")
    if p != n:
        for k, it in n.items():
            if k not in p or _canon(p[k]) != _canon(it):
                add(float(it.get("at", 0.0)), float(it.get("at", 0.0)) + 0.6)
        for k, it in p.items():
            if k not in n:
                add(float(it.get("at", 0.0)), float(it.get("at", 0.0)))

    # ── zooms (output-anchored, inside effects) ───────────────────────────
    fx_p, fx_n = (prev.get("effects") or {}), (new.get("effects") or {})
    p, n = _by_id(fx_p.get("zooms"), "z"), _by_id(fx_n.get("zooms"), "z")
    if p != n:
        for k, it in n.items():
            if k not in p or _canon(p[k]) != _canon(it):
                add(float(it.get("start", 0.0)), float(it.get("end", 0.0)))
        for k, it in p.items():
            if k not in n:
                add(float(it.get("start", 0.0)), float(it.get("start", 0.0)))

    # ── custom filter chains (output-anchored, inside effects) ────────────
    p = _by_id(fx_p.get("custom"), "cf")
    n = _by_id(fx_n.get("custom"), "cf")
    if p != n:
        for k, it in list(n.items()) + \
                [(k, v) for k, v in p.items() if k not in n]:
            if _canon(p.get(k)) == _canon(n.get(k)):
                continue
            if it.get("start") is None:
                glob = True         # whole-video chain: no locality to flash
            elif k in n:
                add(float(it.get("start", 0.0)), float(it.get("end", 0.0)))
            else:                   # removed: flash where it used to start
                add(float(it.get("start", 0.0)), float(it.get("start", 0.0)))

    # ── volume: source-anchored spans ─────────────────────────────────────
    pv = {repr(v) for v in (prev.get("volume") or [])}
    nv = {repr(v) for v in (new.get("volume") or [])}
    if pv != nv:
        for v in list(new.get("volume") or []) + list(prev.get("volume") or []):
            if repr(v) in (pv ^ nv):
                for a, b in tl_new.span_to_out(float(v.get("start", 0.0)),
                                               float(v.get("end", 0.0))):
                    add(a, b)

    # ── everything else is global by nature ───────────────────────────────
    def _fx_rest(fx):
        return {k: v for k, v in fx.items() if k not in ("zooms", "custom")}
    for field in ("captions", "frame", "master", "canvas", "caption_mutes",
                  "source_clean", "voiceover"):
        if (prev.get(field) or None) != (new.get(field) or None):
            glob = True
    if _fx_rest(fx_p) != _fx_rest(fx_n):
        glob = True

    if not ranges and not cuts and not glob:
        return None

    def _merge(spans):
        spans = sorted(spans)
        out = []
        for a, b in spans:
            if out and a <= out[-1][1] + _GAP:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    merged = _merge(ranges)
    if len(merged) > _MAX_RANGES:
        # Too many white markers reads as noise — but if this write is
        # dominated by REMOVALS the red set carries the story, so just drop
        # the white set. Going global here painted the WHOLE bar in a white
        # shimmer over a cut_silences pass, burying the red cut markers —
        # the exact opposite of "show me what got cut" (owner report).
        if cuts:
            merged = []
        else:
            glob, merged = True, []
    # Cuts get a higher cap on purpose: cut_silences removes dozens of thin
    # slivers and every one of them is the answer to "what is being cut".
    merged_cuts = _merge(cuts)[:48]
    return {"out_ranges": [[round(a, 2), round(b, 2)] for a, b in merged],
            "cut_ranges": [[round(a, 2), round(b, 2)] for a, b in merged_cuts],
            "global": bool(glob)}


# ── round 81: the self-check looks WHERE THE EDIT HAPPENED ────────────────
# change_ranges answers "where do I flash the scrubber"; verify_plan answers
# a stronger question: "what should a frame taken at that moment SHOW, if my
# edit landed?" The render self-check has always reviewed a 3x3 sheet of the
# WHOLE programme sampled evenly — nine tiles for any length of video, blind
# to where the turn actually worked, asking a generic "anything broken?".
# So a mis-rendered title at 2.6s of a 20-minute edit had a 9-in-1200 chance
# of even appearing in a tile, and no tile came with the sentence that makes
# the check falsifiable ("this should read INTO A VIDEO EDITOR, behind the
# person"). Claims are that sentence, one per changed item, each with the
# output second a frame should be pulled at.

VERIFY_MAX_TILES = 6


def _clip_name(key):
    return str(key or "").rsplit("/", 1)[-1] or "clip"


def verify_plan(prev, new, max_tiles=VERIFY_MAX_TILES):
    """[(output_second, claim), ...] for the frame-checkable changes between
    two EDLs, time-sorted and capped. Never raises; [] when nothing that a
    single frame could confirm changed (cuts, music, speed and global passes
    have their own deterministic audits)."""
    try:
        return _verify_plan(prev or {}, new or {}, int(max_tiles))
    except Exception:
        return []


def _verify_plan(prev, new, max_tiles):
    tl_new = Timeline(_spans(new.get("keep")), new.get("inserts"),
                      new.get("speed"))
    dur = tl_new.out_duration
    if dur <= 0.2:
        return []
    claims = []

    def at(t):
        return min(max(float(t), 0.05), dur - 0.05)

    def mid(s, e):
        return at((float(s) + float(e or s)) / 2.0)

    # texts — the claim carries the exact words, and whether they belong
    # BEHIND the subject (the one placement a generic damage question can
    # never falsify: front-rendered words over the person look "fine").
    p, n = _by_id(prev.get("texts"), "tx"), _by_id(new.get("texts"), "tx")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        s = float(it.get("start", 0.0))
        e = it.get("end")
        where = ("with the person's body occluding the letters (the words "
                 "sit BEHIND them)" if it.get("behind")
                 else "over the picture")
        claims.append((mid(s, e), 'the text "'
                       + str(it.get("text") or "")[:60]
                       + f'" is on screen and reads EXACTLY that, {where}'))
    for k, it in p.items():
        if k not in n:
            claims.append((at(it.get("start", 0.0)),
                           'the removed text "'
                           + str(it.get("text") or "")[:40]
                           + '" must NOT appear anywhere in the frame'))

    # Vector graphics are visual claims too. Motion gets additional ordered
    # state sampling from screening.plan; this midpoint proves the primitive
    # exists, is the requested kind and points/frames where intended.
    p, n = _by_id(prev.get("vectors"), "vec"), \
        _by_id(new.get("vectors"), "vec")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        kind = it.get("kind") or "vector"
        purpose = ("points to the intended visible target" if kind in
                   ("arrow", "ring") else
                   "supports the intended composition without covering the "
                   "subject, source text or captions")
        claims.append((mid(it.get("start", 0.0), it.get("end")),
                       f"the {kind} graphic is visible and {purpose}"))
    for k, it in p.items():
        if k not in n:
            claims.append((at(it.get("start", 0.0)),
                           f"the removed {it.get('kind') or 'vector'} "
                           "graphic must NOT appear"))

    # inserts — a spliced clip either fills the frame at its window or the
    # splice failed; id-level is enough for a claim (a split's re-minted
    # tail still truthfully "plays this clip here").
    wins_new = insert_windows(new.get("inserts"), tl_new)
    ip, im = _by_id(prev.get("inserts"), "ins"), \
        _by_id(new.get("inserts"), "ins")
    for k, it in im.items():
        if k in ip and _canon(ip[k]) == _canon(it):
            continue
        w = wins_new.get(k) or wins_new.get(it.get("id"))
        if not w:
            continue
        claims.append((mid(w[0], w[1]),
                       f'the spliced {it.get("kind") or "clip"} '
                       f'"{_clip_name(it.get("asset_key"))}" is what fills '
                       "the frame here (not the main footage)"))
    for k, it in ip.items():
        if k not in im:
            claims.append((at(it.get("at_output_s") or 0.0),
                           f'the removed {it.get("kind") or "clip"} '
                           f'"{_clip_name(it.get("asset_key"))}" must NOT '
                           "appear"))

    # overlays — b-roll laid OVER the program.
    p, n = _by_id(prev.get("overlays"), "ov"), _by_id(new.get("overlays"),
                                                      "ov")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        s = float(it.get("start", 0.0))
        claims.append((mid(s, s + float(it.get("duration_s") or 0.0)),
                       f'the b-roll overlay '
                       f'"{_clip_name(it.get("asset_key"))}" covers the '
                       "program here"))
    for k, it in p.items():
        if k not in n:
            claims.append((at(it.get("start", 0.0)),
                           "the removed overlay must NOT appear"))

    # zooms — an aimed zoom is a COMPOSED close-up and fails by clipping its
    # subject or centring on nothing (round 72's launch-video miss).
    fx_p, fx_n = (prev.get("effects") or {}), (new.get("effects") or {})
    p, n = _by_id(fx_p.get("zooms"), "z"), _by_id(fx_n.get("zooms"), "z")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        aimed = (it.get("cx") is not None or it.get("cy") is not None
                 or it.get("path"))
        claims.append((mid(it.get("start", 0.0), it.get("end")),
                       "a deliberate close-up with its subject fully in "
                       "frame — FAILED if the subject is clipped at an edge "
                       "or the shot centres on empty space" if aimed else
                       "a visible push-in, tighter than the shots around it"))

    # windowed stylize — the effect must be visible where it was placed.
    p = _by_id([s for s in (fx_p.get("stylize") or [])
                if s.get("start") is not None], "st")
    n = _by_id([s for s in (fx_n.get("stylize") or [])
                if s.get("start") is not None], "st")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        claims.append((mid(it.get("start", 0.0), it.get("end")),
                       f'a deliberate {it.get("kind") or "stylize"} effect '
                       "is visibly applied to the picture"))

    # windowed custom filter chains (round 96) — the agent WROTE this chain,
    # so a parse that succeeded proves nothing about the look: the claim is
    # that the described effect is actually visible there.
    p = _by_id([c for c in (fx_p.get("custom") or [])
                if c.get("start") is not None], "cf")
    n = _by_id([c for c in (fx_n.get("custom") or [])
                if c.get("start") is not None], "cf")
    for k, it in n.items():
        if k in p and _canon(p[k]) == _canon(it):
            continue
        claims.append((mid(it.get("start", 0.0), it.get("end")),
                       f'the custom effect '
                       f'\'{(it.get("label") or "filter chain")}\' is '
                       "visibly applied to the picture and does not break it "
                       "(no black frame, no smeared geometry, no crushed "
                       "colors)"))

    claims.sort(key=lambda c: c[0])
    # Two claims about the same instant share one tile; then cap by thinning
    # evenly so a big batch still gets looked at across its whole span
    # rather than only at its start.
    merged = []
    for t, c in claims:
        if merged and abs(merged[-1][0] - t) < 0.2:
            merged[-1] = (merged[-1][0], merged[-1][1] + "; ALSO: " + c)
        else:
            merged.append((round(float(t), 2), c))
    if len(merged) > max_tiles:
        step = len(merged) / float(max_tiles)
        merged = [merged[int(i * step)] for i in range(max_tiles)]
    return merged


def color_only_change(prev, new):
    """True when the ONLY difference between two EDLs is the global color —
    effects.grade and/or effects.grade_custom — and at least one of the two
    actually changed. Round 91: this is what lets the agent iterate a grade
    against a ~2s contact strip instead of a full program render per try
    (a real turn burned 409s on five renders of the same 80s program to tune
    a look). Anything else differing — cuts, zooms, texts, captions, music,
    stylize, transitions, master, volume, speed, frame, inserts — returns
    False, because those need the real render to be seen. Never raises."""
    try:
        def strip(edl):
            e = dict(edl or {})
            fx = dict(e.get("effects") or {})
            color = {"grade": fx.pop("grade", None),
                     "grade_custom": fx.pop("grade_custom", None) or None}
            e["effects"] = fx
            return e, color

        rest_p, color_p = strip(prev)
        rest_n, color_n = strip(new)
        return rest_p == rest_n and color_p != color_n
    except Exception:
        return False
