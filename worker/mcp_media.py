"""The video itself, handed to the model on the other end of MCP (round 83).

WHY THIS EXISTS. Every other route by which an MCP caller can "see" this
footage ends in OUR words. `look_at` decodes frames, runs THEM through
Valmera's own vision model, and what crosses the wire is a paragraph — the
outside model reads a description of a picture it never saw. That was the only
honest option while every model on the far end read images at best: there is
no video content type in MCP and no client that wanted one.

That stopped being true. A model that can watch video does not need our
description of the footage, and taking one anyway is worse than useless — it
is a lossy summary standing between the editor and the material, written by a
smaller model, billed to us, and impossible to argue with. So this module does
the one thing the surface could not do: it hands over the FILE. Pixels, audio,
timing, the whole thing, and the caller forms its own opinion.

WHAT IT HANDS OVER, AND WHY IT USUALLY COSTS NOTHING. The artifacts already
exist and are already the right size:

  * the assembled program  -> the PREVIEW RENDER of the current EDL (~480p,
    H.264 + AAC) — literally what the studio player streams to the user;
  * the raw footage        -> the 540p INDEX PROXY, the same file every
    look_at, shot detection and preview render reads;
  * an uploaded clip       -> the user's own file.

So the normal answer is a presigned URL to an object that already exists: no
decode, no encode, no disk, no wait. Re-encoding is the EXCEPTION and happens
for exactly two reasons — the caller asked for a WINDOW (start/end) rather
than the whole thing, or asked for the bytes to come back INSIDE the tool
result, where a 60 MB file cannot go. Both are the caller's explicit request,
and both say so in the reply.

THE TIME BASE IS THE TRAP. A watched PROGRAM runs on OUTPUT seconds, and most
editing tools take SOURCE seconds; after one cut the two clocks disagree
everywhere. Every reply this module writes says which clock the video it just
handed over is on, and names the tools that speak it. Getting that wrong is
how a model that can finally see the edit still cuts the wrong second out.
"""

import hashlib
import os
import time

import config
import db as dbx
import media
import sheets
import storage

MIME = "video/mp4"

# What a re-encode aims at, largest first: (height, kbps that height wants).
# The byte budget picks the biggest rung it can actually pay for, so a tight
# budget spends itself on a smaller picture that is CLEAN rather than a large
# one that is mush — a video model reading a 240p frame beats one reading 540p
# of block noise.
LADDER = ((540, 900), (480, 700), (360, 450), (288, 320), (240, 220),
          (180, 140))

# Container/muxing overhead, and the floor below which the picture stops
# carrying information at any resolution.
_PAYLOAD_FRAC = 0.94
_MIN_VIDEO_KBPS = 90

KINDS = ("timeline", "source", "asset")


class Unavailable(RuntimeError):
    """Something the caller can act on — returned as text, never a traceback."""


def _mb(n):
    n = n or 0
    # "0.0 MB" on a 40 KB clip reads as an empty file, and the model then
    # reports a broken export.
    return (f"{n / 1024:.0f} KB" if n < 1048576 else f"{n / 1048576:.1f} MB")


def _sig(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts)
                          .encode("utf-8")).hexdigest()[:16]


def _num(args, name):
    """A number the caller may have sent as a string, or None. A model that
    types "start": "12.5" gets told which argument was wrong — not a
    ValueError traceback stored on a job row as "Fetching the video failed"."""
    v = args.get(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, not {v!r}.")


# ------------------------------------------------------------------ #
#  Which object                                                        #
# ------------------------------------------------------------------ #

def _preview_for_watching(ctx, render):
    """The preview render to hand over, rendering one if the current edit has
    never been rendered.

    `render=False` takes whatever preview exists, however stale, and says how
    stale — which is the right answer when the caller is asking "what did the
    last render look like", and the wrong one when it is asking "what does the
    edit look like now". The default is the second question."""
    row = ctx.latest_edl()
    version = row["version"]
    asset = ctx.db.run(dbx.find_render_asset, ctx.project_id, "preview",
                       version)
    if asset:
        return asset, version, ""

    stale = ctx.db.run(dbx.latest_render, ctx.project_id, "preview")
    if not render:
        if not stale:
            raise Unavailable(
                "Nothing has been rendered in this project yet, so there is "
                "no program to watch. Call render_preview (or watch_video "
                "again without render=false) and the current edit becomes a "
                "file.")
        old = (stale.get("meta") or {}).get("edl_version")
        return stale, old, (
            f" NOTE: this is the render of EDL v{old}, and the edit is now at "
            f"v{version} — every change since v{old} is NOT in what you are "
            "watching.")

    job_id = ctx.db.run(dbx.enqueue_job, ctx.project_id, ctx.job["user_id"],
                        "preview", {"edl_version": version})
    deadline = time.time() + config.PREVIEW_WAIT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1)
        j = ctx.db.run(dbx.get_job, job_id)
        if j["state"] == "done":
            break
        if j["state"] == "failed":
            raise Unavailable(
                f"The current edit (v{version}) could not be rendered: "
                f"{j.get('error') or 'unknown error'}. Nothing is wrong with "
                "the EDL you built — try render_preview to see the same error "
                "in its own right.")
    else:
        raise Unavailable(
            f"Rendering v{version} is job {job_id} and it is still going. "
            f"Call wait_for_job(job_id={job_id}), then watch_video again — it "
            "will find the finished file instead of starting a second render.")

    asset = ctx.db.run(dbx.find_render_asset, ctx.project_id, "preview",
                       version)
    if not asset:
        raise Unavailable(
            f"The render of v{version} finished but left no file to watch. "
            "Call render_preview and read what it says.")
    return asset, version, " (rendered just now for this call)"


def _source_object(ctx):
    """The raw footage: the index proxy, or the original when there is none."""
    proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
    if proxy:
        return proxy, "the 540p analysis proxy of the uploaded footage"
    original = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
    if not original:
        raise Unavailable(
            "This project has no main video — it is a canvas program built "
            "out of placed assets. Watch the timeline instead "
            "(kind=\"timeline\"), or one asset with kind=\"asset\".")
    return original, ("the ORIGINAL upload — this project has no proxy yet, "
                      "so it is full size")


def _asset_object(ctx, asset_key):
    # Imported here, not at module scope: mcp_media is a leaf that the tests
    # exercise on its own arithmetic, and agent_tools pulls in the entire
    # editor (ffmpeg wrappers, PIL, the renderer) to answer one lookup.
    import agent_tools
    if not asset_key:
        raise Unavailable(
            "kind=\"asset\" needs asset_key — the storage_key of an uploaded "
            "clip or a render, as list_assets prints it.")
    asset, err = agent_tools._resolve_media_asset(
        ctx, asset_key, ("video_clip", "render"))
    if err:
        raise Unavailable(err)
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    return asset, f"the uploaded clip \"{name}\""


# ------------------------------------------------------------------ #
#  Encoding, when it is needed at all                                  #
# ------------------------------------------------------------------ #

def encode_plan(duration, budget_bytes, src_height, max_height, src_fps):
    """(height, video_kbps, fps, tight) for a copy that lands under the budget.

    `tight` means the budget could not pay for the smallest rung on the
    ladder: the encode still happens, at the floor, and the reply says the
    picture is degraded rather than quietly shipping mush as if it were the
    footage."""
    duration = max(float(duration or 0), 0.1)
    total_kbps = (budget_bytes * 8.0 / 1000.0) / duration
    video_kbps = total_kbps * _PAYLOAD_FRAC - config.MCP_VIDEO_AUDIO_KBPS

    ceiling = min(config.MCP_VIDEO_HEIGHT, int(max_height or 10 ** 6))
    if src_height:
        ceiling = min(ceiling, int(src_height))     # never up-scale
    fps = min(float(src_fps or config.MCP_VIDEO_FPS_CAP),
              config.MCP_VIDEO_FPS_CAP)

    for height, wants in LADDER:
        if height > ceiling:
            continue
        if wants <= video_kbps:
            return height, int(max(video_kbps, wants)), fps, False
    floor = min(ceiling, LADDER[-1][0])
    return floor, int(max(video_kbps, _MIN_VIDEO_KBPS)), fps, True


def _encode(src, dst, *, start, duration, height, video_kbps, fps, has_audio):
    """One small H.264 + AAC copy of a window. CRF for quality, VBV for the
    ceiling: CRF alone cannot promise a size and a fixed bitrate wastes bits
    on a locked-off talking head, so both are set and the cheaper one wins."""
    cmd = ["ffmpeg", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vf", rf"scale=-2:min({height}\,floor(ih/2)*2)",
            "-r", f"{fps:.3f}",
            "-c:v", "libx264", "-preset", config.MCP_VIDEO_PRESET,
            "-crf", str(config.MCP_VIDEO_CRF),
            "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
            "-pix_fmt", "yuv420p"]
    # The audio is half of what a video model is being asked to judge — what
    # was said, where the music lands, whether a cut lands mid-word. It is
    # never dropped to save bytes.
    cmd += (["-c:a", "aac", "-b:a", f"{config.MCP_VIDEO_AUDIO_KBPS}k",
             "-ac", "2"] if has_audio else ["-an"])
    cmd += ["-movflags", "+faststart", dst]
    media.run(cmd, timeout=max(600.0, (duration or 0) * 3 + 120))


# ------------------------------------------------------------------ #
#  The tool                                                            #
# ------------------------------------------------------------------ #

def _clock_note(kind, start):
    if kind == "timeline":
        base = ("TIME BASE: this is the ASSEMBLED PROGRAM, so every second "
                "you read off it is an OUTPUT second of the current edit — "
                "not a source timestamp. Tools that speak output seconds "
                "directly: cut_output_range, look_at(output_times=[...]), "
                "and the scene map in project_state (it prints each scene's "
                "output window beside where its pixels come from). Convert "
                "through that map before calling anything that takes source "
                "seconds.")
    elif kind == "source":
        base = ("TIME BASE: this is the SOURCE footage, so its seconds are "
                "source seconds — the same ones the transcript, get_shots "
                "and every cutting tool use. It is the whole recording, "
                "including everything the current edit cuts out.")
    else:
        base = ("TIME BASE: seconds here are offsets INTO THIS CLIP, which is "
                "what insert_media's clip_start takes.")
    if start:
        base += (f" This file starts at {start:.2f}s of that timeline — add "
                 f"{start:.2f} to anything you read off it.")
    return base


def prepare(ctx, args, inline_max_bytes):
    """Resolve, and only if it has to, shrink — the video the caller watches.

    Returns {"text": ..., "video": {...}} for the backend to turn into MCP
    content. `video.storage_key` is presigned THERE, not here: the worker's S3
    endpoint can be the internal one, and a URL the caller cannot reach is
    worse than no URL."""
    kind = (args.get("kind") or "timeline").strip().lower()
    if kind not in KINDS:
        return {"text": f"kind must be one of: {', '.join(KINDS)}.",
                "is_error": True}
    try:
        start = _num(args, "start")
        end = _num(args, "end")
        max_mb = _num(args, "max_mb")
        max_height = _num(args, "max_height")
    except ValueError as e:
        return {"text": f"REJECTED: {e}", "is_error": True}

    # Hidden resolved fields are transport data, never caller authority. An
    # MCP client can send arbitrary JSON arguments, so honor them only inside
    # the Modal executor process that our dispatcher launched.
    resolved = (args.get("_resolved_asset")
                if os.getenv("EXECUTOR_PROVIDER") == "modal" else None)
    if isinstance(resolved, dict) and resolved.get("storage_key"):
        # The warm dispatcher already resolved the current preview/source and
        # waited for any prerequisite render. Modal receives only JSON-safe
        # file facts and goes straight to the encode.
        asset = resolved
        what = str(args.get("_resolved_what") or "the requested video")
    else:
        resolved = None
        try:
            if kind == "timeline":
                asset, version, note = _preview_for_watching(
                    ctx, args.get("render") is not False)
                what = (f"the assembled program — preview render of EDL "
                        f"v{version}{note}")
            elif kind == "source":
                asset, what = _source_object(ctx)
            else:
                asset, what = _asset_object(ctx, args.get("asset_key"))
        except Unavailable as e:
            return {"text": str(e), "is_error": True}

    key = asset["storage_key"]
    full_duration = float(asset.get("duration_s") or 0) or None
    src_height = asset.get("height")
    src_fps = asset.get("fps")
    nbytes = asset.get("bytes") or storage.object_bytes(key)

    # The window, if any.
    if start is not None:
        start = max(0.0, start)
    if end is not None and full_duration:
        end = min(end, full_duration)
    if start is not None and end is not None and end <= start:
        return {"text": f"end ({end}) must be after start ({start}).",
                "is_error": True}
    windowed = start is not None or end is not None
    win_start = start or 0.0
    win_dur = ((end - win_start) if end is not None
               else ((full_duration - win_start) if full_duration else None))

    # Explicit budget beats everything; otherwise a URL may point at anything
    # up to the no-re-encode ceiling, and embedding is only offered when the
    # file already fits.
    delivery = (args.get("delivery") or "auto").strip().lower()
    if max_mb:
        budget = int(max_mb * 1048576)
    elif delivery == "inline":
        budget = inline_max_bytes
    else:
        budget = int(config.MCP_VIDEO_URL_MAX_MB * 1048576)
    # A LINK has no size problem; only an embedded blob does. Once the caller
    # has said "just give me the URL", the untouched object is the right
    # answer at any size — MCP_VIDEO_URL_MAX_MB exists to stop the DEFAULT
    # handing back a 3 GB link nobody asked for, and does not apply here.
    # (budget stays a real number: it still feeds the encoder if a window
    # forces one, and `-maxrate infk` is not a bitrate.)
    untouched_ok = delivery == "url" and not max_mb

    want_frames = args.get("frames") is not False
    n_frames = int(_num(args, "frame_count") or config.MCP_WATCH_FRAMES)

    # ── The path that costs nothing: the object as it already is ──────────
    if not windowed and key.lower().endswith(".mp4") and nbytes \
            and (untouched_ok or nbytes <= budget):
        seen, heard, note = (_look(ctx, key, nbytes, full_duration, 0.0,
                                   n_frames) if want_frames else (0, None, ""))
        return _answer(ctx, kind, key, what, nbytes, full_duration, win_start,
                       src_height, transcoded=False,
                       inline_max_bytes=inline_max_bytes, delivery=delivery,
                       extra=note, frames=seen, audio=heard)

    if not win_dur and full_duration:
        win_dur = full_duration - win_start
    if win_dur and win_dur > config.MCP_VIDEO_MAX_ENCODE_S:
        return {"text": (
            f"That is {win_dur / 60:.0f} minutes to re-encode, and this call "
            f"re-encodes at most {config.MCP_VIDEO_MAX_ENCODE_S / 60:.0f} "
            "minutes at a time. Ask for a window (start/end), or for the file "
            "untouched with delivery=\"url\" and no max_mb — a link has no "
            "limit at all."), "is_error": True}

    if nbytes and nbytes > config.MCP_VIDEO_DOWNLOAD_MAX_MB * 1048576:
        return {"text": (
            f"That file is {_mb(nbytes)} and making a smaller copy means "
            "pulling all of it onto the editing box first, which this call "
            "will not do. Ask for it untouched instead — delivery=\"url\", no "
            "max_mb — and you get a link to the original, or wait for the "
            "analysis to finish and watch the proxy."), "is_error": True}

    # Resolve and wait on the always-on dispatcher, then rent Modal only for
    # the byte-heavy part. Sending the whole queue job to Modal made a timeline
    # watch pay for ~100 seconds of idle preview wait before a short encode.
    if resolved is None:
        import remote
        if remote.mcp_media_available():
            remote_args = dict(args)
            remote_args["_resolved_asset"] = {
                field: asset.get(field) for field in
                ("storage_key", "bytes", "duration_s", "height", "fps")
            }
            remote_args["_resolved_what"] = what
            return remote.run_mcp_media_remote(
                ctx.project_id,
                {"tool": "__media__", "args": remote_args},
                user_id=(ctx.job or {}).get("user_id"))

    # The source has to be here before an unknown duration can be resolved, so
    # a clip the browser never measured is downloaded first and planned after.
    # Everything downstream — the encode signature, the cache key, the reply —
    # then reads ONE plan, made against numbers that are actually true.
    local = os.path.join(ctx.workdir, f"watch_src_{_sig(key)}"
                         + (os.path.splitext(key)[1] or ".mp4"))
    info = None
    if not win_dur:
        if not os.path.exists(local):
            storage.download_to(key, local)
        try:
            info = media.probe(local)
        except media.MediaError as e:
            return {"text": f"That file could not be read ({e}).",
                    "is_error": True}
        win_dur = max(0.1, info["duration"] - win_start)
        src_height, src_fps = info["height"], info["fps"]
        if win_dur > config.MCP_VIDEO_MAX_ENCODE_S:
            return {"text": (
                f"That file turned out to be {win_dur / 60:.0f} minutes long, "
                f"past the {config.MCP_VIDEO_MAX_ENCODE_S / 60:.0f}-minute "
                "limit on one re-encode. Ask for a window (start/end), or for "
                "the file untouched with delivery=\"url\"."), "is_error": True}

    height, video_kbps, fps, tight = encode_plan(
        win_dur, budget, src_height, max_height, src_fps)

    out_key = (f"media/{ctx.project_id}/"
               f"mv_{_sig(key, win_start, win_dur, height, video_kbps, fps)}.mp4")
    if not storage.exists(out_key):
        if not os.path.exists(local):
            storage.download_to(key, local)
        if info is None:
            try:
                info = media.probe(local)
            except media.MediaError as e:
                return {"text": f"That file could not be read ({e}).",
                        "is_error": True}
        out_local = os.path.join(ctx.workdir, os.path.basename(out_key))
        try:
            _encode(local, out_local, start=win_start, duration=win_dur,
                    height=height, video_kbps=video_kbps, fps=fps,
                    has_audio=info["has_audio"])
        except media.MediaError as e:
            return {"text": (f"Could not produce a watchable copy ({e}). The "
                             "footage itself is fine — this is the shrink "
                             "step, not the edit."), "is_error": True}
        storage.upload_file(out_local, out_key, MIME)
        nbytes = os.path.getsize(out_local)
        try:
            os.remove(out_local)
        except OSError:
            pass
    else:
        nbytes = storage.object_bytes(out_key)

    extra = f" Re-encoded to {height}p"
    extra += (f", {win_dur:.1f}s window from {win_start:.2f}s"
              if windowed else " to fit the size you asked for")
    extra += "."
    if tight:
        extra += (" That budget is below what this length needs, so the "
                  "picture is degraded — judge framing and motion from it, "
                  "not fine detail. A shorter window (start/end) buys back "
                  "the quality.")
    # Pictures and sound come off the SOURCE with the window's offset, not off
    # the shrunk copy: same moments, full quality, already on disk.
    seen, heard, note = ((0, None, "") if not (want_frames
                                               and os.path.exists(local))
                         else _perceive(ctx, local, win_dur, win_start,
                                        n_frames, True, True))
    return _answer(ctx, kind, out_key, what, nbytes, win_dur, win_start,
                   height, transcoded=True, inline_max_bytes=inline_max_bytes,
                   delivery=delivery, extra=extra + note, frames=seen,
                   audio=heard)


def _filmstrip(ctx, local, duration, start, count):
    """Evenly-spaced frames across the window, as ONE labeled sheet, queued
    for the caller's own eyes.

    This is what "watch it" has to mean while MCP has no video content type
    and the one client we have proves a blob is worse than useless. The model
    asked to see the program; it gets the program's pixels, in its own
    context, in the same call — instead of a link and an afternoon of ffmpeg.
    Labels are OUTPUT seconds of the real timeline (start + offset), so a
    moment read off a tile can be handed straight to a tool."""
    n = max(2, min(int(count), 20))
    span = max(float(duration or 0), 0.1)
    times = [span * (i + 0.5) / n for i in range(n)]
    frames, labels = [], []
    for i, t in enumerate(times):
        fp = os.path.join(ctx.workdir, f"watch_{_sig(local, t)}_{i}.jpg")
        try:
            media.frame_at(local, t, fp, width=640)
        except media.MediaError:
            continue
        frames.append(fp)
        labels.append(f"@{start + t:.2f}s")
    if not frames:
        return 0
    sheet = os.path.join(ctx.workdir, f"watch_sheet_{_sig(local, start)}.jpg")
    try:
        sheets.build_timestamp_sheet(list(zip(labels, frames)), sheet)
    except Exception:
        return 0
    ctx.pending_images.append(
        (f"The program, {len(frames)} moments across "
         f"{start:.2f}-{start + span:.2f}s", sheet))
    return len(frames)


def audio_plan(duration):
    """(kbps, max_seconds) for the sound of a window this long, or (0, max).

    Sound is cheap next to picture, which is the entire reason it can ride in
    the reply when the video cannot — but only up to a point, and past that
    point the honest move is to say so rather than ship a track too thin to
    hear or a silently truncated one."""
    budget_bits = config.MCP_AUDIO_MAX_KB * 1024 * 8
    ceiling = budget_bits / 1000.0 / max(float(duration or 0), 0.1)
    max_s = budget_bits / 1000.0 / config.MCP_AUDIO_MIN_KBPS
    if ceiling < config.MCP_AUDIO_MIN_KBPS:
        return 0, max_s
    return int(min(config.MCP_AUDIO_MAX_KBPS, ceiling)), max_s


def _audio_clip(ctx, local, duration, start):
    """The window's audio as a small mono mp3 in storage -> (dict, note)."""
    if not config.MCP_AUDIO_OUT or not duration:
        return None, ""
    try:
        if not media.has_audio_stream(local):
            return None, " This program has no audio track at all — silence."
    except Exception:
        return None, ""
    kbps, max_s = audio_plan(duration)
    if not kbps:
        return None, (
            f" The SOUND is not attached: {duration:.0f}s at a listenable "
            f"bitrate is over what a reply can carry. Ask for a window "
            f"(start/end) of about {max_s:.0f}s or less and you get the audio "
            "with it.")
    key = (f"media/{ctx.project_id}/"
           f"aud_{_sig(local, start, duration, kbps)}.mp3")
    out = os.path.join(ctx.workdir, os.path.basename(key))
    if not storage.exists(key):
        cmd = ["ffmpeg", "-y"]
        if start:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", local, "-t", f"{duration:.3f}", "-vn",
                "-ac", "1", "-ar", "32000", "-c:a", "libmp3lame",
                "-b:a", f"{kbps}k", out]
        try:
            media.run(cmd, timeout=max(120.0, duration * 2))
        except media.MediaError as ex:
            print(f"[mcp] no audio attached ({ex})", flush=True)
            return None, ""
        try:
            storage.upload_file(out, key, "audio/mpeg")
        except Exception as ex:
            print(f"[mcp] audio upload failed ({ex})", flush=True)
            return None, ""
        nbytes = os.path.getsize(out)
    else:
        nbytes = storage.object_bytes(key)
    return ({"storage_key": key, "mime": "audio/mpeg", "bytes": nbytes,
             "seconds": round(float(duration), 3), "kbps": kbps}, "")


def _perceive(ctx, local, duration, start, count, want_frames, want_audio):
    """Pictures and sound off ONE local copy — (frames, audio, note)."""
    frames = (_filmstrip(ctx, local, duration, start, count)
              if want_frames else 0)
    audio, note = (_audio_clip(ctx, local, duration, start)
                   if want_audio else (None, ""))
    return frames, audio, note


def _look(ctx, key, nbytes, duration, start, count):
    """Filmstrip for the untouched path, which otherwise never touches the
    bytes at all. Fetches once per session (the context outlives the call),
    and declines rather than dragging a huge original onto the box for
    pictures — the link still works, and the reply says which happened."""
    if not duration:
        return 0, None, ""
    if nbytes and nbytes > config.MCP_VIDEO_DOWNLOAD_MAX_MB * 1048576:
        return 0, None, ""
    local = os.path.join(ctx.workdir, f"watch_src_{_sig(key)}"
                         + (os.path.splitext(key)[1] or ".mp4"))
    try:
        if not os.path.exists(local):
            storage.download_to(key, local)
    except Exception as ex:
        print(f"[mcp] no filmstrip, could not fetch {key} ({ex})", flush=True)
        return 0, None, ""
    return _perceive(ctx, local, duration, start, count, True, True)


def _answer(ctx, kind, key, what, nbytes, duration, start, height,
            *, transcoded, inline_max_bytes, delivery, extra, frames=0,
            audio=None):
    # EMBEDDING IS OPT-IN, and this line is the whole reason (Aug 3 2026).
    # It used to embed whenever the file happened to fit, on the assumption
    # that a client which cannot render a video block would ignore it. It does
    # not — it STRINGIFIES it. The first real call handed Grok a 2.9 MB
    # preview, which arrived as 4 MILLION characters of base64 in the
    # conversation and ended the session ("the conversation is too long"),
    # while the tool reported success. Wrong-by-default costs one extra step
    # here and costs the entire session there, so only an explicit
    # delivery="inline" — from a caller that knows its own model reads video —
    # ever puts bytes in the reply.
    inline = delivery == "inline" and bool(nbytes) and nbytes <= inline_max_bytes
    lines = [f"Here is {what}."]
    if extra:
        lines.append(extra.strip())
    lines.append(f"{duration:.1f}s" if duration else "full length")
    lines[-1] += (f", {height}p" if height else "")
    lines[-1] += f", H.264 + AAC, {_mb(nbytes)}."
    if frames or audio:
        got = []
        if frames:
            got.append(f"{frames} FRAMES on one sheet, evenly spaced and in "
                       "order, each tile labelled with its second")
        if audio:
            got.append(f"the COMPLETE SOUND of it — {audio['seconds']:.1f}s "
                       "of continuous audio, every note of the music and "
                       "anything else audible, nothing sampled or summarised")
        lines.append(
            "WHAT FOLLOWS THIS MESSAGE IS THE PROGRAM: " + " and ".join(got)
            + ". Look and listen yourself and answer from that. You do not "
            "need to download the file, extract frames, decode audio or run "
            "any tool to know what is in this program — it is already in "
            "front of you.")
        # Say exactly what it is NOT, in the same breath. This reply used to
        # call a contact sheet "the video", and a model repeated that back as
        # having received pixels AND audio when it had only pictures. A tool
        # that overstates what it handed over teaches the model to overstate
        # it to the user.
        lines.append(
            "BE PRECISE ABOUT THIS IF YOU ARE ASKED: the audio is complete "
            "and continuous; the picture is " + (f"{frames} sampled moments"
                                                 if frames else "not attached")
            + ", not every frame at full rate. You are seeing stills and "
            "hearing the whole track. If motion between two tiles matters, "
            "say so, or ask for a narrower start/end — the same call over 5 "
            "seconds puts the tiles a few frames apart.")
    lines.append(_clock_note(kind, start))
    if inline:
        lines.append("The video FOLLOWS THIS MESSAGE as an attachment — watch "
                     "it and answer from what you actually see and hear. A "
                     "download link is below as well, in case your client "
                     "cannot play the attachment.")
    else:
        lines.append(
            "THE LINK BELOW IS THE VIDEO. It is a plain, unauthenticated GET "
            "of an ordinary MP4 — fetch it and watch it with whatever your "
            "model uses for video. This is the right way to get it, not a "
            "fallback.")
        if nbytes and nbytes <= inline_max_bytes:
            lines.append(
                "It is small enough to be embedded in the reply itself "
                f"({_mb(nbytes)}) — but ONLY ask for that (delivery="
                "\"inline\") if your client decodes video content blocks "
                "natively. A client that cannot will turn the file into "
                "millions of characters of base64 in this conversation and "
                "run out of context.")
    return {"text": "\n".join(lines),
            "audio": audio,
            "video": {"storage_key": key, "mime": MIME, "bytes": nbytes,
                      "duration_s": round(duration, 3) if duration else None,
                      "height": height, "start_s": round(start, 3),
                      "kind": kind, "transcoded": transcoded,
                      "inline": inline}}
