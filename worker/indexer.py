"""Perception pipeline (job type "index"): video file -> JSON index.

Runs ONCE per unique file (sha256-keyed cache). Since v10 the visual record
is a FILMSTRIP OF TILES — labeled 2x2 frame grids sampled on a clock
(worker/tiles.py) that the agent reads with its own eyes every turn — plus a
word-level transcript when the audio carries speech. There is no vision-
captioning stage any more: no scene prose, no per-sheet model calls, no
TPM ceiling, and the minutes an index used to spend describing frames are
gone.

EVERY video gets the same treatment: the main footage and every uploaded
clip run through this same job (clips skip the proxy encode, the EDL seeding
and the chat greeting — they are perception-only).

Steps (main video): probe, then TWO LANES side by side — picture (proxy
encode -> shots + tiles + proxy upload) and sound (wav -> transcription ->
silences -> perception, with the wav upload overlapped) — then assemble
VideoIndex -> seed EDL + greet. Steps (clip): the same lanes minus the
proxy encode/EDL/greeting — clips are perception-only.
"""

import os
import re
import shutil
import time
from concurrent import futures

import audit
import config
import db as dbx
import llm
import media
import scenes
import spatial
import storage
import tiles as tilestrip
import transcribe
from schemas import (VideoIndex, VideoInfo, clamp_word_times,
                     default_edl, is_canvas_program, keep_boundaries,
                     validate_edl)


PROGRESS_EVERY_S = 5.0


def _cancelled(worker_db):
    cb = getattr(worker_db, "cancelled", None)
    return bool(cb and cb())


def _set_progress(worker_db, job_id, pct):
    """Progress is also the executor lease heartbeat; never discard `False`."""
    if worker_db.run(dbx.set_progress, job_id, pct) is False:
        raise dbx.JobLeaseLost(
            f"job {job_id} was cancelled or handed to another worker")


def _stage_progress(worker_db, job_id, lo, hi):
    """Map one ffmpeg stage's 0..1 onto the job's lo..hi band.

    Throttled: ffmpeg emits -progress a couple of times a SECOND, and this
    lane's connection is the same one the stage's own DB work uses — writing
    every line would be ~1800 UPDATEs for a single long proxy encode.
    """
    last = [0.0]

    def cb(frac):
        now = time.monotonic()
        if now - last[0] < PROGRESS_EVERY_S:
            return
        last[0] = now
        pct = lo + int(round((hi - lo) * max(0.0, min(1.0, frac))))
        try:
            _set_progress(worker_db, job_id, pct)
        except Exception:
            # Never unwind out of ffmpeg's pipe reader: doing so can leave the
            # child alive. Lease loss is already recorded on _LeasedDb, and
            # media.run's watchdog observes that flag and kills/reaps it.
            pass

    return cb


def _index_has_tiles(idx):
    """v10 usability test: the strip exists and its first tile is readable.
    A cached row whose tiles were deleted from storage re-indexes rather
    than serving a filmstrip of dead keys."""
    keys = (idx or {}).get("tile_keys") or []
    if not keys:
        return False
    try:
        return storage.exists(keys[0])
    except Exception:
        return False


def _transcribe_wav(job_id, wav_local, duration, warnings):
    """words, sentences, language — with one retry on a transient crash."""
    words, sentences, language = [], [], None
    if not wav_local:
        return words, sentences, language
    for attempt in range(2):
        try:
            words, language = transcribe.transcribe(wav_local, warnings)
            # ASR on music invents timings past the end of the file — clamp
            # before sentences inherit the bad spans.
            words = clamp_word_times(words, duration)
            sentences = transcribe.group_sentences(words)
            break
        except Exception as e:
            if attempt == 0:
                print(f"[index {job_id}] transcription failed "
                      f"({str(e)[:120]}); retrying once", flush=True)
                continue
            raise
    return words, sentences, language


def _detect_silences(job_id, wav_local, duration, warnings):
    silences = []
    if wav_local:
        try:
            silences = media.detect_silences(wav_local, duration)
        except Exception as e:
            warnings.append(f"silence detection failed ({str(e)[:120]}) — "
                            "silence-based trimming may be less accurate")
            print(f"[index {job_id}] silence detection degraded: {e}",
                  flush=True)
    return silences


def _build_and_upload_tiles(job_id, project_id, sha, src_path, duration,
                            workdir, warnings, seek_ceiling=None):
    """Filmstrip tiles from src_path -> uploaded keys. Returns
    (tile_keys, tile_step_s, spatial_sidecar). Degrades to an empty strip with a warning —
    the transcript and cut points are unaffected."""
    tile_dir = os.path.join(workdir, "tiles")
    try:
        built, step, sample_times, sample_frames = \
            tilestrip.build_for_video_with_frames(
            src_path, duration, tile_dir, seek_ceiling=seek_ceiling,
            parallelism=config.THUMB_PARALLELISM)
    except Exception as e:
        warnings.append(f"filmstrip build failed ({str(e)[:120]}) — the "
                        "video has no visual strip; use look_at instead")
        print(f"[index {job_id}] filmstrip degraded: {e}", flush=True)
        return [], None, None
    if not built:
        warnings.append("filmstrip build produced no tiles — the video has "
                        "no visual strip; use look_at instead")
        return [], None, None
    try:
        spatial_sidecar = spatial.analyze_frames(
            sample_times, sample_frames,
            max_samples=spatial.FILMSTRIP_SAMPLE_BUDGET)
    except Exception as e:
        spatial_sidecar = None
        warnings.append(f"spatial analysis failed ({str(e)[:120]}) — face/"
                        "source-text safety will analyze on first use")

    jobs = [(tp, f"tiles/{project_id}/{sha}/tile_{i:03d}.jpg")
            for i, (tp, _t0, _t1) in enumerate(built, start=1)]
    ok = {}
    with futures.ThreadPoolExecutor(
            max_workers=config.UPLOAD_PARALLELISM) as pool:
        futs = {pool.submit(storage.upload_file, tp, key, "image/jpeg"):
                (i, key) for i, (tp, key) in enumerate(jobs)}
        for fut, (i, key) in futs.items():
            try:
                fut.result()
                ok[i] = key
            except Exception as e:
                print(f"[index {job_id}] tile upload failed ({key}): {e}",
                      flush=True)
    # Keys stay POSITIONAL (time order); a failed upload drops the tail
    # coverage warning in, never a hole that mislabels later tiles.
    keys = []
    for i in range(len(jobs)):
        if i in ok:
            keys.append(ok[i])
        else:
            break
    if len(keys) < len(jobs):
        warnings.append(f"only {len(keys)} of {len(jobs)} filmstrip tiles "
                        "uploaded — the strip ends early; use look_at for "
                        "the rest")
    return keys, step, spatial_sidecar


def run_index_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    asset = worker_db.run(dbx.get_asset, job["payload"].get("asset_id"))
    if not asset:
        raise RuntimeError("Original asset not found")
    if asset["kind"] in ("video_clip", "music"):
        return _run_clip_index(worker_db, job, asset)
    project = worker_db.run(dbx.get_project, project_id)
    session_id = project["chat_session_id"]

    workdir = os.path.join(config.TMP_DIR, f"index_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    timings, _t = {}, time.monotonic()

    def _mark(stage):
        nonlocal _t
        timings[stage] = round(time.monotonic() - _t, 2)
        _t = time.monotonic()

    # DEDUP UPLOADS (round 67d). The browser recognized bytes this user
    # already uploaded (backend /uploads/dedup) and never transferred them:
    # this asset points at an object that exists only once WE copy it,
    # server-side, from the earlier upload. Before anything reads the key,
    # and idempotent — a reaped retry that finds the object copied skips
    # straight through. A missing source is an honest failure the studio
    # surfaces, and the user can simply upload normally.
    dedup_src = (job["payload"].get("dedup_src") or "").strip()
    if dedup_src:
        if not storage.exists(asset["storage_key"]):
            if not storage.exists(dedup_src):
                raise RuntimeError(
                    "The earlier upload this project reuses is gone from "
                    "storage — please upload the file again")
            _set_progress(worker_db, job_id, 3)
            storage.copy_object(dedup_src, asset["storage_key"])
        worker_db.run(dbx.asset_upload_ready, asset["id"])

    # PROXY-FIRST UPLOADS. When the browser could transcode, it sent a 540p
    # proxy in seconds and the multi-GB original is still streaming up in the
    # background — it is not needed until export. Indexing that proxy is what
    # takes "editable" from 24 minutes to under a minute on a 4K recording.
    #
    # The original ALWAYS wins when it is actually there. That single rule is
    # what makes every retry self-healing: a re-index that runs after the
    # background upload lands takes the full, trusted path with no special
    # case, and a client proxy is only ever used while there is no alternative.
    client_proxy_key = (job["payload"].get("client_proxy_key") or "").strip()
    from_client_proxy = bool(client_proxy_key) and \
        not storage.exists(asset["storage_key"])
    if client_proxy_key and not from_client_proxy:
        print(f"[index {job_id}] original has landed — indexing from it "
              f"rather than the browser proxy", flush=True)

    try:
        warnings_pre = []
        if from_client_proxy:
            # 1'. Pull the browser's proxy and adopt it as ours.
            raw = os.path.join(workdir, "client_proxy_raw.mp4")
            storage.download_to(client_proxy_key, raw)
            _set_progress(worker_db, job_id, 8)
            _mark("download_s")
            adopted = os.path.join(workdir, "proxy.mp4")
            proxy_info = media.adopt_client_proxy(raw, adopted, warnings_pre)
            try:
                os.remove(raw)
            except OSError:
                pass
            src = adopted
            sha = media.sha256_file(adopted)
            _mark("sha256_s")

            # The asset row carries what the BROWSER measured of the original:
            # its real duration and its DISPLAY geometry. Keep those — the
            # proxy is 540p by construction, and describing a 4K recording as
            # 960x540 would be wrong everywhere it is shown.
            declared_dur = asset.get("duration_s") or 0
            dur = proxy_info["duration"]
            if declared_dur and abs(declared_dur - dur) > \
                    media.client_proxy_gap_tolerance(declared_dur):
                # The prepared video does not cover the recording it claims to.
                # Refuse rather than build an edit against footage that is not
                # all there — the original is on its way, and the studio's
                # own self-heal re-runs this job, which will then take the
                # branch above and index the real file.
                raise RuntimeError(
                    f"The prepared video is {dur:.1f}s but the recording is "
                    f"{declared_dur:.1f}s. Re-analysing from the original as "
                    "soon as it finishes uploading.")
            info = {
                "duration": dur,
                "video_duration": proxy_info.get("video_duration"),
                "width": asset.get("width") or proxy_info["width"],
                "height": asset.get("height") or proxy_info["height"],
                "fps": proxy_info["fps"],
                "has_audio": proxy_info["has_audio"],
                "vfr": False,          # adopt_client_proxy guarantees CFR
            }
        else:
            # 1. Pull original + hash it
            src = os.path.join(workdir,
                               "src" + os.path.splitext(asset["storage_key"])[1])
            storage.download_to(asset["storage_key"], src)
            _set_progress(worker_db, job_id, 8)
            _mark("download_s")
            sha = media.sha256_file(src)
            _mark("sha256_s")

            # 2. Probe (also enforces the duration quota)
            info = media.probe(src)

        if info["duration"] > config.MAX_DURATION_S:
            raise RuntimeError(
                f"Video is {info['duration']/3600:.1f}h — the limit is "
                f"{config.MAX_DURATION_S/3600:.0f}h")
        worker_db.run(dbx.update_asset_probe, asset["id"], info["duration"],
                      info["width"], info["height"], info["fps"], sha)
        _set_progress(worker_db, job_id, 12)

        proxy_key = f"proxies/{project_id}/{sha}.mp4"

        # Cache hit: this exact file was indexed before (any project) BY THE
        # CURRENT PIPELINE, and its filmstrip is still readable in storage.
        cached = worker_db.run(dbx.get_index_by_sha, sha)
        if cached and cached.get("pipeline_version", 1) == \
                config.PIPELINE_VERSION and _index_has_tiles(cached["json"]):
            _ensure_proxy(worker_db, project_id, sha, proxy_key, src, info,
                          workdir,
                          ready_proxy=src if from_client_proxy else None)
            _finish_setup(worker_db, project_id, session_id, info,
                          cached["json"], job["user_id"],
                          reindex=bool(job["payload"].get("reindex")),
                          asset_id=asset["id"])
            _mark("cache_hit_s")
            return {"sha256": sha, "cached": True,
                    "from_client_proxy": from_client_proxy,
                    "shots": len(cached["json"].get("shots", [])),
                    "words": len(cached["json"].get("words", [])),
                    "timings": timings}
        if cached:
            print(f"[index {job_id}] cached index for sha {sha[:12]} is "
                  f"stale (pipeline v{cached.get('pipeline_version', 1)} < "
                  f"v{config.PIPELINE_VERSION} or tiles missing) — "
                  "re-indexing", flush=True)

        # Non-fatal degradations recorded here and stored on the index so a
        # partially-degraded analysis is visible in admin instead of silently
        # worse.
        warnings = list(warnings_pre)

        # 3-8. TWO LANES, NOT A LADDER (round 91). The pipeline was strictly
        # serial — proxy, wav, whisper, silences, shots, tiles, uploads, one
        # after another — for stages whose dependency graph is actually two
        # independent chains: everything on the PICTURE side needs the proxy,
        # everything on the SOUND side needs the wav, and neither needs the
        # other. On the 8-vCPU executor that serialization was most of the
        # p50: 22s proxy + 10s whisper + 7s shots + 8s tiles + 18s uploads,
        # each waiting on all the others (measured, 240 prod jobs). The two
        # chains now run side by side and the uploads ride inside them, so
        # the wall time is max(picture, sound), not the sum.
        #
        # THE DB STAYS ON THIS THREAD. Db is one-connection-per-thread by
        # contract (db.py), so the lanes touch files and storage only; every
        # set_progress / insert_asset below runs here. Progress during the
        # lanes is a coarse main-thread ticker — the bar the user watches
        # keeps moving, and no lane ever holds a psycopg cursor.
        audio_key = None
        state = {"proxy_info": proxy_info if from_client_proxy else None}

        def _picture_lane():
            if from_client_proxy:
                proxy_local = src        # browser encoded it; adopted above
            else:
                proxy_local = os.path.join(workdir, "proxy.mp4")
                media.make_proxy(
                    src, proxy_local, info["fps"], info["vfr"],
                    info["has_audio"], duration=info["duration"],
                    cancelled_cb=lambda: _cancelled(worker_db))
                # Probed here, not at upload time: tile extraction needs the
                # proxy's REAL duration to keep frame seeks inside it.
                state["proxy_info"] = media.probe(proxy_local)
            state["proxy_local"] = proxy_local
            p_info = state["proxy_info"]
            # The index is about to describe this video to the agent, the
            # player and every cut. If the proxy still doesn't cover the
            # recording, say so rather than shipping a description of footage
            # that isn't there.
            proxy_have = p_info["video_duration"] or p_info["duration"]
            if info["duration"] - proxy_have > max(
                    media.PROXY_SHORT_MIN_S,
                    media.PROXY_SHORT_FRAC * info["duration"]):
                warnings.append(
                    f"only the first {proxy_have:.1f}s of this "
                    f"{info['duration']:.1f}s video has picture — the rest "
                    "has sound but no frames")
            state["proxy_done_at"] = time.monotonic()
            # Shots, tiles and the proxy upload all read the finished proxy
            # and nothing else — side by side.
            proxy_dur = p_info["duration"]
            ceil = max(0.0, proxy_dur - 0.05) if proxy_dur > 0 else None
            with futures.ThreadPoolExecutor(max_workers=3) as sub:
                f_shots = sub.submit(scenes.detect_shots, proxy_local,
                                     info["duration"], warnings)
                f_tiles = sub.submit(
                    _build_and_upload_tiles, job_id, project_id, sha,
                    proxy_local, info["duration"], workdir, warnings,
                    seek_ceiling=ceil)
                f_up = sub.submit(storage.upload_file, proxy_local,
                                  proxy_key, "video/mp4")
                state["shots"] = f_shots.result()
                (state["tile_keys"], state["tile_step"],
                 state["spatial"]) = f_tiles.result()
                try:
                    state["spatial"] = spatial.augment_with_shot_frames(
                        proxy_local, info["duration"], workdir,
                        state.get("spatial"), state["shots"])
                except Exception as e:
                    warnings.append(
                        f"shot-boundary spatial supplement failed "
                        f"({str(e)[:120]}) — coarse face/text track kept")
                try:
                    f_up.result()
                except Exception:
                    print(f"[index {job_id}] proxy upload failed", flush=True)
                    raise

        def _sound_lane():
            wav_local = None
            if info["has_audio"]:
                wav_local = os.path.join(workdir, "audio.wav")
                media.extract_wav(
                    src, wav_local,
                    cancelled_cb=lambda: _cancelled(worker_db))
            state["wav_local"] = wav_local
            state["wav_done_at"] = time.monotonic()
            up = None
            if wav_local:
                with futures.ThreadPoolExecutor(max_workers=1) as sub:
                    up = sub.submit(storage.upload_file, wav_local,
                                    f"audio/{project_id}/{sha}.wav",
                                    "audio/wav")
                    state["words"], state["sentences"], state["language"] = \
                        _transcribe_wav(job_id, wav_local, info["duration"],
                                        warnings)
                    state["silences"] = _detect_silences(
                        job_id, wav_local, info["duration"], warnings)
                    # Perception sidecar (round 35): beat grid / energy /
                    # speech stress from the wav that already exists.
                    # Non-fatal by the same contract as tiles: perception
                    # feeds DECISIONS, never renders.
                    try:
                        import perception as perception_mod
                        state["perception"] = perception_mod.analyze_audio(
                            wav_local)
                    except Exception as e:
                        warnings.append(
                            f"audio perception failed ({str(e)[:120]}) — "
                            "beat-synced and emphasis-driven edits will "
                            "analyze on first use")
                    try:
                        up.result()
                    except Exception:
                        print(f"[index {job_id}] audio upload failed",
                              flush=True)
                        raise
            else:
                state["words"], state["sentences"], state["language"] = \
                    _transcribe_wav(job_id, None, info["duration"], warnings)
                state["silences"] = _detect_silences(
                    job_id, None, info["duration"], warnings)

        with futures.ThreadPoolExecutor(max_workers=2) as lanes:
            f_pic = lanes.submit(_picture_lane)
            f_snd = lanes.submit(_sound_lane)
            # Coarse liveness ticker while the lanes work: 12 -> 90, a step
            # every 2s, from THIS thread. set_progress also refreshes the
            # job heartbeat, which is what keeps a long index claimable-safe.
            pct = 12
            while True:
                done, _pending = futures.wait((f_pic, f_snd), timeout=2.0)
                if len(done) == 2:
                    break
                pct = min(90, pct + 3)
                _set_progress(worker_db, job_id, pct)
            f_pic.result()                 # re-raise lane failures in order
            f_snd.result()

        proxy_info = state["proxy_info"]
        proxy_local = state["proxy_local"]
        wav_local = state.get("wav_local")
        shots = state["shots"]
        tile_keys, tile_step = state["tile_keys"], state["tile_step"]
        words, sentences = state["words"], state["sentences"]
        language, silences = state["language"], state["silences"]
        perception_sidecar = state.get("perception")
        spatial_sidecar = state.get("spatial")
        # Lane timings for the admin views: the two lanes overlap, so the
        # old per-stage ladder is now picture/sound walls plus their split.
        t_lanes = time.monotonic() - _t
        timings["proxy_s"] = round(
            (state.get("proxy_done_at") or _t) - _t, 2)
        timings["wav_s"] = round((state.get("wav_done_at") or _t) - _t, 2)
        timings["lanes_s"] = round(t_lanes, 2)
        _t = time.monotonic()
        print(f"[index {job_id}] filmstrip: {len(tile_keys)} tile(s), "
              f"step {tile_step}s, {len(shots)} shot(s)", flush=True)
        _set_progress(worker_db, job_id, 92)

        audio_key = f"audio/{project_id}/{sha}.wav" if wav_local else None
        worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                      bytes_=os.path.getsize(proxy_local),
                      duration_s=proxy_info["duration"],
                      width=proxy_info["width"], height=proxy_info["height"],
                      fps=proxy_info["fps"], sha256=sha)
        if audio_key:
            worker_db.run(dbx.insert_asset, project_id, "audio", audio_key,
                          bytes_=os.path.getsize(wav_local),
                          duration_s=info["duration"], sha256=sha)
        _set_progress(worker_db, job_id, 94)

        # 9. Assemble + persist the index
        speakers = len({w.speaker for w in words if w.speaker is not None})
        index = VideoIndex(
            video=VideoInfo(duration=info["duration"], fps=info["fps"],
                            width=info["width"], height=info["height"],
                            has_audio=info["has_audio"],
                            vfr_normalized=info["vfr"]),
            shots=shots,
            words=words,
            sentences=sentences,
            silences=silences,
            tile_keys=tile_keys,
            tile_step_s=tile_step,
            speakers=speakers,
            language=language,
            warnings=warnings,
            perception=perception_sidecar,
            spatial=spatial_sidecar,
        ).model_dump()
        worker_db.run(dbx.upsert_index, project_id, sha, index)
        _finish_setup(worker_db, project_id, session_id, info, index,
                      job["user_id"],
                      reindex=bool(job["payload"].get("reindex")),
                      asset_id=asset["id"])
        _mark("upload_persist_s")
        return {"sha256": sha, "cached": False, "shots": len(shots),
                "from_client_proxy": from_client_proxy,
                "tiles": len(tile_keys), "speakers": speakers,
                "words": len(words), "silences": len(silences),
                "language": language, "warnings": warnings,
                "timings": timings}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _run_clip_index(worker_db, job, asset):
    """Perception for a NON-MAIN upload — a spliced clip or a music file.

    Exactly the same senses as the main video (tiles + transcript + silences
    + shots), minus everything that belongs to the program: no proxy encode,
    no EDL seeding, no greeting. Music files get transcript/silences only.
    The result is stored in `indexes` by sha, and the asset is stamped
    indexed so every reader (state, agent context) can find it."""
    job_id, project_id = job["id"], job["project_id"]
    is_music = asset["kind"] == "music"
    workdir = os.path.join(config.TMP_DIR, f"index_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    timings, _t = {}, time.monotonic()

    def _mark(stage):
        nonlocal _t
        timings[stage] = round(time.monotonic() - _t, 2)
        _t = time.monotonic()

    try:
        src = os.path.join(workdir,
                           "src" + os.path.splitext(asset["storage_key"])[1])
        storage.download_to(asset["storage_key"], src)
        _set_progress(worker_db, job_id, 10)
        _mark("download_s")
        sha = media.sha256_file(src)
        if is_music:
            dur = media.probe_audio_duration(src)
            info = {"duration": dur or (asset.get("duration_s") or 0.0),
                    "width": 0, "height": 0, "fps": 0.0, "has_audio": True,
                    "vfr": False}
        else:
            info = media.probe(src)
        worker_db.run(dbx.update_asset_probe, asset["id"], info["duration"],
                      info["width"], info["height"], info["fps"], sha)
        _set_progress(worker_db, job_id, 20)
        _mark("probe_s")

        cached = worker_db.run(dbx.get_index_by_sha, sha)
        if cached and cached.get("pipeline_version", 1) == \
                config.PIPELINE_VERSION and \
                (is_music or _index_has_tiles(cached["json"])):
            worker_db.run(dbx.update_asset_meta, asset["id"],
                          {"indexed": True, "staged": None})
            return {"sha256": sha, "cached": True, "kind": asset["kind"],
                    "timings": timings}

        warnings = []
        wav_local = None
        if info["has_audio"]:
            wav_local = os.path.join(workdir, "audio.wav")
            try:
                media.extract_wav(
                    src, wav_local,
                    cancelled_cb=lambda: _cancelled(worker_db))
            except Exception as e:
                wav_local = None
                warnings.append(f"audio extraction failed ({str(e)[:120]})")
        _set_progress(worker_db, job_id, 35)
        words, sentences, language = _transcribe_wav(
            job_id, wav_local, info["duration"], warnings)
        _set_progress(worker_db, job_id, 60)
        _mark("whisper_s")
        silences = _detect_silences(job_id, wav_local, info["duration"],
                                    warnings)

        shots, tile_keys, tile_step = [], [], None
        if not is_music:
            try:
                shots = scenes.detect_shots(src, info["duration"], warnings)
            except Exception as e:
                warnings.append(f"shot detection failed ({str(e)[:120]})")
            _set_progress(worker_db, job_id, 70)
            tile_keys, tile_step, spatial_sidecar = _build_and_upload_tiles(
                job_id, project_id, sha, src, info["duration"], workdir,
                warnings,
                seek_ceiling=max(0.0, info["duration"] - 0.05))
            try:
                spatial_sidecar = spatial.augment_with_shot_frames(
                    src, info["duration"], workdir, spatial_sidecar, shots)
            except Exception as e:
                warnings.append(
                    f"shot-boundary spatial supplement failed "
                    f"({str(e)[:120]}) — coarse face/text track kept")
            _mark("tiles_s")
        else:
            spatial_sidecar = None

        perception_sidecar = None
        if wav_local:
            try:
                import perception as perception_mod
                perception_sidecar = perception_mod.analyze_audio(wav_local)
            except Exception:
                pass
        _set_progress(worker_db, job_id, 92)

        speakers = len({w.speaker for w in words if w.speaker is not None})
        index = VideoIndex(
            video=VideoInfo(duration=info["duration"], fps=info["fps"] or 0.0,
                            width=info["width"], height=info["height"],
                            has_audio=info["has_audio"],
                            vfr_normalized=False),
            shots=shots,
            words=words,
            sentences=sentences,
            silences=silences,
            tile_keys=tile_keys,
            tile_step_s=tile_step,
            speakers=speakers,
            language=language,
            warnings=warnings,
            perception=perception_sidecar,
            spatial=spatial_sidecar,
        ).model_dump()
        worker_db.run(dbx.upsert_index, project_id, sha, index)
        worker_db.run(dbx.update_asset_meta, asset["id"],
                      {"indexed": True, "staged": None})
        _mark("persist_s")
        print(f"[index {job_id}] {asset['kind']} indexed: "
              f"{info['duration']:.1f}s, {len(words)} words, "
              f"{len(tile_keys)} tile(s)", flush=True)
        return {"sha256": sha, "cached": False, "kind": asset["kind"],
                "words": len(words), "tiles": len(tile_keys),
                "language": language, "warnings": warnings,
                "timings": timings}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _ensure_proxy(worker_db, project_id, sha, proxy_key, src_local, info,
                  workdir, ready_proxy=None):
    """Cache hits still need a proxy asset for THIS project (the player and
    preview renders read it). Reuse the stored object when possible.

    `ready_proxy` is a local file that IS already the proxy — the browser built
    it and `adopt_client_proxy` normalized it. Encoding one from it would be a
    540p re-encode of a 540p file for no gain.
    """
    existing = worker_db.run(
        lambda conn: dbx.asset_by_key(conn, project_id, proxy_key))
    if existing:
        return
    donor = worker_db.run(dbx.any_asset_by_sha, "proxy", sha)
    if donor and storage.exists(donor["storage_key"]):
        storage.copy_object(donor["storage_key"], proxy_key)
        worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                      bytes_=donor["bytes"], duration_s=donor["duration_s"],
                      width=donor["width"], height=donor["height"],
                      fps=donor["fps"], sha256=sha)
        return
    proxy_local = ready_proxy or os.path.join(workdir, "proxy.mp4")
    if not ready_proxy:
        media.make_proxy(src_local, proxy_local, info["fps"], info["vfr"],
                         info["has_audio"], duration=info["duration"])
    storage.upload_file(proxy_local, proxy_key, "video/mp4")
    p = media.probe(proxy_local)
    worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                  bytes_=os.path.getsize(proxy_local),
                  duration_s=p["duration"], width=p["width"],
                  height=p["height"], fps=p["fps"], sha256=sha)


# A ready-notice claiming edits already happened is a lie — analysis only
# just finished. Such drafts are discarded for the template fallback.
_GREET_CLAIM = re.compile(
    r"(?i)\b(?:i(?:'ve| have| already| just)+ (?:cut|trimmed|edited|"
    r"rendered|captioned)|i (?:cut|trimmed|edited|rendered)\b)")


def _greet_via_llm(worker_db, project_id, stats, pending, out_of_credits,
                   index):
    """LLM-authored ready-notice in Valmera's voice — the template in
    _finish_setup is the fallback. Recorded to llm_calls with job_id NULL
    (visible in admin, never charged)."""
    if not config.OPENAI_API_KEY:
        return None
    words = index.get("words") or []
    snippet = " ".join((w.get("w") or "").strip()
                       for w in words[:50] if isinstance(w, dict)).strip()
    if pending:
        branch = ("IMPORTANT: they sent an editing request while you were "
                  "analyzing; it was saved and you are starting on it "
                  "right now — tell them that.")
    elif out_of_credits:
        branch = ("IMPORTANT: they sent a request while you were "
                  "analyzing, but they are out of credits. The free "
                  "allowance is granted once, so do NOT tell them to wait "
                  "for a refresh — say plainly that they are out of credits "
                  "and can start their trial to keep editing.")
    elif not words:
        # The example must be something this footage can actually do. The
        # fixed one was speech-shaped, and on a silent clip it invited the
        # user to ask for the only edit we would have to refuse — see
        # _opening_example.
        branch = ("IMPORTANT: this video has NO SPEECH on its audio — no "
                  "transcript, no filler words, no pauses in any talking. "
                  "Do NOT suggest captions, cutting silences, removing ums "
                  "or anything else that needs speech; those are the edits "
                  "you would have to refuse. End by inviting their first "
                  "editing request with ONE concrete example drawn from what "
                  "this footage HAS — its shots, its motion, its colour, "
                  "music over it, or cutting it to the strongest moments.")
    else:
        branch = ("End by inviting their first editing request, with ONE "
                  "concrete example — grounded in the transcript opening "
                  "if it gives you anything to go on.")
    system = ("You are Valmera, an AI video editor. You just finished "
              "analyzing the user's uploaded video (transcription, "
              "filmstrip mapping). Write the short chat message (2-3 "
              "sentences, plain text, no markdown, no emoji) telling them "
              "their video is ready to edit. State the real stats you were "
              "given. You have NOT made any edits yet — never claim or "
              "imply you did, and never invent facts beyond what is "
              "given. WRITE THE MESSAGE IN ENGLISH — always. The user has "
              "not spoken yet; the transcript's language is the FOOTAGE'S "
              "language, never theirs (a real user got this greeting in "
              "Portuguese because their video was). Quote the transcript "
              "opening verbatim in its own language, but every word around "
              "it is English.")
    user = (f"Real stats to state: {stats}.\n"
            f"Transcript opening (verbatim, may be empty): \"{snippet}\"\n"
            f"{branch}")
    res = llm.ask_text(system, user, max_tokens=220, temperature=0.5,
                       purpose="index_greet")
    try:
        worker_db.run(dbx.insert_llm_call, project_id, None, "index_greet",
                      config.AGENT_MODEL,
                      {"system": system, "user": user},
                      {"text": res["text"]} if res
                      # The REAL provider error, not "call failed" — this row
                      # is the only trace a failed greeting leaves in admin.
                      else {"error": llm.last_error() or "call failed"},
                      res["prompt_tokens"] if res else None,
                      res["completion_tokens"] if res else None)
    except Exception as e:
        print(f"[index] greet llm_call record failed: {e}", flush=True)
    if not res:
        return None
    if _GREET_CLAIM.search(res["text"]):
        print("[index] greet draft claimed edits — using template",
              flush=True)
        return None
    # Deterministic hold on the English rule above: an English ready-notice
    # always carries one of these words outside the quoted snippet; a greet
    # written in the FOOTAGE's language (2026-08-09: Portuguese, off a
    # Portuguese transcript) carries none. The quoted transcript snippet is
    # stripped first so ITS language can never fail an honest draft.
    unquoted = res["text"]
    if snippet:
        unquoted = unquoted.replace(snippet, "")
    low = unquoted.lower()
    if not any(w in low for w in ("your", "ready", "video is", "edit")):
        print("[index] greet draft not in English — using template",
              flush=True)
        return None
    return res["text"]


def _dur_text(seconds):
    """Seconds under a minute and a half, minutes above — "0.2 min" for a
    13-second clip reads like a rounding error, not a duration."""
    return (f"{seconds:.0f} sec" if seconds < 90
            else f"{seconds / 60.0:.1f} min")


def _opening_example(n_words, n_sil):
    """The ONE example in the ready-notice, grounded in what the index
    actually found. Never suggest the edit this footage cannot take."""
    if not n_words:
        return ("cut it to the best moments, put music under it, and punch "
                "in on the action")
    if not n_sil:
        return "caption every word, tighten the intro, and add music"
    return "cut the dead air, caption every word, and tighten the intro"


def _sweep_tray_placements(worker_db, project_id):
    """Round 84: splice every asset the tray submit marked for placement
    (meta.tray_place = {order, before_main, duration_s}) into the EDL — in
    tray order, exactly where the user arranged them (before the footage
    for items ahead of the main video, after it for the rest).

    DB-flag driven, not job-payload driven, so it is race-free by design:
    the submit endpoint places directly when an EDL already exists, and
    this sweep catches everything marked while the main index was still
    running — including a submit that raced the job claim. Idempotent per
    asset (the flag is cleared under the same write) and per asset_key."""
    pending = worker_db.run(dbx.tray_pending_assets, project_id)
    if not pending:
        return 0
    row = worker_db.run(dbx.latest_edl, project_id)
    if not row:
        return 0
    edl = row["json"]
    inserts = list(edl.get("inserts") or [])
    have_keys = {i.get("asset_key") for i in inserts}
    keep = edl.get("keep") or []
    bounds = keep_boundaries(keep, edl.get("speed") or [])
    end_boundary = bounds[-1] if bounds else 0.0
    taken = {i.get("id") for i in inserts}
    added, placed_ids = 0, []
    for a in pending:
        try:
            place = (a.get("meta") or {}).get("tray_place") or {}
            key = a["storage_key"]
            placed_ids.append(a["id"])
            if not key or key in have_keys:
                continue
            dur = round(float(place.get("duration_s")
                              or a.get("duration_s") or 3.0), 2)
            dur = min(max(dur, 0.2), 600.0)
            n = 1
            while f"ins{n}" in taken:
                n += 1
            taken.add(f"ins{n}")
            inserts.append({
                "id": f"ins{n}", "asset_key": key,
                "kind": "image" if a["kind"] == "image_ref" else "video",
                "at_output_s": 0.0 if place.get("before_main")
                else float(end_boundary),
                "duration_s": dur,
            })
            have_keys.add(key)
            added += 1
        except Exception as e:
            print(f"[index] tray insert skipped ({e})", flush=True)
    if added:
        new_edl = dict(edl, inserts=inserts)
        try:
            src_dur = keep[-1][1] if keep else 0.0
            new_edl = validate_edl(new_edl, src_dur).model_dump()
        except Exception as e:
            # Do not commit an invalid EDL or clear tray_place: another index
            # or a submit retry can place it after the defect is corrected.
            print(f"[index] tray EDL validation failed — placements retained: "
                  f"{e}", flush=True)
            return 0
        version = worker_db.run(dbx.insert_edl, project_id, new_edl, "agent")
        # The player must have something to show for this version — nothing
        # else renders it (the studio's self-heal covers USER versions only,
        # and the draft engine refuses programs with spliced clips).
        try:
            project = worker_db.run(dbx.get_project, project_id)
            worker_db.run(dbx.enqueue_job, project_id,
                          project.get("user_id"), "preview",
                          {"edl_version": version, "source": "user_edit"})
        except Exception as e:
            print(f"[index] tray preview enqueue failed: {e}", flush=True)
    for aid in placed_ids:
        try:
            worker_db.run(dbx.update_asset_meta, aid,
                          {"tray_place": None, "placed": True})
        except Exception:
            pass
    return added


def _shorts_index_route(project_kind, duration, reindex=False):
    """Choose the post-index handoff for a project selected as Shorts.

    A sub-minute source already is one short and belongs in the direct editor.
    A long source is ready for a user-authored brief; merely choosing the mode
    must never spend model/render time or decide the creative direction.
    """
    if project_kind != "shorts" or reindex:
        return None
    return "direct_edit" if float(duration or 0.0) < 60.0 else "await_brief"


def _finish_setup(worker_db, project_id, session_id, info, index,
                  user_id=None, reindex=False, asset_id=None):
    """Seed EDL v1 (keep everything) if none exists, splice any staged tray
    items around it, greet in chat, and auto-start the agent on any request
    the user sent while indexing was still running.

    reindex=True marks a background pipeline refresh of an already-greeted
    project — those stay QUIET. The greet is idempotent against the CHAT,
    keyed on the ASSET — any re-run for the same asset (retry, self-heal,
    original-lands re-index, pipeline bump) stays quiet, while a genuine new
    upload is a new asset row and greets normally."""
    already_greeted = False
    if session_id and asset_id is not None:
        try:
            already_greeted = worker_db.run(dbx.has_index_greet, session_id,
                                            asset_id)
        except Exception as e:
            print(f"[index] greet dedup check failed: {e}", flush=True)
    quiet = bool(reindex or already_greeted)
    edl_was_reset = False
    _latest = worker_db.run(dbx.latest_edl, project_id)
    if not _latest:
        worker_db.run(dbx.insert_edl, project_id,
                      default_edl(info["duration"]), "agent")
    elif is_canvas_program(_latest["json"]):
        # The user built a canvas program (images/clips, no main video) FIRST,
        # then uploaded a main video. Migrate to a main-video program that keeps
        # the whole video so it actually renders — carry any placed inserts over
        # (render_edl validates + snaps them onto the new keep boundaries).
        migrated = default_edl(info["duration"])
        migrated["inserts"] = _latest["json"].get("inserts") or []
        worker_db.run(dbx.insert_edl, project_id, migrated, "agent")
    else:
        # The uploads flow appends now — it never replaces the main video —
        # but this validation stays as the safety net for a re-index of a
        # genuinely different file landing on an old project: one out-of-range
        # span would otherwise make the project permanently unwritable.
        try:
            validate_edl(_latest["json"], info["duration"])
        except Exception as e:
            fresh = default_edl(info["duration"])
            worker_db.run(dbx.insert_edl, project_id, fresh, "agent")
            print(f"[index] project {project_id}: the existing edit did not "
                  f"fit the new source ({str(e)[:160]}) — reset to the full "
                  "video so edits can land again", flush=True)
            edl_was_reset = True

    tray_added = 0
    try:
        tray_added = _sweep_tray_placements(worker_db, project_id)
    except Exception as e:
        print(f"[index] tray placement failed: {e}", flush=True)

    # SHORTS ROUTER: "make a short" and "extract several shorts from a long
    # video" are not the same job. A sub-minute source already IS one short;
    # route it into the normal editor and preserve any brief the user sent
    # while uploading. Project 480 lost that brief, failed a shorts_plan at
    # 59.97s, then made the user wait through an unrelated recovery edit.
    # Long sources WAIT for direction. Choosing SHORTS is a mode choice, not a
    # creative brief; auto-starting here picked clips before the user could say
    # how many, which moments, what length, or what style they wanted.
    awaiting_shorts_brief = False
    try:
        project_row = worker_db.run(dbx.get_project, project_id)
        shorts_route = _shorts_index_route(
            (project_row or {}).get("kind"), info.get("duration"), reindex)
        if shorts_route and user_id:
            if shorts_route == "direct_edit":
                worker_db.run(dbx.set_project_kind, project_id, "edit")
                found = (worker_db.run(dbx.pending_user_message,
                                       project_id, session_id)
                         if session_id else None)
                active = worker_db.run(dbx.has_active_agent_turn, project_id)
                if found and not active:
                    if worker_db.run(dbx.user_credits_balance, user_id) >= 1.0:
                        worker_db.run(
                            dbx.enqueue_job, project_id, user_id, "agent_turn",
                            {"message_id": found["id"], "auto_resumed": True,
                             "direct_short": True})
                        print(f"[index] project {project_id}: "
                              f"{info['duration']:.1f}s direct short — "
                              f"auto-resumed brief {found['id']}", flush=True)
                    else:
                        worker_db.run(
                            dbx.add_message, session_id, "assistant",
                            "This upload already fits one short, so I opened "
                            "it in the Editor instead of failing the Shorts "
                            "tool. Your brief is saved; add credits and send "
                            "it again to edit this video directly.",
                            {"kind": "direct_short", "credits_exhausted": True})
                elif not active and session_id:
                    worker_db.run(
                        dbx.add_message, session_id, "assistant",
                        f"This {info['duration']:.0f}-second upload already "
                        "fits one short, so I opened it in the Editor. Tell "
                        "me how you want this short tightened, reframed or "
                        "styled and I'll edit it directly.",
                        {"kind": "direct_short"})
                return
            awaiting_shorts_brief = True
            print(f"[index] project {project_id}: shorts mode ready — "
                  "waiting for the user's direction", flush=True)
    except Exception as e:
        print(f"[index] shorts routing failed: {e}", flush=True)

    pending, out_of_credits = None, False
    if session_id and user_id and config.OPENAI_API_KEY:
        try:
            found = worker_db.run(dbx.pending_user_message,
                                  project_id, session_id)
            if found and worker_db.run(dbx.has_active_agent_turn,
                                       project_id):
                found = None  # a turn is already working on this project
            if found:
                if worker_db.run(dbx.user_credits_balance,
                                 user_id) >= 1.0:
                    pending = found
                else:
                    # The canned reply promised an auto-start — don't break
                    # that promise silently; say why it can't happen.
                    out_of_credits = True
        except Exception as e:
            print(f"[index] auto-resume check failed: {e}", flush=True)

    dur_txt = _dur_text(info["duration"])
    n_shots = len(index.get("shots", []))
    n_words = len(index.get("words", []))
    # Gaps in the SPEECH, not dips in the waveform — the waveform test reads
    # zero on anything with a continuous bed (gameplay, music, a noisy room).
    n_sil = len(audit.speech_gaps(index.get("words", []), info["duration"],
                                  min_s=0.7,
                                  silences=index.get("silences", [])))
    stats = (f"{dur_txt}, {n_shots} "
             f"shot{'s' if n_shots != 1 else ''}, "
             + (f"{n_words} transcribed words, {n_sil} pause"
                f"{'s' if n_sil != 1 else ''} in the talking" if n_words else
                "no speech on the audio"))
    if tray_added:
        stats += (f", plus {tray_added} more upload"
                  f"{'s' if tray_added != 1 else ''} placed on the timeline")
    if awaiting_shorts_brief:
        summary = f"Your video is ready to turn into shorts — {stats}. "
        if pending:
            summary += ("I'm starting on the Shorts direction you sent while "
                        "I was analyzing — give me a moment.")
        elif out_of_credits:
            summary += ("I found the Shorts direction you sent while I was "
                        "analyzing, but you're out of credits. Start your "
                        "trial and send it again.")
        else:
            summary += ("Tell me what kind of shorts you want — how many, "
                        "the moments or topics to prioritize, target length, "
                        "and the caption or pacing style.")
    else:
        summary = f"Your video is ready to edit — {stats}. "
        if pending:
            summary += ("I'm starting on the request you sent while I was "
                        "analyzing — give me a moment.")
        elif out_of_credits:
            summary += ("I found the request you sent while I was analyzing, "
                        "but you're out of credits. Start your trial and send "
                        "it again.")
        else:
            summary += ("Tell me what you'd like changed — for example: "
                        f"\"{_opening_example(n_words, n_sil)}.\"")
    if quiet:
        # Quiet refresh: only speak when there is something the user needs.
        if session_id and pending:
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "Analysis refreshed — I'm starting on the request "
                          "you sent. Give me a moment.",
                          {"kind": "index_ready", "auto_resume": True,
                           "reindex": True})
        elif session_id and out_of_credits:
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "I found the request you sent earlier, but "
                          "you're out of credits. Start your trial and send "
                          "it again.",
                          {"kind": "index_ready", "auto_resume": False,
                           "reindex": True,
                           "credits_exhausted": True})
    else:
        # The Shorts greeting is product state, not creative copy: it must
        # explicitly ask for the missing brief. A generic LLM greeting can
        # accidentally sound as though clip selection already started.
        drafted = (None if awaiting_shorts_brief else
                   _greet_via_llm(worker_db, project_id, stats, pending,
                                  out_of_credits, index))
        if drafted:
            summary = drafted
        if edl_was_reset:
            # Say it plainly. Silently discarding someone's edit is worse than
            # the deadlock it replaces.
            summary += ("\n\nHeads up: this replaced the video the previous "
                        "edit was built on, so I've started fresh from the "
                        "full new upload — the earlier cuts don't apply to "
                        "this footage.")
        if session_id:
            meta = {"kind": "index_ready", "auto_resume": bool(pending),
                    "llm_authored": bool(drafted)}
            if asset_id is not None:
                try:
                    # ON CONFLICT against the partial unique index — two live
                    # workers greeting the same asset (a deploy window runs
                    # both) resolve to one message.
                    worker_db.run(dbx.add_index_greet, session_id, summary,
                                  meta, asset_id)
                except Exception as e:
                    # A deployment whose DB predates the unique index still
                    # greets — the check-first above already dedupes every
                    # non-racing path.
                    print(f"[index] greet conflict-insert unavailable "
                          f"({str(e)[:120]}) — plain insert", flush=True)
                    meta["index_greet"] = str(asset_id)
                    worker_db.run(dbx.add_message, session_id, "assistant",
                                  summary, meta)
            else:
                worker_db.run(dbx.add_message, session_id, "assistant",
                              summary, meta)
    if pending:
        try:
            worker_db.run(dbx.enqueue_job, project_id, user_id, "agent_turn",
                          {"message_id": pending["id"], "auto_resumed": True})
            print(f"[index] auto-resumed pending message {pending['id']} "
                  f"(project {project_id})", flush=True)
        except Exception as e:
            print(f"[index] auto-resume enqueue failed: {e}", flush=True)
