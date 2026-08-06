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
_CHANGEABLE_FX = ("zooms", "regions")

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
    return out


def _canon_items(edl):
    """{layer: {id: canonical-json}} for diffing the changeable layers."""
    out = {}
    fx = edl.get("effects") or {}
    for layer, items in (("texts", edl.get("texts")),
                         ("patches", edl.get("patches")),
                         ("overlays", edl.get("overlays")),
                         ("zooms", fx.get("zooms")),
                         ("regions", fx.get("regions"))):
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
                   "regions": fx.get("regions")}[l] or []
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
    e["overlays"] = _shift(
        e.get("overlays"),
        lambda o: (o.get("start") or 0.0,
                   (o.get("start") or 0.0) + (o.get("duration_s") or 0.0)),
        lambda o, a, b: {**o, "start": round(a, 3)})
    # patches ride the SOURCE clock: keep those whose source span survives
    e["patches"] = [p for p in (e.get("patches") or [])
                    if any(s0 < p["src_end"] and s1 > p["src_start"]
                           for s0, s1 in keep)]
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


def ass_events(path, animated_only=False):
    """[(start, end)] of Dialogue events. With animated_only, just the events
    a window boundary must never land inside — karaoke/transform/move/fade
    straddlers cannot be clamped (see shift_ass), so the keyframe snap has
    to route boundaries around exactly these and no more. Static events are
    NOT included: on a densely-captioned project they cover nearly every
    keyframe, and clamping handles them."""
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
