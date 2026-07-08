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

import os
import shutil
import time

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
                      insert_inputs=None, vo_inputs=None, silence_idx=None):
    """Input layout: [0] main source video; anullsrc at silence_idx when
    needed (no main audio, image inserts, or silent clip inserts); then one
    input per music item, insert item and voiceover item in EDL order.

    insert_inputs: [(input_idx, item, has_audio)] aligned with the sorted
    EDL inserts (same order as tl.insert_positions()).
    vo_inputs: [(input_idx, item, vo_duration_s)].
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
    # cheap graph). The moment a frame is set or foreign material is spliced
    # in, EVERY block must land on identical dims/fps/audio before concat.
    do_norm = bool(insert_inputs) or frame_mode is not None
    mode = frame_mode or "crop"

    # main segments: trim, then (when needed) normalize to the output frame
    if n == 1:
        vlab = "segv0" if do_norm else "v_seg0"
        parts.append(f"[0:v]trim=start={keep[0][0]:.3f}:end={keep[0][1]:.3f},"
                     f"setpts=PTS-STARTPTS[{vlab}]")
        parts.append(f"[asrc]atrim=start={keep[0][0]:.3f}:end={keep[0][1]:.3f},"
                     f"asetpts=PTS-STARTPTS"
                     + (f",{AUDIO_NORM}" if do_norm else "") + "[a_seg0]")
    else:
        parts.append("[0:v]split=" + str(n)
                     + "".join(f"[vin{i}]" for i in range(n)))
        parts.append("[asrc]asplit=" + str(n)
                     + "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            vlab = f"segv{i}" if do_norm else f"v_seg{i}"
            parts.append(f"[vin{i}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[{vlab}]")
            parts.append(f"[ain{i}]atrim=start={s:.3f}:end={e:.3f},"
                         f"asetpts=PTS-STARTPTS"
                         + (f",{AUDIO_NORM}" if do_norm else "")
                         + f"[a_seg{i}]")
    if do_norm:
        for i in range(n):
            _normalize_video(parts, f"segv{i}", f"v_seg{i}", W, H, fps,
                             mode, f"s{i}")

    # insert blocks: trim to their duration, normalize like everything else
    sil_i = 0
    for j, (idx, item, ins_audio) in enumerate(insert_inputs):
        dur = float(item["duration_s"])
        parts.append(f"[{idx}:v]trim=start=0:end={dur:.3f},"
                     f"setpts=PTS-STARTPTS[insv{j}]")
        _normalize_video(parts, f"insv{j}", f"v_ins{j}", W, H, fps,
                         mode, f"i{j}")
        if ins_audio:
            parts.append(f"[{idx}:a]atrim=start=0:end={dur:.3f},"
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
            blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}"))
            ins_j += 1
        blocks.append((f"v_seg{i}", f"a_seg{i}"))
        pre += e - s
    while ins_j < len(insert_inputs):
        blocks.append((f"v_ins{ins_j}", f"a_ins{ins_j}"))
        ins_j += 1

    pairs = "".join(f"[{v}][{a}]" for v, a in blocks)
    parts.append(f"{pairs}concat=n={len(blocks)}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    if ass_path:
        parts.append(f"[{vlabel}]subtitles=filename='{ass_path}'[vsub]")
        vlabel = "vsub"
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

    if mix_labels:
        parts.append(f"[{alabel}]" + "".join(mix_labels) +
                     f"amix=inputs={1 + len(mix_labels)}:duration=first:"
                     f"normalize=0[aout]")
    else:
        parts.append(f"[{alabel}]anull[aout]")

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

    graph = build_filtergraph(edl, src_dur, info["has_audio"], tl, ass_path,
                              music_inputs, index, preview,
                              W=W, H=H, fps=fps, frame_mode=frame_mode,
                              insert_inputs=insert_inputs,
                              vo_inputs=vo_inputs, silence_idx=silence_idx)

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

def run_render_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    variant = "preview" if job["type"] == "preview" else "final"
    version = int(job["payload"].get("edl_version"))

    edl_row = worker_db.run(dbx.get_edl_version, project_id, version)
    if not edl_row:
        raise RuntimeError(f"EDL version {version} not found")
    original = worker_db.run(dbx.latest_asset, project_id, "original")
    if not original or not original["sha256"]:
        raise RuntimeError("No indexed original video for this project")

    # Cache: this exact EDL version was already rendered in this variant
    # against this exact source file — serve the stored asset instead of
    # re-encoding the same output. (EDL versions are append-only, so version
    # N's content can never change; the sha guard covers video replacement.)
    cached = worker_db.run(dbx.find_render_asset, project_id, variant, version)
    if cached and (cached.get("meta") or {}).get("src_sha256") == \
            original["sha256"] and storage.exists(cached["storage_key"]):
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

        def _prog(frac):
            worker_db.run(dbx.set_progress, job_id, 10 + int(frac * 80))

        out_dur = render_edl(edl_row["json"], index, src_local, out_local,
                             workdir, preview=(variant == "preview"),
                             progress_cb=_prog)
        _mark("encode_s")

        sheet_local = os.path.join(workdir, "result_sheet.jpg")
        try:
            sheets.build_result_sheet(out_local, sheet_local, out_dur)
        except Exception:
            sheet_local = None
        _mark("sheet_s")

        render_key = f"renders/{project_id}/{variant}_v{version}.mp4"
        storage.upload_file(out_local, render_key, "video/mp4")
        sheet_key = None
        if sheet_local and os.path.exists(sheet_local):
            sheet_key = f"renders/{project_id}/{variant}_v{version}_sheet.jpg"
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
                  "sheet_key": sheet_key, "src_sha256": original["sha256"]})
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
