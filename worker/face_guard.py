"""Patches and overlays keep faces whole (lane EDIT-SWITCH, 14 Nov 2026).

On the Gauntlet's replays of real requests (.valmera/week-1112/lanes/EDIT-MODEL/table.json):

* p5518, "remove the watermark": gpt-6.1-sol and gpt-6.1-terra each repainted a rectangle that
  lay across the presenter's eye (erase_region, fill text). A repaint rebuilds a picture from what
  is around it; it cannot rebuild an eye, so the face came out smeared.
* p5533, "make the background dark like the curtain, except the floor": Haiku and gpt-5.5 laid a
  dark rectangle (add_region_effect exposure) over the top 70% of the frame, dimming the dancers
  themselves, and gpt-5.5 called it done.

Neither is a question of which model edits: a tool let a rectangle land on a person. So before the
write, the tools measure the faces under the rectangle, and a rectangle that covers one is not
written:

* a repaint (erase_region, erase_burned_text) is never written over a face, whatever was asked:
  the other rectangles of the same call go ahead, and the tool says which one stayed and why;
* a regional effect (add_region_effect on a rect / ellipse / polygon) over a face is written only
  when the editor says the customer asked for exactly that (``on_face`` true): the model reads the
  request, the code measures the frame. add_region_effect's own ``face`` region is that choice
  made explicitly, and blur_region stays the censor a customer asks for to hide a face.

Faces are measured on the exact frames (keepout.detect_faces: YuNet, else the cascades), on the
source for source-space rectangles and through the render's geometry (keepout.face_track) for
output-space ones. A context that cannot decode the footage measures nothing and writes as before:
the guard never blocks on its own failure.
"""

import os

import keepout

SAMPLES = 6           # frames measured across a window (fewer for a short one)
SHORT_S = 4.0


def _measure(ctx, cache_attr="_face_guard_faces", cap=48):
    """src_t -> face boxes (source fractions) on that exact frame, or None when unmeasurable."""
    if not getattr(ctx, "has_main_video", True):
        return None
    try:
        proxy = ctx.proxy_path()
        workdir = ctx.workdir
    except Exception:  # noqa: BLE001 - no footage here: the guard stands aside
        return None
    if not proxy or not os.path.exists(proxy) or not workdir:
        return None
    cache = getattr(ctx, cache_attr, None)
    if cache is None:
        cache = {}
        try:
            setattr(ctx, cache_attr, cache)
        except Exception:  # noqa: BLE001
            pass

    def measure(src_t):
        import media
        key = round(float(src_t), 2)
        if key in cache:
            return cache[key]
        if len(cache) >= cap:
            return None
        fp = os.path.join(workdir, f"faceguard_{int(round(key * 100))}.jpg")
        try:
            media.frame_at(proxy, key, fp, width=480)
            faces = keepout.detect_faces(fp)
        except Exception:  # noqa: BLE001 - that moment is unmeasured
            faces = None
        cache[key] = faces
        return faces
    return measure


def sample_times(start, end, duration):
    """Source seconds to measure: across [start, end], or the whole video when no window."""
    try:
        dur = float(duration or 0)
    except (TypeError, ValueError):
        dur = 0.0
    a = 0.0 if start is None else max(0.0, float(start))
    b = dur if end is None else float(end)
    if dur > 0:
        b = min(b, max(0.0, dur - 0.05))
    if b <= a:
        return [round(a, 2)]
    n = 3 if b - a <= SHORT_S else SAMPLES
    return [round(a + (b - a) * (i + 0.5) / n, 2) for i in range(n)]


def _box(item):
    """{x, y, w, h} fractions -> (x0, y0, x1, y1)."""
    x, y = float(item["x"]), float(item["y"])
    return (x, y, min(1.0, x + float(item["w"])), min(1.0, y + float(item["h"])))


def covering(box, faces):
    """The first face box this rectangle covers (a real share of the face zone, or the rectangle
    lying mostly on it, keepout's own test for graphics), else None."""
    for f in faces or ():
        if keepout.on_face(box, [keepout.face_zone(f)]):
            return f
    return None


def source_hit(ctx, box, times, measure=None):
    """(src_t, face box) where the source-space rectangle covers a face, else None. None too when
    nothing could be measured."""
    measure = measure or _measure(ctx)
    if measure is None:
        return None
    for t in times:
        faces = measure(t)
        hit = covering(box, faces) if faces else None
        if hit is not None:
            return t, hit
    return None


def program_source_times(edl, start, end, duration):
    """Source seconds under program seconds [start, end] (the kept footage there), or the whole
    video's samples when no window is given."""
    if start is None or end is None:
        return sample_times(None, None, duration)
    try:
        from timeline import Timeline
        tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
        outs = sample_times(start, end, tl.out_duration)
        src = [tl.out_to_src(t) for t in outs]
        return [round(float(t), 2) for t in src if t is not None] or sample_times(None, None, duration)
    except Exception:  # noqa: BLE001 - no timeline: the whole video
        return sample_times(None, None, duration)


def erase_split(ctx, items, measure=None):
    """(kept, refused): erase rectangles (source fractions, source-second windows) split by
    whether they lie on a face. refused rows are (item, src_t, face box)."""
    measure = measure or _measure(ctx)
    if measure is None:
        return list(items), []
    kept, refused = [], []
    for it in items:
        try:
            box = _box(it)
        except (KeyError, TypeError, ValueError):
            kept.append(it)
            continue
        hit = source_hit(ctx, box, sample_times(it.get("start"), it.get("end"),
                                                getattr(ctx, "duration", None)), measure)
        if hit is None:
            kept.append(it)
        else:
            refused.append((it, hit[0], hit[1]))
    return kept, refused


def erase_refusal_line(refused):
    if not refused:
        return ""
    where = "; ".join(f"[{it.get('id')}] x={it['x']:g},y={it['y']:g} size {it['w']:g}x{it['h']:g} "
                      f"(a face at {t:g} s)" for it, t, _f in refused)
    return ("NOT REPAINTED, it lies on a person's face: " + where + ". A repaint rebuilds the "
            "picture from what is around it and cannot rebuild an eye, a mouth or skin: it smears "
            "the face. Leave whatever sits on the face where it is, erase only what lies off it, "
            "and tell the customer plainly that the part on the face stays (cropping it out with "
            "set_frame, or a cover they choose, are the honest alternatives). Do NOT say it was "
            "removed.")


def output_hit(ctx, edl, box, start, end):
    """keepout.assess of an output-space rectangle over [start, end] program seconds: the colliding
    seconds and face, or None (no collision, or nothing measured)."""
    try:
        import motion_tools
        W, H = motion_tools._canvas_size(ctx, edl)
        track = keepout.face_track(edl, getattr(ctx, "index", None) or {}, W, H, start, end,
                                   measure=_measure(ctx))
    except Exception:  # noqa: BLE001 - unmeasured: the write goes ahead as before
        return None
    if not track:
        return None
    got = keepout.assess(tuple(box), track)
    return got if got.get("hit") else None


def overlay_refusal(what, when, flag):
    span = f" from {when[0]:.1f} to {when[1]:.1f} s" if when else ""
    return (f"REJECTED: that {what} lies on a person's face{span} (measured on the frames). It "
            "would dim, hide or discolour the person, not the background. Keep the rectangle off "
            "the face (smaller, or beside it), or for a background treatment use a subject-aware "
            "tool (add_subject_effect, set_background) that leaves the people whole. Only when "
            f"the customer asked for exactly this on that face, call again with {flag}=true.")
