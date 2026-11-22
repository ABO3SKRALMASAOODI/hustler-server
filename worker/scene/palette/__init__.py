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
     "family":   "punct" | "finish" | "rhythm" | "type" | "hud" | "alien" | ...,
     "words":    ("steps[]", "sections[].at", "stress[]", "from", ...),   (optional: its time fields beyond f/at/until)
     "span":     True}                       (optional: it lays over a stretch, so an ask with no end runs to the sentence's)

``words`` names every time field of the device's own, so a caller that addresses moments by spoken word number (the
Director: ``_shell/director.py`` palette_asks) turns each into programme frames: "x" is a field, "x[]" each element of
a list (an element that is itself a list has its first value turned: a [word, strength] pair), "x[].y" the field y of
each dict in list x. A value that is a pair [first, last] is a stretch: its first word's start to its last word's end.

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


# The alien bank's cards as the palette shows them, every number with its unit (the bank's own cards name fields only, and
# a model that reads "lift" with no unit writes 0.6 where the device means 0.018 frame widths)
ALIEN_CARDS = {
    "rhyme": "rhyme at cuts[] n pool[] order[] r grow mono. A chain of pictures cut on one round shape, registered and "
             "growing through every cut. Needs at least 4 pictures with a round shape in them",
    "diecut": "diecut at t width lift dim seat. The word is stamped out of the live picture: its letters lift, carrying the "
              "footage, and drop back. A word that must feel made of the moment",
    "lamp": "lamp at t ink width room seat behind. The word is a light: it relights the speaker's take, throws his shadow, "
            "and burns at the level of the voice. Needs the speaker on screen for at least half a second",
    "arc": "arc marks[{f year stakes}] bars develop. The finish follows the year each sentence speaks from, the light and "
           "the frame follow the stakes",
    "answer": "answer at picks[{f pic word}] width dim seat. Each named picture is first seen inside its own word, then "
              "takes the frame on the stressed vowel",
}
NEEDS = {"lamp": ("take", "voice"), "rhyme": ("pictures", "rounds"), "answer": ("pictures", "voice"), "diecut": ("words",)}
# field: (lowest, highest, default, unit). clean() maps a value outside the range onto it and logs the repair.
ALIEN_RANGES = {
    "diecut": {"width": (0.3, 0.92, 0.84, "of the frame's width"), "lift": (0.0, 0.05, 0.018, "frame widths the letters rise"),
               "dim": (0.3, 1.0, 0.7, "light left on the frame while the letters are up"), "up": (3, 12, 7, "frames"),
               "down": (3, 10, 5, "frames")},
    "lamp": {"width": (0.3, 0.8, 0.62, "of the frame's width"), "room": (0.0, 0.7, 0.42, "how far the room's light falls at full voice"),
             "tail": (0, 20, 10, "frames the light takes to die")},
    "answer": {"width": (0.4, 0.92, 0.82, "of the frame's width"), "dim": (0.1, 0.8, 0.32, "light left on the outgoing picture")},
    "rhyme": {"r": (0.12, 0.42, 0.3, "the shape's radius, of the frame's width"), "grow": (1.0, 1.3, 1.12, "scale a cut"),
              "n": (2, 13, 4, "pictures")},
    "arc": {"bars": (0.0, 0.15, 0.085, "of the frame's height"), "develop": (0, 24, 6, "frames")},
}


def _alien_entries():
    """The alien bank's five devices, registered here under their own names. A device that cannot stand on the film
    runs its fallback chain (the bank's own 'skip' is the signal for it)."""
    from .. import alien
    chains = {"rhyme": ("rhyme_eyes", "match_pos", "neg"), "diecut": ("word_blend", "white"),
              "lamp": ("spotlight", "white"), "answer": ("diecut", "white"), "arc": ("arc_stakes", "finish")}
    layer = {"rhyme": "rhythm", "answer": "rhythm", "diecut": "alien", "lamp": "alien", "arc": "look"}
    out = {}

    def wrap(name, fn):
        def plan(l1, work, ask, sense):
            # a lamp throws the speaker's shadow, and any device asked to stand behind him needs his matte: made here
            got = {}
            if (name == "lamp" or ask.get("behind")) and "matte" not in (l1.get("assets") or {}):
                got = sense.matte()
                if got:
                    l1 = dict(l1, assets=dict(l1.get("assets") or {}, **got))
            if name == "diecut" and not ask.get("faces") and not ask.get("seat"):
                from ..alien import sense as clock
                span = ask.get("f") or [0, 1]
                lo, hi = span if isinstance(span, (tuple, list)) else (span, span + 1)
                W, H = l1["frame"]
                faces = []
                for frame in {lo, (lo + hi) // 2, max(lo, hi - 1)}:
                    sh = sense.shot_at(frame)
                    asset = (l1.get("assets") or {}).get((sh or {}).get("pic")) or {}
                    if not sh or not asset.get("w") or not asset.get("h"):
                        continue
                    x, y, right, bottom = clock.crop_at(sh, frame, asset["w"], asset["h"], W, H)
                    for l, t, r, b, _ in sense.faces(sh["pic"], clock.src_time(sh, frame, l1["fps"])):
                        faces.append([(l-x)/(right-x), (t-y)/(bottom-y), (r-l)/(right-x), (b-t)/(bottom-y)])
                ask = dict(ask, faces=faces)
            patch = fn(l1, work, ask)
            if got and isinstance(patch, dict) and not patch.get("skip"):
                patch = dict(patch, assets=dict(patch.get("assets") or {}, **got))
            return patch
        return plan
    for name, d in alien.DEVICES.items():
        out[name] = {"card": ALIEN_CARDS.get(name, d["card"]), "layer": layer.get(name, "alien"),
                     "needs": tuple(NEEDS.get(name) or d.get("needs") or ()), "ranges": ALIEN_RANGES.get(name, {}),
                     "plan": wrap(name, d["plan"]), "fallback": chains.get(name, ("white",)), "family": "alien"}

    def arc_stakes(l1, work, ask, sense):
        """arc on the stakes only: the light and the frame follow the story's height, the finish keeps the film's
        own time (a moment that speaks from one year)."""
        marks = [dict(f=int(m["f"]), stakes=float(m.get("stakes") or 0.0)) for m in ask.get("marks") or [] if "f" in m]
        if not marks:
            a = _span(ask, l1)[0]
            marks = [dict(f=a, stakes=float(ask.get("stakes", -0.4)))]
        return alien.DEVICES["arc"]["plan"](l1, work, dict(ask, dev="arc", marks=marks))
    out["arc_stakes"] = {"card": "arc_stakes marks[{f stakes}]. The arc's light and frame on the story's height alone",
                         "layer": "look", "needs": (), "plan": arc_stakes, "fallback": ("finish",), "family": "alien",
                         "words": ("marks[].f",)}
    for name, w in (("rhyme", ("cuts[]",)), ("answer", ("picks[].f",)), ("arc", ("marks[].f",))):
        if name in out:
            out[name]["words"] = w
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
            e.setdefault("layer", getattr(mod, "LAYER", "surface"))
            if isinstance(e["layer"], int):         # a family that numbers its layers (1 = rhythm ... 6 = look)
                e["layer"] = LAYERS[min(max(e["layer"], 1), len(LAYERS)) - 1]
            e.setdefault("words", ())
            e.setdefault("span", False)
            card = str(e.get("card") or "")
            if card.split(" ", 1)[0].rstrip(".") != name:        # every card opens with its device's name, then its fields
                e["card"] = f"{name} {card}"
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
        if len(n) >= 3 and (k.startswith(n) or n.startswith(k)):
            return k
    return "white" if "white" in DEVICES else sorted(DEVICES)[0]


def word_fields(name):
    """The time fields of a device beyond f/at/until, as its entry declares them (see ``words`` above): () for none."""
    _discover()
    e = DEVICES.get(resolve(name)) or {}
    return tuple(e.get("words") or ())


def spans(name):
    """True when the device lays over a stretch rather than on one frame."""
    _discover()
    return bool((DEVICES.get(resolve(name)) or {}).get("span"))


def menu(layers=LAYERS, families=None):
    """One card a device, by layer: its fields, what it does, the kind of moment it amplifies. For the Director.
    ``families``: only the devices of these family modules (e.g. type, behind, hud)."""
    _discover()
    out = []
    for layer in layers:
        for name in sorted(n for n, e in DEVICES.items() if e["layer"] == layer):
            if families and DEVICES[name].get("family") not in families:
                continue
            out.append(f"[{layer}] {card(name)}")
    return "\n".join(out)


def units(name):
    """The fields' units and drawable ranges of a device, as card text: 'lift 0-0.05 frame widths the letters rise
    (default 0.018)'."""
    rs = (DEVICES.get(name) or {}).get("ranges") or {}
    return "; ".join(f"{k} {lo:g}-{hi:g} {u} (default {d:g})" for k, (lo, hi, d, u) in rs.items())


def card(name):
    """A device's card with its fields' units and ranges."""
    u = units(name)
    amp = DEVICES[name].get("amplifies")
    return DEVICES[name]["card"] + (f". Units: {u}" if u else "") + (f". Amplifies: {amp}." if amp else "")


def _span(ask, l1):
    """[a, b] programme frames of an ask: its "f", else "at" (and "until"), clamped inside the film."""
    dur = int(l1.get("dur_f") or 1)
    f = ask.get("f")
    if isinstance(f, (list, tuple)) and len(f) >= 2:
        a, b = int(f[0]), int(f[1])
    elif isinstance(f, (int, float)):
        a, b = int(f), int(f) + int(ask.get("n") or 2)
    elif isinstance(ask.get("at"), (list, tuple)) and len(ask["at"]) >= 2:      # the alien bank's [a, z]
        a, b = int(ask["at"][0]), int(ask["at"][1])
    else:
        at = ask.get("at")
        a = int(at) if isinstance(at, (int, float)) else 0
        if a == 0 and isinstance(ask.get("marks"), list) and ask["marks"]:       # the arc: from its first mark
            a = int(min(int(m.get("f", 0)) for m in ask["marks"] if isinstance(m, dict)) if any(isinstance(m, dict) for m in ask["marks"]) else 0)
        u = ask.get("until")
        b = int(u) if isinstance(u, (int, float)) else a + int(ask.get("n") or 2)
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


SEATS = {"center": (0.5, 0.45), "centre": (0.5, 0.45), "middle": (0.5, 0.45), "mid": (0.5, 0.45), "top": (0.5, 0.28),
         "upper": (0.5, 0.3), "high": (0.5, 0.3), "above": (0.5, 0.3), "bottom": (0.5, 0.66), "lower": (0.5, 0.64),
         "low": (0.5, 0.64), "below": (0.5, 0.64), "left": (0.32, 0.45), "right": (0.68, 0.45),
         "top_left": (0.34, 0.3), "upper_left": (0.34, 0.3), "top_right": (0.66, 0.3), "upper_right": (0.66, 0.3),
         "bottom_left": (0.34, 0.64), "lower_left": (0.34, 0.64), "bottom_right": (0.66, 0.64), "lower_right": (0.66, 0.64),
         "head": (0.5, 0.3), "over_head": (0.5, 0.24), "beside": (0.5, 0.36), "side": (0.5, 0.36), "face": (0.5, 0.36)}
NAMED = {"room": {"dark": 0.3, "dim": 0.35, "low": 0.35, "warm": 0.55, "bright": 0.7, "lit": 0.65}}
WORDED = {"small": 0.25, "low": 0.25, "little": 0.25, "subtle": 0.25, "soft": 0.3, "medium": 0.5, "mid": 0.5, "normal": 0.5,
          "big": 0.85, "large": 0.85, "high": 0.85, "strong": 0.9, "wide": 0.9, "max": 1.0, "full": 1.0, "huge": 1.0}
NUMBERS = ("width", "lift", "dim", "room", "r", "grow", "bars", "develop", "amount", "th", "y", "h", "stakes", "angle",
           "tail", "up", "down")
INTS = ("n", "run", "flips", "count", "times", "up", "down", "develop", "tail")
SEAT_X = (0.06, 0.94)
SEAT_Y = (0.14, 0.86)


def ranges_of(dev):
    _discover()
    return dict((DEVICES.get(dev) or {}).get("ranges") or {})


def clean(ask):
    """An ask's fields in the shapes and ranges the device reads, every change logged as a repair, nothing refused.
    A seat named becomes its place (``"low"`` a seat low in the frame); a number written as a word is its number; a
    true for a ranged field is the device's default and a worded size ("big") a point of its range; a value outside
    the device's drawable range is mapped onto it (diecut lift 0.6 meant "a big lift": the device's highest, 0.05 frame
    widths); a seat that would put a block of the ask's width off the frame moves in. The ask's meaning is the caller's;
    this reads its format and its units. Returns (ask, notes)."""
    out, notes = dict(ask), []
    rs = ranges_of(resolve(out.get("dev"))) if out.get("dev") else {}
    seat = out.get("seat")
    if seat is not None:
        key = seat.strip().lower().replace("-", "_").replace(" ", "_") if isinstance(seat, str) else None
        if key in SEATS:
            out["seat"] = list(SEATS[key])
            notes.append(f"seat {seat!r} read as {out['seat']}")
        elif isinstance(seat, dict) and all(isinstance(seat.get(k), (int, float)) for k in ("x", "y")):
            out["seat"] = [float(seat["x"]), float(seat["y"])]
        elif not (isinstance(seat, (list, tuple)) and len(seat) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seat)):
            out.pop("seat")
            notes.append(f"seat {seat!r} left to the device")
    for k in NUMBERS:
        if k not in out:
            continue
        v = out.get(k)
        if v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)):
            continue
        got = None
        if isinstance(v, bool):
            if k in rs:
                got = rs[k][2] if v else rs[k][0]
        elif isinstance(v, str):
            w = v.strip().lower()
            named = NAMED.get(k, {}).get(w)
            if named is not None:
                got = named
            elif w in WORDED and k in rs:
                lo, hi = rs[k][0], rs[k][1]
                got = lo + (hi - lo) * WORDED[w]
            else:
                try:
                    got = float(w.rstrip("%")) / (100.0 if w.endswith("%") else 1.0)
                except ValueError:
                    got = None
        if got is None:
            out.pop(k)
            notes.append(f"{k} {v!r} left to the device")
        else:
            out[k] = float(got)
            notes.append(f"{k} {v!r} read as {round(float(got), 4)}")
    for k in INTS:
        if k in out and not (isinstance(out[k], int) and not isinstance(out[k], bool)):
            try:
                out[k] = int(round(float(out[k])))
            except (TypeError, ValueError):
                out.pop(k)
    for k, (lo, hi, _d, unit) in rs.items():           # onto the device's drawable range
        v = out.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not (lo <= v <= hi):
            got = min(max(float(v), lo), hi)
            got = int(round(got)) if isinstance(v, int) and float(lo).is_integer() and float(hi).is_integer() else round(got, 4)
            out[k] = got
            notes.append(f"{k} {v!r} is outside {lo:g}-{hi:g} {unit}: mapped to {got:g}")
    if isinstance(out.get("seat"), list):              # a block of the ask's width stays inside the frame
        x, y = [float(q) for q in out["seat"]]
        half = None
        if "width" in rs:
            half = float(out.get("width") or rs["width"][2]) / 2 + float(out.get("lift") or 0.0) + 0.02
        x0, x1 = (max(SEAT_X[0], half), min(SEAT_X[1], 1 - half)) if half is not None and half < 0.5 else (0.5, 0.5) if half else SEAT_X
        nx, ny = min(max(x, x0), x1), min(max(y, SEAT_Y[0]), SEAT_Y[1])
        if (nx, ny) != (x, y):
            notes.append(f"seat {[round(x, 3), round(y, 3)]} moved to {[round(nx, 3), round(ny, 3)]} to stay inside the frame")
        out["seat"] = [round(nx, 4), round(ny, 4)]
    if "ink" in out and not (isinstance(out["ink"], str) and out["ink"].startswith("#") and len(out["ink"]) == 7):
        out.pop("ink")
    for k in ("order", "pool"):
        if k in out and not isinstance(out[k], list):
            out.pop(k)
    return out, notes


def _try(cur, work, ask, sense):
    """(patch, why): a device's patch that stands on the scene, or why it could not."""
    try:
        patch = DEVICES[ask["dev"]]["plan"](cur, work, ask, sense)
    except Exception as e:      # noqa: BLE001 - a device is never the reason a film does not render: its fallback runs
        return None, f"{e.__class__.__name__}: {str(e)[:200]}"      # `type` here is the type family module
    if not isinstance(patch, dict):
        return None, "no patch"
    if patch.get("skip"):
        return None, str(patch["skip"])
    dropped = glyph_safe(patch, cur.get("voices"))
    if dropped and isinstance(patch.get("log"), dict):
        patch["log"]["glyphs_dropped"] = dropped
    # the watermark's keep-out is the owner's: a device's own type that would meet it moves down, as one block
    moved = _clear_mark(cur, patch, ask.get("id") or "")
    if moved and isinstance(patch.get("log"), dict):
        patch["log"]["moved_off_mark"] = moved
    nxt = merge(cur, patch)
    ok, why = eligible(nxt)
    if not ok:
        # a fault the scene already carried before this device (a frame its builder left without a base picture) is
        # not this device's: only a problem the patch adds sends the ask to its fallback
        _, was = eligible(cur)
        why = [w for w in why if w not in set(was)]
        if why:
            return None, "would break the scene: " + "; ".join(why[:3])
    return (patch, nxt), None


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


def _clear_mark(cur, patch, ask_id):
    from . import kit as K
    try:
        W, H = [int(v) for v in (cur.get("frame") or [1080, 1920])]
        voices = dict(cur.get("voices") or {}, **(patch.get("voices") or {}))
        return K.clear_of_mark(patch.get("items") or [], voices, W, H, f"{ask_id}.")
    except Exception:      # noqa: BLE001 - a word that cannot be measured is the engine's to report
        return 0.0


def chain(name):
    """The device and its fallbacks, each once, in order (a name the palette does not know resolves first)."""
    _discover()
    seen, out, todo = set(), [], [resolve(name)]
    while todo:
        n = todo.pop(0)
        if n in seen or n not in DEVICES:
            continue
        seen.add(n)
        out.append(n)
        todo += list(DEVICES[n]["fallback"])
    return out


def asks_of(l1):
    """The asks a scene carries: its "devices", then its "alien" asks (the older field), each once."""
    out = [dict(a) for a in (l1.get("devices") or []) if isinstance(a, dict)]
    out += [dict(a, layer_hint="alien") for a in (l1.get("alien") or []) if isinstance(a, dict)]
    return out


WORD_DEVICES = ("lamp", "diecut", "answer", "word_blend")
# devices that set the words themselves (the type family's pages, names, counts, words behind him): the builder's display
# copy of the same words leaves while one stands, as it does for the word devices; they seat themselves off other type
SETS_WORDS = WORD_DEVICES + ("counter", "staircase", "name", "ladder", "brackets", "typewriter", "redact", "type_run", "verb",
                             "behind", "marquee", "chapter")
SEAT_ROWS = (0.30, 0.46, 0.62, 0.22, 0.74)        # baselines a word device may move to when its seat is taken


def _words(t):
    return ["".join(ch for ch in w.lower() if ch.isalnum()) for w in str(t or "").split() if any(ch.isalnum() for ch in w)]


def _within(a, b):
    """Is word list a a contiguous run of word list b?"""
    return bool(a) and any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


def same_text(a, b):
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and (_within(wa, wb) or _within(wb, wa))


def device_text(ask, patch):
    """The words a word device sets on the frame: its "t", an answer's picked words, a word blend's own items."""
    if ask.get("t"):
        return [str(ask["t"])]
    if ask.get("dev") == "answer":
        return [str(p.get("word")) for p in ask.get("picks") or [] if isinstance(p, dict) and p.get("word")]
    return sorted({str(i.get("t")) for i in (patch or {}).get("items") or [] if i.get("k") == "word" and i.get("t")})


def seat_box(patch, ask, rs):
    """[x0, y0, x1, y1, f0, f1] of the block a word device set (its patch's seat, else its ask's)."""
    st = (patch or {}).get("seat")
    f = st.get("f") if isinstance(st, dict) and st.get("f") else span(ask, {"dur_f": 10 ** 9})
    if isinstance(st, dict) and all(k in st for k in ("x", "y", "w", "h")):
        x, y, w, h = float(st["x"]), float(st["y"]), float(st["w"]), float(st["h"])
    else:
        x, y = [float(v) for v in (ask.get("seat") or (0.5, 0.5))]
        w, h = float(ask.get("width") or (rs.get("width") or (0, 0, 0.8))[2]), 0.1
    return [x - w / 2, y - h, x + w / 2, y, int(f[0]), int(f[1])]


def _clash(b, others, pad=0.015):
    """How much of block b another block standing at the same time covers (0 when none)."""
    worst = 0.0
    for o in others:
        if o[5] <= b[4] or b[5] <= o[4]:
            continue
        ox = max(0.0, min(b[2], o[2]) - max(b[0], o[0]) + pad)
        oy = max(0.0, min(b[3], o[3]) - max(b[1], o[1]) + pad)
        worst = max(worst, ox * oy / max((b[2] - b[0]) * (b[3] - b[1]), 1e-6))
    return worst


def free_seat(box, others, x=0.5):
    """The seat (centre x, baseline y) among SEAT_ROWS where a block of box's size stands clear of the others, else
    where it is covered least."""
    w, h = box[2] - box[0], box[3] - box[1]
    best = None
    for y in SEAT_ROWS:
        b = [x - w / 2, y - h, x + w / 2, y, box[4], box[5]]
        c = _clash(b, others)
        if best is None or c < best[0] - 1e-9:
            best = (c, [x, y])
        if c == 0.0:
            break
    return best[1]


def type_boxes(patch, scene):
    """[x0, y0, x1, y1, f0, f1] of each word a device set, where it rests, as drawn (none where it cannot be measured)."""
    from . import kit as K
    out = []
    try:
        W, H = [int(v) for v in (scene.get("frame") or [1080, 1920])]
        voices = dict(scene.get("voices") or {}, **(patch.get("voices") or {}))
        for it in patch.get("items") or []:
            if it.get("k") == "word" and not it.get("behind"):
                b = K.item_box(it, voices, W, H)
                if b:
                    out.append([float(b[0]), float(b[1]), float(b[2]), float(b[3]), int(it["f"][0]), int(it["f"][1])])
    except Exception:      # noqa: BLE001 - a word that cannot be measured leaves no seat taken
        return out
    return out


def drop_echoes(cur, texts, f0, f1, made):
    """Ids of the builder's own display type that says one of ``texts`` while [f0, f1) stands (a word device sets that
    word now, so the builder's copy of it leaves): the display block's lines and its bar go together."""
    gone = set()
    for it in cur.get("items") or []:
        if it.get("k") != "word" or it["id"] in made or it.get("role") == "cap" or it["f"][1] <= f0 or it["f"][0] >= f1:
            continue
        if any(same_text(it.get("t"), t) for t in texts):
            on = it.get("on", it["f"][0])
            for j in cur["items"]:
                if j["id"] in made:
                    continue
                if j.get("k") == "word" and j.get("role") == it.get("role") and j.get("on", j["f"][0]) == on and \
                        j["f"][0] < it["f"][1] and j["f"][1] > it["f"][0]:
                    gone.add(j["id"])
                elif j.get("k") == "card" and j.get("pic") == "plate" and j.get("bar_on") == on:
                    gone.add(j["id"])
            gone.add(it["id"])
    return gone


def apply(l1, work, asks=None, pool=None):
    """(scene, log). Every ask in layer order (a section's finish first in its layer: it is the surface the other
    devices stand on), then in the order asked; after each device the scene is checked with the engine's own
    ``eligible``, and an ask that cannot stand runs its device's fallback chain. A word device sets its word once: the
    builder's display copy of that word leaves while it stands, and two word devices at the same time take distinct
    seats. The scene handed in is not changed. ``pool``: picture asset ids a device may draw from (default: every
    picture in the scene)."""
    _discover()
    from .sense import Sense
    asks = asks_of(l1) if asks is None else [dict(a) for a in asks]
    cur = copy.deepcopy({k: v for k, v in l1.items() if k not in ("devices", "alien")})
    from .picks import stamp_ranges
    stamp_ranges(cur)            # each clip's checked slice, kept on its asset before any device carves its shots away
    sense = Sense(cur, work, pool=pool)
    log, events, made, seats = [], [], set(), []

    from ..alien import ORDER as ALIEN_ORDER

    def order(t):
        """Layer first; inside a layer the palette's own devices in the order asked, then the alien bank's in the bank's
        own order (answer, rhyme, lamp, diecut, arc: base pictures first, then what reads them), each in the order asked,
        as the bank lays them itself (scene.alien.apply)."""
        n, a = t
        name = resolve(a.get("dev"))
        e = DEVICES[name]
        lay = LAYERS.index(e["layer"]) if e["layer"] in LAYERS else len(LAYERS)
        if e.get("family") == "alien":
            base = name if name in ALIEN_ORDER else {"arc_stakes": "arc"}.get(name)
            return (lay, 1, ALIEN_ORDER.index(base) if base in ALIEN_ORDER else len(ALIEN_ORDER), n)
        return (lay, 0, 0 if name == "finish" else 1, n)         # a section's finish first: the surface the others stand on
    for n, ask in sorted(enumerate(asks), key=order):
        asked = ask.get("dev")
        name = resolve(asked)
        ask = dict(ask, dev=name, id=str(ask.get("id") or f"d{n}"))
        if name != asked and asked:
            ask.setdefault("as", str(asked).strip().lower().replace("-", "_").replace(" ", "_"))     # "gate" asked: finish, surface gate
        ask, cleaned = clean(ask)
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
            if dev == name:
                dask, dnotes = ask, []
            else:                           # a field the asked device reads in other units is left to the fallback's default
                mine, theirs = ranges_of(name), ranges_of(dev)
                other = [k for k in mine if k in theirs and mine[k][3] != theirs[k][3]]
                dask, dnotes = clean(dict({k: v for k, v in ask.items() if k not in other}, dev=dev))
            got, why = _try(cur, work, dict(dask, dev=dev), sense)
            if got is not None and dev in WORD_DEVICES and dev != "word_blend":
                box = seat_box(got[0], dask, ranges_of(dev))
                if _clash(box, seats) > 0:          # another word device stands there: this one takes a free seat
                    seat = free_seat(box, seats)
                    again, _ = _try(cur, work, dict(dask, dev=dev, seat=seat), sense)
                    if again is not None:
                        got = again
                        dnotes = dnotes + [f"seat moved to {seat}: another word device stood at {[round(v, 3) for v in box[:4]]}"]
            if got is not None:
                landed = (dev, got)
                cleaned = cleaned + [c for c in dnotes if c not in cleaned]
                break
            tried.append(dict(dev=dev, why=why))
            chain += [d for d in DEVICES[dev].get("fallback") or () if d not in seen]
        if cleaned:
            row["cleaned"] = cleaned
        if landed is None:
            log.append(dict(row, done=False, tried=tried))
            continue
        dev, (patch, nxt) = landed
        if l1.get("workflow_text_layout") and dev in SETS_WORDS:
            original_ids = {i["id"] for i in cur["items"]}
            new_type = {i["id"] for i in patch.get("items") or [] if i["k"] == "word" and i["id"] not in original_ids}
            if len(new_type) > 1:
                for it in nxt["items"]:
                    if it["id"] in new_type:
                        it.setdefault("text_group", ask["id"])
        made |= {i["id"] for i in patch.get("items") or []}
        echoes = set()
        if dev in SETS_WORDS:
            texts = device_text(dict(ask, dev=dev), patch)
            box = seat_box(patch, ask, ranges_of(dev))
            if dev in WORD_DEVICES and dev != "word_blend":
                seats.append(box)
            elif dev not in WORD_DEVICES:           # the type family's own words: a later word device seats itself off them
                seats += type_boxes(patch, nxt)
            echoes = set() if cur.get("workflow_text_layout") else drop_echoes(nxt, texts, box[4], box[5], made)
            if echoes:
                nxt = dict(nxt, items=[i for i in nxt["items"] if i["id"] not in echoes])
        cur = nxt
        sense.l1 = cur
        sense.lum += [int(f) for f in patch.get("lum") or []]
        for e in patch.get("events") or []:
            events.append(dict(e, dev=dev, id=ask["id"]))
        log.append(dict(row, done=True, landed=dev, fallback=bool(tried), tried=tried, log=patch.get("log"),
                        marks=patch.get("marks") or [], seat=patch.get("seat"), **({"replaced": sorted(echoes)} if echoes else {})))
    cur, trimmed = behind_on_take(cur)
    if "documentary_cuts" in cur:
        from .kit import cut, picture_spans
        items = []
        for item in cur["items"]:
            if item.get("name_picture"):
                for a, b in picture_spans(cur, item["name_picture"], *item["f"]):
                    items.append(item if [a, b] == item["f"] else cut(item, a, b, f"{item['id']}~{a}"))
            else:
                items.append(item)
        cur = dict(cur, items=items)
    if trimmed:
        for r in log:
            got = {k: v for k, v in trimmed.items() if k.startswith(str(r["id"]))}
            if got:
                r["behind_trimmed"] = got
    if l1.get("story_cues"):
        cur["story_landings"] = [
            {"f": m["f"], "kind": "display" if m.get("kind") == "type" else "device", "landing": True}
            for row in log if row.get("done")
            for m in row.get("marks", []) if isinstance(m.get("f"), int)
        ]
    cur["device_events"] = sorted(events, key=lambda e: (int(e.get("f", 0)), str(e.get("id"))))
    cur["device_log"] = log
    return cur, log


def behind_on_take(l1):
    """(scene, {card id: frames left}). A card drawn behind the speaker stands only on frames whose base picture is his
    take: the engine draws the depth sandwich through the take's matte there, and anywhere else (a later device put its
    own picture under those frames) the card would be drawn in front of the whole frame. Such a card keeps its take
    frames, each run of them its own piece, its clock and per-frame values cut with it."""
    from ..alien import sense as AS
    shots = sorted((i for i in l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0])

    def take_at(f):
        sh = next((s for s in shots if s["f"][0] <= f < s["f"][1]), None)
        p = str((sh or {}).get("pic"))
        return bool(sh and sh.get("src") and (p == "main" or p.startswith("own:")))
    items, trimmed = [], {}
    for it in l1.get("items") or []:
        if it.get("k") != "card" or not it.get("behind"):
            items.append(it)
            continue
        on = [take_at(f) for f in range(it["f"][0], it["f"][1])]
        if all(on):
            items.append(it)
            continue
        runs, f0 = [], None
        for q, ok in enumerate(on + [False]):
            if ok and f0 is None:
                f0 = it["f"][0] + q
            elif not ok and f0 is not None:
                runs.append((f0, it["f"][0] + q))
                f0 = None
        for k, (lo, hi) in enumerate(runs):
            items.append(AS.slice_shot(it, lo, hi, new_id=it["id"] if k == 0 else f"{it['id']}~{k}"))
        trimmed[it["id"]] = sum(on)
    return (dict(l1, items=items), trimmed) if trimmed else (l1, {})
