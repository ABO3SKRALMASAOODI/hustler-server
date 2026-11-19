"""worker/scene/palette: every device the scene engine can already draw, behind one registry, so a workflow can ask
for any of them on any moment of any film. The palette compiles: a model (the Director, ``_shell/director.py``)
chooses which device lands on which moment; nothing here reads a word or a meaning, and nothing here refuses.

THE CONTRACT (CONTRACT 1, 20 Nov 2026; lanes build against it)

A family module in this package declares ``DEVICES = {name: entry}``. Modules are found with pkgutil, so a new
family is a new file and no shared list is edited. An entry is

    {"card":     "name field field ... . What it does, and the kind of moment it amplifies",   (one line, for menus)
     "layer":    one of LAYERS (when it is laid, see below),
     "needs":    ("take", "matte", "pictures", "cards", "words", "faces", "rounds", "depth", "voice"),   (what it reads)
     "plan":     plan(l1, work, ask, sense) -> patch,
     "fallback": ("other", "devices"),       (tried in turn when this one cannot stand on the film)
     "family":   "punct" | "finish" | "rhythm" | "type" | "hud" | "alien" | ...}

An ask is a dict in programme frames: ``{"dev": name, "id": "d3", "f": [a, b] (or "at": a), ...the device's own
fields}``. A workflow resolves the Director's sentence-and-word addresses to frames before it hands asks here.

A patch is ``{"items": [...], "drop": [ids], "assets": {...}, "voices": {...}, "film": {...},
"events": [{"f", "kind", "strength"}], "lum": [frames], "log": {...}}``: plain scene items, nothing drawn here.
An item whose id is already in the scene replaces it in place; a dropped id leaves. ``events`` are the landings the
separate sound workflow may answer (no device emits sound). ``lum`` lists the frames on which the device changes the
whole frame's light (a white, black or negative frame), so the devices after it keep their strobes spaced.
A patch may instead be ``{"skip": why}``: the device cannot stand on this film, and its fallback runs.

LAYERS, the order ``apply`` lays devices in (base-replacing rhythm and layout first, the look last):

    rhythm   cuts, bursts, layouts that replace base pictures
    surface  finishes and treatments of the pictures (mono, gate, negative, variants)
    type     words and type cards
    hud      boxes, fine print, data over the picture
    alien    the alien bank (worker/scene/alien: rhyme, diecut, lamp, arc, answer)
    look     what reads the whole frame last (flashes over everything, the arc)

After each device the scene is checked with the engine's own ``plan.eligible``. A patch that would break the
scene's contract runs the device's fallback chain instead; the log says what landed for every ask, so nothing
silently disappears.

    from scene import palette
    palette.DEVICES                     # name -> entry, every family
    palette.menu()                      # one card a device, for the Director's prompt
    l1, log = palette.apply(l1, work)   # the asks under l1["devices"] (and l1["alien"]), in layer order
"""
import copy
import importlib
import pkgutil

from ..plan import eligible

CONTRACT = 1
LAYERS = ("rhythm", "surface", "type", "hud", "alien", "look")
NOT_FAMILIES = ("sense", "measure", "run", "util")      # modules here that declare no devices
DEVICES = {}
FAMILY_OF = {}            # an unknown device name a model may write -> the registered device of its family


def _alien_entries():
    """The alien bank's five devices, registered here under their own names. A device that cannot stand on the film
    runs its fallback chain (the bank's own 'skip' is the signal for it)."""
    from .. import alien
    chains = {"rhyme": ("rhyme_eyes", "match_pos", "neg"), "diecut": ("word_blend", "white"),
              "lamp": ("spotlight", "white"), "answer": ("diecut", "white"), "arc": ("arc_stakes", "finish")}
    layer = {"rhyme": "rhythm", "answer": "rhythm", "diecut": "alien", "lamp": "alien", "arc": "look"}
    out = {}
    for name, d in alien.DEVICES.items():
        out[name] = {"card": d["card"], "layer": layer.get(name, "alien"), "needs": tuple(d.get("needs") or ()),
                     "plan": (lambda fn: (lambda l1, work, ask, sense: fn(l1, work, ask)))(d["plan"]),
                     "fallback": chains.get(name, ("white",)), "family": "alien"}

    def arc_stakes(l1, work, ask, sense):
        """arc on the stakes only: the light and the frame follow the story's height, the finish keeps the film's
        own time (a moment that speaks from one year)."""
        marks = [dict(f=int(m["f"]), stakes=float(m.get("stakes") or 0.0)) for m in ask.get("marks") or [] if "f" in m]
        if not marks:
            a = _span(ask, l1)[0]
            marks = [dict(f=a, stakes=float(ask.get("stakes", -0.4)))]
        return alien.DEVICES["arc"]["plan"](l1, work, dict(ask, dev="arc", marks=marks))
    out["arc_stakes"] = {"card": "arc_stakes marks[{f stakes}]. The arc's light and frame on the story's height alone",
                         "layer": "look", "needs": (), "plan": arc_stakes, "fallback": ("finish",), "family": "alien"}
    return out


def _discover():
    """Every family module in this package, found by name: lanes add a file, never edit a list."""
    if DEVICES:
        return DEVICES
    found = {}
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if m.name in NOT_FAMILIES or m.name.startswith("_") or m.name.startswith("test"):
            continue
        mod = importlib.import_module(f"{__name__}.{m.name}")
        for name, entry in (getattr(mod, "DEVICES", None) or {}).items():
            e = dict(entry)
            e.setdefault("family", m.name)
            e.setdefault("fallback", ())
            e.setdefault("needs", ())
            e.setdefault("layer", "surface")
            found[name] = e
        FAMILY_OF.update(getattr(mod, "ALIASES", None) or {})
    for name, e in _alien_entries().items():
        found.setdefault(name, e)
    DEVICES.update(found)
    for name, e in DEVICES.items():
        FAMILY_OF.setdefault(name, name)
        FAMILY_OF.setdefault(e["family"], FAMILY_OF.get(e["family"], name))
    return DEVICES


def resolve(name):
    """The registered device a name means: itself, an alias a family declared, or its family's first device. A name
    nothing knows is the punctuation family's white hit, so an ask always lands as something."""
    _discover()
    n = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if n in DEVICES:
        return n
    if n in FAMILY_OF and FAMILY_OF[n] in DEVICES:
        return FAMILY_OF[n]
    for k in sorted(DEVICES):
        if n and (k.startswith(n) or n.startswith(k)):
            return k
    return "white" if "white" in DEVICES else sorted(DEVICES)[0]


def menu(layers=LAYERS):
    """One card a device, by layer: its fields, what it does, the kind of moment it amplifies. For the Director."""
    _discover()
    out = []
    for layer in layers:
        for name in sorted(n for n, e in DEVICES.items() if e["layer"] == layer):
            out.append(f"[{layer}] {DEVICES[name]['card']}")
    return "\n".join(out)


def _span(ask, l1):
    """[a, b] programme frames of an ask: its "f", else "at" (and "until"), clamped inside the film."""
    dur = int(l1.get("dur_f") or 1)
    f = ask.get("f")
    if isinstance(f, (list, tuple)) and len(f) >= 2:
        a, b = int(f[0]), int(f[1])
    elif isinstance(f, (int, float)):
        a, b = int(f), int(f) + int(ask.get("n") or 2)
    else:
        a = int(ask.get("at") if ask.get("at") is not None else 0)
        b = int(ask.get("until") if ask.get("until") is not None else a + int(ask.get("n") or 2))
    a = min(max(a, 0), dur - 1)
    b = min(max(b, a + 1), dur)
    return a, b


span = _span


def plan(l1, work, ask, sense=None):
    """One device's patch, nothing merged: {"items", "drop", ...} or {"skip": why}."""
    _discover()
    name = resolve(ask.get("dev"))
    if sense is None:
        from .sense import Sense
        sense = Sense(l1, work)
    return DEVICES[name]["plan"](l1, work, dict(ask, dev=name), sense)


def merge(l1, patch):
    """A scene with a patch laid in: a replaced item keeps its place, dropped ids leave, new items follow."""
    out = dict(l1)
    gone = set(patch.get("drop") or ())
    new = list(patch.get("items") or [])
    by_id = {i["id"]: i for i in new}
    items = []
    for it in l1.get("items") or []:
        if it["id"] in gone and it["id"] not in by_id:
            continue
        if it["id"] in by_id:
            items.append(by_id.pop(it["id"]))
        else:
            items.append(it)
    items += [i for i in new if i["id"] in by_id]
    out["items"] = items
    for key in ("assets", "voices", "film"):
        if patch.get(key):
            out[key] = dict(l1.get(key) or {}, **patch[key])
    return out


def _try(cur, work, ask, sense):
    """(patch, why): a device's patch that stands on the scene, or why it could not."""
    try:
        patch = DEVICES[ask["dev"]]["plan"](cur, work, ask, sense)
    except Exception as e:      # noqa: BLE001 - a device is never the reason a film does not render: its fallback runs
        return None, f"{type(e).__name__}: {str(e)[:200]}"
    if not isinstance(patch, dict):
        return None, "no patch"
    if patch.get("skip"):
        return None, str(patch["skip"])
    nxt = merge(cur, patch)
    ok, why = eligible(nxt)
    if not ok:
        return None, "would break the scene: " + "; ".join(why[:3])
    return (patch, nxt), None


def asks_of(l1):
    """The asks a scene carries: its "devices", then its "alien" asks (the older field), each once."""
    out = [dict(a) for a in (l1.get("devices") or []) if isinstance(a, dict)]
    out += [dict(a, layer_hint="alien") for a in (l1.get("alien") or []) if isinstance(a, dict)]
    return out


def apply(l1, work, asks=None, pool=None):
    """(scene, log). Every ask in layer order (then in the order asked); after each device the scene is checked with
    the engine's own ``eligible``, and an ask that cannot stand runs its device's fallback chain. The scene handed in
    is not changed. ``pool``: picture asset ids a device may draw from (default: every picture in the scene)."""
    _discover()
    from .sense import Sense
    asks = asks_of(l1) if asks is None else [dict(a) for a in asks]
    cur = copy.deepcopy({k: v for k, v in l1.items() if k not in ("devices", "alien")})
    sense = Sense(cur, work, pool=pool)
    log, events = [], []

    def order(t):
        n, a = t
        e = DEVICES[resolve(a.get("dev"))]
        return (LAYERS.index(e["layer"]) if e["layer"] in LAYERS else len(LAYERS), n)
    for n, ask in sorted(enumerate(asks), key=order):
        asked = ask.get("dev")
        name = resolve(asked)
        ask = dict(ask, dev=name, id=str(ask.get("id") or f"d{n}"))
        if name != asked and asked:
            ask.setdefault("as", str(asked).strip().lower().replace("-", "_").replace(" ", "_"))     # "gate" asked: finish, surface gate
        row = {"id": ask["id"], "dev": name, "asked": asked, "beat": ask.get("beat"), "f": list(_span(ask, cur))}
        if name != asked:
            row["mapped"] = f"{asked} -> {name}"
        chain, tried, landed = [name], [], None
        seen = set()
        while chain:
            dev = chain.pop(0)
            if dev in seen or dev not in DEVICES:
                continue
            seen.add(dev)
            sense.l1 = cur
            got, why = _try(cur, work, dict(ask, dev=dev), sense)
            if got is not None:
                landed = (dev, got)
                break
            tried.append(dict(dev=dev, why=why))
            chain += [d for d in DEVICES[dev].get("fallback") or () if d not in seen]
        if landed is None:
            log.append(dict(row, done=False, tried=tried))
            continue
        dev, (patch, nxt) = landed
        cur = nxt
        sense.l1 = cur
        sense.lum += [int(f) for f in patch.get("lum") or []]
        for e in patch.get("events") or []:
            events.append(dict(e, dev=dev, id=ask["id"]))
        log.append(dict(row, done=True, landed=dev, fallback=bool(tried), tried=tried, log=patch.get("log")))
    cur["device_events"] = sorted(events, key=lambda e: (int(e.get("f", 0)), str(e.get("id"))))
    cur["device_log"] = log
    return cur, log
