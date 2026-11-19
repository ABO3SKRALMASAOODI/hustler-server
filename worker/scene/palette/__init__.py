"""worker/scene/palette: one shared palette of devices that a director chooses per moment and the engine draws.

Each family module declares ``DEVICES[name] = {"card", "needs", "plan", "fallback", "layer", "amplifies"}`` and is
found here by itself (pkgutil), so a lane adds a family without editing a shared list. ``plan(l1, work, ask, sense)``
gives back a patch of plain scene items (``items``, ``drop``, ``voices``, ``assets``, ``film``, ``marks``, ``log``);
nothing in a device draws, and nothing reads a model or a meaning: which words, figures, names and pictures come
from the caller. The five devices of the alien bank are registered here too.

A device that cannot stand on this film as asked does not leave the moment bare: its ``fallback`` chain runs, in
order, with the same ask (``{"skip": why}`` from a device means "try the next"). After each device the engine's own
``eligible`` checks the scene; a patch that would break it runs the fallback chain too. Every step is in the log.

    from scene import palette
    palette.DEVICES                        # name -> card
    palette.menu()                         # one line a device, for a director's prompt
    l1, log = palette.apply(l1, work)      # the asks under l1["devices"] and l1["alien"], by layer
"""
import copy
import importlib
import pkgutil

from ..plan import eligible

# the order patches are laid in: what replaces the base picture first, the look last
LAYERS = {"rhythm": 1, "surface": 2, "type": 3, "hud": 4, "alien": 5, "look": 6}
DEVICES = {}
FAMILY = {}


def _alien():
    from .. import alien as A
    layer = {"answer": 1, "rhyme": 1, "lamp": 5, "diecut": 5, "arc": 6}
    falls = {"answer": ("ladder", "verb"), "lamp": ("verb",), "diecut": ("verb",), "rhyme": (), "arc": ()}
    amp = {"answer": "a list of named things, each seen inside its own name", "rhyme": "repetition, 'again', sameness",
           "lamp": "a word that should feel like light: a revelation, a love, a fire", "diecut": "a verdict word cut from the picture",
           "arc": "a story told across years"}
    for name, d in A.DEVICES.items():
        DEVICES[name] = {"card": d["card"], "needs": d["needs"], "plan": (lambda f: lambda l1, work, ask, sense=None: f(l1, work, ask))(d["plan"]),
                         "fallback": falls.get(name, ()), "layer": layer.get(name, 5), "amplifies": amp.get(name, "")}
        FAMILY[name] = "alien"


def _discover():
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_") or m.name in ("run",):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
        except Exception:      # noqa: BLE001 - a family that cannot load is left out of the menu, never the film's end
            continue
        for name, d in (getattr(mod, "DEVICES", None) or {}).items():
            DEVICES[name] = dict(d, fallback=tuple(d.get("fallback") or ()), layer=int(d.get("layer", 3)))
            FAMILY[name] = m.name
    _alien()


_discover()


def menu(families=None):
    """One line per device: its name and fields, what it does, what moment it amplifies. For a director's prompt."""
    out = []
    for name in sorted(DEVICES, key=lambda n: (DEVICES[n]["layer"], FAMILY.get(n, ""), n)):
        if families and FAMILY.get(name) not in families:
            continue
        d = DEVICES[name]
        amp = f" Amplifies: {d['amplifies']}." if d.get("amplifies") else ""
        out.append(f"{d['card']}{amp}")
    return "\n".join(out)


def plan(l1, work, ask, sense=None):
    d = DEVICES.get(ask.get("dev"))
    if d is None:
        return {"skip": f"unknown device {ask.get('dev')!r}"}
    return d["plan"](l1, work, ask, sense)


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


_GLYPH = {}


def _has(voice, ch):
    key = (voice.get("font"), voice.get("family"), str(voice.get("var")), ch)
    if key not in _GLYPH:
        try:
            from ..alien import glyphs
            got, _ = glyphs.face(voice).shape(ch, 100)
            _GLYPH[key] = bool(got) and all(g[0] != 0 for g in got)
        except Exception:      # noqa: BLE001 - a face that cannot be read is the engine's to report
            _GLYPH[key] = True
    return _GLYPH[key]


def glyph_safe(patch, voices):
    """A device's words with any character its face cannot draw taken out (a dash or a symbol a model wrote that the
    display face lacks), so a strict render never stops on a device's type. Returns how many characters went."""
    n = 0
    for it in patch.get("items") or []:
        if it.get("k") not in ("word", "join") or not isinstance(it.get("t"), str):
            continue
        v = (patch.get("voices") or {}).get(it.get("v")) or (voices or {}).get(it.get("v"))
        if not v:
            continue
        keep = "".join(c for c in it["t"] if c.isspace() or _has(v, c))
        if keep != it["t"]:
            n += len(it["t"]) - len(keep)
            it["t"] = keep if keep.strip() else "."
    return n


def chain(name):
    """The device and its fallbacks, each once, in order. A name the palette does not know (a model's slip) is read as
    the one device every word can stand on, ``verb``."""
    if name not in DEVICES:
        return [name, "verb"]
    seen, out, todo = set(), [], [name]
    while todo:
        n = todo.pop(0)
        if n in seen or n not in DEVICES:
            continue
        seen.add(n)
        out.append(n)
        todo += list(DEVICES[n]["fallback"])
    return out or [name]


def apply(l1, work, asks=None, sense=None):
    """(scene, log). Every ask, by layer; a device that cannot stand runs its fallback chain, a patch that would
    break the scene runs it too. The scene handed in is not changed. The log row of each ask names the device that
    stood (``dev``), what was asked (``asked``) and every step that did not (``tried``)."""
    if asks is None:
        asks = list(l1.get("devices") or []) + list(l1.get("alien") or [])
    cur = copy.deepcopy({k: v for k, v in l1.items() if k not in ("devices", "alien")})
    seq = sorted(enumerate(asks), key=lambda t: (DEVICES.get(t[1].get("dev"), {}).get("layer", 9), t[0]))
    log, marks = [], []
    for n, ask in seq:
        ask = dict(ask, id=str(ask.get("id") or f"dv{n}"))
        tried, row = [], None
        for name in chain(ask.get("dev")):
            a2 = dict(ask, dev=name)
            try:
                patch = plan(cur, work, a2, sense)
            except Exception as e:      # noqa: BLE001 - a device is never the reason a film does not render: the next one runs
                patch = {"skip": f"{type(e).__name__}: {e}"}
            if patch.get("skip"):
                tried.append({"dev": name, "why": patch["skip"]})
                continue
            dropped = glyph_safe(patch, cur.get("voices"))
            if dropped and isinstance(patch.get("log"), dict):
                patch["log"]["glyphs_dropped"] = dropped
            nxt = merge(cur, patch)
            ok, why = eligible(nxt)
            if not ok:
                tried.append({"dev": name, "why": "would break the scene: " + "; ".join(why[:3])})
                continue
            cur = nxt
            mk = [dict(m, dev=name, ask=ask["id"]) for m in patch.get("marks") or []]
            marks += mk
            row = {"id": ask["id"], "asked": ask.get("dev"), "dev": name, "done": True, "marks": mk, "seat": patch.get("seat"),
                   "log": patch.get("log"), "tried": tried}
            break
        log.append(row or {"id": ask["id"], "asked": ask.get("dev"), "dev": None, "done": False, "tried": tried,
                           "why": "; ".join(t["why"] for t in tried)[:400]})
    if marks:
        cur["device_events"] = sorted(marks, key=lambda m: m["f"])
    return cur, log
