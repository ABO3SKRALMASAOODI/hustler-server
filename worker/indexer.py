"""Perception pipeline (job type "index"): video file -> JSON index.

Runs ONCE per unique file (sha256-keyed cache), then the agent works from
text forever after. Steps: probe -> proxy + wav -> whisper words/sentences ->
silences -> shots + thumbs -> contact sheets -> optional vision captions ->
assemble VideoIndex.
"""

import os
import shutil

import config
import db as dbx
import media
import scenes
import sheets
import storage
import transcribe
from schemas import VideoIndex, VideoInfo, default_edl


def run_index_job(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    asset = worker_db.run(dbx.get_asset, job["payload"].get("asset_id"))
    if not asset:
        raise RuntimeError("Original asset not found")
    project = worker_db.run(dbx.get_project, project_id)
    session_id = project["chat_session_id"]

    workdir = os.path.join(config.TMP_DIR, f"index_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    try:
        # 1. Pull original + hash it
        src = os.path.join(workdir,
                           "src" + os.path.splitext(asset["storage_key"])[1])
        storage.download_to(asset["storage_key"], src)
        worker_db.run(dbx.set_progress, job_id, 8)
        sha = media.sha256_file(src)

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

        # Cache hit: this exact file was indexed before (any project).
        cached = worker_db.run(dbx.get_index_by_sha, sha)
        if cached:
            _ensure_proxy(worker_db, project_id, sha, proxy_key, src, info,
                          workdir)
            _finish_setup(worker_db, project_id, session_id, info,
                          cached["json"])
            return {"sha256": sha, "cached": True,
                    "shots": len(cached["json"].get("shots", [])),
                    "words": len(cached["json"].get("words", []))}

        # 3. Proxy (VFR -> CFR here) + 16k mono wav
        proxy_local = os.path.join(workdir, "proxy.mp4")
        media.make_proxy(src, proxy_local, info["fps"], info["vfr"],
                         info["has_audio"])
        worker_db.run(dbx.set_progress, job_id, 30)

        wav_local = None
        if info["has_audio"]:
            wav_local = os.path.join(workdir, "audio.wav")
            media.extract_wav(src, wav_local)
        worker_db.run(dbx.set_progress, job_id, 35)

        # 4. Transcription
        words, sentences = [], []
        if wav_local:
            words, _lang = transcribe.transcribe(wav_local)
            sentences = transcribe.group_sentences(words)
        worker_db.run(dbx.set_progress, job_id, 60)

        # 5. Silences
        silences = []
        if wav_local:
            silences = media.detect_silences(wav_local, info["duration"])
        worker_db.run(dbx.set_progress, job_id, 65)

        # 6. Shots + middle-frame thumbnails
        shots = scenes.detect_shots(proxy_local, info["duration"])
        thumb_dir = os.path.join(workdir, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_paths = {}
        for shot in shots:
            mid = (shot.start + shot.end) / 2.0
            tp = os.path.join(thumb_dir, f"shot_{shot.id}.jpg")
            try:
                media.frame_at(proxy_local, mid, tp, width=320)
                thumb_paths[shot.id] = tp
            except media.MediaError:
                pass
        worker_db.run(dbx.set_progress, job_id, 75)

        # 7. Contact sheets + optional vision captions
        sheet_dir = os.path.join(workdir, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        sheet_list = sheets.build_contact_sheets(shots, thumb_paths, sheet_dir)
        sheets.caption_shots(sheet_list, {s.id: s for s in shots})
        worker_db.run(dbx.set_progress, job_id, 85)

        # 8. Upload artifacts
        storage.upload_file(proxy_local, proxy_key, "video/mp4")
        audio_key = None
        if wav_local:
            audio_key = f"audio/{project_id}/{sha}.wav"
            storage.upload_file(wav_local, audio_key, "audio/wav")
        for shot in shots:
            tp = thumb_paths.get(shot.id)
            if tp:
                tkey = f"thumbs/{project_id}/{sha}/shot_{shot.id}.jpg"
                storage.upload_file(tp, tkey, "image/jpeg")
                shot.thumb_key = tkey
        sheet_keys = []
        for i, (sp, _ids) in enumerate(sheet_list, start=1):
            skey = f"sheets/{project_id}/{sha}/sheet_{i}.jpg"
            storage.upload_file(sp, skey, "image/jpeg")
            sheet_keys.append(skey)
        worker_db.run(dbx.set_progress, job_id, 92)

        proxy_info = media.probe(proxy_local)
        worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                      bytes_=os.path.getsize(proxy_local),
                      duration_s=proxy_info["duration"],
                      width=proxy_info["width"], height=proxy_info["height"],
                      fps=proxy_info["fps"], sha256=sha)
        if audio_key:
            worker_db.run(dbx.insert_asset, project_id, "audio", audio_key,
                          bytes_=os.path.getsize(wav_local),
                          duration_s=info["duration"], sha256=sha)

        # 9. Assemble + persist the index
        index = VideoIndex(
            video=VideoInfo(duration=info["duration"], fps=info["fps"],
                            width=info["width"], height=info["height"],
                            has_audio=info["has_audio"],
                            vfr_normalized=info["vfr"]),
            shots=shots,
            words=words,
            sentences=sentences,
            silences=silences,
            sheet_keys=sheet_keys,
        ).model_dump()
        worker_db.run(dbx.upsert_index, project_id, sha, index)
        _finish_setup(worker_db, project_id, session_id, info, index)
        return {"sha256": sha, "cached": False, "shots": len(shots),
                "words": len(words), "silences": len(silences)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _ensure_proxy(worker_db, project_id, sha, proxy_key, src_local, info,
                  workdir):
    """Cache hits still need a proxy asset for THIS project (the player and
    preview renders read it). Reuse the stored object when possible."""
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
    proxy_local = os.path.join(workdir, "proxy.mp4")
    media.make_proxy(src_local, proxy_local, info["fps"], info["vfr"],
                     info["has_audio"])
    storage.upload_file(proxy_local, proxy_key, "video/mp4")
    p = media.probe(proxy_local)
    worker_db.run(dbx.insert_asset, project_id, "proxy", proxy_key,
                  bytes_=os.path.getsize(proxy_local),
                  duration_s=p["duration"], width=p["width"],
                  height=p["height"], fps=p["fps"], sha256=sha)


def _finish_setup(worker_db, project_id, session_id, info, index):
    """Seed EDL v1 (keep everything) if none exists and greet in chat."""
    if not worker_db.run(dbx.latest_edl, project_id):
        worker_db.run(dbx.insert_edl, project_id,
                      default_edl(info["duration"]), "agent")
    mins = info["duration"] / 60.0
    n_shots = len(index.get("shots", []))
    n_words = len(index.get("words", []))
    n_sil = len([s for s in index.get("silences", [])
                 if s[1] - s[0] >= 0.7])
    summary = (f"Your video is ready to edit — {mins:.1f} min, {n_shots} "
               f"shot{'s' if n_shots != 1 else ''}, "
               f"{n_words} transcribed words, {n_sil} noticeable "
               f"silence{'s' if n_sil != 1 else ''}. "
               "Tell me what you'd like changed — for example: \"cut the "
               "dead air, caption every word, and tighten the intro.\"")
    if session_id:
        worker_db.run(dbx.add_message, session_id, "assistant", summary,
                      {"kind": "index_ready"})
