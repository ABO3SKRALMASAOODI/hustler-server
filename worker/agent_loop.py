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
import music_library
import sfx_library
import storage
from agent_prompt import project_state_block, system_prompt
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
    if cap.get("subtitles"):
        desc += "  [burned-in captions visible]"
    return (f"  [#{s['id']} {s['start']:.1f}-{s['end']:.1f}] "
            f"{desc or '(no visual description)'}")


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
    bc = _burned_captions_line(index)
    if bc:
        lines.append(bc)
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

    bc = _burned_captions_line(index)
    if bc:
        lines.append(bc)
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
            "listed (motion-TRACKED stickers pinned to moving objects, "
            "true crossfades, custom font files, ...), say so plainly and "
            "offer the closest listed alternative — NEVER describe a "
            "change these tools cannot make, and NEVER claim something is "
            "impossible when a tool above covers it.")

    # system_prompt(), not the raw constant: it drops the built-in-library
    # claims when this image shipped no tracks.
    msgs = [{"role": "system", "content": system_prompt()},
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
    ctx.llm_client, ctx.agent_model = llm.agent_client_for(subscribed, plan)
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
        model = (request or {}).get("model")
        if usage:
            ctx.add_usage(model,
                          getattr(usage, "prompt_tokens", 0) or 0,
                          getattr(usage, "completion_tokens", 0) or 0,
                          cached_in, reasoning)
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
        if isinstance(response, dict) and (cached_in or reasoning):
            extra = {}
            if cached_in:
                extra["cached_in"] = cached_in
            if reasoning:
                extra["reasoning_out"] = reasoning
            response = dict(response, **extra)
        worker_db.run(dbx.insert_llm_call, job["project_id"], job["id"],
                      purpose, model,
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
        # The full exception goes to the worker log (and llm_calls) — the chat
        # gets a sentence the user can act on. See _user_facing_failure.
        print(f"[agent] job {job['id']} failed: {type(e).__name__}: {e}",
              flush=True)
        worker_db.run(dbx.add_message, session_id, "assistant",
                      _user_facing_failure(e))
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
    r"restored|made|moved|cropped|resized|reframed|inserted|spliced|"
    r"sped|slowed|overlaid|stylized|mastered|beat.?aligned)\b"
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
    if not made:
        return ""
    return (" — but " + " and ".join(made)
            + " is saved to your project and ready to use")


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
     "camera shutters — from the built-in sound pack, at any volume, and I "
     "can move or remove them afterwards."),
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
     "What I CAN do: score the edit with music on any time range — a track "
     "from the built-in royalty-free library or the user's own upload — "
     "loop it to fill the video, fade it in and out, start it partway in, "
     "swap one track for another, make it louder or quieter, or remove it. "
     "I can also lay an uploaded voiceover over the edit (other audio ducks "
     "while it speaks)."),
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


def _nearest_alternative(user_text):
    for rx, hint in ALTERNATIVE_HINTS:
        if rx.search(user_text or ""):
            # A deployment with link fetching switched off must not offer it.
            # Falling through to the next matching hint (rather than returning
            # nothing) keeps a pasted music link answered by the music hint.
            if "What I CAN do with a link" in hint \
                    and not config.URL_FETCH_ENABLED:
                continue
            # A deployment that shipped no tracks must not offer a library.
            if "built-in royalty-free library" in hint \
                    and not music_library.CATALOG:
                return ("What I CAN do: mix music you upload under the edit "
                        "on any time range, loop it to fill the video, fade "
                        "it in and out, make it louder or quieter, or remove "
                        "it. I can also lay an uploaded voiceover over the "
                        "edit (other audio ducks while it speaks).")
            if "built-in sound pack" in hint and not sfx_library.CATALOG:
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
        resp = client.chat.completions.create(
            model=model, messages=msgs, tools=tools,
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
    meta = {"edl_version": latest["version"], "preview": ctx.last_preview}
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


def _run_loop(ctx, worker_db, job, session_id, user_message,
              attachment_note=""):
    # Resolved from the user's plan in run_agent_job. _build_messages and the
    # tool schemas are model-agnostic and do not change with it.
    client = ctx.llm_client or llm.client()
    model = ctx.agent_model or config.AGENT_MODEL
    messages = _build_messages(ctx, worker_db, user_message, attachment_note)
    tools = agent_tools.openai_tools()
    total_steps = 0
    t_start = time.monotonic()
    timings = {"llm_s": 0.0, "llm_calls": 0, "tools": {}}
    honesty = {"false_claims": 0, "corrective_note": False}
    start_version = ctx.latest_edl()["version"]
    warned = set()                 # time-pressure marks already delivered
    # Completion budget for this step, and how many times a truncated step has
    # already been retried with a bigger one. See _TRUNCATED_NUDGE.
    max_tokens = config.AGENT_MAX_TOKENS
    truncated_retries = 0
    truncated_out = False          # last step died at the ceiling, saying nothing

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
            # "nothing was changed" is true of the EDL but not of the project:
            # a turn can time out after downloading a file, and telling the
            # user nothing happened would leave them re-pasting a link whose
            # media is already sitting in their media picker.
            saved = _assets_made_note(ctx)
            worker_db.run(dbx.add_message, session_id, "assistant",
                          "That request timed out before I could finish "
                          "anything — the edit itself was not changed"
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

        worker_db.run(dbx.set_progress, job["id"],
                      int(100 * iteration / config.AGENT_MAX_ITERATIONS))
        t0 = time.monotonic()
        # reasoning_effort, from the SECOND iteration on. Iteration 0 is where
        # the model reads the project state and plans the edit — that is the
        # thinking worth paying for. Everything after is tool dispatch, which
        # the providers themselves put under "low". Empty config sends no field
        # at all, so a provider that would reject an unknown parameter is
        # untouched until someone opts in.
        extra = {}
        if config.AGENT_REASONING_EFFORT and iteration > 0:
            extra["reasoning_effort"] = config.AGENT_REASONING_EFFORT
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                temperature=config.AGENT_TEMPERATURE,
                max_tokens=max_tokens,
                **extra,
            )
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
                        "messages": messages[-2:],
                        "tools": [t["function"]["name"] for t in tools]},
                       {"error": f"{type(e).__name__}: {str(e)[:400]}"}, None)
            raise
        timings["llm_s"] = round(timings["llm_s"] + time.monotonic() - t0, 2)
        timings["llm_calls"] += 1
        msg = resp.choices[0].message
        finish = getattr(resp.choices[0], "finish_reason", None)
        llm.record("agent",
                   {"model": model, "messages": messages,
                    "tools": [t["function"]["name"] for t in tools],
                    "max_tokens": max_tokens,
                    **({"reasoning_effort": extra["reasoning_effort"]}
                       if extra else {})},
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
            # Auto-render first so the turn facts include the real preview.
            latest, fail_note = _auto_render_if_needed(ctx, worker_db,
                                                       session_id, timings)
            draft = (msg.content or "").strip()
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
            if fail_note:
                final += fail_note
            honesty["auto_render"] = ctx.autorendered
            worker_db.run(dbx.add_message, session_id, "assistant", final,
                          {"edl_version": latest["version"],
                           "preview": ctx.last_preview})
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
            result = _time_pressure_note(result, t_start, warned)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

    return _finalize(
        ctx, worker_db, session_id,
        "I hit my step limit for one request. The edits so far are saved — "
        "tell me to continue, or narrow the request.",
        "step_limit", total_steps, timings, honesty)
