"""Harness v2's loop: one model, seven tools, look - act - look - reply.

    messages = [kernel, project state + conversation + the request]
    loop:
        reply = chain.call(messages, seven tools)   # lanes beneath, unseen
        tool calls: reads in parallel, writes in order -> envelopes + images
                    checkpoint after the step; compact; continue
        no calls:   post the reply verbatim; queue the player's render when
                    the edit changed; finalize

Nothing judges the model, rolls back its work, holds its tools before
dispatch or rewrites its reply. Reliability lives beneath: the lane chain,
a checkpoint after every step (a killed worker resumes at the step; writes
carry an operation id, so a replayed step never writes twice), slices with
a continuation, and a parked request when every lane is down.
"""
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import agent_loop
import agent_tools
import config
import db as dbx
import llm
import read_tools

from harness2 import chain
from harness2 import envelope
from harness2 import find_tool
from harness2 import measure as measure_mod
from harness2 import render_tool
from harness2 import tools as tools_mod

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL_PATH = os.path.join(HERE, "kernel.md")
SLICE_S = float(os.getenv("HARNESS2_SLICE_S", "420"))
MAX_STEPS_PER_SLICE = int(os.getenv("HARNESS2_MAX_STEPS", "40"))
MAX_TOKENS = int(os.getenv("HARNESS2_MAX_TOKENS", "8000"))
MAX_IMAGES_PER_STEP = 12
PARK_WAIT_S = 60.0
PARK_MAX_SLICES = 6
STATE_TRANSCRIPT_CHARS = 12000


# --------------------------------------------------------------------------
# messages

def kernel_text():
    with open(KERNEL_PATH, encoding="utf-8") as fh:
        text = fh.read()
    skills = sorted(os.path.splitext(n)[0] for n in os.listdir(
        os.path.join(os.path.dirname(HERE), "skills")) if n.endswith(".md"))
    return (text + "\n# Craft playbooks\n\nRead one with "
            "`look(show=\"skill:<name>\")`: " + ", ".join(skills) + ".\n")


def _assets(ctx):
    try:
        rows = ctx.db.run(read_tools.assets_by_kinds, ctx.project_id,
                          ("original", "video_clip", "image_ref", "music",
                           "audio"), 60) or []
    except Exception:  # noqa: BLE001
        rows = []
    out = []
    for r in rows:
        if r.get("kind") == "audio" and not (r.get("meta") or {}).get(
                "filename"):
            continue
        meta = r.get("meta") or {}
        name = meta.get("filename") or meta.get("title") or os.path.basename(
            str(r.get("storage_key") or ""))
        dur = r.get("duration_s")
        out.append(f"- {r.get('kind')} '{name}' key={r.get('storage_key')}"
                   + (f" {float(dur):.1f}s" if dur else ""))
    return out[:40]


def state_text(ctx):
    index = getattr(ctx, "index", None) or {}
    row = ctx.latest_edl()
    edl = row["json"] or {}
    measured = measure_mod.edl_measure(
        edl, float(getattr(ctx, "duration", 0.0) or 0.0) or None)
    lines = ["# The project now"]
    video = index.get("video") or {}
    if getattr(ctx, "has_main_video", False):
        lines.append(
            f"Source footage: {measure_mod.clock_text(ctx.duration)} "
            f"({video.get('width')}x{video.get('height')}, "
            f"{video.get('fps')} fps); transcript {len(index.get('words') or [])}"
            f" words; {len(index.get('shots') or [])} shots; storyboard "
            f"{len(((index.get('visual_storyboard') or {}).get('chapters')) or [])}"
            " chapters.")
    else:
        lines.append("No main footage: a canvas the film is built on "
                     "(uploads, pictures, narration).")
    lines.append(f"Current edit: v{row['version']}, film "
                 f"{measure_mod.clock_text(measured['program_s'])}, "
                 f"{measured['pieces']} piece(s)"
                 + (f", {100 * (measured['kept_share'] or 0):.1f}% of the "
                    "source kept" if measured.get("kept_share") else "")
                 + ". timeline() gives the whole of it.")
    try:
        shown = measure_mod.on_screen(edl)
    except Exception:  # noqa: BLE001
        shown = []
    if shown:
        lines.append("On screen now (film seconds; every edit's envelope "
                     "repeats this):")
        lines.extend(f"- {line}" for line in shown)
    assets = _assets(ctx)
    if assets:
        lines.append("Uploads:")
        lines.extend(assets)
    if index:
        try:
            summary = agent_loop._index_summary(index)
        except Exception:  # noqa: BLE001
            summary = ""
        if summary:
            if len(summary) > STATE_TRANSCRIPT_CHARS:
                summary = (summary[:STATE_TRANSCRIPT_CHARS] + "\n... (look("
                           "show='transcript', range=[a, b]) pages the rest)")
            lines.append(summary)
    return "\n".join(lines)


def initial_messages(ctx, worker_db, session_id, user_message):
    messages = [{"role": "system", "content": kernel_text()}]
    try:
        prior = worker_db.run(dbx.recent_chat, session_id, 16,
                              user_message["id"]) or []
    except Exception:  # noqa: BLE001
        prior = []
    for row in prior:
        if row.get("id") == user_message.get("id"):
            continue
        text = str(row.get("content") or "").strip()
        if text and row.get("role") in ("user", "assistant"):
            messages.append({"role": row["role"], "content": text[:4000]})
    request = str(user_message.get("content") or "").strip()
    state = state_text(ctx)
    try:
        from harness2 import find_tool
        named = find_tool.named_lines(request)
    except Exception:  # noqa: BLE001 - the library search is information
        named = []
    if named:
        state += "\n" + "\n".join(named)
    messages.append({"role": "user", "content": (
        state + "\n\n# The request\n" + request)})
    return messages


# --------------------------------------------------------------------------
# tool calls

def _operation_id(root_id, step, index, name, raw_args):
    digest = hashlib.sha1(f"{name}:{raw_args}".encode("utf-8")).hexdigest()
    return f"v2-{root_id}-{step}-{index}-{digest[:10]}"


def _calls(msg):
    out = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        out.append({"id": getattr(tc, "id", None) or f"call_{len(out)}",
                    "name": getattr(fn, "name", "") or "",
                    "arguments": getattr(fn, "arguments", "") or "{}"})
    return out


def run_calls(ctx, calls, root_id, step, wait_s=None):
    """[(call, envelope)]: reads in parallel, writes in order (a write sees
    every read and write before it in the same step)."""
    results = [None] * len(calls)
    reads = [i for i, c in enumerate(calls) if c["name"] in tools_mod.READS]
    if len(reads) > 1:
        def one(i):
            return i, tools_mod.dispatch(ctx, calls[i]["name"],
                                         calls[i]["arguments"],
                                         wait_s=wait_s)
        with ThreadPoolExecutor(max_workers=min(4, len(reads))) as pool:
            for i, env in pool.map(find_tool.carry(one), reads):
                results[i] = env
    for i, call in enumerate(calls):
        if results[i] is not None:
            continue
        args = tools_mod._args(call["arguments"])
        if call["name"] in ("edit", "undo") and not args.get("operation_id"):
            args["operation_id"] = _operation_id(root_id, step, i,
                                                 call["name"],
                                                 call["arguments"])
        results[i] = tools_mod.dispatch(ctx, call["name"], args,
                                        wait_s=wait_s)
    return list(zip(calls, results))


def image_message(images, model):
    if not images or not llm.agent_sees(model):
        return None
    content = [{"type": "text", "text": "Pictures from those tools, each "
                "labelled; times are printed on the tiles:"}]
    for label, path in images[:MAX_IMAGES_PER_STEP]:
        try:
            part = llm.image_part(path)
        except Exception:  # noqa: BLE001
            continue
        content.append({"type": "text", "text": f"[{label}]"})
        content.append(part)
    return {"role": "user", "content": content} if len(content) > 1 else None


def _without_images(messages):
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            labels = [p.get("text") for p in content
                      if isinstance(p, dict) and p.get("type") == "text"]
            m = dict(m, content=" ".join(t for t in labels if t)
                     + " [pictures were shown here]")
        out.append(m)
    return out


# --------------------------------------------------------------------------
# checkpoint, continuation, park

def save_checkpoint(conn, job_id, total_claims, checkpoint):
    from psycopg2.extras import Json
    lease = " AND total_claims = %s" if total_claims is not None else ""
    params = [Json(checkpoint), int(job_id)]
    if total_claims is not None:
        params.append(total_claims)
    with conn.cursor() as cur:
        cur.execute(f"""UPDATE video_jobs
                        SET payload = jsonb_set(COALESCE(payload, '{{}}'),
                                                '{{v2_checkpoint}}', %s,
                                                true),
                            updated_at = NOW()
                        WHERE id = %s AND state = 'running'{lease}""",
                    tuple(params))


def _checkpoint(worker_db, job, messages, step, start_version, extra=None):
    body = {"messages": _without_images(messages), "step": step,
            "start_version": start_version, **(extra or {})}
    try:
        worker_db.run(save_checkpoint, job["id"], job.get("total_claims"),
                      body)
    except dbx.JobLeaseLost:
        raise
    except Exception as exc:  # noqa: BLE001 - the step still stands
        print(f"[harness2] checkpoint not saved: {str(exc)[:160]}",
              flush=True)
    return body


def _continue(worker_db, job, user_message, checkpoint, why):
    payload = dict(job.get("payload") or {})
    root_id = int(payload.get("root_agent_job_id") or job["id"])
    sequence = int(payload.get("continuation_sequence") or 0) + 1
    state = dict(payload.get("continuation_state") or {})
    state.update({"durable_slice": True, "why": why,
                  "n": int(state.get("n") or 0) + 1, "v2": checkpoint})
    next_payload = {k: v for k, v in payload.items()
                    if k not in ("continuation_state", "death_resume",
                                 "steered_into", "v2_checkpoint")}
    next_payload.update({"message_id": int(user_message["id"]),
                         "root_agent_job_id": root_id,
                         "logical_turn_continuation": True,
                         "continuation_sequence": sequence,
                         "continuation_state": state})
    next_id = worker_db.run(
        dbx.enqueue_agent_continuation, job["project_id"], job["user_id"],
        root_id, sequence, next_payload, job["id"], job.get("total_claims"))
    return {"status": "continued", "outcome": "in_progress",
            "billable": False, "root_agent_job_id": root_id,
            "continued_job_id": next_id, "continuation_sequence": sequence,
            "harness": "v2"}


PARKED_TEXT = ("Your request is saved. The editing model is not answering "
               "right now; your request starts by itself as soon as it is "
               "back, and nothing you sent is lost.")


def _park(ctx, worker_db, job, session_id, user_message, checkpoint, err):
    parks = int(checkpoint.get("parks") or 0) + 1
    checkpoint = dict(checkpoint, parks=parks)
    print(f"[harness2] job {job['id']}: every lane is down ({err}); "
          f"park {parks}/{PARK_MAX_SLICES}", flush=True)
    if parks == 1:
        worker_db.run(dbx.add_message, session_id, "assistant", PARKED_TEXT,
                      {"edl_version": ctx.latest_edl()["version"],
                       "harness": "v2", "parked": True, "why": "provider"})
        try:
            worker_db.run(dbx.note_owner_alarm, "harness2_park",
                          f"v2 request parked: every editing lane down "
                          f"({str(err)[:200]})")
        except Exception:  # noqa: BLE001
            pass
    if parks >= PARK_MAX_SLICES:
        worker_db.run(dbx.add_message, session_id, "assistant", (
            "The editing model has not come back for a while, so this "
            "request has stopped. Your project and every saved version are "
            "intact; send the request again and it starts from here."),
            {"edl_version": ctx.latest_edl()["version"], "harness": "v2",
             "outcome": "provider_down"})
        return {"status": "replied", "outcome": "provider_down",
                "billable": False, "harness": "v2",
                "edl_version": ctx.latest_edl()["version"]}
    deadline = time.monotonic() + PARK_WAIT_S
    while time.monotonic() < deadline:
        agent_loop._sleep_keeping_lease(worker_db, job["id"], 15.0)
    chain.reopen()
    return _continue(worker_db, job, user_message, checkpoint,
                     "parked: provider")


# --------------------------------------------------------------------------
# finalize

def _outcome(ctx, start_version, envs):
    latest = ctx.latest_edl()
    changed = int(latest["version"]) != int(start_version)
    delivered = any((e.data or {}).get("format") in ("pptx", "pdf")
                    for e in envs if isinstance(e.data, dict))
    if changed or delivered:
        return "delivered", True, latest, changed
    return "replied", True, latest, changed


def finalize(ctx, worker_db, job, session_id, text, start_version, step,
             counts, envs, timings, extra_meta=None):
    outcome, billable, latest, changed = _outcome(ctx, start_version, envs)
    if changed and (getattr(ctx, "last_preview", None) or {}).get(
            "edl_version") != latest["version"]:
        try:
            render_tool.start_preview(ctx)
        except Exception as exc:  # noqa: BLE001 - the reply still posts
            print(f"[harness2] player render not queued: {exc}", flush=True)
    shown = getattr(ctx, "last_preview", None) or {}
    meta = {"edl_version": latest["version"], "harness": "v2",
            "outcome": outcome,
            "preview": shown if str(shown.get("edl_version")) == str(
                latest["version"]) else None,
            "v2": {"steps": step, "tools": counts,
                   "start_version": start_version}, **(extra_meta or {})}
    reply = str(text or "").strip() or (
        f"The film is now v{latest['version']}." if changed else
        "Done.")
    worker_db.run(dbx.add_message, session_id, "assistant", reply, meta)
    return {"status": "replied", "edl_version": latest["version"],
            "steps": step, "outcome": outcome, "billable": billable,
            "harness": "v2", "edl_changed": changed, "timings": timings,
            "tools": counts}


# --------------------------------------------------------------------------
# what the customer can pay for (a business rule, kept as it is in v1)

def _over_budget(ctx):
    try:
        return bool(ctx.over_budget())
    except Exception:  # noqa: BLE001
        return False


def _budget_stop(ctx, worker_db, job, session_id, start_version, step,
                 counts, envs, timings):
    """The turn's spend reached what the account can pay (credits plus the
    grace, or the trial's allowance): finish on what is saved, with v1's
    own words and the upgrade metadata the Studio reads."""
    subscribed = bool(getattr(ctx, "subscribed", False))
    trialing = bool(getattr(ctx, "trialing", False))
    if getattr(ctx, "claude_trial", None) is not None:
        import claude_trial
        text = claude_trial.EXHAUSTED_TEXT
        extra = claude_trial.stop_meta(claude_trial.Decision.EXHAUSTED,
                                       (job.get("payload") or {}).get(
                                           "message_id"))
    else:
        refill = ("That's the credits included with your free trial. Keep "
                  "your plan and the full monthly pool unlocks straight "
                  "away." if trialing else
                  "Your credits refresh on your plan's cycle, or upgrade for "
                  "a bigger monthly pool to keep editing now." if subscribed
                  else "Subscribe to keep editing.")
        text = ("That used up your available credits. Everything I finished "
                "is saved. " + refill)
        extra = {"credits_exhausted": True,
                 "free_trial_exhausted": not subscribed,
                 "trial_cap_reached": trialing}
    result = finalize(ctx, worker_db, job, session_id, text, start_version,
                      step, counts, envs, timings, extra_meta=extra)
    result["outcome"] = "budget"
    return result


def _record(model, messages, msg, usage):
    try:
        llm.record("agent",
                   {"model": model, "harness": "v2",
                    "messages": agent_loop._messages_for_record(messages[-3:]),
                    "tools": list(tools_mod.NAMES)},
                   {"content": getattr(msg, "content", None),
                    "tool_calls": [{"name": c["name"],
                                    "arguments": c["arguments"][:2000]}
                                   for c in _calls(msg)]}, usage)
    except Exception as exc:  # noqa: BLE001
        print(f"[harness2] llm record failed: {exc}", flush=True)


def run(ctx, worker_db, job, session_id, user_message,
        continuation_state=None):
    payload = job.get("payload") or {}
    ctx.harness = "v2"
    ctx.direct_sight = True
    checkpoint = payload.get("v2_checkpoint") or (
        (continuation_state or {}).get("v2") or {})
    root_id = int(payload.get("root_agent_job_id") or job["id"])
    if checkpoint.get("messages"):
        messages = list(checkpoint["messages"])
        step = int(checkpoint.get("step") or 0)
        start_version = int(checkpoint.get("start_version")
                            or ctx.latest_edl()["version"])
    else:
        messages = initial_messages(ctx, worker_db, session_id, user_message)
        step = 0
        start_version = int(ctx.latest_edl()["version"])
    counts = dict(checkpoint.get("counts") or {})
    started = time.monotonic()
    timings = {"llm_s": 0.0, "tools_s": 0.0, "llm_calls": 0,
               "lane_switches": 0}
    tool_schemas = tools_mod.openai_tools()
    envs = []
    slice_steps = 0

    def seconds_left():
        return SLICE_S - (time.monotonic() - started)

    def nap(seconds):
        agent_loop._sleep_keeping_lease(worker_db, job["id"], seconds)

    while True:
        if _over_budget(ctx):
            return _budget_stop(ctx, worker_db, job, session_id, start_version,
                                step, counts, envs, timings)
        if seconds_left() <= 5 or slice_steps >= MAX_STEPS_PER_SLICE:
            body = _checkpoint(worker_db, job, messages, step, start_version,
                               {"counts": counts})
            return _continue(worker_db, job, user_message, body,
                             "slice budget")
        t0 = time.monotonic()
        try:
            resp, lane, switches = chain.call(
                ctx.agent_lanes, messages, tool_schemas,
                max_tokens=MAX_TOKENS, sleep=nap, seconds_left=seconds_left)
        except chain.AllLanesDown as exc:
            body = _checkpoint(worker_db, job, messages, step, start_version,
                               {"counts": counts,
                                "parks": checkpoint.get("parks")})
            return _park(ctx, worker_db, job, session_id, user_message, body,
                         exc)
        timings["llm_s"] = round(timings["llm_s"] + time.monotonic() - t0, 2)
        timings["llm_calls"] += 1
        timings["lane_switches"] += switches
        if switches:
            ctx.llm_client, ctx.agent_model = lane["client"], lane["model"]
        msg = llm.agent_response_choice(resp).message
        _record(lane["model"], messages, msg, getattr(resp, "usage", None))
        calls = _calls(msg)
        if not calls:
            return finalize(ctx, worker_db, job, session_id,
                            getattr(msg, "content", "") or "", start_version,
                            step, counts, envs, timings)
        messages.append({"role": "assistant",
                         "content": getattr(msg, "content", None) or "",
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {
                                             "name": c["name"],
                                             "arguments": c["arguments"]}}
                                        for c in calls]})
        t1 = time.monotonic()
        before_version = ctx.latest_edl()["version"]
        done = run_calls(ctx, calls, root_id, step,
                         wait_s=min(render_tool.APP_WAIT_S,
                                    max(5.0, seconds_left() - 30)))
        timings["tools_s"] = round(timings["tools_s"] + time.monotonic()
                                   - t1, 2)
        images = []
        for call, env in done:
            counts[call["name"]] = counts.get(call["name"], 0) + 1
            envs.append(env)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": env.text()})
            images.extend(env.see)
            try:
                agent_loop._activity(
                    worker_db, session_id, call["name"],
                    tools_mod._args(call["arguments"]), env.text()[:600],
                    edl_version=(env.version if env.version is not None
                                 else before_version))
            except Exception:  # noqa: BLE001 - progress rows are best-effort
                pass
        sight = image_message(images, lane["model"])
        if sight:
            messages.append(sight)
        agent_loop._compact_old_tool_results(messages, keep_latest=8)
        if any(c["name"] in ("edit", "undo") for c in calls):
            agent_loop._compact_consumed_look_frames(
                messages, before_index=len(messages) - (1 if sight else 0))
        step += 1
        slice_steps += 1
        checkpoint = _checkpoint(worker_db, job, messages, step,
                                 start_version, {"counts": counts})
