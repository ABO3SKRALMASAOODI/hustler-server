"""Department tools: new agent verbs without editing agent_tools.py.

Wave 0 interface pass (worker/devices/INTERFACES.md §7). agent_tools.py calls
``install(module)`` exactly once, at the end of its import. Every
``tools_ext/<name>.py`` whose name does not start with ``_`` is a department
module and exports:

  DEPARTMENT     one of DEPARTMENTS below
  TOOL_SPECS     {name: (fn, description, properties)} — the core TOOLS shape;
                 fn(ctx, **args) -> str, exactly like a core tool
  REQUIRED_ARGS  optional {name: [arg, ...]}
  WRITE_TOOLS    optional set of names whose success is an "EDL vX -> vY" line
  COMPACT        optional {name: contract}: the compact description (else the
                 first sentence of the full one), <= COMPACT_MAX_CHARS
  DISABLED       optional {name: () -> bool}: honest-off, like
                 agent_tools._tool_disabled (a raising predicate hides it)

Department tools are registered in TOOLS (so dispatch, MCP and load_tools
work) and in TOOL_DOMAINS[DEPARTMENT]. They are NOT part of the always-sent
core compact catalog whose 83,000-byte budget tests/test_agent_latency.py
pins: the in-house agent receives them only after load_tools(domains=[...])
or load_tools(names=[...]); MCP clients receive the full catalog. Each
department's compact schemas have their own budget (DEPARTMENT_COMPACT_BUDGET).

A department becomes visible (TOOL_DOMAINS row, load_tools enum) only when it
holds at least one tool. Its name then joins the load_tools / expand_toolset
domain enums, which ARE in the core catalog: that growth (len(',"<name>"')
bytes per enum) is charged to the core budget.
"""

import importlib
import os

DEPARTMENTS = ("compositing", "rhythm", "world", "verify",
               # Wave 4 (4E): aspects, languages, format packs
               "delivery")
DEPARTMENT_COMPACT_BUDGET = 12_000   # bytes of compact schemas per department
TOOL_FULL_MAX_BYTES = 6_000          # one tool's full JSON schema + prose
COMPACT_MAX_CHARS = 600

# Filled by install(); read by agent_tools._tool_disabled and the tests.
EXT_TOOL_NAMES = set()
EXT_DEPARTMENTS = {}
DISABLED = {}

_DIR = os.path.dirname(os.path.abspath(__file__))


def discover():
    """The department modules, imported in file-name order."""
    mods = []
    for fname in sorted(os.listdir(_DIR)):
        if fname.endswith(".py") and not fname.startswith("_"):
            mods.append(importlib.import_module(f"{__name__}.{fname[:-3]}"))
    return mods


def is_disabled(name):
    """None when `name` is not a department tool; else its honest-off."""
    pred = DISABLED.get(name)
    if pred is None:
        return None if name not in EXT_TOOL_NAMES else False
    try:
        return bool(pred())
    except Exception:
        return True


def core_names(tools):
    """The core catalog: every registered tool that is not a department
    tool. The compact-catalog budget is measured over exactly these."""
    return [name for name in tools if name not in EXT_TOOL_NAMES]


def _check_spec(mod_name, name, spec):
    import json
    if not (isinstance(spec, tuple) and len(spec) == 3 and callable(spec[0])
            and isinstance(spec[1], str) and isinstance(spec[2], dict)):
        raise TypeError(f"tools_ext/{mod_name}: {name} must be "
                        "(fn, description, properties)")
    size = len(json.dumps({"name": name, "description": spec[1],
                           "properties": spec[2]}, separators=(",", ":")))
    if size > TOOL_FULL_MAX_BYTES:
        raise ValueError(f"tools_ext/{mod_name}: {name} schema is {size} "
                         f"bytes (cap {TOOL_FULL_MAX_BYTES})")


def install(at, modules=None):
    """Register every department module into the agent_tools module `at`.

    Idempotent per tool name. Raises on a collision with a core tool, an
    unknown department or an oversized spec, so a bad module fails CI at
    import instead of reaching a customer turn.
    """
    for mod in (discover() if modules is None else modules):
        mod_name = getattr(mod, "__name__", "?").rsplit(".", 1)[-1]
        dept = getattr(mod, "DEPARTMENT", None)
        if dept not in DEPARTMENTS:
            raise ValueError(f"tools_ext/{mod_name}: DEPARTMENT must be one "
                             f"of {DEPARTMENTS}, not {dept!r}")
        specs = dict(getattr(mod, "TOOL_SPECS", None) or {})
        if not specs:
            continue
        for name, spec in specs.items():
            if name in at.TOOLS and name not in EXT_TOOL_NAMES:
                raise ValueError(f"tools_ext/{mod_name}: {name} collides "
                                 "with a core tool")
            _check_spec(mod_name, name, spec)
        compact = dict(getattr(mod, "COMPACT", None) or {})
        for name, text in compact.items():
            if name not in specs or len(text) > COMPACT_MAX_CHARS:
                raise ValueError(f"tools_ext/{mod_name}: COMPACT[{name}] must "
                                 f"name its own tool, <= {COMPACT_MAX_CHARS} "
                                 "chars")
        at.TOOLS.update(specs)
        EXT_TOOL_NAMES.update(specs)
        EXT_DEPARTMENTS.setdefault(dept, set()).update(specs)
        at.TOOL_DOMAINS.setdefault(dept, set()).update(specs)
        at.REQUIRED_ARGS.update(getattr(mod, "REQUIRED_ARGS", None) or {})
        at.WRITE_TOOLS.update(getattr(mod, "WRITE_TOOLS", None) or ())
        at._COMPACT_CONTRACTS.update(compact)
        DISABLED.update(getattr(mod, "DISABLED", None) or {})
        if dept not in at.TOOL_DOMAIN_NAMES:
            at.TOOL_DOMAIN_NAMES.add(dept)
            at.AGENT_TOOL_DOMAINS = tuple(at.AGENT_TOOL_DOMAINS) + (dept,)
            for tool in ("load_tools", "expand_toolset"):
                entry = at.TOOLS.get(tool)
                enum = (((entry or (None, None, {}))[2].get("domains") or {})
                        .get("items") or {}).get("enum")
                if isinstance(enum, list) and dept not in enum:
                    enum.append(dept)
