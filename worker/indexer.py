"""Perception pipeline (job type "index"): video file -> JSON index.

Runs ONCE per unique file (sha256-keyed cache), then the agent works from
text forever after. Steps: probe -> proxy + wav -> whisper words/sentences ->
silences -> shots + thumbs -> contact sheets -> optional vision captions ->
assemble VideoIndex.
"""

import os
import re
import shutil
import threading
import time
from concurrent import futures

import audit
import config
import db as dbx
import llm
import media
import scenes
import sheets
import storage
import transcribe
import visual
from schemas import (Moment, VideoIndex, VideoInfo, clamp_word_times,
                     default_edl, is_canvas_program, validate_edl)


PROGRESS_EVERY_S = 5.0


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
            worker_db.run(dbx.set_progress, job_id, pct)
        except Exception:
            # Progress is cosmetic; a hiccup here must never kill the encode.
            pass

    return cb


def run_index_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    asset = worker_db.run(dbx.get_asset, job["payload"].get("asset_id"))
    if not asset:
        raise RuntimeError("Original asset not found")
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
            worker_db.run(dbx.set_progress, job_id, 3)
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
            worker_db.run(dbx.set_progress, job_id, 8)
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
            worker_db.run(dbx.set_progress, job_id, 8)
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
        worker_db.run(dbx.set_progress, job_id, 12)

        proxy_key = f"proxies/{project_id}/{sha}.mp4"

        # Cache hit: this exact file was indexed before (any project) BY THE
        # CURRENT PIPELINE. An index built by an older pipeline version is
        # stale (different segmentation/VAD rules) and gets rebuilt.
        cached = worker_db.run(dbx.get_index_by_sha, sha)
        if cached and cached.get("pipeline_version", 1) == \
                config.PIPELINE_VERSION:
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
            print(f"[index {job_id}] stale index (pipeline "
                  f"v{cached.get('pipeline_version', 1)} < "
                  f"v{config.PIPELINE_VERSION}) for sha {sha[:12]} — "
                  "re-indexing", flush=True)

        # Non-fatal degradations recorded here and stored on the index so a
        # partially-degraded analysis is visible in admin instead of silently
        # worse.
        warnings = list(warnings_pre)

        # 3. Proxy (VFR -> CFR here) + 16k mono wav
        if from_client_proxy:
            # Already have it: the browser encoded it and adopt_client_proxy
            # normalized it. This is the 386.8s that a 4K index used to spend
            # here — 78% of the whole job — and it is now zero.
            proxy_local = src
        else:
            proxy_local = os.path.join(workdir, "proxy.mp4")
            media.make_proxy(src, proxy_local, info["fps"], info["vfr"],
                             info["has_audio"], duration=info["duration"],
                             progress_cb=_stage_progress(worker_db, job_id,
                                                         12, 30))
            # Probed here, not at upload time: step 6 needs the proxy's REAL
            # duration to keep thumbnail seeks inside it.
            proxy_info = media.probe(proxy_local)
        # The index is about to describe this video to the agent, the player
        # and every cut. If the proxy still doesn't cover the recording, say so
        # rather than shipping a description of footage that isn't there.
        proxy_have = proxy_info["video_duration"] or proxy_info["duration"]
        if info["duration"] - proxy_have > max(
                media.PROXY_SHORT_MIN_S,
                media.PROXY_SHORT_FRAC * info["duration"]):
            warnings.append(
                f"only the first {proxy_have:.1f}s of this "
                f"{info['duration']:.1f}s video has picture — the rest has "
                "sound but no frames")
        worker_db.run(dbx.set_progress, job_id, 30)
        _mark("proxy_s")

        wav_local = None
        if info["has_audio"]:
            wav_local = os.path.join(workdir, "audio.wav")
            media.extract_wav(src, wav_local)
        worker_db.run(dbx.set_progress, job_id, 35)
        _mark("wav_s")

        # 4. Transcription (retry once — a transient whisper crash shouldn't
        #    fail the whole job and force a full re-download/re-proxy)
        words, sentences, language = [], [], None
        if wav_local:
            for attempt in range(2):
                try:
                    words, language = transcribe.transcribe(wav_local,
                                                            warnings)
                    # ASR on music invents timings past the end of the file
                    # (a real 16.65s clip got a 'word' ending at 34.72s) —
                    # clamp before sentences inherit the bad spans.
                    words = clamp_word_times(words, info["duration"])
                    sentences = transcribe.group_sentences(words)
                    break
                except Exception as e:
                    if attempt == 0:
                        print(f"[index {job_id}] transcription failed "
                              f"({str(e)[:120]}); retrying once", flush=True)
                        continue
                    raise
        worker_db.run(dbx.set_progress, job_id, 60)
        _mark("whisper_s")

        # 5. Silences (degrade with a recorded warning, never silently to [])
        silences = []
        if wav_local:
            try:
                silences = media.detect_silences(wav_local, info["duration"])
            except Exception as e:
                warnings.append(f"silence detection failed ({str(e)[:120]}) — "
                                "silence-based trimming may be less accurate")
                print(f"[index {job_id}] silence detection degraded: {e}",
                      flush=True)
        worker_db.run(dbx.set_progress, job_id, 65)
        _mark("silences_s")

        # 6. Shots + frames sampled on a CLOCK (round 69).
        #
        # Frames used to be pulled one per SHOT, which meant 46% of real
        # uploads — every locked-off talking head, screen recording and
        # timelapse — were described to the agent by a single thumbnail, the
        # worst measured case being 19.3 minutes of footage summarised from
        # one frame at 9:38. Shots still mean "the camera changed" (transitions
        # depend on that); the SAMPLING is now independent of them. See
        # worker/visual.py.
        shots = scenes.detect_shots(proxy_local, info["duration"], warnings)
        # The ceiling is the one that already existed: MAX_VISION_SHEETS sheets
        # of PER_SHEET tiles. What changed is that a single-shot video used to
        # spend 1 of those 300 tiles.
        point_budget = max(1, config.MAX_VISION_SHEETS) * sheets.PER_SHEET
        points, points_capped = visual.sample_points(
            shots, info["duration"], config.VISUAL_SAMPLE_S, point_budget)
        if points_capped:
            warnings.append(
                f"this video has {len(shots)} shots — more than the "
                f"{point_budget}-frame analysis budget, so shots were sampled "
                "evenly and some have no visual description")
        thumb_dir = os.path.join(workdir, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        # Shot times live on the ORIGINAL's timeline (that is what the EDL and
        # the renderer cut against), but thumbnails are pulled from the PROXY —
        # so the seek has to be inside the PROXY's own decodable range. The two
        # can disagree: a source's container duration counts its audio tail and
        # its edit lists, so a re-encoded proxy is routinely a hair (sometimes a
        # lot) shorter. Seeking past its last frame yields no frame at all.
        proxy_dur = proxy_info["duration"]
        seek_ceiling = max(0.0, proxy_dur - 0.05) if proxy_dur > 0 else None

        # HUNDREDS OF SEEKS. Pulled serially this was ~1s each — 141 shots on a
        # real 4-minute upload cost 48s of seeking plus 139s of uploading, more
        # than the whole rest of the index. Each seek is an independent ffmpeg
        # that spends most of its life in I/O and single-threaded decode, so
        # they run concurrently on a box with cores to spare. Bounded by the
        # same knob the artifact uploads use, since both are "many small
        # independent jobs".
        def _frame(pi):
            t = points[pi]["t"]
            if seek_ceiling is not None:
                t = min(t, seek_ceiling)
            fp = os.path.join(thumb_dir, f"f_{pi:04d}.jpg")
            try:
                media.frame_at(proxy_local, t, fp, width=320)
                return pi, fp
            except media.MediaError as e:
                print(f"[index {job_id}] no frame at {t:.2f}s "
                      f"(proxy {proxy_dur}s): {e}", flush=True)
                return pi, None

        frame_paths = {}
        if points:
            with futures.ThreadPoolExecutor(
                    max_workers=max(1, min(len(points),
                                           config.THUMB_PARALLELISM))) as pool:
                for pi, fp in pool.map(_frame, range(len(points))):
                    if fp:
                        frame_paths[pi] = fp
        # Only the SHOT midpoints become uploaded per-shot thumbnails, so the
        # storage and upload cost of an index is exactly what it was — the
        # extra frames live and die inside this workdir, and reach the agent
        # only through the contact sheets, which were always uploaded.
        thumb_paths = {p["shot"]: frame_paths[i]
                       for i, p in enumerate(points)
                       if p["shot_mid"] and i in frame_paths}
        print(f"[index {job_id}] {len(shots)} shot(s), sampled "
              f"{len(frame_paths)}/{len(points)} frames "
              f"(every ~{config.VISUAL_SAMPLE_S:g}s, budget {point_budget})",
              flush=True)
        worker_db.run(dbx.set_progress, job_id, 75)
        _mark("shots_s")

        # 7. Contact sheets + optional vision captions. The cap now applies
        #    UPSTREAM, at sample_points: a 3-hour video stretches its sampling
        #    grid instead of firing proportionally many vision calls, so every
        #    frame that exists here fits inside MAX_VISION_SHEETS sheets by
        #    construction and the sheets are captioned concurrently.
        sheet_dir = os.path.join(workdir, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        sheet_list = sheets.build_contact_sheets(points, frame_paths,
                                                 sheet_dir)
        # Only caption when vision is actually configured — otherwise
        # caption_points is a no-op and any warning about sampling would
        # falsely claim captioning happened.
        #
        # AND RECORD WHAT IT SPENDS. llm.record() no-ops when no recorder is
        # installed, and only agent_loop and mcp_exec ever installed one — so
        # every contact-sheet caption call an index has ever made was invisible
        # in llm_calls. That is not a cosmetic gap: `SELECT ... FROM llm_calls
        # WHERE purpose='vision...'` is the documented way to check whether
        # vision is alive after a provider change, and it read ZERO for a
        # capability that was working perfectly. It also means every admin cost
        # view has been under-reporting index spend. Same recorder shape as the
        # agent turn's, minus the per-turn usage accumulation (an index is not
        # charged to a user).
        moments = []
        if llm.vision_available():
            # BUFFERED, not written from the callback. Sheets are captioned
            # concurrently now, and WorkerDB holds ONE psycopg connection with
            # no lock — two sheets recording at once would corrupt the lane's
            # connection mid-index. The buffer is drained on this thread below.
            recorded, rec_lock = [], threading.Lock()

            def _index_recorder(purpose, request, response, usage):
                with rec_lock:
                    recorded.append((purpose, request, response, usage))

            caps = {}
            try:
                caps = sheets.caption_points(
                    sheet_list, recorder=_index_recorder,
                    parallelism=config.VISION_CAPTION_PARALLELISM)
            except Exception as e:
                warnings.append(f"visual captioning failed ({str(e)[:120]}) — "
                                "shots have no visual description")
                print(f"[index {job_id}] vision captioning degraded: {e}",
                      flush=True)
            # Round 67's lesson: an index vision call that goes unrecorded is
            # invisible in the one place built to show whether vision is alive.
            for purpose, request, response, usage in recorded:
                try:
                    model = (request or {}).get("model")
                    cached_in = llm.cached_input_tokens(usage)
                    reasoning = llm.reasoning_tokens(usage)
                    if isinstance(response, dict) and (cached_in or reasoning):
                        extra = {}
                        if cached_in:
                            extra["cached_in"] = cached_in
                        if reasoning:
                            extra["reasoning_out"] = reasoning
                        response = dict(response, **extra)
                    worker_db.run(
                        dbx.insert_llm_call, project_id, job_id, purpose,
                        model, request, response,
                        getattr(usage, "prompt_tokens", None) if usage else None,
                        getattr(usage, "completion_tokens", None) if usage
                        else None)
                except Exception as e:
                    print(f"[index {job_id}] llm_call record failed: {e}",
                          flush=True)
            # A moment with no caption carries nothing but "a frame existed
            # here", so only described instants are kept. Shot captions are
            # then DERIVED from them, which is what leaves every existing
            # reader of shot.caption working untouched.
            moments = [Moment(t=p["t"], shot=p["shot"], caption=caps[i])
                       for i, p in enumerate(points) if i in caps]
            visual.derive_shot_captions(shots, moments)
            if not caps and sheet_list:
                warnings.append(
                    "visual captioning returned nothing — shots have timings "
                    "but no description of what is on screen")
        else:
            # SAY IT. Vision going dark is silent by design everywhere else
            # (the honest-off contract: tools report "visual inspection
            # unavailable" rather than failing), and the cost of that silence
            # was measured: after the Jul 26 2026 provider switch the
            # dispatcher lost its inherited vision key and ran 419 agent calls
            # across two days with ZERO look_at, while indexes quietly shipped
            # without a single visual description. Nothing anywhere said so.
            # An index with no captions is a materially worse index — the agent
            # is left with transcript and timings and no idea what is on
            # screen — so it belongs in the warnings an admin already reads.
            warnings.append(
                "no visual descriptions: the vision provider is not "
                "configured on this service (set VISION_API_KEY, or point "
                "VISION_BASE_URL at a provider whose key it can inherit) — "
                "shots have timings but no description of what is on screen")
            print(f"[index {job_id}] VISION UNAVAILABLE — indexing without "
                  f"shot captions (VISION_MODEL={config.VISION_MODEL!r}, "
                  f"key set={bool(config.VISION_API_KEY)})", flush=True)
        worker_db.run(dbx.set_progress, job_id, 85)
        _mark("sheets_vision_s")

        # 8. Upload artifacts — ALL AT ONCE.
        #
        # These PUTs used to run one after another, and they are the last thing
        # standing between the user and "your video is ready": a ~7MB proxy, a
        # wav, dozens of thumbnails and a handful of contact sheets, each
        # waiting for the previous round trip to finish. Measured at ~24.5s of
        # an index, roughly 40% of its cost, and almost all of it network wait
        # on a box that is otherwise idle.
        #
        # They are completely independent objects, so the wall clock should be
        # the slowest single upload, not their sum. Threads (not processes) are
        # right here because every one of them is blocked in a socket with the
        # GIL released.
        #
        # The failure contract is UNCHANGED and is the reason each group is
        # collected separately below: the proxy is load-bearing and a failure
        # must fail the job, while thumbnails and contact sheets are
        # conveniences the agent can re-derive — one un-extractable 320px jpeg
        # once raised FileNotFoundError here and buried ~200s of good analysis
        # under "I couldn't analyze that video. Try a different format like
        # mp4", advice that could not possibly have helped for a video that had
        # in fact been analyzed fine.
        audio_key = f"audio/{project_id}/{sha}.wav" if wav_local else None
        thumb_jobs = [(shot, thumb_paths.get(shot.id),
                       f"thumbs/{project_id}/{sha}/shot_{shot.id}.jpg")
                      for shot in shots]
        sheet_jobs = [(sp, f"sheets/{project_id}/{sha}/sheet_{i}.jpg")
                      for i, (sp, _ids) in enumerate(sheet_list, start=1)]

        with futures.ThreadPoolExecutor(
                max_workers=config.UPLOAD_PARALLELISM) as pool:
            # Required: the job dies if either of these does.
            required = {pool.submit(storage.upload_file, proxy_local,
                                    proxy_key, "video/mp4"): "proxy"}
            if audio_key:
                required[pool.submit(storage.upload_file, wav_local,
                                     audio_key, "audio/wav")] = "audio"
            # Best-effort: a failure drops the key and adds a warning.
            thumb_futs = {
                pool.submit(storage.upload_file, tp, tkey, "image/jpeg"):
                    (shot, tkey)
                for shot, tp, tkey in thumb_jobs if tp}
            sheet_futs = {
                pool.submit(storage.upload_file, sp, skey, "image/jpeg"):
                    (i, skey)
                for i, (sp, skey) in enumerate(sheet_jobs)}

            # Resolve the required ones FIRST so a storage outage still fails
            # fast and loudly, exactly as it did when these ran in sequence.
            for fut, what in required.items():
                try:
                    fut.result()
                except Exception:
                    print(f"[index {job_id}] {what} upload failed",
                          flush=True)
                    raise

            missing = [shot.id for shot, tp, _k in thumb_jobs if not tp]
            for fut, (shot, tkey) in thumb_futs.items():
                try:
                    fut.result()
                    shot.thumb_key = tkey
                except Exception as e:
                    missing.append(shot.id)
                    print(f"[index {job_id}] thumb upload failed for shot "
                          f"{shot.id}: {e}", flush=True)

            # Contact sheets keep their ORDER: sheet_keys is positional (the
            # agent asks for "sheet 3"), so collect by index and then compact,
            # never by completion order.
            ok_sheets = {}
            for fut, (i, skey) in sheet_futs.items():
                try:
                    fut.result()
                    ok_sheets[i] = skey
                except Exception as e:
                    print(f"[index {job_id}] sheet upload failed ({skey}): {e}",
                          flush=True)

        if missing:
            warnings.append(
                f"{len(missing)} of {len(shots)} shot thumbnails could not be "
                "made — those shots have no still image (the transcript and "
                "cut points are unaffected)")
        sheet_keys = []
        for i in sorted(ok_sheets):
            sp, skey = sheet_jobs[i]
            sheet_keys.append(skey)
            # Record contact sheets as assets so admin storage totals count
            # them and any future DB-driven cleanup finds them (thumbs are
            # covered by the sheets/ + thumbs/ R2 prefix delete). Guard against
            # duplicate rows on re-index / job-retry (same key) so totals don't
            # double-count — mirror the proxy asset_by_key check. Best-effort.
            try:
                exists = worker_db.run(
                    lambda conn: dbx.asset_by_key(conn, project_id, skey))
                if not exists:
                    worker_db.run(dbx.insert_asset, project_id, "sheet", skey,
                                  bytes_=os.path.getsize(sp), sha256=sha,
                                  meta={"index_artifact": True})
            except Exception:
                pass
        worker_db.run(dbx.set_progress, job_id, 92)

        worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                      bytes_=os.path.getsize(proxy_local),
                      duration_s=proxy_info["duration"],
                      width=proxy_info["width"], height=proxy_info["height"],
                      fps=proxy_info["fps"], sha256=sha)
        if audio_key:
            worker_db.run(dbx.insert_asset, project_id, "audio", audio_key,
                          bytes_=os.path.getsize(wav_local),
                          duration_s=info["duration"], sha256=sha)

        # 8.5 Perception sidecar (round 35): beat grid / energy / speech
        # stress from the wav that already exists. Computed inline for NEW
        # indexes because it is cheap (~seconds of numpy) while the wav is
        # local; cached pre-v35 indexes get it LAZILY the first time a tool
        # asks (perception.get_or_compute_for_index) — deliberately NOT a
        # PIPELINE_VERSION bump, so shipping this re-indexes nothing.
        # Non-fatal by the same contract as thumbnails: perception feeds
        # DECISIONS, never renders, so a failure degrades to a warning.
        perception_sidecar = None
        if wav_local:
            try:
                import perception as perception_mod
                perception_sidecar = perception_mod.analyze_audio(wav_local)
            except Exception as e:
                warnings.append(
                    f"audio perception failed ({str(e)[:120]}) — beat-synced "
                    "and emphasis-driven edits will analyze on first use")
        worker_db.run(dbx.set_progress, job_id, 94)

        # 9. Assemble + persist the index
        speakers = len({w.speaker for w in words if w.speaker is not None})
        index = VideoIndex(
            video=VideoInfo(duration=info["duration"], fps=info["fps"],
                            width=info["width"], height=info["height"],
                            has_audio=info["has_audio"],
                            vfr_normalized=info["vfr"]),
            shots=shots,
            moments=moments,
            words=words,
            sentences=sentences,
            silences=silences,
            sheet_keys=sheet_keys,
            speakers=speakers,
            language=language,
            warnings=warnings,
            perception=perception_sidecar,
        ).model_dump()
        worker_db.run(dbx.upsert_index, project_id, sha, index)
        _finish_setup(worker_db, project_id, session_id, info, index,
                      job["user_id"],
                      reindex=bool(job["payload"].get("reindex")),
                      asset_id=asset["id"])
        _mark("upload_persist_s")
        return {"sha256": sha, "cached": False, "shots": len(shots),
                "from_client_proxy": from_client_proxy,
                "moments": len(moments), "speakers": speakers,
                "words": len(words), "silences": len(silences),
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
    else:
        branch = ("End by inviting their first editing request, with ONE "
                  "concrete example — grounded in the transcript opening "
                  "if it gives you anything to go on.")
    system = ("You are Valmera, an AI video editor. You just finished "
              "analyzing the user's uploaded video (transcription, shot "
              "mapping). Write the short chat message (2-3 sentences, "
              "plain text, no markdown, no emoji) telling them their "
              "video is ready to edit. State the real stats you were "
              "given. You have NOT made any edits yet — never claim or "
              "imply you did, and never invent facts beyond what is "
              "given.")
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
    return res["text"]


def _finish_setup(worker_db, project_id, session_id, info, index,
                  user_id=None, reindex=False, asset_id=None):
    """Seed EDL v1 (keep everything) if none exists, greet in chat, and
    auto-start the agent on any request the user sent while indexing was
    still running (instead of asking them to resend it — nobody does).

    reindex=True marks a background pipeline refresh of an already-greeted
    project (the /state self-heal sets it on the job payload) — those stay
    QUIET: a real customer got a second "Your video is ready to edit ... I
    haven't made any edits" over a session where the agent had already cut
    her video.

    The flag alone is not enough (round 67): an index job reaped after a
    worker death and re-claimed re-runs this whole function with NO reindex
    flag, and project 300 was greeted twice from one job row, 3.5 minutes
    apart. So the greet is idempotent against the CHAT, keyed on the ASSET —
    any re-run for the same asset (retry, self-heal, original-lands
    re-index, pipeline bump) stays quiet, while a genuine replacement upload
    is a new asset row and greets normally."""
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
        # A REPLACEMENT upload re-bases the whole timeline. Every EDL time is
        # a source time, so an edit built against a 276s video is nonsense
        # against the 202s one that replaced it — and because every write tool
        # validates the WHOLE EDL, one out-of-range span makes the project
        # permanently unwritable: keep blocks a volume fix, volume blocks a
        # keep fix, and nothing can ever land. A real customer hit exactly
        # that on 2026-07-25 and the agent had to tell them "the edit is stuck
        # and I can't change it from here". Re-validating against the NEW
        # duration is the precise test: a re-index of the same file still
        # validates and keeps every edit, and only a genuine replacement
        # resets.
        try:
            validate_edl(_latest["json"], info["duration"])
        except Exception as e:
            fresh = default_edl(info["duration"])
            worker_db.run(dbx.insert_edl, project_id, fresh, "agent")
            print(f"[index] project {project_id}: the existing edit did not "
                  f"fit the new source ({str(e)[:160]}) — reset to the full "
                  "video so edits can land again", flush=True)
            edl_was_reset = True

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

    mins = info["duration"] / 60.0
    n_shots = len(index.get("shots", []))
    n_words = len(index.get("words", []))
    # Gaps in the SPEECH, not dips in the waveform — the waveform test reads
    # zero on anything with a continuous bed (gameplay, music, a noisy room),
    # so this line used to greet those users with "0 noticeable silences"
    # over a video full of nobody talking. See audit.speech_gaps.
    n_sil = len(audit.speech_gaps(index.get("words", []), info["duration"],
                                  min_s=0.7,
                                  silences=index.get("silences", [])))
    stats = (f"{mins:.1f} min, {n_shots} "
             f"shot{'s' if n_shots != 1 else ''}, "
             f"{n_words} transcribed words, {n_sil} pause"
             f"{'s' if n_sil != 1 else ''} in the talking")
    summary = f"Your video is ready to edit — {stats}. "
    if pending:
        summary += ("I'm starting on the request you sent while I was "
                    "analyzing — give me a moment.")
    elif out_of_credits:
        summary += ("I found the request you sent while I was analyzing, "
                    "but you're out of credits. Start your trial and send it "
                    "again.")
    else:
        summary += ("Tell me what you'd like changed — for example: \"cut "
                    "the dead air, caption every word, and tighten the "
                    "intro.\"")
    if quiet:
        # Quiet refresh: only speak when there is something the user needs —
        # a saved request auto-starting, or the reason it CAN'T start.
        # Staying silent on the out-of-credits case would break the
        # concierge's "I'll start on it automatically" promise invisibly.
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
        drafted = _greet_via_llm(worker_db, project_id, stats, pending,
                                 out_of_credits, index)
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
