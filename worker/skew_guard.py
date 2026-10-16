"""Version-skew guard: may THIS executor run a job written by other code?

THE PROBLEM. Render deploys the dispatcher and the backend 3-5 minutes after a
push; the Cloudflare executor follows 25-60 minutes later (CI tests, two image
builds, a staged container rollout), and a failed Cloudflare deploy leaves it
behind until the next good one. Until Oct 2026 the dispatcher refused every
call while the two source fingerprints differed, so every worker deploy turned
into 20-60 minutes of "Deploy in progress" failures for every user (49 of the
72 failed jobs in the week to 15 Oct). The old executor was perfectly able to
run almost all of those jobs.

THE RULE. Skew is not a reason to refuse. It is a reason to check, before any
work, the few things an older executor cannot do safely:

  * a job type it has no runner for;
  * an MCP tool it does not have, or an argument its tool function would not
    accept (a newer release added a tool or a parameter);
  * an EDL its own schema rejects, or one carrying any field its schema
    does not declare. The EDL models IGNORE unknown fields (pydantic's
    default), so an older release would otherwise render without a newer
    device and, worse, an older editor would drop that field from the next
    version it saves. The guard compares the stored document with what this
    release's schema keeps of it: a key that would vanish is a newer field.
    (On 3,000 production EDLs read by their own release this finds nothing.)

Anything that passes runs normally on the older code, which is what production
ran minutes earlier. Anything that fails is refused with the structured,
retryable ``provider_rollout_pending`` outcome and NOTHING has run, so the
dispatcher waits for the rollout and launches the same claim again under a
fresh call identity. A newer executor serving an older dispatcher reads old
data, which every release must already do (old EDLs stay valid).

The guard engages only when the payload names a dispatcher source that differs
from this process's own fingerprint; a dispatcher that predates this module
sends none, and same-source jobs never pay for a check.
"""

import inspect

import version

# Advertised by version.FEATURES (container /health) and by the Cloudflare
# Worker's root /health. The Worker serves a stale container only when it
# carries this guard; the dispatcher tolerates source skew only when the
# Worker advertises it.
FEATURE = "skew_guard"

# Jobs that read the project's newest EDL before doing anything else.
_LATEST_EDL_TYPES = frozenset({"mcp_tool", "agent_turn", "shorts_plan",
                               "filmstrip"})
# Jobs that render one immutable EDL version named in the payload.
_VERSIONED_EDL_TYPES = frozenset({"preview", "preview_check", "final"})
# MCP control tools handled by mcp_exec itself, not the tool registry.
_MCP_CONTROL_TOOLS = frozenset({"__catalog__", "__state__", "__media__"})


class RolloutPending(RuntimeError):
    """This executor is older (or newer) than the code that wrote the job.

    Raised before any work. Carries the established structured-failure
    attributes so failure_policy and the dispatcher treat it as a pre-compute
    rollout wait, never as a failed attempt.
    """

    failure_kind = "provider_rollout_pending"
    retryable = True
    max_attempts = 1
    agent_repairable = False
    skew_refused = True


def skew(job):
    """(dispatcher_source, executor_source) when they differ, else None."""
    sent = str((job or {}).get("dispatcher_source") or "").strip()
    try:
        mine = version.code_version()
    except Exception:
        mine = "unknown"
    if not sent or sent == "unknown" or mine == "unknown" or sent == mine:
        return None
    return sent, mine


def _refuse(job, why, sources):
    sent, mine = sources
    kind = str((job or {}).get("type") or "job")
    return RolloutPending(
        f"Deploy in progress: this {kind} needs the release that is rolling "
        f"out now ({why}); nothing ran. It will start on the new release "
        f"automatically. (executor {mine}, dispatcher {sent})")


def _edl_json(conn, project_id, edl_version=None):
    with conn.cursor() as cur:
        if edl_version is None:
            cur.execute("""SELECT json FROM edls WHERE project_id = %s
                           ORDER BY version DESC LIMIT 1""", (project_id,))
        else:
            cur.execute("""SELECT json FROM edls
                           WHERE project_id = %s AND version = %s""",
                        (project_id, int(edl_version)))
        row = cur.fetchone()
    if not row:
        return None
    value = row["json"] if isinstance(row, dict) else row[0]
    return value


# The EDL check lives in read_tools: the backend's MCP fast path applies
# the same rule before it answers a read from its own release.
from read_tools import _unknown_keys  # noqa: E402,F401
from read_tools import edl_problem as _edl_problem  # noqa: E402,F401


def _gateway_inner(tool, args):
    """(tool, args) of the call an MCP gateway payload carries, else as is.

    expand_toolset(call={"name": T, "arguments": A}) is how a client whose
    cached tools/list predates T reaches it. The backend unwraps it before
    queueing, so its job already names T; a payload that still carries the
    wrapper (a backend mid-deploy) is judged by the INNER tool and
    arguments, never by the wrapper's always-known name. project_id is the
    MCP transport scope, popped before queueing, never a tool argument.
    """
    if tool != "expand_toolset" or not isinstance(args, dict) \
            or "call" not in args:
        return tool, args
    call = args.get("call")
    if not isinstance(call, dict) or not isinstance(call.get("name"), str):
        return tool, args
    inner = call.get("arguments", call.get("args"))
    if inner is None:
        inner = {k: v for k, v in call.items() if k != "name"}
    if not isinstance(inner, dict):
        return tool, args
    inner = {k: v for k, v in inner.items() if k != "project_id"}
    return call["name"].strip(), inner


def _mcp_problem(payload):
    """Why this executor cannot run this MCP tool call, or None."""
    tool = str(payload.get("tool") or "")
    if not tool or tool in _MCP_CONTROL_TOOLS:
        return None
    import agent_tools
    args = payload.get("args")
    if not isinstance(args, dict):
        return None
    tool, args = _gateway_inner(tool, args)
    if not tool or tool in _MCP_CONTROL_TOOLS:
        return None
    try:
        name, normalized, _notes = agent_tools._normalize_tool_call(tool, args)
    except Exception:
        name, normalized = tool, args
    entry = agent_tools.TOOLS.get(name)
    if entry is None:
        try:
            import mcp_exec
            if tool in mcp_exec.MCP_DENIED_TOOLS:
                return None
        except Exception:
            pass
        return f"the tool {tool} is not in this release"
    try:
        params = inspect.signature(entry[0]).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    unknown = sorted(k for k in normalized if k not in params)
    if unknown:
        return (f"{name} has no argument {', '.join(unknown[:4])} in this "
                "release")
    return None


def check(worker_db, job, runners):
    """Raise RolloutPending when this executor must not run ``job``.

    ``worker_db`` is the executor's leased database handle (``run(fn, ...)``).
    Any failure of the check itself is not proof of incompatibility: it is
    logged and the job runs, exactly as it did before this guard existed.
    """
    sources = skew(job)
    if sources is None:
        return
    jtype = str(job.get("type") or "")
    if jtype not in runners:
        raise _refuse(job, f"job type {jtype or '?'} is not in this release",
                      sources)
    payload = job.get("payload") or {}
    try:
        problem = None
        # A call the dispatcher's own release cannot run either (it says so
        # in dispatcher_mcp_problem) is invalid everywhere: this release
        # answers it at once rather than waiting out the rollout for the
        # same correction.
        if jtype == "mcp_tool" and not job.get("dispatcher_mcp_problem"):
            problem = _mcp_problem(payload)
        if problem is None and job.get("project_id") is not None:
            if jtype in _VERSIONED_EDL_TYPES \
                    and payload.get("edl_version") is not None:
                data = worker_db.run(_edl_json, job["project_id"],
                                     payload.get("edl_version"))
                problem = _edl_problem(data, strip_unknown_fx=jtype in (
                    "preview", "preview_check"))
            elif jtype in _LATEST_EDL_TYPES:
                data = worker_db.run(_edl_json, job["project_id"])
                problem = _edl_problem(data)
    except RolloutPending:
        raise
    except Exception as exc:
        print(f"[skew] guard check skipped for {jtype} job={job.get('id')}: "
              f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return
    if problem:
        raise _refuse(job, problem, sources)
    print(f"[skew] {jtype} job={job.get('id')} runs on executor "
          f"{sources[1]} for dispatcher {sources[0]}: compatible", flush=True)
