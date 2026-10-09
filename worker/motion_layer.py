"""Renderer glue for EDL.motion: render clips, add inputs, composite.

Each MotionItem renders (worker/motion_engine.py) to a transparent clip
cropped to what it draws, then overlays the program on the FINAL clock at
its z-layer: ``below_captions`` composites before the caption burn (dialogue
stays on top), ``above_captions`` after the text-graphics burn (a designed
moment wins). Both sit under the frame-shift bars, the floating plate, the
native watermark and the end card, so branding is never covered.

Failure policy: a motion item that cannot render degrades to "absent" with
a logged reason recorded on ``LAST_WARNINGS`` rather than failing the whole
render — the user still gets the edit, and the write tool already proved the
composition renders when it was added.
"""

import os

import motion_engine
import motion_templates

LAST_WARNINGS = []


def program_items(edl, out_duration):
    out = []
    for item in edl.get("motion") or []:
        s, e = float(item["start"]), min(float(item["end"]), float(out_duration))
        if e - s >= 0.05:
            out.append(dict(item, end=e))
    return out


def prepare_inputs(edl, workdir, W, H, fps, out_duration, args, next_idx,
                   fetch_asset=None, extra_items=None):
    """Render motion clips and append ffmpeg inputs. Returns (inputs, next_idx)
    with inputs = [(input_index, item, RenderedClip)]. ``extra_items`` are
    renderer-synthesized items (the motion caption track)."""
    LAST_WARNINGS.clear()
    items = list(extra_items or []) + program_items(edl, out_duration)
    if not items:
        return [], next_idx
    asset_locals = {}
    jobs, kept = [], []
    for item in items:
        try:
            for _k, key in motion_templates.asset_params(item["template"], item.get("params") or {}).items():
                if key not in asset_locals and fetch_asset is not None:
                    asset_locals[key] = fetch_asset(key)
            jobs.append(motion_templates.build_job(item, W, H, fps, asset_locals))
            kept.append(item)
        except Exception as e:  # noqa: BLE001 — degrade one item, keep the render
            msg = f"motion '{item.get('id')}' skipped: {str(e)[:200]}"
            print(f"[render] {msg}", flush=True)
            LAST_WARNINGS.append(msg)
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
                msg = f"{job.label} skipped: {str(e1)[:200]}"
                print(f"[render] {msg}", flush=True)
                LAST_WARNINGS.append(msg)
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


def append_graph(parts, vlabel, inputs, layer, fps):
    """Composite this layer's clips over [vlabel]; returns the new label."""
    j = 0
    for idx, item, clip in inputs or []:
        if (item.get("layer") or "above_captions") != layer:
            continue
        s, e = float(item["start"]), float(item["end"])
        tag = f"mg{layer[0]}{j}"
        # Hold the clip's last frame for a couple of frames: framesync can
        # quantize a fractional program clock a frame past the clip's EOF.
        parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba,"
                     f"tpad=stop_mode=clone:stop_duration={2.0 / max(fps, 1):.4f},"
                     f"setpts=PTS+{s:.4f}/TB[{tag}c]")
        parts.append(f"[{vlabel}][{tag}c]overlay=x={clip.x}:y={clip.y}:eof_action=pass"
                     f":format=auto:enable='gte(t,{s:.4f})*lt(t,{e:.4f})'[{tag}o]")
        vlabel = f"{tag}o"
        j += 1
    return vlabel


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
