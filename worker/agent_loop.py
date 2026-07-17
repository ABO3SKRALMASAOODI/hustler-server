"""Agent turn (job type "agent_turn"): one OpenAI tool-calling loop per user
chat message. Every tool call is persisted as an 'activity' chat message so
the frontend shows live progress over the existing polling channel."""

import difflib
import json
import os
import re
import shutil
import time

import agent_tools
import config
import db as dbx
import llm
import storage
from agent_prompt import SYSTEM_PROMPT, project_state_block
from schemas import describe_edl


def _silence_line(index):
    sil = [s for s in index.get("silences", []) if s[1] - s[0] >= 0.7]
    return (f"SILENCES >=0.7s: {len(sil)}, "
            f"totalling {sum(e - s for s, e in sil):.1f}s "
            "(use find_silences for the exact list with word context).")


def _full_shot_line(s):
    cap = s.get("caption") or {}
    parts = [cap.get("setting"), cap.get("people"), cap.get("action")]
    desc = " | ".join(p for p in parts if p)
    ost = cap.get("on_screen_text")
    if ost:
        desc += f'  on-screen text: "{ost}"'
    return (f"  [#{s['id']} {s['start']:.1f}-{s['end']:.1f}] "
            f"{desc or '(no visual description)'}")


def _full_index_block(index):
    """For SHORT videos, the ENTIRE index inlined into the turn prompt: every
    transcript sentence (untruncated) + every shot description + language. The
    model then never has to remember to call get_transcript/get_shots — the
    single biggest "it didn't bother to look" failure class. Returns None when
    the video is too long or the assembled text would exceed the char cap, so
    the caller falls back to the elided summary + retrieval tools."""
    v = index["video"]
    if float(v.get("duration") or 0) > config.FULL_INDEX_MAX_DURATION_S:
        return None
    sentences = index.get("sentences", [])
    words = index.get("words", [])
    shots = index.get("shots", [])
    lang = index.get("language")
    lines = []
    if sentences:
        lines.append(
            f"TRANSCRIPT — COMPLETE ({len(sentences)} sentences / "
            f"{len(words)} words, every sentence below; you already have the "
            "whole transcript, do NOT call get_transcript for this video — "
            "use get_words only for word-exact cut points):")
        for s in sentences:
            lines.append(f"  [{s['id']} {s['t0']:.1f}-{s['t1']:.1f}] "
                         f"{s['text']}")
    else:
        lines.append("TRANSCRIPT: none (no speech detected or no audio "
                     "track).")
    lines.append(
        f"SHOTS — COMPLETE ({len(shots)} shots, every visual description "
        "below; do NOT call get_shots for this video):"
        if shots else "SHOTS: none detected.")
    lines += [_full_shot_line(s) for s in shots]
    lines.append(_silence_line(index))
    if lang:
        lines.append(f"LANGUAGE (detected): {lang}.")
    text = "\n".join(lines)
    if len(text) > config.FULL_INDEX_MAX_CHARS:
        return None
    return text


def _index_summary(index):
    full = _full_index_block(index)
    if full is not None:
        return full

    # Long-video fallback: elided head/tail with pointers to the retrieval
    # tools, which stay available for pulling any range on demand.
    sentences = index.get("sentences", [])
    words = index.get("words", [])
    shots = index.get("shots", [])
    lines = [f"TRANSCRIPT: {len(sentences)} sentences / {len(words)} words "
             "(long video — only the head/tail is shown here; call "
             "get_transcript / search_transcript / get_words for the rest)."
             if sentences else
             "TRANSCRIPT: none (no speech detected or no audio track)."]

    def sent_line(s):
        return f"  [{s['id']} {s['t0']:.1f}-{s['t1']:.1f}] {s['text'][:110]}"

    if sentences:
        for s in sentences[:3]:
            lines.append(sent_line(s))
        if len(sentences) > 5:
            lines.append(f"  ... {len(sentences) - 5} more "
                         "(use get_transcript) ...")
            for s in sentences[-2:]:
                lines.append(sent_line(s))
        elif len(sentences) > 3:
            for s in sentences[3:]:
                lines.append(sent_line(s))

    lines.append(f"SHOTS: {len(shots)} total.")

    def shot_line(s):
        cap = s.get("caption") or {}
        desc = (cap.get("action") or cap.get("setting") or "")[:70]
        return (f"  [#{s['id']} {s['start']:.1f}-{s['end']:.1f}] {desc}")

    if len(shots) <= 25:
        lines += [shot_line(s) for s in shots]
    else:
        lines += [shot_line(s) for s in shots[:12]]
        lines.append(f"  ... {len(shots) - 17} more (use get_shots) ...")
        lines += [shot_line(s) for s in shots[-5:]]

    lines.append(_silence_line(index))
    if index.get("language"):
        lines.append(f"LANGUAGE (detected): {index['language']}.")
    return "\n".join(lines)


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
            notes.append(f'[User attached a video clip "{name}"{dur} — '
                         f'storage_key: {asset["storage_key"]}. It can be '
                         "spliced into the edit with insert_media.]")
        elif asset["kind"] == "image_ref":
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


def _build_messages(ctx, worker_db, user_message, attachment_note=""):
    index = ctx.index
    v = index["video"]
    video_line = (f"Video: {v['duration']}s ({v['duration']/60:.1f} min), "
                  f"{v['width']}x{v['height']} @ {v['fps']}fps, "
                  f"audio={'yes' if v['has_audio'] else 'no'}.")
    edl = ctx.latest_edl()
    edl_line = f"v{edl['version']} — {describe_edl(edl['json'], v['duration'])}"
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

    state = project_state_block(video_line, _index_summary(index), edl_line,
                                history_lines, music_lines,
                                keep_line=keep_line,
                                captions_line=captions_line)

    # Auto-generated from the tool registry every turn — the model checks
    # requests against this before promising anything.
    caps = ("CAPABILITIES — the complete list of write operations that "
            "exist, generated from the tool registry:\n"
            + agent_tools.capabilities_digest()
            + "\nNothing else exists. If the user asks for anything not "
            "listed (speed changes, stickers/GIF overlays pinned on top of "
            "moving footage, custom fonts, generated VIDEO footage or "
            "motion graphics, ...), say so plainly and offer the closest "
            "listed alternative — NEVER describe a change these tools "
            "cannot make, and NEVER claim something is impossible when a "
            "tool above covers it.")

    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": caps},
            {"role": "system", "content": state}]
    chat = worker_db.run(dbx.recent_chat, ctx.session_id, 20)
    for m in chat:
        if m["id"] == user_message["id"]:
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        content = (m["content"] or "")[:2000]
        if content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user",
                 "content": user_message["content"][:4000] + attachment_note})
    return msgs


def _activity(worker_db, session_id, name, args, result):
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
    worker_db.run(dbx.add_message, session_id, "activity",
                  f"{label} → {res_str}",
                  {"tool": name, "args": args})


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
    if not index_row:
        worker_db.run(dbx.add_message, session_id, "assistant",
                      "I can't edit yet — the video hasn't finished "
                      "indexing. Give it a moment and resend your request.")
        return {"status": "no_index"}

    workdir = os.path.join(config.TMP_DIR, f"agent_{job['id']}")
    os.makedirs(workdir, exist_ok=True)

    ctx = agent_tools.ToolContext(worker_db, job, project,
                                  index_row["json"], workdir)
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

    # Persist every model call this turn (agent, honesty regen, vision) to
    # llm_calls for the admin inspector, and accumulate token usage for the
    # spend cap. Payloads are capped + redacted in dbx.insert_llm_call;
    # failures never break the turn.
    def _llm_recorder(purpose, request, response, usage):
        if usage:
            ctx.tokens_in += getattr(usage, "prompt_tokens", 0) or 0
            ctx.tokens_out += getattr(usage, "completion_tokens", 0) or 0
        worker_db.run(dbx.insert_llm_call, job["project_id"], job["id"],
                      purpose, (request or {}).get("model"),
                      request, response,
                      getattr(usage, "prompt_tokens", None) if usage else None,
                      getattr(usage, "completion_tokens", None) if usage else None)
    llm.set_recorder(_llm_recorder)
    try:
        attachment_note = _attachment_context(worker_db, ctx, user_message)
        return _run_loop(ctx, worker_db, job, session_id, user_message,
                         attachment_note)
    except agent_tools.AskUser:
        raise   # never reaches here (handled in loop), but keep explicit
    except Exception as e:
        worker_db.run(dbx.add_message, session_id, "assistant",
                      "Something went wrong on my end while editing "
                      f"({str(e)[:160]}). Your video and edit history are "
                      "safe — try sending that again.")
        raise
    finally:
        llm.set_recorder(None)
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
        result = agent_tools.render_preview(ctx)
        timings["auto_render_s"] = round(time.monotonic() - t0, 2)
        _activity(worker_db, session_id, "render_preview",
                  {"auto": "model skipped it"}, result)
        if "FAILED" in result:
            fail_note = ("\n\n(Heads up: the preview render failed — "
                         f"{result[:200]})")
    return latest, fail_note


# ── TURN FACTS: the reply must match what the tools actually did ──────

EDIT_CLAIM = re.compile(
    r"(?i)("
    r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?|now|just) "
    r"(?:cut|trimmed|removed|applied|added|set|updated|changed|adjusted|"
    r"restored|made|moved|cropped|resized|reframed|inserted|spliced)\b"
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
    r"premium|presets?|podcast|beast|elegant|serif|uppercase|all.caps|"
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
    r"karaoke|highlights?|transitions?|dips?|ken.?burns|animations?)\b"
    r"|\b(?<!no )(?:color.?grade|grade|zoom|punch.?in|"
    r"fades?(?:[- ]?(?:in|out)| to black)?|filter|transitions?|"
    r"ken.?burns|animations?) "
    r"(?:is|was|has been|are|were) (?:now )?"
    r"(?:added|applied|set|enabled)\b"
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
            f"- Preview: {pv}\n"
            "Rules: your reply may not claim any change, render, or setting "
            "that is not present in these facts. If no writes occurred, say "
            "plainly that nothing was changed and why, or what you need "
            "from the user.")


# Nearest supported alternative for the honest fallback, keyed on what the
# user asked for. User-facing phrasing (no tool names).
ALTERNATIVE_HINTS = [
    # censor requests first: "remove the username/watermark" also contains
    # 'remove' (the cut hint) and 'logo/overlay' (the insert hint), and the
    # most specific hint must win the first-match scan
    (re.compile(r"(?i)username|user.?name|gamertag|nametag|name.?tag|"
                r"watermark|censor|blur|pixelat|black.?out|"
                r"(?:remove|hide|cover|get rid of)[^.\n]{0,40}"
                r"(?:text|logo|name|handle|tag|overlay)"),
     "What I CAN do: blur, pixelate or black-out a fixed rectangle over a "
     "burned-in username, watermark, logo or on-screen text — tell me "
     "roughly where it sits and I'll place the censor box and show you a "
     "preview."),
    # effects next: zoom/filter/fade phrasings often also contain 'animated'
    # or 'tiktok', and the most specific hint must win the first-match scan
    (re.compile(r"(?i)effect|filter|grade|zoom|punch|fade|transition|"
                r"viral|engag|animat|ken.?burns|motion"),
     "What I CAN do: color-grade the whole video (vibrant, warm, cool, "
     "black-and-white, vintage, cinematic), punch-in or smooth Ken Burns "
     "zooms, dip-to-black/white transitions at every cut, fade in/out, "
     "karaoke captions, animated caption entrances (fade/pop/slide), and "
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
     "What I CAN do: mix uploaded music under the edit on any time range, "
     "make existing music or narration louder/quieter, remove it, or lay an "
     "uploaded voiceover over the edit (other audio ducks while it speaks)."),
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


def _nearest_alternative(user_text):
    for rx, hint in ALTERNATIVE_HINTS:
        if rx.search(user_text or ""):
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
    acted = bool(ctx.versions_written or ctx.images_generated)
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
    try:
        resp = client.chat.completions.create(
            model=config.AGENT_MODEL, messages=msgs, tools=tools,
            tool_choice="none", temperature=config.AGENT_TEMPERATURE,
            max_tokens=800)
        llm.record("honesty_regen",
                   {"model": config.AGENT_MODEL, "messages": msgs[-2:],
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
    if wrote or ctx.images_generated:
        print(f"[honesty] job {ctx.job['id']}: regeneration still denies "
              "real changes — posting a corrective note", flush=True)
        return ("*(system: this turn DID modify the edit — see the editing "
                "steps above)*\n\n" + (redraft or draft))
    # Zero-write fabrication that survived regeneration: never publish it.
    honesty["fallback_reply"] = True
    print(f"[honesty] job {ctx.job['id']}: regeneration still fabricates — "
          "discarding both drafts and posting the system fallback",
          flush=True)
    hint = _nearest_alternative(user_text)
    return FALLBACK_REPLY + (f"\n\n{hint}" if hint else "")


def _finalize(ctx, worker_db, session_id, final_text, status, total_steps,
              timings, honesty=None):
    """Post a system-authored assistant reply (timeout/step-limit paths),
    auto-rendering first when the EDL changed without a preview."""
    latest, fail_note = _auto_render_if_needed(ctx, worker_db, session_id,
                                               timings)
    if fail_note:
        final_text += fail_note
    worker_db.run(dbx.add_message, session_id, "assistant", final_text,
                  {"edl_version": latest["version"],
                   "preview": ctx.last_preview})
    return {"status": status, "edl_version": latest["version"],
            "steps": total_steps, "auto_render": ctx.autorendered,
            "honesty": honesty, "timings": timings}


def _run_loop(ctx, worker_db, job, session_id, user_message,
              attachment_note=""):
    client = llm.client()
    messages = _build_messages(ctx, worker_db, user_message, attachment_note)
    tools = agent_tools.openai_tools()
    total_steps = 0
    t_start = time.monotonic()
    timings = {"llm_s": 0.0, "llm_calls": 0, "tools": {}}
    honesty = {"false_claims": 0, "corrective_note": False}
    start_version = ctx.latest_edl()["version"]

    for iteration in range(config.AGENT_MAX_ITERATIONS):
        if time.monotonic() - t_start > config.AGENT_TURN_TIMEOUT_S:
            print(f"[job {job['id']}] turn timeout after "
                  f"{config.AGENT_TURN_TIMEOUT_S:.0f}s", flush=True)
            if ctx.versions_written:
                return _finalize(
                    ctx, worker_db, session_id,
                    "That took longer than I allow myself per request, so "
                    "I'm stopping here — the edits I completed are saved "
                    "and previewed below. Send a follow-up to continue.",
                    "timeout", total_steps, timings, honesty)
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "That request timed out before I could finish "
                          "anything — nothing was changed. Please try again, "
                          "or break the request into smaller steps.",
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
            if ctx.versions_written:
                return _finalize(
                    ctx, worker_db, session_id,
                    "I've hit my budget for this request, so I'm stopping "
                    "here — the edits I completed are saved and previewed "
                    "below. Send a follow-up to keep going.",
                    "budget", total_steps, timings, honesty)
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "This request needed more work than its budget "
                          "allows, so I stopped before changing anything. "
                          "Try breaking it into smaller steps.",
                          {"error": "turn_budget"})
            return {"status": "budget", "steps": total_steps,
                    "timings": timings}

        worker_db.run(dbx.set_progress, job["id"],
                      int(100 * iteration / config.AGENT_MAX_ITERATIONS))
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=config.AGENT_MODEL,
            messages=messages,
            tools=tools,
            temperature=config.AGENT_TEMPERATURE,
            max_tokens=2000,
        )
        timings["llm_s"] = round(timings["llm_s"] + time.monotonic() - t0, 2)
        timings["llm_calls"] += 1
        msg = resp.choices[0].message
        llm.record("agent",
                   {"model": config.AGENT_MODEL, "messages": messages,
                    "tools": [t["function"]["name"] for t in tools]},
                   {"content": msg.content,
                    "tool_calls": [{"name": tc.function.name,
                                    "arguments": tc.function.arguments}
                                   for tc in (msg.tool_calls or [])]},
                   getattr(resp, "usage", None))

        if not msg.tool_calls:
            # Auto-render first so the turn facts include the real preview.
            latest, fail_note = _auto_render_if_needed(ctx, worker_db,
                                                       session_id, timings)
            draft = (msg.content or "").strip()
            if not draft:
                draft = ("Done — check the preview on the right."
                         if ctx.versions_written or ctx.last_preview else
                         "I only reviewed the video — nothing was changed.")
            final = _enforce_honesty(ctx, client, messages, tools, draft,
                                     start_version, honesty,
                                     user_text=user_message["content"] or "")
            if fail_note:
                final += fail_note
            honesty["auto_render"] = ctx.autorendered
            worker_db.run(dbx.add_message, session_id, "assistant", final,
                          {"edl_version": latest["version"],
                           "preview": ctx.last_preview})
            return {"status": "replied", "edl_version": latest["version"],
                    "steps": total_steps, "auto_render": ctx.autorendered,
                    "honesty": honesty, "timings": timings}

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
                    _activity(worker_db, session_id, name, args,
                              f"asked: {q.question}")
                    worker_db.run(dbx.add_message, session_id, "assistant",
                                  q.question, {"ask_user": True})
                    return {"status": "awaiting_user", "steps": total_steps,
                            "timings": timings}
                tt = timings["tools"].setdefault(name, {"n": 0, "s": 0.0})
                tt["n"] += 1
                tt["s"] = round(tt["s"] + time.monotonic() - t0, 2)
                if name in agent_tools.WRITE_TOOLS and \
                        isinstance(result, str) and result.startswith("EDL v"):
                    ctx.write_calls.append(name)
            total_steps += 1
            _activity(worker_db, session_id, name, args, result)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

    return _finalize(
        ctx, worker_db, session_id,
        "I hit my step limit for one request. The edits so far are saved — "
        "tell me to continue, or narrow the request.",
        "step_limit", total_steps, timings, honesty)
