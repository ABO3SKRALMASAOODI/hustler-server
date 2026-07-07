"""Frame-accurate EDL renderer.

Always re-encodes (never stream-copies): trim+concat of keep segments,
captions burned from a generated .ass, music mixed with gain + speech
ducking, volume automation via enable='between(t,a,b)'.

previews read the 720p PROXY and encode fast at 480p; finals read the
ORIGINAL at source resolution. Every render also emits a 3x3 contact sheet
of the result for the agent's self-check.
"""

import os
import shutil
import time

import captions as caplib
import config
import db as dbx
import media
import sheets
import storage
from schemas import EDLValidationError, validate_edl
from timeline import Timeline, merge_spans

DUCK_DB = -12.0
MAX_ENABLE_SPANS = 80


def _enable_expr(spans):
    return "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)


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
                      music_inputs, index, preview):
    """Returns (filter_complex, audio_src_is_lavfi).

    Input layout: [0] source video, [1] anullsrc iff source has no audio,
    then one input per music item in EDL order.
    """
    keep = [(max(0.0, s), min(e, src_dur)) for s, e in edl["keep"]]
    keep = [(s, e) for s, e in keep if e - s > 0.01]
    if not keep:
        raise EDLValidationError("All keep segments fall outside the video.")
    n = len(keep)
    parts = []
    asrc = "0:a" if has_audio else "1:a"

    # Source-time volume automation runs before trimming, so between(t,a,b)
    # windows are in source seconds — exactly what the agent wrote.
    vol_filters = "".join(
        f",volume={v['gain_db']}dB:enable='between(t,{v['start']:.2f},{v['end']:.2f})'"
        for v in edl.get("volume", []))
    parts.append(f"[{asrc}]anull{vol_filters}[asrc]")

    if n == 1:
        s, e = keep[0]
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},"
                     f"setpts=PTS-STARTPTS[v0]")
        parts.append(f"[asrc]atrim=start={s:.3f}:end={e:.3f},"
                     f"asetpts=PTS-STARTPTS[a0]")
    else:
        parts.append("[0:v]split=" + str(n) +
                     "".join(f"[vin{i}]" for i in range(n)))
        parts.append("[asrc]asplit=" + str(n) +
                     "".join(f"[ain{i}]" for i in range(n)))
        for i, (s, e) in enumerate(keep):
            parts.append(f"[vin{i}]trim=start={s:.3f}:end={e:.3f},"
                         f"setpts=PTS-STARTPTS[v{i}]")
            parts.append(f"[ain{i}]atrim=start={s:.3f}:end={e:.3f},"
                         f"asetpts=PTS-STARTPTS[a{i}]")

    pairs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{pairs}concat=n={n}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    if ass_path:
        parts.append(f"[{vlabel}]subtitles=filename='{ass_path}'[vsub]")
        vlabel = "vsub"
    if preview:
        parts.append(rf"[{vlabel}]scale=-2:min(480\,floor(ih/2)*2)[vsc]")
        vlabel = "vsc"
    parts.append(f"[{vlabel}]format=yuv420p[vout]")

    alabel = "ac"
    if music_inputs:
        speech = _speech_spans_out(index, tl)
        parts.append("[ac]aresample=48000[acr]")
        alabel = "acr"
        mus_labels = []
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
            mus_labels.append(f"[mus{j}]")
        parts.append(f"[{alabel}]" + "".join(mus_labels) +
                     f"amix=inputs={1 + len(mus_labels)}:duration=first:"
                     f"normalize=0[aout]")
        alabel = "aout"
    else:
        parts.append(f"[{alabel}]anull[aout]")
        alabel = "aout"

    return ";".join(parts)


def render_edl(edl_dict, index, src_path, out_path, workdir, preview,
               progress_cb=None):
    """Render an EDL against a source file. Returns output duration (s)."""
    info = media.probe(src_path)
    src_dur = info["duration"]
    edl = validate_edl(edl_dict, max(src_dur, max(e for _, e in edl_dict["keep"]))
                       ).model_dump()

    tl = Timeline(edl["keep"])
    ass_path = caplib.build_ass(edl, index, tl,
                                os.path.join(workdir, "captions.ass"))

    music_inputs = []
    extra_inputs = []
    next_idx = 1
    if not info["has_audio"]:
        extra_inputs += ["-f", "lavfi", "-t", f"{src_dur + 1:.2f}",
                         "-i", "anullsrc=r=48000:cl=stereo"]
        next_idx = 2
    for item in edl.get("music", []):
        local = os.path.join(workdir, f"music_{next_idx}"
                             + os.path.splitext(item["storage_key"])[1])
        storage.download_to(item["storage_key"], local)
        extra_inputs += ["-i", local]
        music_inputs.append((next_idx, item))
        next_idx += 1

    graph = build_filtergraph(edl, src_dur, info["has_audio"], tl, ass_path,
                              music_inputs, index, preview)

    if preview:
        encode = ["-c:v", "libx264", "-preset", config.PREVIEW_PRESET,
                  "-crf", "27", "-c:a", "aac", "-b:a", "128k"]
    else:
        encode = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
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
        return {"render_asset_id": asset_id, "sheet_key": sheet_key,
                "duration_s": out_dur, "edl_version": version,
                "variant": variant, "timings": timings}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
