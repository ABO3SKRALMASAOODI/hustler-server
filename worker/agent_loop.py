"""Agent turn (job type "agent_turn"): one OpenAI tool-calling loop per user
chat message. Every tool call is persisted as an 'activity' chat message so
the frontend shows live progress over the existing polling channel."""

import difflib
import json
import os
import re
import shutil
import threading
import time
import uuid

import agent_tools
import config
import db as dbx
import grammar
import llm
import music_search
import preview_critic
import remote
import request_intent
import sfx_search
import song_find
import storage
import timeline
from agent_prompt import project_state_block, system_prompt
from schemas import describe_edl

# Set by main._on_shutdown when Render SIGTERMs the container (every deploy).
# The turn loop checks it between iterations and finalizes honestly inside
# the grace window instead of dying and leaving the reaper to guess.
SHUTDOWN = threading.Event()


def _silence_line(index):
    sil = [s for s in index.get("silences", []) if s[1] - s[0] >= 0.7]
    return (f"SILENCES >=0.7s: {len(sil)}, "
            f"totalling {sum(e - s for s, e in sil):.1f}s "
            "(use find_silences for the exact list with word context).")


def _burned_captions_line(index):
    """A warning line when the SOURCE footage already carries subtitle-style
    captions, detected by the index's vision pass (ShotCaption.subtitles).

    Why it exists: users drop pre-captioned videos, the agent (which never
    sees pixels) burns new captions on top, and the render shows caption
    soup. Two flagged shots AND a quarter of the described shots keeps a
    single misread sign from crying wolf; the transcript cross-check
    upgrades 'looks like' to 'matches the spoken words'. Old indexes have no
    subtitles flags -> no line, honestly silent rather than guessed."""
    shots = index.get("shots") or []
    capped = [s for s in shots if s.get("caption")]
    flagged = [s for s in capped if (s.get("caption") or {}).get("subtitles")]
    if len(flagged) < 2 or len(flagged) < 0.25 * len(capped):
        return None
    words = {w["w"].lower().strip(".,!?\"'") for w in
             (index.get("words") or []) if len(w.get("w") or "") > 3}
    matched = 0
    for s in flagged:
        ost = ((s.get("caption") or {}).get("on_screen_text") or "").lower()
        hits = sum(1 for t in ost.replace(",", " ").split()
                   if t.strip(".,!?\"'") in words)
        if hits >= 2:
            matched += 1
    conf = (" — their text MATCHES the spoken words, so they are subtitles, "
            "not signage" if matched >= 1 else "")
    return (f"WARNING — BURNED-IN CAPTIONS: {len(flagged)} of {len(capped)} "
            f"described shots already show caption text baked into the "
            f"footage{conf}. Adding captions would STACK new text on top of "
            "the old. You can REMOVE the old ones for real: "
            "erase_burned_text() measures the band and repaints those pixels, "
            "so a re-caption in any style lands on a clear frame. Tell the "
            "user what you found and do that first.")


def _speaker_line(index):
    """Who is talking, when the transcriber knows (round 69). Silent on an
    undiarized index rather than claiming one speaker — whisper does not
    diarize, and "one speaker" is a real editorial fact we must not invent."""
    n = index.get("speakers") or 0
    if n < 2:
        return None
    return (f"SPEAKERS: {n} people talk in this video, labelled S0..S{n - 1} "
            "on every transcript line. You can cut, keep or reorder by "
            "speaker — 'keep only the interviewer' is answerable from the "
            "transcript alone, no guessing from the picture.")


def _filler_line(index):
    n = sum(1 for w in (index.get("words") or []) if w.get("filler"))
    if not n:
        return None
    return (f"FILLER SOUNDS: {n} hesitation(s) ('um', 'uh') are timestamped in "
            "the transcript. remove_filler_words() cuts every one of them in "
            "a single call. They are never burned into captions.")


def _shot_boundaries_line(index):
    """Scene changes as one compact line — where transitions may land. The
    PICTURE itself is in the filmstrip; this is just the cut geometry."""
    shots = index.get("shots") or []
    if not shots:
        return "SHOT BOUNDARIES: none detected (one continuous take)."
    if len(shots) == 1:
        return (f"SHOT BOUNDARIES: one continuous shot "
                f"({shots[0]['start']:.1f}-{shots[0]['end']:.1f}s) — no "
                "scene changes for transitions to land on.")
    starts = [f"{s['start']:.1f}" for s in shots]
    if len(starts) > 60:
        head = ", ".join(starts[:50])
        return (f"SHOT BOUNDARIES ({len(shots)} shots — scene changes, "
                f"where transitions may land): {head}, ... "
                f"+{len(starts) - 50} more (get_shots for any range).")
    return (f"SHOT BOUNDARIES ({len(shots)} shots — scene changes, where "
            f"transitions may land): {', '.join(starts)}.")


def _index_summary(index):
    """The main footage's text senses: the transcript (complete when it
    fits), speakers, fillers, shot boundaries, silences, language. The
    PICTURE is not described here — the filmstrip tiles that follow the
    state ARE the visual index, read directly."""
    sentences = index.get("sentences", [])
    words = index.get("words", [])
    lines = []

    def sent_line(s, cap=None):
        text = s['text'] if cap is None else s['text'][:cap]
        spk = f" S{s['speaker']}" if s.get("speaker") is not None else ""
        return f"  [{s['id']}{spk} {s['t0']:.1f}-{s['t1']:.1f}] {text}"

    full_text = None
    if sentences:
        candidate = [
            f"TRANSCRIPT — COMPLETE ({len(sentences)} sentences / "
            f"{len(words)} words; do NOT call get_transcript for this "
            "video — use get_words only for word-exact cut points):"]
        candidate += [sent_line(s) for s in sentences]
        joined = "\n".join(candidate)
        if len(joined) <= config.FULL_INDEX_MAX_CHARS:
            full_text = joined
    if full_text is not None:
        lines.append(full_text)
    elif sentences:
        lines.append(
            f"TRANSCRIPT: {len(sentences)} sentences / {len(words)} words "
            "(long video — head/tail below; call get_transcript / "
            "search_transcript / get_words for the rest).")
        for s in sentences[:4]:
            lines.append(sent_line(s, cap=110))
        if len(sentences) > 6:
            lines.append(f"  ... {len(sentences) - 6} more "
                         "(use get_transcript) ...")
        for s in sentences[-2:]:
            lines.append(sent_line(s, cap=110))
    else:
        lines.append("TRANSCRIPT: none (no speech detected or no audio "
                     "track).")

    sl = _speaker_line(index)
    if sl:
        lines.append(sl)
    fl = _filler_line(index)
    if fl:
        lines.append(fl)
    lines.append(_shot_boundaries_line(index))
    bc = _burned_captions_line(index)
    if bc:
        lines.append(bc)
    lines.append(_silence_line(index))
    if index.get("language"):
        lines.append(f"LANGUAGE OF THE SPEECH IN THE FOOTAGE: "
                     f"{index['language']} (whisper's guess about the AUDIO — "
                     "it defaults to 'en' on a silent clip and says nothing "
                     "about which language to write your reply in).")
    return "\n".join(lines)


def _pending_clips(conn, project_id):
    """Video clips still waiting on their perception pass — listed so the
    agent knows they exist before their filmstrip arrives."""
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'video_clip'
                         AND COALESCE(meta->>'indexed', '') != 'true'
                         AND COALESCE(meta->>'staged', '') != 'true'
                       ORDER BY id ASC LIMIT 20""", (project_id,))
        return cur.fetchall()


def _image_assets(conn, project_id):
    # Oldest first, so the user's own uploads outrank later generated
    # cards when the attach cap bites. The LIMIT follows IMAGES_TURN_MAX
    # so raising that env genuinely raises both this list and the attach.
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'image_ref'
                         AND COALESCE(meta->>'staged', '') != 'true'
                       ORDER BY id ASC LIMIT %s""",
                    (project_id, max(20, config.IMAGES_TURN_MAX)))
        return cur.fetchall()


# ── Filmstrips: the agent's standing eyes on every video and still image
#    in the project ──

def _tile_local(sha, key):
    """Local cache path for one tile — tiles are immutable per sha, so a
    turn only downloads what no earlier turn on this box already has."""
    d = os.path.join(config.TMP_DIR, "tilecache", sha[:16])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, os.path.basename(key))


def _strip_for(worker_db, sha, max_tiles):
    """[(key, local_path)] for one indexed video, evenly thinned to
    max_tiles. Missing/unfetchable tiles are skipped — a shorter strip, not
    a dead turn. Cache misses download CONCURRENTLY: the first turn on a
    project pulls the whole strip, and 36 sequential round trips would tax
    exactly the turn that should feel instant."""
    row = worker_db.run(dbx.get_index_by_sha, sha) if sha else None
    idx = (row or {}).get("json") or {}
    keys = idx.get("tile_keys") or []
    if not keys:
        return [], idx
    if len(keys) > max_tiles > 0:
        stride = len(keys) / float(max_tiles)
        keys = [keys[min(len(keys) - 1, int(i * stride))]
                for i in range(max_tiles)]
    plan = [(key, _tile_local(sha, key)) for key in keys]
    missing = [(key, local) for key, local in plan
               if not os.path.exists(local)]
    if missing:
        import concurrent.futures as _cf

        def _fetch(job):
            key, local = job
            try:
                storage.download_to(key, local)
                return None
            except Exception as e:
                return (key, str(e)[:120])

        with _cf.ThreadPoolExecutor(
                max_workers=min(8, len(missing))) as pool:
            for err in pool.map(_fetch, missing):
                if err:
                    print(f"[filmstrip] tile fetch failed ({err[0]}): "
                          f"{err[1]}", flush=True)
    return [(key, local) for key, local in plan
            if os.path.exists(local)], idx


def _image_attach_local(asset):
    """Local downscaled JPEG for one still image asset, built once per box.

    Uploaded stills are phone-sized (a 12MP photo is ~4000px and megabytes
    of JPEG); attaching originals would put that into every call of every
    turn. One bounded copy — EXIF-rotated (phone portraits otherwise attach
    sideways), alpha flattened dark like the tile canvas (a white-text card
    stays readable), IMAGE_ATTACH_MAX_PX on the long side — costs about one
    tile of context. Cached by asset id: asset rows are append-only and a
    row's storage key never changes. Returns None when the image cannot be
    attached (oversized, undecodable) — the caller skips it, never dies."""
    if (asset.get("bytes") or 0) > 12 * 1024 * 1024:
        return None
    d = os.path.join(config.TMP_DIR, "tilecache", f"img{asset['id']}")
    os.makedirs(d, exist_ok=True)
    local = os.path.join(d, f"attach_{config.IMAGE_ATTACH_MAX_PX}.jpg")
    if os.path.exists(local):
        return local
    from PIL import Image, ImageOps
    ext = os.path.splitext(asset["storage_key"])[1] or ".img"
    src = os.path.join(d, f"orig-{uuid.uuid4().hex[:8]}{ext}")
    storage.download_to(asset["storage_key"], src)
    try:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "PA") or \
                (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (16, 16, 16))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")
        m = max(64, config.IMAGE_ATTACH_MAX_PX)
        img.thumbnail((m, m))
        tmp = f"{local}.tmp-{uuid.uuid4().hex[:6]}"
        img.save(tmp, "JPEG", quality=82)
        os.replace(tmp, local)
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
    return local


def filmstrip_parts(ctx, worker_db):
    """The per-turn senses message content: labeled image parts for the
    main footage, EVERY indexed uploaded clip AND every still image the
    project holds — the agent's fresh eyes on everything in the project,
    rebuilt each turn so nothing is ever stale.

    Returns a content list ([{type: text}, {type: image_url}, ...]) or None
    when there is nothing visual to attach."""
    strips = []      # (label, [(key, local)])
    total = 0
    if ctx.has_main_video:
        original = worker_db.run(dbx.latest_asset, ctx.project_id, "original")
        sha = original and original.get("sha256")
        if sha:
            tiles, idx = _strip_for(worker_db, sha, config.TILES_MAIN_MAX)
            if tiles:
                v = idx.get("video") or {}
                step = idx.get("tile_step_s")
                strips.append((
                    f"MAIN FOOTAGE — {v.get('duration', 0):.1f}s, frames "
                    f"every {step or '?'}s, timestamps under each frame "
                    "(SOURCE seconds)", tiles))
                total += len(tiles)

    # Every indexed clip in the project, newest last — same senses, same
    # freshness. A clip that just finished indexing appears here on the very
    # next turn with no one having to remember it exists.
    try:
        clips = worker_db.run(dbx.indexed_clips, ctx.project_id)
    except Exception:
        clips = []
    for c in clips:
        if total >= config.TILES_TURN_MAX:
            break
        sha = c.get("sha256")
        if not sha:
            continue
        budget = min(config.TILES_CLIP_MAX, config.TILES_TURN_MAX - total)
        tiles, idx = _strip_for(worker_db, sha, budget)
        if not tiles:
            continue
        name = (c.get("meta") or {}).get("filename") or \
            os.path.basename(c["storage_key"])
        dur = c.get("duration_s") or (idx.get("video") or {}).get("duration")
        strips.append((
            f"UPLOADED CLIP \"{name}\" — {float(dur or 0):.1f}s, "
            f"storage_key {c['storage_key']} (timestamps are CLIP seconds)",
            tiles))
        total += len(tiles)

    # Every still image the project holds — uploads and generated cards —
    # one downscaled frame each, same freshness as the strips. As text-only
    # inventory lines the agent either spent a tool call per photo to learn
    # what it showed (round 95, project 380: 8 photos, 8 look_at_asset
    # calls) or placed it blind; and a CANVAS program built purely from
    # stills started with no eyes at all. A failed image is skipped, never
    # a dead turn.
    try:
        images = worker_db.run(
            lambda conn: _image_assets(conn, ctx.project_id))
    except Exception:
        images = []
    images = list(images or [])[:max(0, config.IMAGES_TURN_MAX)]
    if images:
        import concurrent.futures as _cf

        def _prep(a):
            try:
                return a["id"], _image_attach_local(a)
            except Exception as e:
                print(f"[filmstrip] image attach skipped "
                      f"({a['storage_key']}): {str(e)[:120]}", flush=True)
                return a["id"], None

        with _cf.ThreadPoolExecutor(
                max_workers=min(8, len(images))) as pool:
            ready = dict(pool.map(_prep, images))
        for a in images:
            local = ready.get(a["id"])
            if not local:
                continue
            name = (a.get("meta") or {}).get("filename") or \
                os.path.basename(a["storage_key"])
            origin = ("GENERATED IMAGE"
                      if a["storage_key"].startswith("generated/")
                      else "UPLOADED IMAGE")
            strips.append((
                f'{origin} "{name}" — storage_key {a["storage_key"]}',
                [(a["storage_key"], local)]))

    if not strips:
        return None
    content = [{"type": "text", "text":
                "FILMSTRIPS & STILLS — your eyes on every video and image "
                "in this project, current as of THIS message. Each video "
                "tile is a 2x2 grid of frames with the timestamp printed "
                "under each frame; each still image appears once, labeled "
                "with its storage_key. Read the footage directly: what is "
                "on screen, who is in frame, burned-in text or captions, "
                "UI content, framing, where the action is. For a closer or "
                "exact look at any moment, call look_at (main footage / "
                "program) or look_at_asset (a clip or image) — look as "
                "often as you need."}]
    for label, tiles in strips:
        content.append({"type": "text", "text": f"[{label}]"})
        for _key, local in tiles:
            try:
                content.append(llm.image_part(local))
            except Exception as e:
                print(f"[filmstrip] attach failed: {e}", flush=True)
    if len(content) == 1:
        return None
    return content


IMAGE_CAPTION_PROMPT = (
    "Describe this reference image concretely: subject, layout, colors, any "
    "visible text. The user attached it to a video-editing request, so "
    "capture what an editor would need to know about it.")


def _attachment_context(worker_db, ctx, user_message):
    """Chat attachments on this message -> honest context lines. New images
    are captioned once via the vision model (cached on the asset)."""
    ids = (user_message.get("meta") or {}).get("attachments") or []
    if not isinstance(ids, list):
        return ""
    notes = []
    for aid in ids[:4]:
        if isinstance(aid, dict):
            aid = aid.get("id")
        asset = worker_db.run(dbx.get_asset, aid)
        if not asset or asset["project_id"] != ctx.project_id:
            continue
        m = asset.get("meta") or {}
        name = m.get("filename") or os.path.basename(asset["storage_key"])
        if asset["kind"] == "music":
            # never 'audio' — that kind is the pipeline's own extracted
            # transcription WAV, and presenting it as attached music is how
            # the inaudible-music bug started
            dur = (f" ({asset['duration_s']:.0f}s)"
                   if asset.get("duration_s") else "")
            notes.append(f'[User attached music "{name}"{dur} — '
                         f'storage_key: {asset["storage_key"]}]')
        elif asset["kind"] == "video_clip":
            dur = (f" ({asset['duration_s']:.0f}s)"
                   if asset.get("duration_s") else "")
            # The old note said only "it can be spliced with insert_media" —
            # so when a user sent a screen recording of an edit she liked and
            # said "make the beginning like here", the agent spliced HER
            # REFERENCE into the teaser as its opening 24 seconds, and she
            # spent her next two messages teaching the product what a
            # reference is (Aug 3 2026, projects 342/343). The note must
            # present both readings, because the attachment alone cannot say
            # which it is — only their words can.
            notes.append(
                f'[User attached a video clip "{name}"{dur} — storage_key: '
                f'{asset["storage_key"]}. Decide FROM THEIR WORDS what it is '
                "for. If they want it IN the video (\"add this clip\", "
                "\"insert this\", \"put this at the end\"), splice it with "
                "insert_media. If it is a REFERENCE — \"like this\", \"make "
                "it look like this\", \"recreate this\", \"here's an "
                "example\" — do NOT insert it: study it with look_at_asset "
                "(sample enough frames to read its pacing, shot order and "
                "transitions), then rebuild that STYLE from the MAIN "
                "footage. If their words could mean either, ask_user before "
                "splicing.]")
        elif asset["kind"] == "image_ref":
            if ctx.direct_sight and llm.agent_sees(ctx.agent_model):
                # The pixels themselves ride in the FILMSTRIPS & STILLS
                # block this turn (round 95) — no vision round trip, and
                # never a false "you cannot see it".
                notes.append(
                    f'[User attached reference image "{name}" — its pixels '
                    "are attached in the FILMSTRIPS & STILLS block, labeled "
                    f'with storage_key {asset["storage_key"]}.]')
                continue
            cap = m.get("caption")
            if not cap and llm.vision_available() and \
                    (asset.get("bytes") or 0) <= 12 * 1024 * 1024:
                local = os.path.join(
                    ctx.workdir, f"attach_{asset['id']}"
                    + os.path.splitext(asset["storage_key"])[1])
                try:
                    storage.download_to(asset["storage_key"], local)
                    cap = llm.ask_vision(IMAGE_CAPTION_PROMPT, [local],
                                         max_tokens=300,
                                         purpose="vision_caption",
                                         image_names=[asset["storage_key"]])
                    if cap:
                        worker_db.run(dbx.update_asset_meta, asset["id"],
                                      {"caption": cap})
                except Exception:
                    cap = None
            if cap:
                notes.append(f'[User attached reference image "{name}" — '
                             f'what it shows: {cap}]')
            else:
                notes.append(
                    f'[User attached reference image "{name}" — no vision '
                    "model is available, so you CANNOT see it. Say so "
                    "honestly and ask the user to describe what matters.]")
    return ("\n\n" + "\n".join(notes)) if notes else ""


def state_block(ctx, worker_db):
    """The CURRENT PROJECT STATE message: the footage, its transcript/shots,
    the current EDL and what is available to place.

    Extracted from _build_messages so the MCP surface (worker/mcp_exec) can
    hand an OUTSIDE model exactly the state the in-house agent gets — two
    versions of "what does the model know about this project" would drift
    within a round.
    """
    index = ctx.index
    if ctx.has_main_video:
        v = index["video"]
        video_line = (f"Video: {v['duration']}s ({v['duration']/60:.1f} min), "
                      f"{v['width']}x{v['height']} @ {v['fps']}fps, "
                      f"audio={'yes' if v['has_audio'] else 'no'}.")
        index_summary = _index_summary(index)
    else:
        # A canvas program: no main video, so there is no transcript, no shot
        # list and no source clock. Everything below still applies — the EDL,
        # the assets, the music — which is why this is a branch and not an
        # early return. (It is also why _index_summary is not called with an
        # empty index: every one of its readers starts at index["video"].)
        video_line = ("No main video. This is a CANVAS program built from "
                      "uploaded/generated images, clips and audio — place "
                      "them with insert_media / add_overlay / add_text.")
        index_summary = ("TRANSCRIPT / SHOTS / SILENCES: none — there is no "
                         "indexed main video to read them from.")
    edl = ctx.latest_edl()
    edl_line = f"v{edl['version']} — {describe_edl(edl['json'], ctx.duration)}"
    try:
        program_lines = timeline.describe_program(
            edl["json"], agent_tools.program_name_of(ctx))
    except Exception:
        program_lines = ""
    keep = edl["json"].get("keep") or []
    keep_line = json.dumps(keep[:40]) + \
        (f" ...(+{len(keep) - 40} more spans)" if len(keep) > 40 else "")
    caps = edl["json"].get("captions")
    captions_line = json.dumps(caps) if caps else "none"
    history = worker_db.run(dbx.edl_history, ctx.project_id)
    history_lines = [f"v{h['version']} ({h['created_by']})" for h in history]
    music = worker_db.run(agent_tools._music_assets, ctx.project_id)
    music_lines = [
        f"{m['storage_key']} — {(m.get('meta') or {}).get('filename', '?')}"
        for m in music]

    # MEDIA INVENTORY — every video/image the project holds, its state, and
    # whether it is placed in the program. Rebuilt each turn: a clip that
    # finished indexing seconds ago shows up here (and its filmstrip below)
    # with nobody having to remember it.
    media_lines = []
    try:
        placed_keys = {i.get("asset_key")
                       for i in (edl["json"].get("inserts") or [])}
        clips = worker_db.run(
            lambda conn: dbx.indexed_clips(conn, ctx.project_id, 50))
        all_clips = {c["id"]: c for c in clips}
        # Also list clips still indexing, so the agent never denies having
        # a file the user just added.
        pending = worker_db.run(
            lambda conn: _pending_clips(conn, ctx.project_id))
        for c in list(all_clips.values()) + pending:
            cmeta = c.get("meta") or {}
            name = cmeta.get("filename") or \
                os.path.basename(c["storage_key"])
            dur = c.get("duration_s")
            state = ("indexed" if cmeta.get("indexed")
                     else "still analyzing — filmstrip/transcript arrive "
                          "shortly")
            where = ("placed in the program" if c["storage_key"] in
                     placed_keys else "NOT placed in the program")
            role = ("STYLE REFERENCE ONLY — an example to inspect, never "
                    "source footage to insert or edit"
                    if cmeta.get("role") == "shorts_reference" else
                    "source clip")
            media_lines.append(
                f'  clip "{name}" ({float(dur or 0):.1f}s) — {role}; {state}, '
                f'{where}, storage_key {c["storage_key"]}')
        images = worker_db.run(
            lambda conn: _image_assets(conn, ctx.project_id))
        for a in images:
            name = (a.get("meta") or {}).get("filename") or \
                os.path.basename(a["storage_key"])
            where = ("placed in the program" if a["storage_key"] in
                     placed_keys else "NOT placed in the program")
            media_lines.append(f'  image "{name}" — {where}, storage_key '
                               f'{a["storage_key"]}')
        staged = worker_db.run(dbx.staged_assets, ctx.project_id)
        if staged:
            names = "; ".join(
                f'"{(s.get("meta") or {}).get("filename") or os.path.basename(s["storage_key"])}" '
                f'(storage_key {s["storage_key"]})' for s in staged[:8])
            media_lines.append(
                f"  STAGING TRAY (not on the timeline yet — the user has "
                f"not pressed Submit): {names}. You CAN look_at_asset these "
                "to see and discuss them (a user who says 'did you see my "
                "photo?' deserves an answer about the photo, 2026-08-09); "
                "you cannot PLACE them — for placement, tell the user to "
                "press Submit on the tray.")
    except Exception as e:
        print(f"[state] media inventory failed: {e}", flush=True)

    block = project_state_block(video_line, index_summary, edl_line,
                                history_lines, music_lines,
                                keep_line=keep_line,
                                captions_line=captions_line,
                                program_lines=program_lines,
                                media_lines=media_lines)
    # A SHORTS project's chat is the board's chat. Without this section the
    # parent agent literally does not know its children exist — on
    # 2026-08-09 "add the interstellar music to all of them" got the track
    # laid under the 85-minute original and the reply "the project doesn't
    # contain shorts". The board and the routing rule now ride every turn.
    try:
        board = agent_tools._shorts_children(ctx)
    except Exception:
        board = []
    if board:
        lines = ["", "THE SHORTS BOARD — this project's finished vertical "
                     "shorts, each one its OWN project with its own "
                     "timeline and chat:"]
        for i, c in enumerate(board, 1):
            dur = ""
            try:
                dur = f", {float(c['end']) - float(c['start']):.0f}s"
            except (KeyError, TypeError, ValueError):
                pass
            lines.append(f"  {i}. “{c.get('title') or f'Short {i}'}”{dur}")
        lines.append(
            "When the user asks for a change to THE SHORTS — 'all of "
            "them', 'the shorts', 'short 3', 'add music to them' — use "
            "edit_shorts(instruction, shorts). That request is NEVER an "
            "edit of this parent timeline (the original long video); "
            "only edit here when they explicitly ask about the "
            "original/full video. Prepare anything the instruction needs "
            "first (e.g. fetch the track HERE with find_song/fetch_url), "
            "then name it in the instruction — edit_shorts shares this "
            "project's music/clips/images into every short.")
        block += "\n" + "\n".join(lines)
    elif (ctx.project.get("kind") == "shorts"
          and not ctx.project.get("parent_project_id")
          and ctx.has_main_video):
        # Selecting Shorts now establishes an intake project; indexing does
        # not invent a brief and launch the planner. This state instruction is
        # what turns the user's first real direction into the one intentional
        # make_shorts call, including prompts held while indexing.
        block += (
            "\n\nTHIS IS A SHORTS INTAKE PROJECT. The source is indexed, but "
            "no Shorts run has started because the user must choose the "
            "creative direction first. When the user describes the shorts "
            "they want, call make_shorts exactly once: use their requested "
            "count when stated and preserve their full direction (moments, "
            "topics, length, captions, pacing, tone, and audience) in "
            "style_note. Infer a sensible count only if they ask you to "
            "choose. If they are only asking a question or have not yet "
            "given a creative brief, answer or ask for the missing direction "
            "without starting a run. Do not edit the original long timeline "
            "unless they explicitly ask for that instead.")
    elif ctx.project.get("parent_project_id"):
        # The mirror image of the board block: a GENERATED SHORT must know
        # who its parent is, or an agent sitting on one clip has no idea the
        # other seven exist. On 2026-08-10 an MCP model on child 423 was
        # asked to restyle "all of them", got edit_shorts rejected, and told
        # the user projects could only be switched in the app — because
        # nothing in its state said "you are one clip of board 406".
        try:
            parent = worker_db.run(dbx.get_project,
                                   ctx.project["parent_project_id"])
            if parent:
                sibs = (((parent.get("meta") or {}).get("shorts") or {})
                        .get("clips")) or []
                live = [c for c in sibs if c.get("child_project_id")]
                mine = next((i for i, c in enumerate(live, 1)
                             if c.get("child_project_id")
                             == ctx.project_id), None)
                card = f"card {mine} of {len(live)}" if mine else \
                    f"one of {len(live)} clips"
                block += (
                    f"\n\nTHIS PROJECT IS A GENERATED SHORT — {card} on "
                    f"the Shorts board of parent project {parent['id']} "
                    f"(“{parent.get('title') or ''}”). Edit THIS clip here "
                    "with the normal tools. When the user asks for a "
                    "change to ALL the shorts ('all of them', 'every "
                    "short', 'the shorts'), call edit_shorts(instruction, "
                    "shorts) right from here — it reaches the parent "
                    "board automatically; never claim the parent must be "
                    "opened first.")
        except Exception as e:
            print(f"[state] parent-board note failed: {e}", flush=True)
    # Round 82e: the HOUSE STYLE — what this footage most wants to become
    # when the user gives no brief, measured from the exemplar corpus
    # (worker/grammars/). Context, not command: the block itself says the
    # user's words always win. Never allowed to break a turn.
    if ctx.has_main_video:
        try:
            style = grammar.plan_block(ctx.index)
            if style:
                block += "\n\n" + style
        except Exception as e:
            print(f"[grammar] plan_block failed: {e}", flush=True)
    return block


def capabilities_block():
    """The CAPABILITIES message — generated from the tool registry, so it can
    never advertise a tool this deployment turned off.

    Names only (round 71f): every tool's full contract already rides in the
    request's tool schemas, so the old first-sentence-per-tool digest was
    ~13k chars repeated in every call of every turn — context spent telling
    the model things it was being told anyway. MCP is the same: its
    catalog() ships openai_tools() next to this block."""
    return ("CAPABILITIES — the complete list of write operations that "
            "exist this deployment (each one's full contract is in your "
            "tool schemas): "
            + ", ".join(agent_tools.capability_names())
            + ". Nothing else exists. If the user asks for anything not "
            "listed (motion-TRACKED stickers pinned to moving objects, "
            "true crossfades, custom font files, ...), say so plainly and "
            "offer the closest listed alternative — NEVER describe a "
            "change these tools cannot make, and NEVER claim something is "
            "impossible when a tool above covers it.")


# ── Reply language (round 85) ────────────────────────────────────────────────
# A real session: the user wrote one English sentence, the footage was a
# silent 19s reel carrying foreign burned-in text, and after twenty tool steps
# the reply came back in Russian — the system prompt's language rule (which
# forbids exactly that) sat 9k tokens away from the moment of writing. Two
# deterministic layers now hold the anchor:
#   1. PREVENTION: a one-line note appended to the user's own message naming
#      the script their messages are written in, so the instruction travels
#      WITH the request instead of living only at the top of the prompt.
#   2. CORRECTION: a cross-script check on the drafted reply
#      (_enforce_reply_language) with one forced rewrite — the same shape as
#      _enforce_honesty, and fail-open at every step.
# This is unicode-range counting, not language guessing: it only ever fires on
# flagrant cross-script flips (a Latin-script user answered in Cyrillic),
# never on English-vs-Spanish judgment calls the model must keep owning.

_SCRIPT_RANGES = (
    ("Latin", ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F))),
    ("Cyrillic", ((0x0400, 0x04FF), (0x0500, 0x052F))),
    ("Arabic", ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF))),
    ("Hebrew", ((0x0590, 0x05FF),)),
    ("Greek", ((0x0370, 0x03FF),)),
    ("Devanagari", ((0x0900, 0x097F),)),
    ("Thai", ((0x0E00, 0x0E7F),)),
    ("Hangul", ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))),
    ("Kana", ((0x3040, 0x30FF),)),
    ("Han", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))),
)


def _script_counts(text):
    counts = {}
    for ch in text or "":
        cp = ord(ch)
        for name, ranges in _SCRIPT_RANGES:
            if any(lo <= cp <= hi for lo, hi in ranges):
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def _dominant_script(text, min_letters=6):
    """The script most of `text`'s letters are written in, or None when there
    are too few letters to say (an emoji-only or numbers-only message) or no
    script clearly dominates (a heavy mix). None always means "don't act"."""
    counts = _script_counts(text)
    total = sum(counts.values())
    if total < min_letters:
        return None
    name = max(counts, key=counts.get)
    return name if counts[name] > 0.6 * total else None


# Function-word fingerprints for the same-script flips the script check is
# blind to BY CONSTRUCTION (round 96c: an English "Cut the silences, add big
# captions…" was answered in German — Latin to Latin, so round 85's guard
# passed it; the pt/en walls have the same hole). Only words unique to ONE
# list survive _disjoint_markers, so pt/es/fr cognates can never vote; the
# flip additionally requires ZERO of the user's own markers in the reply, so
# a quoted foreign word in an otherwise faithful reply never trips it. The
# detector never NAMES a language to the user (round 85's worry) — the
# rewrite asks for "the USER'S language", nothing more.
_LANG_MARKERS_RAW = {
    "en": ("the", "and", "is", "are", "was", "were", "this", "that", "with",
           "your", "have", "from", "of", "it's", "i'll", "you're", "should"),
    "de": ("der", "die", "das", "und", "ist", "nicht", "ein", "eine",
           "wurde", "wurden", "werden", "auf", "für", "noch", "aber",
           "auch", "dem", "den", "zum", "zur", "jetzt", "wieder"),
    "pt": ("não", "você", "está", "são", "foi", "também", "já", "uma",
           "com", "para", "mais", "isso", "como", "os", "dos", "mas"),
    # 2026-08-09: "Descargué y añadí…" went out to an English user carrying
    # exactly 2 es hits — the original 15-word list was too thin for a short
    # reply. Widened with function words Spanish doesn't share spelling-wise
    # with pt/fr/it/ro ("hasta", "ahora", "hay", "qué", "sí", "aún", "según").
    "es": ("el", "los", "las", "es", "una", "pero", "también", "está",
           "para", "con", "como", "más", "muy", "esto", "ya", "hay",
           "hasta", "ahora", "qué", "sí", "aún", "según", "añadí",
           "quedó"),
    "fr": ("le", "les", "est", "une", "avec", "pour", "dans", "vous",
           "cette", "mais", "été", "sur", "pas", "aussi", "déjà"),
    "it": ("il", "gli", "è", "una", "con", "per", "anche", "questo",
           "sono", "già", "più", "ma", "della", "delle"),
    # Project 501 (2026-08-09): an English documentary brief got a Romanian
    # handoff. Latin-vs-Latin is invisible to the script guard, just like the
    # earlier English->German incident. Diacritics and common function words
    # give a conservative fingerprint without treating one borrowed word as a
    # language change.
    "ro": ("și", "sau", "este", "sunt", "fost", "are", "cu", "pentru", "din",
           "care", "secunde", "subtitrări", "muzică", "eliminat",
           "adăugat", "păstrat", "redus"),
    # Project 505: the English user got a Romanized-Hindi/Hinglish delivery.
    # These short particles only trigger in combination (>=3 hits, 2:1 lead)
    # and only when the reply contains zero markers from the user's language.
    "hi_latn": ("hai", "hain", "aur", "ka", "ki", "ke", "se", "mein",
                "par", "gaya", "gayi", "kiya", "liye", "nahi", "karo",
                "wala"),
}


def _disjoint_markers(raw):
    counts = {}
    for ws in raw.values():
        for w in ws:
            counts[w] = counts.get(w, 0) + 1
    return {lang: frozenset(w for w in ws if counts[w] == 1)
            for lang, ws in raw.items()}


_LANG_MARKERS = _disjoint_markers(_LANG_MARKERS_RAW)

_MARKER_WORD_RE = re.compile(r"[a-zà-öø-ÿœß']+")


def _marker_hits(text, lang):
    ws = _LANG_MARKERS.get(lang) or frozenset()
    return sum(1 for w in _MARKER_WORD_RE.findall((text or "").lower())
               if w in ws)


def _marker_lang(text):
    """The language whose distinctive function words dominate `text`, or
    None when nothing clears 3 hits with a 2:1 lead over the runner-up —
    abstaining is always safe (None means "don't act"), same contract as
    _dominant_script."""
    words = _MARKER_WORD_RE.findall((text or "").lower())
    if not words:
        return None
    scores = sorted(((sum(1 for w in words if w in ws), lang)
                     for lang, ws in _LANG_MARKERS.items()), reverse=True)
    (s1, lang1), (s2, _) = scores[0], scores[1]
    if s1 >= 3 and s1 >= 2 * s2:
        return lang1
    return None


# Latin letters that plain English never uses. One real day (2026-08-09)
# produced replies in Albanian, Turkish, Portuguese, Spanish and French to
# users who wrote only English — every one of them same-script, and every one
# of them under the 3-hit marker threshold (a 2-sentence reply simply doesn't
# carry three distinctive function words). What those replies DO carry is
# accent mass: ë/ş/ã/é/ó in quantity, where an English reply has none.
_ACCENTED_LATIN_RE = re.compile(
    "[À-ÿĀ-ſȘ-ț]")


def _accented_flip(joined, final):
    """True when the user writes plain-ASCII English and the reply is a
    Latin-script text soaked in accented letters with ZERO English function
    words — the same-script flip the marker vote is too coarse to catch.
    Conservative on purpose: a reply that quotes one accented title ("Café
    del Mar") keeps its English markers and never trips this."""
    if not joined or not final:
        return False
    # The user side must be unambiguously English-shaped: ASCII-dominant
    # AND carrying English function words of its own.
    non_ascii_u = sum(1 for ch in joined if ord(ch) > 127)
    if non_ascii_u > max(2, 0.01 * len(joined)):
        return False
    if _marker_hits(joined, "en") < 2:
        return False
    if _marker_hits(final, "en") > 0:
        return False
    accents = len(_ACCENTED_LATIN_RE.findall(final))
    letters = sum(1 for ch in final if ch.isalpha())
    return accents >= 3 and letters > 0 and accents / letters >= 0.01


def _reply_language_note(user_texts):
    """The anchor line appended to the user's message. It names the SCRIPT (a
    measurement), never a guessed language — 'their language' plus the script
    is enough to hold the anchor, and it can never mislabel a Ukrainian as a
    Russian. Empty when the user hasn't written enough letters to measure."""
    joined = " ".join(t for t in user_texts if t)
    script = _dominant_script(joined)
    if not script:
        return ""
    return (f"\n\n[system note: the user's own messages are written in "
            f"{script} script — write your reply in THEIR language. Speech, "
            "captions or on-screen text inside the footage NEVER set your "
            "reply language.]")


def _build_messages(ctx, worker_db, user_message, attachment_note=""):
    # system_prompt(), not the raw constant: it drops the built-in-library
    # claims when this image shipped no tracks.
    msgs = [{"role": "system", "content": system_prompt()},
            {"role": "system", "content": capabilities_block()}]
    # THE FILMSTRIPS — rebuilt fresh every turn for every video in the
    # project, so the agent's picture of the footage can never go stale. A
    # blind provider strips these on first rejection (_strip_image_parts)
    # and the turn continues on text + look_at's vision fallback.
    #
    # BEFORE the state block, deliberately (round 98): the strips are the
    # LARGEST and most STABLE part of the prompt — identical bytes turn
    # after turn — while the state block changes with every EDL write. With
    # the state first, every turn's strip re-tokenized as a cache MISS;
    # with the strips first they ride the provider's cached prefix and only
    # the (small, text) state re-processes. Same content, same senses,
    # meaningfully faster first token and a cheaper turn.
    if ctx.direct_sight and llm.agent_sees(ctx.agent_model):
        try:
            parts = filmstrip_parts(ctx, worker_db)
            if parts:
                msgs.append({"role": "user", "content": parts})
        except Exception as e:
            print(f"[filmstrip] skipped ({e})", flush=True)
    msgs.append({"role": "system", "content": state_block(ctx, worker_db)})
    # The last few messages, not the last twenty (round 71f). Twenty was up
    # to 40k chars of stale conversation re-read on EVERY call of EVERY
    # turn, and it actively misled: superseded requests ("make the title
    # blue" from ten exchanges ago) sat next to the current one with equal
    # weight. Everything durable about the project lives in the STATE
    # above — the EDL, the scene map, the index — not in old chat. The
    # current request plus the last few messages of context is the job.
    chat = worker_db.run(dbx.recent_chat, ctx.session_id, 4)
    user_texts = []
    for m in chat:
        if m["id"] == user_message["id"]:
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        content = (m["content"] or "")[:2000]
        if content:
            msgs.append({"role": role, "content": content})
            if m["role"] == "user":
                user_texts.append(content)
    user_texts.append(user_message["content"] or "")
    msgs.append({"role": "system", "content":
                 request_intent.request_contract(
                     user_message["content"] or "")})
    msgs.append({"role": "user",
                 "content": user_message["content"][:4000] + attachment_note
                 + _reply_language_note(user_texts)})
    return msgs


def _compact_initial_filmstrip(messages):
    """Drop only the turn's broad filmstrip pixels after planning.

    The first model call needs the complete contact sheets to decide what the
    footage is. Re-sending the same large image payload on every dispatch and
    preview call added latency without adding evidence. Once the model has
    recorded a plan or landed a write, keep the labels and an explicit memory
    note but remove those initial pixels. Exact frames returned later by
    look_at/look_at_asset live in separate messages and are never touched.
    """
    marker = "FILMSTRIPS & STILLS"
    for message in messages:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        first_text = next((part.get("text", "") for part in content
                           if isinstance(part, dict)
                           and part.get("type") == "text"), "")
        if not first_text.startswith(marker):
            continue
        text_parts = [part for part in content
                      if isinstance(part, dict)
                      and part.get("type") == "text"]
        if len(text_parts) == len(content):
            return False
        text_parts[0] = {
            "type": "text",
            "text": ("FILMSTRIPS & STILLS — inspected during the initial "
                     "planning call. Their labels remain below. For any "
                     "new visual decision, use look_at/look_at_asset at "
                     "exact measured moments; do not rely on memory."),
        }
        message["content"] = text_parts
        return True
    return False


def _activity(worker_db, session_id, name, args, result, source=None,
              edl_version=None, change=None):
    res_str = (result or "").replace("\n", " ")
    # Long enough that a diff line PLUS its appended WARNING lines survive —
    # truncating warnings out of the activity feed would hide them from the
    # user entirely.
    if len(res_str) > 600:
        res_str = res_str[:600] + "…"
    # Auto-triggered previews read as "auto preview" in the UI; the raw
    # trigger tag stays in meta for the logs.
    if name == "render_preview" and (args or {}).get("auto"):
        label = "auto preview"
    else:
        arg_str = json.dumps(args or {}, ensure_ascii=False)
        if len(arg_str) > 160:
            arg_str = arg_str[:160] + "…"
        label = f"{name}{arg_str if arg_str != '{}' else '()'}"
    meta = {"tool": name, "args": args}
    if edl_version is not None:
        # The EDL version current when this call ran — lets the studio roll
        # the activity feed back in step with the version stepper.
        meta["edl_version"] = edl_version
    if change:
        # Structural diff of THIS write (edl_diff.change_ranges): the output
        # ranges the edit touched, so the studio can flash them briefly when
        # the next preview lands. Only ever present on the row of an EDL
        # write; read rows and non-writing calls never carry it.
        meta["change"] = change
    if source:
        # Which driver made this call. The studio renders MCP activity exactly
        # like the agent's — it IS the same tool doing the same thing — but the
        # admin views and the logs must be able to tell an outside model's edit
        # from ours.
        meta["source"] = source
    worker_db.run(dbx.add_message, session_id, "activity",
                  f"{label} → {res_str}", meta)


def _user_facing_failure(e):
    """Turn an exception into something a customer can act on.

    This used to be f"({str(e)[:160]})" pasted straight into the chat. When the
    xAI account hit its spending limit on Jul 26 2026, every user in the product
    read this, four times in a row, mid-sentence:

        Something went wrong on my end while editing (Error code: 403 -
        {'code': 'permission-denied', 'error': 'Your team 166666fc-e639-...
        has either used all available credits or reached its mo). Your video
        and edit history are safe — try sending that again.

    Three separate failures: it leaks our provider and internal team id, it
    truncates to garbage so it reads as a corrupted app, and "try sending that
    again" was a lie — a provider with no credit left fails identically every
    time, so it invited people to burn their afternoon retrying. The detail
    still goes to llm_calls and the worker log, where it belongs.
    """
    text = f"{type(e).__name__}: {e}".lower()
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    quota = (status in (402, 403) or "permission-denied" in text or
             any(k in text for k in ("insufficient", "quota", "billing",
                                     "spending limit", "credit balance",
                                     "used all available credits",
                                     "exceeded your current")))
    if quota:
        # Ours to fix, not theirs to retry. Say so, and don't imply a retry.
        return ("I can't reach the editing model right now — that's a problem "
                "on my side, not with your video. Your footage and edit "
                "history are safe and this didn't use any of your credits. "
                "We're on it; please try again a little later.")
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return ("I'm being rate-limited by the model right now. Your video and "
                "edit history are safe and this didn't use any of your "
                "credits — give it a minute and resend that.")
    if "timeout" in text or "timed out" in text:
        return ("That took too long and I had to stop. Your video and edit "
                "history are safe — try again, and if it keeps timing out "
                "break the request into smaller steps.")
    return ("Something went wrong on my end while editing. Your video and "
            "edit history are safe — try sending that again.")


def run_agent_job(worker_db, job):
    project = worker_db.run(dbx.get_project, job["project_id"])
    session_id = project["chat_session_id"]

    def _get_msg(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_messages WHERE id = %s",
                        (job["payload"].get("message_id"),))
            return cur.fetchone()

    user_message = worker_db.run(_get_msg)
    if not user_message:
        def _last_user(conn):
            with conn.cursor() as cur:
                cur.execute("""SELECT * FROM chat_messages
                               WHERE session_id = %s AND role = 'user'
                               ORDER BY id DESC LIMIT 1""", (session_id,))
                return cur.fetchone()
        user_message = worker_db.run(_last_user)
    if not user_message:
        raise RuntimeError("No user message to respond to")

    original = worker_db.run(dbx.latest_asset, job["project_id"], "original")
    index_row = original and original["sha256"] and \
        worker_db.run(dbx.get_index_by_sha, original["sha256"])
    if original and not index_row:
        # A main video WAS uploaded but hasn't finished indexing — wait. (With
        # no original at all we run a canvas turn: the user is building from
        # generated / uploaded images and clips, no main video required.)
        worker_db.run(dbx.add_message, session_id, "assistant",
                      "I can't edit yet — the video hasn't finished "
                      "indexing. Give it a moment and resend your request.")
        return {"status": "no_index"}

    workdir = os.path.join(config.TMP_DIR, f"agent_{job['id']}")
    os.makedirs(workdir, exist_ok=True)

    ctx = agent_tools.ToolContext(worker_db, job, project,
                                  index_row["json"] if index_row else None,
                                  workdir)
    # Round 67: only THIS loop can deliver captured frames into the model's
    # context (an MCP tool call has no loop — its result is text, and a
    # "the picture follows" claim there would be a lie). ToolContext ships
    # with direct sight off; the loop that can honour it turns it on.
    ctx.direct_sight = True
    ctx.user_message = (user_message.get("content") or "")[:4000]
    # Wake the executor NOW, while the model is still reading and planning
    # (round 98): the first render of an idle session otherwise pays the
    # Cloud Run cold start ON TOP of the encode. Fire-and-forget — a failed
    # ping costs nothing and the render path keeps its own error handling.
    if config.REMOTE_EXECUTOR_URL:
        threading.Thread(target=remote.warm_executor, daemon=True).start()
    # A turn spends what the user can PAY FOR — balance + a small grace — and
    # nothing else bounds it.
    #
    # There used to be a flat AGENT_TURN_MAX_CREDITS ceiling on top of this, and
    # it was a number tuned on 16-60s clips: a real customer's 19-min
    # documentary hit "spend cap hit: 43.01 >= 40.0" and the agent was switched
    # off MID-EDIT, leaving an auto-rendered partial v2 that reads to the user
    # as the agent doing a bad job. The work genuinely scales with the footage
    # (a 1553-word transcript needs more looking at than a 16s clip), so a flat
    # ceiling silently punished exactly the long videos real users upload.
    # Removed by decision, not by accident. What still bounds a turn: the
    # user's own balance (below), and AGENT_TURN_TIMEOUT_S as a wall clock.
    balance = worker_db.run(dbx.user_credits_balance, job["user_id"])
    ctx.credit_budget = float(balance or 0) + config.AGENT_TURN_BUDGET_GRACE

    # Which model answers this turn. Three tiers, resolved from ONE query:
    # Frontier ('ai_max') gets the frontier provider for its agent AND its
    # vision; trials and other paid customers get config.PAID_* when it is
    # configured; free accounts get AGENT_MODEL. A trialling user IS
    # is_subscribed — Paddle creates the subscription at checkout and only
    # charges on day 3. Fails to the FREE model on any error: the wrong side of
    # that is a slightly cheaper turn, never a 401 storm at a paying customer.
    try:
        subscribed, plan, trialing = worker_db.run(dbx.user_billing,
                                                   job["user_id"])
    except Exception as e:
        print(f"[agent] billing lookup failed for job {job['id']}: {e}",
              flush=True)
        subscribed, plan, trialing = False, "free", False
    ctx.subscribed = subscribed
    ctx.plan = plan
    ctx.trialing = trialing
    # Round 81: the first-turn A/B lane. The DB is consulted only while the
    # lane is configured (it ships OFF), and any error means "not first" —
    # the wrong side of that is one ordinary-model turn, never a dead one.
    first_turn = False
    if not subscribed and llm.first_turn_available():
        try:
            first_turn = not worker_db.run(dbx.user_has_prior_agent_turn,
                                           job["user_id"], job["id"])
        except Exception:
            first_turn = False
    ctx.llm_client, ctx.agent_model = llm.agent_client_for(
        subscribed, plan, first_turn=first_turn)
    # Vision is reached from eight places that know nothing about plans, so the
    # plan is published to this THREAD for the duration of the turn and cleared
    # in the finally below (worker threads are reused across jobs).
    llm.set_turn_plan(plan if subscribed else "")
    if ctx.agent_model != config.AGENT_MODEL:
        print(f"[agent] job {job['id']}: plan {plan} -> {ctx.agent_model}",
              flush=True)
    elif subscribed and (llm.is_frontier(plan) or llm.is_paid_tier(plan)):
        # A tier SOLD on its model, not delivering it. Loud, because the
        # customer cannot see this and the fix is one env var on the worker.
        print(f"[agent] job {job['id']}: plan {plan} promises a better model "
              f"but its provider is not configured — serving "
              f"{config.AGENT_MODEL}. Set FRONTIER_API_KEY / PAID_API_KEY (or "
              "VISION_API_KEY on the same base URL, which they inherit).",
              flush=True)

    # Persist every model call this turn (agent, honesty regen, vision) to
    # llm_calls for the admin inspector, and accumulate token usage for the
    # spend cap. Payloads are capped + redacted in dbx.insert_llm_call;
    # failures never break the turn.
    def _llm_recorder(purpose, request, response, usage):
        cached_in = llm.cached_input_tokens(usage)
        reasoning = llm.reasoning_tokens(usage)
        audio_in, audio_out = llm.audio_token_counts(usage)
        model = (request or {}).get("model")
        if usage:
            ctx.add_usage(model,
                          getattr(usage, "prompt_tokens", 0) or 0,
                          getattr(usage, "completion_tokens", 0) or 0,
                          cached_in, reasoning, audio_in, audio_out)
        # The cache-hit slice and the reasoning count ride in the response
        # payload rather than in new columns: charge_turn_credits reads them
        # back with response->>'cached_in' / ->>'reasoning_out' and prices each
        # row from its own model. prompt_tokens stays the true total so admin
        # token counts are unaffected.
        #
        # reasoning_out is recorded for EVERY provider, including the ones that
        # already fold it into completion_tokens — it is only charged where
        # model_prices says the provider bills it separately. Recording it
        # unconditionally is what makes that flag checkable against reality
        # instead of assumed.
        if isinstance(response, dict) and (
                cached_in or reasoning or audio_in or audio_out):
            extra = {}
            if cached_in:
                extra["cached_in"] = cached_in
            if reasoning:
                extra["reasoning_out"] = reasoning
            if audio_in:
                extra["audio_in"] = audio_in
            if audio_out:
                extra["audio_out"] = audio_out
            response = dict(response, **extra)
        worker_db.run(dbx.insert_llm_call, job["project_id"], job["id"],
                      purpose, model,
                      request, response,
                      getattr(usage, "prompt_tokens", None) if usage else None,
                      getattr(usage, "completion_tokens", None) if usage else None)
    llm.set_recorder(_llm_recorder)
    try:
        attachment_note = _attachment_context(worker_db, ctx, user_message)
        if (job.get("payload") or {}).get("death_resume"):
            # Round 97 (#1): this job is the reaper's resume of a turn the
            # worker died under. Frame it so the model finishes instead of
            # starting over — its earlier writes are already in the state.
            attachment_note += (
                "\n[system: your previous attempt at this request was cut "
                "off mid-work on our side. Everything you already changed "
                "is saved in the project state — do NOT redo it; finish "
                "what remains of the request and reply.]")
        return _run_loop(ctx, worker_db, job, session_id, user_message,
                         attachment_note)
    except agent_tools.AskUser:
        raise   # never reaches here (handled in loop), but keep explicit
    except Exception as e:
        # The full exception goes to the worker log (and llm_calls) — the chat
        # gets a sentence the user can act on. See _user_facing_failure.
        print(f"[agent] job {job['id']} failed: {type(e).__name__}: {e}",
              flush=True)
        # Always stamped: an unstamped reply collapses the studio's version
        # stepper grouping (bounds come from message stamps), so even a
        # failed turn says which state it left the project in.
        if ctx.versions_written:
            fail_v = ctx.versions_written[-1]
        else:
            try:
                fail_v = ctx.latest_edl()["version"]
            except Exception:
                fail_v = None
        worker_db.run(dbx.add_message, session_id, "assistant",
                      _user_facing_failure(e),
                      ({"edl_version": fail_v} if fail_v is not None
                       else None))
        raise
    finally:
        llm.set_recorder(None)
        # Worker threads are reused across jobs — a plan left on this thread
        # would give the NEXT user's turn this user's vision provider.
        llm.clear_turn_plan()
        shutil.rmtree(workdir, ignore_errors=True)


def _auto_render_if_needed(ctx, worker_db, session_id, timings):
    """If the EDL changed this turn without a successful render_preview,
    render one now (logged + counted). Returns (latest_edl_row, fail_note)."""
    latest = ctx.latest_edl()
    fail_note = None
    if ctx.versions_written and latest["version"] not in ctx.rendered_versions:
        ctx.autorendered = True
        print(f"[honesty] job {ctx.job['id']}: model ended the turn without "
              f"render_preview after writing v{latest['version']} — "
              "auto-rendering", flush=True)
        t0 = time.monotonic()
        # The grade-strip shortcut must not answer THIS call: a strip's
        # pending image has no next step to be seen in, and the whole point
        # here is that the USER gets a real preview of what was written.
        ctx.autorendering = True
        try:
            result = agent_tools.render_preview(ctx)
        finally:
            ctx.autorendering = False
        timings["auto_render_s"] = round(time.monotonic() - t0, 2)
        _activity(worker_db, session_id, "render_preview",
                  {"auto": "model skipped it"}, result,
                  edl_version=latest["version"])
        # Successful proof text deliberately contains rubric phrases such as
        # "FAILED if the subject is clipped". A substring check read that as
        # an encode failure and appended a false warning even though the v4
        # preview was attached. Only the render tool's actual failure prefix
        # means failure.
        if result.startswith("Preview render FAILED:"):
            fail_note = ("\n\n(Heads up: the preview render failed — "
                         f"{result[:200]})")
    return latest, fail_note


def _preview_repair_pushback(ctx, messages, t_start, already_pushed):
    """Give one invalid EDL a corrective model pass, never a blind retry."""
    failure = getattr(ctx, "last_preview_failure", None) or {}
    if already_pushed or not failure.get("agent_repairable"):
        return False
    # A correction needs one model dispatch plus one proof render.  At the
    # wall, preserving the saved EDL and explaining honestly is safer than a
    # half-written repair that the user never sees.
    if config.AGENT_TURN_TIMEOUT_S - (time.monotonic() - t_start) < 120:
        return False
    version = ctx.latest_edl()["version"]
    messages.append({
        "role": "system",
        "content": (
            f"The preview of immutable EDL v{version} failed: "
            f"{str(failure.get('error') or 'unknown error')[:500]}. "
            "Do NOT call render_preview on that same version and do NOT "
            "repeat the exact tool call that produced it. Inspect get_edl, "
            "diagnose the smallest invalid/over-expensive part, and make ONE "
            "corrective edit that creates a new EDL version while preserving "
            "the user's intent. Render the new version once. If no honest "
            "EDL correction can address this failure, make no speculative "
            "change and tell the user the saved edit needs an infrastructure "
            "retry later."
        ),
    })
    return True


# ── TURN FACTS: the reply must match what the tools actually did ──────

EDIT_CLAIM = re.compile(
    r"(?i)("
    r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?|now|just) "
    r"(?:cut|trimmed|removed|applied|added|set|updated|changed|adjusted|"
    r"restored|made|moved|cropped|resized|reframed|inserted|spliced|"
    r"sped|slowed|overlaid|stylized|mastered|beat.?aligned|"
    # the correction family — a four-second zero-write turn answered a
    # complaint with "I corrected the sequence to member 1 → 2 → 3" and
    # sailed past this fence because 'corrected' wasn't in it (Aug 3 2026).
    # A verb lexicon lags the model's vocabulary; when a new fabrication
    # verb appears in prod, it gets added HERE with its incident.
    r"corrected|fixed|reordered|re-?ordered|rearranged|resequenced|"
    r"restructured|rebuilt|redid|redone|revised|reworked|swapped|"
    r"replaced|reversed|retimed|re-?cut|shortened|extended|lengthened|"
    r"tightened|reshaped|rebalanced|reorgani[sz]ed)\b"
    r"|\b(?:cuts?|changes?|edits?|adjustments?)(?: (?:were|have been|are|got))? "
    r"(?:applied|made|done)\b"
    r"|\bapplied (?:the |a )?(?:cut|change|edit|style)"
    r"|\b(?:cropped|resized|reframed|converted) to\b"
    # status adverbs right after the verb mark an honest state answer
    # ("captions are still static", "are already karaoke"), not a claim
    r"|\bcaptions? (?:are|is|were|have been|now)"
    r"(?! not\b| still\b| already\b| currently\b| unchanged\b)[^.\n]{0,60}"
    r"(?:red|blue|green|yellow|white|black|orange|purple|pink|"
    r"#[0-9A-Fa-f]{6}|top|bottom|middle|cent(?:er|re)|bigger|smaller|"
    r"karaoke|dynamic|pops?|light(?:s|ing)? up|word.by.word|highlight|"
    r"premium|presets?|clean|documentary|broadcast|retro|neon|podcast|"
    r"beast|elegant|serif|uppercase|all.caps|"
    r"emphasi[sz])"
    r"|\bis now (?:red|blue|green|yellow|white|black|orange|purple|pink|"
    r"#[0-9A-Fa-f]{6}|at the top|at the bottom|in the middle|centered|"
    r"cropped|9:16|16:9|1:1|4:5|vertical|square|portrait|landscape|"
    r"bigger|smaller|graded|color.?graded|vibrant|cinematic|vintage)\b"
    r"|\b(?:font|colou?r|style|frame|aspect ratio) (?:is|was|has been) "
    r"(?:changed|set|updated|applied)\b"
    # effects claims — "Added a vibrant grade, a punch-in and a fade to
    # black" (bare past-participle openers have no I/we/now subject, so the
    # first alternation misses them). Negated participles ("haven't added",
    # "never applied") are honest, not claims.
    r"|\b(?<!n't )(?<!n’t )(?<!not )(?<!never )(?<!no )"
    r"(?:added|applied|enabled)\b[^.\n]{0,60}"
    r"\b(?:grades?|color.?grades?|zooms?|punch.?ins?|fades?|filters?|"
    r"karaoke|highlights?|transitions?|dips?|ken.?burns|animations?|"
    r"sound.?effects?|sfx|whoosh(?:es)?|swipes?|risers?|impacts?|"
    r"booms?|sub.?drops?|glitch(?:es)?|zaps?|dings?|chimes?|stingers?|"
    r"overlays?|texts?|titles?|title.?cards?|lower.?thirds?|callouts?|"
    r"speed(?:.?ramps?)?|slow.?motion|stylize|grain|vignettes?|glows?|"
    r"vhs|shakes?|looks?|loudness|master(?:ing)?|sound.?design)\b"
    r"|\b(?<!no )(?:color.?grade|grade|zoom|punch.?in|"
    r"fades?(?:[- ]?(?:in|out)| to black)?|filter|transitions?|"
    r"ken.?burns|animations?|overlays?|texts?|titles?|lower.?thirds?|"
    r"speed(?:.?ramps?)?|slow.?motion|stylize|grain|vignettes?|looks?|"
    r"loudness|master(?:ing)?|sound.?design) "
    r"(?:is|was|has been|are|were) (?:now )?"
    r"(?:added|applied|set|enabled|active|in place)\b"
    # speed statives — "the intro is now sped up", "that section plays at
    # 2x", "the clip is in slow motion". Same shape as the audio branch, so
    # honest offers ("I can slow it down") never trip the fence.
    r"|\b(?<!no )(?:video|clip|footage|section|segment|intro|outro|part|"
    r"middle|moment)\b[^.\n]{0,50}\b(?:is|are|was|were|has been|have been) "
    r"(?:now )?(?:sped.?up|slowed(?:.?down)?|in slow.?motion|"
    r"(?:running |playing )?at [0-9]+(?:\.[0-9]+)?x)\b"
    # mastering statives — "the mix is mastered to -14 LUFS"
    r"|\b(?<!no )(?:mix|audio|export|loudness)\b[^.\n]{0,40}"
    r"\b(?:is|was|has been) (?:now )?(?:mastered|normali[sz]ed to)\b"
    r"|\bcaptions? (?:now )?(?:fade|pop|slide) in\b"
    # audio claims — "The music now plays only from 0.0 to 15.0 seconds…"
    # Stative/perfect constructions only, so honest offers ("I can make the
    # music quieter") don't trip the guard, and not negated ("No music was
    # added" is an honest refusal, not a claim).
    r"|\b(?<!no )(?:music|audio|track|song|soundtrack|voice.?over|narration|"
    r"sound)\b"
    r"[^.\n]{0,60}\b(?:now plays|plays? only|plays? from|is cut|"
    r"cut (?:after|off)|(?:is|are|was|were|has been|have been) (?:now )?"
    r"(?:added|removed|lowered|reduced|quieter|louder|softer|ducked|muted|"
    r"cut|trimmed|gone))\b"
    r"|\bvolume (?:is |was |has been )?(?:lowered|raised|reduced|increased|"
    r"set|changed|adjusted)\b"
    r"|\b(?:lowered|raised|reduced|boosted) (?:the )?(?:volume|music|audio)\b"
    r")")
RENDER_CLAIM = re.compile(
    r"(?i)(\b(?<!no )preview (?:v?\d+ )?(?:is |was |has been )?"
    r"(?:now )?(?:rendered|ready|updated|attached|refreshed|playing)\b"
    r"|\brendered (?:a |the )?(?:new )?preview\b|\bre-?rendered\b"
    r"|\brendering (?:the |a )?(?:new )?preview\b)")
DENY_CLAIM = re.compile(
    r"(?i)(\bedl (?:did not|didn't) change\b"
    r"|\bnothing (?:was |has been )?changed\b"
    r"|\bno changes? (?:were|was|have been) made\b"
    r"|\bdidn'?t (?:change|modify|touch) (?:the )?(?:edl|edit|video|anything)\b"
    r"|\b(?:edit|edl) (?:is|remains) unchanged\b)")


NEGATORS = re.compile(r"(?i)\b(?:no|nothing|none|haven'?t|hasn'?t|"
                      r"didn'?t|never|wasn'?t|weren'?t)\b")


def _negated_claim(draft, m):
    """True when the matched claim sits in a sentence that negates it —
    "No color grade was applied", "Nothing was added" — which is an honest
    refusal, not a fabrication. Only the words BEFORE the match in the same
    sentence count."""
    sent_start = max(draft.rfind(".", 0, m.start()),
                     draft.rfind("\n", 0, m.start())) + 1
    return bool(NEGATORS.search(draft[sent_start:m.start()]))


# The caption-animation alternation is the only EDIT_CLAIM branch that can
# match inside an offer sentence ("I can make the captions fade in") because
# it matches bare present tense; every other branch needs a perfect/stative
# construction that offers don't use. So the modal guard applies only to it.
CAPTION_ANIM_CLAIM = re.compile(r"(?i)^captions? (?:now )?(?:fade|pop|slide) in\b")
OFFER_WORDS = re.compile(r"(?i)\b(?:can|could|would|shall|should|"
                         r"able to|happy to|want)\b")


def _offered_claim(draft, m):
    """True when a caption-animation match is an offer, not a claim."""
    if not CAPTION_ANIM_CLAIM.match(m.group(0)):
        return False
    sent_start = max(draft.rfind(".", 0, m.start()),
                     draft.rfind("\n", 0, m.start())) + 1
    return bool(OFFER_WORDS.search(draft[sent_start:m.start()]))


def _norm_text(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


ECHO_MIN_CHARS = 120
ECHO_RATIO = 0.92
# _build_messages caps history content at 2000 chars; normalization only
# shrinks text, so anything at/over this length may be a truncated original
ECHO_TRUNC_CHARS = 1900


def _echo_violation(draft, messages):
    """A draft that repeats a previous assistant reply nearly verbatim
    answers nothing — it re-describes an old turn's work as if it just
    happened (the model regurgitates its last message when the user
    complains, and every claim in it is stale). Only long replies count:
    short answers ("Yes, the captions are red.") can legitimately repeat.
    Compared over FULL strings — a longer fresh reply that merely shares an
    opener with an old one is not an echo — except when the stored history
    copy was truncated, where only its length is comparable."""
    d = _norm_text(draft)
    if len(d) < ECHO_MIN_CHARS:
        return None
    for m in messages:
        if m.get("role") != "assistant" or m.get("tool_calls"):
            continue
        prev = _norm_text(m.get("content"))
        if len(prev) < ECHO_MIN_CHARS:
            continue
        d_cmp = d[:len(prev)] if len(prev) >= ECHO_TRUNC_CHARS else d
        if difflib.SequenceMatcher(None, d_cmp, prev).ratio() >= ECHO_RATIO:
            return ("repeats a previous reply nearly verbatim instead of "
                    "answering the user's LATEST message — everything it "
                    "describes happened on an earlier turn, not this one")
    return None


def _reply_violations(draft, wrote, previewed, acted=None):
    """Each violation names the exact fabricated claim it matched, so the
    regeneration correction (and the logs) point at the offending words.
    acted covers non-EDL actions (a generated image): "I made an image" is
    a truthful edit-verb sentence on a zero-write turn, while a denial
    check must still key on the EDL alone."""
    acted = wrote if acted is None else acted
    v = []
    # An explicit denial ("nothing was changed") dominates — its own words
    # ("changes were made") must not read as a change claim.
    m = next((mm for mm in EDIT_CLAIM.finditer(draft)
              if not _negated_claim(draft, mm)
              and not _offered_claim(draft, mm)), None)
    if not acted and m and not DENY_CLAIM.search(draft):
        v.append(f'claims edits ("{m.group(0).strip()}"), but no write tool '
                 "succeeded this turn")
    m = RENDER_CLAIM.search(draft)
    if not previewed and m:
        v.append(f'claims a render ("{m.group(0).strip()}"), but no preview '
                 "was rendered this turn")
    m = DENY_CLAIM.search(draft)
    if wrote and m:
        v.append(f'denies changes ("{m.group(0).strip()}"), but the EDL DID '
                 "change this turn")
    return v


_AUDIO_DENIAL = re.compile(
    r"(?i)(audio playback (?:was|is) unavailable|"
    r"(?:this )?environment (?:does not|doesn't) provide (?:separate )?"
    r"listen(?:ing)?|(?:i |we )?(?:cannot|can't|could not|couldn't) "
    r"(?:personally )?(?:hear|listen)|cannot confirm[^.\n]{0,80}heard by ear|"
    r"listening is unavailable)")


def _audio_denial_violation(ctx, draft):
    """Catch a false 'I cannot hear' after the rendered mix was reviewed."""
    try:
        latest = ctx.latest_edl()["version"]
    except Exception:
        return None
    if latest not in (getattr(ctx, "audio_reviewed_versions", None) or set()):
        return None
    match = _AUDIO_DENIAL.search(draft or "")
    if not match:
        return None
    return (f'denies listening ("{match.group(0).strip()}"), but the '
            "dedicated audio reviewer DID hear this rendered EDL version")


def _assets_made_note(ctx):
    """', but <what> was saved to your project' — or '' when nothing was.

    Used by every system-authored 'nothing changed' message. Those all key on
    ctx.versions_written, which is true of the EDL and NOT of the project: a
    turn can download a file or generate an image and then time out, and
    reporting that as 'nothing was changed' sends the user off to re-paste a
    link whose media is already in their media picker."""
    def _n(count, singular, plural):
        return f"{count} {singular if count == 1 else plural}"

    made = []
    if getattr(ctx, "urls_fetched", None):
        made.append(_n(len(ctx.urls_fetched),
                       "file downloaded from your link",
                       "files downloaded from your links"))
    if getattr(ctx, "images_generated", None):
        made.append(_n(len(ctx.images_generated),
                       "generated image", "generated images"))
    if getattr(ctx, "web_recordings", None):
        made.append(_n(len(ctx.web_recordings),
                       "website recording", "website recordings"))
    if getattr(ctx, "audio_extracted", None):
        made.append(_n(len(ctx.audio_extracted),
                       "soundtrack taken out of a video you uploaded",
                       "soundtracks taken out of videos you uploaded"))
    if not made:
        return ""
    return (" — but " + " and ".join(made)
            + " is saved to your project and ready to use")


def _quality_handoff(ctx):
    """Small, deterministic quality contract for the reply and studio CTA."""
    try:
        latest = ctx.latest_edl()["version"]
    except Exception:
        return {"quality_status": "unchecked", "export_ready": False}
    preview = getattr(ctx, "last_preview", None) or {}
    if preview.get("edl_version") != latest:
        return {"quality_status": "unchecked", "export_ready": False}

    findings = []
    report = getattr(ctx, "last_visual_critic", None) or {}
    for line in preview_critic.repair_lines(report)[:3]:
        findings.append(line)
    for line in (getattr(ctx, "last_audio_qc_findings", None) or [])[:2]:
        findings.append("audio QC: " + str(line))
    audio_review = str(getattr(ctx, "last_audio_review", None) or "").strip()
    if audio_review.upper().startswith("FIX"):
        findings.append("audio review: " + audio_review[:240])
    # Deterministic taste findings (dead air, excessive devices, invalid mix)
    # count too, but only for the exact latest version.
    if getattr(ctx, "last_taste_version", None) == latest:
        for line in (getattr(ctx, "last_taste", None) or []):
            if line not in findings:
                findings.append(str(line))
            if len(findings) >= 4:
                break
    findings = findings[:4]
    if findings:
        return {"quality_status": "repair", "quality_findings": findings,
                "export_ready": False}
    status = "pass" if report.get("verdict") == "pass" else "unchecked"
    return {"quality_status": status, "quality_findings": [],
            "export_ready": True}


def _disclose_outstanding_quality(ctx, text):
    """Never let a known-bad preview be described as a finished handoff."""
    quality = _quality_handoff(ctx)
    if quality.get("quality_status") != "repair":
        return text
    first = quality.get("quality_findings", ["a quality issue remains"])[0]
    return (text.rstrip() +
            "\n\nQuality check: this preview still needs another repair "
            "pass before I can call it export-ready — " + first[:360] + ".")


def _turn_facts(ctx, start_version):
    latest = ctx.latest_edl()
    if ctx.versions_written:
        edl_line = (f"EDL: v{start_version} -> v{latest['version']} "
                    f"({len(ctx.versions_written)} new version(s))")
    else:
        edl_line = f"EDL: unchanged (v{latest['version']})"
    writes = ", ".join(ctx.write_calls) if ctx.write_calls else "none"
    if ctx.images_generated:
        images = (", ".join(i["storage_key"] for i in ctx.images_generated)
                  + " — a generated image is IN the video only if an "
                    "insert_media write also succeeded")
    else:
        images = "none"
    if ctx.urls_fetched:
        # Same caveat as generated images, for the same reason: downloading a
        # song is not scoring the video with it, and a turn that fetched but
        # never placed the file must not be reported as an edit.
        fetched = (", ".join(f"{f['filename']} ({f['storage_key']})"
                             for f in ctx.urls_fetched)
                   + " — downloaded media is IN the video only if an "
                     "insert_media/add_music write also succeeded")
    else:
        fetched = "none"
    if getattr(ctx, "web_recordings", None):
        fetched += ("; website recordings: "
                    + ", ".join(r["storage_key"] for r in ctx.web_recordings)
                    + " — a recording is IN the video only if an "
                      "insert_media/add_overlay write also succeeded")
    if getattr(ctx, "audio_extracted", None):
        fetched += ("; audio taken out of an uploaded video: "
                    + ", ".join(f"{a['storage_key']} (from {a['from']})"
                                for a in ctx.audio_extracted)
                    + " — that sound is IN the video only if an "
                      "add_music/add_sfx/add_voiceover write also succeeded, "
                      "and the source video's PICTURE is not in the edit")
    if ctx.last_preview is not None:
        pv = (f"rendered v{ctx.last_preview.get('edl_version')} "
              f"({ctx.last_preview.get('duration_s')}s)")
        if ctx.last_selfcheck:
            pv += f"; self-check: {ctx.last_selfcheck[:120]}"
    else:
        pv = "none"
    heard_versions = getattr(ctx, "audio_reviewed_versions", None) or set()
    audio_review = ("rendered v" + ", v".join(str(v)
                    for v in sorted(heard_versions))) if heard_versions \
        else "none"
    return ("TURN FACTS (system-verified):\n"
            f"- {edl_line}\n"
            f"- Successful write tools this turn: {writes}\n"
            f"- Images generated this turn: {images}\n"
            f"- Media downloaded from links this turn: {fetched}\n"
            f"- Preview: {pv}\n"
            f"- Program audio reviewed by the dedicated listener: "
            f"{audio_review}\n"
            "Rules: your reply may not claim any change, render, or setting "
            "that is not present in these facts. If no writes occurred, say "
            "plainly that nothing was changed and why, or what you need "
            "from the user.")


# Nearest supported alternative for the honest fallback, keyed on what the
# user asked for. User-facing phrasing (no tool names).
ALTERNATIVE_HINTS = [
    # A pasted link is the most specific signal there is — it beats every
    # keyword hint below, because "here's a song: <youtube link>" also matches
    # the music hint, which would answer with the built-in library and never
    # mention that we could simply have fetched the link.
    # Every alternative must match a URL-SHAPED string, never a bare word.
    # A plain `youtu\.?be` also matched the word "youtube" in ordinary prose
    # ("make it look like a youtube video"), and being first in the scan it
    # stole those messages from the aspect-ratio and caption hints.
    (re.compile(r"(?i)https?://\S+|\bwww\.\S+\.\w|\byoutu\.be/|"
                r"\byoutube\.com/|\btiktok\.com/|\bvimeo\.com/|"
                r"\bsoundcloud\.com/|\bdrive\.google\.com|\bdropbox\.com/"),
     "What I CAN do with a link: download the video, song or image behind it "
     "and put it straight into the edit — direct file links (Dropbox, Drive, "
     "a CDN) and page links (YouTube, TikTok, Vimeo, SoundCloud) both work. "
     "Paste the URL and say what you want done with it."),
    # censor requests first: "remove the username/watermark" also contains
    # 'remove' (the cut hint) and 'logo/overlay' (the insert hint), and the
    # most specific hint must win the first-match scan
    (re.compile(r"(?i)username|user.?name|gamertag|nametag|name.?tag|"
                r"watermark|censor|blur|pixelat|black.?out|"
                r"(?:remove|hide|cover|get rid of)[^.\n]{0,40}"
                r"(?:text|logo|name|handle|tag|overlay)"),
     "What I CAN do: actually REMOVE burned-in text or an object — I find "
     "where it sits, repaint those pixels and rebuild the picture behind "
     "them, so it is gone rather than covered (I can also blur, pixelate or "
     "bar it if you prefer). Tell me what to take out and I'll show you a "
     "preview."),
    # sfx BEFORE effects: the effects regex matches the bare word
    # 'effect', so "add some sound effects" was answered with colour
    # grades and zooms. Most specific wins the first-match scan.
    (re.compile(r"(?i)sound.?effects?|\bsfx\b|whoosh|swoosh|swipe|riser|"
                r"impact|boom|braam|sub.?drop|glitch|zap|ding|chime|buzz|"
                r"\bclick\b|\bstinger\b|shutter|\bhit\b"),
     "What I CAN do: drop one-shot sound effects on exact moments — "
     "whooshes and swipes on cuts, impacts, booms and sub-drops on reveals, "
     "risers into a transition, plus clicks, pops, glitches, zaps, dings and "
     "camera shutters — real recorded sounds I find on the web, at any "
     "volume, and I can move or remove them afterwards."),
    # speed BEFORE effects: "slow motion" contains 'motion', which the
    # effects regex matches, and the most specific hint must win the scan
    (re.compile(r"(?i)slow.?mo(?:tion)?\b|\bspeed\b|speed.?up|sped|"
                r"time.?lapse|fast.?forward|\b[0-9](?:\.[0-9])?x\b"),
     "What I CAN do: speed up or slow down any part of the video (0.25x to "
     "4x) with pitch-preserved audio — mild slow motion (0.6x and up) looks "
     "smooth; more extreme slow motion visibly steps because frames are "
     "duplicated, not synthesized."),
    # overlays/text BEFORE effects and insert: "animated title" contains
    # 'animat' (effects) and "logo" also lives in the insert hint — a
    # title/overlay ask deserves the overlay answer. (?<!sub) keeps
    # "subtitles" with the caption hint below.
    (re.compile(r"(?i)overlay|picture.?in.?picture|\bpip\b|sticker|"
                r"lower.?third|(?<!sub)title\b|title.?cards?|"
                r"big.?numbers?|chapter.?mark|text.?on.?screen|\blogo\b"),
     "What I CAN do: draw an image or clip over the footage — "
     "picture-in-picture, a corner logo, a full-frame cover — at a fixed "
     "or slowly drifting position, and burn designed text templates: "
     "title cards, lower thirds, callouts, big numbers, quotes, chapter "
     "markers. Overlays hold their position; they can't track a moving "
     "object in the footage."),
    # effects next: zoom/filter/fade phrasings often also contain 'animated'
    # or 'tiktok', and the most specific hint must win the first-match scan
    (re.compile(r"(?i)effect|filter|grade|zoom|punch|fade|transition|"
                r"viral|engag|animat|ken.?burns|motion|beat|stylize|"
                r"grain|vignette|vhs|\blook\b|loud"),
     "What I CAN do: color-grade the whole video (presets plus custom "
     "exposure/contrast/saturation/temperature), punch-in or smooth Ken "
     "Burns zooms (including aimed at a subject), seven transition styles "
     "at every cut (dips, whips, zoom-punch, glitch, flash), fade in/out, "
     "stylize effects (film grain, vignette, glow, VHS), beat-aligned "
     "cuts and automatic punch-ins on the most stressed words, one-call "
     "looks (hype/clean/cinematic/luxury/meme), loudness mastering for "
     "social platforms, karaoke captions, animated caption entrances, and "
     "Ken Burns motion on inserted images."),
    (re.compile(r"(?i)9.?:.?16|16.?:.?9|1.?:.?1|4.?:.?5|aspect|ratio|"
                r"vertical|portrait|square|crop|tiktok|reels?|shorts?"),
     "What I CAN do: change the output frame to 16:9, 9:16, 1:1 or 4:5 with "
     "a center-crop or a padded fit."),
    (re.compile(r"(?i)caption|subtitle|font|outline|middle|"
                r"cent(?:er|re)"),
     "What I CAN do with captions: premium preset looks with real fonts — "
     "podcast (keywords light up / get a highlight box, numbers render "
     "huge), beast (loud all-caps karaoke), karaoke (a box follows the "
     "spoken word), elegant (serif-accented lower third) — plus color, "
     "size, position, keyword emphasis words, karaoke mode and entrance "
     "animations."),
    (re.compile(r"(?i)voice.?over|narrat|music|song|soundtrack|audio|volume"),
     "What I CAN do: score the edit with music on any time range — a "
     "track I find online by genre/vibe, a specific song found by NAME "
     "(find_song), any link they paste (a song URL, "
     "YouTube, SoundCloud...), or the user's own "
     "upload — loop it to fill the video, fade it in and out, start it "
     "partway in, swap one track for another, make it louder or quieter, "
     "or remove it. I can also lay an uploaded voiceover over the edit "
     "(other audio ducks while it speaks)."),
    (re.compile(r"(?i)insert|splice|b.?roll|logo|image|photo|clip|overlay|"
                r"generat|create|draw|ai.?(?:image|art)|hair|face|character"),
     "What I CAN do: splice an uploaded video clip or image in at ANY "
     "point — even mid-sentence (the take is split at a word edge) — and "
     "generate images with AI (from a description, or by restyling a "
     "frame of your video) that get spliced in as full-frame still "
     "moments."),
    (re.compile(r"(?i)cut|trim|remove|shorten|tighten|silence|pause"),
     "What I CAN do: cut or restore any time range with word-accurate "
     "boundaries, and remove silences."),
]

FALLBACK_REPLY = ("I wasn't able to make that change — it needs a "
                  "capability I don't have yet; nothing was modified this "
                  "turn.")

# Injected when a step burned its whole completion budget without emitting a
# single token the API would show us. Deliberation is the thing to cut: the
# model has already read the state (that is what the earlier steps were for),
# so the useful next move is the smallest concrete write, not more thinking.
_TRUNCATED_NUDGE = (
    "Your last step produced NO output at all — it ran past the token limit "
    "while deliberating. Stop planning and ACT NOW: make ONE tool call that "
    "starts the user's request, or, if you genuinely need something from "
    "them, reply in two short sentences saying exactly what. Do not restate "
    "the plan, do not re-read state you already have.")

_CUTOFF_NUDGE = (
    "Your reply was CUT OFF mid-sentence by the token limit — the user must "
    "never see a message that stops in the middle of a word. Send the "
    "complete reply again, substantially shorter: lead with what you did, "
    "drop the play-by-play.")


def _nearest_alternative(user_text):
    for rx, hint in ALTERNATIVE_HINTS:
        if rx.search(user_text or ""):
            # A deployment with link fetching switched off must not offer it.
            # Falling through to the next matching hint (rather than returning
            # nothing) keeps a pasted music link answered by the music hint.
            if "What I CAN do with a link" in hint \
                    and not config.URL_FETCH_ENABLED:
                continue
            # A deployment with music search off must not offer to find
            # tracks (round 98 — found music replaced the deleted bundled
            # library).
            if "track I find online" in hint \
                    and not music_search.available():
                return ("What I CAN do: mix music you upload under the edit "
                        "on any time range, loop it to fill the video, fade "
                        "it in and out, make it louder or quieter, or remove "
                        "it. I can also lay an uploaded voiceover over the "
                        "edit (other audio ducks while it speaks).")
            # Same honesty for named-song link finding, which gates
            # separately (it rides the fetch/extractor path).
            if "find_song" in hint and not song_find.available():
                hint = hint.replace(
                    "a specific song found by NAME (find_song), ", "")
            # A deployment with sfx search off must not offer found sounds.
            if "sounds I find on the web" in hint \
                    and not sfx_search.available():
                return ("What I CAN do: place a sound file you upload at an "
                        "exact moment in the edit, set how loud it is, and "
                        "move or remove it afterwards.")
            if ("generate images with AI" in hint
                    and not llm.image_available()):
                return ("What I CAN do: splice an uploaded video clip or "
                        "image in at ANY point — even mid-sentence (the "
                        "take is split at a word edge) — attach or upload "
                        "it and tell me where.")
            return hint
    return None


def _enforce_honesty(ctx, client, messages, tools, draft, start_version,
                     honesty, user_text=""):
    """Deterministic check of the drafted reply against the turn facts.
    On violation: one forced regeneration with a correction naming the exact
    fabricated claims. If the redraft STILL fabricates on a zero-write turn,
    the draft is DISCARDED — the user only ever sees a system-authored
    honest reply; the discarded text goes to the job result for admin
    inspection. (A wrote-but-denies redraft keeps the corrective-note path:
    a denial is wrong but not a fabrication worth suppressing.)"""
    wrote = bool(ctx.versions_written)
    acted = bool(ctx.versions_written or ctx.images_generated
                 or ctx.urls_fetched
                 or getattr(ctx, "web_recordings", None)
                 or getattr(ctx, "audio_extracted", None))
    previewed = ctx.last_preview is not None
    viol = _reply_violations(draft, wrote, previewed, acted)
    audio_denial = _audio_denial_violation(ctx, draft)
    if audio_denial:
        viol.append(audio_denial)
    # Echo detection only polices turns that DID nothing: a working turn's
    # summary may legitimately resemble the last one (same request repeated),
    # and its content claims are already checked against the turn facts.
    echo = None if (acted or previewed) else _echo_violation(draft, messages)
    if echo:
        viol.append(echo)
    if not viol:
        return draft
    honesty["false_claims"] += 1
    facts = _turn_facts(ctx, start_version)
    print(f"[honesty] job {ctx.job['id']}: reply violates turn facts "
          f"({'; '.join(viol)}) — forcing one regeneration", flush=True)
    msgs = messages + [
        {"role": "assistant", "content": draft},
        {"role": "system",
         "content": facts + "\n\nYour draft above violates these facts: it "
         + "; ".join(viol) + ". Each quoted phrase is a fabrication — none "
         "of it happened. Rewrite your reply to match the facts exactly; "
         "do not claim anything the facts do not show."},
    ]
    redraft = ""
    model = ctx.agent_model or config.AGENT_MODEL
    try:
        resp = llm.create_with_dialect(
            client, model, msgs, tools=tools,
            tool_choice="none", temperature=config.AGENT_TEMPERATURE,
            max_tokens=config.AGENT_REPLY_MAX_TOKENS)
        llm.record("honesty_regen",
                   {"model": model, "messages": msgs[-2:],
                    "note": "regeneration after turn-facts violation"},
                   {"content": (resp.choices[0].message.content or "")},
                   getattr(resp, "usage", None))
        redraft = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[honesty] regeneration failed: {e}", flush=True)
    if redraft and not _reply_violations(redraft, wrote, previewed, acted) \
            and not _audio_denial_violation(ctx, redraft) \
            and (acted or previewed
                 or not _echo_violation(redraft, messages)):
        return redraft
    honesty["false_claims"] += 1
    honesty["corrective_note"] = True
    honesty["discarded_drafts"] = [d for d in (draft, redraft) if d]
    if wrote or ctx.images_generated or ctx.urls_fetched \
            or getattr(ctx, "audio_extracted", None):
        # Real work happened, so the reply is corrected rather than discarded.
        # The note has to say WHICH work, though: routing a fetch-only turn
        # into the fallback below told the user nothing happened and that we
        # cannot fetch links, while their downloaded file sat in the project —
        # a lie in the opposite direction to the one being guarded against.
        # Equally, "DID modify the edit" is false when only an asset was
        # created, so each case gets its own wording.
        if wrote:
            note = ("this turn DID modify the edit — see the editing steps "
                    "above")
        else:
            made = []
            if ctx.urls_fetched:
                made.append("the linked media WAS downloaded")
            if ctx.images_generated:
                made.append("an image WAS generated")
            if getattr(ctx, "audio_extracted", None):
                made.append("the audio WAS taken out of the video you "
                            "uploaded")
            note = ("this turn did NOT change the edit, but "
                    + " and ".join(made) + " and saved to the project")
        if _audio_denial_violation(ctx, redraft or draft):
            # Do not preserve the false sentence underneath a corrective note.
            # The language guard that follows can translate this system-authored
            # fact when the user is not writing in English.
            changed = ("modified the edit and " if wrote else "")
            return ("Done — this turn " + changed + "rendered the preview. "
                    "The dedicated audio reviewer listened to the rendered "
                    "program mix; see the verified steps above for the exact "
                    "changes and audio judgment.")
        print(f"[honesty] job {ctx.job['id']}: regeneration still misreports "
              "real work — posting a corrective note", flush=True)
        return f"*(system: {note})*\n\n" + (redraft or draft)
    # Zero-write fabrication that survived regeneration: never publish it.
    honesty["fallback_reply"] = True
    print(f"[honesty] job {ctx.job['id']}: regeneration still fabricates — "
          "discarding both drafts and posting the system fallback",
          flush=True)
    hint = _nearest_alternative(user_text)
    return FALLBACK_REPLY + (f"\n\n{hint}" if hint else "")


def _language_flip(joined, user_text, final):
    """(kind, user_side, reply_side) when `final` is written in a different
    script OR language than the user's own messages, else None.

    Script first (round 85's check, unchanged): fires only when the reply is
    dominated by a script that appears NOWHERE in the user's conversation —
    if the user has written in that script ANYWHERE it is their call, not a
    flip; checking only the latest message here while deciding their script
    from the history would rewrite a bilingual user's deliberate reply.

    Then the same-script class the script check is blind to by construction
    (round 96c: English "Cut the silences, add big captions…" answered in
    German — Latin to Latin): function-word fingerprints on both sides, and
    the reply must additionally contain ZERO of the user's own markers, so
    a reply that quotes a foreign word or mixes languages is left alone."""
    user_script = _dominant_script(user_text) or _dominant_script(joined)
    reply_script = _dominant_script(final, min_letters=10)
    if user_script and reply_script and reply_script != user_script \
            and _script_counts(joined).get(reply_script, 0) == 0:
        return ("script", user_script, reply_script)
    u_lang, r_lang = _marker_lang(joined), _marker_lang(final)
    if u_lang and r_lang and u_lang != r_lang \
            and _marker_hits(final, u_lang) == 0:
        return ("words", u_lang, r_lang)
    # The accent-mass net under the marker vote: same-script flips into
    # languages the marker table doesn't know (Albanian, Turkish) or knows
    # too thinly for a two-sentence reply (es/pt/fr under 3 hits).
    if _accented_flip(joined, final):
        return ("words", "en", "a non-English Latin-script language")
    return None


def _enforce_reply_language(ctx, client, messages, tools, final, user_text,
                            honesty):
    """Deterministic cross-script check of the drafted reply against the
    user's own message, with one forced rewrite — _enforce_honesty's shape
    applied to round 85's bug (an English one-liner answered in Russian).

    Fires ONLY when the reply is dominated by a script that appears NOWHERE
    in the user's message — never on same-script language pairs, never when
    either side is too short to measure — so it cannot cage a legitimate
    reply; it only catches the flip the system prompt already forbids.
    Fail-open at every step: any doubt returns the draft unchanged."""
    # MEASURE THE USER FROM THE WHOLE CONVERSATION, NOT THE LAST MESSAGE.
    # _dominant_script needs a handful of letters to name a script, so a short
    # reply ("Sure", "ok", "نعم") measured nothing, the check failed open, and
    # the flip it exists to catch went out. That is not hypothetical: on
    # 2026-08-05 21:11 a user who had written "What happened", "How many
    # pictures do you see" and "Enhance the video..." answered "Sure" — four
    # letters — and got a reply in Russian. Their language was never in doubt;
    # it just was not in THAT message. The prompt anchor already reads every
    # user message for exactly this reason (_reply_language_note); the
    # enforcement now agrees with it instead of being strictly weaker.
    history = " ".join(
        m["content"] for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), str))
    joined = " ".join(x for x in (history, user_text) if x)
    flip = _language_flip(joined, user_text, final)
    if flip is None:
        return final
    kind, u_side, r_side = flip
    honesty["language_flip"] = f"{u_side}->{r_side}" + \
        ("" if kind == "script" else " (words)")
    print(f"[language] job {ctx.job['id']}: reply drafted in {r_side} "
          f"({kind}) for a {u_side} user — forcing one rewrite", flush=True)
    if kind == "script":
        why = (f"Your reply above is written in {r_side} script, "
               f"but the user's own messages are {u_side}-script. ")
    else:
        why = ("Your reply above reads as a DIFFERENT LANGUAGE than the "
               "user's own messages (none of their function words appear "
               "in it). ")
    msgs = messages + [
        {"role": "assistant", "content": final},
        {"role": "system",
         "content": (why +
                     "Rewrite the reply in the USER'S language — a faithful "
                     "translation with every fact, number and timing kept "
                     "identical, nothing added. Output only the rewritten "
                     "reply.")},
    ]
    model = ctx.agent_model or config.AGENT_MODEL
    try:
        resp = llm.create_with_dialect(
            client, model, msgs, tools=tools, tool_choice="none",
            temperature=config.AGENT_TEMPERATURE,
            max_tokens=config.AGENT_REPLY_MAX_TOKENS)
        llm.record("language_regen",
                   {"model": model, "messages": msgs[-2:],
                    "note": "rewrite after a cross-script reply"},
                   {"content": (resp.choices[0].message.content or "")},
                   getattr(resp, "usage", None))
        redraft = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[language] rewrite failed ({e}) — posting the draft as-is",
              flush=True)
        return final
    if kind == "script":
        ok = redraft and _dominant_script(redraft, min_letters=10) == u_side
    else:
        ok = redraft and _marker_hits(redraft, u_side) >= 1 \
            and _marker_lang(redraft) != r_side
    if ok:
        honesty["language_fixed"] = True
        return redraft
    return final


def _finalize(ctx, worker_db, session_id, final_text, status, total_steps,
              timings, honesty=None, extra_meta=None):
    """Post a system-authored assistant reply (timeout/step-limit paths),
    auto-rendering first when the EDL changed without a preview. extra_meta is
    merged into the message meta so the studio can react to it (e.g. render an
    Upgrade CTA on the out-of-credits stop instead of a dead-end 402 later)."""
    latest, fail_note = _auto_render_if_needed(ctx, worker_db, session_id,
                                               timings)
    if fail_note:
        final_text += fail_note
    final_text = _disclose_outstanding_quality(ctx, final_text)
    meta = {"edl_version": latest["version"], "preview": ctx.last_preview,
            **_quality_handoff(ctx)}
    if extra_meta:
        meta.update(extra_meta)
    worker_db.run(dbx.add_message, session_id, "assistant", final_text, meta)
    return {"status": status, "edl_version": latest["version"],
            "steps": total_steps, "auto_render": ctx.autorendered,
            "honesty": honesty, "timings": timings}


# Fractions of AGENT_TURN_TIMEOUT_S at which the loop tells the model how much
# of the turn is gone. Without this the model has NO clock: it plans as if time
# were free, and the timeout lands mid-rebuild — the user gets "that took longer
# than I allow myself" with a half-finished edit instead of a landed one. The
# note is information, not a cap; the model decides what to drop.
TIME_PRESSURE_MARKS = (0.55, 0.8)


def _time_pressure_note(result, t_start, warned):
    """Append a one-line budget warning to a tool result, once per mark. A
    render_preview still has to fit in what's left, so the last mark says to
    render NOW rather than to keep editing."""
    frac = (time.monotonic() - t_start) / max(1.0, config.AGENT_TURN_TIMEOUT_S)
    for mark in TIME_PRESSURE_MARKS:
        if frac >= mark and mark not in warned:
            warned.add(mark)
            left = max(0, int(config.AGENT_TURN_TIMEOUT_S * (1.0 - frac)))
            if mark == TIME_PRESSURE_MARKS[-1]:
                return result + (
                    f"\n\n[system: ~{left}s left in this turn, and a preview "
                    "render needs a good chunk of it. Stop editing now — "
                    "render_preview and reply with what you changed. Anything "
                    "unfinished belongs in your reply as the next step, not in "
                    "more tool calls.]")
            return result + (
                f"\n\n[system: about half this turn's time is gone (~{left}s "
                "left, render included). Finish the highest-value part of the "
                "request and land it. Do NOT tear down work you already did to "
                "rebuild it a different way — refine in place, or leave it and "
                "say so.]")
    return result


_TASTE_PUSHBACK = """[system: the INDEPENDENT QUALITY REVIEW on the preview you just \
rendered is still outstanding. These are visible or measured craft defects in YOUR edit — things \
the user did not ask for — and the turn does not end with them unanswered:

{findings}

Do ONE of two things now, not neither:
  1. FIX them — remove the devices that are not earning their place, then \
render_preview again. Removing is a normal edit: remove_sfx, remove_zoom, \
remove_stylize, set_transitions('none'), set_fades(0, 0). An edit gets good by \
having things taken OUT of it.
  2. KEEP one deliberately — only when the user's own request requires it — \
and say which one and why, in one clause, in your reply.

What you may not do is reply as though the preview came back clean. \
"Preview is ready" over a review like this is how an edit nobody wants gets \
handed over as finished.]"""


def _taste_pushback(ctx, messages, t_start, pushed_versions):
    """Refuse to let a turn end on an edit its own audit objected to.

    Round 55. The audit was already right about this exact edit — it reported
    the dead air at the open, three zooms in thirty seconds and five sound
    effects nobody asked for — and the agent read all of it, changed nothing,
    and told the user "Preview is ready at 30s. Here's the edit:". A review
    that the reviewed party may silently decline is not a review; it is a
    comment. So the findings come back once, as work to be done.

    Once per reviewed version, and never when there is too little time left to
    act on it. Preview count is deliberately not a lock: a concrete requested
    change must remain writable and reviewable in the same user turn.
    """
    version = getattr(ctx, "last_taste_version", None)
    if version in pushed_versions or not ctx.last_taste:
        return False
    left = config.AGENT_TURN_TIMEOUT_S - (time.monotonic() - t_start)
    if left < config.AGENT_TURN_TIMEOUT_S * 0.25:
        return False
    lines = "\n".join(f"  - {f}" for f in ctx.last_taste[:6])
    messages.append({"role": "system",
                     "content": _TASTE_PUSHBACK.format(findings=lines)})
    return True


def _messages_for_record(messages):
    """What gets written to llm_calls: the image PARTS are replaced by a
    marker. ask_vision has always recorded image names, never bytes — a
    base64 frame in the request payload would put megabytes into every
    llm_calls row of a turn that looked at something."""
    out = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                parts.append({"type": "text", "text": "[image attached]"})
            elif isinstance(p, dict) and p.get("type") == "input_audio":
                # Same rule as frames: base64 sound in the recorded request
                # would put megabytes into llm_calls per listening turn.
                parts.append({"type": "text", "text": "[audio attached]"})
            else:
                parts.append(p)
        out.append({**m, "content": parts})
    return out


def _strip_image_parts(messages):
    """Remove every image content part in place, keeping the text parts.
    Returns True when anything was removed — the caller uses that to tell 'the
    provider is blind' apart from an unrelated 400 that merely pattern-matched
    the error text (round 67)."""
    changed = False
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        kept = [p for p in content
                if not (isinstance(p, dict) and p.get("type") == "image_url")]
        if len(kept) != len(content):
            changed = True
            kept.append({"type": "text", "text":
                         "(the captured frames could not be shown to you — "
                         "this model does not take images; use look_at "
                         "again and read its text answer instead)"})
            m["content"] = kept
    return changed


def _strip_audio_parts(messages):
    """Remove every input_audio content part in place — the deaf-provider
    twin of _strip_image_parts (round 98). Returns True when anything was
    removed, so the caller can tell 'this model has no ears' apart from an
    unrelated 400 that merely mentioned audio."""
    changed = False
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        kept = [p for p in content
                if not (isinstance(p, dict)
                        and p.get("type") == "input_audio")]
        if len(kept) != len(content):
            changed = True
            kept.append({"type": "text", "text":
                         "(the sound clips could not be played to you — "
                         "this model does not take audio; decide from "
                         "get_audio_analysis and the AUDIO CHECK "
                         "measurements instead)"})
            m["content"] = kept
    return changed


def _continue_decision(n_cont, progressed, seconds_left, over_budget):
    """May a pass that spent its iteration budget resume itself? Every gate
    is one of the REAL walls: the continuation allowance, forward progress
    (a pass that moved nothing is a runaway, not an unfinished edit), enough
    wall clock for the next pass to land anything (a continuation that the
    timeout kills 40s in only trades one apology for a later one), and the
    spend cap."""
    return (n_cont < config.AGENT_AUTO_CONTINUES and progressed
            and seconds_left > 120 and not over_budget)


_CONTINUATION_NOTE = (
    "[system: CONTINUATION — you hit the per-pass {why} on this SAME "
    "request and were resumed automatically. The user has NOT seen any reply "
    "and has NOT sent anything new; the message above is the request you "
    "were already working on. Everything you already changed is in the "
    "PROJECT STATE and program map above — trust them: do NOT redo finished "
    "work, do NOT re-read skills, do NOT re-verify what you already saw "
    "land. Already ran this turn: {done}.{plan} Finish ONLY what remains of "
    "the request, render once, and reply.]")


def _run_loop(ctx, worker_db, job, session_id, user_message,
              attachment_note="", _cont=None):
    """One tool-calling pass over the request. When the pass spends all its
    iterations while still landing edits, it hands its clocks and counters to
    a recursive continuation pass (_cont) instead of asking the user to say
    "continue" — the wall clock (AGENT_TURN_TIMEOUT_S), the spend cap and
    AGENT_AUTO_CONTINUES bound the whole chain, so the iteration ceiling is
    back to being what it was meant to be: a runaway-loop breaker, not a
    mid-edit stop sign (project 383: 30 productive calls in 7.5 min, then
    'tell me to continue' in English at a Portuguese-speaking user with half
    the time budget unspent)."""
    # Resolved from the user's plan in run_agent_job. _build_messages and the
    # tool schemas are model-agnostic and do not change with it.
    client = ctx.llm_client or llm.client()
    model = ctx.agent_model or config.AGENT_MODEL
    messages = _build_messages(ctx, worker_db, user_message, attachment_note)
    _cont = _cont or {}
    if _cont:
        done = ", ".join(
            f"{name} x{t['n']}" if t["n"] > 1 else name
            for name, t in sorted(_cont["timings"]["tools"].items())) or \
            "nothing yet"
        # The plan the earlier pass RECORDED (set_edit_plan) — what was
        # decided, not merely what ran. A resumed pass finishes the plan
        # instead of re-deriving the edit mid-flight (round 98).
        plan_note = ""
        ep = getattr(ctx, "edit_plan", None)
        if ep and ep.get("steps"):
            anchors = "; ".join(
                row for row in (
                    f"format={ep.get('format')}" if ep.get("format") else "",
                    f"intent={ep.get('intent')}" if ep.get("intent") else "",
                    ("style=" + str(ep.get("style_family"))
                     if ep.get("style_family") else ""),
                    ("must keep=" + ", ".join(ep.get("must_keep") or [])
                     if ep.get("must_keep") else ""),
                    ("must avoid=" + ", ".join(ep.get("must_avoid") or [])
                     if ep.get("must_avoid") else ""),
                ) if row)
            plan_note = (" YOUR RECORDED PLAN: "
                         + (f"[{ep['brief']}] " if ep.get("brief") else "")
                         + (f"ANCHORS({anchors}). " if anchors else "")
                         + " ".join(f"{i + 1}) {s}"
                                    for i, s in enumerate(ep["steps"]))
                         + ((" COMPLETED TOOLS: " + ", ".join(
                             ep.get("completed_tools") or []) + ".")
                            if ep.get("completed_tools") else "")
                         + " — finish its unfinished steps.")
        messages.append({"role": "user",
                         "content": _CONTINUATION_NOTE.format(
                             done=done, plan=plan_note,
                             why=_cont.get("why", "step ceiling"))})
    tools = agent_tools.openai_tools(
        model, compact=bool(getattr(ctx, "edit_plan", None)
                            or ctx.versions_written))
    total_steps = _cont.get("steps", 0)
    t_start = _cont.get("t_start") or time.monotonic()
    timings = _cont.get("timings") or \
        {"llm_s": 0.0, "llm_calls": 0, "tools": {}}
    honesty = _cont.get("honesty") or \
        {"false_claims": 0, "corrective_note": False}
    start_version = ctx.latest_edl()["version"]
    # time-pressure marks already delivered (carried across continuations —
    # the clock they describe is the same one)
    warned = _cont.get("warned") if _cont.get("warned") is not None else set()
    # Completion budget for this step, and how many times a truncated step has
    # already been retried with a bigger one. See _TRUNCATED_NUDGE.
    max_tokens = config.AGENT_MAX_TOKENS
    truncated_retries = 0
    truncated_out = False          # last step died at the ceiling, saying nothing
    taste_pushed_versions = set(
        _cont.get("taste_pushed_versions") or [])
    preview_repair_pushed = bool(_cont.get("preview_repair_pushed", False))
    _responses_warned = False      # say the lane fell back ONCE, not per step

    # TPM admission (Aug 10): the provider's tokens-per-minute ceiling is
    # org-wide, and a fresh turn's first call is the biggest single request
    # we make (~50K tokens of state + filmstrips). Starting it into a burst
    # that is already over the ceiling buys a guaranteed 429 — so a FRESH
    # turn (nothing done yet, nothing to lose) peeks at the fleet's last-60s
    # burn and yields briefly while it is above the soft cap. Continuations
    # never wait: their clock is already running.
    if not _cont:
        for _adm in range(3):
            try:
                burn = worker_db.run(dbx.recent_llm_tokens, 60)
            except Exception:
                break                # the gate is advisory, never load-bearing
            if burn < config.AGENT_TPM_SOFT_CAP:
                break
            print(f"[job {job['id']}] fleet pushed {burn} tokens in the last "
                  f"60s (soft cap {config.AGENT_TPM_SOFT_CAP}) — pausing 20s "
                  "before the first call instead of joining the burst",
                  flush=True)
            time.sleep(20)

    for iteration in range(config.AGENT_MAX_ITERATIONS):
        if SHUTDOWN.is_set():
            # Render SIGTERMs the worker before every deploy, and an agent
            # turn is deliberately never retried (replaying re-applies EDL
            # writes) — so before this check an in-flight turn just DIED and
            # the reaper told the user "I lost my connection", sometimes over
            # an edit that had in fact landed (v266, Aug 1). Now the loop
            # finalizes inside the grace window instead: honest reply, job
            # marked done, nothing for the reaper to lie about.
            print(f"[job {job['id']}] shutdown drain: finalizing after "
                  f"{total_steps} step(s)", flush=True)
            # No _finalize here: its auto-render waits on a preview encode,
            # which cannot fit inside Render's grace window. The reply is
            # written directly and the studio's next poll shows the state.
            if ctx.versions_written:
                latest = ctx.latest_edl()
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "I had to stop mid-request for a moment of "
                              "maintenance on my side — the edits I finished "
                              "are saved. Tell me to continue and I'll pick "
                              "up exactly where I left off.",
                              {"edl_version": latest["version"]})
            else:
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "I had to pause for a moment of maintenance on "
                              "my side before I could change anything — your "
                              "edit is untouched. Please send that again.",
                              {"edl_version": ctx.latest_edl()["version"]})
            return {"status": "shutdown", "steps": total_steps,
                    "timings": timings, "billable": False}
        if time.monotonic() - t_start > config.AGENT_TURN_TIMEOUT_S:
            # This is an INACTIVITY wall, never a total-edit wall. A complex
            # turn that is still landing EDL versions or successful previews
            # gets another window with no fixed count. The model-call/spend
            # guards still stop churn; the clock only catches a genuinely
            # stalled turn. Tool calls are synchronous, so a long render is
            # not interrupted mid-call either — its completed preview counts
            # as progress when control returns here.
            n_clock = _cont.get("clock", 0)
            progress_lists = (
                "images_generated", "videos_generated", "urls_fetched",
                "web_recordings", "audio_extracted", "stock_added")
            asset_progress = sum(
                len(getattr(ctx, name, None) or []) for name in progress_lists)
            _progressed = (len(ctx.versions_written) > _cont.get("writes0", 0)
                           or len(ctx.rendered_versions)
                           > _cont.get("renders0", 0)
                           or asset_progress > _cont.get("assets0", 0))
            if _progressed and not ctx.over_budget() \
                    and not SHUTDOWN.is_set():
                print(f"[job {job['id']}] progress window spent after "
                      f"{total_steps} step(s), but work is still landing — "
                      f"refreshing it (refresh {n_clock + 1})", flush=True)
                return _run_loop(
                    ctx, worker_db, job, session_id, user_message,
                    attachment_note,
                    _cont={"n": _cont.get("n", 0), "clock": n_clock + 1,
                           "why": "turn clock", "steps": total_steps,
                           "t_start": time.monotonic(),
                           "timings": timings, "honesty": honesty,
                           "preview_repair_pushed": preview_repair_pushed,
                           "taste_pushed_versions":
                               sorted(taste_pushed_versions),
                           "warned": None,
                           "writes0": len(ctx.versions_written),
                           "renders0": len(ctx.rendered_versions),
                           "assets0": asset_progress})
            print(f"[job {job['id']}] stalled with no editing/rendering "
                  f"progress for {config.AGENT_TURN_TIMEOUT_S:.0f}s",
                  flush=True)
            if ctx.versions_written:
                # Name the half-done state plainly. The old copy ("the edits
                # I completed are saved") read as DONE to a user who wasn't
                # counting their asks: a real request for "remove the TikTok
                # UI + brighten it" timed out after the erases with the
                # brightness never applied, the user read the stop as the
                # finish, and left (Aug 3 2026, project 335).
                return _finalize(
                    ctx, worker_db, session_id,
                    "This edit stopped making progress on my side, so I'm "
                    "stopping the stalled run — the edits I finished are "
                    "saved "
                    "and previewed below. If part of your request isn't in "
                    "them yet, it is NOT done — say \"continue\" and I'll "
                    "pick up where I stopped.",
                    "timeout", total_steps, timings, honesty)
            # "nothing was changed" is true of the EDL but not of the project:
            # a turn can time out after downloading a file, and telling the
            # user nothing happened would leave them re-pasting a link whose
            # media is already sitting in their media picker.
            saved = _assets_made_note(ctx)
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "That request stopped making progress before I "
                          "could finish anything — the edit itself was not changed"
                          f"{saved}. Please try again, or break the request "
                          "into smaller steps.",
                          {"error": "turn_timeout"})
            return {"status": "timeout", "steps": total_steps,
                    "timings": timings}

        # Graceful spend cap: stop before starting another (expensive) model
        # call once this turn's model cost has reached the budget. Honest stop
        # message; the edits already made are saved + previewed.
        if ctx.over_budget():
            print(f"[job {job['id']}] spend cap hit: "
                  f"{ctx.running_credits()} >= {ctx.credit_budget} credits",
                  flush=True)
            honesty["budget_stop"] = True
            # credit_budget = balance + grace, so over_budget() can only fire
            # when the turn tried to spend essentially the whole wallet. For a
            # free user that IS "out of credits" — and the next message WILL
            # 402 — so say so honestly with an upgrade path instead of the old
            # "send a follow-up" that dead-ends. exhausted is ~always true here
            # today; the funded-user "send a follow-up" branch only matters if
            # a flat per-turn cap is ever reintroduced (see config.py notes).
            start_balance = (ctx.credit_budget or 0.0) \
                - config.AGENT_TURN_BUDGET_GRACE
            exhausted = (start_balance - ctx.running_credits()) < 1.0
            # A subscriber's pool comes back; a free user's does not. Saying
            # "credits refresh daily" to someone whose one-time allowance is
            # spent is simply false, and it teaches them to wait instead of
            # deciding.
            # Already resolved once at the top of the turn (it also chooses
            # which model answered) — no second query at the worst moment.
            subscribed = bool(getattr(ctx, "subscribed", False))
            trialing = bool(getattr(ctx, "trialing", False))
            # Three states, three different true sentences. A trialling user has
            # already entered a card, so "start your trial" is nonsense to them
            # and "wait for your cycle" is worse — the rest of their plan is
            # released by KEEPING it, which they can do this second.
            _refill = ("That's the credits included with your free trial. "
                       "Keep your plan and the full monthly pool unlocks "
                       "straight away — no waiting."
                       if trialing else
                       "Your credits refresh on your plan's cycle — or "
                       "upgrade for a bigger monthly pool to keep editing now."
                       if subscribed else
                       "Start your trial to keep editing.")
            if ctx.versions_written:
                if exhausted:
                    return _finalize(
                        ctx, worker_db, session_id,
                        "That used up your available credits — the edits I "
                        "finished are saved and previewed below. " + _refill,
                        "budget", total_steps, timings, honesty,
                        extra_meta={"credits_exhausted": True,
                                    "free_trial_exhausted": not subscribed,
                                    "trial_cap_reached": trialing})
                return _finalize(
                    ctx, worker_db, session_id,
                    "I've hit my budget for this request, so I'm stopping "
                    "here — the edits I completed are saved and previewed "
                    "below. Send a follow-up to keep going.",
                    "budget", total_steps, timings, honesty)
            if exhausted:
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "You're out of credits, so I stopped before "
                              "changing anything. " + _refill,
                              {"error": "turn_budget",
                               "credits_exhausted": True,
                               "free_trial_exhausted": not subscribed,
                               "trial_cap_reached": trialing})
            else:
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "This request needed more work than its budget "
                              "allows, so I stopped before changing anything. "
                              "Try breaking it into smaller steps.",
                              {"error": "turn_budget"})
            return {"status": "budget", "steps": total_steps,
                    "timings": timings}

        if timings["llm_calls"] >= config.AGENT_MAX_MODEL_CALLS:
            print(f"[job {job['id']}] model-call ceiling hit: "
                  f"{timings['llm_calls']}", flush=True)
            return _finalize(
                ctx, worker_db, session_id,
                "I stopped at the editing-pass limit instead of continuing "
                "to churn on this video. The valid work completed so far is "
                "saved and previewed; anything not visible there is not done.",
                "model_call_limit", total_steps, timings, honesty)

        worker_db.run(dbx.set_progress, job["id"],
                      int(100 * iteration / config.AGENT_MAX_ITERATIONS))
        t0 = time.monotonic()
        # TIERED reasoning (round 100). Iteration 0 is where the model reads
        # the project state and plans the edit — that is the thinking worth
        # paying for, and it runs at AGENT_REASONING_EFFORT. Every iteration
        # after is tool dispatch: the plan exists, the step is "call the next
        # tool and read its result", and running THAT at 'max' is what made
        # one turn burn 49k reasoning tokens over 21 calls and hold another
        # user's queued message hostage for 14 minutes (job 3211). Dispatch
        # steps run at AGENT_REASONING_EFFORT_DISPATCH ('low' by default) —
        # on both the responses lane and the chat path. Empty config sends no
        # field at all, so a provider that would reject an unknown parameter
        # is untouched until someone opts in.
        step_effort = (config.AGENT_REASONING_EFFORT if iteration == 0
                       else (config.AGENT_REASONING_EFFORT_DISPATCH
                             or config.AGENT_REASONING_EFFORT))
        extra = {}
        if step_effort and iteration > 0 \
                and not llm.reasoning_effort_rejected(model):
            extra["reasoning_effort"] = step_effort
        if llm.tools_need_effort_none(model):
            # This model refuses tools + reasoning on chat/completions
            # outright (Luna); the only accepted spelling is an explicit
            # 'none' on EVERY tools call — including iteration 0, where the
            # field is otherwise never sent.
            extra["reasoning_effort"] = "none"
        try:
            # THE KNOBS HAVE TO BE SAFE TO TURN ON. reasoning_effort is an
            # env var someone sets against a provider we cannot test from
            # here; max_tokens vs max_completion_tokens and whether a custom
            # temperature is allowed are per-provider dialects (OpenAI's
            # reasoning family — the Luna default — rejects both classics);
            # and the direct-sight look_at puts image parts in the agent's
            # own messages, which a blind provider rejects at the JSON
            # layer. Any of those would 400 EVERY step of every turn, so
            # each rejection is matched narrowly, corrected once, and
            # latched for the process — a real API failure still propagates
            # on the first try.
            kw = llm.completion_kwargs(model, max_tokens,
                                       config.AGENT_TEMPERATURE)
            kw.update(extra)
            # THINKING, ON THE MODEL WE ALREADY PAY FOR (round 91). When this
            # model has told us it will not reason alongside tools on
            # chat/completions, ask the endpoint it named instead. Any failure
            # — transport, HTTP, or a body llm._from_responses does not
            # recognise — falls straight through to the call below, which is
            # byte-for-byte what runs today. The lane can only add thinking;
            # it cannot take a turn away.
            resp = None
            # What the request ACTUALLY carried, for the record below. Without
            # this the row said reasoning_effort='none' (the chat path's value)
            # on turns the lane had just asked to think hard — an audit trail
            # that contradicts the reasoning_out beside it is worse than none,
            # because it is the field you would check first.
            used_lane = None
            if llm.responses_available(model, config.OPENAI_BASE_URL):
                try:
                    resp = llm.responses_create(
                        config.OPENAI_BASE_URL, config.OPENAI_API_KEY, model,
                        messages, tools, max_tokens=max_tokens,
                        effort=step_effort,
                        # Thinking-sized, not dispatch-sized: a max-effort
                        # call reasons past LLM_TIMEOUT_S, and timing out
                        # here silently reruns the step at effort='none'.
                        timeout=config.AGENT_LANE_TIMEOUT_S)
                    used_lane = step_effort
                except Exception as e:
                    # A definite "not here" latches the lane off for the
                    # process so a doomed request is paid once, not once per
                    # step. A timeout or a 500 does NOT — that would cost the
                    # agent its thinking for the life of the worker over a
                    # blip.
                    permanent = llm.looks_like_responses_unsupported(e)
                    if permanent:
                        llm.mark_responses_dead(model)
                    if not _responses_warned:
                        _responses_warned = True
                        print(f"[agent {job['id']}] the responses lane failed "
                              f"({str(e)[:180]}) — falling back to "
                              "chat/completions; the model answers WITHOUT "
                              "reasoning there"
                              + (" [latched off for this process]"
                                 if permanent else " [will retry next step]"),
                              flush=True)
                    resp = None
            _adapt_tries = 0
            _rl_waits = 0
            while resp is None:
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=messages, tools=tools, **kw)
                    break
                except Exception as e:
                    # Rate limits get their own budget-aware waits, BEFORE
                    # the dialect adapters — see llm.rate_limit_wait for the
                    # reasoning and bounds (round 91).
                    wait = llm.rate_limit_wait(
                        e, _rl_waits + 1,
                        config.AGENT_TURN_TIMEOUT_S
                        - (time.monotonic() - t_start),
                        shutting_down=SHUTDOWN.is_set())
                    if wait is not None:
                        _rl_waits += 1
                        print(f"[agent {job['id']}] rate-limited by the "
                              f"model ({_rl_waits}/6) — waiting {wait:.0f}s "
                              "and retrying instead of failing the turn",
                              flush=True)
                        time.sleep(wait)
                        continue
                    _adapt_tries += 1
                    if _adapt_tries > 3:
                        raise
                    # Checked BEFORE the strip-the-field branch: this
                    # rejection fires when the field is ABSENT (the model's
                    # default reasoning is what conflicts with tools), so
                    # stripping fixes nothing — the dialect is to SEND
                    # reasoning_effort='none' explicitly, always.
                    # NOT gated on "not already latched" (round 100): the
                    # latch is process-global and another thread could set it
                    # between this step building kw and its 400 arriving —
                    # at which point every branch here used to step aside and
                    # the raw 400 killed the turn (jobs 3123/3156/3201, three
                    # real users on 2026-08-08). The recovery is idempotent;
                    # the only unrecoverable shape is a conflict 400 on a
                    # request that ALREADY carried 'none'.
                    if llm.looks_like_tools_reasoning_conflict(e) and \
                            kw.get("reasoning_effort") != "none":
                        llm.mark_tools_need_effort_none(model)
                        print(f"[agent {job['id']}] {model} takes function "
                              "tools only with reasoning_effort='none' on "
                              "chat/completions — latched, retrying",
                              flush=True)
                        kw["reasoning_effort"] = "none"
                        extra = {"reasoning_effort": "none"}
                        continue
                    if "reasoning_effort" in kw and \
                            not llm.tools_need_effort_none(model) and \
                            llm.looks_like_bad_parameter(e,
                                                         "reasoning_effort"):
                        llm.mark_reasoning_effort_rejected(model)
                        print(f"[agent {job['id']}] provider rejected "
                              f"reasoning_effort="
                              f"{config.AGENT_REASONING_EFFORT!r} for "
                              f"{model} — retrying without it and not "
                              "sending it again", flush=True)
                        kw.pop("reasoning_effort")
                        extra = {}
                        continue
                    adapted = llm.adapt_completion_kwargs(e, model, kw)
                    if adapted is not None:
                        kw = adapted
                        continue
                    # Checked BEFORE the blind branch: 'input_audio' in the
                    # error is the audio part's own field name, so a deaf
                    # rejection must never latch the EYES off.
                    if llm.looks_like_deaf_model(e) \
                            and _strip_audio_parts(messages):
                        llm.mark_agent_deaf(model)
                        print(f"[agent {job['id']}] {model} rejected an "
                              "audio part — agent hearing DISABLED for "
                              "this process; listening degrades to "
                              "get_audio_analysis + the audio QC",
                              flush=True)
                        continue
                    if llm.looks_like_blind_model(e) \
                            and _strip_image_parts(messages):
                        llm.mark_agent_blind(model)
                        print(f"[agent {job['id']}] {model} rejected an "
                              "image part — agent direct sight DISABLED for "
                              "this process; look tools fall back to the "
                              "vision provider", flush=True)
                        continue
                    raise
        except Exception as e:
            # llm.record only ran on success, so a failing agent call left NO
            # row anywhere: through the whole Jul 26 2026 provider outage the
            # admin Model I/O tab showed nothing for the turns users were
            # watching fail, and the only evidence was the chat message. Record
            # it (messages tail only — the full list is large and the failure
            # is what matters), then let it propagate to _user_facing_failure.
            timings["llm_s"] = round(
                timings["llm_s"] + time.monotonic() - t0, 2)
            llm.record("agent",
                       {"model": model,
                        "messages": _messages_for_record(messages[-2:]),
                        "tools": [t["function"]["name"] for t in tools]},
                       {"error": f"{type(e).__name__}: {str(e)[:400]}"}, None)
            raise
        timings["llm_s"] = round(timings["llm_s"] + time.monotonic() - t0, 2)
        timings["llm_calls"] += 1
        msg = resp.choices[0].message
        finish = getattr(resp.choices[0], "finish_reason", None)
        llm.record("agent",
                   {"model": model, "messages": _messages_for_record(messages),
                    "tools": [t["function"]["name"] for t in tools],
                    "max_tokens": max_tokens,
                    # The effort the request CARRIED, and which endpoint
                    # carried it — the lane's value when it answered, the chat
                    # path's when it did not. Recording the chat value
                    # unconditionally made every lane row read
                    # reasoning_effort='none' next to a non-zero reasoning_out,
                    # which is the one place anyone looks to check this works.
                    **({"reasoning_effort": used_lane, "api": "responses"}
                       if used_lane is not None
                       else ({"reasoning_effort": extra["reasoning_effort"],
                              "api": "chat.completions"} if extra
                             else {"api": "chat.completions"})),
                    },
                   {"content": msg.content,
                    "tool_calls": [{"name": tc.function.name,
                                    "arguments": tc.function.arguments}
                                   for tc in (msg.tool_calls or [])],
                    # Recorded because its absence is what made this class of
                    # failure invisible: an empty completion at the ceiling and
                    # a deliberate empty reply look identical without it.
                    "finish_reason": finish},
                   getattr(resp, "usage", None))

        # A step that hit the token ceiling with NOTHING in it — no text, no
        # tool call — is not an answer, it is a truncation. A reasoning model
        # spends the budget deliberating and never reaches `content`. Treating
        # it as "the model chose to say nothing" is what posted "I only
        # reviewed the video" three times at a user asking for an edit. Give it
        # more room and tell it to act; only after that give up, and honestly.
        if not msg.tool_calls and not (msg.content or "").strip() \
                and finish == "length":
            if truncated_retries < 2:
                truncated_retries += 1
                max_tokens = min(max_tokens * 2,
                                 config.AGENT_MAX_TOKENS_CEILING)
                print(f"[job {job['id']}] step truncated at the token ceiling "
                      f"with no output — retrying with max_tokens={max_tokens}",
                      flush=True)
                messages.append({"role": "system", "content": _TRUNCATED_NUDGE})
                continue
            truncated_out = True

        if not msg.tool_calls:
            body = (msg.content or "").strip()
            # A reply the token ceiling cut off MID-SENTENCE is not a reply
            # (round 100 — users watched turns "end mid-word"). Only the
            # empty case retried before; a partial one shipped as-is. Retry
            # shorter; out of retries, trim back to the last finished
            # sentence rather than ever posting a fragment.
            if finish == "length" and body:
                if truncated_retries < 2:
                    truncated_retries += 1
                    max_tokens = min(max_tokens * 2,
                                     config.AGENT_MAX_TOKENS_CEILING)
                    print(f"[job {job['id']}] reply cut off at the token "
                          f"ceiling — retrying with max_tokens={max_tokens}",
                          flush=True)
                    messages.append({"role": "assistant", "content": body})
                    messages.append({"role": "system",
                                     "content": _CUTOFF_NUDGE})
                    continue
                if body[-1] not in ".!?…\"'”)":
                    cut = max(body.rfind("."), body.rfind("!"),
                              body.rfind("?"))
                    if cut > 40:
                        body = body[:cut + 1]
            # Before anything else: if the preview this turn rendered came
            # back with craft findings, the turn is not finished. Send them
            # back once as work rather than as commentary.
            if _taste_pushback(ctx, messages, t_start,
                               taste_pushed_versions):
                taste_pushed_versions.add(ctx.last_taste_version)
                print(f"[job {job['id']}] taste audit outstanding "
                      f"({len(ctx.last_taste)} finding(s)) — pushing back "
                      "before the reply", flush=True)
                if body:
                    messages.append({"role": "assistant",
                                     "content": msg.content})
                continue
            # Auto-render first so the turn facts include the real preview.
            latest, fail_note = _auto_render_if_needed(ctx, worker_db,
                                                       session_id, timings)
            if _preview_repair_pushback(
                    ctx, messages, t_start, preview_repair_pushed):
                preview_repair_pushed = True
                print(f"[job {job['id']}] preview v{latest['version']} "
                      "failed deterministically — requesting one corrected "
                      "EDL version instead of retrying it", flush=True)
                if body:
                    messages.append({"role": "assistant", "content": body})
                continue
            draft = body
            if not draft:
                if ctx.versions_written or ctx.last_preview:
                    draft = "Done — check the preview on the right."
                elif truncated_out:
                    # NOT "I only reviewed the video": nothing was reviewed and
                    # nothing was decided. Say what actually happened, and
                    # invite the one thing that fixes it — asking again.
                    draft = ("Sorry — I ran out of room working that one out "
                             "and never got to the edit itself. Nothing was "
                             f"changed{_assets_made_note(ctx)}. Send it again "
                             "(one instruction at a time helps) and I'll go "
                             "straight at it.")
                else:
                    draft = ("I only reviewed the video — the edit was not "
                             f"changed{_assets_made_note(ctx)}.")
            final = _enforce_honesty(ctx, client, messages, tools, draft,
                                     start_version, honesty,
                                     user_text=user_message["content"] or "")
            final = _disclose_outstanding_quality(ctx, final)
            final = _enforce_reply_language(
                ctx, client, messages, tools, final,
                user_text=user_message["content"] or "", honesty=honesty)
            if fail_note:
                final += fail_note
            honesty["auto_render"] = ctx.autorendered
            message_meta = {
                "edl_version": latest["version"],
                "preview": ctx.last_preview,
                **_quality_handoff(ctx),
            }
            worker_db.run(dbx.add_message, session_id, "assistant", final,
                          message_meta)
            return {"status": "replied", "edl_version": latest["version"],
                    "steps": total_steps, "auto_render": ctx.autorendered,
                    "honesty": honesty, "timings": timings,
                    # A turn that only ever hit the token ceiling delivered
                    # nothing — no edit, no asset, not even an answer. We paid
                    # the provider; the user must not. Same principle as
                    # charge_turn_credits' "a turn that got nothing back costs
                    # nothing", one layer up where the reason is visible.
                    "billable": not (truncated_out and not ctx.versions_written
                                     and not _assets_made_note(ctx)
                                     and not ctx.last_preview),
                    "truncated": truncated_out or None}

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name,
                             "arguments": tc.function.arguments or "{}"},
            } for tc in msg.tool_calls],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = None
            if args is None:
                result = ("REJECTED: arguments were not valid JSON. "
                          "Send a proper JSON object.")
            else:
                t0 = time.monotonic()
                try:
                    result = agent_tools.execute(ctx, name, args)
                except agent_tools.AskUser as q:
                    # The question goes to the user VERBATIM, which makes it
                    # a reply — and until 2026-08-09 it was the one reply
                    # that skipped the language guard (a Russian ask_user
                    # reached an English user that morning). Same
                    # deterministic check, but as a tool REJECTION: the
                    # model rephrases in-loop, no extra LLM call, no posted
                    # wrong-language text.
                    _u_joined = " ".join(
                        [m["content"] for m in messages
                         if m.get("role") == "user"
                         and isinstance(m.get("content"), str)]
                        + [user_message["content"] or ""])
                    if _language_flip(_u_joined,
                                      user_message["content"] or "",
                                      q.question):
                        result = ("REJECTED: that question is not in the "
                                  "USER'S language (the language of the "
                                  "messages they typed — never the "
                                  "footage's). Ask it again in their "
                                  "language.")
                    else:
                        _cur_v = (ctx.versions_written[-1]
                                  if ctx.versions_written
                                  else start_version)
                        _activity(worker_db, session_id, name, args,
                                  f"asked: {q.question}",
                                  edl_version=_cur_v)
                        worker_db.run(dbx.add_message, session_id,
                                      "assistant", q.question,
                                      {"ask_user": True,
                                       "edl_version": _cur_v})
                        return {"status": "awaiting_user",
                                "steps": total_steps, "timings": timings}
                tt = timings["tools"].setdefault(name, {"n": 0, "s": 0.0})
                tt["n"] += 1
                tt["s"] = round(tt["s"] + time.monotonic() - t0, 2)
                if name in agent_tools.WRITE_TOOLS and \
                        isinstance(result, str) and result.startswith("EDL v"):
                    ctx.write_calls.append(name)
            total_steps += 1
            # A fresh write's change ranges belong to ITS activity row only —
            # consume them here so a later read call the same step can never
            # re-attach the same flash.
            chg = None
            if ctx.last_change and ctx.versions_written and \
                    ctx.last_change.get("edl_version") == \
                    ctx.versions_written[-1]:
                chg, ctx.last_change = ctx.last_change, None
            _activity(worker_db, session_id, name, args, result,
                      edl_version=(ctx.versions_written[-1]
                                   if ctx.versions_written
                                   else start_version),
                      change=chg)
            result = _time_pressure_note(result, t_start, warned)
            remaining_calls = (config.AGENT_MAX_MODEL_CALLS
                               - timings["llm_calls"])
            if remaining_calls <= 2:
                result += (
                    "\n\n[system: the request has "
                    f"{max(0, remaining_calls)} model call(s) left. Stop "
                    "exploring or adding polish. If the latest version is "
                    "not proved, render it once; then reply with exactly "
                    "what is complete.]"
                )
            # Step pressure, only once the continuation budget is spent —
            # earlier passes are auto-resumed, so rushing them would only
            # produce premature "done" replies. Same shape as the time
            # marks: information, the model decides what to drop.
            if _cont.get("n", 0) >= config.AGENT_AUTO_CONTINUES \
                    and iteration >= config.AGENT_MAX_ITERATIONS - 5 \
                    and "steps" not in warned:
                warned.add("steps")
                result += (
                    "\n\n[system: only a few model calls remain for this "
                    "request — stop exploring, land the essential remainder "
                    "now, render_preview once, and write your reply.]")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

        # Round 67 — direct sight. A look tool captured frames for the
        # AGENT'S OWN eyes this step; deliver them as image parts in a user
        # message right after the tool results (tool-role content is
        # text-only on every OpenAI-compatible provider, a user message
        # after the tool batch is the standard carrier). One message for
        # the whole batch, then the queue clears so nothing leaks into the
        # next step.
        if getattr(ctx, "pending_images", None):
            content = []
            for label, path in ctx.pending_images:
                try:
                    part = llm.image_part(path)
                except Exception as ex:
                    print(f"[job {job['id']}] could not attach look frame "
                          f"({ex})", flush=True)
                    continue
                content.append({"type": "text", "text": f"[{label}]"})
                content.append(part)
            ctx.pending_images = []
            if content:
                content.insert(0, {"type": "text", "text":
                                   "Frames for your own eyes (each picture "
                                   "is labeled; timestamps are printed "
                                   "under the tiles):"})
                messages.append({"role": "user", "content": content})
                # Only now are those pixels evidence the model has received.
                # A look_at and add_zoom emitted in the SAME tool batch must
                # not let guessed coordinates pass before this message exists.
                ctx._looked_source_times.update(
                    getattr(ctx, "_pending_looked_source_times", set()))
                ctx._looked_output_times.update(
                    getattr(ctx, "_pending_looked_output_times", set()))
                for key, times in getattr(
                        ctx, "_pending_looked_asset_times", {}).items():
                    ctx._looked_asset_times.setdefault(key, set()).update(times)
            ctx._pending_looked_source_times = set()
            ctx._pending_looked_output_times = set()
            ctx._pending_looked_asset_times = {}

        # Round 98 — direct HEARING, the exact pending_images contract for
        # sound: a listen tool (or render_preview) queued short clips this
        # step; deliver them as input_audio parts in a user message. Only
        # when the model has ears — a deaf lane's clips are dropped here
        # (the tools already answered honestly in text).
        if getattr(ctx, "pending_audio", None):
            clips, model_hears = ctx.pending_audio, llm.agent_hears(model)
            ctx.pending_audio = []
            if model_hears:
                content = []
                for label, path in clips:
                    try:
                        part = llm.audio_part(path)
                    except Exception as ex:
                        print(f"[job {job['id']}] could not attach sound "
                              f"clip ({ex})", flush=True)
                        continue
                    content.append({"type": "text", "text": f"[{label}]"})
                    content.append(part)
                if content:
                    content.insert(0, {"type": "text", "text":
                                       "Sound for your own ears (each clip "
                                       "is labeled with its clock and "
                                       "span):"})
                    messages.append({"role": "user", "content": content})
                    # Promotion happens after the audio part is attached, so
                    # listen_to + add_music in one parallel tool batch cannot
                    # masquerade as an audition the model never heard.
                    ctx._listened_asset_keys.update(getattr(
                        ctx, "_pending_listened_asset_keys", set()))
            ctx._pending_listened_asset_keys = set()

        # The broad contact sheets did their job on the planning call. Keep
        # exact look/listen evidence added above, but do not pay to resend all
        # project pixels on every subsequent model dispatch.
        if getattr(ctx, "edit_plan", None) or ctx.versions_written:
            _compact_initial_filmstrip(messages)
            # The first planning request receives every tool's complete
            # handbook. Once a plan or edit exists, re-sending 61k characters
            # of descriptions on every dispatch is pure latency/context
            # pressure. Keep every function and its full parameter schema,
            # but reduce each description to a short reminder.
            tools = agent_tools.openai_tools(model, compact=True)

        # Round 98: speculative preview. A step that landed writes is almost
        # always followed by render_preview one model call (~13s) later —
        # start the encode NOW so it runs DURING that call instead of after
        # it. render_preview adopts the queued/running job for the same
        # version (dbx.pending_preview_job) rather than encoding twice, and
        # the helper itself skips grade-only writes (the contact-strip
        # shortcut is cheaper than any render). Best-effort by contract.
        if ctx.versions_written and not SHUTDOWN.is_set():
            try:
                agent_tools.speculative_preview(ctx)
            except Exception:
                pass

    # The iteration ceiling. A pass that is still landing edits continues on
    # its own — the user cannot tell step 30 from step 31, and "tell me to
    # continue" spends their patience on OUR internal counter. The real
    # resource walls stay exactly where they were: the shared wall clock
    # (t_start rides through _cont), the spend cap (checked at the top of
    # every iteration) and AGENT_AUTO_CONTINUES. A pass that moved NOTHING
    # is the runaway the ceiling was built for — that one still stops.
    n_cont = _cont.get("n", 0)
    progressed = (len(ctx.versions_written) > _cont.get("writes0", 0)
                  or len(ctx.rendered_versions) > _cont.get("renders0", 0))
    seconds_left = config.AGENT_TURN_TIMEOUT_S - (time.monotonic() - t_start)
    if _continue_decision(n_cont, progressed, seconds_left,
                          ctx.over_budget()):
        print(f"[job {job['id']}] iteration ceiling after {total_steps} "
              f"step(s), work landing — auto-continuing "
              f"(pass {n_cont + 2}, {seconds_left:.0f}s left)", flush=True)
        return _run_loop(
            ctx, worker_db, job, session_id, user_message, attachment_note,
            _cont={"n": n_cont + 1, "clock": _cont.get("clock", 0),
                   "why": "step ceiling",
                   "steps": total_steps, "t_start": t_start,
                   "timings": timings, "honesty": honesty, "warned": warned,
                   "preview_repair_pushed": preview_repair_pushed,
                   "taste_pushed_versions": sorted(taste_pushed_versions),
                   "writes0": len(ctx.versions_written),
                   "renders0": len(ctx.rendered_versions)})
    return _finalize(
        ctx, worker_db, session_id,
        "I hit my step limit for one request. The edits so far are saved — "
        "tell me to continue, or narrow the request.",
        "step_limit", total_steps, timings, honesty)
