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
While a motion graphic with a measured ``footprint`` (worker/keepout.py) is
on screen, a cue whose block would land on it steps into the nearest band
clear of the graphic AND the face zones measured over its window (an
explicit zone ``z``), so a graphic that was moved below the chin never
pushes the caption onto the mouth. Graphics without a footprint (written
before the keep-out) leave the cues exactly as before.
Where the spatial index measured the plate, a cue also carries its mean luma
``l`` (0-1) so premium looks firm up their scrim and shadow on bright plates.
At render time the motion layer also measures the picture under every cue
(worker/plate.py, ``MG.plate``): a light-ink look whose own block sits on a
plate too bright for 4.5:1 turns its scrim into a pocket dark enough for it.

The caption track is split at natural gaps into segments of ~6–10 s. Each
segment is an independent RenderJob carrying only its own cues, so segments
render concurrently and cache independently: correcting one word re-renders
one segment, not the whole track.
"""

import bisect

import captions as caplib
import keepout
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


def _out_words(edl, index, tl):
    """The kept, corrected, rejoined, mute-filtered program words (caption
    truth), each stamped with the cut that ends its shot."""
    words = caplib.transcript_words(edl, index, tl, caplib.effective_caption_mutes(edl))
    return caplib._mark_shot_ends(words, tl)


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


def _canvas_of(edl, index, canvas):
    if canvas:
        return float(canvas[0]), float(canvas[1])
    if edl.get("canvas"):
        return float(edl["canvas"]["width"]), float(edl["canvas"]["height"])
    import renderer   # lazy: the renderer imports this module
    video = (index or {}).get("video") or {}
    return renderer.frame_dims(int(video.get("width") or 1920), int(video.get("height") or 1080),
                               (edl.get("frame") or {}).get("ratio", "source"), delivery=True)


def _footprints(edl, index, canvas):
    """[(start, end, ink box, face zones)] of the motion graphics whose
    write-time footprint was measured on this canvas shape."""
    items = [m for m in edl.get("motion") or [] if isinstance(m.get("footprint"), dict)]
    if not items:
        return [], True
    W, H = _canvas_of(edl, index, canvas)
    ar = W / max(1.0, H)
    out = []
    for m in items:
        fp = m["footprint"]
        try:
            if abs(float(fp.get("ar") or 0) - ar) > 0.02 or len(fp.get("box") or []) != 4:
                continue
            out.append((float(m["start"]), float(m["end"]), tuple(float(v) for v in fp["box"]),
                        [tuple(float(v) for v in f) for f in fp.get("faces") or [] if len(f) == 4]))
        except (TypeError, ValueError, KeyError):
            continue
    return out, keepout.portrait(W, H)


def _band_of(y):
    return "t" if y < 0.36 else "m" if y < 0.62 else "b"


def cues(edl, index, tl, canvas=None):
    """[{s, e, y, b, k, w:[{t, s, e, x}]}] on the program clock, or [] when off.

    ``b`` is the placement band (t/m/b) the block must stay inside; ``k`` = 1
    when the line clears hard instead of fading — the next cue follows
    without a pause, or the line ends on a program cut; ``l`` (optional) is
    the nearest spatial sample's mean plate luma; ``z`` (optional) an
    explicit [y0, y1] zone that keeps the block off an on-screen graphic and
    the face (see the module doc). ``canvas`` is the output (W, H), derived
    from the EDL when omitted.
    """
    look = look_of(edl)
    if not look:
        return []
    cfg = LOOKS[look]
    caps = edl["captions"]
    style = dict(caps.get("style") or {})
    words = _out_words(edl, index, tl)
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
    chunks = [ch for ch in caplib._premium_chunks_v2(words, max_w, cfg["chars"], p) if ch]
    emph = {caplib._norm_word(w) for w in (caps.get("emphasis_words") or []) if w}
    upper = bool(style.get("uppercase"))
    lumas = _luma_samples(index)
    prints, port = _footprints(edl, index, canvas)
    mutes = [(float(a), float(b)) for a, b in caplib.effective_caption_mutes(edl)]
    out = []
    prog_end = float(tl.out_duration)
    for i, ch in enumerate(chunks):
        s = float(ch[0]["t0"])
        last = float(ch[-1]["t1"])
        nxt = float(chunks[i + 1][0]["t0"]) if i + 1 < len(chunks) else None
        # Hold through a short breath; release promptly at a real pause.
        e = min(nxt, last + 0.6) if nxt is not None and nxt - last < 0.6 else last + 0.35
        e = min(e, prog_end)
        # A muted window (a graphic that replaces the captions) starts with
        # the line already gone: the hold never reaches into it.
        for m0, _m1 in mutes:
            if s < m0 < e:
                e = m0
        if e - s < 0.12:
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
        y, band = _placement_for(style, caps.get("placement_track"), src_mid)
        ws = []
        for w in ch:
            text = caplib._display_word_v2(w["w"], upper)
            hit = not caplib._word_keys(w).isdisjoint(emph)
            ws.append({"t": text, "s": round(float(w["t0"]), 3), "e": round(float(w["t1"]), 3),
                       "x": 1 if (hit or caplib._word_has_digit(w["w"])) else 0})
        zone = None
        up = [p for p in prints if p[0] < e and p[1] > s]
        if up:
            zone = keepout.caption_zone(y, [p[2] for p in up], [f for p in up for f in p[3]], port)
            if zone:
                y, band = zone[2], _band_of(zone[2])
        cue = {"s": round(s, 3), "e": round(e, 3), "y": round(y, 4), "b": band,
               "k": 1 if on_cut or (nxt is not None and nxt - e < CONTIGUOUS_S) else 0,
               "w": ws}
        if zone:
            cue["z"] = [zone[0], zone[1]]
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

    def led(v):
        # one frame early, but never back across a cut (see CAPTION_LEAD_S).
        # Cue times are rounded to the millisecond and cuts are not (a speed
        # ramp puts one at 1.53846 s): an edge within 1 ms of a cut is ON it.
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
                        **({"z": c["z"]} if "z" in c else {}))
                   for c in seg]
        out.append({"id": f"__captions_{k}", "template": TEMPLATE, "start": round(s0, 3),
                    "end": round(s1, 3), "params": dict(sp, cues=rebased),
                    "layer": "below_captions", "_synthetic": True})
    return out
