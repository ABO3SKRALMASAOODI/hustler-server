"""The MCP surface's invariants.

MCP hands the editor to an outside model (Claude in the user's own Claude Code
session). The whole design rests on there being exactly ONE tool registry, so
these tests guard the seams where a second one could grow: the catalog the
worker publishes, the job type the queue must accept, and the names the
backend adds on top of it.
"""

import ast
import json
import os

import agent_tools
import mcp_exec

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
BACKEND_MCP = os.path.join(REPO, "backend", "routes", "mcp.py")
MIGRATION = os.path.join(REPO, "backend", "migrations", "008_mcp.sql")


def _session_tool_names():
    """The names backend/routes/mcp.py declares itself, read out of the source
    (the module imports Flask + the whole backend, which the worker image does
    not have)."""
    tree = ast.parse(open(BACKEND_MCP).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SESSION_TOOLS"
                for t in node.targets):
            names = []
            for item in node.value.elts:
                for k, v in zip(item.keys, item.values):
                    if getattr(k, "value", None) == "name":
                        names.append(v.value)
            return set(names)
    raise AssertionError("SESSION_TOOLS not found in backend/routes/mcp.py")


def test_catalog_is_the_live_registry():
    cat = mcp_exec.catalog()
    names = [t["function"]["name"] for t in cat["tools"]]
    assert names, "no tools published"
    # Every published tool really exists and really runs here.
    for n in names:
        assert n in agent_tools.TOOLS
        assert not agent_tools._tool_disabled(n), \
            f"{n} is published but disabled on this deployment"
    # ...and nothing enabled was left out except explicit orchestration tools.
    # The outside model must do the edit itself, never enqueue our agent.
    enabled = [n for n in agent_tools.TOOLS
               if not agent_tools._tool_disabled(n)
               and n not in mcp_exec.MCP_DENIED_TOOLS]
    assert sorted(names) == sorted(enabled)


def test_catalog_is_json_round_trippable():
    """It travels through a JSONB column, so anything non-serializable here
    fails at worker boot with the surface silently unavailable."""
    cat = mcp_exec.catalog()
    assert json.loads(json.dumps(cat)) == cat
    assert cat["system_prompt"] and cat["capabilities"]


def test_every_tool_has_a_schema_the_client_can_read():
    for t in mcp_exec.catalog()["tools"]:
        params = t["function"]["parameters"]
        assert params["type"] == "object"
        assert isinstance(params.get("properties"), dict)
        for req in params.get("required", []):
            assert req in params["properties"], \
                f"{t['function']['name']} requires an undeclared argument"


def test_session_tools_never_shadow_an_editor_tool():
    """A collision would silently replace a real editing tool with the
    backend's own — the model would call cut_range and get project plumbing."""
    assert not (_session_tool_names() & set(agent_tools.TOOLS))


def test_migration_allows_the_job_type():
    """video_jobs.type has a CHECK constraint. Without the migration every MCP
    call fails at the INSERT, before any tool runs."""
    sql = open(MIGRATION).read()
    assert "mcp_tool" in sql
    assert "video_jobs_type_check" in sql


def test_worker_dispatches_the_job_type():
    main_src = open(os.path.join(REPO, "worker", "main.py")).read()
    assert '"mcp_tool": mcp_exec.run_mcp_job' in main_src
    assert 'MCP_TYPES = ("mcp_tool",)' in main_src


def test_control_tools_are_not_offered_to_the_model():
    published = {t["function"]["name"] for t in mcp_exec.catalog()["tools"]}
    assert mcp_exec.CATALOG_TOOL not in published
    assert mcp_exec.STATE_TOOL not in published
    assert mcp_exec.MEDIA_TOOL not in published


def test_mcp_context_authoritatively_disables_audio_model_review(
        monkeypatch, tmp_path):
    class Db:
        @staticmethod
        def run(fn, *_args, **_kwargs):
            if fn is mcp_exec.dbx.latest_creative_blueprint:
                return None
            if fn is mcp_exec.dbx.user_billing:
                return False, "free", False
            raise AssertionError(f"unexpected DB call: {fn.__name__}")

    monkeypatch.setattr(mcp_exec.config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(
        mcp_exec.llm, "agent_client_for",
        lambda _subscribed, _plan: (object(), "outside-codex"))
    session = mcp_exec._new_context(
        Db(), {"id": 91, "user_id": 4},
        {"id": 12, "chat_session_id": 13},
        {"video": {"duration": 10.0}}, "source-sha")

    assert session.ctx.sight_out is True
    assert session.ctx.audio_model_review is False


def test_locked_card_agent_boot_is_not_published_or_executable_over_mcp():
    published = {t["function"]["name"]
                 for t in mcp_exec.catalog()["tools"]}
    assert "edit_shorts" not in published
    assert "make_shorts" in published
    # The restriction is an MCP boundary, never a removal from Valmera's
    # internal agent registry.
    assert "edit_shorts" in agent_tools.TOOLS
    assert not agent_tools._tool_disabled("edit_shorts")

    class NoDb:
        def run(self, *_args, **_kwargs):
            raise AssertionError("denied MCP tool must not touch the database")

    out = mcp_exec.run_mcp_job(NoDb(), {
        "payload": {"tool": "edit_shorts", "args": {"instruction": "x"}},
    })
    assert out["is_error"] is True
    assert "explicit locked-card action" in out["text"]
    assert "Edit each child yourself" in out["text"]


def test_mcp_project_state_routes_batch_edits_to_direct_child_tools(monkeypatch):
    class Ctx:
        index = None
        has_main_video = False
        project_id = 7
        duration = 0
        project = {"id": 7, "kind": "shorts", "parent_project_id": None}
        edit_plan = None

        @staticmethod
        def latest_edl():
            return {"version": 1, "json": {"keep": []}}

    class Db:
        @staticmethod
        def run(*_args, **_kwargs):
            return []

    monkeypatch.setattr(agent_tools, "_shorts_children", lambda _ctx: [{
        "title": "First", "start": 0, "end": 20, "child_project_id": 8,
    }])
    direct = mcp_exec.agent_loop.state_block(
        Ctx(), Db(), denied_tools=mcp_exec.MCP_DENIED_TOOLS)
    internal = mcp_exec.agent_loop.state_block(Ctx(), Db())

    assert "edit_shorts" not in direct
    assert "open_short" in direct
    assert "make every requested change yourself" in direct
    assert "edit_shorts" in internal
