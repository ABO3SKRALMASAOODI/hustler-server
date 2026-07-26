"""Remote MCP server (round 49) — Valmera's editor, driven by an outside model.

WHAT THIS IS. A Model Context Protocol endpoint at POST /mcp that hands the
*complete* editor tool registry to whatever model the caller is running —
Claude Opus or Fable inside the user's own Claude Code session, paid for by
their Anthropic subscription, not by our credits. The model does the thinking;
Valmera does the editing. It is the same trade the `mcp` plan was always
written around ("brings its own model").

THE ONE INVARIANT: NO SECOND EDITOR. The tools are not re-declared here. The
worker publishes its live registry (worker/mcp_exec.catalog) into mcp_catalog
on boot and this file serves it verbatim, so every tool the in-house agent can
call, the outside model can call, with the same name, the same JSON schema and
the same description — including the honest-off gating that hides a tool whose
backing service this deployment has no key for. Execution runs in the worker
too, in the same ToolContext the agent uses. There is nothing to keep in sync
because there is no copy.

WHAT IS DECLARED HERE: only the things the studio's UI normally does and a
headless model cannot — pick a project, upload a file, watch the render,
download the result. Those are session tools; the 80 editing tools are not.

HOW A CALL FLOWS.
    Claude Code --HTTP JSON-RPC--> this endpoint
                --video_jobs row (type 'mcp_tool')--> worker MCP lane
                --agent_tools.execute(ctx, name, args)--> EDL / render / R2
    and the result string comes back out the same way.
The backend waits on the row for MCP_SYNC_WAIT_S; anything slower (a render, a
frame-by-frame erase) returns a job id and the model calls wait_for_job. It
never lies about a job that is still running, and it never holds a gunicorn
worker for minutes — this deploys with 3 SYNC workers, so a long block here is
a third of the whole API. If MCP ever gets real traffic, move gunicorn to
`--worker-class gthread --threads 8` BEFORE raising MCP_SYNC_WAIT_S.

VISIBILITY. There is no UI, no marketing and no way in without a token, tokens
can only be minted by the admin account, and every request re-checks the
holder's email against MCP_ALLOWED_EMAILS (default: the admin address alone).
Revoking is a token row or one env var, not a deploy.
"""

import hashlib
import json
import os
import secrets
import time

import psycopg2
from flask import Blueprint, request, jsonify, current_app

import storage
from routes.admin import ADMIN_EMAIL
from routes.auth import token_required
from routes.video import (complete_upload_core, vdb, _enqueue,
                          _project_for_user, _active_original, _index_row,
                          _latest_edl, _running_jobs_count,
                          MAX_CONCURRENT_JOBS_PER_USER)

mcp_bp = Blueprint("mcp", __name__)

# Who may hold a token at all. The token itself is the credential; this is the
# second lock, so pulling access is an env var away and does not need a token
# hunt. Default: nobody but the founder.
ALLOWED_EMAILS = {e.strip().lower()
                  for e in os.getenv("MCP_ALLOWED_EMAILS",
                                     ADMIN_EMAIL).split(",") if e.strip()}

# Longest a tool call may block the HTTP request. See the gunicorn note above.
SYNC_WAIT_S = float(os.getenv("MCP_SYNC_WAIT_S", "25"))
POLL_S = 0.2

TOKEN_PREFIX = "vlm_mcp_"

# Protocol versions we can speak. The client's is echoed back when we know it,
# otherwise it gets ours — a version mismatch is a handshake failure, not a
# silent half-working session.
PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}
DEFAULT_PROTOCOL = "2025-06-18"

SERVER_INFO = {"name": "valmera", "title": "Valmera Video Editor",
               "version": "0.1.0"}


# ------------------------------------------------------------------ #
#  Auth                                                                #
# ------------------------------------------------------------------ #

def _sha(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer():
    h = request.headers.get("Authorization") or ""
    return h[7:].strip() if h.startswith("Bearer ") else ""


def _authenticate():
    """(token_row, error_message). The row carries user_id, email and the
    active project — one query, because it runs on every single tool call."""
    raw = _bearer()
    if not raw:
        return None, "missing Authorization: Bearer <token> header"
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT t.id, t.user_id, t.active_project_id,
                              t.revoked_at, u.email
                       FROM mcp_tokens t JOIN users u ON u.id = t.user_id
                       WHERE t.token_sha256 = %s""", (_sha(raw),))
        row = cur.fetchone()
        if not row or row["revoked_at"]:
            return None, "unknown or revoked token"
        if (row["email"] or "").lower() not in ALLOWED_EMAILS:
            # The account lost access after the token was minted.
            return None, "this account is not enabled for MCP access"
        cur.execute("""UPDATE mcp_tokens
                       SET last_used_at = NOW(), calls = calls + 1
                       WHERE id = %s""", (row["id"],))
    return row, None


def _set_active_project(token_id, project_id):
    with vdb() as conn:
        conn.cursor().execute(
            "UPDATE mcp_tokens SET active_project_id = %s WHERE id = %s",
            (project_id, token_id))


# ------------------------------------------------------------------ #
#  The tool catalog the worker published                               #
# ------------------------------------------------------------------ #

_catalog_cache = {"at": 0.0, "json": None}
CATALOG_TTL_S = 60


def _catalog():
    """The live registry, or None when the worker has never published one
    (migration 008 not applied, or the worker has not restarted since).

    Cached briefly because every tools/call validates the name against it, and
    the row only changes when the worker boots."""
    now = time.time()
    if _catalog_cache["json"] and now - _catalog_cache["at"] < CATALOG_TTL_S:
        return _catalog_cache["json"]
    with vdb() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT json FROM mcp_catalog WHERE id = 1")
            row = cur.fetchone()
        except psycopg2.Error:
            return None          # table absent: migration 008 not applied
    if row:
        _catalog_cache.update(at=now, json=row["json"])
    return row["json"] if row else None


CATALOG_MISSING = (
    "The worker has not published its tool catalog yet, so the editing tools "
    "cannot be listed. It publishes on boot — apply migration 008_mcp.sql and "
    "restart the worker service.")


def _editor_tools(catalog):
    """OpenAI function specs -> MCP tool specs. A rename of two keys; the
    schema itself is passed through untouched, which is the point."""
    out = []
    for t in (catalog or {}).get("tools", []):
        fn = t.get("function") or {}
        out.append({"name": fn.get("name"),
                    "description": fn.get("description"),
                    "inputSchema": fn.get("parameters")
                    or {"type": "object", "properties": {}}})
    return out


# ------------------------------------------------------------------ #
#  Session tools — what the studio UI does, for a headless caller      #
# ------------------------------------------------------------------ #

_NO_ARGS = {"type": "object", "properties": {}}

SESSION_TOOLS = [
    {"name": "list_projects",
     "description": "List this account's video projects, newest first, with "
                    "whether each has a video and whether it finished "
                    "analyzing. Start here.",
     "inputSchema": _NO_ARGS},
    {"name": "open_project",
     "description": "Make a project the active one — every editing tool acts "
                    "on it until you open another. Returns the full project "
                    "state: the video, its transcript and shots, the current "
                    "EDL and what is available to place. Call this before any "
                    "editing tool.",
     "inputSchema": {"type": "object", "properties": {
         "project_id": {"type": "integer"}}, "required": ["project_id"]}},
    {"name": "create_project",
     "description": "Create an empty project and make it active. Upload a "
                    "video into it with upload_start, or build a canvas "
                    "program from generated/uploaded assets.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}}}},
    {"name": "project_state",
     "description": "Re-read the active project's state (video, transcript, "
                    "shots, current EDL, assets). Cheap — call it whenever "
                    "you are unsure what the edit currently looks like.",
     "inputSchema": _NO_ARGS},
    {"name": "upload_start",
     "description": "Begin uploading a LOCAL file into the active project. "
                    "Returns presigned URL(s) you upload the bytes to "
                    "yourself (curl), then call upload_finish. kind: "
                    "'original' the main video, 'clip' b-roll, 'music' audio, "
                    "'image' a still.",
     "inputSchema": {"type": "object", "properties": {
         "filename": {"type": "string",
                      "description": "Name with extension, e.g. talk.mp4"},
         "size_bytes": {"type": "integer",
                        "description": "Exact size of the local file"},
         "kind": {"type": "string",
                  "enum": ["original", "clip", "music", "image"]}},
         "required": ["filename", "size_bytes"]}},
    {"name": "upload_finish",
     "description": "Finish an upload once every byte is in storage. For a "
                    "main video this starts the analysis (transcript, shots, "
                    "silences) — poll index_status until it is done.",
     "inputSchema": {"type": "object", "properties": {
         "storage_key": {"type": "string"},
         "filename": {"type": "string"},
         "kind": {"type": "string",
                  "enum": ["original", "clip", "music", "image"]},
         "upload_id": {"type": "string",
                       "description": "Multipart uploads only"},
         "parts": {"type": "array",
                   "description": "Multipart uploads only: "
                                  "[{part_number, etag}] in order",
                   "items": {"type": "object", "properties": {
                       "part_number": {"type": "integer"},
                       "etag": {"type": "string"}}}}},
         "required": ["storage_key"]}},
    {"name": "index_status",
     "description": "Progress of the active project's video analysis. The "
                    "editing tools cannot read a transcript, shots or "
                    "silences until this reaches 'done'.",
     "inputSchema": _NO_ARGS},
    {"name": "export_final",
     "description": "Render the FINAL export of an EDL version — full "
                    "resolution, from the original file. This is the user's "
                    "deliverable, so only call it when they asked for it. "
                    "Returns a job id; poll with wait_for_job, then "
                    "download_url.",
     "inputSchema": {"type": "object", "properties": {
         "edl_version": {"type": "integer",
                         "description": "Defaults to the latest version"}}}},
    {"name": "wait_for_job",
     "description": "Wait for a background job (a render, or a tool call that "
                    "outran its reply) and return its result. Safe to call "
                    "repeatedly — each call waits a bounded time and tells "
                    "you whether it is still running.",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "integer"}}, "required": ["job_id"]}},
    {"name": "download_url",
     "description": "A temporary URL for watching or downloading a render of "
                    "the active project. kind 'preview' (fast, 540p) or "
                    "'final' (the export).",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["preview", "final"]},
         "edl_version": {"type": "integer"}}}},
]

SESSION_TOOL_NAMES = {t["name"] for t in SESSION_TOOLS}

WORKFLOW = """
HOW TO DRIVE VALMERA OVER MCP

You are editing real video for the person you are talking to. The tools below
are the same tools Valmera's own editing agent runs — same names, same
arguments, same effects — and everything above this line is that agent's
operating doctrine. Follow it.

Two things are different from a normal tool session, and both matter:

1. ONE ACTIVE PROJECT. Editing tools do not take a project id. Call
   list_projects, then open_project(id) — that returns the whole project
   state (footage, transcript, shots, current EDL). Do that before you edit
   anything, and again with project_state() whenever you are unsure.

2. YOU ARE THE ONE TALKING TO THE USER. The ask_user tool exists for the
   in-house agent to suspend a turn; here, just ask them yourself. And nothing
   auto-renders when you stop: call render_preview yourself before you tell
   the user what you did, so what you claim is something you have actually
   seen. Then hand them download_url so they can watch it.

Nothing is charged to their Valmera credits for the thinking you do — but a
render, a look at the footage and a generated image are real work on real
hardware. Do not spend them idly.

A slow tool (a render, erasing burned-in text, generating video) may reply
"STILL RUNNING — job N". That is not a failure and not a timeout: the work is
in flight. Call wait_for_job(N) until it answers.

Uploading a local file: upload_start tells you exactly what to run. Upload the
bytes yourself with curl, then call upload_finish with what it gives you. A
main video then has to be ANALYZED before it can be edited (transcript, shots,
silences) — index_status reports that, and it takes minutes on a long video.
""".strip()


# "full" (default) ships the in-house agent's entire operating doctrine — 44 KB
# of hard-won editing judgment (what a transition is for, when captions lie,
# why you look before you claim). That is the point of the surface: the outside
# model should edit like Valmera edits, not merely reach its tools.
#
# "brief" drops the doctrine and keeps the capability list + the MCP workflow,
# which is ~10x smaller. It exists to answer one question honestly — how much
# of the edit quality is the prompt and how much is the model — and that
# question is worth an env var while this is a private experiment.
INSTRUCTIONS_MODE = os.getenv("MCP_INSTRUCTIONS", "full").strip().lower()


def _instructions(catalog):
    if not catalog:
        return CATALOG_MISSING
    parts = ([] if INSTRUCTIONS_MODE == "brief"
             else [catalog.get("system_prompt", "")])
    parts += [catalog.get("capabilities", ""), WORKFLOW]
    return "\n\n".join(p for p in parts if p)


# ------------------------------------------------------------------ #
#  Jobs                                                                #
# ------------------------------------------------------------------ #

def _job_row(job_id, user_id):
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id, type, state, progress, error, result,
                              payload, project_id
                       FROM video_jobs WHERE id = %s AND user_id = %s""",
                    (job_id, int(user_id)))
        return cur.fetchone()


def _wait(job_id, user_id, seconds=None):
    """Poll the job row until it settles or the budget runs out. A fresh
    connection per read: the alternative holds one open across the whole wait,
    which on 3 sync gunicorn workers is a connection idle-in-transaction for
    as long as a render takes."""
    deadline = time.time() + (SYNC_WAIT_S if seconds is None else seconds)
    row = _job_row(job_id, user_id)
    while row and row["state"] in ("queued", "running") \
            and time.time() < deadline:
        time.sleep(POLL_S)
        row = _job_row(job_id, user_id)
    return row


def _still_running(row, what):
    pct = row.get("progress") or 0
    return (f"STILL RUNNING — {what} is job {row['id']} "
            f"({row['state']}, {pct}%). Nothing has failed; call "
            f"wait_for_job(job_id={row['id']}) to pick the result up.")


def _run_tool_job(tok, name, args):
    """Enqueue one editor tool call for the worker and wait for its answer."""
    project_id = tok["active_project_id"]
    if not project_id:
        return ("No project is open. Call list_projects and then "
                "open_project(project_id) — every editing tool acts on the "
                "active project.")
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, tok["user_id"]):
            return ("The active project no longer exists. Call "
                    "list_projects and open another.")
        # Two editors on one timeline write conflicting EDL versions and each
        # reads state the other is halfway through changing. The studio's own
        # agent owns the project while its turn runs.
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND state IN ('queued','running')""", (project_id,))
        if cur.fetchone():
            return ("Valmera's own agent is mid-turn on this project — wait "
                    "for it to finish before editing, or the two of you will "
                    "overwrite each other's edit.")
        job_id = _enqueue(cur, project_id, tok["user_id"], "mcp_tool",
                          {"tool": name, "args": args})
    # The control calls are plumbing the model never asked for by name, so a
    # failure must not be reported as "__state__ failed".
    label = {"__state__": "Reading the project state"}.get(name, name)
    row = _wait(job_id, tok["user_id"])
    if not row:
        return f"Tool call {name} vanished from the queue — try it again."
    if row["state"] == "failed":
        return (f"{label} failed: {row.get('error') or 'unknown error'}. "
                "Nothing was changed by it.")
    if row["state"] in ("queued", "running"):
        return _still_running(row, label)
    result = row.get("result") or {}
    return result.get("text") or json.dumps(result)


# ------------------------------------------------------------------ #
#  Session tool implementations                                        #
# ------------------------------------------------------------------ #

def _t_list_projects(tok, args):
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.title, p.created_at,
                   (SELECT a.sha256 FROM assets a
                     WHERE a.project_id = p.id AND a.kind = 'original'
                     ORDER BY a.id DESC LIMIT 1) AS sha
            FROM projects p WHERE p.user_id = %s
            ORDER BY p.id DESC LIMIT 50""", (int(tok["user_id"]),))
        rows = cur.fetchall()
        lines = []
        for r in rows:
            if r["sha"]:
                cur.execute("SELECT 1 FROM indexes WHERE video_sha256 = %s",
                            (r["sha"],))
                what = "indexed" if cur.fetchone() else "video not analyzed yet"
            else:
                what = "no video (canvas project)"
            active = " [ACTIVE]" if r["id"] == tok["active_project_id"] else ""
            lines.append(f"  [{r['id']}] {r['title']} — {what}"
                         f" — created {r['created_at']:%Y-%m-%d}{active}")
    if not lines:
        return ("No projects yet. create_project(title) makes one, then "
                "upload_start puts a video in it.")
    return ("Projects (open one with open_project(project_id)):\n"
            + "\n".join(lines))


def _t_open_project(tok, args):
    try:
        project_id = int(args.get("project_id"))
    except (TypeError, ValueError):
        return "project_id must be an integer — see list_projects."
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, tok["user_id"])
        if not p:
            return f"Project {project_id} does not exist on this account."
    _set_active_project(tok["id"], project_id)
    tok["active_project_id"] = project_id
    state = _run_tool_job(tok, "__state__", {})
    return f"Opened project {project_id} — \"{p['title']}\".\n\n{state}"


def _t_create_project(tok, args):
    title = (args.get("title") or "").strip() or "Untitled project"
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO chat_sessions (user_id, title)
                       VALUES (%s, %s) RETURNING id""",
                    (int(tok["user_id"]), title))
        session_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO projects (user_id, title, chat_session_id)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (int(tok["user_id"]), title, session_id))
        pid = cur.fetchone()["id"]
    _set_active_project(tok["id"], pid)
    tok["active_project_id"] = pid
    return (f"Created project {pid} (\"{title}\") and made it active. "
            "Add a video with upload_start, or start placing generated / "
            "uploaded assets for a canvas program.")


def _t_project_state(tok, args):
    return _run_tool_job(tok, "__state__", {})


def _t_upload_start(tok, args):
    project_id = tok["active_project_id"]
    if not project_id:
        return "No project is open — call create_project or open_project."
    if not storage.is_configured():
        return "Storage is not configured on this deployment."
    filename = args.get("filename") or ""
    kind = args.get("kind") or "original"
    if kind not in ("original", "music", "image", "clip"):
        return "kind must be one of: original, clip, music, image."
    try:
        nbytes = int(args.get("size_bytes"))
    except (TypeError, ValueError):
        return ("size_bytes must be the exact byte size of the local file "
                "(stat -f%z on macOS, stat -c%s on Linux).")
    try:
        ext, content_type = storage.validate_upload(filename, nbytes, kind)
    except ValueError as e:
        return str(e)
    with vdb() as conn:
        if not _project_for_user(conn.cursor(), project_id, tok["user_id"]):
            return "The active project no longer exists."
    key = storage.new_original_key(project_id, ext, kind)
    try:
        out = storage.presign_upload(key, nbytes, content_type)
    except Exception as e:
        return f"Could not prepare the upload: {e}"

    if out.get("mode") == "single":
        return (
            f"Upload the file with this exact command, then call "
            f"upload_finish(storage_key=\"{key}\", filename=\"{filename}\", "
            f"kind=\"{kind}\").\n\n"
            f"curl -sS -f -X PUT -H 'Content-Type: {content_type}' "
            f"--upload-file '<LOCAL PATH>' '{out['url']}'\n\n"
            "The URL is valid for 15 minutes and carries the whole upload — "
            "it is long, do not edit or wrap it.\n\n"
            + json.dumps({"mode": "single", "storage_key": key,
                          "content_type": content_type, "url": out["url"]}))

    # Multipart. Every part but the last must be exactly part_size bytes, and
    # each PUT returns an ETag header that upload_finish needs back in order.
    parts = out.get("part_urls") or []
    return (
        f"This file needs a MULTIPART upload: {len(parts)} parts of "
        f"{out.get('part_size')} bytes (the last part is whatever remains). "
        f"Run `python3 scripts/valmera_upload.py <LOCAL PATH>` if you have "
        f"the repo — it does all of this, including the retries — or PUT each "
        f"part yourself with `curl -D-` and keep the ETag header from every "
        f"response.\n\n"
        f"Then call upload_finish(storage_key=\"{key}\", "
        f"filename=\"{filename}\", kind=\"{kind}\", "
        f"upload_id=\"{out.get('upload_id')}\", "
        f"parts=[{{\"part_number\": 1, \"etag\": \"...\"}}, ...]).\n\n"
        + json.dumps({"mode": "multipart", "storage_key": key,
                      "upload_id": out.get("upload_id"),
                      "part_size": out.get("part_size"), "parts": parts}))


def _t_upload_finish(tok, args):
    project_id = tok["active_project_id"]
    if not project_id:
        return "No project is open."
    payload, status = complete_upload_core(
        tok["user_id"], project_id,
        {"storage_key": args.get("storage_key"),
         "kind": args.get("kind") or "original",
         "filename": args.get("filename") or "",
         "upload_id": args.get("upload_id"),
         "parts": args.get("parts") or []})
    if status >= 400:
        return f"Upload could not be finished: {payload.get('error')}"
    if payload.get("index_job_id"):
        return (f"Uploaded (asset {payload['asset_id']}). Analysis started as "
                f"job {payload['index_job_id']} — the transcript, shots and "
                "silences do not exist until it finishes. Poll index_status; "
                "on a long video this takes several minutes.")
    return (f"Uploaded (asset {payload['asset_id']}, kind "
            f"{payload['kind']}). It is now in list_assets and can be placed "
            "with insert_media / add_music / add_overlay.")


def _t_index_status(tok, args):
    project_id = tok["active_project_id"]
    if not project_id:
        return "No project is open."
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, tok["user_id"]):
            return "The active project no longer exists."
        original = _active_original(cur, project_id)
        if not original:
            return ("No main video in this project — it is a canvas program. "
                    "Nothing to analyze.")
        if original["sha256"] and _index_row(cur, original["sha256"]):
            return "done — the video is analyzed and ready to edit."
        cur.execute("""SELECT id, state, progress, error FROM video_jobs
                       WHERE project_id = %s AND type = 'index'
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        job = cur.fetchone()
    if not job:
        return ("The video is uploaded but no analysis job exists — call "
                "upload_finish, or re-open the project in the studio.")
    if job["state"] == "failed":
        return (f"Analysis FAILED: {job['error']}. The video cannot be "
                "edited until it is re-uploaded and analyzed.")
    return (f"{job['state']} — {job['progress']}% (job {job['id']}). "
            "Transcript, shots and silences are unavailable until this "
            "reaches done.")


def _t_export_final(tok, args):
    project_id = tok["active_project_id"]
    if not project_id:
        return "No project is open."
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, tok["user_id"]):
            return "The active project no longer exists."
        version = args.get("edl_version")
        if version is None:
            edl = _latest_edl(cur, project_id)
            if not edl:
                return "This project has no edit yet — nothing to export."
            version = edl["version"]
        else:
            try:
                version = int(version)
            except (TypeError, ValueError):
                return "edl_version must be an integer."
            cur.execute("""SELECT 1 FROM edls
                           WHERE project_id = %s AND version = %s""",
                        (project_id, version))
            if not cur.fetchone():
                return f"EDL v{version} does not exist in this project."
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'final'
                         AND state IN ('queued','running')""", (project_id,))
        running = cur.fetchone()
        if running:
            return (f"A final export is already running (job {running['id']})."
                    f" Call wait_for_job(job_id={running['id']}).")
        if _running_jobs_count(cur, tok["user_id"]) >= MAX_CONCURRENT_JOBS_PER_USER:
            return ("Too much is already running on this account — wait for "
                    "it to finish and try again.")
        job_id = _enqueue(cur, project_id, tok["user_id"], "final",
                          {"edl_version": version})
    row = _wait(job_id, tok["user_id"])
    if row and row["state"] == "done":
        return (f"Final export of v{version} is rendered (job {job_id}). "
                "Call download_url(kind=\"final\") for the link.")
    if row and row["state"] == "failed":
        return f"The final export failed: {row.get('error')}"
    return _still_running(row or {"id": job_id, "state": "queued",
                                  "progress": 0}, f"the final export of v{version}")


def _t_wait_for_job(tok, args):
    try:
        job_id = int(args.get("job_id"))
    except (TypeError, ValueError):
        return "job_id must be an integer."
    row = _wait(job_id, tok["user_id"])
    if not row:
        return f"No job {job_id} on this account."
    if row["state"] == "failed":
        return f"Job {job_id} ({row['type']}) FAILED: {row.get('error')}"
    if row["state"] in ("queued", "running"):
        return _still_running(row, f"job {job_id} ({row['type']})")
    result = row.get("result") or {}
    if row["type"] == "mcp_tool":
        return result.get("text") or json.dumps(result)
    if row["type"] == "final":
        return ("The final export is rendered. Call "
                "download_url(kind=\"final\") for the link.")
    if row["type"] == "index":
        return ("Analysis finished — the transcript, shots and silences are "
                "ready. project_state() shows them.")
    return f"Job {job_id} ({row['type']}) finished: {json.dumps(result)[:800]}"


def _t_download_url(tok, args):
    project_id = tok["active_project_id"]
    if not project_id:
        return "No project is open."
    kind = args.get("kind") or "preview"
    if kind not in ("preview", "final"):
        return "kind must be 'preview' or 'final'."
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, tok["user_id"]):
            return "The active project no longer exists."
        # Renders are assets of kind 'render'; the variant and the EDL version
        # they were made from live in meta.
        sql = """SELECT storage_key, meta FROM assets
                 WHERE project_id = %s AND kind = 'render'
                   AND meta->>'variant' = %s"""
        params = [project_id, kind]
        if args.get("edl_version") is not None:
            sql += " AND (meta->>'edl_version')::int = %s"
            params.append(int(args["edl_version"]))
        sql += " ORDER BY id DESC LIMIT 1"
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return (f"No {kind} has been rendered yet"
                + (" — call render_preview." if kind == "preview"
                   else " — call export_final."))
    try:
        url = storage.presign_get(row["storage_key"])
    except Exception as e:
        return f"Could not mint a download link: {e}"
    ver = (row.get("meta") or {}).get("edl_version")
    return (f"{kind} of EDL v{ver} (link valid a few hours, hand it to the "
            f"user as-is):\n{url}")


SESSION_IMPL = {
    "list_projects": _t_list_projects,
    "open_project": _t_open_project,
    "create_project": _t_create_project,
    "project_state": _t_project_state,
    "upload_start": _t_upload_start,
    "upload_finish": _t_upload_finish,
    "index_status": _t_index_status,
    "export_final": _t_export_final,
    "wait_for_job": _t_wait_for_job,
    "download_url": _t_download_url,
}


# ------------------------------------------------------------------ #
#  JSON-RPC                                                            #
# ------------------------------------------------------------------ #

def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _text(s, is_error=False):
    return {"content": [{"type": "text", "text": s}], "isError": is_error}


def _handle(tok, msg):
    """One JSON-RPC message. Returns a response dict, or None for a
    notification (which by protocol gets no reply)."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        catalog = _catalog()
        want = params.get("protocolVersion")
        return _result(req_id, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS
                               else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": _instructions(catalog),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(req_id, {})
    if method in ("prompts/list", "resources/list", "resources/templates/list"):
        key = method.split("/")[0]
        return _result(req_id, {key: []})

    if method == "tools/list":
        catalog = _catalog()
        if not catalog:
            return _error(req_id, -32603, CATALOG_MISSING)
        return _result(req_id, {"tools": SESSION_TOOLS
                                + _editor_tools(catalog)})

    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _result(req_id, _text("arguments must be an object.", True))
        try:
            if name in SESSION_IMPL:
                return _result(req_id, _text(SESSION_IMPL[name](tok, args)))
            catalog = _catalog()
            known = {t["name"] for t in _editor_tools(catalog)}
            if name not in known:
                # Honest and specific: an unknown name here usually means the
                # tool exists but this deployment has it switched off (no key
                # for its backing service), and "unknown tool" alone would
                # send the model looking for a typo.
                return _result(req_id, _text(
                    f"There is no tool called '{name}' on this deployment. "
                    "Call tools/list for what is actually available — a tool "
                    "whose backing service is unconfigured is hidden rather "
                    "than failing at call time.", True))
            return _result(req_id, _text(_run_tool_job(tok, name, args)))
        except Exception as e:
            current_app.logger.exception("mcp tool %s failed", name)
            return _result(req_id, _text(f"{name} errored: {e}", True))

    return _error(req_id, -32601, f"method not found: {method}")


@mcp_bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    tok, err = _authenticate()
    if err:
        # 401 with an honest reason. MCP clients surface this to the user, who
        # is the only person who can fix it.
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32001, "message": err}}), 401
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(_error(None, -32700, "parse error")), 400

    batch = isinstance(body, list)
    msgs = body if batch else [body]
    out = [r for r in (_handle(tok, m) for m in msgs) if r is not None]
    if not out:
        return "", 202
    return jsonify(out if batch else out[0]), 200


@mcp_bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_stream():
    """This server keeps no stream and no session state — the active project
    lives on the token, so a reconnect resumes exactly where it left off."""
    if request.method == "DELETE":
        return "", 204
    return jsonify({"error": "This MCP server is request/response only; "
                             "there is no SSE stream to open."}), 405


# ------------------------------------------------------------------ #
#  Token management — admin only, no UI                                #
# ------------------------------------------------------------------ #

def _admin_email(user_id):
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("SELECT email FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
    email = (row["email"] if row else "").lower()
    return email if email in ALLOWED_EMAILS else None


@mcp_bp.route("/mcp/tokens", methods=["POST"])
@token_required
def mint_token(user_id):
    if not _admin_email(user_id):
        return jsonify({"error": "Not enabled for this account"}), 403
    label = ((request.get_json(silent=True) or {}).get("label")
             or "claude-code").strip()[:80]
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO mcp_tokens (user_id, token_sha256, label)
                       VALUES (%s, %s, %s) RETURNING id, created_at""",
                    (int(user_id), _sha(raw), label))
        row = cur.fetchone()
    base = os.getenv("BACKEND_URL",
                     "https://entrepreneur-bot-backend.onrender.com")
    return jsonify({
        "id": row["id"], "label": label, "token": raw,
        "note": "Shown once — it is stored hashed and cannot be recovered.",
        "claude_code": (f"claude mcp add --transport http valmera "
                        f"{base}/mcp --header \"Authorization: Bearer {raw}\""),
    }), 201


@mcp_bp.route("/mcp/tokens", methods=["GET"])
@token_required
def list_tokens(user_id):
    if not _admin_email(user_id):
        return jsonify({"error": "Not enabled for this account"}), 403
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id, label, active_project_id, calls, created_at,
                              last_used_at, revoked_at
                       FROM mcp_tokens WHERE user_id = %s
                       ORDER BY id DESC""", (int(user_id),))
        rows = cur.fetchall()
    return jsonify({"tokens": [{
        "id": r["id"], "label": r["label"],
        "active_project_id": r["active_project_id"], "calls": r["calls"],
        "created_at": r["created_at"].isoformat(),
        "last_used_at": (r["last_used_at"].isoformat()
                         if r["last_used_at"] else None),
        "revoked_at": (r["revoked_at"].isoformat()
                       if r["revoked_at"] else None),
    } for r in rows]})


@mcp_bp.route("/mcp/tokens/<int:token_id>", methods=["DELETE"])
@token_required
def revoke_token(user_id, token_id):
    if not _admin_email(user_id):
        return jsonify({"error": "Not enabled for this account"}), 403
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE mcp_tokens SET revoked_at = NOW()
                       WHERE id = %s AND user_id = %s AND revoked_at IS NULL""",
                    (token_id, int(user_id)))
        n = cur.rowcount
    return jsonify({"revoked": bool(n)})
