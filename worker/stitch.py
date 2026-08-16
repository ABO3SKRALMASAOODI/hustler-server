"""Stitched previews (round 93): re-encode the CHANGED seconds, keep the rest.

"Why is it still rendering the preview?" — because until this module, every
preview re-encoded the whole program no matter how small the edit: a title
added at 0:05 re-rendered all ten minutes. The EDL write is instant; the
wait was the file. This makes the preview cost O(change):

  1. GATE (`plan`): stitching happens ONLY when the new EDL differs from the
     last-rendered one in VIDEO-LOCAL layers — texts, vectors, zooms, overlays,
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
import math
import os
import re
import subprocess

import media
import travel
from schemas import MIN_SPAN_S, anim_value

# Video-local layers a stitch may differ in. Everything else must be equal.
_CHANGEABLE_TOP = ("texts", "vectors", "patches", "overlays")
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
    for v in (edl.get("vectors") or []):
        out.append((float(v["start"]), float(v["end"]), "vector"))
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
                         ("vectors", edl.get("vectors")),
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
            src = {"texts": edl.get("texts"),
                   "vectors": edl.get("vectors"),
                   "patches": edl.get("patches"),
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

def _slice_local_anim(value, local_start, local_end):
    """Return the part of an element-local curve inside one proof window.

    ``window_edl`` rebases an intersecting text/vector onto a standalone
    piece.  Its keyframes are local to the *original* element start, so a
    left-hand clip must move their clock as well as the element's program
    timestamps.  A nonlinear easing segment cannot simply be shortened and
    given the same easing name: the restricted part of (say) an ease-in curve
    is not another full ease-in curve.  Boundary-cut nonlinear segments are
    therefore sampled into a few linear subsegments.  Complete authored
    segments retain their original easing, and all authored interior anchors
    are retained when the 24-keyframe schema budget permits it.

    Scalars need no work.  A wholly contained item never calls this helper,
    which preserves its exact stored curve/signature.
    """
    if not isinstance(value, list) or not value:
        return value
    start = max(0.0, float(local_start))
    end = max(start, float(local_end))
    duration = end - start

    def _key_dict(keyframe):
        if isinstance(keyframe, dict):
            return dict(keyframe)
        return {"t": keyframe.t, "v": keyframe.v,
                "ease": keyframe.ease}

    def _time(keyframe):
        raw = keyframe.get("t") if isinstance(keyframe, dict) \
            else keyframe.t
        return float(raw or 0.0)

    if duration <= 1e-9:
        return round(float(anim_value(value, start)), 4)

    source = sorted((_key_dict(keyframe) for keyframe in value),
                    key=lambda keyframe: _time(keyframe))
    source_times = [_time(keyframe) for keyframe in source]
    anchors = [start]
    anchors.extend(when for when in source_times
                   if start + 1e-9 < when < end - 1e-9)
    anchors.append(end)

    # A valid source curve has at most 24 keyframes. Two synthetic boundaries
    # can still take a slice over that limit when the original first/last
    # keyframes do not sit at the element edges. Keep the most evenly spaced
    # authored anchors in that rare case; this is a temporary proof EDL, never
    # a rewrite of the user's curve.
    if len(anchors) > 24:
        interior = anchors[1:-1]
        slots = 22
        picked = []
        for i in range(slots):
            pos = round(i * (len(interior) - 1) / max(1, slots - 1))
            value_at = interior[int(pos)]
            if value_at not in picked:
                picked.append(value_at)
        anchors = [start] + picked[:slots] + [end]

    def _source_segment(a, b):
        """(left, right, incoming ease) for one anchor interval."""
        middle = (a + b) / 2.0
        for i in range(1, len(source)):
            if middle <= source_times[i] + 1e-9:
                return (source_times[i - 1], source_times[i],
                        source[i].get("ease"))
        return None, None, None

    # Add up to three exact samples inside each boundary-cut nonlinear
    # segment. Allocate within the schema budget; dense authored curves already
    # have close anchors and need fewer synthetic samples.
    partial = []
    for a, b in zip(anchors, anchors[1:]):
        left, right, ease = _source_segment(a, b)
        full = (left is not None and abs(a - left) <= 1e-9
                and abs(b - right) <= 1e-9)
        if ease in {"in", "out", "in_out"} and not full:
            partial.append((a, b))
    room = max(0, 24 - len(anchors))
    allocations = [0] * len(partial)
    for i in range(min(room, 3 * len(partial))):
        allocations[i % len(partial)] += 1
    samples = list(anchors)
    for (a, b), count in zip(partial, allocations):
        samples.extend(a + (b - a) * n / (count + 1)
                       for n in range(1, count + 1))
    samples.sort()

    sliced = []
    last_t = None
    for absolute in samples:
        shifted_t = round(absolute - start, 3)
        if last_t is not None and shifted_t <= last_t + 1e-9:
            continue
        point = {"t": shifted_t,
                 "v": round(float(anim_value(value, absolute)), 4)}
        if sliced:
            previous_absolute = start + sliced[-1]["t"]
            left, right, ease = _source_segment(previous_absolute, absolute)
            full = (left is not None
                    and abs(previous_absolute - left) <= 1e-6
                    and abs(absolute - right) <= 1e-6)
            # An intact authored segment keeps its exact curve. A hold remains
            # exact even when entered after its original left boundary.
            if ease == "hold" or (full and ease not in (None, "linear")):
                point["ease"] = ease
        sliced.append(point)
        last_t = shifted_t
    return sliced[0]["v"] if len(sliced) == 1 else sliced


def _clip_program_graphics(items, w0, w1):
    """Clip/rebase text or vector items to a standalone proof window."""
    out = []
    for item in items or []:
        original_start = float(item["start"])
        original_end = float(item["end"])
        clipped_start = max(original_start, w0)
        clipped_end = min(original_end, w1)
        clipped_duration = clipped_end - clipped_start
        # Text/vector validation requires a useful 0.3s window.  A smaller
        # boundary sliver cannot render as a valid standalone graphic.
        if clipped_duration < 0.3 - 1e-6:
            continue

        left_clipped = clipped_start > original_start + 0.001
        right_clipped = clipped_end < original_end - 0.001
        shifted = {
            **item,
            "start": round(clipped_start - w0, 3),
            "end": round(clipped_end - w0, 3),
        }
        # Text/vector timestamps canonicalize to centiseconds during schema
        # validation. A valid exact 0.30s item shifted by a half-centisecond
        # window boundary became 3.375-3.675, then rounded to 3.38-3.67 and
        # was rejected as 0.29s. Expand only that rounding casualty outward
        # on the left; authored spans longer than the minimum are untouched.
        if round((round(shifted["end"], 2)
                  - round(shifted["start"], 2)) * 100) < 30:
            shifted["start"] = round(max(
                0.0, math.floor((shifted["start"] + 1e-9) * 100) / 100), 3)
        if left_clipped and "entrance" in shifted:
            # For designed text, None means "use the template default";
            # explicit "none" is the animation-free value.
            shifted["entrance"] = "none"
        if right_clipped and "exit" in shifted:
            shifted["exit"] = "none"

        if (left_clipped or right_clipped) and isinstance(
                shifted.get("motion"), dict):
            motion = dict(shifted["motion"])
            local_start = clipped_start - original_start
            local_end = clipped_end - original_start
            for prop in ("x", "y", "scale", "rotation", "opacity"):
                if prop in motion:
                    motion[prop] = _slice_local_anim(
                        motion[prop], local_start, local_end)
            shifted["motion"] = motion
        out.append(shifted)
    return out


def _zoom_path_value(item, key, fraction, default):
    """Evaluate one authored zoom-path axis at a window fraction."""
    points = sorted((item.get("path") or []),
                    key=lambda point: float(point.get("f") or 0.0))
    if not points:
        return float(default)
    fraction = min(max(float(fraction), 0.0), 1.0)
    if fraction <= float(points[0].get("f") or 0.0):
        return float(points[0].get(key, default))
    for left, right in zip(points, points[1:]):
        f0 = float(left.get("f") or 0.0)
        f1 = float(right.get("f") or 0.0)
        if fraction <= f1 + 1e-9:
            if f1 - f0 <= 1e-9:
                return float(right.get(key, default))
            u = (fraction - f0) / (f1 - f0)
            u = travel.ease_value(u, item.get("ease"))
            v0 = float(left.get(key, default))
            v1 = float(right.get(key, default))
            return v0 + (v1 - v0) * u
    return float(points[-1].get(key, default))


def _zoom_strength_at(item, absolute_t):
    """Python mirror of the renderer's zoom-strength expressions."""
    start, end = float(item["start"]), float(item["end"])
    span = max(end - start, 1e-9)
    fraction = min(max((absolute_t - start) / span, 0.0), 1.0)
    strength = float(item.get("strength") or 0.25)
    mode = item.get("mode") or "punch"
    if mode == "path":
        return _zoom_path_value(item, "s", fraction, strength)
    if mode in ("ease", "follow"):
        ramp = max(0.15, min(0.4, span / 4.0))
        return strength * min(max((absolute_t - start) / ramp, 0.0), 1.0) \
            * min(max((end - absolute_t) / ramp, 0.0), 1.0)
    if mode == "push_in":
        return strength * fraction
    if mode == "pull_out":
        return strength * (1.0 - fraction)
    return strength


def _clip_program_zooms(items, w0, w1):
    """Clip/rebase zooms without changing their visible motion.

    Changed-section containment grows a requested proof around whole zooms,
    then its hard 25-second compute budget may cut the final window back
    through one.  Keeping the original end timestamp in that shorter
    standalone EDL makes schema validation reject the proof before ffmpeg can
    render it.  Simple punch zooms can be clipped directly.  A partially cut
    eased/drifting/travelling zoom is represented as a temporary linear
    strength path sampled from the original curve, preserving the state at
    the proof boundaries rather than inventing a fresh ramp there.  This is a
    proof-only copy; the user's stored EDL is never rewritten.
    """
    out = []
    for item in items or []:
        original_start = float(item["start"])
        original_end = float(item["end"])
        clipped_start = max(original_start, w0)
        clipped_end = min(original_end, w1)
        clipped_duration = clipped_end - clipped_start
        if clipped_duration < 0.2 - 1e-6:
            continue

        shifted = {**item,
                   "start": round(clipped_start - w0, 3),
                   "end": round(clipped_end - w0, 3)}
        left_clipped = clipped_start > original_start + 0.001
        right_clipped = clipped_end < original_end - 0.001
        mode = item.get("mode") or "punch"
        if not (left_clipped or right_clipped) or mode == "punch":
            out.append(shifted)
            continue

        # Preserve all authored travel waypoints and the exact corners of the
        # renderer's hidden ease/follow ramp.  Path-mode cubic pieces are
        # sampled between anchors because a clipped subsection of a cubic is
        # not another complete cubic with the same easing name.
        sample_times = {clipped_start, clipped_end}
        span = original_end - original_start
        if mode in ("ease", "follow"):
            ramp = max(0.15, min(0.4, span / 4.0))
            sample_times.update((original_start + ramp,
                                 original_end - ramp))
        if mode in ("follow", "path"):
            for point in item.get("path") or []:
                sample_times.add(original_start
                                 + float(point.get("f") or 0.0) * span)
        sample_times = sorted(t for t in sample_times
                              if clipped_start - 1e-9 <= t
                              <= clipped_end + 1e-9)
        if mode == "path" and item.get("ease") not in (None, "linear"):
            # The clipped curve is emitted as a linear temporary path. Keep
            # every authored anchor when practical, then repeatedly bisect
            # the largest uncovered interval. Twenty-four samples bound the
            # filtergraph while putting a cubic's linear approximation below
            # a visible frame/strength delta in regression tests.
            if len(sample_times) > 24:
                sample_times = [
                    clipped_start + clipped_duration * idx / 23.0
                    for idx in range(24)
                ]
            else:
                dense = list(sample_times)
                while len(dense) < 24:
                    left, right = max(
                        zip(dense, dense[1:]), key=lambda pair: pair[1]
                        - pair[0])
                    if right - left <= 1e-6:
                        break
                    dense.append((left + right) / 2.0)
                    dense.sort()
                sample_times = dense

        path = []
        for absolute_t in sample_times:
            original_fraction = min(max(
                (absolute_t - original_start) / max(span, 1e-9), 0.0), 1.0)
            local_fraction = ((absolute_t - clipped_start)
                              / clipped_duration)
            cx = (_zoom_path_value(item, "cx", original_fraction, 0.5)
                  if mode in ("follow", "path")
                  else float(item.get("cx") if item.get("cx") is not None
                             else 0.5))
            cy = (_zoom_path_value(item, "cy", original_fraction, 0.5)
                  if mode in ("follow", "path")
                  else float(item.get("cy") if item.get("cy") is not None
                             else 0.5))
            path.append({"f": round(local_fraction, 4),
                         "cx": round(cx, 3), "cy": round(cy, 3),
                         "s": round(_zoom_strength_at(item, absolute_t), 3)})
        # Duplicate times can arise where a ramp corner is also an authored
        # waypoint.  The schema accepts two or more strictly ordered path
        # fractions; de-duplicate after centisecond/fraction rounding.
        deduped = []
        for point in path:
            if deduped and point["f"] <= deduped[-1]["f"] + 1e-9:
                deduped[-1] = point
            else:
                deduped.append(point)
        if len(deduped) < 2:
            out.append(shifted)
            continue
        deduped[0]["f"], deduped[-1]["f"] = 0.0, 1.0
        shifted["mode"] = "path"
        shifted["path"] = deduped
        shifted["ease"] = "linear"
        out.append(shifted)
    return out


def window_edl(edl, tl, w0, w1, keep_audio=False):
    """The EDL that renders output [w0, w1] of `edl`, standalone.

    Full-preview stitching calls this with windows expand() grew to contain
    every intersecting element.  Changed-section proofs can subsequently
    clamp that expansion to their physical render budget, so text/vector and
    overlay items also support safe boundary clipping here."""
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
                src_a, src_b = round(src_a, 3), round(src_b, 3)
                # A proof/check window may begin a few hundredths before an
                # existing EDL boundary. Keeping that boundary sliver creates
                # an invalid standalone EDL even though the saved full EDL is
                # valid (production jobs 11851/11893/12058: 0.030s). It is
                # below both the schema's useful span and render tolerance, so
                # omit it from the temporary proof rather than blaming the edit.
                if src_b - src_a < MIN_SPAN_S - 1e-9:
                    acc += plen
                    continue
                keep.append([src_a, src_b])
                if abs(f - 1.0) > 1e-9:
                    speed.append({"id": f"sw{len(speed) + 1}",
                                  "start": src_a,
                                  "end": src_b, "factor": f})
            acc += plen
    e["keep"] = keep
    e["speed"] = speed
    fx = dict(e.get("effects") or {})

    # Full-preview stitching normally expands windows to contain every
    # graphic.  Changed-section proofs re-apply a hard 25s budget after that
    # expansion, though, so the last of several windows can be truncated in
    # the middle of a text/vector.  Clip those program windows and their
    # element-local motion rather than leaving an end/keyframe beyond this
    # standalone EDL's duration.
    e["texts"] = _clip_program_graphics(e.get("texts"), w0, w1)
    e["vectors"] = _clip_program_graphics(e.get("vectors"), w0, w1)
    fx["zooms"] = _clip_program_zooms(fx.get("zooms"), w0, w1)

    def _shift_optional_window(items, clip=False):
        """Shift timed effects and preserve whole-program effects.

        The ordinary stitch gate historically guaranteed regions/custom
        were windowed and contained. Changed-section proofs may instead
        sample a global effect or a stylize span larger than the proof.
        Clipping stylize to that sample prevents a two-second standalone EDL
        from retaining a full-program timestamp range.
        """
        out = []
        for item in items or []:
            a, b = item.get("start"), item.get("end")
            if a is None and b is None:
                out.append(dict(item))
                continue
            if a is None or b is None:
                continue
            a, b = float(a), float(b)
            if a >= w1 - 0.001 or b <= w0 + 0.001:
                continue
            if clip:
                a, b = max(a, w0), min(b, w1)
            out.append({**item, "start": round(a - w0, 3),
                        "end": round(b - w0, 3)})
        return out

    fx["regions"] = _shift_optional_window(fx.get("regions"))
    fx["custom"] = _shift_optional_window(fx.get("custom"))
    fx["stylize"] = _shift_optional_window(fx.get("stylize"), clip=True)
    # Proof-budget clamping can cut the RIGHT edge of the last contained
    # overlay. Merely shifting its start leaves the original duration on a
    # shorter standalone EDL, which validate_edl rejects (the live failure was
    # a 4.79s overlay inside the 2.60s left in its proof piece). Clip the
    # element and its local animation curves to the bytes the piece contains.
    from schemas import clip_anim
    overlays = []
    piece_dur = w1 - w0
    for item in e.get("overlays") or []:
        original_start = float(item.get("start") or 0.0)
        original_end = original_start + float(item.get("duration_s") or 0.0)
        clipped_start = max(original_start, w0)
        clipped_end = min(original_end, w1)
        clipped_dur = clipped_end - clipped_start
        if clipped_dur < 0.2 - 1e-6:
            continue
        shifted = {**item,
                   "start": round(clipped_start - w0, 3),
                   "duration_s": round(clipped_dur, 3)}
        left_trim = max(0.0, clipped_start - original_start)
        if left_trim and shifted.get("source_start_s") is not None:
            shifted["source_start_s"] = round(
                float(shifted["source_start_s"]) + left_trim, 3)
        if left_trim:
            shifted["entrance"] = None
        if clipped_end < original_end - 0.001:
            shifted["exit"] = None
        for prop in ("x", "y", "scale", "rotation", "opacity"):
            if prop in shifted:
                shifted[prop] = clip_anim(shifted[prop], clipped_dur)
        # A tracked screen has its own time-indexed camera geometry. The
        # changed-section planner normally contains it whole; if the hard
        # budget ever clips one, omitting the partial proof is safer than
        # validating/rending a false camera path.
        if shifted.get("screen") and (
                left_trim > 0.001 or clipped_end < original_end - 0.001):
            continue
        if shifted["start"] <= piece_dur - 0.1 + 1e-6:
            overlays.append(shifted)
    e["overlays"] = overlays
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
    inserts = [i for i in (e.get("inserts") or [])
               if i.get("id") in iw
               and iw[i["id"]][0] >= w0 - 0.011
               and iw[i["id"]][1] <= w1 + 0.011]
    # at_output_s is on the PRE-insert clock. The window boundaries above are
    # on the FINAL clock, so merely retaining the original value strands a
    # carried insert at a non-existent junction in the standalone EDL. This
    # happened when a proof began 0.05s before four inserts at 2.73s: the new
    # keep was 0.05s long but every insert still claimed the old 2.73s seam.
    # Rebase from each insert's known final window, subtracting only inserts
    # already carried into this proof piece.
    consumed = 0.0
    for item in sorted(inserts, key=lambda value: iw[value["id"]][0]):
        final_start = float(iw[item["id"]][0])
        item["at_output_s"] = round(
            max(0.0, final_start - w0 - consumed), 3)
        consumed += float(item.get("duration_s") or 0.0)
    e["inserts"] = inserts
    # captions/graphics burn from the SHIFTED full-program ASS (see
    # shift_ass) — the windowed EDL itself must not rebuild them.
    e["captions"] = None
    # Caption suppression has already been applied while building that full-
    # program ASS. Retaining its absolute program spans in this standalone
    # local EDL can only make validation fail after the clock was rebased.
    e["caption_mutes"] = []
    # fades belong to the program's ends; plan() refused windows near them
    if isinstance(fx, dict):
        fx["fade_in_s"] = 0.0
        fx["fade_out_s"] = 0.0
    # Stitched pieces discard their audio and keep the previous preview's
    # track. Changed-section proof reels are different: the changed seconds
    # must sound like the current EDL, so shift output-anchored audio into the
    # local window. Voiceover has no stored duration; starting it before the
    # window is represented by advancing its source offset.  A bounded
    # voiceover is also clipped/removed against its actual duration.
    if keep_audio:
        music = []
        for item in e.get("music") or []:
            a, b = float(item.get("start") or 0.0), \
                float(item.get("end") or 0.0)
            if b <= w0 + 0.001 or a >= w1 - 0.001:
                continue
            clipped_a, clipped_b = max(a, w0), min(b, w1)
            shifted = dict(item, start=round(clipped_a - w0, 3),
                           end=round(clipped_b - w0, 3))
            if clipped_a > a:
                shifted["offset_s"] = round(
                    float(item.get("offset_s") or 0.0) + clipped_a - a, 3)
                shifted["fade_in_s"] = None
            if clipped_b < b:
                shifted["fade_out_s"] = None
            music.append(shifted)
        e["music"] = music
        e["sfx"] = [dict(item, at=round(float(item.get("at") or 0.0)
                                        - w0, 3))
                    for item in (e.get("sfx") or [])
                    if w0 - 0.001 <= float(item.get("at") or 0.0)
                    < w1 - 0.001]
        voiceovers = []
        for item in e.get("voiceover") or []:
            start = float(item.get("start_output_s") or 0.0)
            duration = item.get("duration_s")
            finish = (start + float(duration)
                      if duration is not None else float("inf"))
            if start >= w1 - 0.001 or finish <= w0 + 0.001:
                continue
            shifted = dict(item)
            if start < w0:
                skipped = w0 - start
                shifted["source_offset_s"] = round(
                    float(item.get("source_offset_s") or 0.0) + skipped,
                    3)
                shifted["start_output_s"] = 0.0
                if duration is not None:
                    shifted["duration_s"] = round(float(duration) - skipped,
                                                  3)
            else:
                shifted["start_output_s"] = round(start - w0, 3)
            if duration is not None:
                local_start = max(start, w0)
                shifted["duration_s"] = round(min(
                    float(shifted["duration_s"]), w1 - local_start), 3)
            voiceovers.append(shifted)
        e["voiceover"] = voiceovers
    else:
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
    for v in (new_edl.get("vectors") or []):
        anchored.append((float(v["start"]), float(v["end"])))
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
