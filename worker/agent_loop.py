"""Agent turn (job type "agent_turn"): one OpenAI tool-calling loop per user
chat message. Every tool call is persisted as an 'activity' chat message so
the frontend shows live progress over the existing polling channel."""

import json
import os
import shutil

import agent_tools
import config
import db as dbx
import llm
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


def _build_messages(ctx, worker_db, user_message):
    index = ctx.index
    v = index["video"]
    video_line = (f"Video: {v['duration']}s ({v['duration']/60:.1f} min), "
                  f"{v['width']}x{v['height']} @ {v['fps']}fps, "
                  f"audio={'yes' if v['has_audio'] else 'no'}.")
    edl = ctx.latest_edl()
    edl_line = f"v{edl['version']} — {describe_edl(edl['json'], v['duration'])}"
    history = worker_db.run(dbx.edl_history, ctx.project_id)
    history_lines = [f"v{h['version']} ({h['created_by']})" for h in history]
    music = worker_db.run(agent_tools._music_assets, ctx.project_id)
    music_lines = [
        f"{m['storage_key']} — {(m.get('meta') or {}).get('filename', '?')}"
        for m in music]

    state = project_state_block(video_line, _index_summary(index), edl_line,
                                history_lines, music_lines)

    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": state}]
    chat = worker_db.run(dbx.recent_chat, ctx.session_id, 20)
    for m in chat:
        if m["id"] == user_message["id"]:
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        content = (m["content"] or "")[:2000]
        if content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": user_message["content"][:4000]})
    return msgs


def _activity(worker_db, session_id, name, args, result):
    arg_str = json.dumps(args or {}, ensure_ascii=False)
    if len(arg_str) > 160:
        arg_str = arg_str[:160] + "…"
    res_str = (result or "").replace("\n", " ")
    if len(res_str) > 240:
        res_str = res_str[:240] + "…"
    worker_db.run(dbx.add_message, session_id, "activity",
                  f"{name}{arg_str if arg_str != '{}' else '()'} → {res_str}",
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
    try:
        ctx = agent_tools.ToolContext(worker_db, job, project,
                                      index_row["json"], workdir)
        return _run_loop(ctx, worker_db, job, session_id, user_message)
    except agent_tools.AskUser:
        raise   # never reaches here (handled in loop), but keep explicit
    except Exception as e:
        worker_db.run(dbx.add_message, session_id, "assistant",
                      "Something went wrong on my end while editing "
                      f"({str(e)[:160]}). Your video and edit history are "
                      "safe — try sending that again.")
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _run_loop(ctx, worker_db, job, session_id, user_message):
    client = llm.client()
    messages = _build_messages(ctx, worker_db, user_message)
    tools = agent_tools.openai_tools()
    total_steps = 0

    for iteration in range(config.AGENT_MAX_ITERATIONS):
        worker_db.run(dbx.set_progress, job["id"],
                      int(100 * iteration / config.AGENT_MAX_ITERATIONS))
        resp = client.chat.completions.create(
            model=config.AGENT_MODEL,
            messages=messages,
            tools=tools,
            temperature=config.AGENT_TEMPERATURE,
            max_tokens=2000,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            final = (msg.content or "").strip() or \
                "Done — check the preview on the right."
            edl = ctx.latest_edl()
            worker_db.run(dbx.add_message, session_id, "assistant", final,
                          {"edl_version": edl["version"],
                           "preview": ctx.last_preview})
            return {"status": "replied", "edl_version": edl["version"],
                    "steps": total_steps}

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
                try:
                    result = agent_tools.execute(ctx, name, args)
                except agent_tools.AskUser as q:
                    _activity(worker_db, session_id, name, args,
                              f"asked: {q.question}")
                    worker_db.run(dbx.add_message, session_id, "assistant",
                                  q.question, {"ask_user": True})
                    return {"status": "awaiting_user", "steps": total_steps}
            total_steps += 1
            _activity(worker_db, session_id, name, args, result)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

    edl = ctx.latest_edl()
    worker_db.run(dbx.add_message, session_id, "assistant",
                  "I hit my step limit for one request. The edits so far are "
                  f"saved as EDL v{edl['version']} — tell me to continue, or "
                  "narrow the request.", {"edl_version": edl["version"]})
    return {"status": "step_limit", "edl_version": edl["version"],
            "steps": total_steps}
