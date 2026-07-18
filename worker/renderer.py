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
import os
import shutil
import time
import uuid

import audit
import captions as caplib
import config
import db as dbx
import media
import sheets
import storage
from schemas import EDLValidationError, validate_edl
from timeline import Timeline, merge_spans

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


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


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


def _normalize_video(parts, in_label, out_label, W, H, fps, mode, uid):
    """Append graph parts that bring in_label to exactly WxH @ fps, sar 1.
    mode: crop (center-crop), pad (black bars), pad_blur (blurred backdrop)."""
    tail = f"fps={fps:.3f},setsar=1,format=yuv420p"
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
        parts.append(
            f"[{in_label}]scale={W}:{H}:force_original_aspect_ratio=increase,"
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


def build_filtergraph(edl, src_dur, has_audio, tl, ass_path,
                      music_inputs, index, preview,
                      W=None, H=None, fps=30.0, frame_mode=None,
                      insert_inputs=None, vo_inputs=None, silence_idx=None,
                      src_w=None, src_h=None, src_pad=0.0):
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
    if not keep:
        raise EDLValidationError("All keep segments fall outside the video.")
    insert_inputs = insert_inputs or []
    vo_inputs = vo_inputs or []
    n = len(keep)
    parts = []
    asrc = f"0:a" if has_audio else f"{silence_idx}:a"

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
    # identical dims/fps/audio before concat.
    fx = edl.get("effects") or {}
    zooms = fx.get("zooms") or []
    regions = fx.get("regions") or []
    do_norm = bool(insert_inputs) or frame_mode is not None or bool(zooms)
    mode = frame_mode or "crop"

    # Censor regions are burned into each SOURCE segment BEFORE any
    # reframe/normalization: their fractions are of the SOURCE frame
    # (exactly what look_at showed the agent), a later crop/pad moves the
    # censored footage as one, and inserted material is never censored.
    sw = sh = None
    seg_prog = []
    if regions:
        sw, sh = int(src_w or W), int(src_h or H)
        # program-time start of every keep segment (inserts included), for
        # mapping windowed regions into segment-local time — mirrors the
        # block-order loop below
        _at = [tl.ins[j][0] for j in range(len(insert_inputs))]
        _pre = _prog = 0.0
        _j = 0
        for s, e in keep:
            while _j < len(_at) and _at[_j] <= _pre + 1e-6:
                _prog += float(insert_inputs[_j][1]["duration_s"])
                _j += 1
            seg_prog.append(_prog)
            _pre += e - s
            _prog += e - s

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

    # main segments: trim (+ censor regions), then (when needed) normalize
    # to the output frame
    vsrc = "0:v"
    if src_pad > 0:
        parts.append(f"[0:v]tpad=stop_mode=clone:"
                     f"stop_duration={src_pad:.3f}[vpad]")
        vsrc = "vpad"
    if n == 1:
        _seg_video(0, vsrc, keep[0][0], keep[0][1])
        parts.append(f"[asrc]atrim=start={keep[0][0]:.3f}:end={keep[0][1]:.3f},"
                     f"asetpts=PTS-STARTPTS"
                     + (f",{AUDIO_NORM}" if do_norm else "") + "[a_seg0]")
    else:
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
            _normalize_video(parts, f"segv{i}", f"v_seg{i}", W, H, fps,
                             mode, f"s{i}")

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
                         mode, f"i{j}")
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
        blocks.append((f"v_seg{i}", f"a_seg{i}", e - s))
        pre += e - s
    while ins_j < len(insert_inputs):
        blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}",
                       float(insert_inputs[ins_j][1]["duration_s"])))
        ins_j += 1

    # Transitions: a dip through black/white at every junction. Duration-
    # preserving by construction — each block fades out/in within its own
    # footage (video only; audio concat is untouched), so no timeline math
    # anywhere changes.
    transition = fx.get("transition") or None
    if transition and len(blocks) > 1:
        tdur = float(transition.get("duration_s") or 0.3)
        tcolor = "white" if transition.get("style") == "dip_white" \
            else "black"
        faded = []
        for k, (vlab, alab, bd) in enumerate(blocks):
            td = min(tdur, max(0.0, bd / 2 - 0.05))
            chain = []
            if td >= 0.05:
                if k > 0:
                    chain.append(f"fade=t=in:st=0:d={td:.2f}:c={tcolor}")
                if k < len(blocks) - 1:
                    chain.append(f"fade=t=out:st={max(0.0, bd - td):.2f}:"
                                 f"d={td:.2f}:c={tcolor}")
            if chain:
                parts.append(f"[{vlab}]{','.join(chain)}[vtr{k}]")
                faded.append((f"vtr{k}", alab, bd))
            else:
                faded.append((vlab, alab, bd))
        blocks = faded

    pairs = "".join(f"[{v}][{a}]" for v, a, _d in blocks)
    parts.append(f"{pairs}concat=n={len(blocks)}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    # effects: grade -> punch-in zooms -> (captions burn) -> fades. Zooms use
    # one zoompan whose z steps up inside each window; do_norm guarantees the
    # frames entering it are exact CFR WxH so on/fps is program time.
    grade = fx.get("grade")
    if grade and grade in GRADE_FILTERS:
        parts.append(f"[{vlabel}]{GRADE_FILTERS[grade]}[vgrade]")
        vlabel = "vgrade"
    zoom_terms = []
    for z in zooms:
        a = max(0.0, float(z["start"]))
        b = min(tl.out_duration, float(z["end"]))
        if b - a < 0.05:
            continue
        st = float(z.get("strength", 0.25))
        t = f"on/{fps:.3f}"
        zmode = z.get("mode") or "punch"
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
    if zoom_terms:
        zexpr = "1+" + "+".join(zoom_terms)
        parts.append(f"[{vlabel}]zoompan=z='{zexpr}'"
                     f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s={W}x{H}:fps={fps:.3f}[vzoom]")
        vlabel = "vzoom"
    if ass_path:
        # fontsdir points libass at the premium fonts bundled with the
        # worker (worker/fonts) — system fontconfig still supplies DejaVu
        # and the Noto fallbacks for scripts the bundled fonts lack.
        parts.append(f"[{vlabel}]subtitles=filename='{ass_path}'"
                     f":fontsdir='{caplib.FONTS_DIR}'[vsub]")
        vlabel = "vsub"
    fade_in = float(fx.get("fade_in_s") or 0.0)
    fade_out = float(fx.get("fade_out_s") or 0.0)
    if fade_in:
        parts.append(f"[{vlabel}]fade=t=in:st=0:d={fade_in:.2f}[vfi]")
        vlabel = "vfi"
    if fade_out:
        st = max(0.0, tl.out_duration - fade_out)
        parts.append(f"[{vlabel}]fade=t=out:st={st:.2f}:d={fade_out:.2f}[vfo]")
        vlabel = "vfo"
    if preview:
        parts.append(rf"[{vlabel}]scale=-2:min(480\,floor(ih/2)*2)[vsc]")
        vlabel = "vsc"
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
    if music_inputs:
        speech = _speech_spans_out(index, tl)
        for j, (input_idx, item) in enumerate(music_inputs):
            m_start = max(0.0, min(item["start"], tl.out_duration - 0.05))
            m_end = max(m_start + 0.05, min(item["end"], tl.out_duration))
            dur = m_end - m_start
            duck = ""
            if item.get("duck", True) and speech:
                win = [(max(s, m_start), min(e, m_end)) for s, e in speech
                       if min(e, m_end) - max(s, m_start) > 0.05]
                if win:
                    duck = f",volume={DUCK_DB}dB:enable='{_enable_expr(win)}'"
            delay_ms = int(m_start * 1000)
            delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
            parts.append(
                f"[{input_idx}:a]atrim=start=0:end={dur:.3f},"
                f"asetpts=PTS-STARTPTS,volume={item.get('gain_db', -18)}dB,"
                f"aresample=48000{delay}{duck}[mus{j}]")
            mix_labels.append(f"[mus{j}]")
    for j, (input_idx, vo, vd) in enumerate(vo_inputs):
        delay_ms = int(max(0.0, float(vo["start_output_s"])) * 1000)
        delay = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        parts.append(f"[{input_idx}:a]volume={vo.get('gain_db', 0.0)}dB,"
                     f"aresample=48000{delay}[vo{j}]")
        mix_labels.append(f"[vo{j}]")

    a_final = "apre" if (fade_in or fade_out) else "aout"
    if mix_labels:
        parts.append(f"[{alabel}]" + "".join(mix_labels) +
                     f"amix=inputs={1 + len(mix_labels)}:duration=first:"
                     f"normalize=0[{a_final}]")
    else:
        parts.append(f"[{alabel}]anull[{a_final}]")
    if a_final == "apre":
        chain = []
        if fade_in:
            chain.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out:
            st = max(0.0, tl.out_duration - fade_out)
            chain.append(f"afade=t=out:st={st:.2f}:d={fade_out:.2f}")
        parts.append(f"[apre]{','.join(chain)}[aout]")

    return ";".join(parts)


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def render_edl(edl_dict, index, src_path, out_path, workdir, preview,
               progress_cb=None):
    """Render an EDL against a source file. Returns output duration (s)."""
    info = media.probe(src_path)
    src_dur = info["duration"]
    edl = validate_edl(edl_dict, max(src_dur, max(e for _, e in edl_dict["keep"]))
                       ).model_dump()

    frame = edl.get("frame") or None
    W, H = frame_dims(info["width"], info["height"],
                      (frame or {}).get("ratio"))
    frame_mode = (frame or {}).get("mode", "crop") if frame else None
    fps = max(1.0, min(float(info["fps"]) or 30.0, 60.0))

    inserts = edl.get("inserts") or []
    voiceover = edl.get("voiceover") or []
    tl = Timeline(edl["keep"], inserts)
    ass_path = caplib.build_ass(edl, index, tl,
                                os.path.join(workdir, "captions.ass"),
                                play_res=(W, H))

    def _fetch(key, tag, idx):
        local = os.path.join(workdir, f"{tag}_{idx}"
                             + os.path.splitext(key)[1].lower())
        storage.download_to(key, local)
        return local

    music_inputs, insert_inputs, vo_inputs = [], [], []
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
        extra_inputs += ["-i", _fetch(item["storage_key"], "music", next_idx)]
        music_inputs.append((next_idx, item))
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

    # Finals render from the ORIGINAL, previews from the proxy — and the proxy
    # already holds its last frame across a short picture track. Without the
    # same hold here the two would disagree: the user approves a preview and
    # exports something else.
    src_pad = 0.0
    vdur = info.get("video_duration")
    if vdur and src_dur - vdur > max(media.PROXY_SHORT_MIN_S,
                                     media.PROXY_SHORT_FRAC * src_dur):
        src_pad = src_dur - vdur

    graph = build_filtergraph(edl, src_dur, info["has_audio"], tl, ass_path,
                              music_inputs, index, preview,
                              W=W, H=H, fps=fps, frame_mode=frame_mode,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx,
                              src_w=info["width"], src_h=info["height"],
                              src_pad=src_pad)

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
           *encode, "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out_path]
    media.run(cmd, progress_cb=progress_cb, expected_out_s=tl.out_duration)
    return media.duration_of(out_path)


# ------------------------------------------------------------------ #
#  Job entrypoint (types: preview | final)                             #
# ------------------------------------------------------------------ #

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
    expected = Timeline(keep, edl_json.get("inserts") or []).out_duration
    tol = max(config.RENDER_DURATION_TOLERANCE_S,
              config.RENDER_DURATION_TOLERANCE_FRAC * expected)
    if abs(out_dur - expected) > tol:
        raise media.MediaError(
            f"{variant} render duration check failed: output is "
            f"{out_dur:.2f}s but the edit is {expected:.2f}s "
            f"(tolerance {tol:.2f}s) — the render is the wrong length")
    if out_dur > 1.0:
        out_black = media.black_seconds(out_path, out_dur) / out_dur
        if out_black > config.RENDER_BLACK_MAX_RATIO:
            # The output is mostly black — but that's only a DEFECT if the
            # source wasn't. Probe the source (once, only in this rare case).
            src_black = 0.0
            if src_path and src_dur and src_dur > 1.0:
                src_black = media.black_seconds(src_path, src_dur) / src_dur
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

    edl_row = worker_db.run(dbx.get_edl_version, project_id, version)
    if not edl_row:
        raise RuntimeError(f"EDL version {version} not found")
    original = worker_db.run(dbx.latest_asset, project_id, "original")
    if not original or not original["sha256"]:
        raise RuntimeError("No indexed original video for this project")

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
            original["sha256"] and storage.exists(cached["storage_key"]):
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
        if fp_ok:
            return {"render_asset_id": cached["id"],
                    "sheet_key": (cached.get("meta") or {}).get("sheet_key"),
                    "duration_s": cached["duration_s"], "edl_version": version,
                    "variant": variant, "cached": True}
    index_row = worker_db.run(dbx.get_index_by_sha, original["sha256"])
    if not index_row:
        raise RuntimeError("Video index missing — re-run indexing")
    index = index_row["json"]

    src_asset = original
    if variant == "preview":
        proxy = worker_db.run(dbx.latest_asset, project_id, "proxy")
        if proxy:
            src_asset = proxy

    workdir = os.path.join(config.TMP_DIR, f"render_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    timings, t0 = {}, time.monotonic()

    def _mark(stage):
        nonlocal t0
        timings[stage] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    try:
        src_local = os.path.join(
            workdir, "src" + os.path.splitext(src_asset["storage_key"])[1])
        worker_db.run(dbx.set_progress, job_id, 5)
        storage.download_to(src_asset["storage_key"], src_local)
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

        out_dur = render_edl(edl_row["json"], index, src_local, out_local,
                             workdir, preview=(variant == "preview"),
                             progress_cb=_prog)
        _mark("encode_s")

        # Render verification: the output must be the expected length and must
        # not be newly-black vs the source. On failure this raises, so the
        # worker retries the encode once (MAX_ATTEMPTS_MEDIA) before surfacing a
        # real error — a visually broken render never uploads silently.
        try:
            src_dur = media.duration_of(src_local)
        except Exception:
            src_dur = None
        _verify_render(edl_row["json"], out_local, out_dur, job_id, variant,
                       src_path=src_local, src_dur=src_dur)
        _mark("verify_s")

        sheet_local = os.path.join(workdir, "result_sheet.jpg")
        try:
            sheets.build_result_sheet(out_local, sheet_local, out_dur)
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
                  "sheet_key": sheet_key, "src_sha256": original["sha256"],
                  "caption_fp": _caption_index_fp(edl_row["json"], index)})
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
        # agent even if it ignored the write-time warnings.
        mw = audit.midword_audit(edl_row["json"]["keep"],
                                 index.get("words", []),
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
