"""expand_toolset: the gateway for MCP sessions with a stale tools/list.

Clients fetch tools/list once, at connect, and this server cannot announce
changes (listChanged is False). A session opened before a release cannot call
anything that release added; expand_toolset is in every client's cached list
and clients pass unknown arguments through, so it carries:

  describe=[names]                         current schemas
  call={"name": T, "arguments": A}         T run exactly like a direct call
  domains=[...]                            current names per department

What is pinned here: a call through the gateway is indistinguishable from a
direct call of the same tool (result, queued job payload — which is what the
executor charges, audits and skew-guards — gates, refusals and public error
metrics under the inner name); the gateway itself only refuses, with nothing
run, recursion, an unknown name and a project_id that differs from the outer
one.

    cd backend && python -m pytest tests -q
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routes.mcp as mcpmod                                 # noqa: E402
from tests.test_mcp import (CATALOG, DB, STATIC_TOKEN,      # noqa: E402,F401
                            client, rpc, stub_db)
from tests.test_w0c_mcp_music_gate import (                 # noqa: E402,F401
    LIBRARY, UPLOAD, api, _batch, _item)

NEW_TOOL = "apply_format_pack"     # a Wave 2 verb a stale session never saw


def _tool(name, props=None, required=None, desc=None):
    return {"type": "function", "function": {
        "name": name, "description": desc or f"{name} does its job.",
        "parameters": {"type": "object", "properties": props or {},
                       "required": required or []}}}


EXPAND = _tool(
    "expand_toolset",
    {"domains": {"type": "array", "items": {
        "type": "string", "enum": ["captions", "direction", "verify"]}}},
    ["domains"],
    "Load additional tool schemas for the NEXT reasoning step when the "
    "compact catalog does not expose a capability you need. Over MCP it "
    "lists current tools by domain and reaches tools your cached list "
    "lacks: describe=[names] returns their current schemas, call={name, "
    "arguments} runs one exactly like a direct call.")

# What the stale client cached at connect: the old registry.
OLD_CATALOG = copy.deepcopy(CATALOG)
OLD_CATALOG["tools"] = OLD_CATALOG["tools"] + [EXPAND]

# What the worker publishes after the release.
NEW_CATALOG = copy.deepcopy(OLD_CATALOG)
NEW_CATALOG["tools"] = NEW_CATALOG["tools"] + [
    _tool(NEW_TOOL, {"pack": {"type": "string"},
                     "intensity": {"type": "number"}}, ["pack"],
          "Apply a format pack to the short."),
    _tool("suggest_devices", {"limit": {"type": "integer"}}),
    _tool("apply_edit_batch", {"base_version": {"type": "integer"},
                               "operation_id": {"type": "string"},
                               "operations": {"type": "array"}},
          ["base_version", "operations", "operation_id"]),
]
NEW_CATALOG["write_tools"] = ["make_shorts", NEW_TOOL, "apply_edit_batch"]
NEW_CATALOG["domains"] = {
    "captions": ["get_transcript"],
    "direction": [NEW_TOOL],
    "verify": ["suggest_devices"],
}


def _names(client):
    return {t["name"] for t in rpc(client, "tools/list", STATIC_TOKEN)
            .get_json()["result"]["tools"]}


@pytest.fixture
def released(client, monkeypatch):
    """A client connected on the OLD catalog; the server then released."""
    monkeypatch.setattr(mcpmod, "_catalog", lambda: OLD_CATALOG)
    cached = _names(client)
    monkeypatch.setattr(mcpmod, "_catalog", lambda: NEW_CATALOG)
    return cached


def _call(client, name, arguments, _id=1):
    return rpc(client, "tools/call", STATIC_TOKEN,
               {"name": name, "arguments": arguments}, _id=_id).get_json()


def _via_gateway(client, name, arguments, outer=None, _id=2, **extra):
    wrapper = dict(extra, call={"name": name, "arguments": arguments})
    if outer is not None:
        wrapper["project_id"] = outer
    return _call(client, "expand_toolset", wrapper, _id=_id)


def _without_call_id(payloads):
    return [{k: v for k, v in p.items() if k != "mcp_call_id"}
            for p in payloads]


def _events(monkeypatch):
    events = []

    def capture(user_id, project_id, kind, asset_id=None, detail=None,
                origin="client"):
        events.append(detail)
        return True
    monkeypatch.setattr(mcpmod, "record_client_event", capture)
    return events


# ── 1. a stale client reaches a tool its cached list lacks ───────────────

def test_a_tool_absent_from_the_cached_list_runs_exactly_like_a_direct_call(
        client, released):
    assert NEW_TOOL not in released and "expand_toolset" in released
    args = {"project_id": 3, "pack": "documentary", "intensity": 0.6}
    DB["job_result"] = {"text": "EDL v6 -> v7: pack applied.",
                        "edl_version": 7, "edl_changed": True,
                        "metered_contract": True}

    direct = _call(client, NEW_TOOL, dict(args), _id=1)
    direct_jobs = list(DB["enqueued"])
    DB["enqueued"].clear()
    gateway = _via_gateway(client, NEW_TOOL, dict(args), outer=3,
                           domains=[], _id=1)

    assert gateway == direct
    assert gateway["result"]["isError"] is False
    assert "pack applied" in gateway["result"]["content"][0]["text"]
    # The queued job IS the inner call: the executor charges, audits and
    # skew-guards apply_format_pack and its own arguments, never a wrapper.
    assert _without_call_id(DB["enqueued"]) == _without_call_id(direct_jobs)
    assert DB["enqueued"][0]["tool"] == NEW_TOOL
    assert DB["enqueued"][0]["args"] == {"pack": "documentary",
                                         "intensity": 0.6}
    assert DB["enqueued"][0]["mutation"] is True     # the write gate's input


def test_describe_returns_the_current_schema_the_cached_list_lacks(
        client, released):
    result = _call(client, "expand_toolset",
                   {"project_id": 3, "domains": [],
                    "describe": [NEW_TOOL, "get_transcript"]})["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    rows = [json.loads(line) for line in text.splitlines()
            if line.startswith("{")]
    by_name = {row["name"]: row for row in rows}
    served = {t["name"]: t for t in rpc(client, "tools/list", STATIC_TOKEN)
              .get_json()["result"]["tools"]}
    # Exactly what a fresh tools/list would carry, project_id included.
    for name in (NEW_TOOL, "get_transcript"):
        assert by_name[name]["inputSchema"] == served[name]["inputSchema"]
        assert by_name[name]["description"] == served[name]["description"]
    assert "project_id" in by_name[NEW_TOOL]["inputSchema"]["required"]
    assert "expand_toolset(call=" in text
    assert DB["enqueued"] == []


def test_describe_names_near_matches_for_an_unknown_name(client, released):
    result = _call(client, "expand_toolset",
                   {"describe": ["apply_format_pak"]})["result"]
    assert result["isError"] is True
    assert "Near names: apply_format_pack" in result["content"][0]["text"]
    assert len(_call(client, "expand_toolset", {
        "describe": [f"t{i}" for i in range(mcpmod.DESCRIBE_MAX + 1)]})
        ["result"]["content"][0]["text"]) < 400


def test_domains_lists_current_department_verbs_and_the_call_path(
        client, released):
    result = _call(client, "expand_toolset",
                   {"project_id": 3, "domains": ["direction"]})["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is False
    assert f"direction: {NEW_TOOL}" in text
    assert "verify:" not in text
    assert "expand_toolset(describe=[names])" in text
    assert "expand_toolset(call=" in text

    everything = _call(client, "expand_toolset", {})["result"]
    text = everything["content"][0]["text"]
    assert "verify: suggest_devices" in text
    assert "projects, uploads, jobs and delivery:" in text and \
        "export_final" in text
    assert DB["enqueued"] == []

    bad = _call(client, "expand_toolset", {"domains": ["nonsense"]})["result"]
    assert bad["isError"] is True
    assert "Available: captions, direction, verify" in \
        bad["content"][0]["text"]


def test_domains_without_a_published_department_map_still_lists_names(
        client, monkeypatch):
    monkeypatch.setattr(mcpmod, "_catalog", lambda: OLD_CATALOG)
    text = _call(client, "expand_toolset", {"domains": ["captions"]}
                 )["result"]["content"][0]["text"]
    assert "CURRENT TOOLS" in text and "get_transcript" in text


def test_tools_list_advertises_describe_and_call(client, released):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"][
        "tools"]
    gateway = next(t for t in tools if t["name"] == "expand_toolset")
    props = gateway["inputSchema"]["properties"]
    assert {"domains", "describe", "call", "project_id"} <= set(props)
    assert gateway["inputSchema"]["required"] == []
    assert props["call"]["required"] == ["name"]
    assert "describe=[names]" in gateway["description"]
    assert "call={name, arguments}" in gateway["description"]
    assert not gateway["description"].startswith("PROJECT-SCOPED")
    # The worker's catalog row is passed through untouched.
    worker_row = next(t["function"] for t in NEW_CATALOG["tools"]
                      if t["function"]["name"] == "expand_toolset")
    assert "describe" not in worker_row["parameters"]["properties"]


# ── 2. the same gates, refusals and errors ───────────────────────────────

def test_index_gate_answers_the_gateway_exactly_as_the_direct_call(
        client, released, monkeypatch):
    monkeypatch.setattr(mcpmod, "_active_original",
                        lambda *a: {"sha256": "not-indexed", "id": 1})
    monkeypatch.setattr(mcpmod, "_index_row", lambda *a: None)
    args = {"project_id": 3, "pack": "documentary"}
    direct = _call(client, NEW_TOOL, dict(args))
    gateway = _via_gateway(client, NEW_TOOL, dict(args), _id=1)
    assert gateway == direct
    assert direct["result"]["isError"] is True
    assert "No editing job was started" in str(direct)
    assert DB["enqueued"] == []


def test_a_failed_write_reports_the_same_receipt(client, released):
    DB["job_row"] = {"id": 5, "type": "mcp_tool", "state": "failed",
                     "progress": 40, "error": "renderer crashed",
                     "project_id": 3, "result": None,
                     "payload": {"tool": NEW_TOOL, "mutation": True,
                                 "before_edl_version": 6,
                                 "mcp_call_id": "c1"}}
    args = {"project_id": 3, "pack": "documentary"}
    direct = _call(client, NEW_TOOL, dict(args))
    gateway = _via_gateway(client, NEW_TOOL, dict(args), _id=1)
    assert gateway == direct
    assert "Confirmed unchanged at v6" in direct["result"]["content"][0][
        "text"]


def test_owner_music_rule_refuses_the_gateway_like_the_direct_batch(
        client, released, api):
    """apply_edit_batch is written by the backend itself
    (apply_edit_batch_core): the owner's music rule must refuse a library
    track nobody picked whichever door the batch came through."""
    batch = _batch(_item(LIBRARY, provenance="user_picked_library"))
    direct = _call(client, "apply_edit_batch",
                   dict(copy.deepcopy(batch), project_id=2))
    gateway = _via_gateway(client, "apply_edit_batch",
                           dict(copy.deepcopy(batch), project_id=2),
                           outer=2, _id=1)
    assert gateway == direct
    assert direct["result"]["isError"] is True
    assert "agents never choose music" in direct["result"]["content"][0][
        "text"]
    assert list(api.edls) == [1]

    placed = _via_gateway(client, "apply_edit_batch",
                          dict(_batch(_item(UPLOAD)), project_id=2))
    assert placed["result"]["isError"] is False
    assert api.edls[2]["music"][0]["provenance"] == "user_upload"


@pytest.mark.parametrize("status,code", [(409, "repair_required"),
                                         (409, "verification_open")])
def test_export_gate_refusals_are_identical(client, released, monkeypatch,
                                            status, code):
    from flask import jsonify
    monkeypatch.setattr(mcpmod, "_request_final", lambda *a, **k: (
        jsonify(error="Blocked", code=code), status))
    args = {"project_id": 3, "edl_version": 9}
    direct = _call(client, "export_final", dict(args))
    gateway = _via_gateway(client, "export_final", dict(args), outer=3,
                           _id=1)
    assert gateway == direct
    assert direct["result"]["structuredContent"]["export"]["code"] == code


def test_errors_are_recorded_under_the_inner_tool(client, released,
                                                  monkeypatch):
    events = _events(monkeypatch)
    direct = _call(client, NEW_TOOL, {"pack": "documentary"})
    gateway = _via_gateway(client, NEW_TOOL, {"pack": "documentary"}, _id=1)
    assert gateway == direct
    assert "requires an explicit integer project_id" in \
        direct["result"]["content"][0]["text"]
    assert [e["tool"] for e in events] == [NEW_TOOL, NEW_TOOL]
    assert events[0] == events[1]


def test_denied_tools_stay_denied_through_the_gateway(client, released):
    for name in mcpmod.MCP_DENIED_TOOLS:
        direct = _call(client, name, {"project_id": 3})
        gateway = _via_gateway(client, name, {"project_id": 3}, _id=1)
        assert gateway == direct and direct["result"]["isError"] is True
    assert DB["enqueued"] == []


def test_session_tools_run_through_the_gateway(client, released):
    direct = _call(client, "list_projects", {})
    gateway = _via_gateway(client, "list_projects", {}, outer=3, _id=1)
    assert gateway == direct


# ── 3. the gateway's own refusals run nothing ────────────────────────────

@pytest.mark.parametrize("outer,inner", [(3, {"project_id": 9}),
                                         (3, {}),
                                         ("three", {"project_id": 3}),
                                         (3, {"project_id": "nine"})])
def test_project_scope_mismatch_is_refused(client, released, monkeypatch,
                                           outer, inner):
    events = _events(monkeypatch)
    result = _via_gateway(client, NEW_TOOL, dict(inner, pack="documentary"),
                          outer=outer)["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.startswith("CORRECTION NEEDED:") and "Nothing ran." in text
    assert "project_id" in text
    assert DB["enqueued"] == []
    assert [e["tool"] for e in events] == ["expand_toolset"]


def test_matching_outer_and_inner_scope_runs(client, released):
    result = _via_gateway(client, NEW_TOOL,
                          {"project_id": "3", "pack": "documentary"},
                          outer=3)["result"]
    assert result["isError"] is False
    assert len(DB["enqueued"]) == 1


def test_recursion_is_refused(client, released):
    for inner in ({"call": {"name": NEW_TOOL, "arguments": {
            "project_id": 3, "pack": "documentary"}}},
                  {"describe": [NEW_TOOL]}):
        result = _via_gateway(client, "expand_toolset", inner,
                              outer=3)["result"]
        assert result["isError"] is True
        assert "cannot call itself" in result["content"][0]["text"]
    assert DB["enqueued"] == []


def test_unknown_inner_tool_is_refused_with_near_names(client, released):
    result = _via_gateway(client, "apply_format_packs",
                          {"project_id": 3}, outer=3)["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True
    assert "no tool called 'apply_format_packs'" in text
    assert "Near names: apply_format_pack" in text
    assert DB["enqueued"] == []


@pytest.mark.parametrize("call", ["not json", {"arguments": {}},
                                  {"name": NEW_TOOL, "arguments": [1]},
                                  {"name": NEW_TOOL, "arguments": {},
                                   "project_id": 3}])
def test_malformed_call_is_refused(client, released, call):
    result = _call(client, "expand_toolset", {"call": call})["result"]
    assert result["isError"] is True
    assert "Nothing ran." in result["content"][0]["text"]
    assert DB["enqueued"] == []


def test_a_call_written_as_flat_or_json_text_is_understood(client, released):
    flat = _call(client, "expand_toolset", {"call": {
        "name": NEW_TOOL, "project_id": 3, "pack": "documentary"}})
    text = _call(client, "expand_toolset", {"call": json.dumps({
        "name": NEW_TOOL, "arguments": {"project_id": 3,
                                        "pack": "documentary"}})})
    assert flat["result"]["isError"] is False
    assert text["result"]["isError"] is False
    assert [p["args"] for p in DB["enqueued"]] == [{"pack": "documentary"}] * 2


def test_describe_and_call_together_is_refused(client, released):
    result = _call(client, "expand_toolset", {
        "describe": [NEW_TOOL],
        "call": {"name": NEW_TOOL, "arguments": {"project_id": 3}}})["result"]
    assert result["isError"] is True
    assert DB["enqueued"] == []


# ── 4. guidance and import paths ─────────────────────────────────────────

def test_instructions_teach_the_gateway(client, released):
    instructions = rpc(client, "initialize", STATIC_TOKEN).get_json()[
        "result"]["instructions"]
    flat = " ".join(instructions.split())
    assert "A TOOL YOUR LIST LACKS IS STILL CALLABLE" in flat
    assert 'expand_toolset(describe=["tool_name"])' in flat
    assert 'expand_toolset(call={"name": "tool_name"' in flat
    assert "reconnect to refresh the catalog" not in flat


def test_slow_request_log_names_the_inner_tool(client):
    app = client.application
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "expand_toolset", "arguments": {"call": {"name": NEW_TOOL}}}}
    with app.test_request_context("/mcp", method="POST", json=body):
        assert mcpmod._tool_names() == [f"expand_toolset>{NEW_TOOL}"]


def test_backend_file_path_imports_still_load():
    """routes.mcp and video_services.mcp_reads import worker modules by
    file path (no worker dependencies); a fresh interpreter proves the
    chain still loads with the gateway in it."""
    backend = Path(__file__).resolve().parents[1]
    code = ("import routes.mcp as m, video_services.mcp_reads as r;"
            "assert m.GATEWAY_TOOL == 'expand_toolset';"
            "assert r._skills.read_skill_text('devices');"
            "assert callable(r._reader.read_edl);print('ok')")
    env = dict(os.environ, SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub", PYTHONPATH=str(backend))
    out = subprocess.run([sys.executable, "-c", code], cwd=str(backend),
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")


def test_a_gateway_fault_is_a_tool_error_with_a_reference(client,
                                                          monkeypatch):
    def broken():
        raise RuntimeError("catalog read failed")
    monkeypatch.setattr(mcpmod, "_catalog", broken)
    result = _call(client, "expand_toolset", {"describe": [NEW_TOOL]})
    assert result["result"]["isError"] is True
    assert "Reference " in result["result"]["content"][0]["text"]
    assert DB["enqueued"] == []


def test_a_stale_client_sends_undeclared_arguments_as_json_text(client,
                                                                released):
    """Production, Oct 2026: a client whose cached expand_toolset schema
    lacks describe/call serializes them as strings. They must still work."""
    described = _call(client, "expand_toolset", {
        "project_id": 3, "domains": [],
        "describe": json.dumps([NEW_TOOL, "suggest_devices"])})["result"]
    assert described["isError"] is False
    names = [json.loads(line)["name"] for line in
             described["content"][0]["text"].splitlines()
             if line.startswith("{")]
    assert names == [NEW_TOOL, "suggest_devices"]
    assert _call(client, "expand_toolset", {
        "describe": f"{NEW_TOOL}, suggest_devices"})["result"][
        "isError"] is False
    listed = _call(client, "expand_toolset",
                   {"domains": '["verify"]'})["result"]
    assert "verify: suggest_devices" in listed["content"][0]["text"]

    args = {"project_id": 3, "pack": "documentary"}
    direct = _call(client, NEW_TOOL, dict(args))
    as_text = _call(client, "expand_toolset", {
        "project_id": 3, "domains": [],
        "call": json.dumps({"name": NEW_TOOL, "arguments": args})})
    assert as_text == direct
    assert [p["args"] for p in DB["enqueued"]] == [{"pack": "documentary"}] * 2
