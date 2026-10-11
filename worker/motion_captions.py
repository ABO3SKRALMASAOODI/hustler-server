"""Motion captions: transcript captions drawn by the browser motion engine.

When ``captions.style.motion_look`` names a look, the renderer skips the
libass caption burn and instead asks this module for synthetic motion items
(template ``caption_motion``) built from the SAME kept-transcript words the
ASS path uses — fillers dropped, text corrections applied, caption mutes
honoured, insert breaks respected — grouped by the same prosody-aware phrase
optimizer. Because the cues are derived at render time from the timeline,
they follow every cut exactly like ordinary captions do.

Each cue carries its vertical anchor ``y`` and band ``b`` (t/m/b) from the
shot-aware placement track (or the style's position/anchor_y). The template
keeps the whole block inside that band and the platform-safe area, so a
look never grows onto the face the placement compiler steered around.
While a motion graphic or a picture card is on screen, the words come from
the caption plan (worker/caption_carry.py — the same pass the libass
captions use): the words a graphic shows leave the captions, every other
heard word stays, and a cue whose usual place would touch a graphic's box,
a card or panel edge or seam, a stack's content panel, a face with its
chin or the watermark takes the plan's free band (worker/caption_place.py;
an explicit zone ``z`` the block may not grow out of). A line never holds
its place across a layout change (Plan.hold_limit), and a line waiting for
a graphic to clear waits at most two frames.
Where the spatial index measured the plate, a cue also carries its mean luma
``l`` (0-1) so premium looks firm up their shadow on bright plates. At
render time the motion layer also measures the picture under every cue
(worker/plate.py, ``MG.plate``: luma and busyness) and the template makes
legibility decisions, not boxes (round 7): a run of cues slides inside its
zone to a darker, calmer spot; past what the look's own shadow carries, dark
ink where the plate is bright under every word (accents deepened in their
hue, never paled), else a glyph scrim that follows the letterforms, and a
box only past that, sized to the final block. Every cue sharing a place
keeps its first line's baseline on one row (a second line grows down), the
same in every segment, so captions never hop between one- and two-line
cues; a cue with no solved zone keeps that row inside the band the plan
cleared around its anchor (caption_carry.CAP_HALF_H) on the face's side.

The caption track is split at natural gaps into segments of ~6–10 s. Each
segment is an independent RenderJob carrying only its own cues, so segments
render concurrently and cache independently: correcting one word re-renders
one segment, not the whole track.
"""

import bisect

import caption_carry
import caption_place
import captions as caplib
import motion_engine

# look -> phrase grouping + template defaults. ``desc`` is shown to editors.
LOOKS = {
    "clean": {"mode": "reveal", "max_words": 6, "target_words": 4, "chars": 32,
              "max_chunk_s": 2.6,
              "desc": "calm premium sentence case: near-white Inter, soft shadow, each word snaps "
                      "up out of a short blur (two frames) on its spoken onset inside a pre-laid-out "
                      "phrase, 1-2 emphasis words in a warm accent; phrases fade on pauses and clear "
                      "on cuts (talking heads, interviews, documentary, tutorials)"},
    "editorial": {"mode": "reveal", "max_words": 5, "target_words": 3, "chars": 26,
                  "max_chunk_s": 2.2,
                  "desc": "tight bold grotesk with the emphasis word switched to a large serif italic "
                          "tucked above/below the line (Mr Gum / '11' system); words hard-pop in "
                          "place on their spoken onset — the flagship premium podcast/reel look"},
    "lockup": {"mode": "reveal", "max_words": 4, "target_words": 3, "chars": 22,
               "max_chunk_s": 2.0,
               "desc": "two-tier lockup: small caps connector words over ONE huge hero word "
                       "(4-5x size ladder); connectors hard-pop, the hero ghost-snaps in "
                       "(motivational, punchy talking heads)"},
    "pop": {"mode": "reveal", "max_words": 3, "target_words": 2, "chars": 20,
            "max_chunk_s": 1.6,
            "desc": "bold outlined creator captions, 1-3 words; the spoken word punches in "
                    "and takes the accent colour (hype, gaming, sports, memes)"},
    "box": {"mode": "reveal", "max_words": 4, "target_words": 3, "chars": 24,
            "max_chunk_s": 2.0,
            "desc": "phrase on a soft dark pill; a coloured highlight box glides to each spoken word"},
    "serif": {"mode": "reveal", "max_words": 5, "target_words": 3, "chars": 26,
              "max_chunk_s": 2.2,
              "desc": "sans phrase whose emphasis words turn into a gold serif italic inline, words "
                      "snap up out of a short blur (calm, cinematic, luxury, storytelling)"},
    "glow": {"mode": "karaoke", "max_words": 4, "target_words": 3, "chars": 24,
             "max_chunk_s": 1.9,
             "desc": "whole phrase visible dim; each spoken word lights up with an accent glow "
                     "(karaoke fill; music, night, neon moods)"},
    "stack": {"mode": "reveal", "max_words": 3, "target_words": 2, "chars": 18,
              "max_chunk_s": 1.6,
              "desc": "small connectors over a giant condensed (Anton) hero word that slams in; "
                      "the loud version of lockup (hype, sport, trailers)"},
    "mono": {"mode": "reveal", "max_words": 5, "target_words": 4, "chars": 28,
             "max_chunk_s": 2.4,
             "desc": "monospace typewriter captions on a quiet plate with a blinking block caret "
                     "(tech, code, terminal stories)"},
}
TEMPLATE = "caption_motion"
SEGMENT_TARGET_S = 8.0
SEGMENT_MAX_S = 12.0
# Captions land one frame BEFORE the spoken onset (measured in the premium
# references: 0-40 ms early). Applied to the rendered items only; cues()
# stays on the true program clock. The lead never pulls an edge back across
# a program cut: a line that ends on a cut clears ON it, and a line whose
# first word starts just after a cut appears with the new shot, not on the
# last frame of the old one.
CAPTION_LEAD_S = 0.033
# A cue whose successor starts within this window swaps with a hard cut;
# otherwise it clears with a short fade into the pause.
CONTIGUOUS_S = 0.05
# A line that waits for a graphic on its band to clear must still hold this
# long after the wait; a shorter one starts on time instead (a dropped line
# would be a sound-off gap).
MIN_WAITED_CUE_S = 0.25

_BAND = {"top": "t", "middle": "m", "bottom": "b"}
_DEFAULT_Y = {"top": 0.2, "middle": 0.5, "bottom": 0.74}
_WEIGHT_WORDS = {"black": 900, "extrabold": 800, "bold": 700, "semibold": 600,
                 "medium": 500, "regular": 400, "light": 300}


def look_of(edl):
    caps = (edl or {}).get("captions")
    if not isinstance(caps, dict) or caps.get("mode") != "from_transcript":
        return None
    style = caps.get("style") or {}
    look = style.get("motion_look") if isinstance(style, dict) else None
    return look if look in LOOKS else None


def _out_words(edl, index, tl, carry=None):
    """The kept, corrected, rejoined, mute-filtered program words (caption
    truth: graphics have taken the words they show and moved the rest clear
    of themselves, worker/caption_carry.py), each stamped with the cut that
    ends its shot."""
    if carry is None:
        carry = caplib.caption_plan(edl, index, tl)
    return caplib._mark_shot_ends(carry.caption_words(), tl)


def _runs_by_room(words, chars, canvas, edl, index):
    """[(run of words, page character budget)]: the whole track at the
    look's budget, except where the plan set captions in a spot that holds
    ONE line (a place's ``l``: a card's foot under the speaker's chin,
    worker/caption_carry.py) — there pages are cut to what one line of
    that column holds (caption_place.line_chars), so they read at full size
    instead of shrinking two lines into it."""
    if not any((w.get("place") or {}).get("l") for w in words):
        return [(words, chars)]
    W, H = caption_carry.frame_wh(edl, index, canvas)
    tc = caption_place.template_column(W, H)
    out, fits = [], {}
    for w in words:
        pl = w.get("place") or {}
        budget = chars
        if pl.get("l"):
            col = tuple(pl.get("x") or tc)
            if col not in fits:            # (a measured face: once per column)
                fits[col] = caption_place.line_chars(edl, W, H, col)
            budget = min(chars, fits[col])
        if out and out[-1][1] == budget and \
                ((out[-1][0][-1].get("place") or {}).get("l") == pl.get("l")):
            out[-1][0].append(w)
        else:
            out.append(([w], budget))
    return out


def _card_column(edl, cue, W, H, col):
    """The inner column (x0, x1) of the picture card a cue in its usual
    place is set inside — the card live over the whole cue whose window
    holds its anchor (caption_place.home_rect) — or None. A line never runs
    past the card's sides (Diamandis run: 'in being actually remarkably'
    spanned x 0.09-0.905 over a card 0.14-0.86 wide). Hero ladders
    (caption_place.HERO_LOOKS) are poster lockups across the card: None."""
    if caption_place.look_of(edl) in caption_place.HERO_LOOKS:
        return None
    s, e = float(cue["s"]), float(cue["e"])
    cards = [c for c in caption_place.live_cards(edl, s + 1e-4, e - 1e-4)
             if float(c["start"]) <= s + 1e-3 and float(c["end"]) >= e - 1e-3]
    home = caption_place.home_rect(caption_place.card_rects(cards), float(cue["y"]), col) \
        if cards else None
    return list(caption_place.card_column(home, W, H)) if home else None


def _placement_for(style, placement_track, src_mid):
    """(anchor_y, band) for a cue: the measured placement span wins, then the
    style's anchor_y/position, then the look-neutral bottom default."""
    if placement_track:
        for span in placement_track:
            if float(span.get("t0", 0)) <= src_mid <= float(span.get("t1", 0)):
                pos = span.get("position") or "bottom"
                if span.get("anchor_y") is not None:
                    return float(span["anchor_y"]), _BAND.get(pos, "b")
                return _DEFAULT_Y.get(pos, 0.74), _BAND.get(pos, "b")
    if style.get("anchor_y") is not None:
        y = float(style["anchor_y"])
        pos = style.get("position") or ("top" if y < 0.36 else "middle" if y < 0.62 else "bottom")
        return y, _BAND.get(pos, "b")
    pos = style.get("position") or "bottom"
    return _DEFAULT_Y.get(pos, 0.74), _BAND.get(pos, "b")


def _anchor_for(style, placement_track, src_mid):
    return _placement_for(style, placement_track, src_mid)[0]


# A spatial sample this close (source seconds) speaks for the plate under a
# cue; further away it may already be another shot.
LUMA_NEAR_S = 2.0


def _luma_samples(index):
    out = []
    for s in ((index or {}).get("spatial") or {}).get("samples") or []:
        try:
            out.append((float(s["t"]), float(s["mean_luma"]) / 255.0))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def _luma_at(samples, src_t):
    """Mean plate luma (0-1) of the nearest spatial sample, or None."""
    best = None
    for t, l in samples:
        if abs(t - src_t) <= LUMA_NEAR_S and (best is None or abs(t - src_t) < best[0]):
            best = (abs(t - src_t), l)
    return None if best is None else round(best[1], 2)


def cues(edl, index, tl, canvas=None):
    """[{s, e, y, b, k, w:[{t, s, e, x}]}] on the program clock, or [] when off.

    ``b`` is the placement band (t/m/b) the block must stay inside; ``k`` = 1
    when the line clears hard instead of fading — the next cue follows
    without a pause, or the line ends on a program cut; ``l`` (optional) is
    the nearest spatial sample's mean plate luma; ``z`` (optional) an
    explicit [y0, y1] zone that keeps the block off an on-screen graphic and
    the face (see the module doc); ``h`` = 1 when that zone is the
    caption's own anchor inside a picture card (the template keeps it
    however tight) and ``n`` = 1 when it holds one line (a page of one
    line); ``x`` (optional) the [x0, x1] column its lines keep to
    — the inner column of the card it is set in (caption_place
    .card_column); ``f`` = 1 when the line starts ON a layout change its
    place flips at (rendered without the one-frame lead). ``canvas`` is the
    output (W, H), derived from the EDL when omitted.
    """
    look = look_of(edl)
    if not look:
        return []
    cfg = LOOKS[look]
    caps = edl["captions"]
    style = dict(caps.get("style") or {})
    mutes = [(float(a), float(b)) for a, b in caplib.effective_caption_mutes(edl)]
    carry = caplib.caption_plan(edl, index, tl, mutes, canvas=canvas)
    words = _out_words(edl, index, tl, carry)
    if not words:
        return []
    # The look owns its phrase grammar; a stored max_words only narrows it.
    try:
        stored = int(caps.get("max_words_per_caption") or 0)
    except (TypeError, ValueError):
        stored = 0
    max_w = max(1, min(stored, cfg["max_words"])) if stored > 0 else cfg["max_words"]
    p = {"mode": cfg["mode"], "max_words": max_w,
         "target_words": min(max_w, cfg["target_words"]),
         "max_chunk_s": cfg["max_chunk_s"]}
    if caps.get("min_words_per_caption"):
        p["min_words"] = min(int(caps["min_words_per_caption"]), max_w)
    W, H = caption_carry.frame_wh(edl, index, canvas)
    col = caption_place.column(W, H, caption_carry.COLUMN)
    chunks = [ch for run, chars in _runs_by_room(words, cfg["chars"], canvas, edl, index)
              for ch in caplib._premium_chunks_v2(run, max_w, chars, p) if ch]
    emph = {caplib._norm_word(w) for w in (caps.get("emphasis_words") or []) if w}
    upper = bool(style.get("uppercase"))
    lumas = _luma_samples(index)
    # Stretches a graphic holds on the caption band: a line in its usual
    # place never holds into one, and never starts in the last moments of
    # one (it waits for the graphic to clear instead of touching it). A line
    # never holds into a stretch a graphic owns its phrase either (one
    # reading path: "that we have" clears as the lockup's "a" lands).
    holds = mutes + [(float(a), float(b)) for a, b in carry.yield_spans]
    waits = [(float(a), float(b)) for a, b in carry.clamp_spans + carry.wait_spans]
    out = []
    prog_end = float(tl.out_duration)
    # layout changes where the captions' place changes: a line starting on
    # one appears WITH the new layout, never a frame early (items: no lead)
    flips, prev_st = [], None
    for a, _b, st in carry.segments:
        if st != prev_st:
            flips.append(a)
        prev_st = st
    for i, ch in enumerate(chunks):
        s = float(ch[0]["t0"])
        last = float(ch[-1]["t1"])
        nxt = float(chunks[i + 1][0]["t0"]) if i + 1 < len(chunks) else None
        # Hold through a short breath; release promptly at a real pause.
        e = min(nxt, last + 0.6) if nxt is not None and nxt - last < 0.6 else last + 0.35
        e = min(e, prog_end)
        # A muted window (a graphic that replaces the captions) starts with
        # the line already gone: the hold never reaches into it.
        for m0, _m1 in holds:
            if s < m0 < e:
                e = m0
        place = ch[0].get("place")
        # ...and never carries its place across a layout change onto the
        # new layout (the plan re-solves placement there)
        lim = carry.hold_limit(max(s, float(ch[-1]["t0"])), place)
        if s < lim < e:
            e = lim
        if not place:
            for m0, m1 in waits:
                # lands ON the graphic's exit — unless waiting would leave
                # the line too short to read (then it touches the exit
                # rather than vanish: every spoken word stays readable)
                if m0 <= s < m1 and m1 - s <= caption_carry.START_WAIT_S \
                        and e - (m1 + CAPTION_LEAD_S) >= MIN_WAITED_CUE_S:
                    s = m1 + CAPTION_LEAD_S
        if e - s < caption_carry.MIN_PAGE_S - 1e-6:
            continue
        # Every word spoken: the line clears ON the cut that ends its shot
        # (a hard clear — a hold or fade surviving a jump cut ghosts over
        # the new framing). Cards already break at cuts; one a stored
        # min_words_per_caption holds across a cut is still speaking, and
        # runs on.
        on_cut = False
        cut = ch[-1].get("cut")
        if cut is not None and s < cut < e:
            e, on_cut = cut, True
        src_mid = (float(ch[0].get("src_t0", s)) + float(ch[-1].get("src_t1", last))) / 2.0
        if place:
            # moved clear of a graphic: its band, and a zone the block may
            # not grow out of (the template's ``z``)
            y, band = float(place["y"]), place["b"]
        else:
            y, band = _placement_for(style, caps.get("placement_track"), src_mid)
        ws = []
        for w in ch:
            text = caplib._display_word_v2(w["w"], upper)
            hit = not caplib._word_keys(w).isdisjoint(emph)
            ws.append({"t": text, "s": round(float(w["t0"]), 3), "e": round(float(w["t1"]), 3),
                       "x": 1 if (hit or caplib._word_has_digit(w["w"])) else 0})
        cue = {"s": round(s, 3), "e": round(e, 3), "y": round(y, 4), "b": band,
               "k": 1 if on_cut or (nxt is not None and nxt - e < CONTIGUOUS_S) else 0,
               "w": ws}
        if place:
            cue["z"] = list(place["z"])
            if place.get("h"):
                cue["h"] = 1        # its own anchor in a card: the zone holds however tight
            if place.get("l"):
                cue["n"] = 1        # ...one line: a page of one line, its serif word inline
        x = (place or {}).get("x") or _card_column(edl, cue, W, H, col)
        if x:
            cue["x"] = list(x)      # the card's column its lines keep to
        if any(abs(s - f) < 1e-3 for f in flips):
            cue["f"] = 1
        luma = _luma_at(lumas, src_mid) if lumas else None
        if luma is not None:
            cue["l"] = luma      # bright plates get a firmer scrim + shadow
        out.append(cue)
    return out


def _segments(all_cues):
    """Split the track into ~8 s render segments, preferring pauses.

    A placement move (another band/anchor) also starts a segment once the
    current one has some length: each segment is captured inside the union
    of its blocks, so a top-and-bottom segment would capture most of the
    frame for every changing frame."""
    segs, cur = [], []
    for c in all_cues:
        if cur:
            seg_s = cur[0]["s"]
            pause = c["s"] - cur[-1]["e"] >= CONTIGUOUS_S
            moved = (c.get("b") != cur[-1].get("b") or
                     abs(float(c.get("y", 0)) - float(cur[-1].get("y", 0))) > 0.06)
            if (c["e"] - seg_s > SEGMENT_MAX_S) or (moved and c["s"] - seg_s >= 1.5) or \
                    (c["s"] - seg_s >= SEGMENT_TARGET_S and (pause or c["s"] - seg_s >= SEGMENT_TARGET_S + 1.5)):
                segs.append(cur)
                cur = []
        cur.append(c)
    if cur:
        segs.append(cur)
    return segs


def _css_font(name):
    """CaptionStyle.font ('Inter Display Black') -> (CSS family, weight)."""
    if not name:
        return None, None
    name = " ".join(str(name).split())
    fams = {}
    for fam, _fn, weight, style in motion_engine.FONT_FACES + motion_engine.OPTIONAL_FONT_FACES:
        if style == "normal":
            fams.setdefault(fam, set()).add(weight)
    if name in fams:
        return name, min(fams[name], key=lambda w: abs(w - 700))
    head, _, tail = name.rpartition(" ")
    weight = _WEIGHT_WORDS.get(tail.lower())
    if head in fams and weight:
        return head, weight
    return None, None


def style_params(edl):
    caps = edl.get("captions") or {}
    st = dict(caps.get("style") or {})
    family, weight = _css_font(st.get("font"))
    return {
        "look": look_of(edl),
        "color": (st.get("color") or "#FFFFFF").upper(),
        "accent": (st.get("highlight_color") or "").upper() or None,
        "font": family,
        "font_weight": weight,
        "size": float(st.get("size_scale") or {"s": 0.82, "m": 1.0, "l": 1.2, "xl": 1.45}.get(st.get("size") or "m", 1.0)),
        "uppercase": bool(st.get("uppercase")),
        "align": st.get("text_align") or "center",
        "anim": "none" if st.get("animation") == "none" else "auto",
    }


def items(edl, index, tl, canvas=None):
    """Synthetic MotionItem dicts (one per segment) for the caption track.

    Raises when this deployment cannot draw them: the renderer then burns the
    ordinary libass captions (the style's preset) instead of none at all."""
    if look_of(edl) and not motion_engine.available():
        raise motion_engine.MotionRenderError("motion captions need the browser engine")
    allc = cues(edl, index, tl, canvas=canvas)
    if not allc:
        return []
    sp = style_params(edl)
    lead = CAPTION_LEAD_S
    cuts = caplib.program_cuts(tl)
    flips = [c["s"] for c in allc if c.get("f")]

    def led(v):
        # one frame early, but never back across a cut (see CAPTION_LEAD_S)
        # nor off a layout change the line flips its place on (cues: f).
        # Cue times are rounded to the millisecond and cuts are not (a speed
        # ramp puts one at 1.53846 s): an edge within 1 ms of a cut is ON it.
        if any(abs(v - f) < 1e-3 for f in flips):
            return v
        k = bisect.bisect_right(cuts, v + 1e-3)
        if k and cuts[k - 1] > v - lead + 1e-6:
            return cuts[k - 1]
        return v - lead

    def rb(v, s0):
        return round(max(0.0, led(v) - s0), 3)

    out = []
    for k, seg in enumerate(_segments(allc)):
        s0 = max(0.0, led(seg[0]["s"]))
        s1 = max(s0 + 0.1, led(seg[-1]["e"]))
        rebased = [dict({"s": rb(c["s"], s0), "e": rb(c["e"], s0), "y": c["y"], "b": c.get("b", "b"),
                         "k": c.get("k", 0),
                         "w": [dict(w, s=rb(w["s"], s0), e=rb(w["e"], s0)) for w in c["w"]]},
                        **({"l": c["l"]} if "l" in c else {}),
                        **({"z": c["z"]} if "z" in c else {}),
                        **({"h": c["h"]} if "h" in c else {}),
                        **({"n": c["n"]} if "n" in c else {}),
                        **({"x": c["x"]} if "x" in c else {}))
                   for c in seg]
        out.append({"id": f"__captions_{k}", "template": TEMPLATE, "start": round(s0, 3),
                    "end": round(s1, 3), "params": dict(sp, cues=rebased),
                    "layer": "below_captions", "_synthetic": True})
    return out
