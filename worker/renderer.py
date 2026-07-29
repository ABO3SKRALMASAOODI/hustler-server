"""Frame-accurate EDL renderer.

Always re-encodes (never stream-copies): trim+concat of keep segments with
inserts spliced at their boundaries, captions burned from a generated .ass,
music mixed with gain + speech ducking, voiceover mixed with program-audio
ducking, volume automation via enable='between(t,a,b)'.

Every source — the main video, inserted clips, inserted images — is
normalized to the project's output frame (EDL.frame), fps and audio format
before concat, so mixed-resolution material can never distort.

previews read the 720p PROXY and encode fast at 480p with dense keyframes
(Safari scrubbing accuracy); finals read the ORIGINAL at source resolution.
Every render also emits a 3x3 contact sheet for the agent's self-check.
"""

import hashlib
import json
import os
import shutil
import time
import uuid

import audit
import captions as caplib
import config
import db as dbx
import graphics
import media
import music_library
import sfx_library
import screenframe
import sheets
import storage
import travel
from schemas import (clean_fingerprint, EDLValidationError,
                     is_canvas_program, quad_bbox, speed_pieces, validate_edl)
from timeline import Timeline, merge_spans, transition_junctions

DUCK_DB = -12.0            # music under speech AND program audio under voiceover
MAX_ENABLE_SPANS = 80
AUDIO_NORM = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"

# Color-grade presets (EDL.effects.grade). Applied to all footage after
# concat, BEFORE captions burn — text never gets graded.
GRADE_FILTERS = {
    "vibrant": "eq=saturation=1.35:contrast=1.08",
    "warm": "colorbalance=rs=.08:gs=.02:bs=-.08,eq=saturation=1.12",
    "cool": "colorbalance=rs=-.05:bs=.08,eq=saturation=1.05",
    "bw": "hue=s=0,eq=contrast=1.1",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "cinematic": ("colorbalance=bs=.05:rs=-.03,"
                  "eq=contrast=1.12:saturation=1.12:brightness=-0.02"),
}


def _enable_expr(spans):
    return "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)


# ── The screen takeover (round 55) ──────────────────────────────────────────
# Transparent border, in pixels, padded around the asset before it is warped.
# `perspective` samples outside the source by CLAMPING to the edge pixel, so
# without this the pixels outside the destination quad are the asset's own
# opaque border smeared across the whole frame — the base video disappears.
# Two is enough to give the interpolator something transparent to reach for,
# and small enough that the compensation below is sub-pixel honest.
SCREEN_PAD_PX = 2


def _ease_expr(kind, p):
    """The move's shape as an ffmpeg expression over p (already clipped 0-1).

    'smooth' is smoothstep — zero velocity at both ends, which is what stops a
    push from starting with a visible lurch and stopping with a visible thud.
    """
    if kind == "linear":
        return p
    if kind == "accelerate":
        return f"({p})*({p})"
    return f"({p})*({p})*(3-2*({p}))"


def _screen_lock_ease(lock, tvar, t0, dur, fps):
    """The eased 0-1 progress of a takeover, as an expression over `tvar`.

    The denominator is the window MINUS one frame, not the window: the last
    frame a filter emits inside [t0, t0+dur] sits at t0+dur-1/fps, so dividing
    by the full window means the move stops just short of arriving and the pin
    hands off a pixel or two out of register. Both the camera push and the
    corner pin call this — the same string, so they cannot drift apart even by
    a rounding.
    """
    span = max(dur - 1.0 / max(fps, 1e-6), dur * 0.5, 1e-3)
    p = f"clip(({tvar}-{t0:.3f})/{span:.5f},0,1)" if t0 else \
        f"clip(({tvar})/{span:.5f},0,1)"
    return _ease_expr(lock.get("ease") or "smooth", p)


def screen_lock_geometry(lock):
    """The two numbers a screen takeover is defined by, from its quad alone.

    Returns (cx, cy, zoom_end): where the camera aims, and how far in it goes
    for the quad to exactly fill the frame. ONE function, called by the zoom
    stage AND by the corner pin, so the camera and the content cannot be
    computed from different arithmetic — that is the whole reason the takeover
    is a single EDL item and not a zoom sitting next to an overlay.
    """
    corners = [float(v) for v in lock["corners"]]
    x, y, w, h = quad_bbox(corners)
    cx, cy = x + w / 2.0, y + h / 2.0
    # max, not min: the quad must COVER the frame at the end, so the push is
    # driven by whichever side of it is furthest from filling.
    span = max(w, h, 1e-4)
    z_full = 1.0 / span
    push = min(max(float(lock.get("push", 1.0)), 0.0), 1.0)
    return cx, cy, 1.0 + push * (z_full - 1.0)


def _screen_lock_terms(lock, t, a, b, fps):
    """(strength_term, cx_term, cy_term) for the shared zoompan.

    The takeover's camera push is emitted as one more term of the SAME zoompan
    every other zoom rides, so a takeover and an unrelated zoom compose instead
    of fighting over the geometry filter — and so there is exactly one place
    the frame's crop is decided.
    """
    cx, cy, z_end = screen_lock_geometry(lock)
    e = _screen_lock_ease(lock, t, a, b - a, fps)
    win = f"between({t},{a:.3f},{b:.3f})"
    strength = f"{z_end - 1.0:.5f}*({e})*{win}"
    cxt = (f"{cx - 0.5:.5f}*{win}" if abs(cx - 0.5) > 1e-6 else None)
    cyt = (f"{cy - 0.5:.5f}*{win}" if abs(cy - 0.5) > 1e-6 else None)
    return strength, cxt, cyt


def screen_lock_corner_paths(lock, W, H, fps, dur):
    """The eight per-frame `perspective` expressions that pin the content.

    Geometry, once, so the next reader does not have to re-derive it:

    The base is zoomed about (cx, cy) by z(t). The renderer's targeted zoom
    crops at offset (1-1/z)*cx and shows a window 1/z wide, so a point at frame
    fraction u lands on screen at (u - (1-1/z)*cx)*z. Note what that gives for
    u = cx: the quad's CENTRE is pinned at cx for every z — the screen does not
    drift across the frame as the camera pushes, it only grows. That is the
    property that makes the move read as a dolly rather than a pan.

    At z_end the quad exactly fills the frame in its longer dimension. Whatever
    is left over — the short dimension of a screen that is not the output's
    aspect, and the SKEW of a screen shot at an angle — is closed by adding the
    quad's own end-state error, weighted by g. Writing the correction as
    (frame_corner - lock_corner_at_end) and not as a blend toward the frame is
    what keeps a frontal, output-aspect screen glued to the glass for the whole
    push: its error is zero, so the correction term is identically zero and the
    content never detaches. An angled screen's error is its skew, and g rides
    it out over the tail of the move — the glass flattening into the frame,
    which IS the transition people mean when they say "it opens up".

    g is the ease SQUARED on purpose: the un-skew has to happen late, while the
    screen is already large and the correction is least visible.

    Expressions are in `on/fps`, not `t`: vf_perspective exposes W, H, `in` and
    `on` — there is no `t` variable. The chain forces CFR at the render rate
    first, so on/fps is exactly the local second.
    """
    cx, cy, z_end = screen_lock_geometry(lock)
    corners = [float(v) for v in lock["corners"]]
    e = _screen_lock_ease(lock, f"on/{fps:.3f}", 0.0, dur, fps)
    z = f"(1+{z_end - 1.0:.5f}*({e}))"
    g = f"(({e})*({e}))"
    # The destination is grown by the transparent border's share so the CONTENT
    # (which sits inset by SCREEN_PAD_PX) lands where the quad says. Without
    # this the takeover hands off two pixels small and the cut shows.
    kx = W / max(1.0, W - 2.0 * SCREEN_PAD_PX)
    ky = H / max(1.0, H - 2.0 * SCREEN_PAD_PX)
    out = []
    # Storage order is the filter's order (TL, TR, BL, BR); the frame corner
    # each one has to arrive at follows the same order.
    frame_corners = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    for i, (fx, fy) in enumerate(frame_corners):
        qx, qy = corners[2 * i], corners[2 * i + 1]
        # where this corner sits on screen under the push, and where it ends up
        lock_x = f"(({qx:.5f}-(1-1/{z})*{cx:.5f})*{z})"
        lock_y = f"(({qy:.5f}-(1-1/{z})*{cy:.5f})*{z})"
        end_x = (qx - (1.0 - 1.0 / z_end) * cx) * z_end
        end_y = (qy - (1.0 - 1.0 / z_end) * cy) * z_end
        ex = f"({lock_x}+{g}*{fx - end_x:.5f})"
        ey = f"({lock_y}+{g}*{fy - end_y:.5f})"
        # fractions -> pixels, expanded about the frame centre by the border
        out.append(f"{W}*(0.5+({ex}-0.5)*{kx:.6f})")
        out.append(f"{H}*(0.5+({ey}-0.5)*{ky:.6f})")
    return out


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def _anim_expr(v, tvar):
    """Compile an AnimFloat (a constant or a keyframe list — see schemas)
    to an ffmpeg expression over `tvar` (an expression yielding the
    ELEMENT-LOCAL time in seconds). Easing curves are the same closed forms
    anim_value evaluates python-side, so tools, tests and renders agree.
    Nested if() rather than summed between(): keyframe segments share
    endpoints, and summed windows double-count exactly at the shared
    instant."""
    if not isinstance(v, list):
        return f"{float(v):.4f}"
    kfs = [(k["t"], k["v"], k.get("ease")) if isinstance(k, dict)
           else (k.t, k.v, k.ease) for k in v]
    expr = f"{kfs[-1][1]:.4f}"          # after the last keyframe: hold
    for i in range(len(kfs) - 1, 0, -1):
        t0, v0, _ = kfs[i - 1]
        t1, v1, ease = kfs[i]
        if t1 - t0 <= 1e-9 or ease == "hold":
            seg = f"{v0:.4f}"
        else:
            p = f"(({tvar}-{t0:.3f})/{t1 - t0:.3f})"
            if ease == "in":
                p = f"pow({p},2)"
            elif ease == "out":
                p = f"({p}*(2-{p}))"
            elif ease == "in_out":
                p = f"({p}*{p}*(3-2*{p}))"
            seg = f"({v0:.4f}+{v1 - v0:.4f}*{p})"
        expr = f"if(lt({tvar},{t1:.3f}),{seg},{expr})"
    return f"if(lt({tvar},{kfs[0][0]:.3f}),{kfs[0][1]:.4f},{expr})"


def _atempo_chain(factor):
    """atempo accepts 0.5-2.0 per instance; chain instances for the rest.
    Returns e.g. 'atempo=2.0,atempo=1.5' for 3.0x."""
    steps = []
    f = float(factor)
    while f > 2.0 + 1e-9:
        steps.append(2.0)
        f /= 2.0
    while f < 0.5 - 1e-9:
        steps.append(0.5)
        f /= 0.5
    steps.append(round(f, 4))
    return ",".join(f"atempo={s:g}" for s in steps)


def frame_dims(src_w, src_h, ratio):
    """Output dims for a target aspect ratio, never exceeding the source's
    pixel budget: the output's short side is the source's short side, the
    long side derived from the ratio and capped at the source's long side
    (re-deriving the short side when capped). 1920x1080 at 9:16 -> 1080x1920;
    at 1:1 -> 1080x1080; at 4:5 -> 1080x1350."""
    if not ratio or ratio == "source":
        return _even(src_w), _even(src_h)
    rw, rh = (int(x) for x in ratio.split(":"))
    short_src, long_src = min(src_w, src_h), max(src_w, src_h)
    r_long, r_short = max(rw, rh), min(rw, rh)
    long_out = short_src * r_long / r_short
    short_out = short_src
    if long_out > long_src:
        long_out = long_src
        short_out = long_out * r_short / r_long
    if rh >= rw:                       # portrait or square target
        return _even(short_out), _even(long_out)
    return _even(long_out), _even(short_out)


def _normalize_video(parts, in_label, out_label, W, H, fps, mode, uid,
                     focus=None, seg_dur=None):
    """Append graph parts that bring in_label to exactly WxH @ fps, sar 1.
    mode: crop (center-crop), pad (black bars), pad_blur (blurred backdrop).

    focus (round 36): (fx, fy) fractions of the source frame the crop window
    centers on, or None for the legacy center crop. Only the crop mode uses
    it — pad modes never discard picture. None/center emits the EXACT legacy
    filter string, so every stored EDL (all focus-less) renders
    byte-identically.

    seg_dur (round 56): this block's own PROGRAM length, when the block is
    about to become a `concat` segment. `fps` is what makes that concat legal
    — kept spans, a looped PNG title card and a clip from another file all
    arrive at different rates and concat needs one — but on **ffmpeg 5.1.9**
    (Debian bookworm, which is what this image installs) `fps` after a
    `setpts=PTS-STARTPTS` emits frames PAST the segment's real content, and
    concat then honours that longer duration when placing the next segment.

    Three things kept it hidden for a day of forensics:
      * ffmpeg 8.1.2 renders the IDENTICAL graph to the correct length, so it
        never reproduced on a dev machine.
      * Video alone is correct even on 5.1 — the stretch appears only when
        audio shares the concat, because that is when the padded video is what
        the segment's length is taken from.
      * A preview renders from our own proxy and a final from the customer's
        original, which made it look like a property of their file.

    Measured on the export that exposed it (project 226, 14 kept spans + a 1.5s
    title card): 158.58s for a 150.48s edit on 5.1.9, +0.6s per segment
    compounding, and 150.53s from the same graph on 8.1.2. It failed
    verification three times and the user was told only "the render is the
    wrong length".

    So the block is bounded to the length the EDL says it is. On 5.1 that
    discards the padding; on 8.1 there is nothing to discard and the trim is a
    no-op — which is what makes it safe to apply unconditionally instead of
    sniffing an ffmpeg version into a filter chain. Verified on both builds
    against the real EDL: 150.60s and 150.53s for 150.48s expected.
    """
    bound = ("" if seg_dur is None
             else f"trim=end={float(seg_dur):.3f},setpts=PTS-STARTPTS,")
    tail = f"fps={fps:.3f},{bound}setsar=1,format=yuv420p"
    if mode == "pad":
        parts.append(
            f"[{in_label}]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,{tail}[{out_label}]")
    elif mode == "pad_blur":
        parts.append(f"[{in_label}]split[pbA{uid}][pbB{uid}]")
        parts.append(f"[pbA{uid}]scale={W}:{H}:"
                     f"force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"boxblur=20[pbBG{uid}]")
        parts.append(f"[pbB{uid}]scale={W}:{H}:"
                     f"force_original_aspect_ratio=decrease[pbFG{uid}]")
        parts.append(f"[pbBG{uid}][pbFG{uid}]overlay=(W-w)/2:(H-h)/2,"
                     f"{tail}[{out_label}]")
    else:                              # crop
        fx = focus[0] if focus else None
        fy = focus[1] if focus else None
        if (fx is not None and abs(float(fx) - 0.5) > 1e-6) or \
                (fy is not None and abs(float(fy) - 0.5) > 1e-6):
            # Fractions survive the uniform scale, so the focus point maps
            # straight onto the SCALED frame; clip() keeps the window inside
            # the picture when the subject sits near an edge.
            xe = (f"x='clip(iw*{float(fx if fx is not None else 0.5):.4f}"
                  f"-ow/2,0,iw-ow)'")
            ye = (f"y='clip(ih*{float(fy if fy is not None else 0.5):.4f}"
                  f"-oh/2,0,ih-oh)'")
            parts.append(
                f"[{in_label}]scale={W}:{H}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:{xe}:{ye},{tail}[{out_label}]")
        else:
            parts.append(
                f"[{in_label}]scale={W}:{H}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={W}:{H},{tail}[{out_label}]")


def _region_parts(parts, in_label, out_label, regions, sw, sh,
                  seg_start, seg_dur, uid):
    """Censor-region chain for ONE source segment, in SOURCE-frame pixels
    (the coordinate space the agent measures with look_at). Windowed regions
    are mapped from program time to segment-local time; regions whose window
    misses this segment entirely are skipped. Always ends on out_label."""
    todo = []
    for rg in regions:
        if rg.get("start") is not None and rg.get("end") is not None:
            a = max(0.0, float(rg["start"]) - seg_start)
            b = min(seg_dur, float(rg["end"]) - seg_start)
            if b - a < 0.02:
                continue
            win = None if (a <= 0.01 and b >= seg_dur - 0.01) else (a, b)
        else:
            win = None
        todo.append((rg, win))
    if not todo:
        parts.append(f"[{in_label}]null[{out_label}]")
        return
    cur = in_label
    for k, (rg, win) in enumerate(todo):
        rx = min(int(round(float(rg["x"]) * sw)), sw - 4)
        ry = min(int(round(float(rg["y"]) * sh)), sh - 4)
        rw = min(max(4, int(round(float(rg["w"]) * sw))), sw - rx)
        rh = min(max(4, int(round(float(rg["h"]) * sh))), sh - ry)
        enable = (f":enable='between(t,{win[0]:.2f},{win[1]:.2f})'"
                  if win else "")
        last = out_label if k == len(todo) - 1 else f"rgc{uid}_{k}"
        if rg.get("mode") == "black":
            parts.append(f"[{cur}]drawbox=x={rx}:y={ry}:w={rw}:h={rh}:"
                         f"color=black:t=fill{enable}[{last}]")
        else:
            if rg.get("mode") == "pixelate":
                pf = max(2, min(rw, rh) // 8)
                obscure = (f"scale={max(2, rw // pf)}:{max(2, rh // pf)},"
                           f"scale={rw}:{rh}:flags=neighbor")
            else:                       # blur
                # gblur, not boxblur: boxblur's radius must stay under the
                # CHROMA plane's min(w,h)/2 (half the pixel dims on
                # yuv420p), which small regions violate; gblur has no such
                # constraint
                sigma = max(3, min(min(rw, rh) // 6, 30))
                obscure = f"gblur=sigma={sigma}:steps=2"
            parts.append(f"[{cur}]split[rgA{uid}_{k}][rgB{uid}_{k}]")
            parts.append(f"[rgA{uid}_{k}]crop={rw}:{rh}:{rx}:{ry},"
                         f"{obscure}[rgF{uid}_{k}]")
            parts.append(f"[rgB{uid}_{k}][rgF{uid}_{k}]overlay={rx}:{ry}"
                         f"{enable}[{last}]")
        cur = last


def _speech_spans_out(index, tl):
    spans = []
    for sent in index.get("sentences", []):
        spans.extend(tl.span_to_out(sent["t0"], sent["t1"]))
    gap = 0.3
    merged = merge_spans(spans, gap)
    while len(merged) > MAX_ENABLE_SPANS:
        gap *= 2
        merged = merge_spans(merged, gap)
    return merged


ENDCARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "brand", "endcard.png")
# The mark itself: the same white robot the end card and the site navbar use,
# so the corner watermark and the brand are one character.
ROBOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "brand", "robot.png")


def _screen_frame_input(edl, workdir, W, H, fps, out_dur, extra_inputs,
                        next_idx):
    """Build the floating-window plate and append it as a looped still input.

    Returns (input_index, (pw, ph, ox, oy)) or (None, None). A plate that
    cannot be drawn degrades to NO framing rather than failing the render —
    the same contract endcard_path() documents: the user loses a cosmetic
    treatment, not their export.
    """
    sf = ((edl.get("effects") or {}).get("screen_frame")) or None
    if not sf or not W or not H:
        return None, None
    path = screenframe.plate_path(workdir, W, H, sf)
    try:
        if not os.path.exists(path):
            screenframe.build_plate(path, W, H, sf)
        box = screenframe.picture_box(W, H, float(sf.get("inset", 0.08)))
    except Exception as e:
        print(f"[render] screen_frame plate could not be built "
              f"({str(e)[:160]}) — rendering without it", flush=True)
        return None, None
    extra_inputs += ["-loop", "1", "-t", f"{out_dur + 5.0:.3f}",
                     "-r", f"{fps:.3f}", "-i", path]
    return next_idx, box


def robot_path():
    """The bundled robot, or None. Same contract as endcard_path(): a missing
    brand asset degrades the render rather than failing it."""
    return ROBOT_PATH if os.path.exists(ROBOT_PATH) else None


def endcard_path():
    """The bundled end-card image, or None if this image does not carry one.

    Returning None (rather than raising) is deliberate. A missing brand asset
    means a broken build, and the two ways to react are: ship exports without
    branding, or ship no exports at all. Failing every export takes the product
    down over a cosmetic asset, and main.py's failure notes would tell the user
    to "press Download to try again" — advice that could not possibly help,
    which that module's own rule forbids. So the render proceeds and the
    caller logs loudly instead; the miss is visible in logs and in the job
    result, not buried in a customer's confusing failure.
    """
    return ENDCARD_PATH if os.path.exists(ENDCARD_PATH) else None


def clean_source_key(edl_json, variant, src_sha=None):
    """Round 39 — the REPAINTED source this EDL renders from, or None.

    When the agent erased burned-in text or an object, the pixels were fixed in
    a copy of the source and the EDL points at it: full-res for the final, the
    repainted 540p proxy for the preview, so the preview the user approves is
    the one that ships. A preview with no repainted proxy falls back to the
    full-res clean rather than to the ORIGINAL — slower, but showing the user
    the text they just watched disappear would be a lie the reply would not
    catch. Nothing else about rendering changes: the cleaned file has the same
    duration, rate and audio, so every EDL and index timestamp still lands on
    the same frame.
    """
    clean = (edl_json or {}).get("source_clean") or {}
    key = clean.get("asset_key")
    if not key:
        return None
    # An EDL outlives a video REPLACEMENT (uploading a new file keeps the
    # project's edit), so a repaint of the old upload would otherwise render
    # the old footage entirely — the user swaps their video and watches the
    # previous one come back. The fingerprint ties the cleaned file to the
    # source sha it was made from: when it no longer matches, this project's
    # current video was simply never cleaned, so render it as it is.
    if src_sha and clean.get("fp") and clean["fp"] != clean_fingerprint(
            src_sha, clean.get("regions") or []):
        print(f"[render] ignoring cleaned source {key}: it is a repaint of a "
              "different upload (the video was replaced)", flush=True)
        return None
    if variant == "preview":
        return clean.get("proxy_key") or key
    return key


def outro_seconds(preview):
    """How much time the end card adds to a render of this variant.

    The single source of truth for the outro's length. Everything that must
    agree — the duration check, the progress estimate, the result-sheet
    sampling window, the filtergraph — reads it from here, because these have
    to move together or the render fails verification.
    """
    if preview and not config.OUTRO_ON_PREVIEW:
        return 0.0
    return config.OUTRO_DURATION_S if endcard_path() else 0.0


def outro_current(meta, variant):
    """Does this cached render carry the end card this variant should have?

    An ABSENT stamp means 0 — "no card baked in" — not "unknown". That
    distinction is the whole grandfathering rule: a pre-card FINAL (wants 1)
    busts and re-encodes, while a pre-card PREVIEW (wants 0) still matches and
    is served. Treating absent as unknown would re-render every cached preview
    on the platform for nothing, which on a ~1 vCPU box is exactly how real
    customers got starved before.

    A module-level function, not an inline comparison, so the rule is
    testable — the same reason sfx_source/music_source exist.
    """
    want = config.OUTRO_VERSION if outro_seconds(variant == "preview") else 0
    return ((meta or {}).get("outro_v") or 0) == want


def edl_has_shaped_text(edl):
    """Does this EDL burn any text in a script that needs complex shaping?"""
    return any(graphics.needs_shaping(t.get("text") or "")
               for t in ((edl or {}).get("texts") or []))


def shaping_current(meta, edl):
    """Does this cached render predate the complex-script text fix?

    Only EDLs that actually contain a shaped script are ever busted. For
    everything else this is unconditionally True, so Latin renders keep their
    cache — same reasoning as outro_current's grandfathering, and the same
    reason it is a named function rather than an inline comparison.
    """
    if not edl_has_shaped_text(edl):
        return True
    return ((meta or {}).get("gfx_shape_v") or 0) == config.GFX_SHAPING_VERSION


def transitions_current(meta, edl):
    """Does this cached render predate scene-scoped transitions?

    Renders are cached by (project, variant, EDL VERSION), not by content, so
    a render-pipeline change is invisible to the cache — the same problem the
    end card had. Every render made before round 48 put a junction effect on
    EVERY cut, which on a silence-cut talking head is a full-screen effect
    every couple of seconds through one continuous shot. A real customer's
    preview is sitting in that cache right now; leaving it there means she
    keeps being served the broken video no matter what she does next.

    Only EDLs that actually carry a transition are ever busted — everything
    else keeps its cache, same grandfathering discipline as shaping_current
    and outro_current, and the same reason this is a named function rather
    than an inline comparison.
    """
    if not ((edl or {}).get("effects") or {}).get("transition"):
        return True
    return ((meta or {}).get("trans_v") or 0) == config.TRANSITION_VERSION


def watermark_font_path():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fonts", "PlusJakartaSans-ExtraBold.ttf")
    return p if os.path.exists(p) else None


def wants_watermark(variant, is_paid, settings=None):
    """Should THIS render carry the free-tier mark?

    Finals only (a preview is a working artefact, not the thing the user
    keeps), and only when the user has no paid plan. Also requires both the
    robot and the wordmark font to be present: half a watermark — a robot
    with no text, or text in a fallback font that is not the site's — would
    be worse than none, so a missing asset means no mark at all rather than
    a degraded one.

    `settings` are the admin panel's live toggles (db.video_settings):
      enabled — master switch, off means nobody is marked.
      force   — mark EVERY final, paid included. This exists because the
                owner of the product is on a paid plan and therefore cannot
                see the feature on their own exports; without it the only
                way to check the mark is to register a throwaway account.
                It overrides the plan check, NOT the master switch.
    When settings is None the config default stands, so a worker that cannot
    reach the table still behaves sensibly.
    """
    s = settings or {"enabled": config.WATERMARK_ENABLED, "force": False}
    if variant != "final" or not s.get("enabled"):
        return False
    if is_paid and not s.get("force"):
        return False
    return bool(robot_path() and watermark_font_path())


def watermark_version(variant, is_paid, settings=None):
    """The `wm_v` stamp for a render: the version burned in, or 0 for none.
    0 is a real answer ("this file carries no mark"), never "unknown"."""
    return (config.WATERMARK_VERSION
            if wants_watermark(variant, is_paid, settings) else 0)


def watermark_current(meta, variant, is_paid, settings=None):
    """Does this cached render carry the mark THIS user should have now?

    The upgrade path is the whole point. A free user exports (wm_v=1), pays,
    and downloads again: without this the cached marked file is served
    forever and they are still looking at the watermark they just paid to
    remove. It busts downward too — a lapsed subscriber's clean export
    re-encodes with the mark.

    An ABSENT stamp means 0, matching outro_current: renders predating the
    feature carry no mark, which is correct for a paid user and stale for a
    free one, so only free users re-encode.
    """
    return ((meta or {}).get("wm_v") or 0) == watermark_version(
        variant, is_paid, settings)


def watermark_geometry(W, H):
    """Pixel geometry of the mark for an output frame: the robot's box, and
    where the text sits relative to it. One function so the ffmpeg overlay
    and the ASS text agree on where the robot ends and the words begin —
    computing them separately is how the two drift into overlapping."""
    rh = max(24, _even(H * config.WATERMARK_ROBOT_H_FRAC))
    rw = max(16, _even(rh * config.WATERMARK_ROBOT_ASPECT))
    margin = max(10, int(round(min(W, H) * config.WATERMARK_MARGIN_FRAC)))
    fs = max(9, int(round(H * config.WATERMARK_TEXT_H_FRAC)))
    gap = max(6, int(round(fs * 0.72)))
    slide = max(4, int(round(W * config.WATERMARK_SLIDE_FRAC)))
    return {"rw": rw, "rh": rh, "margin": margin, "fontsize": fs,
            # tucked against the robot, then slid clear
            "x_in": margin + rw + gap,
            "x_out": margin + rw + gap + slide,
            "y": margin + max(0, (rh - fs) // 2)}


def build_watermark_ass(path, out_duration_s, W, H):
    g = watermark_geometry(W, H)
    return graphics.build_watermark_ass(
        path, out_duration_s, (W, H), config.WATERMARK_TEXT,
        g["x_in"], g["x_out"], g["y"], g["fontsize"],
        config.WATERMARK_FONT_NAME, config.WATERMARK_PERIOD_S,
        config.WATERMARK_SHOW_S, config.WATERMARK_FADE_S)


def _watermark_parts(vlabel, out_label, robot_idx, wm_ass_path, W, H):
    """Filtergraph for the corner mark: the robot pinned top-left, then the
    wordmark burned over it from its own .ass layer.

    Applied to the PROGRAM stream before the end card is concatenated, so the
    mark never lands on the card (which is already branded).

    The robot is an overlay and the text is libass — not one mechanism for
    both — because a PNG needs no shaping and a string does. libass is the
    pipeline's proven text burner (captions and graphics both ride it) and
    gives \\move/\\fad for free; drawtext would need ffmpeg built with
    libfreetype, which is not guaranteed.
    """
    g = watermark_geometry(W, H)
    parts = [f"[{robot_idx}:v]scale={g['rw']}:{g['rh']}[wmbot]"]
    tail = out_label if not wm_ass_path else "wmv"
    # shortest=1 is load-bearing: the robot is a LOOPED still, so with
    # overlay's default shortest=0 ("do not end when the shortest input
    # ends") the filter keeps emitting frames forever after the programme
    # finishes and ffmpeg encodes into the void. That is not theoretical --
    # it hung a real export at 89% for 8 minutes with a healthy heartbeat,
    # which looks exactly like "slow" and never ends. The main stream drives
    # the length; the input is ALSO bounded with -t as a second line of
    # defence, generously, so it can never be the shorter one and truncate.
    parts.append(f"[{vlabel}][wmbot]overlay={g['margin']}:{g['margin']}:"
                 f"format=auto:shortest=1[{tail}]")
    if wm_ass_path:
        parts.append(f"[wmv]subtitles=filename='{wm_ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[{out_label}]")
    return parts


def sfx_source(key, fetch):
    """Resolve an sfx item's storage_key to a local file.

    Module-level (not a closure) for the same reason as music_source: this
    branch is exactly the wiring a filtergraph test cannot see. The music
    version of this function was once imported and never called, so every
    library reference went to S3 and failed EVERY render — after the tool had
    reported success and minted a version.
    """
    if sfx_library.is_library_ref(key):
        local = sfx_library.local_path(key)
        if not local or not os.path.exists(local):
            raise media.MediaError(
                f"Sound effect '{key}' is no longer in the built-in pack, so "
                "this render cannot be produced. Remove it and pick another.")
        return local
    return fetch(key)


def music_source(key, fetch):
    """Local path for a music item's audio.

    Built-in library tracks ship inside the image, so there is nothing to
    download — and handing "library:<slug>" to object storage as a key would
    fail every render that used one. Everything else is a real bucket object.
    Module-level (not a closure) so this branch is unit-testable: the wiring
    is exactly what a filtergraph test cannot see."""
    if music_library.is_library_ref(key):
        local = music_library.local_path(key)
        if not local or not os.path.exists(local):
            # Only reachable if a track was withdrawn from the catalog while
            # an older EDL still referenced it. Fail loudly — the alternative
            # is rendering with the music silently missing, after the agent
            # already told the user it added some.
            raise media.MediaError(
                f"Music track '{key}' is no longer in the built-in library, "
                f"so this edit cannot be rendered as saved. Swap it for "
                f"another track and re-render.")
        return local
    return fetch(key)


def build_filtergraph(edl, src_dur, has_audio, tl, ass_path,
                      music_inputs, index, preview,
                      W=None, H=None, fps=30.0, frame_mode=None,
                      insert_inputs=None, vo_inputs=None, silence_idx=None,
                      src_w=None, src_h=None, src_pad=0.0,
                      sfx_inputs=None, outro_s=0.0, card_idx=None,
                      src_sar=1.0, src_fps=None,
                      overlay_inputs=None, gfx_ass_path=None,
                      frame_focus=None, robot_idx=None, wm_ass_path=None,
                      plate_idx=None, plate_box=None):
    """Input layout: [0] main source video; anullsrc at silence_idx when
    needed (no main audio, image inserts, or silent clip inserts); then one
    input per music item, insert item and voiceover item in EDL order.

    insert_inputs: [(input_idx, item, has_audio)] aligned with the sorted
    EDL inserts (same order as tl.insert_positions()).
    vo_inputs: [(input_idx, item, vo_duration_s)].
    src_pad: seconds of the source whose picture track ran out early (a phone
    screen recording stops writing frames while the screen is static). The last
    frame is held across them, matching what a player shows, what the proxy
    holds and therefore what the user approved in the preview — trimming a keep
    span that lands in there would otherwise yield no picture at all.
    """
    keep = [(max(0.0, s), min(e, src_dur)) for s, e in edl["keep"]]
    keep = [(s, e) for s, e in keep if e - s > 0.01]
    # A canvas program (image/clip-only, no main video) has no keep segments and
    # no input [0]: its program is the inserts alone, concatenated on the canvas.
    canvas_prog = not (edl.get("keep") or []) and bool(edl.get("canvas"))
    if not keep and not canvas_prog:
        raise EDLValidationError("All keep segments fall outside the video.")
    insert_inputs = insert_inputs or []
    vo_inputs = vo_inputs or []
    n = len(keep)
    parts = []

    if n > 0:
        asrc = "0:a" if has_audio else f"{silence_idx}:a"
        # Source-time volume automation runs before trimming, so between(t,a,b)
        # windows are in source seconds — exactly what the agent wrote.
        vol_filters = "".join(
            f",volume={v['gain_db']}dB:enable='between(t,{v['start']:.2f},{v['end']:.2f})'"
            for v in edl.get("volume", []))
        parts.append(f"[{asrc}]anull{vol_filters}[asrc]")

    # anullsrc slices for silent blocks (image inserts / clips without audio)
    n_silent_blocks = sum(1 for _idx, _it, hs in insert_inputs if not hs)
    if n_silent_blocks:
        if n_silent_blocks == 1:
            parts.append(f"[{silence_idx}:a]anull[sil0]")
        else:
            parts.append(f"[{silence_idx}:a]asplit={n_silent_blocks}"
                         + "".join(f"[sil{i}]" for i in range(n_silent_blocks)))

    # A plain single-source cut needs no per-segment normalization (the old,
    # cheap graph). The moment a frame is set, foreign material is spliced
    # in, or a zoom needs exact CFR WxH frames, EVERY block must land on
    # identical dims/fps/audio before concat. Round 35 widens the list:
    # speed pieces need CFR so their concat is seamless; overlays compute
    # their geometry from exact WxH; the whip/zoom_punch/glitch junctions
    # run per-block zoompan/overlay math that assumes CFR WxH; shake is a
    # zoompan.
    fx = edl.get("effects") or {}
    zooms = fx.get("zooms") or []
    regions = fx.get("regions") or []
    speed = edl.get("speed") or []
    stylize = fx.get("stylize") or []
    grade_custom = fx.get("grade_custom") or {}
    overlay_inputs = overlay_inputs or []
    # A screen takeover is an overlay that carries a ScreenLock. It leaves the
    # ordinary PIP loop entirely: it needs the zoom stage to have already run
    # (its corner path is expressed in POST-push screen space) and it must not
    # pick up the PIP loop's static scale/position, which the pin overrides.
    takeovers = [(i, it) for i, it in overlay_inputs if it.get("screen")]
    overlay_inputs = [(i, it) for i, it in overlay_inputs
                      if not it.get("screen")]
    master = edl.get("master") or {}
    transition = fx.get("transition") or None
    tstyle = (transition or {}).get("style")
    shifts = fx.get("frame_shifts") or []
    screen_frame = fx.get("screen_frame") or None
    # An aspect shift pushes the picture in via the same zoompan the zooms use,
    # and the floating frame scales the finished picture to an exact WxH box —
    # both need the CFR WxH guarantee every other geometry effect needs.
    do_norm = (bool(insert_inputs) or frame_mode is not None or bool(zooms)
               or bool(speed) or bool(overlay_inputs) or bool(takeovers)
               or tstyle in ("whip_left", "whip_right", "zoom_punch")
               or bool(shifts) or screen_frame is not None
               or any(s.get("kind") == "shake" for s in stylize))
    mode = frame_mode or "crop"

    # Censor regions are burned into each SOURCE segment BEFORE any
    # reframe/normalization: their fractions are of the SOURCE frame
    # (exactly what look_at showed the agent), a later crop/pad moves the
    # censored footage as one, and inserted material is never censored.
    # Pieces per segment: with no speed spans every segment is one piece at
    # factor 1 and the classic emission below runs untouched. seg_out_len is
    # the segment's PROGRAM length (speed-remapped) — the number every block
    # duration and program-time accumulation must use.
    seg_pcs = [speed_pieces(s, e, speed) for s, e in keep]
    seg_out_len = [sum((pe - ps) / f for ps, pe, f in pcs)
                   for pcs in seg_pcs]

    sw = sh = None
    seg_prog = []
    if regions:
        sw, sh = int(src_w or W), int(src_h or H)
        # program-time start of every keep segment (inserts included), for
        # mapping windowed regions into segment-local time — mirrors the
        # block-order loop below. Uses the SPED segment lengths: a windowed
        # region's program times only line up when the accumulation matches
        # what the viewer's clock does.
        _at = [tl.ins[j][0] for j in range(len(insert_inputs))]
        _pre = _prog = 0.0
        _j = 0
        for i, (s, e) in enumerate(keep):
            while _j < len(_at) and _at[_j] <= _pre + 1e-6:
                _prog += float(insert_inputs[_j][1]["duration_s"])
                _j += 1
            seg_prog.append(_prog)
            _pre += seg_out_len[i]
            _prog += seg_out_len[i]

    def _seg_video(i, in_label, s, e):
        vlab = f"segv{i}" if do_norm else f"v_seg{i}"
        if regions:
            parts.append(f"[{in_label}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[segraw{i}]")
            _region_parts(parts, f"segraw{i}", vlab, regions, sw, sh,
                          seg_prog[i], e - s, f"s{i}")
        else:
            parts.append(f"[{in_label}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[{vlab}]")

    def _seg_pieces_video_audio(i, v_in, a_in, s, e):
        """Speed path: one keep segment -> constant-rate pieces, each
        trimmed, censored (windows mapped into the piece's own pre-speed
        clock), retimed with setpts/atempo, then concatenated back into
        [segv{i}]/[a_seg{i}]. Only reached when the EDL carries speed spans
        (do_norm is forced on, so segv{i} is normalized right after)."""
        pcs = seg_pcs[i]
        k = len(pcs)
        if k > 1:
            parts.append(f"[{v_in}]split={k}"
                         + "".join(f"[pv{i}_{j}]" for j in range(k)))
            parts.append(f"[{a_in}]asplit={k}"
                         + "".join(f"[pa{i}_{j}]" for j in range(k)))
        else:
            parts.append(f"[{v_in}]null[pv{i}_0]")
            parts.append(f"[{a_in}]anull[pa{i}_0]")
        p_acc = 0.0        # sped seconds consumed within this segment so far
        for j, (ps, pe, f) in enumerate(pcs):
            spts = "" if abs(f - 1.0) < 1e-9 else f"/{f:.4f}"
            if regions:
                # Region windows arrive in PROGRAM time; inside a sped piece
                # the local pre-speed clock runs `f` times program speed.
                # The piece's program start accumulates from seg_prog — the
                # same insert-aware walk the non-speed path uses — NEVER via
                # tl.src_to_out(ps): at a contiguous keep boundary (the
                # mid-take-insert split shape) src_to_out first-matches the
                # EARLIER segment and returns a time missing the insert's
                # duration, landing the censor window on the wrong footage.
                p0 = seg_prog[i] + p_acc
                p1 = p0 + (pe - ps) / f
                local_rgs = []
                for rg in regions:
                    if rg.get("start") is not None:
                        a = max(float(rg["start"]), p0)
                        b = min(float(rg["end"]), p1)
                        if b - a < 0.02:
                            continue
                        local_rgs.append(dict(rg, start=(a - p0) * f,
                                              end=(b - p0) * f))
                    else:
                        local_rgs.append(dict(rg, start=None, end=None))
                parts.append(f"[pv{i}_{j}]trim=start={ps:.3f}:end={pe:.3f},"
                             f"setpts=PTS-STARTPTS[pvr{i}_{j}]")
                _region_parts(parts, f"pvr{i}_{j}", f"pvt{i}_{j}", local_rgs,
                              sw, sh, 0.0, pe - ps, f"sp{i}_{j}")
                parts.append(f"[pvt{i}_{j}]setpts=PTS{spts}[pvz{i}_{j}]")
            else:
                parts.append(f"[pv{i}_{j}]trim=start={ps:.3f}:end={pe:.3f},"
                             f"setpts=(PTS-STARTPTS){spts}[pvz{i}_{j}]")
            tempo = "" if abs(f - 1.0) < 1e-9 else f",{_atempo_chain(f)}"
            parts.append(f"[pa{i}_{j}]atrim=start={ps:.3f}:end={pe:.3f},"
                         f"asetpts=PTS-STARTPTS{tempo},{AUDIO_NORM}"
                         f"[paz{i}_{j}]")
            p_acc += (pe - ps) / f
        if k == 1:
            parts.append(f"[pvz{i}_0]null[segv{i}]")
            parts.append(f"[paz{i}_0]anull[a_seg{i}]")
        else:
            pairs = "".join(f"[pvz{i}_{j}][paz{i}_{j}]" for j in range(k))
            parts.append(f"{pairs}concat=n={k}:v=1:a=1[segv{i}][a_seg{i}]")

    # main segments: trim (+ censor regions), then (when needed) normalize
    # to the output frame. Skipped entirely for a canvas program (no [0]).
    if n >= 1:
        vsrc = "0:v"
        if src_pad > 0:
            parts.append(f"[0:v]tpad=stop_mode=clone:"
                         f"stop_duration={src_pad:.3f}[vpad]")
            vsrc = "vpad"
    if speed and n >= 1:
        # Speed path: every segment needs its own video AND audio tap.
        if n == 1:
            parts.append(f"[{vsrc}]null[vin0]")
            parts.append("[asrc]anull[ain0]")
        else:
            parts.append(f"[{vsrc}]split=" + str(n)
                         + "".join(f"[vin{i}]" for i in range(n)))
            parts.append("[asrc]asplit=" + str(n)
                         + "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            _seg_pieces_video_audio(i, f"vin{i}", f"ain{i}", s, e)
    elif n == 1:
        _seg_video(0, vsrc, keep[0][0], keep[0][1])
        parts.append(f"[asrc]atrim=start={keep[0][0]:.3f}:end={keep[0][1]:.3f},"
                     f"asetpts=PTS-STARTPTS"
                     + (f",{AUDIO_NORM}" if do_norm else "") + "[a_seg0]")
    elif n > 1:
        parts.append(f"[{vsrc}]split=" + str(n)
                     + "".join(f"[vin{i}]" for i in range(n)))
        parts.append("[asrc]asplit=" + str(n)
                     + "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            _seg_video(i, f"vin{i}", s, e)
            parts.append(f"[ain{i}]atrim=start={s:.3f}:end={e:.3f},"
                         f"asetpts=PTS-STARTPTS"
                         + (f",{AUDIO_NORM}" if do_norm else "")
                         + f"[a_seg{i}]")
    if do_norm:
        for i in range(n):
            # frame_focus reaches ONLY the main footage: the focus point was
            # measured on the source video, so inserts (below) keep the
            # center crop.
            _normalize_video(parts, f"segv{i}", f"v_seg{i}", W, H, fps,
                             mode, f"s{i}", focus=frame_focus,
                             seg_dur=seg_out_len[i])

    # insert blocks: trim to their window (source_start_s picks where in
    # the clip the window starts), normalize like everything else
    sil_i = 0
    for j, (idx, item, ins_audio) in enumerate(insert_inputs):
        dur = float(item["duration_s"])
        off = float(item.get("source_start_s") or 0.0) \
            if item["kind"] != "image" else 0.0
        parts.append(f"[{idx}:v]trim=start={off:.3f}:end={off + dur:.3f},"
                     f"setpts=PTS-STARTPTS[insv{j}]")
        # Ken Burns motion on image inserts: a per-block zoompan that
        # drifts across the still instead of freezing it.
        motion = item.get("motion") if item["kind"] == "image" else None
        norm_out = f"v_insn{j}" if motion else f"v_ins{j}"
        _normalize_video(parts, f"insv{j}", norm_out, W, H, fps,
                         mode, f"i{j}", seg_dur=dur)
        if motion:
            nframes = max(1, int(round(dur * fps)))
            prog = f"(on/{nframes})"
            cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            if motion == "zoom_in":
                z, x, y = f"1+0.25*{prog}", cx, cy
            elif motion == "zoom_out":
                z, x, y = f"1.25-0.25*{prog}", cx, cy
            elif motion == "pan_left":
                z, x, y = "1.15", f"(iw-iw/zoom)*(1-{prog})", cy
            else:                       # pan_right
                z, x, y = "1.15", f"(iw-iw/zoom)*{prog}", cy
            parts.append(f"[{norm_out}]zoompan=z='{z}':x='{x}':y='{y}'"
                         f":d=1:s={W}x{H}:fps={fps:.3f}[v_ins{j}]")
        if ins_audio:
            parts.append(f"[{idx}:a]atrim=start={off:.3f}:end={off + dur:.3f},"
                         f"asetpts=PTS-STARTPTS,{AUDIO_NORM},"
                         f"apad=whole_dur={dur:.3f}[a_ins{j}]")
        else:
            parts.append(f"[sil{sil_i}]atrim=start=0:end={dur:.3f},"
                         f"asetpts=PTS-STARTPTS,{AUDIO_NORM}[a_ins{j}]")
            sil_i += 1

    # program order: inserts splice at their keep boundary, before the
    # segment that starts there (mirrors Timeline)
    blocks, ins_j, pre = [], 0, 0.0
    at_list = [tl.ins[j][0] for j in range(len(insert_inputs))]
    for i, (s, e) in enumerate(keep):
        while ins_j < len(insert_inputs) and at_list[ins_j] <= pre + 1e-6:
            blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}",
                           float(insert_inputs[ins_j][1]["duration_s"])))
            ins_j += 1
        # seg_out_len, not e - s: a sped segment's block duration is its
        # REMAPPED length (identical to e - s when no speed spans exist).
        blocks.append((f"v_seg{i}", f"a_seg{i}", seg_out_len[i]))
        pre += seg_out_len[i]
    while ins_j < len(insert_inputs):
        blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}",
                       float(insert_inputs[ins_j][1]["duration_s"])))
        ins_j += 1

    # Transitions: a junction effect at every cut/insert boundary, chosen
    # from TRANSITION_STYLES. Every style is duration-preserving by
    # construction — each block animates within its own footage (video only;
    # audio concat is untouched), so no timeline math anywhere changes.
    # Styles that manufacture geometry (whip/zoom_punch) forced do_norm
    # above, so W/H/fps here are the real per-block dimensions.
    trans_post = None    # (style, tdur) applied ONCE after concat, not per block
    # WHICH junctions get one. Not all of them, by default — after cut_silences
    # nearly every junction is a jump cut inside one continuous shot, and a
    # full-screen effect every couple of seconds through footage that never
    # changed scene is what a real user shipped and called broken. The set is
    # resolved by timeline.transition_junctions so this and set_transitions'
    # own count can never disagree; scope 'every_cut' returns all of them.
    junctions = (transition_junctions(edl, index, n_blocks=len(blocks))
                 if transition and len(blocks) > 1 else set())
    if transition and len(blocks) > 1 and not junctions:
        transition = None       # nothing qualified — emit clean hard cuts
    if transition and len(blocks) > 1:
        tdur = float(transition.get("duration_s") or 0.3)
        style = transition.get("style") or "dip_black"
        nb = len(blocks)
        if style in ("whip_left", "whip_right", "zoom_punch"):
            # Geometry-manufacturing styles run as ONE post-concat instance
            # (below): per-block emission put a full-resolution overlay/
            # color/zoompan chain in the graph for EVERY block, so graph
            # size and filter frame queues scaled with the cut count on the
            # OOM-prone 1-vCPU worker. dip/flash/glitch stay per block —
            # fade/eq/rgbashift are cheap and enable-gated.
            trans_post = (style, tdur)
        else:
            faded = []
            for k, (vlab, alab, bd) in enumerate(blocks):
                td = min(tdur, max(0.0, bd / 2 - 0.05))
                # `first`/`last` have always meant "no incoming edge" / "no
                # outgoing edge" — they were just spelled as the ends of the
                # timeline because every junction qualified. Now a junction
                # only exists where transition_junctions says so, and the two
                # ends fall out for free (junction -1 and junction nb-1 are
                # never in the set).
                first = (k - 1) not in junctions
                last = k not in junctions
                if td < 0.05 or (first and last):
                    faded.append((vlab, alab, bd))
                    continue
                out_lab = f"vtr{k}"
                # edge windows this block participates in (an interior block
                # has both: an incoming edge at t=0, an outgoing edge at bd)
                spans = []
                if not first:
                    spans.append((0.0, td))
                if not last:
                    spans.append((max(0.0, bd - td), bd))
                en = "+".join(f"between(t,{a:.3f},{b:.3f})"
                              for a, b in spans)
                if style in ("dip_black", "dip_white"):
                    tcolor = "white" if style == "dip_white" else "black"
                    chain = []
                    if not first:
                        chain.append(f"fade=t=in:st=0:d={td:.2f}:c={tcolor}")
                    if not last:
                        chain.append(f"fade=t=out:st={max(0.0, bd - td):.2f}:"
                                     f"d={td:.2f}:c={tcolor}")
                    parts.append(f"[{vlab}]{','.join(chain)}[{out_lab}]")
                elif style == "flash":
                    # Additive white pop peaking exactly ON the cut — eq's
                    # brightness accepts a per-frame expression, so the ramp
                    # is continuous, unlike a dip's fade-through.
                    terms = []
                    if not last:
                        terms.append(
                            f"0.85*max(0,1-({bd:.3f}-t)/{td:.3f})")
                    if not first:
                        terms.append(f"0.85*max(0,1-t/{td:.3f})")
                    parts.append(f"[{vlab}]eq=brightness='{'+'.join(terms)}'"
                                 f":eval=frame[{out_lab}]")
                elif style == "glitch":
                    rr = max(4, int(round((W or 1280) * 0.008)))
                    parts.append(
                        f"[{vlab}]rgbashift=rh={rr}:bh=-{rr}:enable='{en}',"
                        f"noise=alls=18:allf=t:enable='{en}'[{out_lab}]")
                else:
                    faded.append((vlab, alab, bd))
                    continue
                faded.append((out_lab, alab, bd))
            blocks = faded

    pairs = "".join(f"[{v}][{a}]" for v, a, _d in blocks)
    parts.append(f"{pairs}concat=n={len(blocks)}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    if trans_post:
        # Junction list in PROGRAM time. Each side of a junction keeps the
        # per-block rule: it participates only when its own block affords
        # td >= 0.05 (min(tdur, bd/2 - 0.05)). Terms are half-open-windowed
        # (gte*lt) so the junction frame belongs to the incoming side — the
        # exact frame ownership concat gave the per-block version.
        style, tdur = trans_post
        cum, juncs = 0.0, []
        for k in range(len(blocks) - 1):
            bd_k, bd_n = blocks[k][2], blocks[k + 1][2]
            cum += bd_k
            # `cum` must keep accumulating for EVERY block — it is the junction's
            # position in program time. Only whether we emit a whip here is
            # conditional. Skipping the accumulation instead of the append would
            # slide every later transition onto the wrong moment.
            if k not in junctions:
                continue
            td_o = min(tdur, max(0.0, bd_k / 2 - 0.05))
            td_i = min(tdur, max(0.0, bd_n / 2 - 0.05))
            juncs.append((cum, td_o if td_o >= 0.05 else None,
                          td_i if td_i >= 0.05 else None))

        def _win(tvar, a, b):
            return f"gte({tvar},{a:.3f})*lt({tvar},{b:.3f})"

        if style in ("whip_left", "whip_right"):
            # The frame whips off in the cut direction over a black backdrop
            # while a directional blur smears the motion; the next block
            # whips in from the opposite edge. Quadratic easing so the move
            # accelerates INTO the cut.
            dirn = -1 if style == "whip_left" else 1
            xterms, enspans = [], []
            for c, td_o, td_i in juncs:
                if td_o:
                    xterms.append(
                        f"{dirn}*{W}*pow(max(0,"
                        f"(t-{c - td_o:.3f})/{td_o:.3f}),2)"
                        f"*{_win('t', c - td_o, c)}")
                    enspans.append((c - td_o, c))
                if td_i:
                    # ({-dirn}) not -({dirn}): terms are '+'-joined, and
                    # ffmpeg's expression parser rejects the '+-(' sequence
                    # a leading unary minus would produce.
                    xterms.append(
                        f"({-dirn})*{W}*pow(max(0,"
                        f"1-(t-{c:.3f})/{td_i:.3f}),2)"
                        f"*{_win('t', c, c + td_i)}")
                    enspans.append((c, c + td_i))
            if xterms:
                total_bd = sum(b for _, _, b in blocks)
                blur_r = max(6, int(round((W or 1280) * 0.012)))
                en = "+".join(f"between(t,{a:.3f},{b:.3f})"
                              for a, b in merge_spans(enspans, gap=0.0))
                parts.append(f"color=c=black:s={W}x{H}:r={fps:.3f}:"
                             f"d={total_bd:.3f}[wbg]")
                parts.append(f"[wbg][{vlabel}]overlay="
                             f"x='{'+'.join(xterms)}':y=0:"
                             f"eof_action=pass[wov]")
                parts.append(f"[wov]dblur=angle=0:radius={blur_r}:"
                             f"enable='{en}'[vwhip]")
                vlabel = "vwhip"
        else:                          # zoom_punch
            # Accelerating push INTO the cut; the next block lands from a
            # slight over-zoom. zoompan needs CFR (do_norm forced), so
            # on/fps is program time.
            T = f"on/{fps:.3f}"
            zterms = []
            for c, td_o, td_i in juncs:
                if td_o:
                    zterms.append(
                        f"0.45*pow(max(0,({T}-{c - td_o:.3f})"
                        f"/{td_o:.3f}),2)*{_win(T, c - td_o, c)}")
                if td_i:
                    zterms.append(
                        f"0.30*pow(max(0,1-({T}-{c:.3f})"
                        f"/{td_i:.3f}),2)*{_win(T, c, c + td_i)}")
            if zterms:
                parts.append(f"[{vlabel}]zoompan=z='1+{'+'.join(zterms)}'"
                             f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                             f":d=1:s={W}x{H}:fps={fps:.3f}[vpunch]")
                vlabel = "vpunch"
    # effects: grade -> custom grade -> stylize -> zooms -> overlays ->
    # (captions burn) -> (graphics burn) -> fades. Zooms use one zoompan
    # whose z steps up inside each window; do_norm guarantees the frames
    # entering it are exact CFR WxH so on/fps is program time. Overlays sit
    # ABOVE zooms deliberately (a corner PIP must not scale when the footage
    # punches) and BELOW both text layers (words always win).
    grade = fx.get("grade")
    if grade and grade in GRADE_FILTERS:
        parts.append(f"[{vlabel}]{GRADE_FILTERS[grade]}[vgrade]")
        vlabel = "vgrade"
    if grade_custom:
        # Continuous color controls, applied AFTER the preset so "cinematic
        # but warmer" composes. exposure maps to eq brightness (+-0.35 full
        # scale); temperature/tint to colorbalance shadows+midtones — the
        # portable approximation (colortemperature exists but its neutral
        # point drifts across ffmpeg majors; colorbalance does not).
        gc = grade_custom
        eq_bits = []
        if gc.get("exposure") is not None:
            eq_bits.append(f"brightness={0.35 * float(gc['exposure']):.3f}")
        if gc.get("contrast") is not None:
            eq_bits.append(f"contrast={float(gc['contrast']):.3f}")
        if gc.get("saturation") is not None:
            eq_bits.append(f"saturation={float(gc['saturation']):.3f}")
        cb_bits = []
        temp = float(gc.get("temperature") or 0.0)
        tint = float(gc.get("tint") or 0.0)
        rs = 0.10 * temp + 0.03 * tint
        bs = -0.12 * temp + 0.05 * tint
        gs = -0.08 * tint
        if abs(rs) > 1e-4:
            cb_bits.append(f"rs={rs:.3f}:rm={rs * 0.6:.3f}")
        if abs(gs) > 1e-4:
            cb_bits.append(f"gs={gs:.3f}:gm={gs * 0.6:.3f}")
        if abs(bs) > 1e-4:
            cb_bits.append(f"bs={bs:.3f}:bm={bs * 0.6:.3f}")
        chain = []
        if eq_bits:
            chain.append("eq=" + ":".join(eq_bits))
        if cb_bits:
            chain.append("colorbalance=" + ":".join(cb_bits))
        if chain:
            parts.append(f"[{vlabel}]{','.join(chain)}[vgcust]")
            vlabel = "vgcust"
    for si, styl in enumerate(stylize):
        kind = styl.get("kind")
        i_ = float(styl.get("intensity") or 0.5)
        a = styl.get("start")
        b = styl.get("end")
        if a is not None:
            a = max(0.0, float(a))
            b = min(tl.out_duration, float(b))
            if b - a < 0.05:
                continue
            en = f":enable='between(t,{a:.3f},{b:.3f})'"
            win = f"between(t,{a:.3f},{b:.3f})"
        else:
            en = ""
            win = "1"
        out_lab = f"vsty{si}"
        if kind == "grain":
            parts.append(f"[{vlabel}]noise=alls={5 + int(25 * i_)}"
                         f":allf=t+u{en}[{out_lab}]")
        elif kind == "vignette":
            parts.append(f"[{vlabel}]vignette=a=PI/{4.8 - 2.2 * i_:.2f}"
                         f"{en}[{out_lab}]")
        elif kind == "chromatic":
            r = 2 + int(10 * i_)
            parts.append(f"[{vlabel}]rgbashift=rh={r}:bh=-{r}{en}"
                         f"[{out_lab}]")
        elif kind == "dream_blur":
            parts.append(f"[{vlabel}]gblur=sigma={2 + 8 * i_:.1f}{en}"
                         f"[{out_lab}]")
        elif kind == "vhs":
            parts.append(f"[{vlabel}]rgbashift=rh=3:bh=-3{en},"
                         f"noise=alls=12:allf=t{en},"
                         f"eq=saturation=0.8:contrast=1.05{en}[{out_lab}]")
        elif kind == "flash":
            parts.append(f"[{vlabel}]eq=brightness={0.15 + 0.45 * i_:.2f}"
                         f"{en}[{out_lab}]")
        elif kind == "glow":
            # split -> blur -> screen-blend. blend's enable passes the TOP
            # (first) input through when off, which is the ungraded main —
            # exactly the off state a windowed glow needs.
            parts.append(f"[{vlabel}]split[glA{si}][glB{si}]")
            parts.append(f"[glB{si}]gblur=sigma={10 + 25 * i_:.1f}[glG{si}]")
            parts.append(f"[glA{si}][glG{si}]blend=all_mode=screen"
                         f":all_opacity={0.25 + 0.4 * i_:.2f}{en}"
                         f"[{out_lab}]")
        elif kind == "sharpen":
            # unsharp with a 5x5 kernel: the honest answer to "make it
            # clearer". It recovers apparent detail a phone's encoder smeared;
            # it does NOT add resolution, and past ~0.8 it starts ringing on
            # edges, which is why the amount tops out below the filter's own
            # maximum.
            parts.append(f"[{vlabel}]unsharp=5:5:{0.35 + 1.05 * i_:.2f}"
                         f":5:5:{0.15 + 0.45 * i_:.2f}{en}[{out_lab}]")
        elif kind == "denoise":
            # hqdn3d before sharpening is what stops a sharpen pass from
            # amplifying sensor noise into crawling grain. Low-light phone
            # footage is the case; it costs detail, so the default is gentle.
            parts.append(f"[{vlabel}]hqdn3d={1.5 + 3.5 * i_:.2f}"
                         f":{1.0 + 2.5 * i_:.2f}:{4.0 + 6.0 * i_:.2f}"
                         f":{3.0 + 5.0 * i_:.2f}{en}[{out_lab}]")
        elif kind == "motion_blur":
            # Real frame blending — each output frame is the average of the
            # last N — which is what "motion blur" means on already-shot
            # footage. Only reads on MOVEMENT (a whip, a sprint, a speed ramp);
            # on a static shot it does nothing, which is correct and worth
            # telling the user.
            n = 2 + int(round(3 * i_))
            parts.append(f"[{vlabel}]tmix=frames={n}:weights='"
                         + " ".join(["1"] * n) + f"'{en}[{out_lab}]")
        elif kind == "shake":
            # Windowed handheld wobble via zoompan: z=1 outside the window,
            # so there is NO hidden crop on the rest of the program (a naive
            # crop-shift shakes cheaply but permanently zooms everything).
            T = f"on/{fps:.3f}"
            winT = win.replace("t,", f"{T},") if win != "1" else "1"
            amp = (W or 1280) * 0.006 * (0.5 + i_)
            z_amt = 0.04 + 0.06 * i_
            parts.append(
                f"[{vlabel}]zoompan=z='1+{z_amt:.3f}*({winT})'"
                f":x='iw/2-(iw/zoom/2)+{amp:.1f}*sin({T}*{13 + 8 * i_:.1f})"
                f"*({winT})'"
                f":y='ih/2-(ih/zoom/2)+{amp * 0.7:.1f}*cos({T}*{11 + 6 * i_:.1f})"
                f"*({winT})'"
                f":d=1:s={W}x{H}:fps={fps:.3f}[{out_lab}]")
        else:
            continue
        vlabel = out_lab
    zoom_terms = []
    # A takeover always aims (at the screen), so it forces the targeted branch
    # for the whole zoompan. It must be decided BEFORE the zoom loop: that loop
    # only emits a zoom's own cx/cy terms when the graph is already targeted,
    # so flipping this afterwards would silently drop them.
    zoom_targeted = bool(takeovers) or any(
        z.get("cx") is not None or z.get("cy") is not None or z.get("path")
        for z in zooms)
    cx_terms, cy_terms = [], []
    for z in zooms:
        a = max(0.0, float(z["start"]))
        b = min(tl.out_duration, float(z["end"]))
        if b - a < 0.05:
            continue
        st = float(z.get("strength", 0.25))
        t = f"on/{fps:.3f}"
        zmode = z.get("mode") or "punch"
        if zmode in ("follow", "path"):
            # Round 45 / round 51. The travelling zoom: the CENTRE glides
            # along the waypoints while the frame stays pushed in, instead of
            # cutting out and back between two subjects. 'follow' holds one
            # strength and ramps it at the window edges; 'path' keyframes the
            # strength too. Both shapes — and both callers, showcase_demo and
            # add_zoom_path — go through worker/travel.py, which emits ONE
            # expression per axis. Legacy 'follow' zooms come back out of it
            # character-for-character, so their cached renders still match.
            zoom_terms.append(travel.strength_term(z, t, a, b))
            cxe, cye = travel.centre_terms(z, t, a, b)
            if cxe:
                cx_terms.append(cxe)
            if cye:
                cy_terms.append(cye)
            continue
        if zmode == "ease":
            # smooth ramp in and out inside the window (0 outside it)
            r = max(0.15, min(0.4, (b - a) / 4.0))
            zoom_terms.append(
                f"{st:.2f}*clip(({t}-{a:.3f})/{r:.3f},0,1)"
                f"*clip(({b:.3f}-{t})/{r:.3f},0,1)")
        elif zmode == "push_in":
            # Ken Burns drift: zoom grows 0 -> strength across the window
            zoom_terms.append(
                f"{st:.2f}*(({t}-{a:.3f})/{b - a:.3f})"
                f"*between({t},{a:.3f},{b:.3f})")
        elif zmode == "pull_out":
            zoom_terms.append(
                f"{st:.2f}*(1-(({t}-{a:.3f})/{b - a:.3f}))"
                f"*between({t},{a:.3f},{b:.3f})")
        else:                           # punch: instant step in/out
            zoom_terms.append(f"{st:.2f}*between({t},{a:.3f},{b:.3f})")
        if zoom_targeted:
            # target expressions: 0.5 (center) outside every window, the
            # zoom's own cx/cy inside its window — so multiple zooms can
            # each punch toward their own subject.
            cx = z.get("cx")
            cy = z.get("cy")
            if cx is not None and abs(float(cx) - 0.5) > 1e-6:
                cx_terms.append(f"{float(cx) - 0.5:.3f}"
                                f"*between({t},{a:.3f},{b:.3f})")
            if cy is not None and abs(float(cy) - 0.5) > 1e-6:
                cy_terms.append(f"{float(cy) - 0.5:.3f}"
                                f"*between({t},{a:.3f},{b:.3f})")
    # An aspect shift optionally pushes the picture in as the frame narrows, so
    # the subject holds its size instead of just losing its sides. It rides the
    # SAME zoompan as the zooms (one geometry filter, not two) and is emitted
    # as one more term — which also means a zoom and a shift over the same
    # moment compose rather than fight.
    # A screen takeover's camera push is a zoom term like any other — one
    # geometry filter, and the same resolver the corner pin below reads, so the
    # shot and the content it is pinned to can never be computed differently.
    for idx, item in takeovers:
        a = max(0.0, float(item["start"]))
        b = min(tl.out_duration, a + float(item["duration_s"]))
        if b - a < 0.05:
            continue
        st, cxt, cyt = _screen_lock_terms(item["screen"], f"on/{fps:.3f}",
                                          a, b, fps)
        zoom_terms.append(st)
        if cxt:
            cx_terms.append(cxt)
        if cyt:
            cy_terms.append(cyt)
    shift_w, shift_h, shift_z = ([], [], [])
    if shifts:
        shift_w, shift_h, shift_z = screenframe.shift_tracks(
            shifts, W or 1280, H or 720, tl.out_duration)
        if screenframe.track_varies(shift_z):
            zexp = travel.path_value_expr(
                shift_z, "v", f"on/{fps:.3f}", 0.0, tl.out_duration,
                default=0.0, ease="cubic_in_out")
            if zexp:
                zoom_terms.append(f"({zexp})")
    if zoom_terms:
        zexpr = "1+" + "+".join(zoom_terms)
        if zoom_targeted:
            cxe = "0.5" + ("+" + "+".join(cx_terms) if cx_terms else "")
            cye = "0.5" + ("+" + "+".join(cy_terms) if cy_terms else "")
            xexpr = f"(iw-iw/zoom)*({cxe})"
            yexpr = f"(ih-ih/zoom)*({cye})"
        else:
            # the exact legacy strings — mathematically (iw-iw/zoom)*0.5
            xexpr = "iw/2-(iw/zoom/2)"
            yexpr = "ih/2-(ih/zoom/2)"
        parts.append(f"[{vlabel}]zoompan=z='{zexpr}'"
                     f":x='{xexpr}':y='{yexpr}'"
                     f":d=1:s={W}x{H}:fps={fps:.3f}[vzoom]")
        vlabel = "vzoom"
    # ---- screen takeovers (round 55): content pinned INTO the footage ----
    # Emitted after the zoom because the corner path is written in POST-push
    # screen space, and before the PIP overlays because a takeover is the
    # PICTURE for its window — a logo or a caption still belongs on top of it.
    for j, (idx, item) in enumerate(takeovers):
        o_start = float(item["start"])
        o_dur = float(item["duration_s"])
        lock = item["screen"]
        pad = SCREEN_PAD_PX
        iw_, ih_ = _even((W or 1280) - 2 * pad), _even((H or 720) - 2 * pad)
        chain = []
        if item["kind"] != "image":
            off = float(item.get("source_start_s") or 0.0)
            chain.append(f"trim=start={off:.3f}:end={off + o_dur:.3f}")
            chain.append("setpts=PTS-STARTPTS")
        # The content is cover-fitted to the OUTPUT frame, not to the screen's
        # shape: the takeover ENDS full-frame, and an asset that changed aspect
        # on the way there would have to squash to get out of the glass.
        chain.append(f"scale={iw_}:{ih_}:force_original_aspect_ratio=increase")
        chain.append(f"crop={iw_}:{ih_}")
        chain.append("format=rgba")
        chain.append(f"pad={W}:{H}:{pad}:{pad}:color=black@0")
        # vf_perspective has no `t` — only `on` — so the frames reaching it
        # have to be at the render rate or every corner expression is off by
        # the ratio of the asset's fps to ours.
        chain.append(f"fps={fps:.3f}")
        cs = screen_lock_corner_paths(lock, W or 1280, H or 720, fps, o_dur)
        chain.append(
            "perspective=" + ":".join(
                f"{k}='{v}'" for k, v in zip(
                    ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"), cs))
            + ":sense=destination:eval=frame:interpolation=cubic")
        op = item.get("opacity")
        if op is not None and float(op) < 0.999:
            chain.append(f"colorchannelmixer=aa={float(op):.3f}")
        chain.append(f"setpts=PTS+{o_start:.3f}/TB")
        parts.append(f"[{idx}:v]{','.join(chain)}[tko{j}]")
        parts.append(
            f"[{vlabel}][tko{j}]overlay=0:0:eof_action=pass"
            f":enable='between(t,{o_start:.3f},{o_start + o_dur:.3f})'"
            f"[vtk{j}]")
        vlabel = f"vtk{j}"
    # ---- overlays (round 35): PIP / b-roll / logo layer -----------------
    for j, (idx, item) in enumerate(overlay_inputs):
        o_start = float(item["start"])
        o_dur = float(item["duration_s"])
        ow = _even((W or 1280) * float(item.get("scale") or 0.4))
        chain = []
        if item["kind"] != "image":
            off = float(item.get("source_start_s") or 0.0)
            chain.append(f"trim=start={off:.3f}:end={off + o_dur:.3f}")
            chain.append("setpts=PTS-STARTPTS")
        if item.get("fit") == "cover":
            # B-roll cutaway (round 36): fill the WHOLE output frame — scale
            # up + center-crop the overflow. The position expression below
            # still runs; with w == main_w and the default x/y of 0.5 it
            # resolves to 0, and entrances/exits/opacity keep working.
            chain.append(f"scale={W or 1280}:{H or 720}:"
                         f"force_original_aspect_ratio=increase,"
                         f"crop={W or 1280}:{H or 720}")
        else:
            chain.append(f"scale={ow}:-2")
        chain.append("format=rgba")
        op = item.get("opacity")
        if op is not None and float(op) < 0.999:
            chain.append(f"colorchannelmixer=aa={float(op):.3f}")
        ent, ext = item.get("entrance"), item.get("exit")
        ed = min(0.35, o_dur / 3)
        if ent == "fade":
            chain.append(f"fade=t=in:st=0:d={ed:.2f}:alpha=1")
        if ext == "fade":
            chain.append(f"fade=t=out:st={max(0.0, o_dur - ed):.2f}"
                         f":d={ed:.2f}:alpha=1")
        rot = item.get("rotation")
        if rot:
            rad = float(rot) * 3.14159265 / 180.0
            chain.append(f"rotate={rad:.4f}:c=black@0.0"
                         f":ow=rotw({rad:.4f}):oh=roth({rad:.4f})")
        chain.append(f"setpts=PTS+{o_start:.3f}/TB")
        parts.append(f"[{idx}:v]{','.join(chain)}[ovp{j}]")
        # position: keyframed fractions of the MAIN frame, center-anchored.
        # Slide entrance/exit rides the position expression (quadratic ease
        # from/to one frame-width/height away).
        lt = f"(t-{o_start:.3f})"
        xe = f"main_w*({_anim_expr(item.get('x', 0.5), lt)})-w/2"
        ye = f"main_h*({_anim_expr(item.get('y', 0.5), lt)})-h/2"
        if ent == "slide_left":       # arrives moving leftward: from right
            xe += f"+main_w*pow(max(0,1-{lt}/{ed:.2f}),2)"
        elif ent == "slide_right":
            xe += f"-main_w*pow(max(0,1-{lt}/{ed:.2f}),2)"
        elif ent == "slide_up":       # arrives moving upward: from below
            ye += f"+main_h*pow(max(0,1-{lt}/{ed:.2f}),2)"
        xt0 = max(0.0, o_dur - ed)
        if ext == "slide_left":
            xe += f"-main_w*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        elif ext == "slide_right":
            xe += f"+main_w*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        elif ext == "slide_up":
            ye += f"-main_h*pow(max(0,({lt}-{xt0:.2f})/{ed:.2f}),2)"
        parts.append(
            f"[{vlabel}][ovp{j}]overlay=x='{xe}':y='{ye}'"
            f":eof_action=pass"
            f":enable='between(t,{o_start:.3f},{o_start + o_dur:.3f})'"
            f"[vov{j}]")
        vlabel = f"vov{j}"
    if ass_path:
        # fontsdir points libass at the premium fonts bundled with the
        # worker (worker/fonts) — system fontconfig still supplies DejaVu
        # and the Noto fallbacks for scripts the bundled fonts lack.
        parts.append(f"[{vlabel}]subtitles=filename='{ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[vsub]")
        vlabel = "vsub"
    if gfx_ass_path:
        # The motion-graphics layer burns on its own pass ABOVE captions —
        # a title always wins over a caption crossing it (see graphics.py
        # for why it is a separate file, not extra caption events).
        parts.append(f"[{vlabel}]subtitles=filename='{gfx_ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[vgfx]")
        vlabel = "vgfx"
    # ---- mid-video aspect change (round 51) -----------------------------
    # The bars go on ABOVE the captions on purpose: when the frame narrows to
    # 9:16, anything outside the new window is outside the FRAME, and a caption
    # half-hanging into the letterbox is the single artefact that would give
    # the effect away as a matte rather than a reframe.
    if shifts:
        # NOT drawbox. drawbox evaluates x/y/w/h ONCE, at configuration time —
        # verified against ffmpeg, where a box with w='320*t/2' renders at a
        # constant width for the whole clip (and w=0 means "the input's full
        # width", so a zero-thickness bar blanks the frame instead of
        # disappearing). overlay DOES re-evaluate its position per frame with
        # eval=frame, so each bar is a full-frame colour plate slid in from
        # off-screen: the visible part of it IS the bar, and its thickness
        # animates because its position does.
        col = (shifts[0].get("color") or "#000000").lstrip("#")
        # The expressions are resolved BEFORE the split is emitted. Deciding
        # how many bar copies to fan out and then skipping one in the loop
        # would leave a split output unconnected, which ffmpeg rejects — the
        # whole render dies rather than one effect being missing.
        axes = []
        for axis, pts, dim in (("w", shift_w, W or 1280),
                               ("h", shift_h, H or 720)):
            if not screenframe.track_varies(pts):
                continue
            expr = travel.path_value_expr(pts, "v", "t", 0.0, tl.out_duration,
                                          default=1.0, ease="cubic_in_out")
            if expr:
                axes.append((axis, dim, expr))
        if axes:
            n_bars = 2 * len(axes)
            parts.append(
                f"color=c=0x{col}:s={W or 1280}x{H or 720}:r={fps:.3f}:"
                f"d={tl.out_duration + 1.0:.3f},format=rgba,split"
                + (f"={n_bars}" if n_bars > 2 else "")
                + "".join(f"[fsb{i}]" for i in range(n_bars)))
        bar_i = 0
        for axis, dim, expr in axes:
            # bar thickness = half of what the target aspect gives up
            thick = f"({dim}*(1-({expr}))/2)"
            for k, pos in enumerate((f"({thick})-{dim}", f"{dim}-({thick})")):
                out_lab = f"vfs{axis}{k}"
                xy = (f"x='{pos}':y=0" if axis == "w"
                      else f"x=0:y='{pos}'")
                parts.append(
                    f"[{vlabel}][fsb{bar_i}]overlay={xy}:eval=frame:"
                    f"format=auto[{out_lab}]")
                vlabel = out_lab
                bar_i += 1
    # ---- the floating rounded window (round 51) -------------------------
    # ONE composite: the finished picture is scaled into the plate's hole and
    # the plate — backdrop, shadow and rounded corners already drawn — is laid
    # over it. Applied after captions and graphics so the whole finished video
    # is what floats, which is what the look means.
    if plate_idx is not None and plate_box:
        pw, ph, ox, oy = plate_box
        parts.append(f"[{vlabel}]scale={pw}:{ph},setsar=1[vsfp]")
        parts.append(f"[vsfp]pad={W}:{H}:{ox}:{oy}:color=black[vsfb]")
        parts.append(f"[{plate_idx}:v]format=rgba[vsfpl]")
        parts.append("[vsfb][vsfpl]overlay=0:0:format=auto[vsf]")
        vlabel = "vsf"
    fade_in = float(fx.get("fade_in_s") or 0.0)
    fade_out = float(fx.get("fade_out_s") or 0.0)
    if fade_in:
        parts.append(f"[{vlabel}]fade=t=in:st=0:d={fade_in:.2f}[vfi]")
        vlabel = "vfi"
    if fade_out:
        st = max(0.0, tl.out_duration - fade_out)
        parts.append(f"[{vlabel}]fade=t=out:st={st:.2f}:d={fade_out:.2f}[vfo]")
        vlabel = "vfo"
    # ---- branded end card ---------------------------------------------
    # Placed AFTER the grade, captions and fades. Upstream of the grade it
    # would be recoloured (GRADE_FILTERS['bw'] desaturates the brand red,
    # 'vintage' tints it); upstream of fade_out a user's fade-to-black would
    # swallow the branding instead of ending the programme. It is deliberately
    # NOT routed through the music amix: that mix is `duration=first`, keyed to
    # the programme stream, so appending the card's silence there would
    # silently extend every music item's span.
    #
    # The preview downscale happens AFTER the concat, not before. Doing it
    # first and then forcing the programme back to WxH for concat compatibility
    # would scale a 480p preview back UP to full resolution — a preview that is
    # slower to encode and larger than the final it is standing in for.
    # Free-tier mark on the PROGRAM stream, before any end card is
    # concatenated — the card is already branded, and marking it would stack
    # two logos on one frame.
    if robot_idx is not None:
        parts.extend(_watermark_parts(vlabel, "vwm", robot_idx,
                                      wm_ass_path, W, H))
        vlabel = "vwm"

    outro_here = outro_s > 0.0 and card_idx is not None
    v_final = "vout"
    if not outro_here and preview:
        parts.append(rf"[{vlabel}]scale=-2:min(480\,floor(ih/2)*2)[vsc]")
        vlabel = "vsc"
    if outro_here:
        # Force exact geometry before concat. concat demands identical
        # dimensions, SAR and pixel format across segments, and the cheap
        # graph (no inserts, no reframe, no zoom) makes no such guarantee.
        #
        # But "exact" must not mean "different from what this render would
        # otherwise have produced". Two properties the cheap graph passes
        # through untouched, both measured as regressions before this:
        #
        #  * SAR. Anamorphic sources (a 16:9 picture stored in 4:3 pixels)
        #    carry a non-1 pixel aspect. A blanket setsar=1 at the coded width
        #    squashes the picture, so the width is widened to the DISPLAY
        #    width first and the result genuinely is square-pixel.
        #  * Frame rate. `fps` is capped at 60 for the normalized path; the
        #    cheap path keeps the source's own rate. Forcing the cap turned a
        #    120fps export into 60fps only because it gained an end card.
        #
        # When do_norm already ran, the programme is W x H, SAR 1, at `fps` —
        # so those are the right targets and no correction applies.
        sar = 1.0 if do_norm else (float(src_sar) or 1.0)
        oW = W if abs(sar - 1.0) < 0.001 else _even(W * sar)
        ofps = fps if (do_norm or not src_fps) else float(src_fps)
        parts.append(f"[{vlabel}]scale={oW}:{H},setsar=1,"
                     f"format=yuv420p[vprog]")
        cw, ch = _even(oW * 0.72), _even(H * 0.62)
        parts.append(f"color=c=black:s={oW}x{H}:r={ofps:.3f}:d={outro_s:.3f},"
                     f"format=rgba[obg]")
        # One square-ish card fits every aspect ratio: scaled to fit inside a
        # box that is a fraction of BOTH dimensions, it lands proportionate on
        # 9:16, 16:9, 1:1 and 4:5 without a per-ratio asset.
        parts.append(f"[{card_idx}:v]scale={cw}:{ch}:"
                     f"force_original_aspect_ratio=decrease,format=rgba[ocard]")
        parts.append("[obg][ocard]overlay=(W-w)/2:(H-h)/2:shortest=0[ocomp]")
        fi = min(config.OUTRO_FADE_IN_S, outro_s / 3)
        fo = min(config.OUTRO_FADE_OUT_S, outro_s / 3)
        parts.append(f"[ocomp]fade=t=in:st=0:d={fi:.2f},"
                     f"fade=t=out:st={outro_s - fo:.2f}:d={fo:.2f},"
                     f"format=yuv420p,setsar=1[ovid]")
        v_final = "vprog"
    else:
        parts.append(f"[{vlabel}]format=yuv420p[vout]")

    # program audio: duck under active voiceover, then mix music + voiceover
    alabel = "ac"
    duck_wins = merge_spans(
        [(max(0.0, float(vo["start_output_s"])),
          min(tl.out_duration, float(vo["start_output_s"]) + vd))
         for _idx, vo, vd in vo_inputs if vo.get("duck_others", True)], 0.05)
    duck_wins = [(s, e) for s, e in duck_wins if e - s > 0.05]
    if duck_wins:
        parts.append(f"[{alabel}]volume={DUCK_DB}dB:"
                     f"enable='{_enable_expr(duck_wins)}'[aduck]")
        alabel = "aduck"

    mix_labels = []
    # Smooth (sidechain) ducking: each opted-in music item compresses
    # against a copy of the program audio, so the bed dips WITH the voice
    # and swells back in the gaps instead of the legacy -12dB step. Split
    # the program feed once, before the mix consumes it.
    smooth_js = [j for j, (_i, item, _d) in enumerate(music_inputs or [])
                 if item.get("duck", True)
                 and item.get("duck_mode") == "smooth"]
    if smooth_js:
        taps = "".join(f"[dref{j}]" for j in smooth_js)
        parts.append(f"[{alabel}]asplit={len(smooth_js) + 1}"
                     f"[aduckm]{taps}")
        alabel = "aduckm"
    if music_inputs:
        speech = _speech_spans_out(index, tl)
        for j, (input_idx, item, track_dur) in enumerate(music_inputs):
            m_start = max(0.0, min(item["start"], tl.out_duration - 0.05))
            m_end = max(m_start + 0.05, min(item["end"], tl.out_duration))
            dur = m_end - m_start
            # Offset seeks INTO the track — start on the drop instead of the
            # intro. With -stream_loop the trim window runs straight across
            # repeats, so this one atrim expresses both "seek in" and
            # "loop until the span is full".
            off = max(0.0, float(item.get("offset_s") or 0.0))
            if track_dur and off >= track_dur - 0.05:
                off = 0.0          # past the end would render pure silence
            smooth = j in smooth_js
            duck = ""
            if item.get("duck", True) and not smooth and speech:
                win = [(max(s, m_start), min(e, m_end)) for s, e in speech
                       if min(e, m_end) - max(s, m_start) > 0.05]
                if win:
                    duck = f",volume={DUCK_DB}dB:enable='{_enable_expr(win)}'"
            # Fades are the music item's OWN, and must land before adelay
            # while t=0 still means "the music's first sample". Clamped to
            # half the span so a 2s sting can't fade in past its own end.
            fades = ""
            fi = min(max(0.0, float(item.get("fade_in_s") or 0.0)), dur / 2)
            fo = min(max(0.0, float(item.get("fade_out_s") or 0.0)), dur / 2)
            if fi > 0.01:
                fades += f",afade=t=in:st=0:d={fi:.2f}"
            if fo > 0.01:
                fades += (f",afade=t=out:st={max(0.0, dur - fo):.2f}"
                          f":d={fo:.2f}")
            delay_ms = int(m_start * 1000)
            delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
            parts.append(
                f"[{input_idx}:a]atrim=start={off:.3f}:end={off + dur:.3f},"
                f"asetpts=PTS-STARTPTS{fades},"
                f"volume={item.get('gain_db', -18)}dB,"
                f"aresample=48000{delay}{duck}[mus{j}]")
            if smooth:
                # After adelay both streams share the program clock, so the
                # compressor reacts to the words playing at that instant.
                # threshold 0.03 ~= -30dBFS: real speech, not room tone.
                parts.append(f"[mus{j}][dref{j}]sidechaincompress="
                             f"threshold=0.03:ratio=12:attack=180:"
                             f"release=550[musc{j}]")
                mix_labels.append(f"[musc{j}]")
            else:
                mix_labels.append(f"[mus{j}]")
    for j, (input_idx, vo, vd) in enumerate(vo_inputs):
        delay_ms = int(max(0.0, float(vo["start_output_s"])) * 1000)
        delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        parts.append(f"[{input_idx}:a]volume={vo.get('gain_db', 0.0)}dB,"
                     f"aresample=48000{delay}[vo{j}]")
        mix_labels.append(f"[vo{j}]")
    for j, (input_idx, item, _sdur) in enumerate(sfx_inputs or []):
        at = max(0.0, min(float(item.get("at") or 0.0), tl.out_duration))
        delay_ms = int(at * 1000)
        delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        # No ducking and no atrim, unlike music. An accent that dips under the
        # very word it is punctuating is not an accent, and a one-shot plays
        # for exactly as long as the file is — amix's duration=first already
        # stops a late boom from running past the end of the programme.
        parts.append(f"[{input_idx}:a]volume={item.get('gain_db', -6.0)}dB,"
                     f"aresample=48000{delay}[sfx{j}]")
        mix_labels.append(f"[sfx{j}]")

    outro_on = outro_here          # one predicate, so the video and audio
    loud = (master or {}).get("loudness") == "social"
    a_prog = "aprog" if (outro_on or loud) else "aout"
    a_final = "apre" if (fade_in or fade_out or outro_on) else a_prog
    if mix_labels:
        parts.append(f"[{alabel}]" + "".join(mix_labels) +
                     f"amix=inputs={1 + len(mix_labels)}:duration=first:"
                     f"normalize=0[{a_final}]")
    else:
        parts.append(f"[{alabel}]anull[{a_final}]")
    if a_final == "apre":
        # Deliberately NO limiter on the sfx mix. The obvious guard against a
        # one-shot summing past 0 dBFS is an alimiter, but alimiter has 5ms of
        # lookahead and therefore DELAYS the whole programme audio by 5ms
        # against the picture — measured, by differencing two renders that
        # should have been identical outside the sfx. Trading a global A/V
        # offset for a hypothetical transient clip is a bad deal, and the
        # pipeline already sums voiceover at 0 dB with no limiter. Headroom is
        # handled where it belongs instead: the pack is normalized to -16 LUFS
        # and sfx default to -6 dB, so the loudest one peaks near -7 dBFS.
        chain = []
        if fade_in:
            chain.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out:
            st = max(0.0, tl.out_duration - fade_out)
            chain.append(f"afade=t=out:st={st:.2f}:d={fade_out:.2f}")
        elif outro_on:
            # Without this the programme's music or speech cuts dead into the
            # card's silence. Skipped when the EDL sets its own fade_out,
            # which already lands the programme in silence.
            d = min(config.OUTRO_AUDIO_TAIL_FADE_S, tl.out_duration / 2)
            if d > 0.01:
                chain.append(f"afade=t=out:st={tl.out_duration - d:.2f}"
                             f":d={d:.2f}")
        parts.append(f"[apre]{','.join(chain) or 'anull'}[{a_prog}]")

    if loud:
        # Master loudness: -14 LUFS / -1.5 dBTP (the social/streaming
        # target), single-pass dynamic loudnorm on the PROGRAM only — the
        # end card's silence must not drag the integrated measurement, and
        # normalizing before the concat keeps it out. loudnorm internally
        # resamples to 192k, so the format is pinned back after.
        nxt = "amst" if outro_on else "aout"
        parts.append(f"[{a_prog}]loudnorm=I=-14:TP=-1.5:LRA=11,"
                     f"{AUDIO_NORM}[{nxt}]")
        a_prog = nxt

    if outro_on:
        parts.append(f"anullsrc=r=48000:cl=stereo:d={outro_s:.3f},"
                     "aformat=sample_fmts=fltp:channel_layouts=stereo[osil]")
        cat_v = "vcat" if preview else "vout"
        parts.append(f"[{v_final}][{a_prog}][ovid][osil]"
                     f"concat=n=2:v=1:a=1[{cat_v}][aout]")
        if preview:
            parts.append(rf"[vcat]scale=-2:min(480\,floor(ih/2)*2),"
                         r"format=yuv420p[vout]")

    return ";".join(parts)


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _render_canvas_edl(edl_dict, out_path, workdir, preview, progress_cb=None,
                       want_wm=False):
    """Render a canvas program (round 34): a timeline with NO main video, where
    the ordered inserts (clips/images) are concatenated on the canvas, plus
    music / sfx / voiceover / manual captions / effects. Mirrors render_edl but
    assembles the ffmpeg inputs with NO input [0] main video — every input
    (silence, music, sfx, inserts, voiceover, end card) starts at index 0 — and
    takes the output geometry from the canvas rather than probing a source."""
    edl = validate_edl(edl_dict).model_dump()
    canvas = edl["canvas"]
    W, H = int(canvas["width"]), int(canvas["height"])
    fps = max(1.0, min(float(canvas.get("fps") or 30.0), 60.0))

    inserts = edl.get("inserts") or []
    voiceover = edl.get("voiceover") or []
    # keep=[] -> Timeline.out_duration == sum of insert durations (the program).
    tl = Timeline(edl["keep"], inserts)
    ass_path = caplib.build_ass(edl, {}, tl,
                                os.path.join(workdir, "captions.ass"),
                                play_res=(W, H))
    gfx_path = graphics.build_gfx_ass(edl, tl.out_duration,
                                      os.path.join(workdir, "graphics.ass"),
                                      play_res=(W, H))

    def _fetch(key, tag, idx):
        local = os.path.join(workdir, f"{tag}_{idx}"
                             + os.path.splitext(key)[1].lower())
        storage.download_to(key, local)
        return local

    music_inputs, insert_inputs, vo_inputs, sfx_inputs = [], [], [], []
    extra_inputs = []
    next_idx = 0                       # no main video: inputs start at [0]

    # Shared anullsrc: the program audio base and the silence under image
    # inserts / silent clips. Always present on a canvas program.
    max_len = tl.out_duration + 1
    extra_inputs += ["-f", "lavfi", "-t", f"{max_len:.2f}",
                     "-i", "anullsrc=r=48000:cl=stereo"]
    silence_idx = next_idx
    next_idx += 1

    for item in edl.get("music", []):
        local = music_source(item["storage_key"],
                             lambda k: _fetch(k, "music", next_idx))
        try:
            track_dur = media.probe_audio_duration(local)
        except Exception:
            track_dur = None
        span = max(0.05, float(item.get("end") or 0.0)
                   - float(item.get("start") or 0.0))
        offset = max(0.0, float(item.get("offset_s") or 0.0))
        if (item.get("loop") and track_dur
                and (track_dur - offset) < span - 0.05):
            extra_inputs += ["-stream_loop", "-1"]
        extra_inputs += ["-i", local]
        music_inputs.append((next_idx, item, track_dur))
        next_idx += 1

    for item in edl.get("sfx", []):
        local = sfx_source(item["storage_key"],
                           lambda k: _fetch(k, "sfx", next_idx))
        extra_inputs += ["-i", local]
        sfx_inputs.append((next_idx, item, None))
        next_idx += 1

    for item in inserts:               # sorted by validate_edl = tl.ins order
        local = _fetch(item["asset_key"], "insert", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
            has_ins_audio = False
        else:
            extra_inputs += ["-i", local]
            has_ins_audio = media.probe(local)["has_audio"]
        insert_inputs.append((next_idx, item, has_ins_audio))
        next_idx += 1

    for item in voiceover:
        local = _fetch(item["asset_key"], "vo", next_idx)
        extra_inputs += ["-i", local]
        vo_dur = media.probe_audio_duration(local)
        vo_inputs.append((next_idx, item, vo_dur))
        next_idx += 1

    overlay_inputs = []
    for item in edl.get("overlays") or []:
        local = _fetch(item["asset_key"], "overlay", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
        else:
            extra_inputs += ["-i", local]
        overlay_inputs.append((next_idx, item))
        next_idx += 1

    outro_s = outro_seconds(preview)
    card_idx = None
    if outro_s > 0.0:
        extra_inputs += ["-loop", "1", "-t", f"{outro_s:.3f}",
                         "-r", f"{fps:.3f}", "-i", endcard_path()]
        card_idx = next_idx
        next_idx += 1

    robot_idx, wm_ass_path = None, None
    if want_wm:
        extra_inputs += ["-loop", "1",
                         "-t", f"{tl.out_duration + 5.0:.3f}",
                         "-r", f"{fps:.3f}", "-i", robot_path()]
        robot_idx = next_idx
        next_idx += 1
        wm_ass_path = build_watermark_ass(
            os.path.join(workdir, "watermark.ass"), tl.out_duration, W, H)

    plate_idx, plate_box = _screen_frame_input(
        edl, workdir, W, H, fps, tl.out_duration, extra_inputs, next_idx)
    if plate_idx is not None:
        next_idx += 1

    graph = build_filtergraph(edl, tl.out_duration, False, tl, ass_path,
                              music_inputs, {}, preview,
                              W=W, H=H, fps=fps, frame_mode=None,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx,
                              src_w=W, src_h=H, src_pad=0.0,
                              sfx_inputs=sfx_inputs, outro_s=outro_s,
                              card_idx=card_idx, src_sar=1.0, src_fps=fps,
                              overlay_inputs=overlay_inputs,
                              gfx_ass_path=gfx_path, robot_idx=robot_idx,
                              wm_ass_path=wm_ass_path,
                              plate_idx=plate_idx, plate_box=plate_box)

    if preview:
        encode = ["-c:v", "libx264", "-preset", config.PREVIEW_PRESET,
                  "-crf", "27", "-g", "48", "-keyint_min", "24",
                  "-c:a", "aac", "-b:a", "128k"]
    else:
        encode = ["-c:v", "libx264", "-preset", config.FINAL_PRESET,
                  "-crf", str(config.FINAL_CRF), "-g", "120",
                  "-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", *extra_inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode, *_output_clock(fps), "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    media.run(cmd, progress_cb=progress_cb,
              expected_out_s=tl.out_duration + outro_s)
    return media.duration_of(out_path)


def render_edl(edl_dict, index, src_path, out_path, workdir, preview,
               progress_cb=None, want_wm=False):
    """Render an EDL against a source file. Returns output duration (s)."""
    if is_canvas_program(edl_dict):
        # No main video: the program is built on the canvas from inserts alone.
        return _render_canvas_edl(edl_dict, out_path, workdir, preview,
                                  progress_cb, want_wm=want_wm)
    info = media.probe(src_path)
    src_dur = info["duration"]
    edl = validate_edl(edl_dict, max(src_dur, max(e for _, e in edl_dict["keep"]))
                       ).model_dump()

    frame = edl.get("frame") or None
    W, H = frame_dims(info["width"], info["height"],
                      (frame or {}).get("ratio"))
    frame_mode = (frame or {}).get("mode", "crop") if frame else None
    frame_focus = ((frame.get("focus_x"), frame.get("focus_y"))
                   if frame and (frame.get("focus_x") is not None or
                                 frame.get("focus_y") is not None) else None)
    fps = max(1.0, min(float(info["fps"]) or 30.0, 60.0))

    inserts = edl.get("inserts") or []
    voiceover = edl.get("voiceover") or []
    tl = Timeline(edl["keep"], inserts, edl.get("speed"))
    ass_path = caplib.build_ass(edl, index, tl,
                                os.path.join(workdir, "captions.ass"),
                                play_res=(W, H))
    gfx_path = graphics.build_gfx_ass(edl, tl.out_duration,
                                      os.path.join(workdir, "graphics.ass"),
                                      play_res=(W, H))

    def _fetch(key, tag, idx):
        local = os.path.join(workdir, f"{tag}_{idx}"
                             + os.path.splitext(key)[1].lower())
        storage.download_to(key, local)
        return local

    music_inputs, insert_inputs, vo_inputs, sfx_inputs = [], [], [], []
    extra_inputs = []
    next_idx = 1

    # one shared anullsrc covers a silent main track AND silent insert blocks
    needs_silence = (not info["has_audio"]) or any(
        i["kind"] == "image" for i in inserts) or bool(inserts)
    silence_idx = None
    if needs_silence:
        max_len = max(src_dur, tl.out_duration) + 1
        extra_inputs += ["-f", "lavfi", "-t", f"{max_len:.2f}",
                         "-i", "anullsrc=r=48000:cl=stereo"]
        silence_idx = next_idx
        next_idx += 1

    for item in edl.get("music", []):
        local = music_source(item["storage_key"],
                             lambda k: _fetch(k, "music", next_idx))
        try:
            track_dur = media.probe_audio_duration(local)
        except Exception:
            track_dur = None      # unknown: never loop, just play what's there
        span = max(0.05, float(item.get("end") or 0.0)
                   - float(item.get("start") or 0.0))
        offset = max(0.0, float(item.get("offset_s") or 0.0))
        # -stream_loop repeats the file at the demuxer, so a short track can
        # fill a long span. Only ask for it when the track genuinely cannot
        # cover the span from its offset: looping is a MUSICAL compromise (the
        # seam lands wherever the phrase happens to end), so we never pay it
        # when the track is long enough. Deliberately not aloop, which buffers
        # the whole track in RAM — this worker has OOM-crashed before, and
        # measured output was identical (no gaps, no seam discontinuity).
        # Opt-IN, never defaulted on: an EDL written before loop existed must
        # render exactly as it always did, or a cached render and a fresh one
        # of the SAME version would differ. add_music opts new music in.
        if (item.get("loop") and track_dur
                and (track_dur - offset) < span - 0.05):
            extra_inputs += ["-stream_loop", "-1"]
        extra_inputs += ["-i", local]
        music_inputs.append((next_idx, item, track_dur))
        next_idx += 1

    for item in edl.get("sfx", []):
        local = sfx_source(item["storage_key"],
                           lambda k: _fetch(k, "sfx", next_idx))
        # No duration probe, unlike music: nothing in the graph needs it (a
        # one-shot is never trimmed or looped, and amix's duration=first
        # already stops a late tail overrunning the programme). Probing would
        # spawn an ffprobe per sound per render on a ~1 vCPU box for a number
        # that is then discarded. add_sfx warns about an over-long tail at
        # write time, where the duration is already known.
        extra_inputs += ["-i", local]
        sfx_inputs.append((next_idx, item, None))
        next_idx += 1

    for item in inserts:                      # sorted by validate_edl = tl.ins order
        local = _fetch(item["asset_key"], "insert", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
            has_ins_audio = False
        else:
            extra_inputs += ["-i", local]
            has_ins_audio = media.probe(local)["has_audio"]
        insert_inputs.append((next_idx, item, has_ins_audio))
        next_idx += 1

    for item in voiceover:
        local = _fetch(item["asset_key"], "vo", next_idx)
        extra_inputs += ["-i", local]
        vo_dur = media.probe_audio_duration(local)
        vo_inputs.append((next_idx, item, vo_dur))
        next_idx += 1

    overlay_inputs = []
    for item in edl.get("overlays") or []:
        local = _fetch(item["asset_key"], "overlay", next_idx)
        if item["kind"] == "image" or local.endswith(IMAGE_EXTS):
            extra_inputs += ["-loop", "1", "-t", f"{item['duration_s']:.3f}",
                             "-r", f"{fps:.3f}", "-i", local]
        else:
            extra_inputs += ["-i", local]
        overlay_inputs.append((next_idx, item))
        next_idx += 1

    # Finals render from the ORIGINAL, previews from the proxy — and the proxy
    # already holds its last frame across a short picture track. Without the
    # same hold here the two would disagree: the user approves a preview and
    # exports something else.
    src_pad = 0.0
    vdur = info.get("video_duration")
    if vdur and src_dur - vdur > max(media.PROXY_SHORT_MIN_S,
                                     media.PROXY_SHORT_FRAC * src_dur):
        src_pad = src_dur - vdur

    # The end card is its own ffmpeg input — no filter conjures a bundled PNG
    # out of nothing. -loop 1 -t gives it a real duration and framerate so the
    # overlay does not depend on eof_action to hold a single frame.
    outro_s = outro_seconds(preview)
    card_idx = None
    if outro_s > 0.0:
        extra_inputs += ["-loop", "1", "-t", f"{outro_s:.3f}",
                         "-r", f"{fps:.3f}", "-i", endcard_path()]
        card_idx = next_idx
        next_idx += 1

    # Same deal for the free-tier robot: a looped still input, held for the
    # PROGRAM's length (not the outro's — the mark stops before the card).
    robot_idx, wm_ass_path = None, None
    if want_wm:
        extra_inputs += ["-loop", "1",
                         "-t", f"{tl.out_duration + 5.0:.3f}",
                         "-r", f"{fps:.3f}", "-i", robot_path()]
        robot_idx = next_idx
        next_idx += 1
        wm_ass_path = build_watermark_ass(
            os.path.join(workdir, "watermark.ass"), tl.out_duration, W, H)

    plate_idx, plate_box = _screen_frame_input(
        edl, workdir, W, H, fps, tl.out_duration, extra_inputs, next_idx)
    if plate_idx is not None:
        next_idx += 1

    graph = build_filtergraph(edl, src_dur, info["has_audio"], tl, ass_path,
                              music_inputs, index, preview,
                              W=W, H=H, fps=fps, frame_mode=frame_mode,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx,
                              src_w=info["width"], src_h=info["height"],
                              src_pad=src_pad, sfx_inputs=sfx_inputs,
                              outro_s=outro_s, card_idx=card_idx,
                              src_sar=info.get("sar") or 1.0,
                              src_fps=float(info["fps"]) or fps,
                              overlay_inputs=overlay_inputs,
                              gfx_ass_path=gfx_path,
                              frame_focus=frame_focus, robot_idx=robot_idx,
                              wm_ass_path=wm_ass_path,
                              plate_idx=plate_idx, plate_box=plate_box)

    if preview:
        # Dense keyframes so Safari scrubbing lands precisely (~1.6s GOP).
        encode = ["-c:v", "libx264", "-preset", config.PREVIEW_PRESET,
                  "-crf", "27", "-g", "48", "-keyint_min", "24",
                  "-c:a", "aac", "-b:a", "128k"]
    else:
        # veryfast/CRF 20 is visually transparent for this content and cuts
        # export wall time hard vs the old medium/CRF 18 (see README timings).
        encode = ["-c:v", "libx264", "-preset", config.FINAL_PRESET,
                  "-crf", str(config.FINAL_CRF), "-g", "120",
                  "-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", "-i", src_path, *extra_inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode, *_output_clock(fps), "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    # Progress is percent-of-expected, so it must be the RENDERED length. Left
    # at the programme duration the bar hits 99.9% at programme end and then
    # flatlines through the whole end card.
    media.run(cmd, progress_cb=progress_cb,
              expected_out_s=tl.out_duration + outro_s)
    return media.duration_of(out_path)


# ------------------------------------------------------------------ #
#  Job entrypoint (types: preview | final)                             #
# ------------------------------------------------------------------ #

def _output_clock(fps):
    """Output timing options for every render.

    Round 52. The rendered file's length should come from the filtergraph's
    TIMESTAMPS and nothing else. Left unpinned it also depends on the frame
    rate the last filter link happens to ADVERTISE, because ffmpeg's default
    frame handling re-times the stream to that declared rate — so a link whose
    declared rate disagrees with the PTS it is carrying stretches or squeezes
    the whole video, which is exactly what a wrong-length export is.

    Two real exports failed verification on 2026-07-27 — 102.40s for a 93.50s
    edit, 47.11s for a 45.38s one — and one user pressed Download ten times for
    the same message. BE PRECISE ABOUT WHAT IS KNOWN: their PREVIEWS were
    correct and repeatable, both stored EDLs render to the exact expected
    length here against synthetic CFR, VFR and short-picture-track sources, and
    the customers' originals are not reachable from a dev box, so the specific
    property of those two files was never reproduced. What IS established is
    the structural asymmetry — a preview renders from our own proxy, whose
    encode already pins `-fps_mode cfr -r` for anything it detected as VFR, and
    a final renders from the customer's original, which nothing pinned.

    So this closes the whole family rather than one instance: the output clock
    is now declared, both paths agree, and frames are duplicated or dropped
    only where the timestamps say to. If a length mismatch survives it,
    _stream_report names the offending stream in the worker log — which is the
    datum this investigation did not have.
    """
    return ["-fps_mode", "cfr", "-r", f"{max(1.0, min(float(fps or 30.0), 120.0)):.3f}"]


def _stream_report(path):
    """Per-stream durations, rates and frame counts of a rendered file, as one
    log line. Best-effort: a diagnostic must never be the thing that fails."""
    try:
        out = media.run(["ffprobe", "-v", "error", "-print_format", "json",
                         "-show_format", "-show_streams",
                         "-count_frames" if os.path.getsize(path) < 60_000_000
                         else "-show_format", path], timeout=120)
        data = json.loads(out)
    except Exception as e:
        return f"stream probe failed ({str(e)[:80]})"
    bits = [f"container={float(data.get('format', {}).get('duration') or 0):.2f}s"]
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind not in ("video", "audio"):
            continue
        piece = f"{kind}={float(st.get('duration') or 0):.2f}s"
        if kind == "video":
            piece += (f" r={st.get('r_frame_rate')} avg={st.get('avg_frame_rate')}"
                      f" frames={st.get('nb_read_frames') or st.get('nb_frames') or '?'}")
        bits.append(piece)
    return " ".join(bits)


def _verify_render(edl_json, out_path, out_dur, job_id, variant,
                   src_path=None, src_dur=None):
    """Fail a render whose output is the wrong length or newly-black. The EDL
    gives the expected program duration, but keep spans may legitimately extend
    past the real source content (render_edl validates against the larger of
    src_dur / max keep end, and ffmpeg's trim truncates at real content end),
    so each keep end is clamped to the actual source duration before computing
    the expectation — otherwise a container whose metadata overstates its
    content would falsely fail forever. The black check only fails when the
    output is black where the SOURCE was not, so legitimately-black uploads
    (podcast audio over a black screen) render fine. Raises media.MediaError on
    a real defect -> worker retries once, then surfaces."""
    keep = edl_json["keep"]
    if src_dur:
        keep = [[s, min(e, src_dur)] for s, e in keep if s < src_dur]
        keep = keep or edl_json["keep"]     # never let clamping empty it out
    program = Timeline(keep, edl_json.get("inserts") or [],
                       edl_json.get("speed")).out_duration
    # The rendered file is the programme PLUS the branded end card. The
    # tolerance does not absorb it: 2.5s exceeds max(0.75s, 3%) for anything
    # under ~83s, so without this every short export fails verification and
    # retries forever.
    outro = outro_seconds(variant == "preview")
    expected = program + outro
    tol = max(config.RENDER_DURATION_TOLERANCE_S,
              config.RENDER_DURATION_TOLERANCE_FRAC * expected)
    if abs(out_dur - expected) > tol:
        # Name the stream before failing. "The render is the wrong length" was
        # all a user got, ten times in a row, while pressing Download — and it
        # was all WE got too, which is why the cause took a day of forensics to
        # narrow. Which stream is long (and by how many frames) separates the
        # three possible causes — a stretched picture clock, an audio tail that
        # outruns the programme, or a genuinely mis-built graph — in one line.
        print(f"[render {job_id}] LENGTH MISMATCH {variant}: "
              f"{_stream_report(out_path)} | expected {expected:.2f}s "
              f"(programme {program:.2f}s + outro {outro:.2f}s)", flush=True)
        raise media.MediaError(
            f"{variant} render duration check failed: output is "
            f"{out_dur:.2f}s but the edit is {expected:.2f}s "
            f"(tolerance {tol:.2f}s) — the render is the wrong length")
    if out_dur > 1.0:
        # Measure the PROGRAMME only. The end card is black by design, and the
        # source it is compared against has none, so counting it is pure
        # unmatched numerator in the out_black - src_black comparison below.
        prog_dur = max(0.1, out_dur - outro)
        out_black = media.black_seconds(out_path, prog_dur) / prog_dur
        if out_black > config.RENDER_BLACK_MAX_RATIO:
            # The output is mostly black — but that's only a DEFECT if the
            # source wasn't. Probe the source (once, only in this rare case).
            src_black = 0.0
            if src_path and src_dur and src_dur > 1.0:
                src_black = media.black_seconds(src_path, src_dur) / src_dur
            elif src_dur is None and out_black < 0.98:
                # Canvas program (no source to compare): a lyric/caption or dark
                # program can be legitimately black. Only a near-total black
                # frame is a real defect, so treat anything less as intended.
                src_black = out_black
            if out_black - src_black > config.RENDER_BLACK_MAX_RATIO:
                raise media.MediaError(
                    f"{variant} render black-frame check failed: output is "
                    f"{100 * out_black:.0f}% black vs {100 * src_black:.0f}% in "
                    "the source — the render looks broken")
    print(f"[render {job_id}] verified {variant}: {out_dur:.2f}s "
          f"(expected {expected:.2f}s)", flush=True)


def _caption_index_fp(edl_json, index):
    """Fingerprint of the inputs that decide from_transcript caption TEXT.

    from_transcript captions are burned from the index words at render time, and
    the index row is mutable (self-heal re-index, Deepgram heal, transcript
    edits all upsert it in place). The render cache is otherwise keyed only by
    (version, sha), so a version once rendered against an empty/old transcript
    was served forever — a re-render was a silent no-op and captions never
    updated. Mixing this fingerprint into the cache guard invalidates exactly
    those renders. Returns None when captions don't depend on the transcript, so
    caption-off and explicit-item renders keep the cheap (version, sha) cache.
    """
    caps = edl_json.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return None
    h = hashlib.sha256()
    for w in (index.get("words") or []):
        h.update(f"{w.get('w', '')}|{w.get('t0')}|{w.get('t1')};"
                 .encode("utf-8"))
    return h.hexdigest()[:16]


def _render_stamp(job_id):
    """Name fragment for a render object. Unique PER RENDER, and carrying no
    word a client-side content blocker can pattern-match. Both properties are
    load-bearing:

    (1) The old key was `renders/{pid}/{variant}_v{version}.mp4` — the SAME key
        for every re-render of a version. Bytes mutated behind live 6h
        presigned URLs, and a re-render meant to FIX an object the user could
        not play simply overwrote it at the same address, so recovery could
        never produce genuinely new bytes at a new URL.
    (2) `renders/` and `preview_` are ad-blocker / AV-shield bait; the proxy key
        (an opaque sha) is not — and proxies have played in sessions where a
        render did not. Nothing anywhere parses these keys (they are only
        stored on the asset row and presigned), so opacity is free.

    job_id keeps the object traceable back to the job that wrote it.
    """
    return f"{job_id}-{uuid.uuid4().hex[:12]}"


def run_render_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    variant = "preview" if job["type"] == "preview" else "final"
    version = int(job["payload"].get("edl_version"))
    # A render the USER could not play is the one case where re-encoding the
    # same EDL is the point: the stored object is what failed them, so serving
    # it back from cache makes every retry a guaranteed no-op. force=1 (set by
    # the studio's "couldn't load" recovery) re-encodes to a FRESH key.
    force = bool(job["payload"].get("force"))
    # Entitlement is read HERE, once, at render time — not from anything
    # stored on the project. A user who upgraded a minute ago must get a
    # clean file for this export, and a lapsed one must get the mark back.
    is_paid = bool(worker_db.run(dbx.user_is_paid, job.get("user_id")))
    wm_settings = worker_db.run(dbx.video_settings)
    want_wm = wants_watermark(variant, is_paid, wm_settings)

    edl_row = worker_db.run(dbx.get_edl_version, project_id, version)
    if not edl_row:
        raise RuntimeError(f"EDL version {version} not found")
    original = worker_db.run(dbx.latest_asset, project_id, "original")
    # A canvas program (no main video) renders purely from its inserts on the
    # canvas — there is no original/proxy/index to require or download.
    is_canvas = is_canvas_program(edl_row["json"])
    if not is_canvas and (not original or not original["sha256"]):
        raise RuntimeError("No indexed original video for this project")
    src_sha = original["sha256"] if original else "canvas"

    # Cache: this exact EDL version was already rendered in this variant against
    # this exact source file — serve the stored asset instead of re-encoding.
    # (EDL versions are append-only, so version N's geometry can never change;
    # the sha guard covers video replacement.) For from_transcript captions the
    # burned TEXT also depends on the mutable index, so a caption fingerprint
    # must match too (see _caption_index_fp) — otherwise a caption-less render
    # is served forever after the transcript gains words.
    cached = (None if force else
              worker_db.run(dbx.find_render_asset, project_id, variant, version))
    if cached and (cached.get("meta") or {}).get("src_sha256") == \
            src_sha and storage.exists(cached["storage_key"]):
        caps = edl_row["json"].get("captions")
        needs_fp = isinstance(caps, dict) and caps.get("mode") == "from_transcript"
        stored_fp = (cached.get("meta") or {}).get("caption_fp")
        fp_ok = True
        # Grandfather renders made before fingerprinting existed (stored_fp is
        # None): trust them rather than force-re-encode every cached preview AND
        # final on this box (a long final re-render is minutes on ~1 vCPU). New
        # renders all carry a fingerprint, so the stale-transcript guard applies
        # going forward; only a PRESENT fingerprint that no longer matches busts.
        if needs_fp and stored_fp is not None:
            idx_c = worker_db.run(dbx.get_index_by_sha, original["sha256"])
            want_fp = _caption_index_fp(edl_row["json"],
                                        (idx_c or {}).get("json") or {})
            fp_ok = (want_fp == stored_fp)
        # The end card is a render-pipeline constant, so nothing about adding
        # it moves the EDL version or the source sha — every already-rendered
        # version would keep serving un-branded bytes forever. Grandfathering
        # here must therefore be the OPPOSITE of caption_fp's above: a MISSING
        # stamp means the render predates the card and must be re-encoded,
        # where a missing caption fingerprint is trusted.
        if fp_ok and outro_current(cached.get("meta"), variant) \
                and shaping_current(cached.get("meta"), edl_row["json"]) \
                and transitions_current(cached.get("meta"), edl_row["json"]) \
                and watermark_current(cached.get("meta"), variant, is_paid,
                                      wm_settings):
            return {"render_asset_id": cached["id"],
                    "sheet_key": (cached.get("meta") or {}).get("sheet_key"),
                    "duration_s": cached["duration_s"], "edl_version": version,
                    "variant": variant, "cached": True}
    if is_canvas:
        index = {}
        src_asset = None
    else:
        index_row = worker_db.run(dbx.get_index_by_sha, original["sha256"])
        if not index_row:
            raise RuntimeError("Video index missing — re-run indexing")
        index = index_row["json"]

        src_asset = original
        if variant == "preview":
            proxy = worker_db.run(dbx.latest_asset, project_id, "proxy")
            if proxy:
                src_asset = proxy
        elif (original.get("meta") or {}).get("upload_state") == "pending":
            # Proxy-first upload: the original is registered but its bytes are
            # still in flight. /render/final refuses this with a percentage, so
            # reaching here means a non-studio caller (the MCP surface) asked
            # for an export — give it the same answer rather than a download
            # failure from inside ffmpeg.
            pct = int(round(float((original.get("meta") or {})
                                  .get("upload_progress") or 0) * 100))
            raise RuntimeError(
                "Exports render from the full-resolution original, which is "
                f"still uploading ({pct}% done). The edit is saved — export "
                "again once it lands.")
        clean_key = clean_source_key(edl_row["json"], variant, src_sha)
        if clean_key:
            if not storage.exists(clean_key):
                raise RuntimeError(
                    "The cleaned source for this edit is missing from storage "
                    f"({clean_key}) — re-run the erase so the repainted video "
                    "exists before rendering")
            src_asset = {"storage_key": clean_key}

    workdir = os.path.join(config.TMP_DIR, f"render_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    timings, t0 = {}, time.monotonic()

    def _mark(stage):
        nonlocal t0
        timings[stage] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    try:
        if src_asset:
            src_local = os.path.join(
                workdir, "src" + os.path.splitext(src_asset["storage_key"])[1])
            worker_db.run(dbx.set_progress, job_id, 5)
            storage.download_to(src_asset["storage_key"], src_local)
        else:
            src_local = None            # canvas program: nothing to download
        worker_db.run(dbx.set_progress, job_id, 10)
        _mark("download_s")

        out_local = os.path.join(workdir, f"{variant}_v{version}.mp4")

        # Throttled: ffmpeg emits -progress a couple of times a second, and
        # unthrottled that was ~2 UPDATE/s against the shared DB for the
        # whole encode (a long final = ~1700 writes). set_progress also
        # refreshes the job heartbeat, so a few seconds apart is plenty.
        _last_prog = [0.0]

        def _prog(frac):
            now = time.monotonic()
            if now - _last_prog[0] < 3.0 and frac < 0.99:
                return
            _last_prog[0] = now
            worker_db.run(dbx.set_progress, job_id, 10 + int(frac * 80))

        if variant == "final" and not endcard_path():
            # Exports keep working, but this must never be silent: it means a
            # build shipped without its brand asset, and nobody downstream
            # would otherwise notice that every export lost its end card.
            print(f"[render {job_id}] BRAND CARD MISSING at {ENDCARD_PATH} — "
                  "exporting WITHOUT the Valmera end card", flush=True)

        out_dur = render_edl(edl_row["json"], index, src_local, out_local,
                             workdir, preview=(variant == "preview"),
                             progress_cb=_prog, want_wm=want_wm)
        _mark("encode_s")

        # Render verification: the output must be the expected length and must
        # not be newly-black vs the source. On failure this raises, so the
        # worker retries the encode once (MAX_ATTEMPTS_MEDIA) before surfacing a
        # real error — a visually broken render never uploads silently.
        try:
            src_dur = media.duration_of(src_local) if src_local else None
        except Exception:
            src_dur = None
        _verify_render(edl_row["json"], out_local, out_dur, job_id, variant,
                       src_path=src_local, src_dur=src_dur)
        _mark("verify_s")

        sheet_local = os.path.join(workdir, "result_sheet.jpg")
        try:
            # The PROGRAMME duration, not the file duration. build_result_sheet
            # samples at duration*(i+0.5)/9, so with the file duration the last
            # tile of any render under ~45s lands on the end card — and the
            # vision self-check that reads this sheet is told to flag
            # unexpected black frames. It would report the branding as a defect
            # and the agent would tell the user their video is broken.
            sheets.build_result_sheet(
                out_local, sheet_local,
                max(0.1, out_dur - outro_seconds(variant == "preview")))
        except Exception:
            sheet_local = None
        _mark("sheet_s")

        stamp = _render_stamp(job_id)
        render_key = f"media/{project_id}/{stamp}.mp4"
        storage.upload_file(out_local, render_key, "video/mp4")
        sheet_key = None
        if sheet_local and os.path.exists(sheet_local):
            sheet_key = f"media/{project_id}/{stamp}_s.jpg"
            storage.upload_file(sheet_local, sheet_key, "image/jpeg")
        worker_db.run(dbx.set_progress, job_id, 96)
        _mark("upload_s")

        out_info = media.probe(out_local)
        asset_id = worker_db.run(
            dbx.insert_asset, project_id, "render", render_key,
            bytes_=os.path.getsize(out_local), duration_s=out_dur,
            width=out_info["width"], height=out_info["height"],
            fps=out_info["fps"],
            meta={"variant": variant, "edl_version": version,
                  "sheet_key": sheet_key, "src_sha256": src_sha,
                  "caption_fp": _caption_index_fp(edl_row["json"], index),
                  "outro_v": (config.OUTRO_VERSION
                              if outro_seconds(variant == "preview") else 0),
                  "gfx_shape_v": config.GFX_SHAPING_VERSION,
                  "trans_v": config.TRANSITION_VERSION,
                  "wm_v": watermark_version(variant, is_paid,
                                            wm_settings)})
        # Reclaim the renders this one just replaced. Unique-per-render keys
        # made recovery possible but left every superseded object in the bucket
        # forever; only this exact (variant, version) is pruned, so pinned older
        # VERSIONS still play. Best-effort — never fail a finished render over
        # cleanup.
        try:
            old = worker_db.run(dbx.superseded_renders, project_id, variant,
                                version, asset_id)
            if old:
                keys = []
                for a in old:
                    keys.append(a["storage_key"])
                    keys.append((a.get("meta") or {}).get("sheet_key"))
                storage.delete_keys(keys)
                worker_db.run(dbx.delete_assets, [a["id"] for a in old])
                print(f"[render {job_id}] pruned {len(old)} superseded "
                      f"render(s) for v{version}", flush=True)
        except Exception as e:
            print(f"[render {job_id}] prune skipped: {e}", flush=True)
        # Deterministic mid-word audit: keep boundaries that clip a word,
        # computed straight from the index — visible in logs and to the
        # agent even if it ignored the write-time warnings. Meaningless (and
        # unsafe: index is {} with no ['video']) for a canvas program.
        mw = [] if is_canvas else audit.midword_audit(
            edl_row["json"]["keep"], index.get("words", []),
            index["video"]["duration"])
        if mw:
            print(f"[render {job_id}] MID-WORD AUDIT: {'; '.join(mw)}",
                  flush=True)
        return {"render_asset_id": asset_id, "sheet_key": sheet_key,
                "duration_s": out_dur, "edl_version": version,
                "variant": variant, "timings": timings,
                "midword_audit": mw}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
