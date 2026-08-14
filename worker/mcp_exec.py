"""MCP tool execution — job type "mcp_tool" (round 49).

The MCP surface hands Valmera's editor to an OUTSIDE model: Claude running in
the user's own Claude Code session, on their own Anthropic subscription. It
replaces the LLM in the agent loop and NOTHING else — the tool registry, the
ToolContext, the EDL writes, the honest-off gating and the activity feed are
the same code the in-house agent runs, in the same process, on the same box.
Parity is therefore structural: there is no second list of tools to keep in
sync, and a tool added to agent_tools.TOOLS is on the MCP surface the moment
the worker restarts.

One tool call from Claude becomes one `mcp_tool` row in video_jobs, claimed by
the worker's own MCP lane (so it never queues behind a customer's agent turn
or steals a slot from one) and answered by the backend, which polls the row.

WHY A CACHED CONTEXT. The agent loop builds one ToolContext per turn and
throws it away; MCP has no turn — every call arrives cold. Rebuilding the
context per call would re-download the 540p proxy for every look_at and re-read
the index JSON for every cut. So the context is kept per project, keyed on the
indexed video's sha (a replaced upload invalidates it), and swept after
MCP_SESSION_TTL_S. A per-session lock serializes calls on one project, which is
also what keeps two overlapping tool calls from racing each other's EDL write.

NOT BILLED. An MCP call runs no agent model of ours — the thinking is paid for
by the caller's Claude subscription — but vision, image/video generation and
stock fetches still cost real money, and they are recorded to llm_calls under
this job id so the admin cost views see them. Nothing charges credits for them
today. That is fine while MCP is admin-only; it MUST be decided before the
surface is sold (see docs/MCP.md).
"""

import os
import shutil
import threading
import time

import agent_loop
import agent_tools
import config
import db as dbx
import director
import llm
import mcp_media
import storage
from agent_prompt import system_prompt

# Control calls the backend makes on the model's behalf — not editor tools, so
# they never appear in the catalog the model sees.
CATALOG_TOOL = "__catalog__"
STATE_TOOL = "__state__"
MEDIA_TOOL = "__media__"        # watch_video — see mcp_media.py

# Editor tools that exist for Valmera's in-house agent but must never be
# reachable from MCP. edit_shorts is not an EDL operation, and the locked-card
# lifecycle reserves in-house child-agent boot for an explicit Studio press.
# The model on the other end of MCP is already the editor, so it opens a child
# and edits that EDL directly.
MCP_DENIED_TOOLS = frozenset({"edit_shorts"})
MCP_DENIED_MESSAGE = (
    "edit_shorts is unavailable over MCP. Studio's child-agent boot is an "
    "explicit locked-card action. Edit each child yourself: call "
    "shorts_status, open_short, then use the normal EDL editing and preview "
    "tools.")


class _Session:
    def __init__(self, ctx, workdir, sha):
        self.ctx = ctx
        self.workdir = workdir
        self.sha = sha              # index sha the context was built on
        self.lock = threading.Lock()
        self.used = time.time()


_sessions = {}                      # project_id -> _Session
_sessions_lock = threading.Lock()


def catalog():
    """Every direct editor tool this deployment can run, plus the prompt.

    Built from the live registry, so tools whose backing service is
    unconfigured remain absent. Agent-orchestration tools are filtered too:
    an outside model must edit with the EDL tools itself, never enqueue a
    second model to do its work.
    """
    tools = [t for t in agent_tools.openai_tools()
             if (t.get("function") or {}).get("name")
             not in MCP_DENIED_TOOLS]
    return {
        "tools": tools,
        "system_prompt": system_prompt(),
        "capabilities": agent_loop.capabilities_block(),
        "write_tools": sorted(agent_tools.WRITE_TOOLS),
    }


_EVICT_GRACE_S = 5


def _drop_dead_sessions(now, keep=None):
    """Sweep idle contexts (and their work dirs).

    Two locks protect a live call from being swept out from under it: the
    caller's own session is never a candidate (`keep`), and any session is
    skipped unless its lock is free AND it has been idle for a few seconds —
    which covers the one gap where a thread has taken a session out of the map
    but not yet locked it. Oldest first, so pressure evicts the coldest."""
    order = sorted(_sessions.items(), key=lambda kv: kv[1].used)
    for pid, s in order:
        if pid == keep:
            continue
        idle = now - s.used
        over = len(_sessions) > config.MCP_MAX_SESSIONS
        if idle < config.MCP_SESSION_TTL_S and \
                not (over and idle > _EVICT_GRACE_S):
            continue
        if not s.lock.acquire(blocking=False):
            continue
        try:
            _sessions.pop(pid, None)
            shutil.rmtree(s.workdir, ignore_errors=True)
        finally:
            s.lock.release()


def _drain_images(ctx):
    """Frames a look tool captured this call -> [{storage_key, label}].

    They go through STORAGE rather than riding the job row as base64: the
    result column is JSONB, and a permanent copy of every picture anyone ever
    looked at is not something a database should be carrying. The backend
    reads them back and emits them as MCP image content."""
    pending, ctx.pending_images = list(ctx.pending_images or []), []
    out = []
    for label, path in pending[:config.MCP_MAX_IMAGES]:
        try:
            key = (f"media/{ctx.project_id}/look_"
                   f"{os.path.basename(path).rsplit('.', 1)[0]}_"
                   f"{int(os.path.getsize(path))}.jpg")
            storage.upload_file(path, key, "image/jpeg")
        except Exception as ex:
            print(f"[mcp] could not publish look frame ({ex})", flush=True)
            continue
        out.append({"storage_key": key, "label": label})
    if len(pending) > config.MCP_MAX_IMAGES:
        print(f"[mcp] {len(pending) - config.MCP_MAX_IMAGES} look frame(s) "
              "not sent (per-call cap)", flush=True)
    return out


def _index_for(worker_db, project_id):
    """(index_json, sha, has_original) for the project's main video. index is
    None for a canvas program (no original at all) AND for a video that is
    still being analyzed — has_original tells those two apart."""
    original = worker_db.run(dbx.latest_asset, project_id, "original")
    if not original:
        return None, None, False
    if not original.get("sha256"):
        return None, None, True
    row = worker_db.run(dbx.get_index_by_sha, original["sha256"])
    if not row:
        return None, original["sha256"], True
    return row["json"], original["sha256"], True


def _new_context(worker_db, job, project, index, sha):
    workdir = os.path.join(config.TMP_DIR,
                           f"mcp_{project['id']}_{int(time.time())}")
    os.makedirs(workdir, exist_ok=True)
    ctx = agent_tools.ToolContext(worker_db, job, project, index, workdir)
    try:
        ctx.edit_plan = director.normalize_blueprint(worker_db.run(
            dbx.latest_creative_blueprint, project["chat_session_id"]))
        ctx.plan_loaded = bool(ctx.edit_plan)
    except Exception:
        pass
    # The caller is a model, and a tools/call result carries image content.
    # So a look tool hands over the FRAMES, not our vision model's paragraph
    # about them — cheaper for us and first-hand for whoever is editing.
    ctx.sight_out = True
    # Same resolution the agent loop does: the plan decides the vision provider
    # (Frontier looks at footage with the frontier model) and what an
    # out-of-credits message may honestly promise.
    try:
        subscribed, plan, trialing = worker_db.run(dbx.user_billing,
                                                   job["user_id"])
    except Exception:
        subscribed, plan, trialing = False, "free", False
    ctx.subscribed, ctx.plan, ctx.trialing = subscribed, plan, trialing
    ctx.llm_client, ctx.agent_model = llm.agent_client_for(subscribed, plan)
    return _Session(ctx, workdir, sha)


def _session(worker_db, job, project):
    """The project's live ToolContext, rebuilt when the footage under it
    changed (a replaced upload) — an EDL written against a stale index is the
    failure that leaves every later write rejected."""
    pid = project["id"]
    index, sha, has_original = _index_for(worker_db, pid)
    if has_original and index is None:
        raise RuntimeError(
            "This project's video hasn't finished analyzing yet. Call "
            "index_status and wait for it to reach 'done' — the transcript, "
            "shots and silences the editing tools read do not exist until "
            "then.")
    now = time.time()
    with _sessions_lock:
        s = _sessions.get(pid)
        if s is not None and s.sha != sha:
            # Footage replaced: drop the old context (and its downloads).
            if s.lock.acquire(blocking=False):
                try:
                    _sessions.pop(pid, None)
                    shutil.rmtree(s.workdir, ignore_errors=True)
                finally:
                    s.lock.release()
                s = None
            else:
                raise RuntimeError("Another tool call on this project is "
                                   "still running — retry in a moment.")
        if s is None:
            s = _new_context(worker_db, job, project, index, sha)
            _sessions[pid] = s
        s.used = now
        _drop_dead_sessions(now, keep=pid)
    return s


def run_mcp_job(worker_db, job):
    """One MCP tool call. Returns {"text": ..., ...} — stored on the job row
    and handed back to the model verbatim by the backend.

    The one non-text answer is watch_video's: it adds a "video" field naming
    an object in storage, which the backend presigns and (when it fits) embeds
    as an MCP resource block. The BYTES never come through here — a base64
    video on the job row would be a permanent copy in Postgres of a file that
    is already in the bucket."""
    payload = job.get("payload") or {}
    tool = payload.get("tool") or ""
    args = payload.get("args") or {}

    if tool == CATALOG_TOOL:
        return catalog()

    # Defense in depth for stale clients and manually-created mcp_tool rows.
    # The backend also refuses this before queueing, but the worker is the
    # authority that would otherwise enqueue the agent turns.
    if tool in MCP_DENIED_TOOLS:
        return {"text": MCP_DENIED_MESSAGE, "is_error": True}

    project = worker_db.run(dbx.get_project, job["project_id"])
    if not project:
        raise RuntimeError("project not found")

    s = _session(worker_db, job, project)
    with s.lock:
        ctx = s.ctx
        # The context outlives the job that built it: re-point it at THIS
        # call's job row and this lane's connection, or previews it enqueues
        # would be attributed to a job that finished minutes ago.
        ctx.db = worker_db
        ctx.job = job
        s.used = time.time()

        def _recorder(purpose, request, response, usage):
            cached_in = llm.cached_input_tokens(usage)
            reasoning = llm.reasoning_tokens(usage)
            audio_in, audio_out = llm.audio_token_counts(usage)
            model = (request or {}).get("model")
            if usage:
                ctx.add_usage(model,
                              getattr(usage, "prompt_tokens", 0) or 0,
                              getattr(usage, "completion_tokens", 0) or 0,
                              cached_in, reasoning, audio_in, audio_out)
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
                          purpose, model, request, response,
                          getattr(usage, "prompt_tokens", None) if usage else None,
                          getattr(usage, "completion_tokens", None) if usage else None)

        llm.set_recorder(_recorder)
        llm.set_turn_plan(ctx.plan if ctx.subscribed else "")
        try:
            if tool == STATE_TOOL:
                return {"text": agent_loop.state_block(
                            ctx, worker_db, denied_tools=MCP_DENIED_TOOLS),
                        "edl_version": ctx.latest_edl()["version"]}

            if tool == MEDIA_TOOL:
                # Reads and (rarely) transcodes; never touches the EDL, so it
                # is not an edit and does not belong in the activity feed.
                # The inline budget is the BACKEND's — it is the one that has
                # to carry the base64 through a JSON-RPC reply.
                return mcp_media.prepare(
                    ctx, args, int(args.get("_inline_max_bytes")
                                   or 12 * 1048576))

            if tool not in agent_tools.TOOLS:
                return {"text": f"Unknown tool '{tool}'.", "is_error": True}
            if agent_tools._tool_disabled(tool):
                # Should be unreachable — a disabled tool is not in the
                # catalog — but a stale client could still call one.
                return {"text": f"{tool} is not available on this deployment.",
                        "is_error": True}

            before = ctx.latest_edl()["version"]
            try:
                text = agent_tools.execute(ctx, tool, args)
            except agent_tools.AskUser as e:
                # ask_user exists to suspend the in-house loop. Over MCP the
                # model IS talking to the user already, so the honest answer
                # is to hand the question back rather than pretend to wait.
                text = (f"ASK THE USER YOURSELF — nothing was changed. "
                        f"Question: {e.question}")
            after = ctx.latest_edl()["version"]

            agent_loop._activity(worker_db, project["chat_session_id"],
                                 tool, args, text, source="mcp",
                                 edl_version=after,
                                 creative_blueprint=(ctx.edit_plan if tool in {
                                     "set_edit_plan",
                                     "complete_edit_plan_steps"} else None))
            out = {"text": text, "edl_version": after,
                   "edl_changed": after != before}
            imgs = _drain_images(ctx)
            if imgs:
                out["images"] = imgs
            if ctx.last_preview:
                out["preview"] = {
                    "edl_version": ctx.last_preview.get("edl_version"),
                    "duration_s": ctx.last_preview.get("duration_s"),
                    # The render job's result names this render_asset_id
                    # (renderer.run_render_job); "asset_id" never existed,
                    # so this field was silently null since round 63.
                    "asset_id": ctx.last_preview.get("render_asset_id"),
                }
            return out
        finally:
            llm.set_recorder(None)
            llm.clear_turn_plan()
