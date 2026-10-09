"""Motion captions: transcript captions drawn by the browser motion engine.

When ``captions.style.motion_look`` names a look, the renderer skips the
libass caption burn and instead asks this module for synthetic motion items
(template ``caption_motion``) built from the SAME kept-transcript words the
ASS path uses — fillers dropped, text corrections applied, caption mutes
honoured, insert breaks respected — grouped by the same prosody-aware phrase
optimizer. Because the cues are derived at render time from the timeline,
they follow every cut exactly like ordinary captions do.

The caption track is split at natural gaps into segments of ~6–10 s. Each
segment is an independent RenderJob carrying only its own cues, so segments
render concurrently and cache independently: correcting one word re-renders
one segment, not the whole track.
"""

import captions as caplib

# look -> phrase grouping + template defaults
LOOKS = {
    "pop": {"mode": "reveal", "max_words": 3, "target_words": 2, "chars": 20,
            "max_chunk_s": 1.6, "desc": "bold 1–3 word punches; spoken word springs in and takes the accent color (Hormozi/Submagic class, refined)"},
    "box": {"mode": "reveal", "max_words": 4, "target_words": 3, "chars": 24,
            "max_chunk_s": 2.0, "desc": "phrase on a soft dark pill; a colored highlight box glides to each spoken word"},
    "clean": {"mode": "reveal", "max_words": 6, "target_words": 4, "chars": 32,
              "max_chunk_s": 2.6, "desc": "quiet premium sentence-case captions; words fade/rise in as spoken (documentary / Mr Gum class)"},
    "serif": {"mode": "reveal", "max_words": 5, "target_words": 3, "chars": 26,
              "max_chunk_s": 2.2, "desc": "sans phrase with emphasis words switching to large accent serif italic (editorial luxury)"},
    "glow": {"mode": "karaoke", "max_words": 4, "target_words": 3, "chars": 24,
             "max_chunk_s": 1.9, "desc": "whole phrase visible dim; spoken words light up with an accent glow (karaoke fill)"},
    "stack": {"mode": "reveal", "max_words": 4, "target_words": 3, "chars": 22,
              "max_chunk_s": 2.0, "desc": "two-level stack: small connector words above a huge hero word that slams in"},
    "mono": {"mode": "reveal", "max_words": 5, "target_words": 4, "chars": 28,
             "max_chunk_s": 2.4, "desc": "monospace typewriter captions with a blinking caret (tech/terminal stories)"},
}
TEMPLATE = "caption_motion"
SEGMENT_TARGET_S = 8.0
SEGMENT_MAX_S = 12.0


def look_of(edl):
    caps = (edl or {}).get("captions")
    if not isinstance(caps, dict) or caps.get("mode") != "from_transcript":
        return None
    style = caps.get("style") or {}
    look = style.get("motion_look") if isinstance(style, dict) else None
    return look if look in LOOKS else None


def _out_words(edl, index, tl):
    """The kept, corrected, mute-filtered program words (caption truth)."""
    caps = edl.get("captions") or {}
    mutes = caplib.effective_caption_mutes(edl)
    src_words = [w for w in (index.get("words") or [])
                 if not (w.get("filler") if isinstance(w, dict) else getattr(w, "filler", False))]
    out = caplib._mark_insert_breaks(tl.kept_words(src_words), tl)
    if caps.get("corrections"):
        legacy = [{"from": a, "to": b, "preserve_affixes": True}
                  for a, b in caps.get("text_fixes") or []]
        out = caplib.apply_scoped_fixes(out, legacy + caps["corrections"])
    else:
        out = caplib.apply_text_fixes(out, caps.get("text_fixes"))
    return caplib._drop_muted_words(out, mutes)


def _anchor_for(style, placement_track, src_mid):
    if placement_track:
        for span in placement_track:
            if float(span.get("t0", 0)) <= src_mid <= float(span.get("t1", 0)):
                if span.get("anchor_y") is not None:
                    return float(span["anchor_y"])
                pos = span.get("position")
                return {"top": 0.2, "middle": 0.5, "bottom": 0.74}.get(pos, 0.74)
    if style.get("anchor_y") is not None:
        return float(style["anchor_y"])
    return {"top": 0.2, "middle": 0.5, "bottom": 0.74}.get(style.get("position") or "bottom", 0.74)


def cues(edl, index, tl):
    """[{s, e, y, w:[{t, s, e, x}]}] on the program clock, or [] when off."""
    look = look_of(edl)
    if not look:
        return []
    cfg = LOOKS[look]
    caps = edl["captions"]
    style = dict(caps.get("style") or {})
    words = _out_words(edl, index, tl)
    if not words:
        return []
    max_w = int(caps.get("max_words_per_caption") or cfg["max_words"])
    p = {"mode": cfg["mode"], "max_words": max_w,
         "target_words": min(max_w, cfg["target_words"]),
         "max_chunk_s": cfg["max_chunk_s"]}
    if caps.get("min_words_per_caption"):
        p["min_words"] = int(caps["min_words_per_caption"])
    chunks = caplib._premium_chunks_v2(words, max_w, cfg["chars"], p)
    emph = {caplib._norm_word(w) for w in (caps.get("emphasis_words") or []) if w}
    upper = bool(style.get("uppercase"))
    out = []
    prog_end = float(tl.out_duration)
    for i, ch in enumerate(chunks):
        if not ch:
            continue
        s = float(ch[0]["t0"])
        last = float(ch[-1]["t1"])
        nxt = float(chunks[i + 1][0]["t0"]) if i + 1 < len(chunks) and chunks[i + 1] else None
        # Hold through a short breath; release promptly at a real pause.
        e = min(nxt, last + 0.6) if nxt is not None and nxt - last < 0.6 else last + 0.35
        e = min(e, prog_end)
        if e - s < 0.12:
            continue
        src_mid = (float(ch[0].get("src_t0", s)) + float(ch[-1].get("src_t1", last))) / 2.0
        ws = []
        for w in ch:
            text = caplib._display_word_v2(w["w"], upper)
            key = caplib._norm_word(w["w"])
            ws.append({"t": text, "s": round(float(w["t0"]), 3), "e": round(float(w["t1"]), 3),
                       "x": 1 if (key in emph or caplib._word_has_digit(w["w"])) else 0})
        out.append({"s": round(s, 3), "e": round(e, 3),
                    "y": round(_anchor_for(style, caps.get("placement_track"), src_mid), 4),
                    "w": ws})
    return out


def _segments(all_cues):
    segs, cur = [], []
    for c in all_cues:
        if cur:
            seg_s = cur[0]["s"]
            gap = c["s"] - cur[-1]["e"]
            if (c["e"] - seg_s > SEGMENT_MAX_S) or (c["s"] - seg_s >= SEGMENT_TARGET_S and gap >= 0.0):
                segs.append(cur)
                cur = []
        cur.append(c)
    if cur:
        segs.append(cur)
    return segs


def style_params(edl):
    caps = edl.get("captions") or {}
    st = dict(caps.get("style") or {})
    return {
        "look": look_of(edl),
        "color": (st.get("color") or "#FFFFFF").upper(),
        "accent": (st.get("highlight_color") or "").upper() or None,
        "font": st.get("font"),
        "size": float(st.get("size_scale") or {"s": 0.82, "m": 1.0, "l": 1.2, "xl": 1.45}.get(st.get("size") or "m", 1.0)),
        "uppercase": bool(st.get("uppercase")),
        "align": st.get("text_align") or "center",
    }


def items(edl, index, tl):
    """Synthetic MotionItem dicts (one per segment) for the caption track."""
    allc = cues(edl, index, tl)
    if not allc:
        return []
    sp = style_params(edl)
    out = []
    for k, seg in enumerate(_segments(allc)):
        s0, s1 = seg[0]["s"], seg[-1]["e"]
        rebased = [{"s": round(c["s"] - s0, 3), "e": round(c["e"] - s0, 3), "y": c["y"],
                    "w": [dict(w, s=round(w["s"] - s0, 3), e=round(w["e"] - s0, 3)) for w in c["w"]]}
                   for c in seg]
        out.append({"id": f"__captions_{k}", "template": TEMPLATE, "start": s0, "end": s1,
                    "params": dict(sp, cues=rebased), "layer": "below_captions",
                    "_synthetic": True})
    return out
