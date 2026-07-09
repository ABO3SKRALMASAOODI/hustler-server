"""Agent turn (job type "agent_turn"): one OpenAI tool-calling loop per user
chat message. Every tool call is persisted as an 'activity' chat message so
the frontend shows live progress over the existing polling channel."""

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


def _index_summary(index):
    v = index["video"]
    sentences = index.get("sentences", [])
    words = index.get("words", [])
    shots = index.get("shots", [])
    sil = [s for s in index.get("silences", []) if s[1] - s[0] >= 0.7]
    lines = [f"TRANSCRIPT: {len(sentences)} sentences / {len(words)} words."
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

    lines.append(f"SILENCES >=0.7s: {len(sil)}, "
                 f"totalling {sum(e - s for s, e in sil):.1f}s "
                 "(use find_silences for the list).")
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
        if asset["kind"] in ("music", "audio"):
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
            "listed (speed changes, transitions, zooms, filters, fonts, "
            "animations, ...), say so plainly and offer the closest listed "
            "alternative — NEVER describe a change these tools cannot make.")

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

    # Persist every model call this turn (agent, honesty regen, vision) to
    # llm_calls for the admin inspector. Payloads are capped + redacted in
    # dbx.insert_llm_call; failures never break the turn.
    def _llm_recorder(purpose, request, response, usage):
        worker_db.run(dbx.insert_llm_call, job["project_id"], job["id"],
                      purpose, (request or {}).get("model"),
                      request, response,
                      getattr(usage, "prompt_tokens", None) if usage else None,
                      getattr(usage, "completion_tokens", None) if usage else None)
    llm.set_recorder(_llm_recorder)
    try:
        ctx = agent_tools.ToolContext(worker_db, job, project,
                                      index_row["json"], workdir)
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
    r"|\bcaptions? (?:are|is|were|have been)[^.\n]{0,60}"
    r"(?:red|blue|green|yellow|white|black|orange|purple|pink|"
    r"#[0-9A-Fa-f]{6}|top|bottom|middle|cent(?:er|re)|bigger|smaller)"
    r"|\bis now (?:red|blue|green|yellow|white|black|orange|purple|pink|"
    r"#[0-9A-Fa-f]{6}|at the top|at the bottom|in the middle|centered|"
    r"cropped|9:16|16:9|1:1|4:5|vertical|square|portrait|landscape|"
    r"bigger|smaller)\b"
    r"|\b(?:font|colou?r|style|frame|aspect ratio) (?:is|was|has been) "
    r"(?:changed|set|updated|applied)\b"
    # audio claims — "The music now plays only from 0.0 to 15.0 seconds…"
    # Stative/perfect constructions only, so honest offers ("I can make the
    # music quieter") don't trip the guard.
    r"|\b(?:music|audio|track|song|soundtrack|voice.?over|narration|sound)\b"
    r"[^.\n]{0,60}\b(?:now plays|plays? only|plays? from|is cut|"
    r"cut (?:after|off)|(?:is|are|was|were|has been|have been) (?:now )?"
    r"(?:added|removed|lowered|reduced|quieter|louder|softer|ducked|muted|"
    r"cut|trimmed|gone))\b"
    r"|\bvolume (?:is |was |has been )?(?:lowered|raised|reduced|increased|"
    r"set|changed|adjusted)\b"
    r"|\b(?:lowered|raised|reduced|boosted) (?:the )?(?:volume|music|audio)\b"
    r")")
RENDER_CLAIM = re.compile(
    r"(?i)(\b(?<!no )preview (?:is |was |has been )?"
    r"(?:rendered|ready|updated|attached|refreshed|playing)\b"
    r"|\brendered (?:a |the )?(?:new )?preview\b|\bre-?rendered\b"
    r"|\brendering (?:the |a )?(?:new )?preview\b)")
DENY_CLAIM = re.compile(
    r"(?i)(\bedl (?:did not|didn't) change\b"
    r"|\bnothing (?:was |has been )?changed\b"
    r"|\bno changes? (?:were|was|have been) made\b"
    r"|\bdidn'?t (?:change|modify|touch) (?:the )?(?:edl|edit|video|anything)\b"
    r"|\b(?:edit|edl) (?:is|remains) unchanged\b)")


def _reply_violations(draft, wrote, previewed):
    """Each violation names the exact fabricated claim it matched, so the
    regeneration correction (and the logs) point at the offending words."""
    v = []
    # An explicit denial ("nothing was changed") dominates — its own words
    # ("changes were made") must not read as a change claim.
    m = EDIT_CLAIM.search(draft)
    if not wrote and m and not DENY_CLAIM.search(draft):
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
            f"- Preview: {pv}\n"
            "Rules: your reply may not claim any change, render, or setting "
            "that is not present in these facts. If no writes occurred, say "
            "plainly that nothing was changed and why, or what you need "
            "from the user.")


# Nearest supported alternative for the honest fallback, keyed on what the
# user asked for. User-facing phrasing (no tool names).
ALTERNATIVE_HINTS = [
    (re.compile(r"(?i)9.?:.?16|16.?:.?9|1.?:.?1|4.?:.?5|aspect|ratio|"
                r"vertical|portrait|square|crop|tiktok|reels?|shorts?"),
     "What I CAN do: change the output frame to 16:9, 9:16, 1:1 or 4:5 with "
     "a center-crop or a padded fit."),
    (re.compile(r"(?i)caption|subtitle|font|animat|outline|middle|"
                r"cent(?:er|re)"),
     "What I CAN do with captions: color (#RRGGBB), size (s/m/l) and "
     "position (top / middle / bottom)."),
    (re.compile(r"(?i)voice.?over|narrat|music|song|soundtrack|audio|volume"),
     "What I CAN do: mix uploaded music under the edit on any time range, "
     "make existing music or narration louder/quieter, remove it, or lay an "
     "uploaded voiceover over the edit (other audio ducks while it speaks)."),
    (re.compile(r"(?i)insert|splice|b.?roll|logo|image|photo|clip|overlay"),
     "What I CAN do: splice an uploaded video clip or image between "
     "segments — attach or upload it and tell me where."),
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
    previewed = ctx.last_preview is not None
    viol = _reply_violations(draft, wrote, previewed)
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
    if redraft and not _reply_violations(redraft, wrote, previewed):
        return redraft
    honesty["false_claims"] += 1
    honesty["corrective_note"] = True
    honesty["discarded_drafts"] = [d for d in (draft, redraft) if d]
    if wrote:
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
