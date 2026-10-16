"""The worker's side of the expand_toolset gateway (backend/routes/mcp.py).

A client whose cached tools/list predates a release reaches a newer tool
through expand_toolset(call={"name": T, "arguments": A}). The backend unwraps
it before queueing, so the mcp_tool job names T and A exactly as a direct
call's job does (backend/tests/test_mcp_gateway.py pins that payload). Here:

  * the rollout skew guard judges that job by T and A — and judges a payload
    that still carries the wrapper by the INNER call too, never by the
    always-known wrapper name;
  * the worker's own expand_toolset never executes describe/call (that would
    be a second, ungated path);
  * the published catalog carries the department map the gateway lists and
    the description that names describe and call;
  * the core compact catalog the in-house agent pays for is unchanged.
"""

import json

import pytest

import agent_tools
import mcp_exec
import remote
import skew_guard
import tools_ext

INNER = "apply_format_pack"          # a department verb (tools_ext)


def _gateway_job_payload(name, arguments):
    """The payload backend/routes/mcp.py queues for a gateway call: the
    inner tool and its arguments, project_id popped as transport scope."""
    args = {k: v for k, v in arguments.items() if k != "project_id"}
    return {"tool": name, "args": args, "mutation": True,
            "before_edl_version": 6, "mcp_call_id": "c1"}


def _wrapped(name, arguments):
    return {"tool": "expand_toolset", "args": {
        "domains": [], "call": {"name": name, "arguments": arguments}}}


@pytest.fixture
def older_release(monkeypatch):
    """This executor predates INNER (it is not in its registry)."""
    tools = dict(agent_tools.TOOLS)
    tools.pop(INNER)
    monkeypatch.setattr(agent_tools, "TOOLS", tools)


# ── the skew guard judges the INNER tool ─────────────────────────────────

def test_guard_passes_the_inner_call_this_release_can_run():
    payload = _gateway_job_payload(INNER, {"project_id": 3,
                                           "pack": "documentary"})
    assert skew_guard._mcp_problem(payload) is None
    assert skew_guard._mcp_problem(_wrapped(INNER, {
        "project_id": 3, "pack": "documentary"})) is None


def test_guard_refuses_an_inner_tool_this_release_lacks(older_release):
    payload = _gateway_job_payload(INNER, {"project_id": 3,
                                           "pack": "documentary"})
    assert skew_guard._mcp_problem(payload) == \
        f"the tool {INNER} is not in this release"
    # A wrapper that reached a worker is judged by what it wraps.
    assert skew_guard._mcp_problem(_wrapped(INNER, {"project_id": 3})) == \
        f"the tool {INNER} is not in this release"


def test_guard_refuses_an_inner_argument_this_release_lacks():
    payload = _gateway_job_payload(INNER, {"project_id": 3,
                                           "newer_knob": 1})
    assert skew_guard._mcp_problem(payload) == \
        f"{INNER} has no argument newer_knob in this release"
    assert skew_guard._mcp_problem(_wrapped(INNER, {
        "project_id": 3, "newer_knob": 1})) == \
        f"{INNER} has no argument newer_knob in this release"


def test_skewed_executor_waits_for_the_rollout_on_the_inner_tool(
        older_release, monkeypatch):
    monkeypatch.setattr(skew_guard.version, "code_version", lambda: "old")
    job = {"id": 7, "type": "mcp_tool", "project_id": None,
           "dispatcher_source": "new",
           "payload": _gateway_job_payload(INNER, {"project_id": 3,
                                                   "pack": "documentary"})}
    with pytest.raises(skew_guard.RolloutPending) as refused:
        skew_guard.check(None, job, {"mcp_tool": object()})
    assert INNER in str(refused.value) and "nothing ran" in str(refused.value)


def test_dispatcher_verdict_names_the_inner_problem(older_release):
    verdict = remote._dispatcher_mcp_verdict(
        {"type": "mcp_tool", "payload": _wrapped(INNER, {"project_id": 3})})
    assert verdict == {"dispatcher_mcp_problem":
                       f"the tool {INNER} is not in this release"}


def test_malformed_or_recursive_wrappers_are_judged_as_the_wrapper():
    """Nothing to unwrap: this release's expand_toolset accepts (and
    refuses, running nothing) the argument, so there is no rollout to wait
    for."""
    assert skew_guard._mcp_problem(
        {"tool": "expand_toolset", "args": {"call": "nonsense"}}) is None
    assert skew_guard._mcp_problem(_wrapped("expand_toolset", {})) is None


# ── the worker never runs describe/call itself ───────────────────────────

class _Ctx:
    def __init__(self):
        self._loaded_tool_domains = set()
        self._loaded_tool_names = set()


@pytest.mark.parametrize("kwargs", [
    {"call": {"name": INNER, "arguments": {"pack": "documentary"}}},
    {"describe": [INNER]},
    {"domains": ["verify"], "call": {"name": INNER}},
])
def test_worker_refuses_gateway_arguments_without_running_anything(kwargs):
    ctx = _Ctx()
    out = agent_tools.expand_toolset(ctx, **kwargs)
    assert out.startswith("CORRECTION NEEDED:") and "nothing ran" in out
    assert ctx._loaded_tool_domains == set()
    assert ctx._loaded_tool_names == set()


def test_worker_alias_still_loads_domains():
    ctx = _Ctx()
    assert "Capabilities loaded" in agent_tools.expand_toolset(ctx, ["verify"])
    assert ctx._loaded_tool_domains == {"verify"}


# ── the published catalog ────────────────────────────────────────────────

def test_catalog_publishes_current_names_per_department():
    cat = mcp_exec.catalog()
    assert json.loads(json.dumps(cat)) == cat
    published = {t["function"]["name"] for t in cat["tools"]}
    domains = cat["domains"]
    assert set(domains) == set(agent_tools.TOOL_DOMAINS)
    for dept, names in domains.items():
        assert set(names) <= published, dept
        assert names == sorted(names)
    # Department (tools_ext) verbs are listed under their department.
    for dept, names in tools_ext.EXT_DEPARTMENTS.items():
        live = {n for n in names if not agent_tools._tool_disabled(n)}
        assert live <= set(domains[dept]), dept
    assert INNER in domains["direction"]
    assert "expand_toolset" not in set().union(*map(set, domains.values()))


def test_catalog_description_names_describe_and_call():
    row = next(t["function"] for t in mcp_exec.catalog()["tools"]
               if t["function"]["name"] == "expand_toolset")
    assert "describe=[names]" in row["description"]
    assert "call={name, arguments}" in row["description"]


def test_core_compact_catalog_is_unchanged_and_under_the_ceiling():
    core = tools_ext.core_names(agent_tools.TOOLS)
    cat = agent_tools.openai_tools("any-model", compact=True, names=core)
    size = len(json.dumps(cat, separators=(",", ":")))
    assert size <= 82_700 < 83_000, size
    row = next(t["function"] for t in cat
               if t["function"]["name"] == "expand_toolset")
    # The in-house agent never sees expand_toolset; its compact line keeps
    # the bytes it had, so the gateway costs the core budget nothing.
    assert row["description"] == (
        "Load additional tool schemas for the NEXT reasoning step when the "
        "compact catalog does not...")
    assert set(row["parameters"]["properties"]) == {"domains"}
