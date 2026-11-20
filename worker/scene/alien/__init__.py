"""worker/scene/alien: what no human editor does, because a machine holds the whole film at once: every
frame of every source, every word and its frame, the level of the voice on each of them.

Five devices. Each takes a scene (the Score's L1) and an ask, measures what it needs by signal, and gives
back a patch of plain scene items (and, for a relit take, one baked clip): nothing here draws, and the
engine needs no new op to show any of it. No model is read (the engine's rule): which word, which picture,
which year come from the caller, a model that read the script. A device that meets a fixable gap repairs it and
says what it did (owner, 16 Nov 2026: a fix gets the outcome): answer's words with no picture still land as
words, a lamp with nothing to relight burns as a glow at the voice's level, a diecut over a plate cuts its letters
from the next picture, a lamp makes the take's matte when the scene has none. Only a true impossibility (no word,
no round shape anywhere) leaves the film as it was, and the palette (worker/scene/palette) then runs the device's
fallback chain; it never fails a render.

    from scene import alien
    alien.DEVICES                       # name -> {"card", "claim", "plan"}
    alien.menu()                        # one line a device, for a prompt or a skill
    l1, log = alien.apply(l1, work)     # the asks under l1["alien"] (or asks=[...]), in a safe order
    patch = alien.plan(l1, work, ask)   # one device, nothing merged

An ask is {"dev": name, "id": "a1", ...}; frames are programme frames. ``apply`` checks the scene after each
device with the engine's own ``eligible``; a device that would break the scene's contract runs its fallback instead.
"""
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
    """(scene, log). Every ask laid by the palette (worker/scene/palette) in its layer order: a device that cannot stand,
    or would break the scene, runs its fallback chain (rhyme to a position match or a flip, diecut to a word blend,
    lamp to a spotlight, answer to a diecut, arc to the arc on its stakes), so the moment keeps a device; the log says
    what landed and why the asked device could not. The scene handed in is not changed."""
    from .. import palette
    asks = list(asks if asks is not None else (l1.get("alien") or []))
    out, plog = palette.apply({k: v for k, v in l1.items() if k not in ("alien", "devices")}, work, asks=asks)
    log = []
    for r in plog:
        tried = r.get("tried") or []
        row = {"id": r["id"], "dev": r.get("asked") or r.get("dev"), "done": bool(r.get("done")), "landed": r.get("landed"),
               "fallback": bool(r.get("fallback")), "marks": r.get("marks") or [], "seat": r.get("seat"), "log": r.get("log")}
        if tried:
            row["why"] = "; ".join(f"{t['dev']}: {t['why']}" for t in tried)
        log.append(row)
    return out, log
