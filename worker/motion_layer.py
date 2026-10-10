"""Renderer glue for EDL.motion: render clips, add inputs, composite.

Each MotionItem renders (worker/motion_engine.py) to a transparent clip
cropped to what it draws, then overlays the program on the FINAL clock at
its z-layer: ``below_captions`` composites before the caption burn (dialogue
stays on top), ``above_captions`` after the text-graphics burn (a designed
moment wins). Both sit under the frame-shift bars, the floating plate, the
native watermark and the end card, so branding is never covered.

``behind_subject`` items are not composited by ``append_graph`` at all: the
renderer hands them to the behind-subject stage (the same split/alphamerge
composite add_text_behind's words use, before the zoom stage), which draws
the clip on the picture with ``overlay_clip`` and lays the measured subject
back over it. One whose mask cannot be used is ``demote``d to an ordinary
above-captions graphic with a logged warning.

Failure policy: a motion item that cannot render degrades to "absent" with
a logged reason recorded on ``LAST_WARNINGS`` rather than failing the whole
render — the user still gets the edit, and the write tool already proved the
composition renders when it was added.

Legibility: before rendering, ``prepare_inputs`` asks the renderer's plate
probe (worker/plate.py) for the program picture at a few moments inside each
item — every cue of the motion-caption track — and hands each composition
its grid as ``MG.plate``, so light type over a bright shirt can raise its own
backing. A probe that fails leaves the item without a plate (rendered exactly
as before).
"""

import math
import os
from contextvars import ContextVar

import motion_engine
import motion_templates
import plate as plate_mod

LAST_WARNINGS = []
# Per-render collector (renderer._run_render_job installs one). LAST_WARNINGS
# is process-global and cleared by whichever render starts next, so a result
# built from it could report another job's skips.
_WARNING_SINK = ContextVar("motion_warning_sink", default=None)


def collect_warnings(sink):
    """Also record this context's skip reasons into ``sink``; returns the
    token for stop_collecting."""
    return _WARNING_SINK.set(sink)


def stop_collecting(token):
    _WARNING_SINK.reset(token)


def warn(msg):
    """Log one degraded motion item and record it for the render result."""
    print(f"[render] {msg}", flush=True)
    LAST_WARNINGS.append(msg)
    sink = _WARNING_SINK.get()
    if sink is not None:
        sink.append(msg)


def program_items(edl, out_duration):
    out = []
    for item in edl.get("motion") or []:
        s, e = float(item["start"]), min(float(item["end"]), float(out_duration))
        if e - s >= 0.05:
            out.append(dict(item, end=e))
    return out


# Plate moments per item: a short graphic is sampled twice, a long one up to
# PLATE_MAX_PER_ITEM times; a caption cue once at its middle (twice when it
# is long enough to span a camera move).
PLATE_MAX_PER_ITEM = 5
PLATE_EVERY_S = 1.2
PLATE_CUE_SPLIT_S = 1.6


def plate_moments(item):
    """[(program second, composition second)] at which to measure the plate
    under ``item``. Composition seconds are what the page's MG.t reads (a
    windowed fragment's phase included)."""
    s, e = float(item["start"]), float(item["end"])
    if item.get("template") == "caption_motion":
        out = []
        for c in (item.get("params") or {}).get("cues") or []:
            try:
                a, b = float(c["s"]), float(c["e"])
            except (KeyError, TypeError, ValueError):
                continue
            if b - a < 0.04:
                continue
            ts = [(a + b) / 2.0] if b - a < PLATE_CUE_SPLIT_S else \
                [a + 0.3 * (b - a), a + 0.75 * (b - a)]
            out += [(s + x, x) for x in ts if s + x <= e + 1e-6]
        return out
    d = e - s
    if d <= 0:
        return []
    phase = float(item.get("phase_s") or 0.0)
    n = max(2, min(PLATE_MAX_PER_ITEM, int(d / PLATE_EVERY_S) + 1))
    return [(s + d * (i + 0.5) / n, phase + d * (i + 0.5) / n)
            for i in range(n)]


def measure_plates(items, probe):
    """{index in items: MG.plate dict} for the items the probe could
    measure. One probe call for the whole render (moments shared between
    items are measured once); any failure leaves items without a plate."""
    if probe is None or not items:
        return {}
    plan, want = [], {}
    for k, item in enumerate(items):
        try:
            for prog_t, comp_t in plate_moments(item):
                key = round(prog_t, 2)
                want.setdefault(key, None)
                plan.append((k, key, comp_t))
        except Exception:  # noqa: BLE001 — that item goes unmeasured
            continue
    if not want:
        return {}
    keys = sorted(want)
    try:
        grids = probe(keys)
    except Exception as e:  # noqa: BLE001 — fail open: render as before
        print(f"[render] plate probe failed ({str(e)[:160]}) — graphics "
              "keep their default contrast", flush=True)
        return {}
    st = getattr(probe, "stats", None)
    if isinstance(st, dict):
        print(f"[render] plate: {len(keys)} moments ({st.get('cached', 0)} "
              f"cached, {st.get('decoded', 0)} decoded) in "
              f"{st.get('seconds', 0.0):.2f}s", flush=True)
    got = dict(zip(keys, grids or []))
    cols = getattr(probe, "cols", None)
    rows = getattr(probe, "rows", None)
    out = {}
    for k, key, comp_t in plan:
        g = got.get(key)
        if not g or not cols or not rows or len(g) != cols * rows:
            continue
        p = out.setdefault(k, {"c": cols, "r": rows, "s": []})
        p["s"].append({"t": round(comp_t, 3), "g": plate_mod.encode_grid(g)})
    return out


# ── the persistent headline band: yielding to other graphics ────────────
# A persistent template (motion_templates.persistent: the headline of a card
# or letterbox layout) holds its band for the program. While another graphic
# occupies that band it fades out (YIELD_OUT_S, ending as the other lands) and
# comes back YIELD_IN_S after it leaves; two band graphics less than
# YIELD_MERGE_S apart keep it away (a flash back for half a second is a
# flicker, not a restore). Decided at render time from the stored footprints,
# so adding, moving or removing a lockup never leaves a stale headline.
YIELD_OUT_S = 0.12
YIELD_IN_S = 0.3
YIELD_MERGE_S = 1.2
# Boxes this close (frame fractions) already read as one crowded band.
YIELD_PAD = 0.006


def _band_box(item, W, H):
    """Where an item draws (frame fractions): its fresh footprint, else the
    template's nominal ink estimate, else None (it occupies no band)."""
    import caption_carry
    ar = caption_carry.frame_ar(W, H)
    box = caption_carry.footprint_box(item, ar)
    if box:
        return [float(v) for v in box]
    if item.get("box"):
        # the capture hint bounds what it may draw (an authored page)
        try:
            hint = [float(v) for v in item["box"]]
        except (TypeError, ValueError):
            hint = None
        if hint and len(hint) == 4 and hint[2] > hint[0] and hint[3] > hint[1]:
            return hint
    name = item.get("template")
    if not name or name == "html":
        return None
    return _nominal(item, W, H)


def _nominal(item, W, H):
    """The template's nominal ink estimate (a lockup's grown by the bridge
    lines its reading sets), or None."""
    import caption_carry
    import keepout
    try:
        est = caption_carry.bridged_box(keepout.nominal_ink(
            item.get("template"), motion_templates.spec(item.get("template")),
            item.get("params") or {}, frame=(W, H)), item)
    except Exception:  # noqa: BLE001 — an unknown box occupies nothing
        return None
    return [float(v) for v in est] if est else None


def _ink_lead(item):
    """Seconds into an item (from its start on the program clock) before it
    draws anything: a phrase build whose first row is revealed on a later
    spoken word leaves its band empty until then (the Jobs 'liberal arts'
    lockup: 1.17 s), and the headline keeps the band meanwhile. The reveal
    is the page's own (caption_carry.lockup_reveals: spoken rows and bridge
    lines on their onsets from the item's reading, others on their 'at', in
    reading order) on the composition clock, so a windowed piece already
    ``phase_s`` into the composition has that much less to wait."""
    import caption_carry
    reveals = caption_carry.lockup_reveals(item)
    if not reveals:
        return 0.0
    phase = float(item.get("phase_s") or 0.0)
    return max(0.0, reveals[0] - phase)


def _shares_band(a, b, pad=YIELD_PAD):
    return (min(a[2], b[2]) - max(a[0], b[0]) > -pad
            and min(a[3], b[3]) - max(a[1], b[1]) > -pad)


def yield_windows(item, items, W, H):
    """Composition-second windows [[a, b], ...] in which the persistent
    ``item`` hands its band to the other ``items`` (program-clock motion
    items) whose box meets its own; [] for any other item, or when nothing
    meets it. Windows closer than YIELD_MERGE_S merge; they are clipped to
    the item's span."""
    if not motion_templates.persistent(item):
        return []
    # its band: what it drew (the footprint) and the band it may fill
    boxes = [b for b in (_band_box(item, W, H), _nominal(item, W, H)) if b]
    if not boxes:
        return []
    mine = [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
    s, e = float(item["start"]), float(item["end"])
    phase = float(item.get("phase_s") or 0.0)
    spans = []
    for other in items or []:
        if other is item or other.get("id") == item.get("id") \
                or other.get("_synthetic") \
                or str(other.get("template") or "").startswith("caption") \
                or motion_templates.persistent(other):
            continue
        try:
            a = max(s, float(other["start"]) + _ink_lead(other))
            b = min(e, float(other["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if b - a < 0.05:
            continue
        box = _band_box(other, W, H)
        if box and _shares_band(mine, box):
            spans.append([a, b])
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < YIELD_MERGE_S:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # a gap at either end of the composition shorter than the merge is no
    # restore (a stitched piece's own edges are not the composition's)
    full = float(item.get("full_duration_s") or (e - s))
    if merged and phase <= 1e-6 and merged[0][0] - s < YIELD_MERGE_S:
        merged[0][0] = s
    if merged and phase + (e - s) >= full - 1e-3 and e - merged[-1][1] < YIELD_MERGE_S:
        merged[-1][1] = e
    return [[round(a - s + phase, 3), round(b - s + phase, 3)] for a, b in merged]


def render_pieces(item, fps):
    """``item`` as the clips the engine renders: [item], or — a persistent
    item held longer than one clip may last (motion_engine.MAX_DURATION_S:
    a whole-program headline on a long program) — consecutive frame-aligned
    pieces on the SAME composition clock (phase_s / full_duration_s, exactly
    like a stitched preview's pieces), so the band never silently drops out
    of a long render."""
    s, e = float(item["start"]), float(item["end"])
    cap = float(motion_engine.MAX_DURATION_S)
    if e - s <= cap or not motion_templates.persistent(item) \
            or item.get("layer") == "behind_subject":
        return [item]
    fps = float(fps or 30.0)
    # two frames of slack: a frame-aligned piece is at most a frame longer
    n = int(math.ceil((e - s) / (cap - 2.0 / fps)))
    step_f = (e - s) * fps / n
    phase = float(item.get("phase_s") or 0.0)
    full = float(item.get("full_duration_s") or (e - s))
    edges = [s] + [s + round(k * step_f) / fps for k in range(1, n)] + [e]
    return [dict(item, start=round(a, 6), end=round(b, 6),
                 phase_s=round(phase + a - s, 6), full_duration_s=full)
            for a, b in zip(edges, edges[1:]) if b - a > 1e-6]


def yields_doc(windows):
    """The MG.yields input for build_document (None when there are none)."""
    if not windows:
        return None
    return {"w": windows, "out": YIELD_OUT_S, "in": YIELD_IN_S}


def headline_visible(item, items, W, H):
    """(seconds the persistent item shows, [[a, b], ...] program windows it
    yields) — for the write tools' report."""
    s, e = float(item["start"]), float(item["end"])
    phase = float(item.get("phase_s") or 0.0)
    wins = [[a + s - phase, b + s - phase]
            for a, b in yield_windows(item, items, W, H)]
    hidden = sum(b - a for a, b in wins)
    return max(0.0, (e - s) - hidden), wins


def prepare_inputs(edl, workdir, W, H, fps, out_duration, args, next_idx,
                   fetch_asset=None, extra_items=None, plate=None):
    """Render motion clips and append ffmpeg inputs. Returns (inputs, next_idx)
    with inputs = [(input_index, item, RenderedClip)]. ``extra_items`` are
    renderer-synthesized items (the motion caption track); ``plate`` is the
    renderer's plate probe (worker/plate.Probe) or None."""
    LAST_WARNINGS.clear()
    items = list(extra_items or []) + program_items(edl, out_duration)
    if not items:
        return [], next_idx
    plates = measure_plates(items, plate)
    asset_locals = {}
    jobs, kept = [], []
    for k, item in enumerate(items):
        try:
            for _k, key in motion_templates.asset_params(item["template"], item.get("params") or {}).items():
                if key not in asset_locals and fetch_asset is not None:
                    asset_locals[key] = fetch_asset(key)
            ydoc = yields_doc(yield_windows(item, items, W, H))
            pieces = render_pieces(item, fps)
            built = [motion_templates.build_job(
                part, W, H, fps, asset_locals, plate=plates.get(k),
                yields=ydoc) for part in pieces]
            jobs.extend(built)
            kept.extend(pieces)
        except Exception as e:  # noqa: BLE001 — degrade one item, keep the render
            warn(f"motion '{item.get('id')}' skipped: {str(e)[:200]}")
    if not jobs:
        return [], next_idx
    out_dir = os.path.join(workdir, "motion")
    try:
        clips = motion_engine.render_jobs(jobs, out_dir)
    except motion_engine.MotionRenderError as e:
        # One bad composition must not take the others down: retry singly.
        clips = []
        for job in jobs:
            try:
                clips.append(motion_engine.render_jobs([job], out_dir)[0])
            except motion_engine.MotionRenderError as e1:
                warn(f"{job.label} skipped: {str(e1)[:200]}")
                clips.append(None)
        if all(c is None for c in clips):
            print(f"[render] motion layer unavailable: {e}", flush=True)
    inputs = []
    for item, clip in zip(kept, clips):
        if clip is None or clip.empty:
            continue
        args += ["-i", clip.path]
        inputs.append((next_idx, item, clip))
        next_idx += 1
    return inputs, next_idx


def overlay_clip(parts, vlabel, idx, item, clip, fps, tag):
    """Overlay one rendered clip on [vlabel] inside its program window;
    returns the new label."""
    s, e = float(item["start"]), float(item["end"])
    # Hold the clip's last frame for a couple of frames: framesync can
    # quantize a fractional program clock a frame past the clip's EOF.
    parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba,"
                 f"tpad=stop_mode=clone:stop_duration={2.0 / max(fps, 1):.4f},"
                 f"setpts=PTS+{s:.4f}/TB[{tag}c]")
    parts.append(f"[{vlabel}][{tag}c]overlay=x={clip.x}:y={clip.y}:eof_action=pass"
                 f":format=auto:enable='gte(t,{s:.4f})*lt(t,{e:.4f})'[{tag}o]")
    return f"{tag}o"


def append_graph(parts, vlabel, inputs, layer, fps):
    """Composite this layer's clips over [vlabel]; returns the new label."""
    j = 0
    for idx, item, clip in inputs or []:
        if (item.get("layer") or "above_captions") != layer:
            continue
        vlabel = overlay_clip(parts, vlabel, idx, item, clip, fps,
                              f"mg{layer[0]}{j}")
        j += 1
    return vlabel


def demote(item, why):
    """A behind_subject item drawn as an ordinary above-captions graphic.

    The words-behind contract, applied to graphics: losing the depth is a
    disappointment, losing the graphic (or the render) is a broken product."""
    msg = (f"motion '{item.get('id')}' rendered above the picture instead of "
           f"behind the subject: {why}")
    print(f"[render] {msg}", flush=True)
    LAST_WARNINGS.append(msg)
    return dict(item, layer="above_captions", behind=None)


def demote_behind(inputs, why):
    """``inputs`` with every behind_subject item demoted (a render path
    that has no behind-subject stage, e.g. a canvas program)."""
    return [(idx, demote(item, why), clip)
            if item.get("layer") == "behind_subject" else (idx, item, clip)
            for idx, item, clip in inputs or []]


def caption_mute_spans(edl):
    """Program windows of the motion items with an explicit
    mute_captions=true. No caption path mutes them as a whole any more
    (round 6, every heard word reaches the screen once): the caption plan
    (worker/caption_carry.py) hides just the words a graphic shows and mutes
    others only where no band is clear of it, naming them."""
    return [[float(item["start"]), float(item["end"])]
            for item in edl.get("motion") or []
            if item.get("mute_captions") is True]


# ── footprints for caption placement ─────────────────────────────────────
# The caption plan (worker/caption_carry.py) places the captions a graphic
# does not show in a band clear of the box it draws: its stored footprint
# (MotionItem.footprint). add/set_motion_graphic write it — measured where a
# browser runs, ESTIMATED from the template on the browserless agent, MCP
# and shorts lanes. Before the renderer builds its captions it measures,
# once per composition per process, every item that has none, one measured
# at another frame shape, or only an estimate (the render lane has the
# browser), so what burns is placed against the real box.
_FOOTPRINT_CACHE = {}
_FOOTPRINT_CACHE_MAX = 256


def probe_times(span):
    """Item-local seconds the write-time probe (and this fill) samples."""
    return [round(span * f, 3) for f in (0.12, 0.35, 0.6, 0.9)]


def caption_box(report, times):
    """The box captions keep clear of, from one probe report: its settled
    COVER box (motion_engine.COVER_ALPHA — type, plates and the dense core of
    a scrim; keepout.cover_box). A composition with nothing that dense (a
    light leak, a soft glow) draws nothing a caption could collide with:
    None. An older report falls back to its ink, then its visible boxes."""
    import keepout
    box = keepout.cover_box(report, times)
    return [round(float(v), 4) for v in box] if box else None


def fill_footprints(edl, W, H, fps=30.0, index=None, tl=None):
    """Measure the footprint box of every motion item that matters to the
    caption plan and has none, has one measured at another frame shape, or
    has only an estimate (in place; returns ``edl``). Only transcript
    captions use it — mute_captions=true items included: they no longer hide
    a whole window, so the captions they do not show are placed against
    their real box too. Face zones the keep-out stored at this frame shape
    are kept. A probe that cannot run leaves an estimate in place and an
    item without a box (a stale one is dropped): the plan then keeps the old
    behaviour for it (a graphic that says the line is assumed to sit on the
    captions).

    With the program's ``index`` and Timeline ``tl``, every whole lockup is
    first timed to the speech it shows (caption_carry.attach_readings: its
    words land on their spoken onsets; an old stored reading's bridge lines
    are dropped), so it is measured and rendered as it will read; a stored
    box measured with another reading is measured again."""
    import caption_carry
    if index is not None and tl is not None:
        before = {m.get("id"): m.get("reading") for m in edl.get("motion") or []
                  if isinstance(m, dict)}
        caption_carry.attach_readings(edl, index, tl)
        for m in edl.get("motion") or []:
            if isinstance(m, dict) and m.get("reading") != before.get(m.get("id")) \
                    and isinstance(m.get("footprint"), dict) \
                    and not m["footprint"].get("estimated"):
                # the lockup grew or shrank: its box is measured again
                m["footprint"] = dict(m["footprint"], estimated=True)
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return edl
    ar = caption_carry.frame_ar(W, H)
    todo = [m for m in edl.get("motion") or []
            if isinstance(m, dict) and not m.get("_synthetic")
            and not (caption_carry.footprint_box(m, ar) and not caption_carry.estimated(m))
            and not m.get("phase_s")
            and float(m.get("end", 0)) - float(m.get("start", 0)) >= 0.05]
    if not todo:
        return edl
    faces = {}
    for m in todo:
        faces[id(m)] = (caption_carry.footprint_faces(m, ar),
                        (m.get("footprint") or {}).get("geo"))
        if not caption_carry.footprint_fresh(m, ar):
            # measured at another frame shape: no evidence here
            m.pop("footprint", None)
    # The design canvas at the frame's aspect: fractions do not depend on
    # the pixel size, so previews and finals share one measurement.
    w = 1080
    h = max(2, int(round(w * float(H) / max(float(W), 1.0))))

    def store(m, box):
        if box:
            zones, geo = faces.get(id(m)) or ([], None)
            m["footprint"] = caption_carry.make_footprint(box, W, H, zones, geo=geo)
        else:
            m.pop("footprint", None)      # nothing dense: nothing to keep clear of
    jobs, times, keys, pending = [], [], [], []
    for m in todo:
        try:
            job = motion_templates.build_job(m, w, h, fps)
        except Exception as e:  # noqa: BLE001 — that item stays unmeasured
            print(f"[render] caption box for motion '{m.get('id')}' skipped: "
                  f"{str(e)[:120]}", flush=True)
            continue
        span = float(m["end"]) - float(m["start"])
        key = (job.key(), tuple(probe_times(span)))
        if key in _FOOTPRINT_CACHE:
            store(m, _FOOTPRINT_CACHE[key])
            continue
        jobs.append(job)
        times.append(probe_times(span))
        keys.append(key)
        pending.append(m)
    if not jobs:
        return edl
    try:
        reports = motion_engine.probe(jobs, times)
    except Exception as e:  # noqa: BLE001 — fail open: estimates / old behaviour
        print(f"[render] caption boxes unmeasured ({str(e)[:160]})", flush=True)
        return edl
    for m, key, ts, rep in zip(pending, keys, times, reports):
        if rep.get("errors"):
            continue                      # the render reports its own error
        box = caption_box(rep, ts)
        if len(_FOOTPRINT_CACHE) >= _FOOTPRINT_CACHE_MAX:
            _FOOTPRINT_CACHE.clear()
        _FOOTPRINT_CACHE[key] = box
        store(m, box)
    return edl
