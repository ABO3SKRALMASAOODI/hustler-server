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
            jobs.append(motion_templates.build_job(item, W, H, fps, asset_locals,
                                                   plate=plates.get(k)))
            kept.append(item)
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
    """Program windows where a motion item owns the caption area."""
    spans = []
    for item in edl.get("motion") or []:
        mute = item.get("mute_captions")
        if mute is None:
            try:
                mute = bool(motion_templates.spec(item["template"]).get("mutes_captions"))
            except ValueError:
                mute = False
        if mute:
            spans.append([float(item["start"]), float(item["end"])])
    return spans
