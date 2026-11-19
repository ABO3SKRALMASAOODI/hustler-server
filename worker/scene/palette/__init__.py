"""worker/scene/palette: one palette of devices for every workflow. A model (the Director) chooses, per moment,
which devices a film carries; this package compiles each choice into plain scene items (the Score's L1) that the
engine draws. Nothing here reads a model or a meaning: which moment, which picture and which device come from the
asks; where exactly, how strong and on which pixels come from signal (``sense``).

Every module of this package declares ``DEVICES``: name -> {"card", "needs", "plan", "fallback", "layer"}. Modules
are found by themselves (pkgutil), so a lane adds a family by adding a file and never edits a shared list.

    from scene import palette
    palette.DEVICES                       # name -> {"card", "needs", "plan", "fallback", "layer", "family"}
    palette.menu()                        # one card a device, for the Director's prompt
    l1, log = palette.apply(l1, work)     # the asks under l1["devices"] and l1["alien"], laid in layer order

An ask is {"dev": name, "id": "d1", "f": [a, b] | "at": frame, "until": frame | "t": s, "t_end": s, "pics": [ids],
"args": {...}} (arguments may also sit on the ask itself). ``plan(l1, work, ask, sense)`` returns a patch: {"items",
"drop", "assets", "voices", "film", "log"}, or {"skip": why} when this film cannot carry it. A device that cannot
stand runs its fallback chain; the last link of every chain draws on any film, so an ask never leaves its moment
bare. A patch that would break the scene's contract (``plan.eligible``) is not merged and the next link runs.
Every repair is logged; nothing is refused.

No device emits sound. Each landing is listed in the log with its frame, kind and strength (``events``), for the
music and sound workflow to find the moments worth amplifying.
"""
import copy
import importlib
import pkgutil

from ..plan import eligible

LAYERS = ("rhythm", "surface", "type", "hud", "alien", "look")      # merge order: base-replacing first, the look last
DEVICES = {}
FAMILIES = {}
SKIP = {"run", "sense"}


def _discover():
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if m.name in SKIP or m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{m.name}")
        for name, d in (getattr(mod, "DEVICES", None) or {}).items():
            if name in DEVICES:
                continue
            DEVICES[name] = dict(d, family=m.name, layer=d.get("layer") or getattr(mod, "LAYER", "surface"))
            FAMILIES.setdefault(m.name, []).append(name)


_discover()


def menu(names=None):
    """One card a device: its fields, what it does, the moment it amplifies. For the Director's prompt."""
    out = []
    for name in sorted(DEVICES, key=lambda n: (LAYERS.index(DEVICES[n]["layer"]) if DEVICES[n]["layer"] in LAYERS else 9, n)):
        if names is None or name in names:
            out.append(f"{name}: {DEVICES[name]['card']}")
    return "\n".join(out)


def resolve(name):
    """A device name as asked: the device itself, else the first device of a family of that name, else a device
    whose name starts with it. None when nothing is near."""
    if name in DEVICES:
        return name
    if name in FAMILIES:
        return FAMILIES[name][0]
    near = sorted(n for n in DEVICES if n.startswith(str(name or "")[:4])) if name else []
    return near[0] if near else None


def plan(l1, work, ask, sense=None):
    """One device's patch, nothing merged."""
    from .sense import Sense
    name = resolve(ask.get("dev"))
    if name is None:
        return {"skip": f"no device near {ask.get('dev')!r}"}
    return DEVICES[name]["plan"](l1, work, dict(ask, dev=name), sense or Sense(l1, work))


def merge(l1, patch):
    """A scene with a patch laid in: dropped ids leave, items arrive (an id that stayed is replaced)."""
    out = dict(l1)
    gone = set(patch.get("drop") or ())
    new = patch.get("items") or []
    ids = {i["id"] for i in new}
    out["items"] = [i for i in l1.get("items") or [] if i["id"] not in gone and i["id"] not in ids] + new
    for key in ("assets", "voices", "film"):
        if patch.get(key):
            out[key] = dict(l1.get(key) or {}, **patch[key])
    return out


def chain_of(name):
    """The device, then its fallbacks, then theirs: each name once."""
    seen, todo = [], [name]
    while todo:
        n = resolve(todo.pop(0))
        if n and n not in seen:
            seen.append(n)
            todo.extend(DEVICES[n].get("fallback") or ())
    return seen


def apply(l1, work, asks=None, sense=None):
    """(scene, log). Every ask in layer order (``LAYERS``), each through its fallback chain until one link stands
    on this film. The scene handed in is not changed."""
    from .sense import Sense
    if asks is None:
        asks = list(l1.get("devices") or []) + [dict(a, dev=a.get("dev")) for a in (l1.get("alien") or [])]
    cur = copy.deepcopy({k: v for k, v in l1.items() if k not in ("devices", "alien")})
    sense = sense or Sense(cur, work)
    log = []

    def rank(t):
        n = resolve(t[1].get("dev"))
        lay = DEVICES[n]["layer"] if n else "look"
        return (LAYERS.index(lay) if lay in LAYERS else 9, t[0])
    for n, ask in sorted(enumerate(asks), key=rank):
        ask = dict(ask, id=str(ask.get("id") or f"pd{n}"))
        row = {"id": ask["id"], "asked": ask.get("dev"), "tried": []}
        for name in chain_of(ask.get("dev")) or []:
            sense.l1 = cur
            try:
                patch = DEVICES[name]["plan"](cur, work, dict(ask, dev=name), sense)
            except Exception as e:      # noqa: BLE001 - a device is never the reason a film does not render
                patch = {"skip": f"{type(e).__name__}: {e}"}
            if patch.get("skip"):
                row["tried"].append({"dev": name, "why": patch["skip"]})
                continue
            nxt = merge(cur, patch)
            ok, why = eligible(nxt)
            if not ok:
                row["tried"].append({"dev": name, "why": "the patch broke the scene: " + "; ".join(why[:3])})
                continue
            cur = nxt
            row.update(done=True, dev=name, log=patch.get("log"), events=patch.get("events") or [])
            break
        else:
            row["done"] = False
        log.append(row)
    return cur, log


def events(log):
    """Every landing the devices laid, in frame order: {f, kind, strength, dev} (for the sound workflow)."""
    out = []
    for r in log:
        for e in r.get("events") or []:
            out.append(dict(e, dev=r.get("dev")))
    return sorted(out, key=lambda e: e.get("f", 0))
