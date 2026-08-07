"""Stitched previews (round 93): re-encode the CHANGED seconds, keep the rest.

"Why is it still rendering the preview?" — because until this module, every
preview re-encoded the whole program no matter how small the edit: a title
added at 0:05 re-rendered all ten minutes. The EDL write is instant; the
wait was the file. This makes the preview cost O(change):

  1. GATE (`plan`): stitching happens ONLY when the new EDL differs from the
     last-rendered one in VIDEO-LOCAL layers — texts, zooms, overlays,
     censor regions, patches — while the whole timeline STRUCTURE (keep,
     speed, inserts, frame, captions config, grade, stylize, transitions,
     fades, master, audio tracks, source derivations) is identical. Anything
     else — and any doubt at all — falls back to the full render, which is
     always correct. Audio must be identical because the stitched file
     carries the previous preview's audio stream unchanged.
  2. WINDOWS: every changed/added/removed item names its output span; spans
     are padded, grown to CONTAIN any overlapping item, caption/graphics
     event, insert, or transition junction zone (so nothing is ever cut
     mid-element), merged, and snapped OUTWARD to the previous preview's
     keyframes (stream copy can only cut there).
  3. PIECES: each window renders through the ORDINARY render_edl on a
     windowed EDL — same filters, same encoder settings — with caption and
     graphics ASS built from the FULL program and time-shifted, so burned
     text in a window is bit-comparable to the full render's. Unchanged
     stretches are stream-copied from the previous preview. Concat, then
     mux with the previous preview's audio.

Every hazard identified in the design is fenced by expansion or fallback:
fade zones, transition junctions, straddled caption events, inserts, pieces
that come out the wrong length. `plan` returning None is the normal case
for a structural edit and costs nothing.
"""

import json
import os
import re
import subprocess

import media

# Video-local layers a stitch may differ in. Everything else must be equal.
_CHANGEABLE_TOP = ("texts", "patches", "overlays")
_CHANGEABLE_FX = ("zooms", "regions", "custom")

_PAD_S = 0.5
_MAX_WINDOWS = 4
_MAX_COVER = 0.65          # beyond this share of the program, full render wins


def _canon(v):
    """Drop None/empty values recursively, so an EDL stored before a field
    existed compares equal to a fresh dump carrying the field's empty
    default — the same rule edl_signature uses for no-op detection. Callers
    are expected to have run BOTH dumps through the CURRENT validate_edl
    first, which materializes today's defaults on old EDLs."""
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items()
                if x not in (None, [], {}, "")}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    return v


def _strip(edl):
    """(structural-dump, changeable-dump) for comparison."""
    e = json.loads(json.dumps(edl or {}))
    fx = e.get("effects") or {}
    changeable = {k: e.pop(k, None) for k in _CHANGEABLE_TOP}
    for k in _CHANGEABLE_FX:
        changeable[k] = fx.pop(k, None) if isinstance(fx, dict) else None
    e["effects"] = fx
    # _canon's drop test reads values PRE-recursion, so effects that is a
    # dict of Nones survives as {} while an absent effects drops — and the
    # two sides then read as a structural change when the only real delta
    # was a changeable item. Normalize the shell away on both sides.
    if not _canon(fx):
        e.pop("effects", None)
    return _canon(e), changeable


def _item_windows(edl, tl, duration):
    """[(a, b, kind)] output spans of every changeable item in this EDL."""
    out = []
    for t in (edl.get("texts") or []):
        if t.get("behind"):
            continue
        out.append((float(t["start"]), float(t["end"]), "text"))
    fx = edl.get("effects") or {}
    for z in (fx.get("zooms") or []):
        out.append((float(z["start"]), float(z["end"]), "zoom"))
    for r in (fx.get("regions") or []):
        if r.get("start") is None:
            out.append((0.0, duration, "region"))
        else:
            out.append((float(r["start"]), float(r["end"]), "region"))
    for o in (edl.get("overlays") or []):
        s = float(o.get("start") or 0.0)
        out.append((s, s + float(o.get("duration_s") or 0.0), "overlay"))
    for p in (edl.get("patches") or []):
        pieces = tl.span_to_out(float(p["src_start"]), float(p["src_end"]))
        for a, b in pieces:
            out.append((a, b, "patch"))
    for c in (fx.get("custom") or []):
        if c.get("start") is not None:
            out.append((float(c["start"]), float(c["end"]), "custom"))
    return out


def _canon_items(edl):
    """{layer: {id: canonical-json}} for diffing the changeable layers."""
    out = {}
    fx = edl.get("effects") or {}
    for layer, items in (("texts", edl.get("texts")),
                         ("patches", edl.get("patches")),
                         ("overlays", edl.get("overlays")),
                         ("zooms", fx.get("zooms")),
                         ("regions", fx.get("regions")),
                         ("custom", fx.get("custom"))):
        d = {}
        for i, it in enumerate(items or []):
            d[it.get("id") or f"#{i}"] = json.dumps(it, sort_keys=True)
        out[layer] = d
    return out


def plan(prev_edl, new_edl, tl_prev, tl_new, duration, out_duration):
    """The changed output windows, or None with a reason when a full render
    is the right call. Returns (windows, reason)."""
    sp, cp = _strip(prev_edl)
    sn, cn = _strip(new_edl)
    if json.dumps(sp, sort_keys=True) != json.dumps(sn, sort_keys=True):
        return None, "structural change (timeline/captions/grade/audio/…)"
    if any(t.get("behind") for t in (new_edl.get("texts") or [])) or \
            any(t.get("behind") for t in (prev_edl.get("texts") or [])):
        return None, "behind-subject text present"
    if any(o.get("screen") for o in (new_edl.get("overlays") or [])) or \
            any(o.get("screen") for o in (prev_edl.get("overlays") or [])):
        return None, "screen takeover present"
    if abs(tl_prev.out_duration - tl_new.out_duration) > 0.002:
        return None, "program length moved"
    # unwindowed censor region touches every frame
    for r in ((new_edl.get("effects") or {}).get("regions") or []) + \
            ((prev_edl.get("effects") or {}).get("regions") or []):
        if r.get("start") is None:
            return None, "whole-video censor region"
    # so does an unwindowed custom filter chain
    for c in ((new_edl.get("effects") or {}).get("custom") or []) + \
            ((prev_edl.get("effects") or {}).get("custom") or []):
        if c.get("start") is None:
            return None, "whole-video custom filter"

    old_items, new_items = _canon_items(prev_edl), _canon_items(new_edl)
    changed = []
    for layer in old_items:
        ids = set(old_items[layer]) | set(new_items[layer])
        for iid in ids:
            if old_items[layer].get(iid) != new_items[layer].get(iid):
                changed.append((layer, iid))
    if not changed:
        return None, "no video-local change (audio-only or metadata)"

    spans_old = {(l, i): [] for l, i in changed}
    for l, i in changed:
        for edl, tl in ((prev_edl, tl_prev), (new_edl, tl_new)):
            fx = edl.get("effects") or {}
            src = {"texts": edl.get("texts"), "patches": edl.get("patches"),
                   "overlays": edl.get("overlays"), "zooms": fx.get("zooms"),
                   "regions": fx.get("regions"),
                   "custom": fx.get("custom")}[l] or []
            for idx, it in enumerate(src):
                if (it.get("id") or f"#{idx}") != i:
                    continue
                if l == "patches":
                    spans_old[(l, i)] += [
                        (a, b) for a, b in tl.span_to_out(
                            float(it["src_start"]), float(it["src_end"]))]
                elif l == "overlays":
                    s = float(it.get("start") or 0.0)
                    spans_old[(l, i)].append(
                        (s, s + float(it.get("duration_s") or 0.0)))
                else:
                    spans_old[(l, i)].append(
                        (float(it["start"]), float(it["end"])))
    windows = []
    for spans in spans_old.values():
        for a, b in spans:
            windows.append([max(0.0, a - _PAD_S),
                            min(out_duration, b + _PAD_S)])
    if not windows:
        return None, "changed items carry no output span"
    return windows, None


def expand(windows, all_item_spans, event_spans, junction_zones, fade_zones,
           out_duration):
    """Grow windows so nothing is cut mid-element; merge; None on a zone that
    cannot be satisfied (fades) or runaway growth."""
    ws = [list(w) for w in windows]
    for _ in range(24):                       # fixpoint with a hard ceiling
        grew = False
        merged = []
        for w in sorted(ws):
            if merged and w[0] <= merged[-1][1] + 0.2:
                merged[-1][1] = max(merged[-1][1], w[1])
            else:
                merged.append(list(w))
        ws = merged
        for w in ws:
            for a, b in all_item_spans + event_spans + junction_zones:
                if a < w[1] and b > w[0]:      # overlaps
                    na, nb = min(w[0], a - 0.05), max(w[1], b + 0.05)
                    if (na, nb) != (w[0], w[1]):
                        w[0], w[1] = max(0.0, na), min(out_duration, nb)
                        grew = True
        if not grew:
            break
    else:
        return None
    for w in ws:
        for a, b in fade_zones:
            if a < w[1] and b > w[0]:
                return None                    # window reaches into a fade
    total = sum(b - a for a, b in ws)
    if len(ws) > _MAX_WINDOWS or total > _MAX_COVER * out_duration:
        return None
    return [(round(a, 3), round(b, 3)) for a, b in ws]


# ------------------------------------------------------------------ pieces --

def window_edl(edl, tl, w0, w1):
    """The EDL that renders output [w0, w1] of `edl`, standalone.

    Only called under plan()'s gate, and only with windows expand() grew to
    CONTAIN every intersecting element — so items are carried whole (shifted
    by -w0) or dropped whole, never clipped."""
    e = json.loads(json.dumps(edl))
    keep, speed = [], []
    for (s0, s1), pcs, off, L in zip(tl.segs, tl.pieces, tl.offsets,
                                     tl.seg_out_len):
        seg_a, seg_b = off, off + L
        a, b = max(seg_a, w0), min(seg_b, w1)
        if b - a < 0.001:
            continue
        # walk this segment's constant-rate pieces to map output->source
        acc = seg_a
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            pa, pb = max(acc, a), min(acc + plen, b)
            if pb - pa > 0.001:
                src_a = ps + (pa - acc) * f
                src_b = ps + (pb - acc) * f
                keep.append([round(src_a, 3), round(src_b, 3)])
                if abs(f - 1.0) > 1e-9:
                    speed.append({"id": f"sw{len(speed) + 1}",
                                  "start": round(src_a, 3),
                                  "end": round(src_b, 3), "factor": f})
            acc += plen
    e["keep"] = keep
    e["speed"] = speed
    fx = dict(e.get("effects") or {})

    def _shift(items, get, put):
        out = []
        for it in items or []:
            a, b = get(it)
            if a >= w1 - 0.001 or b <= w0 + 0.001:
                continue
            out.append(put(it, a - w0, b - w0))
        return out

    e["texts"] = _shift(e.get("texts"), lambda t: (t["start"], t["end"]),
                        lambda t, a, b: {**t, "start": round(a, 3),
                                         "end": round(b, 3)})
    fx["zooms"] = _shift(fx.get("zooms"), lambda z: (z["start"], z["end"]),
                         lambda z, a, b: {**z, "start": round(a, 3),
                                          "end": round(b, 3)})
    fx["regions"] = _shift(fx.get("regions"),
                           lambda r: (r["start"], r["end"]),
                           lambda r, a, b: {**r, "start": round(a, 3),
                                            "end": round(b, 3)})
    # custom filter chains: windowed only under plan()'s gate (an unwindowed
    # one refused the stitch), so start/end are always present here
    fx["custom"] = _shift(fx.get("custom"),
                          lambda c: (c["start"], c["end"]),
                          lambda c, a, b: {**c, "start": round(a, 3),
                                           "end": round(b, 3)})
    e["overlays"] = _shift(
        e.get("overlays"),
        lambda o: (o.get("start") or 0.0,
                   (o.get("start") or 0.0) + (o.get("duration_s") or 0.0)),
        lambda o, a, b: {**o, "start": round(a, 3)})
    # patches ride the SOURCE clock: keep those whose source span survives
    e["patches"] = [p for p in (e.get("patches") or [])
                    if any(s0 < p["src_end"] and s1 > p["src_start"]
                           for s0, s1 in keep)]
    # Inserts are carried only when FULLY inside the window (their output
    # spans are containment zones in the planner, exactly like items — the
    # first prod attempt with inserts carried a program's every insert into
    # a 7.7s window and rendered 14.3s). Their at_src anchors sit on kept
    # source boundaries, so the windowed Timeline re-places them correctly.
    from timeline import insert_windows as _iw
    iw = _iw(e.get("inserts") or [], tl)
    e["inserts"] = [i for i in (e.get("inserts") or [])
                    if i.get("id") in iw
                    and iw[i["id"]][0] >= w0 - 0.011
                    and iw[i["id"]][1] <= w1 + 0.011]
    # captions/graphics burn from the SHIFTED full-program ASS (see
    # shift_ass) — the windowed EDL itself must not rebuild them.
    e["captions"] = None
    # fades belong to the program's ends; plan() refused windows near them
    if isinstance(fx, dict):
        fx["fade_in_s"] = 0.0
        fx["fade_out_s"] = 0.0
    # music/sfx/voiceover: the piece's audio is discarded (the stitched file
    # keeps the previous preview's audio track) — drop them so no music
    # fetch/loop work happens per piece.
    e["music"], e["sfx"], e["voiceover"] = [], [], []
    e["effects"] = fx
    return e


_TS = re.compile(r"(\d+):(\d\d):(\d\d)\.(\d\d)")


def _ass_t(s):
    h, m, sec, cs = s
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(cs) / 100.0


def _ass_fmt(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_events(path, animated_only=False, with_payload=False):
    """[(start, end)] of Dialogue events. With animated_only, just the events
    a window boundary must never land inside — karaoke/transform/move/fade
    straddlers cannot be clamped (see shift_ass), so the keyframe snap has
    to route boundaries around exactly these and no more. Static events are
    NOT included: on a densely-captioned project they cover nearly every
    keyframe, and clamping handles them.

    with_payload adds each event's line with the two timestamps blanked —
    style, position and text — so timeline mode can check that an event of
    the new program IS the same picture as one of the old program, not just
    that both programs have an event there."""
    if not path or not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8-sig"):
        if not line.startswith("Dialogue:"):
            continue
        if animated_only and not (_ANIM_TAG.search(line)
                                  or _LEAD_FAD.search(line)):
            continue
        m = _TS.findall(line)
        if len(m) >= 2:
            if with_payload:
                out.append((_ass_t(m[0]), _ass_t(m[1]),
                            _TS.sub("<t>", line.strip(), count=2)))
            else:
                out.append((_ass_t(m[0]), _ass_t(m[1])))
    return out


_ANIM_TAG = re.compile(r"\\(k[fo]?|K|t\(|move\()")
_LEAD_FAD = re.compile(r"\\fad\(\s*(\d+)\s*,\s*(\d+)\s*\)")


def shift_ass(src_path, dst_path, w0, w1):
    """Copy an ASS keeping events that touch [w0, w1], shifted by -w0.

    An event STRADDLING a boundary is one already mid-display where a copied
    piece meets a re-encoded one, and it must keep rendering identically:

      * static text clamps to the boundary — the glyphs of an event in
        progress don't depend on when it started;
      * a leading \\fad whose fade-in already finished before the boundary
        is STRIPPED from the clamped copy (an in-progress fade shows full
        opacity — re-playing it would blink at the seam); a fade still in
        flight refuses;
      * karaoke (\\k…), transforms (\\t) and \\move time themselves from the
        event's start, so a clamped copy would animate wrongly — refuse,
        and the caller runs the full render.

    Returns False on any refusal."""
    if not src_path or not os.path.exists(src_path):
        return True                            # nothing to burn is fine
    out = []
    for line in open(src_path, encoding="utf-8-sig"):
        if not line.startswith("Dialogue:"):
            out.append(line)
            continue
        m = _TS.findall(line)
        if len(m) < 2:
            continue
        a, b = _ass_t(m[0]), _ass_t(m[1])
        if a >= w1 - 0.001 or b <= w0 + 0.001:
            continue
        na, nb = a - w0, b - w0
        if na < -0.011 or nb > (w1 - w0) + 0.011:      # straddles a boundary
            if _ANIM_TAG.search(line):
                return False
            fad = _LEAD_FAD.search(line)
            if fad:
                if na < 0 and abs(na) * 1000.0 < float(fad.group(1)) + 60:
                    return False               # fade-in still in flight
                line = _LEAD_FAD.sub("", line, count=1)
            na = max(0.0, na)
            nb = min(w1 - w0, nb)
        line = _TS.sub(lambda mm, _c=iter((_ass_fmt(na), _ass_fmt(nb))):
                       next(_c), line, count=2)
        out.append(line)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return True


# ------------------------------------------------- timeline mode (round 97) --
#
# Round 93 stitched only when the timeline STRUCTURE was identical, so the
# edit users make most — a trim — always paid the full render (134-206s on
# real projects, 13 times in one session). Timeline mode lifts that: a keep /
# speed / audio-layer change stitches too, by matching every span of the NEW
# program to the span of the PREVIOUS preview that showed the same content
# and stream-copying it FROM ITS OLD POSITION (a per-run constant offset d),
# re-encoding only what genuinely changed.
#
# The audio track cannot be spliced the same way (loudnorm is adaptive, AAC
# has no clean cut points, music is output-anchored), so timeline mode
# REBUILDS the whole audio track through the ordinary render graph with the
# video chains pruned away (renderer.render_edl(audio_only=True)) — audio
# filters on a few minutes of sound cost ~1-2s, which is why this is cheap
# where it looks expensive.
#
# What may differ and still stitch:
#   keep, speed        -> the matching itself handles them
#   music, sfx, volume, voiceover, master -> audio is rebuilt from scratch
#   texts, patches, overlays, zooms, regions, custom -> windows, as before
# Everything else (captions config, caption_mutes, grade, stylize set,
# transitions config, frame, inserts, screen_frame, frame_shifts, canvas,
# source_clean, fades) must be byte-equal or the plan refuses.
#
# Copies are only valid where the composite — footage AND every burned
# element — is provably identical modulo the shift:
#   * footage: atoms match on (source span, rate) or (insert id, length);
#   * output-anchored elements (texts/zooms/regions/custom/overlays and
#     windowed stylize): identical JSON means identical pixels only where
#     d == 0; on a shifted run their spans are carved out for re-encoding;
#   * transition junctions: a junction in the new program must exist in the
#     previous one at +d with the same global config, else its zone is
#     carved;
#   * captions: never assumed — the caller passes both programs' ASS events
#     and every event that does not pair up exactly (text and timing, modulo
#     d) has its span carved. A trim shifts from_transcript captions with
#     their words, so on real projects the carve list is empty or tiny; when
#     line regrouping DOES change downstream events, those spans re-encode
#     and stay pixel-correct.
#   * whole-video effects (grade, LUTs, unwindowed stylize, screen_frame
#     plates, watermark): per-frame deterministic functions of the frame —
#     the copied pixels already went through them, so any d is fine. The one
#     stochastic case (grain) is time-seeded noise: statistically identical,
#     visually indistinguishable, accepted.
#
# Sub-frame phase: d is not forced to the frame grid. A copied frame shows
# the source instant the PREVIOUS render sampled for that content, which can
# sit up to half a frame period from the instant a fresh render would pick —
# ≤ 16ms at 30fps, invisible, and bounded (it does not accumulate: every
# piece re-encodes from source on the new grid).

# Fields the audio rebuild owns outright — a difference here never blocks a
# timeline stitch and never opens a video window.
AUDIO_FIELDS = ("music", "sfx", "volume", "voiceover", "master", "stem_mix")

_MIN_COPY_S = 0.75          # a copy shorter than this joins its neighbours
_SEAM_EPS = 1e-6


def _strip_timeline(edl):
    """Structural dump with the timeline (keep/speed) and the audio layers
    removed as well — what MUST still be equal for timeline mode."""
    s, _changeable = _strip(edl)
    for k in ("keep", "speed") + AUDIO_FIELDS:
        s.pop(k, None)
    return s


def timeline_atoms(edl, tl):
    """The program flattened into content atoms on the output clock:
    [(out_a, out_b, key)] where key identifies WHAT plays there —
    ("src", src_a, src_b, rate) for main footage, ("ins", id) for a spliced
    insert. Sorted by output position."""
    atoms = []
    for (_s, _e), pcs, off in zip(tl.segs, tl.pieces, tl.offsets):
        acc = off
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            if plen > 1e-4:
                atoms.append((acc, acc + plen, ("src", ps, pe, f)))
            acc += plen
    from timeline import insert_windows as _iw
    for iid, (a, b) in _iw(edl.get("inserts") or [], tl).items():
        atoms.append((a, b, ("ins", iid)))
    return sorted(atoms)


def match_runs(prev_atoms, new_atoms):
    """[(new_a, new_b, d)] — maximal spans of the NEW output clock whose
    content the previous preview already shows at +d. Candidates can overlap
    when the same source appears twice; a greedy longest-first pass keeps a
    non-overlapping cover."""
    cands = []
    for na, nb, key in new_atoms:
        if key[0] == "src":
            _t, ps, pe, f = key
            for pa, _pb, pkey in prev_atoms:
                if pkey[0] != "src":
                    continue
                _t2, qs, qe, g = pkey
                if abs(g - f) > 1e-9:
                    continue
                lo, hi = max(ps, qs), min(pe, qe)
                if hi - lo <= 1e-3:
                    continue
                n0 = na + (lo - ps) / f
                n1 = na + (hi - ps) / f
                p0 = pa + (lo - qs) / g
                cands.append((n0, n1, p0 - n0))
        else:
            for pa, pb, pkey in prev_atoms:
                if pkey == key and abs((pb - pa) - (nb - na)) < 0.005:
                    cands.append((na, nb, pa - na))
                    break
    cands.sort(key=lambda c: c[1] - c[0], reverse=True)
    chosen = []
    for a, b, d in cands:
        pieces = [(a, b)]
        for ca, cb, _cd in chosen:
            nxt = []
            for x, y in pieces:
                if ca > x + 1e-4:
                    nxt.append((x, min(y, ca)))
                if cb < y - 1e-4:
                    nxt.append((max(x, cb), y))
            pieces = nxt
            if not pieces:
                break
        for x, y in pieces:
            if y - x > 1e-3:
                chosen.append((x, y, d))
    chosen.sort()
    merged = []
    for a, b, d in chosen:
        if merged and abs(merged[-1][2] - d) < _SEAM_EPS \
                and a <= merged[-1][1] + 1e-4:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b, d])
    return [(a, b, d) for a, b, d in merged if b - a > 1e-3]


def carve_runs(runs, spans):
    """Remove [a, b) spans (new clock) from the copyable runs."""
    out = list(runs)
    for ca, cb in spans:
        nxt = []
        for a, b, d in out:
            if cb <= a + 1e-6 or ca >= b - 1e-6:
                nxt.append((a, b, d))
                continue
            if ca > a + 1e-4:
                nxt.append((a, min(b, ca), d))
            if cb < b - 1e-4:
                nxt.append((max(a, cb), b, d))
        out = nxt
    return [(a, b, d) for a, b, d in out if b - a > 1e-3]


def _out_spans_of_item(layer, it, tl, out_duration):
    if layer == "patches":
        return list(tl.span_to_out(float(it["src_start"]),
                                   float(it["src_end"])))
    if layer == "overlays":
        s = float(it.get("start") or 0.0)
        return [(s, s + float(it.get("duration_s") or 0.0))]
    if it.get("start") is None:
        return [(0.0, out_duration)]
    return [(float(it["start"]), float(it["end"]))]


def plan_timeline(prev_edl, new_edl, tl_prev, tl_new, out_duration,
                  cap_events_prev=None, cap_events_new=None):
    """Timeline-mode plan: (windows, runs, reason).

    windows: output spans that MUST re-encode (pre-expansion, unpadded
    beyond _PAD_S); runs: [(a, b, d)] spans provably copyable from the
    previous preview at +d; reason: set only on refusal (windows is None).

    cap_events_*: [(start, end, payload)] caption events of each program's
    full ASS (renderer builds both — captions burn on the output clock, so
    equality has to be CHECKED, never assumed)."""
    if json.dumps(_strip_timeline(prev_edl), sort_keys=True) != \
            json.dumps(_strip_timeline(new_edl), sort_keys=True):
        return None, None, "non-timeline structural change"
    if any(t.get("behind") for t in (new_edl.get("texts") or [])) or \
            any(t.get("behind") for t in (prev_edl.get("texts") or [])):
        return None, None, "behind-subject text present"
    if any(o.get("screen") for o in (new_edl.get("overlays") or [])) or \
            any(o.get("screen") for o in (prev_edl.get("overlays") or [])):
        return None, None, "screen takeover present"
    fx = new_edl.get("effects") or {}
    if fx.get("frame_shifts"):
        return None, None, "aspect shifts present"
    for r in (fx.get("regions") or []) + \
            ((prev_edl.get("effects") or {}).get("regions") or []):
        if r.get("start") is None:
            return None, None, "whole-video censor region"
    for c in (fx.get("custom") or []) + \
            ((prev_edl.get("effects") or {}).get("custom") or []):
        if c.get("start") is None:
            return None, None, "whole-video custom filter"

    runs = match_runs(timeline_atoms(prev_edl, tl_prev),
                      timeline_atoms(new_edl, tl_new))
    if not runs:
        return None, None, "nothing matches the previous preview"
    shifted = [(a, b, d) for a, b, d in runs if abs(d) > _SEAM_EPS]

    # Fades are output-time effects the pieces cannot reproduce (window_edl
    # zeroes them): each fade zone must be copied from where the PREVIOUS
    # preview burned ITS fade. The fade-in lives at 0 in both programs, so
    # its zone needs an unshifted run; the fade-out lives at each program's
    # own END, so its zone needs a run whose shift is exactly the length
    # difference — a head trim keeps its fade-out copyable, a tail trim
    # (whose fade genuinely moved onto different footage) refuses.
    fi = float(fx.get("fade_in_s") or 0.0)
    fo = float(fx.get("fade_out_s") or 0.0)
    d_end = tl_prev.out_duration - out_duration
    for zone, on, d_need in (
            ((0.0, fi + 0.3), fi > 0, 0.0),
            ((out_duration - fo - 0.3, out_duration), fo > 0, d_end)):
        if on and not any(abs(d - d_need) <= 1e-3 and a <= zone[0] + 1e-3
                          and b >= zone[1] - 1e-3 for a, b, d in runs):
            return None, None, "a program fade moved with the timeline"

    carve = []

    # Changed / added / removed video-local items: their spans on BOTH
    # clocks re-encode (the prev span mapped into new time through its run).
    old_items, new_items = _canon_items(prev_edl), _canon_items(new_edl)
    for layer in old_items:
        for iid in set(old_items[layer]) | set(new_items[layer]):
            o, n = old_items[layer].get(iid), new_items[layer].get(iid)
            if o == n:
                continue
            for edl, tl, blob in ((prev_edl, tl_prev, o),
                                  (new_edl, tl_new, n)):
                if blob is None:
                    continue
                it = json.loads(blob)
                for a, b in _out_spans_of_item(layer, it, tl, out_duration):
                    if tl is tl_prev:
                        for ra, rb, rd in runs:
                            lo = max(ra, a - rd)
                            hi = min(rb, b - rd)
                            if hi - lo > 1e-3:
                                carve.append((lo, hi))
                    else:
                        carve.append((a, b))

    # UNCHANGED output-anchored elements are only identical where d == 0 —
    # on a shifted run the same pixels sit over different footage. Carve
    # their spans out of every shifted run. (Patches ride the source clock
    # and shift WITH the footage; they are exempt.)
    anchored = []
    for t in (new_edl.get("texts") or []):
        anchored.append((float(t["start"]), float(t["end"])))
    for z in (fx.get("zooms") or []):
        anchored.append((float(z["start"]), float(z["end"])))
    for r in (fx.get("regions") or []):
        anchored.append((float(r["start"]), float(r["end"])))
    for c in (fx.get("custom") or []):
        anchored.append((float(c["start"]), float(c["end"])))
    for o in (new_edl.get("overlays") or []):
        s = float(o.get("start") or 0.0)
        anchored.append((s, s + float(o.get("duration_s") or 0.0)))
    for st in (fx.get("stylize") or []):
        if st.get("start") is not None:
            anchored.append((float(st["start"]), float(st["end"])))
    for a, b in anchored:
        for ra, rb, rd in shifted:
            lo, hi = max(a, ra), min(b, rb)
            if hi - lo > 1e-3:
                carve.append((a, b))
                break

    # Transition junctions, SYMMETRICALLY. With a transition configured, the
    # frames around every scene boundary carry its pixels (a dip, a whip),
    # so a copied span is only right where the two programs agree a junction
    # is there:
    #  * a NEW boundary with no previous twin at +d re-encodes (the fresh
    #    cut's transition has never been rendered);
    #  * a PREVIOUS boundary with no new twin at its mapped position
    #    re-encodes too — the copied pixels would smear the OLD transition
    #    over footage that is now continuous (a restored range merges two
    #    scenes and the dip must vanish).
    # Cut boundaries are used as junction candidates on both sides — a
    # superset of where transitions actually draw (scope='scene'), which
    # only ever carves MORE than strictly needed, never less.
    tr = fx.get("transition") or None
    if tr:
        zone = float(tr.get("duration_s") or 0.5) + 0.2
        prev_juncs = [tl_prev.offsets[i] for i in range(1, len(tl_prev.segs))]
        prev_juncs += [a for a, _d in tl_prev.insert_positions()]
        new_juncs = [tl_new.offsets[i] for i in range(1, len(tl_new.segs))]
        new_juncs += [a for a, _d in tl_new.insert_positions()]
        for j in new_juncs:
            covering = [(a, b, d) for a, b, d in runs
                        if a - 1e-3 <= j <= b + 1e-3]
            ok = any(any(abs((j + d) - pj) < 0.02 for pj in prev_juncs)
                     for _a, _b, d in covering)
            if covering and not ok:
                carve.append((j - zone, j + zone))
        for pj in prev_juncs:
            for a, b, d in runs:
                if a + d - zone < pj < b + d + zone:
                    if not any(abs((pj - d) - nj) < 0.02
                               for nj in new_juncs):
                        carve.append((pj - d - zone, pj - d + zone))

    # Caption events: pair every event across the two programs, modulo each
    # run's own d. Any event that does not pair exactly re-encodes.
    if cap_events_prev or cap_events_new:
        carve += caption_mismatch_spans(
            runs, cap_events_prev or [], cap_events_new or [], out_duration)

    runs = carve_runs(runs, carve)
    covered = _merge_spans([(a, b) for a, b, _d in runs])
    windows = _complement(covered, out_duration)
    if not windows and not carve:
        # Identical video content end to end (an audio-only change): one
        # whole-program copy and a fresh audio track.
        return [], runs, None
    return [[max(0.0, a - _PAD_S), min(out_duration, b + _PAD_S)]
            for a, b in windows], runs, None


def caption_mismatch_spans(runs, prev_events, new_events, out_duration):
    """Spans of the NEW clock where the two programs' burned captions are
    NOT the same picture modulo the run's shift."""
    bad = []
    tol = 0.05
    for a, b, d in runs:
        prev_here = [(s, e, p) for s, e, p in prev_events
                     if s < b + d and e > a + d]
        new_here = [(s, e, p) for s, e, p in new_events if s < b and e > a]
        unpaired = []
        pool = list(prev_here)
        for s, e, p in new_here:
            hit = None
            for i, (ps, pe, pp) in enumerate(pool):
                if pp == p and abs(ps - (s + d)) <= tol \
                        and abs(pe - (e + d)) <= tol:
                    hit = i
                    break
            if hit is None:
                unpaired.append((s, e))
            else:
                pool.pop(hit)
        unpaired += [(ps - d, pe - d) for ps, pe, _pp in pool]
        for s, e in unpaired:
            bad.append((max(a, s) - 0.05, min(b, e) + 0.05))
    return bad


def _merge_spans(spans, gap=1e-4):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _complement(spans, dur):
    out, cursor = [], 0.0
    for a, b in spans:
        if a - cursor > 1e-3:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if dur - cursor > 1e-3:
        out.append((cursor, dur))
    return out


def snap_parts(windows, runs, kfs, out_duration, forbidden_new,
               forbidden_prev_animated):
    """The final alternating plan: [("copy", a, b, d) | ("win", a, b)].

    Copies can only start and stop where the PREVIOUS file has a keyframe
    (a + d must be a keyframe time), so copies SHRINK inward to the nearest
    usable keyframe and the re-encode windows absorb the slack — the inverse
    of round 93's outward window snap, with per-boundary offsets. A boundary
    also refuses to land inside any forbidden zone on either clock. Copies
    shorter than _MIN_COPY_S dissolve into the windows around them.

    Returns None when the result stops being worth it (window cap /
    coverage cap), and the full render runs."""
    if not kfs:
        return None
    kfs = sorted(kfs)

    def _ok_boundary(t, d):
        for a, b in forbidden_new:
            if a + 0.005 < t < b - 0.005:
                return False
        for a, b in forbidden_prev_animated:
            if a + 0.005 < t + d < b - 0.005:
                return False
        return True

    win = [(a, b) for a, b in windows]
    copies = []
    for a, b, d in runs:
        ka = a if abs(a) < 1e-6 and abs(d) < _SEAM_EPS else None
        if ka is None:
            cands = [k - d for k in kfs if k - d >= a - 0.002]
            cands = [t for t in cands if _ok_boundary(t, d)]
            ka = min(cands) if cands else None
        kb = b if abs(b - out_duration) < 1e-6 else None
        if kb is None and ka is not None:
            cands = [k - d for k in kfs if k - d <= b + 0.002]
            cands = [t for t in cands if t > ka + 0.01
                     and _ok_boundary(t, d)]
            kb = max(cands) if cands else None
        if ka is None or kb is None or kb - ka < _MIN_COPY_S:
            win.append((a, b))
            continue
        if ka - a > 1e-3:
            win.append((a, ka))
        if b - kb > 1e-3:
            win.append((kb, b))
        copies.append((max(a, ka), min(b, kb), d))

    win = _merge_spans(win, gap=0.05)
    total_win = sum(b - a for a, b in win)
    if len(win) > _MAX_WINDOWS or total_win > _MAX_COVER * out_duration:
        return None

    parts, cursor = [], 0.0
    events = sorted([(a, b, "win", None) for a, b in win]
                    + [(a, b, "copy", d) for a, b, d in copies])
    for a, b, kind, d in events:
        if a > cursor + 1e-3:
            return None                       # uncovered gap — bug guard
        if b <= cursor + 1e-3:
            continue
        a = max(a, cursor)
        if kind == "win":
            if parts and parts[-1][0] == "win":
                parts[-1] = ("win", parts[-1][1], b)
            else:
                parts.append(("win", a, b))
        else:
            parts.append(("copy", a, b, d))
        cursor = b
    if out_duration - cursor > 1e-3:
        return None
    return parts


def assemble_offset(prev_local, parts, piece_paths, audio_path,
                    expected_dur, workdir, out_path):
    """Like assemble(), but every copy names its own position in the
    previous file (a + d) and the audio comes from a freshly-rendered track
    instead of the previous preview."""
    ts_files = []
    pieces = iter(piece_paths)
    for i, part in enumerate(parts):
        ts = os.path.join(workdir, f"st_{i}.ts")
        if part[0] == "copy":
            _k, a, b, d = part
            media.run(["ffmpeg", "-y", "-v", "error",
                       "-ss", f"{a + d:.5f}", "-i", prev_local,
                       "-t", f"{b - a:.5f}", "-map", "0:v:0", "-c", "copy",
                       "-avoid_negative_ts", "make_zero",
                       "-f", "mpegts", ts])
        else:
            media.run(["ffmpeg", "-y", "-v", "error", "-i", next(pieces),
                       "-map", "0:v:0", "-c", "copy",
                       "-f", "mpegts", ts])
        ts_files.append(ts)
    lst = os.path.join(workdir, "stitch_list.txt")
    with open(lst, "w") as f:
        for ts in ts_files:
            f.write(f"file '{ts}'\n")
    vcat = os.path.join(workdir, "stitched_v.ts")
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
               "-i", lst, "-c", "copy", vcat])
    media.run(["ffmpeg", "-y", "-v", "error", "-i", vcat, "-i", audio_path,
               "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy",
               "-c:a", "copy", "-movflags", "+faststart", out_path])
    out_dur = media.duration_of(out_path)
    if abs(out_dur - expected_dur) > 0.25:
        raise media.MediaError(
            f"timeline-stitched length {out_dur:.2f}s vs expected "
            f"{expected_dur:.2f}s")
    return out_dur


# ---------------------------------------------------------------- assembly --

def keyframe_times(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
         "-of", "csv=p=0", path], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise media.MediaError(f"keyframe probe failed: {r.stderr[-200:]}")
    out = []
    for tok in r.stdout.split():
        tok = tok.strip().strip(",")          # csv writers append a trailing ,
        if not tok or tok == "N/A":
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return sorted(out)


def snap_windows(windows, kfs, file_dur, forbidden=()):
    """Snap each (a, b) OUTWARD to keyframe times of the previous preview.

    A cut boundary must satisfy TWO masters: stream copy can only start and
    stop on a keyframe, and no boundary may land INSIDE a caption event, an
    item's span or a transition junction zone (a half-copied xfade or a
    karaoke event clamped mid-word is a visible seam). Boundaries walk
    outward keyframe by keyframe until both hold — or the whole idea is
    abandoned (None) and the full render runs."""
    if not kfs:
        return None

    def _in_zone(t):
        return any(a + 0.005 < t < b - 0.005 for a, b in forbidden)

    out = []
    for a, b in windows:
        lo = [k for k in kfs if k <= a + 0.001] or [0.0]
        ka = lo[-1]
        while _in_zone(ka):
            lo = lo[:-1]
            if not lo:
                ka = 0.0
                break
            ka = lo[-1]
        hi = [k for k in kfs if k >= b - 0.001] or [file_dur]
        kb = hi[0]
        while _in_zone(kb):
            hi = hi[1:]
            if not hi:
                kb = file_dur
                break
            kb = hi[0]
        out.append([ka, kb])
    merged = []
    for w in sorted(out):
        if merged and w[0] <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
    if len(merged) > _MAX_WINDOWS or \
            sum(b - a for a, b in merged) > _MAX_COVER * file_dur:
        return None
    return [(round(a, 5), round(b, 5)) for a, b in merged]


def assemble(prev_local, piece_paths, windows, file_dur, workdir, out_path):
    """Copy the gaps from prev_local, drop in the re-encoded pieces, concat,
    and mux with prev_local's untouched audio."""
    parts = []                                # (kind, a, b) | (piece, path)
    cursor = 0.0
    for (a, b), piece in zip(windows, piece_paths):
        if a - cursor > 0.01:
            parts.append(("copy", cursor, a))
        parts.append(("piece", piece))
        cursor = b
    if file_dur - cursor > 0.01:
        parts.append(("copy", cursor, file_dur))

    ts_files = []
    for i, part in enumerate(parts):
        ts = os.path.join(workdir, f"st_{i}.ts")
        if part[0] == "copy":
            _k, a, b = part
            media.run(["ffmpeg", "-y", "-v", "error",
                       "-ss", f"{a:.5f}", "-i", prev_local,
                       "-t", f"{b - a:.5f}", "-map", "0:v:0", "-c", "copy",
                       "-avoid_negative_ts", "make_zero",
                       "-f", "mpegts", ts])
        else:
            media.run(["ffmpeg", "-y", "-v", "error", "-i", part[1],
                       "-map", "0:v:0", "-c", "copy",
                       "-f", "mpegts", ts])
        ts_files.append(ts)
    lst = os.path.join(workdir, "stitch_list.txt")
    with open(lst, "w") as f:
        for ts in ts_files:
            f.write(f"file '{ts}'\n")
    vcat = os.path.join(workdir, "stitched_v.ts")
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
               "-i", lst, "-c", "copy", vcat])
    media.run(["ffmpeg", "-y", "-v", "error", "-i", vcat, "-i", prev_local,
               "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy",
               "-movflags", "+faststart", out_path])
    out_dur = media.duration_of(out_path)
    if abs(out_dur - file_dur) > 0.25:
        raise media.MediaError(
            f"stitched length {out_dur:.2f}s vs expected {file_dur:.2f}s")
    return out_dur
