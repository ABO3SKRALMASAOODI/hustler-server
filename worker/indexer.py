"""Perception pipeline (job type "index"): video file -> JSON index.

Runs ONCE per unique file (sha256-keyed cache), then the agent works from
text forever after. Steps: probe -> proxy + wav -> whisper words/sentences ->
silences -> shots + thumbs -> contact sheets -> optional vision captions ->
assemble VideoIndex.
"""

import os
import re
import shutil
import time

import config
import db as dbx
import llm
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
    timings, _t = {}, time.monotonic()

    def _mark(stage):
        nonlocal _t
        timings[stage] = round(time.monotonic() - _t, 2)
        _t = time.monotonic()

    try:
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
                          workdir)
            _finish_setup(worker_db, project_id, session_id, info,
                          cached["json"], job["user_id"])
            _mark("cache_hit_s")
            return {"sha256": sha, "cached": True,
                    "shots": len(cached["json"].get("shots", [])),
                    "words": len(cached["json"].get("words", [])),
                    "timings": timings}
        if cached:
            print(f"[index {job_id}] stale index (pipeline "
                  f"v{cached.get('pipeline_version', 1)} < "
                  f"v{config.PIPELINE_VERSION}) for sha {sha[:12]} — "
                  "re-indexing", flush=True)

        # 3. Proxy (VFR -> CFR here) + 16k mono wav
        proxy_local = os.path.join(workdir, "proxy.mp4")
        media.make_proxy(src, proxy_local, info["fps"], info["vfr"],
                         info["has_audio"])
        worker_db.run(dbx.set_progress, job_id, 30)
        _mark("proxy_s")

        wav_local = None
        if info["has_audio"]:
            wav_local = os.path.join(workdir, "audio.wav")
            media.extract_wav(src, wav_local)
        worker_db.run(dbx.set_progress, job_id, 35)
        _mark("wav_s")

        # 4. Transcription
        words, sentences = [], []
        if wav_local:
            words, _lang = transcribe.transcribe(wav_local)
            sentences = transcribe.group_sentences(words)
        worker_db.run(dbx.set_progress, job_id, 60)
        _mark("whisper_s")

        # 5. Silences
        silences = []
        if wav_local:
            silences = media.detect_silences(wav_local, info["duration"])
        worker_db.run(dbx.set_progress, job_id, 65)
        _mark("silences_s")

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
        _mark("shots_s")

        # 7. Contact sheets + optional vision captions
        sheet_dir = os.path.join(workdir, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        sheet_list = sheets.build_contact_sheets(shots, thumb_paths, sheet_dir)
        sheets.caption_shots(sheet_list, {s.id: s for s in shots})
        worker_db.run(dbx.set_progress, job_id, 85)
        _mark("sheets_vision_s")

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
        _finish_setup(worker_db, project_id, session_id, info, index,
                      job["user_id"])
        _mark("upload_persist_s")
        return {"sha256": sha, "cached": False, "shots": len(shots),
                "words": len(words), "silences": len(silences),
                "timings": timings}
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
                  "analyzing, but they are out of credits (credits "
                  "refresh daily) — tell them honestly to send it again "
                  "once credits refresh.")
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
                      else {"error": "call failed"},
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
                  user_id=None):
    """Seed EDL v1 (keep everything) if none exists, greet in chat, and
    auto-start the agent on any request the user sent while indexing was
    still running (instead of asking them to resend it — nobody does)."""
    if not worker_db.run(dbx.latest_edl, project_id):
        worker_db.run(dbx.insert_edl, project_id,
                      default_edl(info["duration"]), "agent")

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
    n_sil = len([s for s in index.get("silences", [])
                 if s[1] - s[0] >= 0.7])
    stats = (f"{mins:.1f} min, {n_shots} "
             f"shot{'s' if n_shots != 1 else ''}, "
             f"{n_words} transcribed words, {n_sil} noticeable "
             f"silence{'s' if n_sil != 1 else ''}")
    summary = f"Your video is ready to edit — {stats}. "
    if pending:
        summary += ("I'm starting on the request you sent while I was "
                    "analyzing — give me a moment.")
    elif out_of_credits:
        summary += ("I found the request you sent while I was analyzing, "
                    "but you're out of credits — they refresh daily, so "
                    "send it again once they do.")
    else:
        summary += ("Tell me what you'd like changed — for example: \"cut "
                    "the dead air, caption every word, and tighten the "
                    "intro.\"")
    drafted = _greet_via_llm(worker_db, project_id, stats, pending,
                             out_of_credits, index)
    if drafted:
        summary = drafted
    if session_id:
        worker_db.run(dbx.add_message, session_id, "assistant", summary,
                      {"kind": "index_ready", "auto_resume": bool(pending),
                       "llm_authored": bool(drafted)})
    if pending:
        try:
            worker_db.run(dbx.enqueue_job, project_id, user_id, "agent_turn",
                          {"message_id": pending["id"], "auto_resumed": True})
            print(f"[index] auto-resumed pending message {pending['id']} "
                  f"(project {project_id})", flush=True)
        except Exception as e:
            print(f"[index] auto-resume enqueue failed: {e}", flush=True)
