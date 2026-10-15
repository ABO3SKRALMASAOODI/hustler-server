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


def _edl_problem(data, strip_unknown_fx=False):
    """Why this executor's schema cannot read ``data``, or None.

    ``strip_unknown_fx`` applies the render path's own Wave 0 degrade rule
    (schemas.strip_unknown_fx: a lane older than the writer drops an fx
    device it does not register and reports it, rather than failing). It is
    used for previews only; a final waits for the release that draws every
    device, and an editor never edits a document it cannot fully read.
    """
    if not isinstance(data, dict):
        return None
    import schemas
    if strip_unknown_fx and hasattr(schemas, "strip_unknown_fx"):
        try:
            data = schemas.strip_unknown_fx(data)[0]
        except Exception:
            pass
    try:
        model = schemas.EDL.model_validate(data)
    except Exception as exc:
        first = " ".join(str(exc).split())[:160]
        return f"its EDL uses fields this release does not know: {first}"
    unknown = []
    _unknown_keys(data, model.model_dump(), model.model_dump(by_alias=True),
                  "", unknown)
    if unknown:
        shown = ", ".join(sorted(set(unknown))[:4])
        return f"its EDL uses fields this release does not know: {shown}"
    return None


def _unknown_keys(raw, kept, kept_alias, path, out):
    """Append every key of ``raw`` this schema would silently drop.

    ``kept``/``kept_alias`` are the validated model dumped by field name and
    by alias. A raw dict facing no dumped dict is a free-form value (a
    dict-typed field) and is not inspected further.
    """
    if isinstance(raw, dict):
        if not isinstance(kept, dict) and not isinstance(kept_alias, dict):
            return
        a = kept if isinstance(kept, dict) else {}
        b = kept_alias if isinstance(kept_alias, dict) else {}
        for key, value in raw.items():
            if key not in a and key not in b:
                out.append(f"{path}.{key}" if path else str(key))
            else:
                _unknown_keys(value, a.get(key), b.get(key),
                              f"{path}.{key}" if path else str(key), out)
    elif isinstance(raw, list):
        a = kept if isinstance(kept, list) else []
        b = kept_alias if isinstance(kept_alias, list) else []
        for i, value in enumerate(raw):
            _unknown_keys(value, a[i] if i < len(a) else None,
                          b[i] if i < len(b) else None, f"{path}[]", out)


def _mcp_problem(payload):
    """Why this executor cannot run this MCP tool call, or None."""
    tool = str(payload.get("tool") or "")
    if not tool or tool in _MCP_CONTROL_TOOLS:
        return None
    import agent_tools
    args = payload.get("args")
    if not isinstance(args, dict):
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
        if jtype == "mcp_tool":
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
