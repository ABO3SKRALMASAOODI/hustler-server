"""The engine's alien bank (worker/scene/alien: answer, rhyme, lamp, diecut, arc) in the palette, so one apply lays
every device of a film in one order. Their own plans run unchanged; an ask that names them under ``devices`` or
under ``alien`` lands the same."""
from .. import alien

LAYER = "alien"


def _wrap(name):
    def plan(l1, work, ask, sense):
        return alien.plan(l1, work, dict(ask, dev=name))
    return plan


DEVICES = {name: {"plan": _wrap(name), "needs": tuple(d.get("needs") or ()), "fallback": (), "card": d["card"],
                  "layer": "look" if name == "arc" else "alien"}
           for name, d in alien.DEVICES.items()}
