"""worker/scene/alien: what no human editor does, because a machine holds the whole film at once: every
frame of every source, every word and its frame, the level of the voice on each of them.

Five devices. Each takes a scene (the Score's L1) and an ask, measures what it needs by signal, and gives
back a patch of plain scene items (and, for a relit take, one baked clip): nothing here draws, and the
engine needs no new op to show any of it. No model is read (the engine's rule): which word, which picture,
which year come from the caller, a model that read the script. A device that cannot stand on this film
(no round shape, no picture under the word) says so and leaves the film as it was; it never fails a render.

    from scene import alien
    alien.DEVICES                       # name -> {"card", "claim", "plan"}
    alien.menu()                        # one line a device, for a prompt or a skill
    l1, log = alien.apply(l1, work)     # the asks under l1["alien"] (or asks=[...]), in a safe order
    patch = alien.plan(l1, work, ask)   # one device, nothing merged

An ask is {"dev": name, "id": "a1", ...}; frames are programme frames. ``apply`` checks the scene after each
device with the engine's own ``eligible`` and takes a device back out if it broke the scene's contract.
"""
import copy

from ..plan import eligible
from . import answer, arc, diecut, lamp, rhyme

DEVICES = {
    "rhyme": {"plan": rhyme.plan, "needs": ("pictures",),
              "card": 'rhyme at cuts[]|n pool[] order[] seat r grow mono. A chain of pictures cut on one round shape, registered and growing through every cut',
              "claim": "A match cut found across every frame of the supply, the shape locked to the pixel inside moving video."},
    "diecut": {"plan": diecut.plan, "needs": ("picture",),
               "card": 'diecut at t width seat lift. The word is stamped out of the live picture: its letters lift, carrying the footage, and drop back',
               "claim": "Type made of the take itself, registered to the shot's crop and clock on every frame; no ink is laid."},
    "lamp": {"plan": lamp.plan, "needs": ("take", "voice"),
             "card": 'lamp at t ink width seat room behind. The word is a light: it relights the take, throws the speaker\'s shadow, and burns at the level of the voice',
             "claim": "A display word that lights the set and the speaker, frame by frame at the voice's own level."},
    "arc": {"plan": arc.plan, "needs": ("marks",),
            "card": 'arc marks[{f year stakes}] bars develop. The finish follows the year each sentence speaks from, the light and the frame follow the stakes',
            "claim": "One continuous look bound to the story's calendar and stakes on every shot, whatever the footage's age."},
    "answer": {"plan": answer.plan, "needs": ("pictures", "voice"),
               "card": 'answer at picks[{f pic word}] width seat dim. Each named picture is first seen inside its own word, then takes the frame on the stressed vowel',
               "claim": "Pictures land on the syllable that names them, each born through the letters of its own word."},
}
ORDER = ("answer", "rhyme", "lamp", "diecut", "arc")       # base pictures first, then what reads them, the look last


def menu():
    """One line per device: its fields, then what it does. For the Director's prompt and the workflow skills."""
    return "\n".join(DEVICES[k]["card"] for k in ORDER)


def plan(l1, work, ask):
    """One device's patch: {"items", "drop", "assets", "voices", "film", "marks", "seat", "log"} or {"skip": why}."""
    d = DEVICES.get(ask.get("dev"))
    if d is None:
        return {"skip": f"unknown device {ask.get('dev')!r}"}
    return d["plan"](l1, work, ask)


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


def apply(l1, work, asks=None):
    """(scene, log). Every ask in a safe order; one that cannot stand, or would break the scene, is left out and
    says why in the log. The scene handed in is not changed."""
    asks = list(asks if asks is not None else (l1.get("alien") or []))
    cur = copy.deepcopy({k: v for k, v in l1.items() if k != "alien"})
    log = []
    seq = sorted(enumerate(asks), key=lambda t: (ORDER.index(t[1].get("dev")) if t[1].get("dev") in ORDER else 99, t[0]))
    for n, ask in seq:
        ask = dict(ask, id=str(ask.get("id") or f"al{n}"))
        row = {"id": ask["id"], "dev": ask.get("dev")}
        try:
            patch = plan(cur, work, ask)
        except Exception as e:      # noqa: BLE001 - a device is never the reason a film does not render
            patch = {"skip": f"{type(e).__name__}: {e}"}
        if patch.get("skip"):
            log.append(dict(row, done=False, why=patch["skip"], log=patch.get("log")))
            continue
        nxt = merge(cur, patch)
        ok, why = eligible(nxt)
        if not ok:
            log.append(dict(row, done=False, why="would break the scene: " + "; ".join(why[:3])))
            continue
        cur = nxt
        log.append(dict(row, done=True, marks=patch.get("marks") or [], seat=patch.get("seat"), log=patch.get("log")))
    return cur, log
