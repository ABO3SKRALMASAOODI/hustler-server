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
import director
import editorial_contracts
import grammar
import llm
import model_prices
import motion_judge
import music_search
import preview_critic
import reference_profile
import remote
import request_intent
import sfx_search
import song_find
import storage
import story_critic
import timeline
import version as worker_version
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
    motion = motion_judge.describe(
        index.get("motion"), (index.get("video") or {}).get("duration"))
    if motion:
        lines.append(motion + " Use it with the filmstrip and story brief; "
                     "motion intensity is evidence, not an instruction to "
                     "make every edit faster.")
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
                       ORDER BY id ASC LIMIT 80""", (project_id,))
        return cur.fetchall()


def _image_assets(conn, project_id, limit=200):
    # Pull a broad bounded inventory, then let _image_visual_plan spend the
    # per-turn pixel budget fairly. Querying only IMAGES_TURN_MAX here made
    # every later image disappear from both the overview *and* the overflow
    # notice on subsequent turns: the caller could not know rows existed
    # beyond its SQL LIMIT. Two hundred matches list_assets' durable inventory
    # bound without injecting two hundred image payloads into the prompt.
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'image_ref'
                         AND COALESCE(meta->>'staged', '') != 'true'
                       ORDER BY id ASC LIMIT %s""",
                    (project_id, max(1, int(limit or 1))))
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


def _spread_rows(rows, count):
    """Deterministically sample ``count`` rows across an ordered library."""
    rows = list(rows or [])
    count = max(0, min(int(count or 0), len(rows)))
    if count >= len(rows):
        return rows
    if count <= 0:
        return []
    if count == 1:
        return [rows[-1]]
    picks = []
    seen = set()
    for i in range(count):
        idx = round(i * (len(rows) - 1) / (count - 1))
        if idx not in seen:
            picks.append(rows[idx])
            seen.add(idx)
    return picks


def _clip_visual_plan(clips, tile_budget, per_clip, priority_ids=None,
                      used_keys=None):
    """[(clip, tile_count)] with fair coverage before visual depth.

    The old sequential allocator gave ten tiles each to the first clips and
    none to the rest.  This gives every selected clip one tile first, then
    distributes depth evenly.  Current-message attachments lead, unused
    library media comes next, and a very large remainder is sampled across
    upload order instead of silently truncating its tail.
    """
    clips = list(clips or [])
    budget = max(0, int(tile_budget or 0))
    ceiling = max(1, int(per_clip or 1))
    if not clips or budget <= 0:
        return [], len(clips)
    priority_ids = {int(x) for x in (priority_ids or [])
                    if str(x).isdigit()}
    used_keys = set(used_keys or [])
    priority = [row for row in clips if row.get("id") in priority_ids]
    rest = [row for row in clips if row.get("id") not in priority_ids]
    unused = [row for row in rest if row.get("storage_key") not in used_keys]
    used = [row for row in rest if row.get("storage_key") in used_keys]
    ordered = priority + unused + used
    slots = min(len(ordered), budget)
    selected = priority[:slots]
    remaining = [row for row in ordered if row not in selected]
    selected += _spread_rows(remaining, slots - len(selected))
    allocations = [1] * len(selected)
    left = budget - len(selected)
    i = 0
    while left > 0 and any(n < ceiling for n in allocations):
        if allocations[i] < ceiling:
            allocations[i] += 1
            left -= 1
        i = (i + 1) % len(allocations)
    return list(zip(selected, allocations)), len(clips) - len(selected)


def _image_visual_plan(images, image_budget, priority_ids=None,
                       used_keys=None):
    """Fair still-image coverage before truncating an extreme library."""
    images = list(images or [])
    budget = max(0, int(image_budget or 0))
    if not images or budget <= 0:
        return [], len(images)
    priority_ids = {int(x) for x in (priority_ids or [])
                    if str(x).isdigit()}
    used_keys = set(used_keys or [])
    priority = [row for row in images if row.get("id") in priority_ids]
    rest = [row for row in images if row.get("id") not in priority_ids]
    unused = [row for row in rest if row.get("storage_key") not in used_keys]
    used = [row for row in rest if row.get("storage_key") in used_keys]
    ordered = priority + unused + used
    slots = min(len(ordered), budget)
    selected = priority[:slots]
    remaining = [row for row in ordered if row not in selected]
    selected += _spread_rows(remaining, slots - len(selected))
    return selected, len(images) - len(selected)


def filmstrip_parts(ctx, worker_db, priority_asset_ids=None):
    """The per-turn senses message content: a labeled visual overview of
    the main footage, uploaded clips and stills, rebuilt every turn.

    Normal projects fit in full.  Very large libraries use explicit balanced
    budgets; current-message attachments are prioritized and omitted files
    remain named in project state for exact ``look_at_asset`` retrieval.

    Returns a content list ([{type: text}, {type: image_url}, ...]) or None
    when there is nothing visual to attach."""
    strips = []      # (label, [(key, local)])
    total = 0
    priority_asset_ids = list(priority_asset_ids or [])
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
        clips = list(worker_db.run(
            dbx.indexed_clips, ctx.project_id, 80) or [])
    except Exception:
        clips = []
    # An attachment can sit beyond indexed_clips' broad library query on an
    # extreme project. Fetch those few explicit ids and prepend them if ready.
    for aid in priority_asset_ids[:4]:
        try:
            attached = worker_db.run(dbx.get_asset, aid)
        except Exception:
            attached = None
        if attached and attached.get("kind") == "video_clip" and \
                attached.get("sha256") and \
                (attached.get("meta") or {}).get("indexed") and \
                not any(row.get("id") == attached.get("id") for row in clips):
            clips.insert(0, attached)
    try:
        used_keys = agent_tools.edl_used_asset_keys(ctx.latest_edl()["json"])
    except Exception:
        used_keys = set()
    clip_plan, clips_omitted = _clip_visual_plan(
        clips, max(0, config.TILES_TURN_MAX - total),
        config.TILES_CLIP_MAX, priority_asset_ids, used_keys)
    for c, budget in clip_plan:
        sha = c.get("sha256")
        if not sha:
            continue
        tiles, idx = _strip_for(worker_db, sha, budget)
        if not tiles:
            continue
        name = (c.get("meta") or {}).get("filename") or \
            os.path.basename(c["storage_key"])
        dur = c.get("duration_s") or (idx.get("video") or {}).get("duration")
        asset_role = ((c.get("meta") or {}).get("role") or "")
        role_note = ("STYLE REFERENCE ONLY — study its grammar; never insert "
                     "its picture" if asset_role in
                     ("edit_reference", "shorts_reference") else
                     "source footage available to place")
        strips.append((
            f"UPLOADED CLIP \"{name}\" — {float(dur or 0):.1f}s, "
            f"{role_note}, storage_key {c['storage_key']} "
            "(timestamps are CLIP seconds)",
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
        images = list(worker_db.run(
            lambda conn: _image_assets(conn, ctx.project_id)) or [])
    except Exception:
        images = []
    priority_images = []
    for aid in priority_asset_ids[:4]:
        try:
            attached = worker_db.run(dbx.get_asset, aid)
        except Exception:
            attached = None
        if attached and attached.get("kind") == "image_ref":
            priority_images.append(attached)
    deduped = []
    for row in priority_images + images:
        if not any(old.get("id") == row.get("id") for old in deduped):
            deduped.append(row)
    images, images_omitted = _image_visual_plan(
        deduped, config.IMAGES_TURN_MAX, priority_asset_ids, used_keys)
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
    omitted_note = ""
    if clips_omitted or images_omitted:
        omitted_note = (
            f" This project exceeds the visual overview budget: "
            f"{clips_omitted} clip(s) and {images_omitted} image(s) are "
            "inventory-only in this message. Use list_assets for their "
            "storage keys, then look_at_asset for any one that matters.")
    content = [{"type": "text", "text":
                "FILMSTRIPS & STILLS — a current, labeled visual overview "
                "of this project's footage and images. Current-message "
                "attachments are prioritized; normal libraries fit in full. "
                "Each video "
                "tile is a 2x2 grid of frames with the timestamp printed "
                "under each frame; each still image appears once, labeled "
                "with its storage_key. Read the footage directly: what is "
                "on screen, who is in frame, burned-in text or captions, "
                "UI content, framing, where the action is. For a closer or "
                "exact look at any moment, call look_at (main footage / "
                "program) or look_at_asset (a clip or image) — look as "
                "often as you need." + omitted_note}]
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
            # Database kind `music` is the upload container for every audio
            # file: songs, voice notes/VO and one-shot sounds. Never infer its
            # editorial role from that storage kind. (`audio` is different:
            # the pipeline's private transcription WAV and is skipped.)
            dur = (f" ({asset['duration_s']:.0f}s)"
                   if asset.get("duration_s") else "")
            notes.append(
                f'[User attached an audio file "{name}"{dur} — storage_key: '
                f'{asset["storage_key"]}. Decide FROM THEIR WORDS and its '
                "measured/transcribed evidence whether it is music, "
                "voiceover/dialogue, or a sound effect; do not assume "
                "'music' merely because that is the database asset kind.]")
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


def state_block(ctx, worker_db, denied_tools=()):
    """The CURRENT PROJECT STATE message: the footage, its transcript/shots,
    the current EDL and what is available to place.

    Extracted from _build_messages so the MCP surface (worker/mcp_exec) can
    hand an OUTSIDE model the same project facts the in-house agent gets.
    `denied_tools` changes only routing advice for tools intentionally absent
    from a surface; it never changes the underlying project state.
    """
    index = ctx.index
    if ctx.has_main_video:
        v = index["video"]
        video_line = (f"Video: {v['duration']}s ({v['duration']/60:.1f} min), "
                      f"{v['width']}x{v['height']} @ {v['fps']}fps, "
                      f"audio={'yes' if v['has_audio'] else 'no'}.")
        index_summary = _index_summary(index)
        # Let joined evidence avoid duplicating a transcript that this exact
        # caller just received.  MCP contexts start false as well; state_block
        # flips them only when its project-state response contains COMPLETE.
        ctx.full_transcript_in_context = \
            "TRANSCRIPT — COMPLETE" in index_summary
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
    music_lines = []
    for m in music:
        label = (f"{m['storage_key']} — "
                 f"{(m.get('meta') or {}).get('filename', '?')}")
        evidence = []
        idx = None
        if m.get("sha256"):
            try:
                idx_row = worker_db.run(dbx.get_index_by_sha, m["sha256"])
                idx = (idx_row or {}).get("json") or None
            except Exception:
                idx = None
        if idx:
            words = [w for w in (idx.get("words") or [])
                     if str(w.get("w") or "").strip()]
            if len(words) >= 3:
                excerpt = " ".join(str(w.get("w") or "").strip()
                                   for w in words[:18])
                evidence.append(
                    f"speech/vocals detected ({len(words)} words, "
                    f"{idx.get('language') or 'language unknown'}): "
                    f"\"{excerpt}{' …' if len(words) > 18 else ''}\"")
            else:
                evidence.append("no reliable speech/vocals transcript")
            sensed = idx.get("perception") or {}
            if sensed.get("bpm") and float(sensed.get("bpm_conf") or 0) >= .3:
                evidence.append(
                    f"measured {float(sensed['bpm']):.0f} BPM "
                    f"(confidence {float(sensed.get('bpm_conf') or 0):.2f})")
        if evidence:
            label += " — " + "; ".join(evidence)
        music_lines.append(label)

    # MEDIA INVENTORY — every video/image the project holds, its state, and
    # whether it is placed in the program. Rebuilt each turn: a clip that
    # finished indexing seconds ago shows up here (and its filmstrip below)
    # with nobody having to remember it.
    media_lines = []
    try:
        used_keys = agent_tools.edl_used_asset_keys(edl["json"])
        clips = worker_db.run(
            lambda conn: dbx.indexed_clips(conn, ctx.project_id, 80))
        all_clips = {c["id"]: c for c in clips}
        # Also list clips still indexing, so the agent never denies having
        # a file the user just added.
        pending = worker_db.run(
            lambda conn: _pending_clips(conn, ctx.project_id))
        unused_n = 0
        for c in list(all_clips.values()) + pending:
            cmeta = c.get("meta") or {}
            name = cmeta.get("filename") or \
                os.path.basename(c["storage_key"])
            dur = c.get("duration_s")
            state = ("indexed" if cmeta.get("indexed")
                     else "still analyzing — filmstrip/transcript arrive "
                          "shortly")
            if c["storage_key"] in used_keys:
                where = "in the current edit"
            else:
                where = ("AVAILABLE — not in the current edit "
                         "(insert_media / add_overlay)")
                unused_n += 1
            role = ("STYLE REFERENCE ONLY — an example to inspect, never "
                    "source footage to insert or edit"
                    if cmeta.get("role") in
                    ("shorts_reference", "edit_reference") else
                    "source clip")
            media_lines.append(
                f'  clip "{name}" ({float(dur or 0):.1f}s) — {role}; {state}, '
                f'{where}, storage_key {c["storage_key"]}')
            if cmeta.get("role") == "edit_reference" and c.get("sha256"):
                try:
                    ref_index = worker_db.run(
                        dbx.get_index_by_sha, c["sha256"])
                    measured = reference_profile.describe(
                        reference_profile.from_index(
                            (ref_index or {}).get("json") or {}))
                    if measured:
                        media_lines.append("    " + measured.replace(
                            "\n", "\n    "))
                except Exception as exc:
                    print(f"[reference] profile skipped: {exc}", flush=True)
        images = worker_db.run(
            lambda conn: _image_assets(conn, ctx.project_id))
        # State is text, but an unbounded filename dump still taxes every
        # model dispatch. Name a broad, fair slice and state the overflow;
        # list_assets returns the durable 200-row inventory on demand.
        inventory_images, inventory_omitted = _image_visual_plan(
            images, max(40, config.IMAGES_TURN_MAX), used_keys=used_keys)
        for a in inventory_images:
            ameta = a.get("meta") or {}
            name = ameta.get("filename") or \
                os.path.basename(a["storage_key"])
            if a["storage_key"] in used_keys:
                where = "in the current edit"
            else:
                where = ("AVAILABLE — not in the current edit "
                         "(insert_media / add_overlay)")
                unused_n += 1
            role = ("STYLE REFERENCE ONLY; "
                    if ameta.get("role") == "edit_reference" else "")
            media_lines.append(f'  image "{name}" — {role}{where}, storage_key '
                               f'{a["storage_key"]}')
        if inventory_omitted:
            suffix = "+" if len(images) >= 200 else ""
            media_lines.append(
                f"  {inventory_omitted}{suffix} additional image(s) are "
                "outside this text inventory slice — use "
                "list_assets(kind='image') for their storage keys, then "
                "look_at_asset for exact pixels.")
        for m in music:
            key = m.get("storage_key")
            fname = (m.get("meta") or {}).get("filename") or \
                os.path.basename(key or "?")
            if key in used_keys:
                where = "in the current edit"
            else:
                where = "AVAILABLE — not in the current edit (add_music)"
                unused_n += 1
            media_lines.append(
                f'  audio "{fname}" — role may be music, voiceover or SFX; '
                f'{where}, storage_key {key}')
        if unused_n:
            media_lines.insert(
                0, f"  {unused_n} file(s) are in the library and NOT on "
                   "the timeline. Place them; do not ask for a re-upload.")
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
    denied_tools = frozenset(denied_tools or ())
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
        if "edit_shorts" in denied_tools:
            lines.append(
                "When the user asks for a change to THE SHORTS — 'all of "
                "them', 'the shorts', 'short 3', 'add music to them' — NEVER "
                "edit this parent timeline (the original long video) and do "
                "not delegate to Valmera's agent. Use shorts_status and "
                "open_short, then make every requested change yourself with "
                "the normal editor tools on each child project.")
        else:
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
                route = (
                    "Use shorts_status and open_short to visit every selected "
                    "sibling, then make the changes yourself with the normal "
                    "editor tools; do not delegate to Valmera's agent."
                    if "edit_shorts" in denied_tools else
                    "Call edit_shorts(instruction, shorts) right from here — "
                    "it reaches the parent board automatically; never claim "
                    "the parent must be opened first.")
                block += (
                    f"\n\nTHIS PROJECT IS A GENERATED SHORT — {card} on "
                    f"the Shorts board of parent project {parent['id']} "
                    f"(“{parent.get('title') or ''}”). Edit THIS clip here "
                    "with the normal tools. When the user asks for a change "
                    "to ALL the shorts ('all of them', 'every short', 'the "
                    f"shorts'), {route}")
                treatment = (ctx.project.get("meta") or {}).get("clip") or {}
                story = treatment.get("story") or {}
                if isinstance(story, dict) and any(story.values()):
                    block += (
                        "\n\nSOURCE STORY CONTRACT — preserve this complete "
                        "micro-story while refining the cut:\n"
                        f"  SETUP: {story.get('setup') or '?'}\n"
                        f"  DEVELOPMENT: {story.get('development') or '?'}\n"
                        f"  PAYOFF: {story.get('payoff') or '?'}")
                if treatment.get("visual_direction"):
                    block += ("\nVISUAL DIRECTION — "
                              + str(treatment["visual_direction"])[:700])
                broll = treatment.get("broll") or []
                if broll:
                    rows = [
                        f"  source {moment.get('at')}s — "
                        f"{moment.get('query')} — PURPOSE: "
                        f"{moment.get('purpose')}"
                        for moment in broll[:6]
                    ]
                    block += (
                        "\nPLANNED B-ROLL — research actual candidates, judge "
                        "them visually, then place only shots that fulfill "
                        "the named story purpose; never generic wallpaper. "
                        "These are ORIGINAL source seconds: translate through "
                        "the current keep mapping before placing on the output "
                        "clock (on the untouched one-span seed, subtract the "
                        "clip's source start):\n"
                        + "\n".join(rows))
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
    blueprint = director.prompt_block(getattr(ctx, "edit_plan", None))
    if blueprint:
        block += "\n\n" + blueprint
    # Reference grammars suggest a skin; this contract states what must be
    # true for the chosen format to work. It is deliberately invariant-level
    # (no fixed cut/B-roll/effect density), so a novel style remains possible
    # while a vague "make it nice" still receives a real quality target.
    try:
        inferred = grammar.classify(ctx.index)[0] if ctx.has_main_video else None
        family = director.editorial_family(
            getattr(ctx, "edit_plan", None), inferred, ctx.has_main_video,
            request_text=getattr(ctx, "user_message", None))
        block += "\n\n" + editorial_contracts.prompt_block(family)
    except Exception as e:
        print(f"[editorial-contract] state block failed: {e}", flush=True)
    return block


def capabilities_block():
    """The CAPABILITIES message — generated from the tool registry, so it can
    never advertise a tool this deployment turned off.

    Names only (round 71f): the active stage's tools carry full contracts in
    the request schemas. Omitted domains are loaded with expand_toolset. The
    old first-sentence-per-tool digest was ~13k chars repeated every call;
    MCP still gets the complete catalog from catalog()."""
    return ("CAPABILITIES — the complete list of write operations that "
            "exist this deployment. Your current stage has full schemas for "
            "the relevant subset; if a listed capability is not callable, "
            "use expand_toolset for its domain: "
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


def _build_messages(ctx, worker_db, user_message, attachment_note="",
                    include_visual_overview=True):
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
    if include_visual_overview and ctx.direct_sight \
            and llm.agent_sees(ctx.agent_model):
        try:
            attachment_ids = (user_message.get("meta") or {}).get(
                "attachments") or []
            attachment_ids = [
                item.get("id") if isinstance(item, dict) else item
                for item in attachment_ids]
            parts = filmstrip_parts(
                ctx, worker_db, priority_asset_ids=attachment_ids[:4])
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


def _compact_consumed_look_frames(messages, before_index=None):
    """Drop targeted frame pixels only after they produced a committed edit.

    ``look_at``/``look_at_asset`` and preview review frames must survive the
    next model dispatch: that is when the editor actually judges them.  Once
    that judgment has landed a new EDL version, resending the same base64
    pictures on every later dispatch adds no evidence and can multiply the
    input bill dramatically in a long turn.  Keep the labels/timestamps as a
    provenance record and tell the model how to reopen the pixels.

    ``before_index`` excludes evidence captured by the same tool batch as the
    write.  Those new frames have not been seen by the model yet and therefore
    remain intact for the following dispatch.
    """
    limit = len(messages) if before_index is None else max(
        0, min(int(before_index), len(messages)))
    removed = 0
    marker = "Frames for your own eyes"
    for message in messages[:limit]:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        first_text = next((part.get("text", "") for part in content
                           if isinstance(part, dict)
                           and part.get("type") == "text"), "")
        if not first_text.startswith(marker):
            continue
        image_count = sum(
            1 for part in content if isinstance(part, dict)
            and part.get("type") == "image_url")
        if not image_count:
            continue
        labels = [part for part in content
                  if isinstance(part, dict) and part.get("type") == "text"]
        labels[0] = {
            "type": "text",
            "text": (
                "Frames for your own eyes — inspected before a committed "
                "EDL change. Their exact labels/timestamps remain below, "
                "but the old pixel payloads were released. If a later "
                "decision needs those pixels again, call look_at or "
                "look_at_asset for the named moment instead of relying on "
                "visual memory."),
        }
        message["content"] = labels
        removed += image_count
    return removed


def _compact_old_tool_results(messages, keep_latest=8, char_limit=900):
    """Keep protocol-valid tool history without resending bulky old output."""
    call_names = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            call_names[call.get("id")] = fn.get("name") or "tool"
    tool_indexes = [i for i, m in enumerate(messages)
                    if m.get("role") == "tool"]
    old = tool_indexes[:-max(0, int(keep_latest))] if keep_latest else tool_indexes
    changed = 0
    for i in old:
        message = messages[i]
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= char_limit:
            continue
        name = call_names.get(message.get("tool_call_id"), "tool")
        # Skill instructions and their exact constraints remain authoritative
        # for the whole turn; compact ordinary evidence and diffs only.
        if name == "read_skill":
            continue
        head = content[:max(300, char_limit - 380)].rstrip()
        tail = content[-180:].lstrip()
        message["content"] = (
            f"{head}\n...[older {name} result compacted; current EDL/state "
            f"is authoritative]...\n{tail}")
        changed += 1
    return changed


def _agent_request_token_estimate(messages, tools, max_tokens):
    """Conservative TPM estimate without counting base64 image bytes."""
    image_parts = 0

    def clean(value):
        nonlocal image_parts
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                image_parts += 1
                return {"type": "image_url", "image_url": "[image]"}
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    raw = json.dumps({"messages": clean(messages), "tools": tools},
                     ensure_ascii=False, separators=(",", ":"), default=str)
    prompt = (len(raw) + 2) // 3 + image_parts * 1700
    completion = min(max(0, int(max_tokens)), 8000)
    return max(2000, prompt + completion)


def _retryable_provider_rejection(exc):
    text = str(exc).lower()
    # Explicit provider refusals are safe to repeat. A timeout/reset is not:
    # the server may have accepted and billed the request before our socket
    # lost its answer, so replaying it can duplicate a very large prompt.
    return any(marker in text for marker in (
        "connection refused", "temporarily unavailable", "http 500",
        "http 502", "http 503", "http 504", "status 500", "status 502",
        "status 503", "status 504"))


def _adopt_steering_messages(ctx, worker_db, job, session_id, messages,
                             after_message_id):
    """Append messages typed while this editor is running; return newest id."""
    try:
        adopted = worker_db.run(
            dbx.adopt_queued_agent_steers, job["project_id"], job["id"],
            session_id, int(after_message_id or 0))
    except Exception as e:
        print(f"[agent {job['id']}] steering poll failed: {str(e)[:120]}",
              flush=True)
        return after_message_id
    if not adopted:
        return after_message_id
    rows = adopted.get("messages") or []
    job_ids = adopted.get("job_ids") or []
    if not rows:
        return after_message_id
    existing_ids = set(getattr(ctx, "adopted_steer_job_ids", set()))
    existing_ids.update(int(x) for x in job_ids)
    ctx.adopted_steer_job_ids = existing_ids
    messages.append({
        "role": "system",
        "content": (
            "The user added instructions while you were editing. Incorporate "
            "them now into this same turn. Preserve compatible work already "
            "landed; when an instruction conflicts, the newest user message "
            "wins. Do not finish or render the complete preview until these "
            "new instructions are handled."),
    })
    newest = int(after_message_id or 0)
    joined = []
    for row in rows:
        content = str(row.get("content") or "")[:4000]
        note = _attachment_context(worker_db, ctx, row)
        messages.append({"role": "user", "content": content + note})
        joined.append(content)
        newest = max(newest, int(row["id"]))
    if joined:
        ctx.user_message = (str(getattr(ctx, "user_message", "")) + "\n" +
                            "\n".join(joined))[-8000:]
        ctx.editing_metrics["steering_messages_adopted"] = (
            ctx.editing_metrics.get("steering_messages_adopted", 0)
            + len(rows))
        print(f"[agent {job['id']}] adopted {len(rows)} queued user "
              "message(s) into the live turn", flush=True)
    return newest


def _complete_adopted_steers(ctx, worker_db, active_job_id):
    ids = sorted(getattr(ctx, "adopted_steer_job_ids", set()))
    if not ids:
        return
    try:
        worker_db.run(dbx.complete_adopted_agent_steers, ids, active_job_id)
        ctx.adopted_steer_job_ids = set()
    except Exception as e:
        # Leave the durable rows queued. A duplicate follow-up is safer than
        # losing a message the live turn already promised to handle.
        print(f"[agent {active_job_id}] could not retire adopted steering "
              f"jobs ({str(e)[:120]}); durable fallback remains queued",
              flush=True)


def _activity(worker_db, session_id, name, args, result, source=None,
              edl_version=None, change=None, creative_blueprint=None):
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
    if creative_blueprint is not None:
        # Append-only durable direction: the latest activity carrying this
        # field becomes the project's blueprint on the next agent/MCP turn.
        normalized = director.normalize_blueprint(creative_blueprint)
        if normalized:
            meta["creative_blueprint"] = normalized
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
        return {"status": "no_index", "outcome": "blocked",
                "billable": False}

    workdir = os.path.join(config.TMP_DIR, f"agent_{job['id']}")
    os.makedirs(workdir, exist_ok=True)

    ctx = agent_tools.ToolContext(worker_db, job, project,
                                  index_row["json"] if index_row else None,
                                  workdir)
    try:
        ctx.edit_plan = director.normalize_blueprint(worker_db.run(
            dbx.latest_creative_blueprint, session_id))
        ctx.plan_loaded = bool(ctx.edit_plan)
    except Exception as e:
        # Direction improves continuity but never makes the editor unavailable.
        print(f"[director] could not load project blueprint: {e}", flush=True)
    # Round 67: only THIS loop can deliver captured frames into the model's
    # context (an MCP tool call has no loop — its result is text, and a
    # "the picture follows" claim there would be a lie). ToolContext ships
    # with direct sight off; the loop that can honour it turns it on.
    ctx.direct_sight = True
    ctx.user_message = (user_message.get("content") or "")[:4000]
    # Do not pre-warm the preview fleet here. Agent planning routinely lasts
    # longer than the executor's short idle window, which made the warm-up
    # container disappear before rendering and charged for two cold starts.
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
        # Persist the shape of a turn, not just its aggregate tokens.  The
        # Aug-13 spike was caused by more calls per job while prompt size and
        # cache behavior were stable; aggregate token totals alone could not
        # tell an operator whether planning, vision, repair or reply rewrites
        # multiplied.  Count failed calls too (they still cost wall time), but
        # token/cost fields below continue to use provider-reported usage.
        call_counts = ctx.editing_metrics.setdefault(
            "model_calls_by_purpose", {})
        purpose_key = str(purpose or "unknown")[:64]
        call_counts[purpose_key] = call_counts.get(purpose_key, 0) + 1
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
    """Surface one failed immutable preview as repair evidence."""
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
            "diagnose the invalid/over-expensive part, and make whatever "
            "corrective edits create a valid new EDL version while preserving "
            "the user's intent. Preview new versions as useful. If no honest "
            "EDL correction can address this failure, make no speculative "
            "change and tell the user the saved edit needs an infrastructure "
            "retry later."
        ),
    })
    return True


def _quality_repair_pushback(ctx, messages, t_start, pushed_versions):
    """Require one decision on each newly proven high-confidence defect.

    This is semantic, not a turn-count throttle: an immutable preview version
    is surfaced once. A repaired version may produce new evidence and earns
    its own decision; the same false positive cannot trap the model forever.
    """
    report = getattr(ctx, "last_visual_critic", None) or {}
    findings = preview_critic.repair_lines(report)
    findings.extend(story_critic.repair_lines(
        getattr(ctx, "last_story_review", None) or {}))
    audio_review = getattr(ctx, "last_audio_review", None) or {}
    if audio_review.get("verdict") == "fix" and audio_review.get("text"):
        findings.append(
            "independent actual-audio review: "
            + str(audio_review["text"])[:700])
    if not findings:
        return False
    preview = getattr(ctx, "last_preview", None) or {}
    version = preview.get("edl_version")
    try:
        latest = ctx.latest_edl()["version"]
    except Exception:
        return False
    if version is None or int(version) != int(latest) \
            or int(version) in pushed_versions:
        return False
    # Leave room for a targeted write plus one immutable proof. If there is
    # not enough clock, the finding is still disclosed in the final handoff.
    if config.AGENT_TURN_TIMEOUT_S - (time.monotonic() - t_start) < 120:
        return False
    pushed_versions.add(int(version))
    messages.append({
        "role": "system",
        "content": (
            f"The independent finishing review found publish-blocking craft "
            f"evidence in preview v{version}:\n- "
            + "\n- ".join(findings[:4])
            + "\nDo not hand this version off as world-class. Inspect the "
              "named exact moment if needed, then make a targeted repair and "
              "render the new version. If direct pixels prove a finding is a "
              "false positive or conflicts with the user's explicit choice, "
              "keep the version and state that concrete evidence; do not add "
              "random polish or repeat the same failed edit."
        ),
    })
    return True


_PLAN_WITHOUT_WRITE_NUDGE = (
    "You recorded an edit plan but have not written the EDL. A concrete "
    "brief is permission to cut — execute the plan NOW in this same turn. "
    "Do not ask the user to approve a clip order. Stop before writing only "
    "if a required asset is missing or a listed capability does not exist; "
    "otherwise apply_edit_recipe / the write tools and render_preview."
)


def _plan_without_write_pushback(ctx, messages, already_pushed):
    """A recorded plan with no EDL write is not a finished turn."""
    if already_pushed:
        return False
    plan = getattr(ctx, "edit_plan", None) or {}
    if not plan.get("steps"):
        return False
    if getattr(ctx, "versions_written", None):
        return False
    messages.append({"role": "system", "content": _PLAN_WITHOUT_WRITE_NUDGE})
    return True


def _plan_completion_pushback(ctx, messages, already_pushed):
    """One semantic close-out pass for a blueprint authored this turn.

    This is deliberately not a tool/call cap.  It asks the director to compare
    its own finite plan with the EDL and preview evidence, finish genuinely
    open work, and record closure.  A later turn inherits the unresolved state
    instead of rediscovering or silently forgetting it.
    """
    if already_pushed or not getattr(ctx, "plan_revised_this_turn", False):
        return False
    if not getattr(ctx, "versions_written", None):
        return False
    state = director.status(getattr(ctx, "edit_plan", None))
    if state["state"] in {"none", "complete", "blocked"}:
        return False
    messages.append({
        "role": "system",
        "content": (
            "Before you finish, close the CREATIVE BLUEPRINT against the "
            "current EDL and preview evidence. Its state is "
            f"{state['state']}; pending steps={state['pending_steps'] or 'none'}; "
            f"pending acceptance={state['pending_criteria'] or 'none'}; "
            f"failed acceptance={state['failed_criteria'] or 'none'}. "
            "Do not redo completed work and do not invent evidence. Finish or "
            "repair anything genuinely open, then call "
            "complete_edit_plan_steps (it can be batched with final reads). "
            "If a step is impossible with the available assets/capabilities, "
            "mark it blocked with the concrete reason instead of looping."
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
    if getattr(ctx, "audio_fetched", None):
        made.append(_n(len(ctx.audio_fetched),
                       "track downloaded", "tracks downloaded"))
    if not made:
        return ""
    return (" — but " + " and ".join(made)
            + " is saved to your project and ready to use")


def _quality_handoff(ctx):
    """Expose quality evidence without withholding the studio export CTA."""
    try:
        latest = ctx.latest_edl()["version"]
    except Exception:
        return {"quality_status": "unchecked", "export_ready": True}
    preview = getattr(ctx, "last_preview", None) or {}
    if preview.get("edl_version") != latest:
        return {"quality_status": "unchecked", "export_ready": True}

    findings = []
    report = getattr(ctx, "last_visual_critic", None) or {}
    for line in preview_critic.repair_lines(report)[:3]:
        findings.append(line)
    for line in story_critic.repair_lines(
            getattr(ctx, "last_story_review", None) or {})[:2]:
        findings.append(line)
    for line in (getattr(ctx, "last_audio_qc_findings", None) or [])[:2]:
        findings.append("audio QC: " + str(line))
    audio_review = getattr(ctx, "last_audio_review", None) or {}
    if audio_review.get("edl_version") == latest \
            and audio_review.get("verdict") == "fix":
        findings.append("actual-audio review: "
                        + str(audio_review.get("text") or "needs repair"))
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
        return {"quality_status": "advisory", "quality_findings": findings,
                "export_ready": True}
    story_report = getattr(ctx, "last_story_review", None) or {}
    status = ("pass" if report.get("verdict") == "pass" or
              story_report.get("verdict") == "pass" else "unchecked")
    return {"quality_status": status, "quality_findings": [],
            "export_ready": True}


def _unused_fetched_audio_note(ctx):
    """User-facing: a track was fetched this turn but never add_music'd."""
    keys = list(getattr(ctx, "audio_fetched", None) or [])
    if not keys:
        return ""
    try:
        music = (ctx.latest_edl()["json"] or {}).get("music") or []
    except Exception:
        music = []
    placed = {m.get("storage_key") for m in music}
    leftover = [k for k in keys if k not in placed]
    if not leftover:
        return ""
    return (" A soundtrack was downloaded into the project but was not "
            "placed on the timeline.")


def _disclose_outstanding_quality(ctx, text):
    """Disclose quality evidence without turning it into an export lock."""
    quality = _quality_handoff(ctx)
    if quality.get("quality_status") != "advisory":
        return text
    first = quality.get("quality_findings", ["a quality issue remains"])[0]
    return (text.rstrip() +
            "\n\nQuality advisory (export remains available): "
            + first[:360] + ".")


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
    return ("TURN FACTS (system-verified):\n"
            f"- {edl_line}\n"
            f"- Successful write tools this turn: {writes}\n"
            f"- Images generated this turn: {images}\n"
            f"- Media downloaded from links this turn: {fetched}\n"
            f"- Preview: {pv}\n"
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
     "or keyframed position/scale/rotation/opacity, and burn designed text "
     "templates: "
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
     "local push/pan motion on inserted images or video B-roll."),
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


def _turn_has_asset_progress(ctx):
    return any(getattr(ctx, name, None) for name in (
        "images_generated", "videos_generated", "urls_fetched",
        "web_recordings", "audio_extracted", "audio_fetched",
        "stock_added"))


def _record_outer_tool_outcome(ctx, name, result):
    """Record one model-visible tool result (a recipe remains one call)."""
    kind = agent_tools.tool_result_kind(result)
    first = str(result or "").strip().splitlines()[0][:500]
    # Normalize volatile numbers so "same invalid window at 7.91s/7.92s" is
    # still one repeated structural failure, while retaining the words and
    # tool identity that distinguish different recovery attempts.
    fingerprint = name + "|" + re.sub(r"\d+(?:\.\d+)?", "#", first)
    ctx.last_tool_result = first
    ctx.turn_tool_outcomes.append(
        {"tool": name, "kind": kind, "fingerprint": fingerprint,
         "writes": len(ctx.versions_written)})
    if name in agent_tools.WRITE_TOOLS:
        ctx.write_attempts += 1


def _repeated_tool_failure(ctx):
    rows = ctx.turn_tool_outcomes
    if len(rows) < 2:
        return False
    a, b = rows[-2], rows[-1]
    return (a.get("kind") in {"failed", "refused"}
            and b.get("kind") in {"failed", "refused"}
            and a.get("fingerprint") == b.get("fingerprint")
            and a.get("writes") == b.get("writes"))


def _turn_completion(ctx, status="replied", fail_note=None, truncated=False):
    """Return (outcome, billable) from value delivered, not job state."""
    has_value = bool(ctx.versions_written or _turn_has_asset_progress(ctx)
                     or ctx.last_preview)
    kinds = [row.get("kind") for row in ctx.turn_tool_outcomes]
    failed = "failed" in kinds
    refused = "refused" in kinds
    attempted_edit = bool(ctx.write_attempts or getattr(ctx, "edit_plan", None))

    if not has_value and (failed or status in {"timeout", "shutdown"}):
        outcome = "internal_error"
    elif not has_value and (refused or attempted_edit
                            or status in {"budget", "awaiting_user"}):
        outcome = "blocked"
    elif has_value and (status != "replied" or fail_note or failed):
        outcome = "partial"
    else:
        outcome = "fulfilled"

    # A read/analysis answer can be valuable without a timeline write. An
    # attempted EDIT that created nothing is not. Nor is a turn whose only
    # terminal fact is our own tool/infrastructure failure or truncation.
    billable = not (
        not has_value and (
            attempted_edit or failed or refused or truncated
            or status in {"timeout", "shutdown", "budget", "no_index"}
        )
    )
    return outcome, billable


def _outcome_meta(ctx, outcome):
    counts = {}
    for row in ctx.turn_tool_outcomes:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    usage = list((getattr(ctx, "model_usage", None) or {}).values())
    tokens_in = sum(int(row.get("in") or 0) for row in usage)
    cached_in = sum(int(row.get("cached") or 0) for row in usage)
    tokens_out = sum(int(row.get("out") or 0) for row in usage)
    try:
        latest = ctx.latest_edl()["json"]
        program = timeline.Timeline(latest.get("keep") or [],
                                    latest.get("inserts") or [],
                                    latest.get("speed") or [])
        duration = round(float(program.out_duration or 0.0), 2)
        effects = latest.get("effects") or {}
        edit_shape = {
            "duration_s": duration,
            "cuts": max(0, len(latest.get("keep") or [])
                        + len(latest.get("inserts") or []) - 1),
            "captions": bool(latest.get("captions")),
            "music_layers": len(latest.get("music") or []),
            "sfx": len(latest.get("sfx") or []),
            "zooms": len(effects.get("zooms") or []),
            "broll_overlays": sum(
                1 for row in (latest.get("overlays") or [])
                if row.get("fit") == "cover"),
            "designed_texts": len(latest.get("texts") or []),
            "vector_graphics": len(latest.get("vectors") or []),
        }
    except Exception:
        edit_shape = {}
    plan_state = director.status(getattr(ctx, "edit_plan", None))["state"]
    try:
        inferred_grammar = grammar.classify(
            getattr(ctx, "index", None))[0]
    except Exception:
        inferred_grammar = None
    editorial_family = director.editorial_family(
        getattr(ctx, "edit_plan", None), inferred_grammar,
        bool(getattr(ctx, "has_main_video", False)),
        request_text=getattr(ctx, "user_message", None))
    metrics = dict(getattr(ctx, "editing_metrics", None) or {})
    model_calls = metrics.get("model_calls_by_purpose") or {}
    try:
        estimated_model_cost_usd = round(
            float(ctx.running_credits()) * model_prices.USD_PER_CREDIT, 6)
    except Exception:
        estimated_model_cost_usd = 0.0
    metrics.update({
        "code_version": worker_version.code_version(),
        "editorial_family": editorial_family,
        "editorial_contract_v": editorial_contracts.CONTRACT_VERSION,
        "model_calls": sum(int(n or 0) for n in model_calls.values()),
        "agent_dispatches": int(model_calls.get("agent") or 0),
        "tool_calls": len(getattr(ctx, "turn_tool_outcomes", None) or []),
        "versions_written": len(getattr(ctx, "versions_written", None) or []),
        "previews_rendered": len(getattr(ctx, "rendered_versions", None) or []),
        "blueprint_state": plan_state,
        "tokens_in": tokens_in,
        "tokens_cached_in": cached_in,
        "prompt_cache_ratio": (round(cached_in / tokens_in, 3)
                               if tokens_in else 0.0),
        "tokens_out": tokens_out,
        "estimated_model_cost_usd": estimated_model_cost_usd,
        "edit_shape": edit_shape,
        "quality_evidence": {
            "visual_critic_verdict": (
                (getattr(ctx, "last_visual_critic", None) or {}).get("verdict")),
            "visual_rubric": (
                (getattr(ctx, "last_visual_critic", None) or {}).get("rubric")
                or {}),
            "visual_finding_categories": [
                row.get("category") for row in
                ((getattr(ctx, "last_visual_critic", None) or {}).get(
                    "findings") or [])],
            "deterministic_taste_findings": len(
                getattr(ctx, "last_taste", None) or []),
            "audio_qc_findings": len(
                getattr(ctx, "last_audio_qc_findings", None) or []),
            "audio_review_verdict": (
                (getattr(ctx, "last_audio_review", None) or {}).get(
                    "verdict")),
            "story_critic_v": story_critic.STORY_CRITIC_VERSION,
            "story_review_verdict": (
                (getattr(ctx, "last_story_review", None) or {}).get(
                    "verdict")),
            "story_finding_categories": [
                row.get("category") for row in
                ((getattr(ctx, "last_story_review", None) or {}).get(
                    "findings") or [])],
            "screening_frames": int(
                (getattr(ctx, "last_preview", None) or {}).get(
                    "screening_frame_count") or 0),
            "screening_pages": len(
                (getattr(ctx, "last_preview", None) or {}).get(
                    "screening_pages") or []),
        },
    })
    return {"outcome": outcome,
            "tool_outcomes": counts,
            "write_attempts": ctx.write_attempts,
            "editing_metrics": metrics}


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
    final_text += _unused_fetched_audio_note(ctx)
    outcome, billable = _turn_completion(ctx, status, fail_note=fail_note)
    meta = {"edl_version": latest["version"], "preview": ctx.last_preview,
            **_quality_handoff(ctx), **_outcome_meta(ctx, outcome)}
    if extra_meta:
        meta.update(extra_meta)
    worker_db.run(dbx.add_message, session_id, "assistant", final_text, meta)
    return {"status": status, "edl_version": latest["version"],
            "steps": total_steps, "auto_render": ctx.autorendered,
            "honesty": honesty, "timings": timings,
            "outcome": outcome, "billable": billable}


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


_CONTINUATION_NOTE = (
    "[system: CONTINUATION — this SAME request was resumed after {why}. "
    "The user has NOT seen any reply "
    "and has NOT sent anything new; the message above is the request you "
    "were already working on. Current PROJECT STATE and version history are "
    "authoritative. Already ran this turn: {done}.{plan} Continue exercising "
    "your own judgment: use, repeat, inspect, write, and preview with any "
    "tools needed to finish the request. The broad project filmstrip was "
    "already used during the earlier planning window and is not reattached "
    "to this continuation; use look_at/look_at_asset for exact pixels needed "
    "by any remaining visual decision.]")


def _run_loop(ctx, worker_db, job, session_id, user_message,
              attachment_note="", _cont=None):
    """Run a quality-driven tool-calling loop over the request.

    There is no arbitrary preview or write quota. Shutdown, available credits,
    shared provider capacity, an inactivity window, and an absolute turn wall
    bound runaway work. Productive work refreshes only the inactivity window.
    """
    # Resolved from the user's plan in run_agent_job. _build_messages and the
    # tool schemas are model-agnostic and do not change with it.
    client = ctx.llm_client or llm.client()
    # This loop already owns bounded, turn-budget-aware rate-limit recovery.
    # The SDK's five hidden retries could otherwise hold a user's message for
    # up to 7.5 minutes before our first 12-second recovery wait even began.
    # Disable that nested layer for agent calls only; other LLM consumers keep
    # the configured SDK retry policy.
    step_client = llm.without_sdk_retries(client)
    model = ctx.agent_model or config.AGENT_MODEL
    _cont = _cont or {}
    # A progress-window continuation is the SAME user turn after ten minutes
    # of already-landed work. Its durable blueprint + current EDL are rebuilt
    # below; paying for the broad 36-60-tile library overview again neither
    # restores the discarded internal conversation nor adds exact evidence.
    # The continuation note explicitly routes new visual decisions through
    # look_at/look_at_asset. Fresh user turns still receive the full overview.
    messages = _build_messages(
        ctx, worker_db, user_message, attachment_note,
        include_visual_overview=not bool(_cont))
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
                    ("treatment=" + str(ep.get("treatment"))
                     if ep.get("treatment") else ""),
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
    names = agent_tools.compact_tool_names(ctx)
    tools = agent_tools.openai_tools(
        model,
        compact=bool(getattr(ctx, "edit_plan", None) or ctx.versions_written),
        names=names)
    ctx.editing_metrics["initial_tool_schemas"] = len(tools)
    ctx.editing_metrics["initial_tool_schema_chars"] = len(
        json.dumps(tools, separators=(",", ":")))
    total_steps = _cont.get("steps", 0)
    t_start = _cont.get("t_start") or time.monotonic()
    turn_started = _cont.get("turn_started") or time.monotonic()
    seen_message_id = int(_cont.get("seen_message_id")
                          or user_message.get("id") or 0)
    timings = _cont.get("timings") or \
        {"llm_s": 0.0, "llm_calls": 0, "tools": {}}
    honesty = _cont.get("honesty") or \
        {"false_claims": 0, "corrective_note": False}
    start_version = ctx.latest_edl()["version"]
    # Completion budget for this step, and how many times a truncated step has
    # already been retried with a bigger one. See _TRUNCATED_NUDGE.
    max_tokens = config.AGENT_MAX_TOKENS
    truncated_retries = 0
    truncated_out = False          # last step died at the ceiling, saying nothing
    preview_repair_pushed = bool(_cont.get("preview_repair_pushed", False))
    quality_repair_versions = set(
        int(x) for x in (_cont.get("quality_repair_versions") or []))
    plan_write_pushed = bool(_cont.get("plan_write_pushed", False))
    plan_close_pushed = bool(_cont.get("plan_close_pushed", False))
    _responses_warned = False      # say the lane fell back ONCE, not per step

    while True:
        iteration = timings["llm_calls"]
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
                outcome = "partial"
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "I had to stop mid-request for a moment of "
                              "maintenance on my side — the edits I finished "
                              "are saved. Tell me to continue and I'll pick "
                              "up exactly where I left off.",
                              {"edl_version": latest["version"],
                               **_outcome_meta(ctx, outcome)})
            else:
                outcome = "internal_error"
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "I had to pause for a moment of maintenance on "
                              "my side before I could change anything — your "
                              "edit is untouched. Please send that again.",
                              {"edl_version": ctx.latest_edl()["version"],
                               **_outcome_meta(ctx, outcome)})
            return {"status": "shutdown", "steps": total_steps,
                    "timings": timings, "billable": False,
                    "outcome": outcome}
        total_expired = (time.monotonic() - turn_started
                         > config.AGENT_TURN_TOTAL_TIMEOUT_S)
        if total_expired or \
                time.monotonic() - t_start > config.AGENT_TURN_TIMEOUT_S:
            # Productive work may refresh the INACTIVITY window, but never the
            # absolute turn wall. Tool calls are synchronous, so a call already
            # running is not killed halfway through; its result is preserved.
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
            if _progressed and not ctx.over_budget() and not total_expired \
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
                           "turn_started": turn_started,
                           "seen_message_id": seen_message_id,
                           "timings": timings, "honesty": honesty,
                           "preview_repair_pushed": preview_repair_pushed,
                           "quality_repair_versions": sorted(
                               quality_repair_versions),
                           "plan_write_pushed": plan_write_pushed,
                           "plan_close_pushed": plan_close_pushed,
                           "writes0": len(ctx.versions_written),
                           "renders0": len(ctx.rendered_versions),
                           "assets0": asset_progress})
            why = (f"absolute turn limit of "
                   f"{config.AGENT_TURN_TOTAL_TIMEOUT_S:.0f}s reached"
                   if total_expired else
                   f"no editing/rendering progress for "
                   f"{config.AGENT_TURN_TIMEOUT_S:.0f}s")
            print(f"[job {job['id']}] {why}", flush=True)
            if ctx.versions_written:
                # Name the half-done state plainly. The old copy ("the edits
                # I completed are saved") read as DONE to a user who wasn't
                # counting their asks: a real request for "remove the TikTok
                # UI + brighten it" timed out after the erases with the
                # brightness never applied, the user read the stop as the
                # finish, and left (Aug 3 2026, project 335).
                return _finalize(
                    ctx, worker_db, session_id,
                    ("This edit reached this turn's safe processing limit, "
                     if total_expired else
                     "This edit stopped making progress on my side, ")
                    + "so I'm stopping here — the edits I finished are "
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
            outcome, billable = _turn_completion(ctx, "timeout")
            worker_db.run(dbx.add_message, session_id, "assistant",
                          ("That request reached this turn's safe processing "
                           "limit before I " if total_expired else
                           "That request stopped making progress before I ")
                          +
                          "could finish anything — the edit itself was not changed"
                          f"{saved}. Please try again, or break the request "
                          "into smaller steps.",
                          {"error": "turn_timeout",
                           **_outcome_meta(ctx, outcome)})
            return {"status": "timeout", "steps": total_steps,
                    "timings": timings, "outcome": outcome,
                    "billable": billable}

        seen_message_id = _adopt_steering_messages(
            ctx, worker_db, job, session_id, messages, seen_message_id)

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
                outcome, billable = _turn_completion(ctx, "budget")
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "You're out of credits, so I stopped before "
                              "changing anything. " + _refill,
                              {"error": "turn_budget",
                               "credits_exhausted": True,
                               "free_trial_exhausted": not subscribed,
                               "trial_cap_reached": trialing,
                               **_outcome_meta(ctx, outcome)})
            else:
                outcome, billable = _turn_completion(ctx, "budget")
                worker_db.run(dbx.add_message, session_id, "assistant",
                              "This request needed more work than its budget "
                              "allows, so I stopped before changing anything. "
                              "Try breaking it into smaller steps.",
                              {"error": "turn_budget",
                               **_outcome_meta(ctx, outcome)})
            return {"status": "budget", "steps": total_steps,
                    "timings": timings, "outcome": outcome,
                    "billable": billable}

        progress = (85 if ctx.rendered_versions else
                    55 if ctx.versions_written else
                    20 if getattr(ctx, "edit_plan", None) else 5)
        worker_db.run(dbx.set_progress, job["id"], progress)
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

        # Reserve this exact request before every dispatch. Completed-call
        # rows are retrospective; this atomic ledger prevents two workers
        # from simultaneously opening prompts that exceed the org TPM tier.
        estimate = _agent_request_token_estimate(messages, tools, max_tokens)
        capacity_expired = False
        capacity_waited = 0.0
        while True:
            try:
                wait = worker_db.run(
                    dbx.reserve_llm_tokens, estimate,
                    config.AGENT_TPM_SOFT_CAP, config.AGENT_TPM_WINDOW_S)
            except Exception as e:
                print(f"[agent {job['id']}] TPM reservation unavailable "
                      f"({str(e)[:120]}) — failing open", flush=True)
                wait = 0.0
            if not wait or wait <= 0:
                break
            remaining = min(
                config.AGENT_TURN_TOTAL_TIMEOUT_S
                - (time.monotonic() - turn_started),
                config.AGENT_TURN_TIMEOUT_S
                - (time.monotonic() - t_start))
            if remaining <= 0.25:
                capacity_expired = True
                break
            nap = min(float(wait), 20.0, remaining)
            print(f"[agent {job['id']}] reserving {estimate} TPM tokens "
                  f"would exceed the fleet soft cap — waiting {nap:.1f}s",
                  flush=True)
            time.sleep(nap)
            capacity_waited += nap
        if capacity_waited:
            timings["tpm_wait_s"] = round(
                timings.get("tpm_wait_s", 0.0) + capacity_waited, 2)
        if capacity_expired:
            continue
        t0 = time.monotonic()
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
            # chat/completions, ask the endpoint it named instead. Only a
            # definite unsupported-endpoint response falls back; ambiguous
            # timeouts never replay a possibly accepted expensive request.
            resp = None
            # What the request ACTUALLY carried, for the record below. Without
            # this the row said reasoning_effort='none' (the chat path's value)
            # on turns the lane had just asked to think hard — an audit trail
            # that contradicts the reasoning_out beside it is worse than none,
            # because it is the field you would check first.
            used_lane = None
            if llm.responses_available(model, config.OPENAI_BASE_URL):
                lane_rate_waits = 0
                for lane_attempt in range(3):
                    try:
                        resp = llm.responses_create(
                            config.OPENAI_BASE_URL, config.OPENAI_API_KEY,
                            model, messages, tools, max_tokens=max_tokens,
                            effort=step_effort,
                            timeout=config.AGENT_LANE_TIMEOUT_S)
                        used_lane = step_effort
                        break
                    except Exception as e:
                        permanent = llm.looks_like_responses_unsupported(e)
                        if permanent:
                            llm.mark_responses_dead(model)
                            if not _responses_warned:
                                _responses_warned = True
                                print(
                                    f"[agent {job['id']}] responses is not "
                                    f"supported ({str(e)[:180]}) — using "
                                    "chat/completions for this process",
                                    flush=True)
                            break
                        remaining = min(
                            config.AGENT_TURN_TOTAL_TIMEOUT_S
                            - (time.monotonic() - turn_started),
                            config.AGENT_TURN_TIMEOUT_S
                            - (time.monotonic() - t_start))
                        retry_wait = llm.rate_limit_wait(
                            e, lane_rate_waits + 1, remaining,
                            shutting_down=SHUTDOWN.is_set())
                        if retry_wait is not None and lane_attempt < 2:
                            lane_rate_waits += 1
                            print(f"[agent {job['id']}] responses lane was "
                                  f"rate-limited — waiting {retry_wait:.1f}s "
                                  "and retrying the same lane", flush=True)
                            time.sleep(retry_wait)
                            continue
                        if _retryable_provider_rejection(e) \
                                and lane_attempt < 2 \
                                and remaining > 3:
                            retry_wait = min(2 ** lane_attempt, remaining)
                            print(f"[agent {job['id']}] transient responses "
                                  f"failure — retrying the same lane in "
                                  f"{retry_wait:.1f}s", flush=True)
                            time.sleep(retry_wait)
                            continue
                        # Never duplicate a possibly accepted expensive call
                        # on chat/completions after a timeout/5xx. Only a
                        # definite unsupported-endpoint response falls back.
                        raise
            _adapt_tries = 0
            _rl_waits = 0
            while resp is None:
                try:
                    resp = step_client.chat.completions.create(
                        model=model, messages=messages, tools=tools, **kw)
                    break
                except Exception as e:
                    # Rate limits get their own budget-aware waits, BEFORE
                    # the dialect adapters — see llm.rate_limit_wait for the
                    # reasoning and bounds (round 91).
                    wait = llm.rate_limit_wait(
                        e, _rl_waits + 1,
                        min(config.AGENT_TURN_TIMEOUT_S
                            - (time.monotonic() - t_start),
                            config.AGENT_TURN_TOTAL_TIMEOUT_S
                            - (time.monotonic() - turn_started)),
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
            # A user message can arrive while the provider is drafting this
            # reply. Adopt it before the expensive complete preview and keep
            # editing; the draft remains context, not a premature response.
            steered_to = _adopt_steering_messages(
                ctx, worker_db, job, session_id, messages, seen_message_id)
            if steered_to != seen_message_id:
                seen_message_id = steered_to
                if body:
                    messages.append({"role": "assistant", "content": body})
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
            if _quality_repair_pushback(
                    ctx, messages, t_start, quality_repair_versions):
                print(f"[job {job['id']}] preview v{latest['version']} has "
                      "high-confidence craft defects — requesting a targeted "
                      "repair decision", flush=True)
                if body:
                    messages.append({"role": "assistant", "content": body})
                continue
            if _plan_without_write_pushback(
                    ctx, messages, plan_write_pushed):
                plan_write_pushed = True
                print(f"[job {job['id']}] plan recorded with no EDL write "
                      "— requesting execution instead of a propose-only "
                      "reply", flush=True)
                if body:
                    messages.append({"role": "assistant", "content": body})
                continue
            if _plan_completion_pushback(ctx, messages, plan_close_pushed):
                plan_close_pushed = True
                print(f"[job {job['id']}] creative blueprint still has open "
                      "semantic work — requesting one evidence-based close "
                      "pass", flush=True)
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
            # Language MUST run on the model draft before the English
            # quality advisory is appended — otherwise a Cyrillic reply
            # plus a long Latin advisory fails the 60% script threshold
            # and ships (Robbie / project 755).
            final = _enforce_reply_language(
                ctx, client, messages, tools, final,
                user_text=user_message["content"] or "", honesty=honesty)
            final = _disclose_outstanding_quality(ctx, final)
            final += _unused_fetched_audio_note(ctx)
            if fail_note:
                final += fail_note
            honesty["auto_render"] = ctx.autorendered
            outcome, billable = _turn_completion(
                ctx, "replied", fail_note=fail_note,
                truncated=truncated_out)
            message_meta = {
                "edl_version": latest["version"],
                "preview": ctx.last_preview,
                **_quality_handoff(ctx),
                **_outcome_meta(ctx, outcome),
            }
            worker_db.run(dbx.add_message, session_id, "assistant", final,
                          message_meta)
            _complete_adopted_steers(ctx, worker_db, job["id"])
            return {"status": "replied", "edl_version": latest["version"],
                    "steps": total_steps, "auto_render": ctx.autorendered,
                    "honesty": honesty, "timings": timings,
                    # A turn that only ever hit the token ceiling delivered
                    # nothing — no edit, no asset, not even an answer. We paid
                    # the provider; the user must not. Same principle as
                    # charge_turn_credits' "a turn that got nothing back costs
                    # nothing", one layer up where the reason is visible.
                    "billable": billable, "outcome": outcome,
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

        # Everything before this boundary was visible to the model when it
        # chose the tool batch. Frames captured by the batch itself are added
        # later and must survive into the next dispatch.
        visible_message_boundary = len(messages)
        batch_committed_edl = False

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
                                       "edl_version": _cur_v,
                                       "outcome": "blocked"})
                        _complete_adopted_steers(ctx, worker_db, job["id"])
                        return {"status": "awaiting_user",
                                "steps": total_steps, "timings": timings,
                                "outcome": "blocked"}
                tt = timings["tools"].setdefault(name, {"n": 0, "s": 0.0})
                tt["n"] += 1
                tt["s"] = round(tt["s"] + time.monotonic() - t0, 2)
                if name in agent_tools.WRITE_TOOLS and \
                        isinstance(result, str) and result.startswith("EDL v"):
                    ctx.write_calls.append(name)
                    batch_committed_edl = True
            _record_outer_tool_outcome(ctx, name, result)
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
                      change=chg,
                      creative_blueprint=(ctx.edit_plan if name in {
                          "set_edit_plan", "complete_edit_plan_steps"}
                          else None))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

        if _repeated_tool_failure(ctx):
            last = ctx.turn_tool_outcomes[-1]
            print(f"[job {job['id']}] stopping repeated deterministic "
                  f"failure from {last['tool']} before another model call",
                  flush=True)
            has_progress = bool(ctx.versions_written
                                or _turn_has_asset_progress(ctx))
            last_err = ""
            raw = str(getattr(ctx, "last_tool_result", "") or "").strip()
            if raw:
                last_err = " Last error: " + raw.splitlines()[0][:180]
            text = (
                "I stopped this attempt because the same editing operation "
                "failed twice without changing anything. No credits are "
                "charged for this run; try a different instruction or tell "
                "me the exact scene to target."
                if not has_progress else
                "I stopped the repeated failing operation instead of spending "
                "more time on the same error. The edits that did succeed are "
                "saved and previewed below; that last part is not complete."
            ) + last_err
            return _finalize(ctx, worker_db, session_id, text, "tool_stall",
                             total_steps, timings, honesty)

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

        if batch_committed_edl:
            released = _compact_consumed_look_frames(
                messages, before_index=visible_message_boundary)
            if released:
                ctx.editing_metrics["consumed_look_images_compacted"] = (
                    ctx.editing_metrics.get(
                        "consumed_look_images_compacted", 0) + released)

        # A fetched/generated visual's review is now in the model-visible
        # tool results (and, for direct sight, the image message above). The
        # next reasoning step may place it. Keeping this set through the whole
        # tool batch prevents a download+placement batch from claiming it was
        # judged before the evidence had actually reached the editor.
        if getattr(ctx, "_pending_visual_review_assets", None):
            ctx._pending_visual_review_assets.clear()

        compacted = _compact_old_tool_results(messages)
        if compacted:
            ctx.editing_metrics["old_tool_results_compacted"] = (
                ctx.editing_metrics.get("old_tool_results_compacted", 0)
                + compacted)

        # The broad contact sheets did their job on the planning call. Keep
        # exact look evidence added above, but do not pay to resend all
        # project pixels on every subsequent model dispatch.
        if getattr(ctx, "edit_plan", None) or ctx.versions_written:
            _compact_initial_filmstrip(messages)
            # Stage routing avoids resending 100+ schemas after every action.
            # Every capability remains name-visible and expand_toolset can
            # load any omitted domain; this changes context size, not power.
            names = agent_tools.compact_tool_names(ctx)
            tools = agent_tools.openai_tools(
                model, compact=True, names=names)
            ctx.editing_metrics["post_plan_tool_schemas"] = len(tools)
            ctx.editing_metrics["post_plan_tool_schema_chars"] = len(
                json.dumps(tools, separators=(",", ":")))

        # Speculative verification is a bounded changed-section proof only.
        # A complete preview is reserved for the turn-end honesty pass, so an
        # edit that takes five model steps cannot buy five full-length files.
        if ctx.versions_written and not SHUTDOWN.is_set():
            try:
                agent_tools.speculative_preview(ctx)
            except Exception:
                pass
