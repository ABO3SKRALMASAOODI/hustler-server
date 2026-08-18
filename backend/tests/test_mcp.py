"""The MCP surface's protocol contract, end to end, with Postgres faked.

Two clients have to work and they authenticate completely differently:

  * Claude Code  — static bearer token in a header.
  * claude.ai    — no header field exists, so it MUST discover an
                   authorization server from the 401, register itself, run a
                   browser login and exchange a code. Break any one of those
                   links and the connector cannot be added at all, while
                   Claude Code keeps working and hides it.

So the OAuth dance is tested as one continuous flow, in the exact order
claude.ai performs it, plus the ways it should refuse.

    cd backend && python -m pytest tests -q
"""

import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, parse_qs

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("BACKEND_URL", "https://api.example.com")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

import routes.mcp as mcpmod  # noqa: E402
import routes.mcp_oauth as oauth  # noqa: E402

EMAIL = "thevalmera@gmail.com"
PASSWORD = "correct horse battery staple"
STATIC_TOKEN = "vlm_mcp_static"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"

CATALOG = {
    # Include the retired delegation tool to simulate a stale worker catalog;
    # the backend boundary must still hide and refuse it.
    "tools": [
        {"type": "function", "function": {
            "name": "get_transcript",
            "description": "Read the transcript.",
            "parameters": {"type": "object",
                           "properties": {"start": {"type": "number"}}}}},
        {"type": "function", "function": {
            "name": "edit_shorts",
            "description": "Delegate to child agents.",
            "parameters": {"type": "object", "properties": {
                "instruction": {"type": "string"}},
                "required": ["instruction"]}}},
        {"type": "function", "function": {
            "name": "make_shorts",
            "description": "Build shorts from a long podcast.",
            "parameters": {"type": "object", "properties": {
                "clips": {"type": "array", "items": {"type": "object"}},
                "style_note": {"type": "string"}}}}},
    ],
    "system_prompt": "DOCTRINE.",
    "capabilities": "CAPABILITIES — ...",
}

DB = {}


def _reset():
    DB.clear()
    # The static-token client starts with project 3 open, which is the normal
    # state for an editing call; the one test that needs no open project
    # clears it explicitly.
    DB.update(clients={}, grants={}, codes={}, tokens={}, seq=0,
              static_project=3, expired_codes=set(), enqueued=[],
              job_result=None, created_project=None,
              render_assets=[],
              project_rows={
                  3: {"id": 3, "title": "Long podcast", "kind": "shorts",
                      "parent_project_id": None,
                      "meta": {"shorts": {
                          "status": "ready",
                          "clips": [{"order": 0, "title": "Strong hook",
                                     "start": 12.0, "end": 38.0,
                                     "child_project_id": 9,
                                     "edl_version": 6,
                                     "story": {
                                         "setup": "A risky launch",
                                         "development": "Evidence was ignored",
                                         "payoff": "The team changed course"},
                                     "visual_direction": "Clean evidence-led design",
                                     "broll": [{"at": 20, "query": "failed product launch",
                                                "purpose": "show the consequence"}]}]}}},
                  9: {"id": 9, "title": "Strong hook", "kind": "short",
                      "parent_project_id": 3, "meta": {}},
              },
              shorts_job={"id": 8, "state": "done", "progress": 100,
                          "error": None, "result": {"clips": 1}},
              final_rows=[{"project_id": 9, "id": 10, "state": "done",
                           "progress": 100, "error": None}])


def _next_id():
    DB["seq"] += 1
    return DB["seq"]


class FakeCur:
    """Recognises the handful of statements these two modules issue. Anything
    unmatched returns no rows, which surfaces as a clean failure rather than a
    silent pass."""

    rowcount = 1

    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        if s.startswith("INSERT INTO mcp_oauth_clients"):
            DB["clients"][p[0]] = {"client_id": p[0], "client_name": p[1],
                                   "redirect_uris": json.loads(p[2])}
        elif "FROM mcp_oauth_clients" in s:
            c = DB["clients"].get(p[0])
            self.rows = [c] if c else []
        elif "FROM users WHERE LOWER(email)" in s:
            self.rows = ([{"id": 60, "email": EMAIL,
                           "password": generate_password_hash(PASSWORD),
                           "is_verified": 1}] if p[0] == EMAIL else [])
        elif s.startswith("INSERT INTO mcp_oauth_grants"):
            gid = _next_id()
            DB["grants"][gid] = {"id": gid, "user_id": p[0], "client_id": p[1],
                                 "revoked_at": None, "active_project_id": None}
            self.rows = [{"id": gid}]
        elif s.startswith("INSERT INTO mcp_oauth_codes"):
            DB["codes"][p[0]] = {"code_sha256": p[0], "grant_id": p[1],
                                 "client_id": p[2], "redirect_uri": p[3],
                                 "code_challenge": p[4], "resource": p[5],
                                 "used_at": None, "expires_at": p[0]}
        elif "FROM mcp_oauth_codes WHERE code_sha256" in s:
            row = DB["codes"].get(p[0])
            self.rows = [row] if row else []
        elif s.startswith("SELECT NOW() >"):
            self.rows = [{"expired": p[0] in DB["expired_codes"]}]
        elif s.startswith("UPDATE mcp_oauth_codes SET used_at"):
            DB["codes"][p[0]]["used_at"] = "now"
        elif s.startswith("INSERT INTO mcp_oauth_tokens"):
            for gid, kind, sha in ((p[0], "access", p[1]),
                                   (p[3], "refresh", p[4])):
                DB["tokens"][sha] = {"id": _next_id(), "grant_id": gid,
                                     "kind": kind, "revoked_at": None,
                                     "expired": False}
        elif "FROM mcp_oauth_tokens t JOIN mcp_oauth_grants g" in s:
            t = DB["tokens"].get(p[0])
            want = "refresh" if "kind = 'refresh'" in s else "access"
            if t and t["kind"] == want:
                g = DB["grants"][t["grant_id"]]
                self.rows = [{"id": t["id"], "token_id": t["id"],
                              "grant_id": g["id"], "user_id": g["user_id"],
                              "revoked_at": t["revoked_at"],
                              "expired": t["expired"],
                              "grant_revoked": g["revoked_at"],
                              "active_project_id": g["active_project_id"],
                              "email": EMAIL}]
        elif s.startswith("UPDATE mcp_oauth_tokens SET revoked_at") \
                and "id = %s" in s:
            for t in DB["tokens"].values():
                if t["id"] == p[0]:
                    t["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_tokens SET revoked_at"):
            for t in DB["tokens"].values():
                if t["grant_id"] == p[0]:
                    t["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_grants SET revoked_at"):
            DB["grants"][p[0]]["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_grants SET active_project_id"):
            DB["grants"][p[1]]["active_project_id"] = p[0]
        elif "FROM mcp_tokens t JOIN users" in s:
            want = hashlib.sha256(STATIC_TOKEN.encode()).hexdigest()
            self.rows = ([{"id": 1, "user_id": 60, "email": EMAIL,
                           "revoked_at": None,
                           "active_project_id": DB["static_project"]}]
                         if p[0] == want else [])
        elif s.startswith("UPDATE mcp_tokens SET active_project_id"):
            DB["static_project"] = p[0]
        elif "FROM mcp_catalog" in s:
            self.rows = [{"json": CATALOG}]
        elif s.startswith("INSERT INTO chat_sessions"):
            self.rows = [{"id": 70}]
        elif s.startswith("INSERT INTO projects"):
            DB["created_project"] = {"user_id": p[0], "title": p[1],
                                     "chat_session_id": p[2], "kind": p[3]}
            self.rows = [{"id": 71}]
        elif "FROM projects WHERE id = %s AND user_id = %s" in s:
            row = DB["project_rows"].get(p[0])
            self.rows = [row] if row else []
        elif "type = 'shorts_plan'" in s:
            self.rows = [DB["shorts_job"]] if DB.get("shorts_job") else []
        elif "project_id = ANY(%s) AND type = 'final'" in s:
            wanted = set(p[0])
            self.rows = [r for r in DB["final_rows"]
                         if r["project_id"] in wanted]
        elif "type = 'agent_turn'" in s:
            self.rows = []
        elif s.startswith("INSERT INTO video_jobs"):
            self.rows = [{"id": 5}]
        elif "FROM video_jobs WHERE id" in s:
            self.rows = [{"id": 5, "type": "mcp_tool", "state": "done",
                          "progress": 100, "error": None, "payload": {},
                          "project_id": 3,
                          "result": DB.get("job_result")
                          or {"text": "12 sentences."}}]
        elif "FROM assets" in s and "kind = 'render'" in s:
            rows = [row for row in DB["render_assets"]
                    if row["project_id"] == p[0]
                    and (row.get("meta") or {}).get("variant") == p[1]]
            if "audio_model_review' = 'false'" in s:
                rows = [row for row in rows if
                        (row.get("meta") or {}).get(
                            "audio_model_review") is False]
            if "meta->>'edl_version'" in s and len(p) > 2:
                rows = [row for row in rows if int(
                    (row.get("meta") or {}).get("edl_version")) == int(p[2])]
            self.rows = sorted(rows, key=lambda row: row["id"], reverse=True)[:1]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def cursor(self):
        return FakeCur()


@contextlib.contextmanager
def fake_vdb():
    yield FakeConn()


def _fake_enqueue(cur, pid, uid, jtype, payload):
    """Records what the backend asked the worker to run — the ONLY place the
    injected arguments (an inline budget the model never sees) are visible."""
    DB["enqueued"].append(payload)
    return 5


@pytest.fixture(autouse=True)
def stub_db(monkeypatch):
    _reset()
    monkeypatch.setattr(mcpmod, "vdb", fake_vdb)
    monkeypatch.setattr(oauth, "vdb", fake_vdb)
    monkeypatch.setattr(mcpmod, "_project_for_user",
                        lambda cur, pid, uid: {"id": pid, "title": "P"})
    monkeypatch.setattr(mcpmod, "_enqueue", _fake_enqueue)
    mcpmod._catalog_cache.update(at=0.0, json=None)


@pytest.fixture
def client():
    from app import create_app
    return create_app().test_client()


def rpc(client, method, token=None, params=None, accept=None, _id=1):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if accept:
        h["Accept"] = accept
    return client.post("/mcp", headers=h, data=json.dumps(
        {"jsonrpc": "2.0", "id": _id, "method": method,
         "params": params or {}}))


def text_of(resp):
    return resp.get_json()["result"]["content"][0]["text"]


# ── the handshake ────────────────────────────────────────────────────

def test_initialize_carries_the_editing_doctrine(client):
    b = rpc(client, "initialize", STATIC_TOKEN,
            {"protocolVersion": "2025-06-18"}).get_json()
    assert b["result"]["protocolVersion"] == "2025-06-18"
    assert "DOCTRINE." in b["result"]["instructions"]
    assert "EVERY EDITOR CALL IS EXPLICITLY PROJECT-SCOPED" in \
        b["result"]["instructions"]
    assert "Never say MCP can only send instructions" in \
        b["result"]["instructions"]
    assert "Valmera's in-house agent is not callable over MCP" in \
        b["result"]["instructions"]
    assert "A SHORT IS A MICRO-STORY" in b["result"]["instructions"]
    assert "make_shorts(project_id, clips=[...]" in \
        b["result"]["instructions"]
    assert "Final export is deliberately Studio-only" in \
        b["result"]["instructions"]
    assert "render_preview(complete=false)" in b["result"]["instructions"]
    assert "render_preview(complete=true) exactly once" in \
        b["result"]["instructions"]


def test_unknown_protocol_version_falls_back_to_ours(client):
    b = rpc(client, "initialize", STATIC_TOKEN,
            {"protocolVersion": "1999-01-01"}).get_json()
    assert b["result"]["protocolVersion"] == mcpmod.DEFAULT_PROTOCOL


def test_notification_gets_no_reply(client):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {STATIC_TOKEN}",
                                     "Content-Type": "application/json"},
                    data=json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}))
    assert r.status_code == 202 and not r.data


def test_tools_list_is_session_tools_plus_the_worker_registry(client):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "open_project" in names and "get_transcript" in names
    assert "export_final" not in names
    assert "edit_shorts" not in names
    assert "make_shorts" in names
    editor = [t for t in tools if t["name"] == "get_transcript"][0]
    # MCP adds only its transport-level immutable project scope; the worker's
    # arguments otherwise remain untouched.
    assert editor["inputSchema"]["properties"]["start"] == {"type": "number"}
    assert editor["inputSchema"]["properties"]["project_id"]["type"] == "integer"
    assert "project_id" in editor["inputSchema"]["required"]
    make_shorts = next(t for t in tools if t["name"] == "make_shorts")
    assert "clips" in make_shorts["inputSchema"]["required"]


def test_stale_child_agent_boot_call_is_refused_before_queueing(client):
    r = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "edit_shorts",
        "arguments": {"project_id": 3, "instruction": "add captions"},
    }).get_json()["result"]
    assert r["isError"] is True
    assert "reserved for an explicit locked-card Edit press" in \
        r["content"][0]["text"]
    assert "You are the editor" in r["content"][0]["text"]
    assert DB["enqueued"] == []


def test_podcast_shorts_are_first_class_session_tools(client):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    by_name = {t["name"]: t for t in tools}
    assert "shorts_status" in by_name
    assert "open_short" in by_name
    assert by_name["open_short"]["annotations"]["readOnlyHint"] is True
    assert by_name["create_project"]["inputSchema"]["properties"]["kind"] \
        ["enum"] == ["edit", "shorts"]
    for name in ("project_state", "upload_start", "upload_finish",
                 "index_status", "shorts_status", "download_url",
                 "watch_video"):
        assert "project_id" in by_name[name]["inputSchema"]["required"]


def test_upload_tools_advertise_the_shorts_reference_contract(client):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    by_name = {t["name"]: t for t in tools}
    for name in ("upload_start", "upload_finish"):
        props = by_name[name]["inputSchema"]["properties"]
        assert props["role"]["enum"] == ["shorts_reference"]
        assert "only with kind='clip'" in props["role"]["description"]
        assert props["duration_s"]["type"] == "number"
        assert props["duration_s"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("upload_plan", [
    {"mode": "single", "url": "https://storage.example/put"},
    {"mode": "multipart", "upload_id": "multi-7", "part_size": 64,
     "part_urls": [{"part_number": 1,
                     "url": "https://storage.example/part-1"}]},
])
def test_upload_start_preserves_reference_metadata_in_finish_instructions(
        client, monkeypatch, upload_plan):
    monkeypatch.setattr(mcpmod.storage, "is_configured", lambda: True)
    monkeypatch.setattr(mcpmod.storage, "validate_upload",
                        lambda filename, size, kind: ("mp4", "video/mp4"))
    monkeypatch.setattr(mcpmod.storage, "new_original_key",
                        lambda project_id, ext, kind: "clips/3/ref.mp4")
    monkeypatch.setattr(mcpmod.storage, "presign_upload",
                        lambda key, size, content_type: upload_plan)

    body = text_of(rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "upload_start",
        "arguments": {"project_id": 3, "filename": "reference.mp4",
                      "size_bytes": 1234, "kind": "clip",
                      "role": "shorts_reference", "duration_s": 42.5},
    }))

    assert 'role="shorts_reference"' in body
    assert "duration_s=42.5" in body
    plan = json.loads(body.rsplit("\n\n", 1)[-1])
    finish = plan["upload_finish_arguments"]
    assert finish["project_id"] == 3
    assert finish["storage_key"] == "clips/3/ref.mp4"
    assert finish["kind"] == "clip"
    assert finish["role"] == "shorts_reference"
    assert finish["duration_s"] == 42.5
    if upload_plan["mode"] == "multipart":
        assert finish["upload_id"] == "multi-7"
        assert "parts=[" in body


def test_shorts_reference_role_is_refused_for_non_clip_uploads(
        client, monkeypatch):
    completed = []
    monkeypatch.setattr(mcpmod.storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        mcpmod, "complete_upload_core",
        lambda *args: completed.append(args) or ({"asset_id": 1}, 200))
    bad = {"project_id": 3, "kind": "music",
           "role": "shorts_reference"}

    start = text_of(rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "upload_start",
        "arguments": {**bad, "filename": "song.mp3", "size_bytes": 12},
    }))
    finish = text_of(rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "upload_finish",
        "arguments": {**bad, "storage_key": "music/3/song.mp3"},
    }))

    assert "valid only with kind='clip'" in start
    assert "valid only with kind='clip'" in finish
    assert completed == []


def test_upload_finish_passes_reference_metadata_and_says_it_is_not_media(
        client, monkeypatch):
    captured = {}

    def complete(user_id, project_id, data):
        captured.update(user_id=user_id, project_id=project_id, data=data)
        return {"asset_id": 81, "index_job_id": 82,
                "kind": "video_clip"}, 200

    monkeypatch.setattr(mcpmod, "complete_upload_core", complete)
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "upload_finish",
        "arguments": {"project_id": 3,
                      "storage_key": "clips/3/ref.mp4",
                      "filename": "reference.mp4", "kind": "clip",
                      "role": "shorts_reference", "duration_s": 42.5},
    }))

    assert captured["user_id"] == 60 and captured["project_id"] == 3
    assert captured["data"]["role"] == "shorts_reference"
    assert captured["data"]["duration_s"] == 42.5
    assert "Shorts style reference (asset 81)" in body
    assert "wait_for_job(job_id=82)" in body
    assert "reference-only analysis input" in body
    assert "not added to the timeline" in body
    assert "or offered as placeable media" in body


def test_create_podcast_shorts_project_persists_its_kind(client):
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "create_project",
                        "arguments": {"title": "My podcast",
                                      "kind": "shorts"}}))
    assert DB["created_project"]["kind"] == "shorts"
    assert DB["static_project"] == 71
    assert "nothing selects story arcs automatically" in body
    assert "make_shorts with explicit clips" in body


def test_shorts_status_returns_children_ready_for_follow_up_edits(client):
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "shorts_status",
                        "arguments": {"project_id": 3}}))
    assert "status: ready" in body
    assert "Planner job 8: done" in body
    assert "card 1, project [9] Strong hook" in body
    assert "edit v6" in body and "final done (job 10)" in body
    assert "open_short(parent_project_id=3, card=N)" in body
    assert "in-house agent is not callable over MCP" in body
    assert "must make every child edit itself" in body
    assert "story: A risky launch -> Evidence was ignored -> The team changed course" in body
    assert "design: Clean evidence-led design" in body
    assert "B-roll plan: 20s failed product launch" in body
    assert "Final export is Studio-only" in body


def test_open_short_puts_the_child_edl_under_direct_mcp_control(client):
    DB["static_project"] = None  # explicit board, never the stale pointer
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "open_short",
                        "arguments": {"parent_project_id": 3, "card": 1}}))
    assert DB["static_project"] == 9
    assert "DIRECT MCP editing" in body
    assert "No Valmera agent was called" in body
    assert "project_id=9 on every normal editor tool" in body


def test_open_short_resolves_an_explicit_child_without_active_pointer(client):
    DB["static_project"] = None
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "open_short",
                        "arguments": {"child_project_id": 9}}))
    assert DB["static_project"] == 9
    assert "DIRECT MCP editing" in body


def test_open_short_rejects_an_unknown_explicit_child(client):
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "open_short",
                        "arguments": {"child_project_id": 999}}))
    assert DB["static_project"] == 3
    assert "does not exist on this account" in body


def test_open_short_card_numbers_match_status_while_earlier_card_builds(client):
    DB["project_rows"][3]["meta"]["shorts"]["clips"].insert(
        0, {"order": -1, "title": "Building", "start": 0, "end": 20})
    waiting = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                          {"name": "open_short", "arguments": {
                              "parent_project_id": 3, "card": 1}}))
    assert "still building" in waiting
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "open_short", "arguments": {
                           "parent_project_id": 3, "card": 2}}))
    assert DB["static_project"] == 9
    assert "DIRECT MCP editing" in body


def test_sse_preferring_client_gets_an_sse_frame(client):
    r = rpc(client, "ping", STATIC_TOKEN, accept="text/event-stream")
    assert r.mimetype == "text/event-stream"
    assert r.data.startswith(b"event: message")


def test_json_preferring_client_gets_json(client):
    r = rpc(client, "ping", STATIC_TOKEN,
            accept="application/json, text/event-stream")
    assert r.mimetype == "application/json"


# ── tool calls ───────────────────────────────────────────────────────

def test_editor_tool_returns_the_workers_own_text(client):
    r = rpc(client, "tools/call", STATIC_TOKEN,
            {"name": "get_transcript", "arguments": {"project_id": 3}})
    assert text_of(r).endswith("12 sentences.")
    assert "PROJECT 3" in text_of(r)
    assert r.get_json()["result"].get("isError") is not True


def test_delayed_tool_result_repeats_immutable_project_identity(client):
    body = text_of(rpc(client, "tools/call", STATIC_TOKEN,
                       {"name": "wait_for_job",
                        "arguments": {"job_id": 5}}))
    assert body.startswith('PROJECT 3 — "P"')
    assert body.endswith("12 sentences.")


def test_a_look_tool_returns_the_PICTURES_not_a_paragraph(client, monkeypatch):
    """THE POINT OF ROUND 83e. Over MCP, look_at used to run OUR vision model
    over the frames and send the outside model a description — second-hand,
    billed to us, and impossible to argue with. A tools/call result carries
    image content perfectly well; the plumbing just never did. Now the model
    that is doing the editing sees the footage itself."""
    DB["job_result"] = {"text": "Captured 3 frames.",
                        "images": [{"storage_key": "media/3/look_a.jpg",
                                    "label": "@4.20s"}]}
    monkeypatch.setattr(mcpmod.storage, "get_object_whole",
                        lambda key, cap: b"\xff\xd8jpegbytes")
    res = rpc(client, "tools/call", STATIC_TOKEN,
              {"name": "get_transcript",
               "arguments": {"project_id": 3}}).get_json()["result"]
    kinds = [c["type"] for c in res["content"]]
    assert kinds == ["text", "text", "image"]     # body, label, picture
    img = res["content"][2]
    assert img["mimeType"] == "image/jpeg"
    assert base64.b64decode(img["data"]) == b"\xff\xd8jpegbytes"


def test_an_unreadable_frame_never_costs_the_answer(client, monkeypatch):
    """The words are the tool's result; the picture is an attachment. A
    storage hiccup must degrade to what we had before, not fail the call."""
    DB["job_result"] = {"text": "Captured 3 frames.",
                        "images": [{"storage_key": "media/3/gone.jpg"}]}
    monkeypatch.setattr(mcpmod.storage, "get_object_whole",
                        lambda key, cap: None)
    res = rpc(client, "tools/call", STATIC_TOKEN,
              {"name": "get_transcript",
               "arguments": {"project_id": 3}}).get_json()["result"]
    assert [c["type"] for c in res["content"]] == ["text"]
    assert res.get("isError") is not True


def test_unknown_tool_explains_the_likely_reason(client):
    r = rpc(client, "tools/call", STATIC_TOKEN,
            {"name": "nope", "arguments": {}})
    assert r.get_json()["result"]["isError"] is True
    assert "hidden rather than failing" in text_of(r)


def test_stale_client_cannot_call_final_export(client):
    """Removing a tool from tools/list is not a security boundary: connected
    clients cache schemas. The server must refuse the old name before it can
    reach either a session implementation or the worker queue."""
    r = rpc(client, "tools/call", STATIC_TOKEN,
            {"name": "export_final",
             "arguments": {"project_id": 3, "edl_version": 9}})
    assert r.get_json()["result"]["isError"] is True
    assert "unavailable over MCP" in text_of(r)
    assert "Valmera Studio" in text_of(r)
    assert not DB["enqueued"]


def test_worker_catalog_cannot_reintroduce_final_export(client, monkeypatch):
    """A future editor-registry change must not punch through the explicit
    MCP delivery boundary."""
    stale = dict(CATALOG)
    stale["tools"] = list(CATALOG["tools"]) + [{
        "type": "function", "function": {
            "name": "export_final", "description": "stale",
            "parameters": {"type": "object", "properties": {}}}}]
    monkeypatch.setattr(mcpmod, "_catalog", lambda: stale)
    names = [t["name"] for t in
             rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]]
    assert "export_final" not in names


def test_editing_without_an_explicit_project_says_what_to_do(client):
    DB["static_project"] = None
    body = text_of(rpc(
        client, "tools/call", STATIC_TOKEN,
        {"name": "get_transcript", "arguments": {}}))
    assert "project_id" in body and "will not guess" in body


def test_unknown_method_is_a_jsonrpc_error(client):
    assert rpc(client, "nonsense", STATIC_TOKEN).get_json()["error"]["code"] \
        == -32601


def test_render_preview_public_response_preserves_no_audio_model_provenance(
        client, monkeypatch):
    catalog = dict(CATALOG)
    catalog["tools"] = list(CATALOG["tools"]) + [{
        "type": "function", "function": {
            "name": "render_preview",
            "description": "Render deterministic preview evidence.",
            "parameters": {"type": "object", "properties": {
                "complete": {"type": "boolean"}}}}}]
    monkeypatch.setattr(mcpmod, "_catalog", lambda: catalog)
    DB["job_result"] = {
        "text": "Preview v8 rendered: 61.2s.",
        "edl_version": 8,
        "edl_changed": False,
        "preview": {"edl_version": 8, "duration_s": 61.2,
                    "asset_id": 44, "audio_model_review": False},
    }

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "render_preview",
        "arguments": {"project_id": 3, "complete": True},
    }).get_json()["result"]

    body = public["content"][0]["text"]
    assert "PREVIEW PROVENANCE: audio_model_review=false" in body
    assert "no separate listening model was invoked" in body
    assert public["structuredContent"] == {
        "edl_version": 8,
        "edl_changed": False,
        "preview": {"edl_version": 8, "duration_s": 61.2,
                    "asset_id": 44, "audio_model_review": False},
    }
    assert "listen_keys" not in json.dumps(public)


def test_wait_for_mcp_preview_preserves_the_same_public_provenance(client):
    DB["job_result"] = {
        "text": "Preview v8 rendered: 61.2s.",
        "edl_version": 8,
        "edl_changed": False,
        "preview": {"edl_version": 8, "duration_s": 61.2,
                    "asset_id": 44, "audio_model_review": False},
    }

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "wait_for_job", "arguments": {"job_id": 5},
    }).get_json()["result"]

    assert "audio_model_review=false" in public["content"][0]["text"]
    assert public["structuredContent"]["preview"][
        "audio_model_review"] is False


# ── watch_video: the one tool whose answer is not a sentence ─────────
#
# Every other tool returns text. This one has to put a real MP4 into a
# JSON-RPC reply for a model that can watch video, and fall back to a link
# for one that cannot — so what is tested here is the CONTENT SHAPE, which
# nothing else on the surface exercises.

MOVIE = b"\x00\x00\x00\x18ftypmp42" + b"fake bytes" * 20


def _served(monkeypatch, *, inline=True, nbytes=len(MOVIE)):
    DB["job_result"] = {
        "text": "Here is the assembled program.",
        "video": {"storage_key": "media/3/mv_abc.mp4", "mime": "video/mp4",
                  "bytes": nbytes, "duration_s": 8.9, "height": 480,
                  "start_s": 0, "kind": "timeline", "transcoded": False,
                  "inline": inline}}
    monkeypatch.setattr(mcpmod.storage, "presign_get",
                        lambda key, **kw: f"https://cdn.example/{key}?sig=1")
    monkeypatch.setattr(mcpmod.storage, "get_object_whole",
                        lambda key, cap: MOVIE if len(MOVIE) <= cap else None)


def _call_watch(client, **args):
    args.setdefault("project_id", 3)
    return rpc(client, "tools/call", STATIC_TOKEN,
               {"name": "watch_video", "arguments": args}
               ).get_json()["result"]


def test_watch_video_is_on_the_surface(client):
    names = [t["name"] for t in
             rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]]
    assert "watch_video" in names


def test_watch_video_schema_exposes_clean_asr_retrieval_switch(client):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    watch = next(tool for tool in tools if tool["name"] == "watch_video")
    frames = watch["inputSchema"]["properties"]["frames"]
    assert frames["type"] == "boolean"
    assert "external ASR" in frames["description"]


def test_watch_video_public_result_preserves_false_preview_receipt(
        client, monkeypatch):
    _served(monkeypatch)
    DB["job_result"]["preview"] = {
        "asset_id": 44, "edl_version": 8, "duration_s": 61.2,
        "audio_model_review": False}

    public = _call_watch(client, frames=False)

    assert DB["enqueued"][-1]["args"]["frames"] is False
    assert "audio_model_review=false" in public["content"][0]["text"]
    assert public["structuredContent"]["preview"][
        "audio_model_review"] is False
    assert [block["type"] for block in public["content"]] == ["text"]


def test_version_pinned_preview_download_returns_server_receipt(
        client, monkeypatch):
    DB["render_assets"] = [{
        "id": 44, "project_id": 3, "storage_key": "media/3/p8.mp4",
        "duration_s": 61.2,
        "meta": {"variant": "preview", "edl_version": 8,
                 "audio_model_review": False, "render_job_id": 92},
    }]
    monkeypatch.setattr(
        mcpmod.storage, "presign_get",
        lambda key: f"https://cdn.example/{key}?sig=receipt")

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "download_url", "arguments": {
            "project_id": 3, "kind": "preview", "edl_version": 8},
    }).get_json()["result"]

    receipt = public["structuredContent"]["preview_receipt"]
    assert receipt["asset_id"] == 44
    assert receipt["render_job_id"] == 92
    assert receipt["edl_version"] == 8
    assert receipt["duration_s"] == 61.2
    assert receipt["audio_model_review"] is False
    assert receipt["listen_keys_count"] == 0
    assert receipt["listen_clips_count"] == 0
    assert receipt["audio_reviewer_findings_count"] == 0
    assert len(receipt["meta_sha256"]) == 64
    assert receipt["url"] == \
        "https://cdn.example/media/3/p8.mp4?sig=receipt"
    assert "PREVIEW RECEIPT:" in public["content"][0]["text"]
    assert "audio_model_review=false" in public["content"][0]["text"]


def test_preview_download_rejects_listener_enabled_cached_asset(
        client, monkeypatch):
    DB["render_assets"] = [{
        "id": 45, "project_id": 3, "storage_key": "media/3/studio.mp4",
        "duration_s": 61.2,
        "meta": {"variant": "preview", "edl_version": 8,
                 "audio_model_review": True},
    }]
    monkeypatch.setattr(
        mcpmod.storage, "presign_get",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("incompatible preview must not be signed")))

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "download_url", "arguments": {
            "project_id": 3, "kind": "preview", "edl_version": 8},
    }).get_json()["result"]

    assert "audio_model_review=false" in public["content"][0]["text"]
    assert "call render_preview" in public["content"][0]["text"]
    assert "structuredContent" not in public


def test_preview_download_rejects_false_stamp_with_listener_artifacts(
        client, monkeypatch):
    DB["render_assets"] = [{
        "id": 46, "project_id": 3, "storage_key": "media/3/leaked.mp4",
        "duration_s": 61.2,
        "meta": {"variant": "preview", "edl_version": 8,
                 "audio_model_review": False,
                 "render_job_id": 93,
                 "listen_keys": ["listen/46-opening.mp3"]},
    }]
    monkeypatch.setattr(
        mcpmod.storage, "presign_get",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("listener-bearing preview must not be signed")))

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "download_url", "arguments": {
            "project_id": 3, "kind": "preview", "edl_version": 8},
    }).get_json()["result"]

    assert "listener artifacts" in public["content"][0]["text"]
    assert "Call render_preview" in public["content"][0]["text"]
    assert "structuredContent" not in public


def test_preview_download_rejects_false_legacy_asset_without_job_lineage(
        client, monkeypatch):
    DB["render_assets"] = [{
        "id": 47, "project_id": 3, "storage_key": "media/3/legacy.mp4",
        "duration_s": 61.2,
        "meta": {"variant": "preview", "edl_version": 8,
                 "audio_model_review": False},
    }]
    monkeypatch.setattr(
        mcpmod.storage, "presign_get",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("lineage-free preview must not be signed")))

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "download_url", "arguments": {
            "project_id": 3, "kind": "preview", "edl_version": 8},
    }).get_json()["result"]

    assert "lacks complete deterministic-only provenance" in \
        public["content"][0]["text"]
    assert "Call render_preview" in public["content"][0]["text"]
    assert "structuredContent" not in public


@pytest.mark.parametrize("duration_s", [0, float("nan"), float("inf")])
def test_preview_download_rejects_non_positive_or_non_finite_duration(
        client, monkeypatch, duration_s):
    DB["render_assets"] = [{
        "id": 48, "project_id": 3, "storage_key": "media/3/bad-duration.mp4",
        "duration_s": duration_s,
        "meta": {"variant": "preview", "edl_version": 8,
                 "audio_model_review": False, "render_job_id": 94},
    }]
    monkeypatch.setattr(
        mcpmod.storage, "presign_get",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("invalid-duration preview must not be signed")))

    public = rpc(client, "tools/call", STATIC_TOKEN, {
        "name": "download_url", "arguments": {
            "project_id": 3, "kind": "preview", "edl_version": 8},
    }).get_json()["result"]

    assert "lacks complete deterministic-only provenance" in \
        public["content"][0]["text"]
    assert "structuredContent" not in public


def test_a_small_video_comes_back_embedded_beside_its_link(client, monkeypatch):
    _served(monkeypatch)
    monkeypatch.setattr(mcpmod, "VIDEO_ALLOW_INLINE", True)
    res = _call_watch(client, delivery="inline")
    kinds = [c["type"] for c in res["content"]]
    # Text FIRST — it says which clock the video runs on, and the model has to
    # read that before it acts on anything it saw.
    assert kinds == ["text", "resource"]
    assert "https://cdn.example/media/3/mv_abc.mp4" in res["content"][0]["text"]
    blob = res["content"][1]["resource"]
    assert blob["mimeType"] == "video/mp4"
    assert base64.b64decode(blob["blob"]) == MOVIE


def test_the_model_cannot_turn_embedding_on_by_itself(client, monkeypatch):
    """THE BUG THIS EXISTS FOR (Aug 3 2026, the SECOND time). Making embedding
    an opt-in argument was not enough: asked whether it could hear the music,
    Grok passed delivery="inline" — reasonably, since the tool offered it and
    the only caveat was a fact about its own CLIENT that it cannot check. It
    embedded 2.9 MB and the session ended.

    Whether a client can decode a video block is the operator's knowledge, so
    it is the operator's switch. The model asking must never be enough."""
    _served(monkeypatch, inline=True)
    assert mcpmod.VIDEO_ALLOW_INLINE is False       # off unless the env says
    res = _call_watch(client, delivery="inline")
    assert [c["type"] for c in res["content"]] == ["text"]
    body = res["content"][0]["text"]
    assert "does not do that" in body               # ...and says so honestly
    assert res.get("isError") is not True           # refusing is not failing


def test_inline_is_not_even_offered_when_it_is_off(client):
    """Honest-off gating: a capability this deployment will refuse is hidden
    from the schema rather than left out for the model to trip over."""
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    watch = [t for t in tools if t["name"] == "watch_video"][0]
    assert watch["inputSchema"]["properties"]["delivery"]["enum"] == \
        ["auto", "url"]


def test_the_worker_is_told_not_to_advertise_embedding_either(client,
                                                              monkeypatch):
    """The worker writes the reply text and has no other way to know. A zero
    budget is how it is told to stop inviting the model to ask."""
    _served(monkeypatch)
    _call_watch(client)
    assert DB["enqueued"][-1]["args"]["_inline_max_bytes"] == 0


def test_the_default_NEVER_embeds(client, monkeypatch):
    """THE BUG THIS EXISTS FOR (Aug 3 2026). It used to embed whenever the
    file happened to fit, on the assumption that a client which cannot render
    a video block would ignore it. Grok STRINGIFIED it: a 2.9 MB preview
    arrived as 4 million characters of base64 and ended the session, while
    the tool reported success. Embedding is opt-in now, forever."""
    # The worker is faked here as saying inline=True — i.e. even if it
    # regressed, this service must still refuse. The rule is enforced twice,
    # independently, because it is enforced across two deployables.
    _served(monkeypatch, inline=True)
    res = _call_watch(client)
    assert [c["type"] for c in res["content"]] == ["text"]
    assert "https://cdn.example/" in res["content"][0]["text"]


def test_the_sound_comes_back_as_audio_content(client, monkeypatch):
    """THE QUESTION THAT EXPOSED THIS (Aug 4 2026): "can you hear the music?"
    The reply carried frames and no audio, so the model downloaded the MP4 and
    built a SPECTROGRAM to answer — while the text said "H.264 + AAC", which
    invited it to claim it had heard something it was never sent. MCP has an
    audio content type; we simply were not using it."""
    _served(monkeypatch)
    DB["job_result"]["audio"] = {"storage_key": "media/3/aud_x.mp3",
                                 "mime": "audio/mpeg", "bytes": 180000,
                                 "seconds": 28.7, "kbps": 48}
    monkeypatch.setattr(mcpmod.storage, "get_object_whole",
                        lambda key, cap: b"ID3mp3bytes")
    res = _call_watch(client)
    kinds = [c["type"] for c in res["content"]]
    assert "audio" in kinds
    blk = res["content"][kinds.index("audio")]
    assert blk["mimeType"] == "audio/mpeg"
    assert base64.b64decode(blk["data"]) == b"ID3mp3bytes"


def test_a_silent_programme_simply_has_no_audio_block(client, monkeypatch):
    """No sound is a normal outcome, not an error — and the text is what says
    so. What must never happen is a claim of audio with none attached."""
    _served(monkeypatch)
    res = _call_watch(client)
    assert "audio" not in [c["type"] for c in res["content"]]
    assert res.get("isError") is not True


def test_delivery_url_never_embeds(client, monkeypatch):
    """A client that cannot read a video block must be able to say so and
    still get the file — the link is the universal answer."""
    _served(monkeypatch)
    res = _call_watch(client, delivery="url")
    assert [c["type"] for c in res["content"]] == ["text"]
    assert "https://cdn.example/" in res["content"][0]["text"]


def test_a_video_too_big_to_embed_is_still_delivered(client, monkeypatch):
    _served(monkeypatch, inline=False, nbytes=400 * 1048576)
    res = _call_watch(client)
    assert [c["type"] for c in res["content"]] == ["text"]
    assert res.get("isError") is not True


def test_the_worker_is_told_the_backends_own_inline_budget(client, monkeypatch):
    """The cap lives here, where the base64 is actually carried. Sending it
    down with the call is what stops the two services keeping two copies of
    the number and drifting."""
    _served(monkeypatch)
    monkeypatch.setattr(mcpmod, "VIDEO_ALLOW_INLINE", True)
    _call_watch(client)
    args = DB["enqueued"][-1]["args"]
    assert args["_inline_max_bytes"] == int(mcpmod.VIDEO_INLINE_MAX_MB * 1048576)
    assert args["delivery"] == "auto"


def test_a_bad_delivery_value_is_refused_before_any_work(client, monkeypatch):
    _served(monkeypatch)
    res = _call_watch(client, delivery="telepathy")
    assert res["isError"] is True
    assert not DB["enqueued"]


def test_the_workers_refusal_survives_as_a_refusal(client, monkeypatch):
    """No video means the worker had a reason. It must reach the model as an
    error, not as a cheerful text block with nothing attached."""
    DB["job_result"] = {"text": "This project has no main video.",
                        "is_error": True}
    res = _call_watch(client, kind="source")
    assert res["isError"] is True
    assert "no main video" in res["content"][0]["text"]


def test_a_render_that_outran_the_wait_is_not_reported_as_a_failure(
        client, monkeypatch):
    """watch_video renders the current edit when it has to, and a render can
    outlast the HTTP request. STILL RUNNING is the protocol's normal answer —
    flagging it as an error sends the model looking for a bug."""
    DB["job_result"] = {"text": "STILL RUNNING — rendering v12 is job 9."}
    res = _call_watch(client)
    assert res.get("isError") is not True
    assert "STILL RUNNING" in res["content"][0]["text"]


# ── the claude.ai connector flow, in order ───────────────────────────

def _pkce():
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def test_claude_ai_connector_flow(client):
    # 1. The 401 is the only entry point claude.ai has.
    r = rpc(client, "initialize")
    assert r.status_code == 401
    wa = r.headers.get("WWW-Authenticate", "")
    meta_url = re.search(r'resource_metadata="([^"]+)"', wa).group(1)
    assert meta_url.startswith("https://api.example.com/.well-known/")

    # 2. Discovery.
    prm = client.get(urlsplit(meta_url).path).get_json()
    assert prm["resource"] == "https://api.example.com/mcp"
    assert prm["authorization_servers"] == ["https://api.example.com"]
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code \
        == 200
    asm = client.get("/.well-known/oauth-authorization-server").get_json()
    assert asm["issuer"] == "https://api.example.com"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]

    # 3. The client enrols itself — no human involved yet.
    reg = client.post("/mcp/oauth/register",
                      json={"client_name": "Claude",
                            "redirect_uris": [CALLBACK]})
    assert reg.status_code == 201
    client_id = reg.get_json()["client_id"]

    # 4. Login + consent.
    verifier, challenge = _pkce()
    q = {"client_id": client_id, "redirect_uri": CALLBACK,
         "response_type": "code", "scope": oauth.SCOPE, "state": "xyz123",
         "code_challenge": challenge, "code_challenge_method": "S256",
         "resource": "https://api.example.com/mcp"}
    page = client.get("/mcp/oauth/authorize", query_string=q)
    assert page.status_code == 200
    assert b"wants to edit your videos" in page.data
    assert b"Claude" in page.data and b'value="xyz123"' in page.data

    r = client.post("/mcp/oauth/authorize",
                    data={**q, "action": "allow", "email": EMAIL,
                          "password": PASSWORD})
    assert r.status_code == 302 and r.headers["Location"].startswith(CALLBACK)
    qs = parse_qs(urlsplit(r.headers["Location"]).query)
    assert qs["state"] == ["xyz123"]
    code = qs["code"][0]
    grant_id = DB["codes"][hashlib.sha256(code.encode()).hexdigest()]["grant_id"]

    # 5. Code -> tokens, PKCE enforced.
    bad = client.post("/mcp/oauth/token",
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": CALLBACK, "client_id": client_id,
                            "code_verifier": "not-the-verifier"})
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "invalid_grant"

    tok = client.post("/mcp/oauth/token",
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": CALLBACK, "client_id": client_id,
                            "code_verifier": verifier}).get_json()
    assert tok["token_type"] == "Bearer" and tok["refresh_token"]
    access = tok["access_token"]

    # 6. It edits.
    assert "DOCTRINE." in rpc(client, "initialize", access
                              ).get_json()["result"]["instructions"]
    rpc(client, "tools/call", access,
        {"name": "open_project", "arguments": {"project_id": 3}})
    assert DB["grants"][grant_id]["active_project_id"] == 3
    assert text_of(rpc(client, "tools/call", access,
                       {"name": "get_transcript",
                        "arguments": {"project_id": 3}})) \
        .endswith("12 sentences.")

    # 7. Refresh rotates, and the open project survives it — the whole reason
    #    that pointer lives on the grant and not on the token.
    tok2 = client.post("/mcp/oauth/token",
                       data={"grant_type": "refresh_token",
                             "refresh_token": tok["refresh_token"],
                             "client_id": client_id}).get_json()
    assert tok2["access_token"] != access
    assert DB["grants"][grant_id]["active_project_id"] == 3
    assert client.post("/mcp/oauth/token",
                       data={"grant_type": "refresh_token",
                             "refresh_token": tok["refresh_token"],
                             "client_id": client_id}).status_code == 400

    # 8. A replayed code means the code may be stolen: the grant dies.
    assert client.post("/mcp/oauth/token",
                       data={"grant_type": "authorization_code", "code": code,
                             "redirect_uri": CALLBACK, "client_id": client_id,
                             "code_verifier": verifier}).status_code == 400
    assert DB["grants"][grant_id]["revoked_at"] is not None
    assert rpc(client, "tools/list", tok2["access_token"]).status_code == 401


# ── the ways authorization must refuse ───────────────────────────────

def _registered(client):
    return client.post("/mcp/oauth/register",
                       json={"client_name": "Claude",
                             "redirect_uris": [CALLBACK]}).get_json()["client_id"]


def _q(client_id, **over):
    _v, challenge = _pkce()
    q = {"client_id": client_id, "redirect_uri": CALLBACK,
         "response_type": "code", "scope": oauth.SCOPE, "state": "s",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    q.update(over)
    return q


def test_plain_http_redirect_uri_cannot_register(client):
    r = client.post("/mcp/oauth/register",
                    json={"client_name": "Bad",
                          "redirect_uris": ["http://evil.example/x"]})
    assert r.status_code == 400


def test_unregistered_redirect_never_redirects(client):
    """An authorization server that bounces errors to an unvalidated URI is an
    open redirector. Both of these must dead-end on our own page."""
    cid = _registered(client)
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q(cid, redirect_uri="https://evil.example/x"))
    assert r.status_code == 400 and b"not one this app" in r.data
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q("mcpc_nope"))
    assert r.status_code == 400 and b"not registered" in r.data


def test_pkce_is_mandatory(client):
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q(_registered(client), code_challenge=""))
    assert r.status_code == 302
    assert "error=invalid_request" in r.headers["Location"]


def test_wrong_password_issues_no_code(client):
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "allow",
                          "email": EMAIL, "password": "nope"})
    assert r.status_code == 401
    assert b"Wrong email or password" in r.data
    assert not DB["codes"]


def test_deny_returns_access_denied(client):
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "deny",
                          "email": EMAIL, "password": PASSWORD})
    assert "error=access_denied" in r.headers["Location"]
    assert not DB["codes"]


def test_pressing_enter_in_the_form_means_ALLOW(client):
    """THE BUG THIS EXISTS FOR (Aug 3 2026). Enter in a text field does
    implicit submission, which activates the FIRST submit button. Cancel was
    written first, so typing a password and pressing Enter posted
    action=deny — the server refused correctly, redirected the app away with
    access_denied, and Grok showed "Unable to authorize app". Nothing in the
    server logs looked wrong and no code row was ever written; it simply did
    not work for anyone who does not click.

    The default submit button must therefore be Allow. This asserts the
    MARKUP order, because that is what the browser reads — the on-screen
    order is CSS, and the two are deliberately opposite."""
    body = client.get("/mcp/oauth/authorize", query_string=_q(
        _registered(client))).get_data(as_text=True)
    form = body[body.index("<form"):]
    assert form.index('value="allow"') < form.index('value="deny"')


def test_a_post_with_no_button_is_not_reported_as_a_refusal(client):
    """Belt to the braces above: if a client ever submits without a button
    value, that is ambiguous, not a decline. Bouncing the app away with
    access_denied is what made the Enter bug impossible to read from the
    outside — the user stays here instead, and is told which button to press."""
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "email": EMAIL,
                          "password": PASSWORD})
    assert r.status_code == 400
    assert "Location" not in r.headers
    assert b"Press Allow" in r.data
    assert not DB["codes"]


def test_right_password_off_the_allowlist_is_refused_honestly(client,
                                                              monkeypatch):
    """The credentials were correct and the feature is still closed. Saying
    'wrong password' here would be a lie."""
    monkeypatch.setattr(mcpmod, "ALLOWED_EMAILS", {"someone@else.com"})
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "allow",
                          "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403
    assert b"not enabled for this account" in r.data
    assert not DB["codes"]


def test_a_get_probe_also_gets_the_challenge(client):
    """Some clients probe with GET first; without the challenge there they
    never discover the authorization server."""
    r = client.get("/mcp")
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("WWW-Authenticate", "")


def test_production_gunicorn_keeps_threaded_http_capacity_for_mcp_waits():
    start = (Path(__file__).resolve().parents[2] / "start.sh").read_text()
    assert "--workers 3" in start
    assert "--worker-class gthread" in start
    assert "--threads 8" in start
